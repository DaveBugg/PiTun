"""Both dataplane layers must move together.

xray owns L3/L7 routing, nftables owns MAC bypass and the TPROXY redirect.
Several paths used to touch only one of them, so a rule the UI reported as
applied was not actually in effect (and, worse, a DELETED MAC bypass kept
bypassing until the next restart).
"""
from unittest import mock
from unittest.mock import AsyncMock

import pytest

from app.models import Node, RoutingRule, Settings as DBSettings


@pytest.fixture(name="running_xray")
def running_xray_fixture():
    """xray reports as running; reload/start/restart/nftables are captured."""
    with (
        mock.patch(
            "app.core.xray.XrayManager.is_running",
            new_callable=mock.PropertyMock, return_value=True,
        ),
        mock.patch(
            "app.core.xray.xray_manager.reload", new_callable=AsyncMock,
        ) as reload_mock,
        mock.patch(
            "app.core.xray.xray_manager.restart", new_callable=AsyncMock,
        ) as restart_mock,
        mock.patch(
            "app.api.system._regenerate_and_write", new_callable=AsyncMock,
        ) as regen_mock,
        mock.patch(
            "app.core.nftables.nftables_manager.apply_rules",
            new_callable=AsyncMock,
        ) as nft_mock,
        mock.patch(
            "app.core.nftables.nftables_manager.flush", new_callable=AsyncMock,
        ) as flush_mock,
    ):
        yield {
            "reload": reload_mock, "restart": restart_mock,
            "regen": regen_mock, "nft": nft_mock, "flush": flush_mock,
        }


