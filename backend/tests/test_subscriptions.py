"""Tests for subscription CRUD, cascade delete."""
from unittest import mock
from unittest.mock import AsyncMock

import pytest


class TestSubscriptionList:
    def test_list_empty(self, client, admin_user, auth_headers):
        resp = client.get("/api/subscriptions", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_data(self, client, admin_user, auth_headers, sample_subscription):
        resp = client.get("/api/subscriptions", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["name"] == "Test Sub"


class TestSubscriptionCreate:
    def test_create(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/subscriptions",
            json={"name": "New Sub", "url": "https://external.com/sub", "ua": "clash"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "New Sub"
        assert data["url"] == "https://external.com/sub"
        assert "id" in data


class TestSubscriptionGet:
    def test_get(self, client, admin_user, auth_headers, sample_subscription):
        resp = client.get(f"/api/subscriptions/{sample_subscription.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Sub"

    def test_get_not_found(self, client, admin_user, auth_headers):
        resp = client.get("/api/subscriptions/9999", headers=auth_headers)
        assert resp.status_code == 404


class TestSubscriptionUpdate:
    def test_update(self, client, admin_user, auth_headers, sample_subscription):
        resp = client.patch(
            f"/api/subscriptions/{sample_subscription.id}",
            json={"name": "Renamed", "enabled": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Renamed"
        assert data["enabled"] is False

    def test_update_not_found(self, client, admin_user, auth_headers):
        resp = client.patch("/api/subscriptions/9999", json={"name": "x"}, headers=auth_headers)
        assert resp.status_code == 404


class TestSubscriptionDelete:
    def test_delete_with_cascade(self, client, admin_user, auth_headers, session, sample_subscription):
        from app.models import Node

        node = Node(
            name="Sub Node", protocol="vless", address="1.1.1.1", port=443,
            uuid="sub-uuid", transport="ws", enabled=True, order=0,
            subscription_id=sample_subscription.id,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        node_id = node.id

        resp = client.delete(
            f"/api/subscriptions/{sample_subscription.id}?delete_nodes=true",
            headers=auth_headers,
        )
        assert resp.status_code == 204

        resp2 = client.get(f"/api/nodes/{node_id}", headers=auth_headers)
        assert resp2.status_code == 404

    def test_delete_without_cascade(self, client, admin_user, auth_headers, session, sample_subscription):
        from app.models import Node

        node = Node(
            name="Keep Node", protocol="vless", address="2.2.2.2", port=443,
            uuid="keep-uuid", transport="ws", enabled=True, order=0,
            subscription_id=sample_subscription.id,
        )
        session.add(node)
        session.commit()
        session.refresh(node)
        node_id = node.id

        resp = client.delete(
            f"/api/subscriptions/{sample_subscription.id}?delete_nodes=false",
            headers=auth_headers,
        )
        assert resp.status_code == 204

        resp2 = client.get(f"/api/nodes/{node_id}", headers=auth_headers)
        assert resp2.status_code == 200

    def test_delete_not_found(self, client, admin_user, auth_headers):
        resp = client.delete("/api/subscriptions/9999", headers=auth_headers)
        assert resp.status_code == 404


# ── Refresh-time upsert + active-node preservation (since v1.3.6) ────────────


class TestSubscriptionFingerprint:
    """`_node_fingerprint` is the stable identity used to match new
    subscription entries back to existing Node DB rows on refresh.
    Pin the shape so changes to the formula are intentional — a
    quietly broadened fingerprint would re-cause the 1.3.5 bug where
    every refresh invalidates `active_node_id`."""

    def test_fingerprint_stable_across_calls(self):
        from app.api.subscriptions import _node_fingerprint
        d = {
            "protocol": "vless", "address": "1.2.3.4", "port": 443,
            "uuid": "abc", "transport": "tcp", "tls": "reality",
        }
        assert _node_fingerprint(d) == _node_fingerprint({**d, "name": "Renamed"})

    def test_fingerprint_changes_on_protocol(self):
        from app.api.subscriptions import _node_fingerprint
        d1 = {"protocol": "vless", "address": "1.2.3.4", "port": 443, "uuid": "x"}
        d2 = {**d1, "protocol": "trojan"}
        assert _node_fingerprint(d1) != _node_fingerprint(d2)

    def test_fingerprint_changes_on_address(self):
        from app.api.subscriptions import _node_fingerprint
        d1 = {"protocol": "vless", "address": "1.2.3.4", "port": 443, "uuid": "x"}
        d2 = {**d1, "address": "5.6.7.8"}
        assert _node_fingerprint(d1) != _node_fingerprint(d2)

    def test_fingerprint_ignores_sni(self):
        """SNI rotation (panels with random cover-domain pools) must
        NOT look like a brand-new node — operators expect a refresh
        to preserve their reorder + active selection across SNI
        churn."""
        from app.api.subscriptions import _node_fingerprint
        d1 = {"protocol": "vless", "address": "1.2.3.4", "port": 443,
              "uuid": "x", "sni": "first.example"}
        d2 = {**d1, "sni": "second.example"}
        assert _node_fingerprint(d1) == _node_fingerprint(d2)

    def test_row_and_dict_fingerprints_match(self):
        """`_node_row_fingerprint` (operating on ORM row) and
        `_node_fingerprint` (operating on parsed dict) MUST be
        symmetric — otherwise the upsert loop can't find matches."""
        from app.api.subscriptions import _node_fingerprint, _node_row_fingerprint
        from app.models import Node

        node = Node(
            id=42, name="test", protocol="vless", address="1.2.3.4",
            port=443, uuid="abc", transport="tcp", tls="reality",
        )
        d = {
            "protocol": "vless", "address": "1.2.3.4", "port": 443,
            "uuid": "abc", "transport": "tcp", "tls": "reality",
        }
        assert _node_row_fingerprint(node) == _node_fingerprint(d)


class TestSubscriptionRefreshUpsert:
    """End-to-end test for the fingerprint-based upsert that survives
    `active_node_id` across a refresh. Drives the same code path as
    the real `_fetch_subscription` but with the network fetch stubbed
    to a deterministic URI list. Mirrors the real-world failure mode
    the user hit on 192.168.1.4 with a 1256-node subscription."""

    def test_active_node_survives_refresh_when_node_returns(
        self, client, admin_user, auth_headers, session,
    ):
        """Active node still in the parsed list AFTER refresh →
        node id unchanged, `active_node_id` setting unchanged.
        This is the "panel returned the same servers again" case —
        the most common one."""
        import asyncio
        from app.api.subscriptions import _fetch_subscription_unlocked
        from app.models import Subscription, Node, Settings as DBSettings

        sub = Subscription(name="Test", url="http://example/sub", enabled=True)
        session.add(sub)
        session.commit()
        session.refresh(sub)

        original = Node(
            name="east-1", protocol="vless", address="1.2.3.4", port=443,
            uuid="aaa", transport="tcp", tls="reality",
            subscription_id=sub.id, enabled=True, order=10,
        )
        session.add(original)
        session.add(DBSettings(key="active_node_id", value=""))
        session.commit()
        session.refresh(original)

        # Set active node
        active_row = session.query(DBSettings).filter(
            DBSettings.key == "active_node_id"
        ).first()
        active_row.value = str(original.id)
        session.add(active_row)
        session.commit()
        original_id = original.id

        # Stub the network fetch to return the SAME node verbatim
        with mock.patch(
            "app.api.subscriptions.httpx.AsyncClient"
        ) as mock_client:
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock.Mock(
                status_code=200,
                text="vless://aaa@1.2.3.4:443?type=tcp&security=reality#east-1",
                raise_for_status=lambda: None,
            ))
            asyncio.run(_fetch_subscription_unlocked(sub.id))

        # Active row still points at the same id — upsert preserved
        # the row identity through the refresh.
        session.expire_all()
        active_after = session.query(DBSettings).filter(
            DBSettings.key == "active_node_id"
        ).first()
        assert active_after.value == str(original_id), (
            f"active_node_id changed: was {original_id}, now {active_after.value}"
        )
        # The node row itself is still there with the same id
        from sqlmodel import select as sm_select
        same_node = session.exec(
            sm_select(Node).where(Node.id == original_id)
        ).first()
        assert same_node is not None
        assert same_node.address == "1.2.3.4"


class TestSubscriptionRefreshMutex:
    """The endpoint must refuse a second `/refresh` while a previous
    one is still in flight. Without this, two clicks within ~100ms
    used to race two background fetch tasks against the same
    subscription — sometimes truncating the imported node set when
    one of them caught a rate-limited panel response."""

    def test_concurrent_refresh_returns_409(
        self, client, admin_user, auth_headers, sample_subscription,
    ):
        from app.api import subscriptions as subs_mod
        sub_id = sample_subscription.id

        # Simulate "previous refresh still running" by populating the
        # in-flight set. TestClient runs BackgroundTasks synchronously
        # so we can't realistically time two POSTs to overlap; the
        # in-flight set is what the endpoint actually checks anyway.
        subs_mod._REFRESH_IN_FLIGHT.add(sub_id)
        try:
            resp = client.post(
                f"/api/subscriptions/{sub_id}/refresh",
                headers=auth_headers,
            )
            assert resp.status_code == 409, (
                f"expected 409 Conflict on concurrent refresh, got "
                f"{resp.status_code}: {resp.text!r}"
            )
            detail = resp.json().get("detail")
            assert isinstance(detail, dict)
            assert detail.get("subscription_id") == sub_id
            assert "in progress" in detail.get("error", "").lower()
        finally:
            subs_mod._REFRESH_IN_FLIGHT.discard(sub_id)
