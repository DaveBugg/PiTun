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
    seen with a 1256-node subscription."""

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


class TestSubscriptionRefreshPreservesCircles:
    """When refresh removes a Node, every NodeCircle that referenced
    that Node id must have the dangling ref pruned. The circle stays
    enabled regardless of how many members survive: circle_scheduler
    is defensive (skips rotation when <2 members), and if all members
    die the routing layer falls back to kill-switch / direct anyway.
    Leaving the circle enabled lets it spring back to life if the
    operator re-adds nodes."""

    def test_circle_loses_dangling_id_after_node_removal(
        self, client, admin_user, auth_headers, session,
    ):
        import asyncio, json
        from app.api.subscriptions import _fetch_subscription_unlocked
        from app.models import Subscription, Node, NodeCircle

        # Subscription with 3 nodes
        sub = Subscription(name="Test", url="http://example/sub", enabled=True)
        session.add(sub)
        session.commit()
        session.refresh(sub)

        a = Node(name="a", protocol="vless", address="1.1.1.1", port=443,
                 uuid="a", transport="tcp", subscription_id=sub.id, enabled=True)
        b = Node(name="b", protocol="vless", address="2.2.2.2", port=443,
                 uuid="b", transport="tcp", subscription_id=sub.id, enabled=True)
        c = Node(name="c", protocol="vless", address="3.3.3.3", port=443,
                 uuid="c", transport="tcp", subscription_id=sub.id, enabled=True)
        for n in (a, b, c):
            session.add(n)
        session.commit()
        for n in (a, b, c):
            session.refresh(n)

        # Circle includes all 3
        circle = NodeCircle(
            name="rotate-all", node_ids=json.dumps([a.id, b.id, c.id]),
            mode="sequential", interval_min=5, interval_max=10,
            current_index=0, enabled=True,
        )
        session.add(circle)
        session.commit()
        session.refresh(circle)
        circle_id = circle.id

        # Refresh returns ONLY `a` and `b` — node `c` vanished
        with mock.patch(
            "app.api.subscriptions.httpx.AsyncClient"
        ) as mock_client:
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock.Mock(
                status_code=200,
                text=(
                    "vless://a@1.1.1.1:443?type=tcp#a\n"
                    "vless://b@2.2.2.2:443?type=tcp#b\n"
                ),
                raise_for_status=lambda: None,
            ))
            asyncio.run(_fetch_subscription_unlocked(sub.id))

        # Circle's node_ids should now be [a.id, b.id] — c.id pruned
        session.expire_all()
        circle_after = session.get(NodeCircle, circle_id)
        ids_after = json.loads(circle_after.node_ids)
        assert ids_after == [a.id, b.id], (
            f"circle didn't prune dangling id: {ids_after!r}"
        )
        # Still enabled — has 2 members which is enough to rotate
        assert circle_after.enabled is True

    def test_linked_circle_auto_syncs_and_keeps_manual_members(
        self, client, admin_user, auth_headers, session,
    ):
        """A circle linked to a subscription tracks it: nodes the panel
        still serves are added (including ones that came back under a NEW
        id — the case pruning alone can't fix), dropped ones leave, and
        members that didn't come from this subscription stay put."""
        import asyncio, json
        from app.api.subscriptions import _fetch_subscription_unlocked
        from app.models import Subscription, Node, NodeCircle

        sub = Subscription(name="Linked", url="http://example/sub", enabled=True)
        session.add(sub)
        session.commit()
        session.refresh(sub)

        # `a` survives the refresh, `c` disappears from the panel.
        a = Node(name="a", protocol="vless", address="1.1.1.1", port=443,
                 uuid="a", transport="tcp", subscription_id=sub.id, enabled=True)
        c = Node(name="c", protocol="vless", address="3.3.3.3", port=443,
                 uuid="c", transport="tcp", subscription_id=sub.id, enabled=True)
        # Standalone node added by hand — must survive the sync.
        manual = Node(name="manual", protocol="vless", address="9.9.9.9", port=443,
                      uuid="m", transport="tcp", enabled=True)
        for n in (a, c, manual):
            session.add(n)
        session.commit()
        for n in (a, c, manual):
            session.refresh(n)
        # Capture ids as plain ints: `c` is deleted by the refresh below, and
        # touching the ORM object afterwards raises DetachedInstanceError.
        a_id, c_id, manual_id = a.id, c.id, manual.id

        circle = NodeCircle(
            name="linked", node_ids=json.dumps([manual.id, a.id, c.id]),
            mode="sequential", interval_min=5, interval_max=10,
            current_index=2, enabled=True, subscription_id=sub.id,
        )
        session.add(circle)
        session.commit()
        session.refresh(circle)
        circle_id = circle.id

        # Panel now serves `a` and a brand-new `d`; `c` is gone.
        with mock.patch(
            "app.api.subscriptions.httpx.AsyncClient"
        ) as mock_client:
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock.Mock(
                status_code=200,
                text=(
                    "vless://a@1.1.1.1:443?type=tcp#a\n"
                    "vless://d@4.4.4.4:443?type=tcp#d\n"
                ),
                raise_for_status=lambda: None,
            ))
            asyncio.run(_fetch_subscription_unlocked(sub.id))

        session.expire_all()
        circle_after = session.get(NodeCircle, circle_id)
        ids_after = json.loads(circle_after.node_ids)

        from sqlmodel import select as sm_select
        d = session.exec(
            sm_select(Node).where(Node.name == "d")
        ).first()
        assert d is not None, "new node from the panel was not imported"

        # Manual member kept, subscription set refreshed, dead id gone.
        assert manual_id in ids_after, f"manual member dropped: {ids_after!r}"
        assert a_id in ids_after, f"surviving node missing: {ids_after!r}"
        assert d.id in ids_after, f"new node not auto-added: {ids_after!r}"
        assert c_id not in ids_after, f"removed node still present: {ids_after!r}"
        # current_index pointed at the (now gone) 3rd entry — must be in range.
        assert circle_after.current_index < len(ids_after)

    def test_unlinked_circle_is_not_auto_populated(
        self, client, admin_user, auth_headers, session,
    ):
        """Without an explicit link the old behaviour stands: dangling ids
        are pruned, but new subscription nodes are NOT pulled in — the
        operator manages membership."""
        import asyncio, json
        from app.api.subscriptions import _fetch_subscription_unlocked
        from app.models import Subscription, Node, NodeCircle

        sub = Subscription(name="Unlinked", url="http://example/sub", enabled=True)
        session.add(sub)
        session.commit()
        session.refresh(sub)

        a = Node(name="a", protocol="vless", address="1.1.1.1", port=443,
                 uuid="a", transport="tcp", subscription_id=sub.id, enabled=True)
        session.add(a)
        session.commit()
        session.refresh(a)

        circle = NodeCircle(
            name="manual-circle", node_ids=json.dumps([a.id]),
            mode="sequential", interval_min=5, interval_max=10,
            current_index=0, enabled=True,  # subscription_id stays None
        )
        session.add(circle)
        session.commit()
        session.refresh(circle)
        circle_id = circle.id

        with mock.patch(
            "app.api.subscriptions.httpx.AsyncClient"
        ) as mock_client:
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock.Mock(
                status_code=200,
                text=(
                    "vless://a@1.1.1.1:443?type=tcp#a\n"
                    "vless://d@4.4.4.4:443?type=tcp#d\n"
                ),
                raise_for_status=lambda: None,
            ))
            asyncio.run(_fetch_subscription_unlocked(sub.id))

        session.expire_all()
        ids_after = json.loads(session.get(NodeCircle, circle_id).node_ids)
        from sqlmodel import select as sm_select
        d = session.exec(sm_select(Node).where(Node.name == "d")).first()
        assert d is not None
        assert ids_after == [a.id], (
            f"unlinked circle should not absorb new nodes: {ids_after!r}"
        )

    def test_circle_stays_enabled_when_drops_below_two_members(
        self, client, admin_user, auth_headers, session,
    ):
        """Circle shrinking to 1 member is NOT auto-disabled. The
        scheduler skips rotation defensively; operator-visible state
        only changes if/when they re-edit the circle."""
        import asyncio, json
        from app.api.subscriptions import _fetch_subscription_unlocked
        from app.models import Subscription, Node, NodeCircle

        sub = Subscription(name="Test", url="http://example/sub", enabled=True)
        session.add(sub)
        session.commit()
        session.refresh(sub)

        a = Node(name="a", protocol="vless", address="1.1.1.1", port=443,
                 uuid="a", transport="tcp", subscription_id=sub.id, enabled=True)
        b = Node(name="b", protocol="vless", address="2.2.2.2", port=443,
                 uuid="b", transport="tcp", subscription_id=sub.id, enabled=True)
        for n in (a, b):
            session.add(n)
        session.commit()
        for n in (a, b):
            session.refresh(n)

        circle = NodeCircle(
            name="just-two", node_ids=json.dumps([a.id, b.id]),
            mode="sequential", interval_min=5, interval_max=10,
            current_index=0, enabled=True,
        )
        session.add(circle)
        session.commit()
        session.refresh(circle)
        circle_id = circle.id

        # Refresh keeps only `a` — `b` vanished, circle drops to 1 node
        with mock.patch(
            "app.api.subscriptions.httpx.AsyncClient"
        ) as mock_client:
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock.Mock(
                status_code=200,
                text="vless://a@1.1.1.1:443?type=tcp#a\n",
                raise_for_status=lambda: None,
            ))
            asyncio.run(_fetch_subscription_unlocked(sub.id))

        session.expire_all()
        circle_after = session.get(NodeCircle, circle_id)
        # 1 surviving node, current_index reset to 0
        assert json.loads(circle_after.node_ids) == [a.id]
        assert circle_after.current_index == 0
        # Stays enabled — scheduler will just skip it; if operator
        # adds nodes back via panel + refresh, rotation resumes.
        assert circle_after.enabled is True

    def test_empty_subscription_response_does_not_touch_circle(
        self, client, admin_user, auth_headers, session,
    ):
        """A flaky panel returning empty body must NOT cascade into
        wiping circle membership. The refresh itself bails out on
        '0 nodes parsed', so no nodes are removed, so no circle
        pruning happens. Belt-and-suspenders check that the safety
        net upstream still holds."""
        import asyncio, json
        from app.api.subscriptions import _fetch_subscription_unlocked
        from app.models import Subscription, Node, NodeCircle

        sub = Subscription(name="Test", url="http://example/sub", enabled=True)
        session.add(sub)
        session.commit()
        session.refresh(sub)

        a = Node(name="a", protocol="vless", address="1.1.1.1", port=443,
                 uuid="a", transport="tcp", subscription_id=sub.id, enabled=True)
        b = Node(name="b", protocol="vless", address="2.2.2.2", port=443,
                 uuid="b", transport="tcp", subscription_id=sub.id, enabled=True)
        for n in (a, b):
            session.add(n)
        session.commit()
        for n in (a, b):
            session.refresh(n)

        circle = NodeCircle(
            name="all-die", node_ids=json.dumps([a.id, b.id]),
            mode="sequential", interval_min=5, interval_max=10,
            current_index=1, enabled=True,
        )
        session.add(circle)
        session.commit()
        session.refresh(circle)
        circle_id = circle.id

        with mock.patch(
            "app.api.subscriptions.httpx.AsyncClient"
        ) as mock_client:
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock.Mock(
                status_code=200,
                text="",
                raise_for_status=lambda: None,
            ))
            asyncio.run(_fetch_subscription_unlocked(sub.id))

        session.expire_all()
        circle_after = session.get(NodeCircle, circle_id)
        # Both nodes still alive, circle untouched
        assert json.loads(circle_after.node_ids) == [a.id, b.id]
        assert circle_after.enabled is True