class TestRoutingRuleReappliesNftables:
    """`mac` rules never reach the xray config (config_gen skips them —
    nftables owns L2), so reloading only xray made them a no-op."""

    def test_create_mac_rule_reapplies_nftables(
        self, client, admin_user, auth_headers, default_settings, running_xray,
    ):
        resp = client.post(
            "/api/routing/rules",
            json={
                "name": "bypass-tv", "rule_type": "mac",
                "match_value": "aa:bb:cc:dd:ee:ff",
                "action": "direct", "enabled": True,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        running_xray["reload"].assert_awaited()
        running_xray["nft"].assert_awaited()

    def test_delete_mac_rule_reapplies_nftables(
        self, client, admin_user, auth_headers, default_settings, running_xray,
    ):
        created = client.post(
            "/api/routing/rules",
            json={
                "name": "bypass-tv", "rule_type": "mac",
                "match_value": "aa:bb:cc:dd:ee:ff",
                "action": "direct", "enabled": True,
            },
            headers=auth_headers,
        ).json()
        running_xray["nft"].reset_mock()

        resp = client.delete(
            f"/api/routing/rules/{created['id']}", headers=auth_headers,
        )
        assert resp.status_code in (200, 204)
        # Without this the device stays in the nft bypass set and keeps
        # skipping every rule until the next manual restart.
        running_xray["nft"].assert_awaited()

    def test_nftables_receives_split_multi_mac_value(
        self, client, admin_user, auth_headers, default_settings, running_xray,
    ):
        client.post(
            "/api/routing/rules",
            json={
                "name": "bypass-pair", "rule_type": "mac",
                "match_value": "aa:bb:cc:dd:ee:ff, 11:22:33:44:55:66",
                "action": "direct", "enabled": True,
            },
            headers=auth_headers,
        )
        kwargs = running_xray["nft"].await_args.kwargs
        assert kwargs["bypass_macs"] == [
            "aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66",
        ]


class TestSetModeAppliesDataplane:
    def test_switch_to_bypass_flushes_nftables(
        self, client, admin_user, auth_headers, default_settings, running_xray,
    ):
        resp = client.post(
            "/api/system/mode", json={"mode": "bypass"}, headers=auth_headers,
        )
        assert resp.status_code == 204
        running_xray["regen"].assert_awaited()
        running_xray["reload"].assert_awaited()
        # bypass mode means "no tproxy at all" — the table is flushed.
        running_xray["flush"].assert_awaited()

    def test_switch_to_rules_applies_nftables(
        self, client, admin_user, auth_headers, default_settings, running_xray,
    ):
        resp = client.post(
            "/api/system/mode", json={"mode": "rules"}, headers=auth_headers,
        )
        assert resp.status_code == 204
        running_xray["nft"].assert_awaited()
        running_xray["reload"].assert_awaited()

    def test_mode_change_while_stopped_touches_nothing(
        self, client, admin_user, auth_headers, default_settings,
    ):
        with (
            mock.patch(
                "app.core.xray.XrayManager.is_running",
                new_callable=mock.PropertyMock, return_value=False,
            ),
            mock.patch(
                "app.api.system._regenerate_and_write", new_callable=AsyncMock,
            ) as regen,
        ):
            resp = client.post(
                "/api/system/mode", json={"mode": "bypass"},
                headers=auth_headers,
            )
        assert resp.status_code == 204
        regen.assert_not_awaited()  # /system/start will build both layers


class TestStartOrderXrayBeforeNftables:
    """Applying nftables first meant a failed start left the whole LAN
    redirected into a TPROXY port with no listener."""

    def test_start_applies_nftables_after_xray(
        self, client, admin_user, auth_headers, default_settings,
    ):
        order = []
        with (
            mock.patch(
                "app.core.xray.XrayManager.is_running",
                new_callable=mock.PropertyMock, return_value=False,
            ),
            mock.patch("app.api.system._regenerate_and_write", new_callable=AsyncMock),
            mock.patch(
                "app.core.xray.xray_manager.start", new_callable=AsyncMock,
                side_effect=lambda *a, **k: order.append("xray"),
            ),
            mock.patch(
                "app.api.system._apply_nftables", new_callable=AsyncMock,
                side_effect=lambda *a, **k: (order.append("nft"), "tproxy")[1],
            ),
        ):
            resp = client.post("/api/system/start", headers=auth_headers)
        assert resp.status_code == 204
        assert order == ["xray", "nft"]

    def test_failed_start_leaves_nftables_untouched(
        self, client, admin_user, auth_headers, default_settings,
    ):
        # The apply runs after the response (a restart would otherwise kill
        # the caller's own connection), so the request itself succeeds and
        # the failure is reported through the events feed. What must NOT
        # happen either way: nftables steering the LAN into a dead port.
        with (
            mock.patch(
                "app.core.xray.XrayManager.is_running",
                new_callable=mock.PropertyMock, return_value=False,
            ),
            mock.patch("app.api.system._regenerate_and_write", new_callable=AsyncMock),
            mock.patch(
                "app.core.xray.xray_manager.start", new_callable=AsyncMock,
                side_effect=RuntimeError("xray binary missing"),
            ),
            mock.patch(
                "app.api.system._apply_nftables", new_callable=AsyncMock,
            ) as nft,
            mock.patch(
                "app.core.dataplane._record_failure", new_callable=AsyncMock,
            ) as failure,
        ):
            resp = client.post("/api/system/start", headers=auth_headers)
        assert resp.status_code == 204
        nft.assert_not_awaited()
        failure.assert_awaited_once()

    def test_restart_applies_nftables_after_xray(
        self, client, admin_user, auth_headers, default_settings,
    ):
        order = []
        with (
            mock.patch("app.api.system._regenerate_and_write", new_callable=AsyncMock),
            mock.patch(
                "app.core.xray.xray_manager.restart", new_callable=AsyncMock,
                side_effect=lambda *a, **k: order.append("xray"),
            ),
            mock.patch(
                "app.api.system._apply_nftables", new_callable=AsyncMock,
                side_effect=lambda *a, **k: (order.append("nft"), "tproxy")[1],
            ),
        ):
            resp = client.post("/api/system/restart", headers=auth_headers)
        assert resp.status_code == 204
        assert order == ["xray", "nft"]


class TestWatchdogBypassMacs:
    """The auto-restart path rebuilds nftables from scratch. It used to
    collect MAC bypasses with a raw list-comp, so a comma-separated
    multi-MAC rule was passed through as one token, failed validation and
    silently vanished after every crash or reboot."""

    def test_auto_restart_splits_multi_mac_rules(self, client, session):
        import asyncio

        session.add(RoutingRule(
            name="bypass-pair", rule_type="mac",
            match_value="aa:bb:cc:dd:ee:ff, 11:22:33:44:55:66",
            action="direct", enabled=True, order=0,
        ))
        session.add(DBSettings(key="mode", value="rules"))
        session.add(DBSettings(key="auto_restart_xray", value="true"))
        session.add(Node(
            name="n", protocol="vless", address="1.2.3.4", port=443,
            uuid="u", transport="tcp", tls="none", enabled=True,
        ))
        session.commit()

        from app.core import xray as xray_mod

        # `xray run -test` would try to exec the (absent) binary.
        verify_proc = mock.MagicMock()
        verify_proc.returncode = 0
        verify_proc.communicate = AsyncMock(return_value=(b"", b""))

        with (
            mock.patch("asyncio.sleep", new_callable=AsyncMock),
            mock.patch(
                "asyncio.create_subprocess_exec", new_callable=AsyncMock,
                return_value=verify_proc,
            ),
            mock.patch.object(
                xray_mod.xray_manager, "_start_unlocked", new_callable=AsyncMock,
            ),
            mock.patch(
                "app.core.nftables.nftables_manager.apply_rules",
                new_callable=AsyncMock,
            ) as nft,
            mock.patch(
                "app.api.system._regenerate_and_write", new_callable=AsyncMock,
            ),
            mock.patch(
                "app.core.device_scanner.get_device_macs_for_mode",
                new_callable=AsyncMock,
                return_value={"mode": "all", "include_macs": [], "exclude_macs": []},
            ),
        ):
            asyncio.run(xray_mod._auto_restart_if_enabled(from_boot=True))

        nft.assert_awaited()
        assert nft.await_args.kwargs["bypass_macs"] == [
            "aa:bb:cc:dd:ee:ff", "11:22:33:44:55:66",
        ]


class TestSubscriptionRefreshReloadsDataplane:
    """A panel rotating a Reality key updates the row in place — the
    fingerprint still matches — while xray keeps dialling with the old
    crypto. Health checks TCP-connect to the fresh address and stay green."""

    def _seed(self, session, *, pbk="old-pbk", active=True):
        from app.models import Subscription

        sub = Subscription(name="s", url="https://example.com/sub")
        session.add(sub)
        session.commit()
        session.refresh(sub)
        node = Node(
            name="n", protocol="vless", address="1.2.3.4", port=443,
            uuid="the-uuid", transport="tcp", tls="reality",
            reality_pbk=pbk, subscription_id=sub.id, enabled=True,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        if active:
            session.add(DBSettings(key="active_node_id", value=str(node.id)))
            session.commit()
        return sub, node

    def _refresh(self, sub_id, uri):
        import asyncio

        from app.api.subscriptions import _fetch_subscription_unlocked

        class _Resp:
            status_code = 200
            text = uri

            def raise_for_status(self):
                return None

        class _Client:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def get(self, *a, **k):
                return _Resp()

        with mock.patch("httpx.AsyncClient", _Client):
            asyncio.run(_fetch_subscription_unlocked(sub_id))

    @staticmethod
    def _uri(pbk):
        return (
            "vless://the-uuid@1.2.3.4:443?type=tcp&security=reality"
            f"&pbk={pbk}&sni=example.com#n"
        )

    def test_rotated_reality_key_on_active_node_reloads(self, client, session):
        sub, _node = self._seed(session)
        with (
            mock.patch(
                "app.core.xray.XrayManager.is_running",
                new_callable=mock.PropertyMock, return_value=True,
            ),
            mock.patch(
                "app.api.system._regenerate_and_write", new_callable=AsyncMock,
            ) as regen,
            mock.patch(
                "app.core.xray.xray_manager.reload", new_callable=AsyncMock,
            ) as reload_mock,
            mock.patch("app.api.system._apply_nftables", new_callable=AsyncMock),
        ):
            self._refresh(sub.id, self._uri("new-pbk"))
        regen.assert_awaited()
        reload_mock.assert_awaited()

    def test_unchanged_refresh_does_not_reload(self, client, session):
        sub, _node = self._seed(session)
        # First refresh normalises the hand-seeded row to exactly what the
        # parser produces; the SECOND one is the true no-op case.
        with mock.patch(
            "app.core.xray.XrayManager.is_running",
            new_callable=mock.PropertyMock, return_value=False,
        ):
            self._refresh(sub.id, self._uri("old-pbk"))

        with (
            mock.patch(
                "app.core.xray.XrayManager.is_running",
                new_callable=mock.PropertyMock, return_value=True,
            ),
            mock.patch(
                "app.api.system._regenerate_and_write", new_callable=AsyncMock,
            ) as regen,
            mock.patch(
                "app.core.xray.xray_manager.reload", new_callable=AsyncMock,
            ) as reload_mock,
        ):
            self._refresh(sub.id, self._uri("old-pbk"))
        regen.assert_not_awaited()
        reload_mock.assert_not_awaited()

    def test_change_on_unused_node_does_not_reload(self, client, session):
        # Node exists but nothing in the config references it.
        sub, _node = self._seed(session, active=False)
        with (
            mock.patch(
                "app.core.xray.XrayManager.is_running",
                new_callable=mock.PropertyMock, return_value=True,
            ),
            mock.patch(
                "app.api.system._regenerate_and_write", new_callable=AsyncMock,
            ) as regen,
            mock.patch(
                "app.core.xray.xray_manager.reload", new_callable=AsyncMock,
            ) as reload_mock,
        ):
            self._refresh(sub.id, self._uri("new-pbk"))
        regen.assert_not_awaited()
        reload_mock.assert_not_awaited()


class TestResponseIsSentBeforeXrayRestart:
    """`reload()` restarts the xray PROCESS, dropping every connection it
    carries. When the operator reaches the UI through the box (LAN proxy,
    or simply gateway routing), that includes the very request asking for
    the change — the browser reported "Network Error" / ERR_EMPTY_RESPONSE
    on a switch that had actually applied, and nginx logged a 499.

    So the restart must be queued as a BackgroundTask, which Starlette runs
    only after the response has been flushed.
    """

    ENDPOINTS = [
        ("post", "/api/system/active-node", {"node_id": None}),
        ("post", "/api/system/mode", {"mode": "rules"}),
        ("post", "/api/system/reload-config", None),
        ("post", "/api/system/start", None),
        ("post", "/api/system/restart", None),
        ("post", "/api/system/stop", None),
    ]

    def _call(self, client, auth_headers, method, url, body, node_id):
        if body and body.get("node_id", "sentinel") is None:
            body = {**body, "node_id": node_id}
        return getattr(client, method)(url, json=body, headers=auth_headers)

    @pytest.mark.parametrize(("method", "url", "body"), ENDPOINTS)
    def test_restart_is_queued_not_awaited_inline(
        self, client, session, admin_user, auth_headers, default_settings,
        method, url, body,
    ):
        node = Node(
            name="n", protocol="vless", address="1.2.3.4", port=443,
            uuid="u", transport="tcp", tls="none", enabled=True,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        node_id = node.id

        order: list[str] = []

        with (
            mock.patch(
                "app.core.xray.XrayManager.is_running",
                new_callable=mock.PropertyMock, return_value=True,
            ),
            mock.patch("app.api.system._regenerate_and_write", new_callable=AsyncMock),
            mock.patch("app.api.system._apply_nftables", new_callable=AsyncMock),
            mock.patch("app.api.system._pin_circle_balancer", new_callable=AsyncMock),
            mock.patch(
                "app.core.dataplane.apply_dataplane", new_callable=AsyncMock,
                side_effect=lambda *a, **k: order.append("dataplane"),
            ) as applied,
            mock.patch(
                "starlette.responses.Response.__call__", autospec=True,
            ) as send_response,
        ):
            send_response.side_effect = _record_response(order)
            resp = self._call(client, auth_headers, method, url, body, node_id)

        assert resp.status_code == 204, resp.text
        applied.assert_awaited_once()
        # The response goes out first; only then does xray get restarted.
        assert order.index("response") < order.index("dataplane"), (
            f"{url} restarts xray before answering — that kills its own "
            f"response when the client is routed through the box"
        )


def _record_response(order):
    """Wrap Starlette's Response.__call__ so we can see when it ran."""
    original = _ORIGINAL_RESPONSE_CALL

    async def _call(self, scope, receive, send):
        order.append("response")
        return await original(self, scope, receive, send)

    return _call


import starlette.responses as _starlette_responses  # noqa: E402

_ORIGINAL_RESPONSE_CALL = _starlette_responses.Response.__call__
