"""Deleting the active node — directly or via a subscription cascade — must
re-point (or clear) ``Settings['active_node_id']`` instead of leaving it
dangling. A dangling value made the NEXT config regeneration silently produce
a proxy-less config, and all LAN traffic fell through to direct routing."""
import asyncio
from unittest import mock
from unittest.mock import AsyncMock

from sqlmodel import select

from app.models import Node, Settings as DBSettings, Subscription


def _mk_node(session, name, *, enabled=True, sub_id=None):
    node = Node(
        name=name, protocol="vless", address="1.2.3.4", port=443,
        uuid="u", transport="tcp", tls="none", enabled=enabled,
        subscription_id=sub_id,
    )
    session.add(node)
    session.commit()
    session.refresh(node)
    return node


def _set_active(session, node_id):
    session.add(DBSettings(key="active_node_id", value=str(node_id)))
    session.commit()


def _active_value(session):
    session.expire_all()
    row = session.exec(
        select(DBSettings).where(DBSettings.key == "active_node_id")
    ).first()
    return row.value if row else None


def _not_running():
    return mock.patch(
        "app.core.xray.XrayManager.is_running",
        new_callable=mock.PropertyMock, return_value=False,
    )


class TestDeleteNodeHealsActive:
    def test_delete_active_node_repoints_to_survivor(
        self, client, session, admin_user, auth_headers,
    ):
        n1 = _mk_node(session, "active")
        n2 = _mk_node(session, "survivor")
        _set_active(session, n1.id)

        with _not_running():
            resp = client.delete(f"/api/nodes/{n1.id}", headers=auth_headers)
        assert resp.status_code == 204
        assert _active_value(session) == str(n2.id)

    def test_delete_active_node_prefers_online_survivor(
        self, client, session, admin_user, auth_headers,
    ):
        n1 = _mk_node(session, "active")
        # Lower id, but offline — the heal must skip it. `is_online`
        # defaults to True on the model, so mark it explicitly.
        offline = _mk_node(session, "offline-survivor")
        offline.is_online = False
        session.add(offline)
        session.commit()
        n3 = _mk_node(session, "online-survivor")
        _set_active(session, n1.id)

        with _not_running():
            resp = client.delete(f"/api/nodes/{n1.id}", headers=auth_headers)
        assert resp.status_code == 204
        assert _active_value(session) == str(n3.id)

    def test_delete_nonactive_node_keeps_setting(
        self, client, session, admin_user, auth_headers,
    ):
        n1 = _mk_node(session, "active")
        n2 = _mk_node(session, "other")
        _set_active(session, n1.id)

        with _not_running():
            resp = client.delete(f"/api/nodes/{n2.id}", headers=auth_headers)
        assert resp.status_code == 204
        assert _active_value(session) == str(n1.id)

    def test_delete_last_enabled_node_clears_setting(
        self, client, session, admin_user, auth_headers,
    ):
        n1 = _mk_node(session, "active")
        _mk_node(session, "disabled", enabled=False)
        _set_active(session, n1.id)

        with _not_running():
            resp = client.delete(f"/api/nodes/{n1.id}", headers=auth_headers)
        assert resp.status_code == 204
        assert _active_value(session) == ""

    def test_heal_reapplies_config_when_xray_running(
        self, client, session, admin_user, auth_headers,
    ):
        n1 = _mk_node(session, "active")
        _mk_node(session, "survivor")
        _set_active(session, n1.id)

        with (
            mock.patch(
                "app.core.xray.XrayManager.is_running",
                new_callable=mock.PropertyMock, return_value=True,
            ),
            mock.patch(
                "app.api.system._regenerate_and_write", new_callable=AsyncMock,
            ) as regen,
            mock.patch(
                "app.api.system._apply_nftables", new_callable=AsyncMock,
            ) as nft,
            mock.patch(
                "app.core.xray.xray_manager.reload", new_callable=AsyncMock,
            ) as reload_mock,
            mock.patch(
                "app.api.system._pin_circle_balancer", new_callable=AsyncMock,
            ),
        ):
            resp = client.delete(f"/api/nodes/{n1.id}", headers=auth_headers)
        assert resp.status_code == 204
        regen.assert_awaited_once()
        nft.assert_awaited_once()
        reload_mock.assert_awaited_once()

    def test_no_survivors_while_running_stops_proxy(
        self, client, session, admin_user, auth_headers,
    ):
        n1 = _mk_node(session, "active")
        _set_active(session, n1.id)

        with (
            mock.patch(
                "app.core.xray.XrayManager.is_running",
                new_callable=mock.PropertyMock, return_value=True,
            ),
            mock.patch(
                "app.core.xray.xray_manager.stop", new_callable=AsyncMock,
            ) as stop_mock,
            mock.patch(
                "app.core.nftables.nftables_manager.flush",
                new_callable=AsyncMock,
            ) as flush_mock,
        ):
            resp = client.delete(f"/api/nodes/{n1.id}", headers=auth_headers)
        assert resp.status_code == 204
        assert _active_value(session) == ""
        stop_mock.assert_awaited_once()
        flush_mock.assert_awaited_once()


class TestDeleteSubscriptionHealsActive:
    def test_cascade_delete_heals_to_node_outside_subscription(
        self, client, session, admin_user, auth_headers,
    ):
        sub = Subscription(name="s", url="https://example.com/sub")
        session.add(sub)
        session.commit()
        session.refresh(sub)

        n1 = _mk_node(session, "sub-node-1", sub_id=sub.id)
        _mk_node(session, "sub-node-2", sub_id=sub.id)
        n3 = _mk_node(session, "standalone")
        _set_active(session, n1.id)

        with _not_running():
            resp = client.delete(
                f"/api/subscriptions/{sub.id}?delete_nodes=true",
                headers=auth_headers,
            )
        assert resp.status_code == 204
        assert _active_value(session) == str(n3.id)

    def test_keep_nodes_leaves_setting_alone(
        self, client, session, admin_user, auth_headers,
    ):
        sub = Subscription(name="s", url="https://example.com/sub")
        session.add(sub)
        session.commit()
        session.refresh(sub)

        n1 = _mk_node(session, "sub-node", sub_id=sub.id)
        _set_active(session, n1.id)

        with _not_running():
            resp = client.delete(
                f"/api/subscriptions/{sub.id}?delete_nodes=false",
                headers=auth_headers,
            )
        assert resp.status_code == 204
        assert _active_value(session) == str(n1.id)