class TestSubscriptionRefreshDedupsParsed:
    """Panels (especially Happ JSON bundles) often return the SAME
    (addr,port,uuid) server under multiple SNI/fingerprint variants.
    Our Node model treats those as a single row (see
    `_node_fingerprint`). The upsert must collapse parsed entries to
    one-per-fingerprint, otherwise legacy duplicate rows accumulate
    and never get cleaned up. Also: refs from active_node + circles
    must remap from any deleted dup to the surviving sibling."""

    def test_parsed_duplicates_collapse_to_unique_count(
        self, client, admin_user, auth_headers, session,
    ):
        import asyncio, json
        from app.api.subscriptions import _fetch_subscription_unlocked
        from app.models import Subscription, Node

        sub = Subscription(name="Test", url="http://example/sub", enabled=True)
        session.add(sub)
        session.commit()
        session.refresh(sub)

        # Panel returns 5 lines for the SAME (addr,port,uuid), differing
        # only in SNI — these should collapse to 1 Node row.
        body = "\n".join(
            f"vless://uuid-A@server.example:443?type=tcp&sni=sni{i}.example#name{i}"
            for i in range(5)
        )

        with mock.patch(
            "app.api.subscriptions.httpx.AsyncClient"
        ) as mock_client:
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock.Mock(
                status_code=200, text=body,
                raise_for_status=lambda: None,
            ))
            asyncio.run(_fetch_subscription_unlocked(sub.id))

        session.expire_all()
        nodes = session.query(Node).filter(
            Node.subscription_id == sub.id
        ).all()
        assert len(nodes) == 1, (
            f"5 SNI variants of one server should collapse to 1 Node, "
            f"got {len(nodes)}"
        )

    def test_legacy_duplicates_in_db_collapse_on_refresh(
        self, client, admin_user, auth_headers, session,
    ):
        """Inverse case: DB already carries 3 legacy duplicate rows
        (same fingerprint) from pre-1.3.6 inserts; refresh must keep
        the smallest-id row and delete the other two."""
        import asyncio, json
        from app.api.subscriptions import _fetch_subscription_unlocked
        from app.models import Subscription, Node

        sub = Subscription(name="Test", url="http://example/sub", enabled=True)
        session.add(sub)
        session.commit()
        session.refresh(sub)

        # 3 rows, all with same (protocol, addr, port, uuid) — only
        # SNI varies. They're legacy dups that need collapsing.
        rows = [
            Node(name=f"dup{i}", protocol="vless", address="server.example",
                 port=443, uuid="uuid-A", transport="tcp", sni=f"sni{i}",
                 subscription_id=sub.id, enabled=True)
            for i in range(3)
        ]
        for r in rows:
            session.add(r)
        session.commit()
        for r in rows:
            session.refresh(r)
        ids_before = sorted(r.id for r in rows)
        survivor_id = ids_before[0]

        with mock.patch(
            "app.api.subscriptions.httpx.AsyncClient"
        ) as mock_client:
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock.Mock(
                status_code=200,
                text="vless://uuid-A@server.example:443?type=tcp&sni=fresh#name",
                raise_for_status=lambda: None,
            ))
            asyncio.run(_fetch_subscription_unlocked(sub.id))

        session.expire_all()
        nodes = session.query(Node).filter(
            Node.subscription_id == sub.id
        ).all()
        assert len(nodes) == 1, (
            f"3 legacy dups should collapse to 1, got {len(nodes)}"
        )
        assert nodes[0].id == survivor_id, (
            f"survivor should be smallest-id row {survivor_id}, "
            f"got {nodes[0].id}"
        )

    def test_active_node_and_circle_remap_through_legacy_dup_collapse(
        self, client, admin_user, auth_headers, session,
    ):
        """When the active node OR a circle member is one of the
        deleted legacy dups, both must transparently remap to the
        surviving sibling — user never notices."""
        import asyncio, json
        from app.api.subscriptions import _fetch_subscription_unlocked
        from app.models import Subscription, Node, NodeCircle
        from app.models import Settings as DBSettings

        sub = Subscription(name="Test", url="http://example/sub", enabled=True)
        session.add(sub)
        session.commit()
        session.refresh(sub)

        # Two dup groups: group A (3 rows, same fingerprint) and
        # group B (2 rows, same fingerprint).
        a_rows = [
            Node(name=f"A{i}", protocol="vless", address="srv-a",
                 port=443, uuid="uuid-A", transport="tcp", sni=f"sni-a{i}",
                 subscription_id=sub.id, enabled=True)
            for i in range(3)
        ]
        b_rows = [
            Node(name=f"B{i}", protocol="vless", address="srv-b",
                 port=443, uuid="uuid-B", transport="tcp", sni=f"sni-b{i}",
                 subscription_id=sub.id, enabled=True)
            for i in range(2)
        ]
        for r in a_rows + b_rows:
            session.add(r)
        session.commit()
        for r in a_rows + b_rows:
            session.refresh(r)

        a_survivor = sorted(r.id for r in a_rows)[0]
        a_dup = sorted(r.id for r in a_rows)[2]  # one of the dups to die
        b_survivor = sorted(r.id for r in b_rows)[0]
        b_dup = sorted(r.id for r in b_rows)[1]

        # Active node pinned at a dup that's about to die
        session.add(DBSettings(key="active_node_id", value=str(a_dup)))
        # Circle uses one dup of A and one dup of B (both must remap)
        circle = NodeCircle(
            name="cross-dup", node_ids=json.dumps([a_dup, b_dup]),
            mode="sequential", interval_min=5, interval_max=10,
            current_index=0, enabled=True,
        )
        session.add(circle)
        session.commit()
        session.refresh(circle)
        circle_id = circle.id

        body = (
            "vless://uuid-A@srv-a:443?type=tcp&sni=fresh-a#A\n"
            "vless://uuid-B@srv-b:443?type=tcp&sni=fresh-b#B\n"
        )
        with mock.patch(
            "app.api.subscriptions.httpx.AsyncClient"
        ) as mock_client:
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock.Mock(
                status_code=200, text=body,
                raise_for_status=lambda: None,
            ))
            asyncio.run(_fetch_subscription_unlocked(sub.id))

        session.expire_all()
        # Active remapped to A's survivor
        active = session.query(DBSettings).filter(
            DBSettings.key == "active_node_id"
        ).first()
        assert int(active.value) == a_survivor, (
            f"active_node_id should remap from dup {a_dup} to "
            f"survivor {a_survivor}, got {active.value}"
        )
        # Circle remapped both members
        circle_after = session.get(NodeCircle, circle_id)
        assert json.loads(circle_after.node_ids) == [a_survivor, b_survivor]
        assert circle_after.enabled is True

    def test_routing_rule_action_remaps_through_legacy_dup_collapse(
        self, client, admin_user, auth_headers, session,
    ):
        """A RoutingRule with `action="node:<dup_id>"` must follow the
        legacy-dup collapse to the survivor, otherwise the rule points
        at a deleted row and silently fails to route (config_gen skips
        unresolvable node-actions). User would see traffic falling
        through to the catch-all rule with no warning."""
        import asyncio, json
        from app.api.subscriptions import _fetch_subscription_unlocked
        from app.models import Subscription, Node, RoutingRule

        sub = Subscription(name="Test", url="http://example/sub", enabled=True)
        session.add(sub)
        session.commit()
        session.refresh(sub)

        # 3 dup rows, all same fingerprint.
        rows = [
            Node(name=f"A{i}", protocol="vless", address="srv-a", port=443,
                 uuid="uuid-A", transport="tcp", sni=f"sni{i}",
                 subscription_id=sub.id, enabled=True)
            for i in range(3)
        ]
        for r in rows:
            session.add(r)
        session.commit()
        for r in rows:
            session.refresh(r)
        ids = sorted(r.id for r in rows)
        survivor_id, dup_id, other_dup = ids[0], ids[1], ids[2]

        # Three rules: one on a dup (should remap), one on the survivor
        # (should NOT change), one on a non-dup id we leave untouched.
        rule_on_dup = RoutingRule(
            name="dup-rule", rule_type="domain", match_value="example.com",
            action=f"node:{dup_id}", order=1, enabled=True,
        )
        rule_on_survivor = RoutingRule(
            name="survivor-rule", rule_type="domain", match_value="other.example.com",
            action=f"node:{survivor_id}", order=2, enabled=True,
        )
        rule_passthrough = RoutingRule(
            name="passthrough", rule_type="domain", match_value="passthrough.example.com",
            action="proxy", order=3, enabled=True,
        )
        for r in (rule_on_dup, rule_on_survivor, rule_passthrough):
            session.add(r)
        session.commit()
        for r in (rule_on_dup, rule_on_survivor, rule_passthrough):
            session.refresh(r)
        rule_on_dup_id = rule_on_dup.id
        rule_on_survivor_id = rule_on_survivor.id
        rule_passthrough_id = rule_passthrough.id

        body = "vless://uuid-A@srv-a:443?type=tcp&sni=fresh#A\n"
        with mock.patch(
            "app.api.subscriptions.httpx.AsyncClient"
        ) as mock_client:
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock.Mock(
                status_code=200, text=body,
                raise_for_status=lambda: None,
            ))
            asyncio.run(_fetch_subscription_unlocked(sub.id))

        session.expire_all()
        # Rule pointing at the dup → remapped to survivor
        rule_on_dup_after = session.get(RoutingRule, rule_on_dup_id)
        assert rule_on_dup_after.action == f"node:{survivor_id}", (
            f"rule on dup should remap to node:{survivor_id}, "
            f"got {rule_on_dup_after.action!r}"
        )
        # Rule already pointing at the survivor → untouched
        rule_on_survivor_after = session.get(RoutingRule, rule_on_survivor_id)
        assert rule_on_survivor_after.action == f"node:{survivor_id}"
        # Non-node rule → untouched
        rule_passthrough_after = session.get(RoutingRule, rule_passthrough_id)
        assert rule_passthrough_after.action == "proxy"


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
