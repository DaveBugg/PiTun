"""Tests for node CRUD, import, reorder, and cascade delete."""
import pytest


class TestNodeCRUD:
    def test_list_nodes_empty(self, client, admin_user, auth_headers):
        resp = client.get("/api/nodes", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_node(self, client, admin_user, auth_headers):
        node_data = {
            "name": "My VLESS", "protocol": "vless", "address": "5.6.7.8",
            "port": 443, "uuid": "some-uuid", "transport": "ws", "tls": "tls",
            "sni": "test.com",
        }
        resp = client.post("/api/nodes", json=node_data, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My VLESS"
        assert data["protocol"] == "vless"
        assert "id" in data

    def test_create_node_invalid_protocol(self, client, admin_user, auth_headers):
        node_data = {
            "name": "Bad", "protocol": "invalid", "address": "1.2.3.4", "port": 443,
        }
        resp = client.post("/api/nodes", json=node_data, headers=auth_headers)
        assert resp.status_code == 422

    def test_get_node(self, client, admin_user, auth_headers, sample_node):
        resp = client.get(f"/api/nodes/{sample_node.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test VLESS"

    def test_update_node(self, client, admin_user, auth_headers, sample_node):
        resp = client.patch(
            f"/api/nodes/{sample_node.id}",
            json={"name": "Updated VLESS"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated VLESS"

    def test_delete_node(self, client, admin_user, auth_headers, sample_node):
        resp = client.delete(f"/api/nodes/{sample_node.id}", headers=auth_headers)
        assert resp.status_code == 204

        # Verify gone
        resp2 = client.get(f"/api/nodes/{sample_node.id}", headers=auth_headers)
        assert resp2.status_code == 404


class TestChainThroughWireguardRejected:
    """xray can't tunnel THROUGH a WireGuard node — the API must reject a
    chain whose relay (chain_node_id target) is WireGuard."""

    def _mk(self, client, auth_headers, **over):
        body = {"name": "n", "protocol": "vless", "address": "1.2.3.4", "port": 443,
                "uuid": "u", "transport": "tcp", "tls": "none"}
        body.update(over)
        return client.post("/api/nodes", json=body, headers=auth_headers)

    def test_create_chaining_through_wg_rejected(self, client, admin_user, auth_headers):
        wg = self._mk(client, auth_headers, name="wg", protocol="wireguard",
                      wg_private_key="p", wg_public_key="pub", wg_local_address="10.0.0.2/32")
        assert wg.status_code == 201, wg.text
        wg_id = wg.json()["id"]
        resp = self._mk(client, auth_headers, name="exit", chain_node_id=wg_id)
        assert resp.status_code == 400
        assert "wireguard" in resp.json()["detail"].lower()

    def test_update_chaining_through_wg_rejected(self, client, admin_user, auth_headers):
        wg = self._mk(client, auth_headers, name="wg", protocol="wireguard",
                      wg_private_key="p", wg_public_key="pub", wg_local_address="10.0.0.2/32")
        exit_ = self._mk(client, auth_headers, name="exit")
        r = client.patch(f"/api/nodes/{exit_.json()['id']}",
                         json={"chain_node_id": wg.json()["id"]}, headers=auth_headers)
        assert r.status_code == 400

    def test_chaining_through_stream_ok(self, client, admin_user, auth_headers):
        # Chaining through a stream relay (vless) is allowed.
        relay = self._mk(client, auth_headers, name="relay")
        resp = self._mk(client, auth_headers, name="exit", chain_node_id=relay.json()["id"])
        assert resp.status_code == 201, resp.text

    def test_wireguard_exit_through_stream_ok(self, client, admin_user, auth_headers):
        # A WireGuard node MAY chain through a stream relay (WG = exit). OK.
        relay = self._mk(client, auth_headers, name="relay")
        resp = self._mk(client, auth_headers, name="wg-exit", protocol="wireguard",
                        wg_private_key="p", wg_public_key="pub", wg_local_address="10.0.0.2/32",
                        chain_node_id=relay.json()["id"])
        assert resp.status_code == 201, resp.text


class TestDeleteNodeCascade:
    def test_delete_node_cleans_routing_rules(self, client, admin_user, auth_headers, session):
        from app.models import Node, RoutingRule

        # Create a node
        node = Node(
            name="Cascade Node", protocol="vless", address="9.9.9.9",
            port=443, uuid="cascade-uuid", transport="tcp", enabled=True, order=0,
        )
        session.add(node)
        session.commit()
        session.refresh(node)

        # Create a routing rule pointing to that node
        rule = RoutingRule(
            name="Rule for cascade node", rule_type="domain",
            match_value="example.com", action=f"node:{node.id}",
            enabled=True, order=100,
        )
        session.add(rule)
        session.commit()
        session.refresh(rule)
        rule_id = rule.id

        # Delete the node
        resp = client.delete(f"/api/nodes/{node.id}", headers=auth_headers)
        assert resp.status_code == 204

        # Verify the routing rule is also deleted
        resp2 = client.get(f"/api/routing/rules/{rule_id}", headers=auth_headers)
        assert resp2.status_code == 404


class TestNodeImport:
    def test_import_nodes(self, client, admin_user, auth_headers):
        vless_uri = "vless://test-uuid@1.2.3.4:443?type=ws&security=tls&sni=example.com&path=%2F#TestNode"
        resp = client.post(
            "/api/nodes/import",
            json={"uris": vless_uri},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] >= 1


class TestNodeReorder:
    def test_reorder_nodes(self, client, admin_user, auth_headers, session):
        from app.models import Node

        nodes = []
        for i in range(3):
            n = Node(
                name=f"Node {i}", protocol="vless", address=f"10.0.0.{i}",
                port=443, uuid=f"uuid-{i}", transport="tcp", enabled=True, order=i * 10,
            )
            session.add(n)
            session.commit()
            session.refresh(n)
            nodes.append(n)

        # Reverse the order
        reversed_ids = [n.id for n in reversed(nodes)]
        resp = client.post("/api/nodes/reorder", json=reversed_ids, headers=auth_headers)
        assert resp.status_code == 204

        # Verify new order
        resp2 = client.get("/api/nodes", headers=auth_headers)
        assert resp2.status_code == 200
        result = resp2.json()
        result_ids = [n["id"] for n in result]
        assert result_ids == reversed_ids


class TestNodePagination:
    """Tests for `GET /api/nodes/page` (since v1.3.3) — pagination +
    multi-filter endpoint used by the Nodes UI when subscriptions pull
    1000+ nodes."""

    def _seed(self, session, count: int, subscription_id=None, protocol="vless"):
        """Helper: insert `count` nodes with a stable name pattern."""
        from app.models import Node
        for i in range(count):
            session.add(Node(
                name=f"n-{protocol}-{i}", protocol=protocol,
                address=f"10.0.0.{i + 1}", port=443,
                uuid=f"uuid-{protocol}-{i}", transport="tcp",
                enabled=True, order=i * 10,
                subscription_id=subscription_id,
            ))
        session.commit()

    def test_empty(self, client, admin_user, auth_headers):
        resp = client.get("/api/nodes/page", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["limit"] == 50
        assert body["offset"] == 0

    def test_pagination(self, client, admin_user, auth_headers, session):
        self._seed(session, 25)
        # First page
        r1 = client.get("/api/nodes/page?limit=10&offset=0", headers=auth_headers)
        b1 = r1.json()
        assert b1["total"] == 25
        assert len(b1["items"]) == 10
        # Last (partial) page
        r3 = client.get("/api/nodes/page?limit=10&offset=20", headers=auth_headers)
        b3 = r3.json()
        assert b3["total"] == 25
        assert len(b3["items"]) == 5

    def test_limit_zero_returns_all(self, client, admin_user, auth_headers, session):
        # Escape hatch used by bulk-export and tests.
        self._seed(session, 25)
        r = client.get("/api/nodes/page?limit=0", headers=auth_headers)
        b = r.json()
        assert b["total"] == 25
        assert len(b["items"]) == 25

    def test_filter_by_subscription(self, client, admin_user, auth_headers, session):
        from app.models import Subscription
        sub_a = Subscription(name="A", url="http://a")
        sub_b = Subscription(name="B", url="http://b")
        session.add(sub_a); session.add(sub_b); session.commit()
        session.refresh(sub_a); session.refresh(sub_b)
        self._seed(session, 7, subscription_id=sub_a.id)
        self._seed(session, 3, subscription_id=sub_b.id, protocol="trojan")
        r = client.get(f"/api/nodes/page?subscription_id={sub_a.id}", headers=auth_headers)
        b = r.json()
        assert b["total"] == 7
        assert all(n["protocol"] == "vless" for n in b["items"])

    def test_filter_local_only(self, client, admin_user, auth_headers, session):
        """`local=true` should match nodes with subscription_id IS NULL."""
        from app.models import Subscription
        sub = Subscription(name="S", url="http://s")
        session.add(sub); session.commit(); session.refresh(sub)
        self._seed(session, 4, subscription_id=sub.id)  # subscription
        self._seed(session, 3, subscription_id=None, protocol="trojan")  # local
        r = client.get("/api/nodes/page?local=true", headers=auth_headers)
        b = r.json()
        assert b["total"] == 3
        assert all(n.get("subscription_id") in (None, 0) for n in b["items"])

    def test_filter_by_protocol(self, client, admin_user, auth_headers, session):
        self._seed(session, 4, protocol="vless")
        self._seed(session, 2, protocol="trojan")
        r = client.get("/api/nodes/page?protocol=trojan", headers=auth_headers)
        b = r.json()
        assert b["total"] == 2
        assert all(n["protocol"] == "trojan" for n in b["items"])

    def test_search_by_name(self, client, admin_user, auth_headers, session):
        from app.models import Node
        for name in ["alpha-east", "beta-east", "alpha-west"]:
            session.add(Node(
                name=name, protocol="vless", address="1.1.1.1", port=443,
                uuid=name, transport="tcp",
            ))
        session.commit()
        r = client.get("/api/nodes/page?search=alpha", headers=auth_headers)
        b = r.json()
        assert b["total"] == 2
        assert {n["name"] for n in b["items"]} == {"alpha-east", "alpha-west"}

    def test_filters_compose_AND(self, client, admin_user, auth_headers, session):
        self._seed(session, 5, protocol="vless")
        self._seed(session, 5, protocol="trojan")
        # both filters together → only vless nodes whose name contains '-1'
        r = client.get(
            "/api/nodes/page?protocol=vless&search=-1", headers=auth_headers,
        )
        b = r.json()
        assert b["total"] == 1  # only 'n-vless-1' matches both
        assert b["items"][0]["name"] == "n-vless-1"

    def test_direction_desc_default_newest_first(self, client, admin_user, auth_headers, session):
        """Default direction (desc) shows newest IDs first — fixes the
        UX issue where subscription imports buried the freshly-added
        nodes on the last page."""
        from app.models import Node
        for i in range(5):
            session.add(Node(
                name=f"sub-{i}", protocol="vless", address=f"3.3.3.{i}",
                port=443, uuid=f"sub-u-{i}", transport="tcp", order=0,
            ))
        session.commit()
        r = client.get("/api/nodes/page?limit=3", headers=auth_headers)
        ids = [n["id"] for n in r.json()["items"]]
        # Default direction=desc → newest (largest id) first
        assert ids == sorted(ids, reverse=True)

    def test_direction_asc(self, client, admin_user, auth_headers, session):
        from app.models import Node
        for i in range(5):
            session.add(Node(
                name=f"a-{i}", protocol="vless", address=f"4.4.4.{i}",
                port=443, uuid=f"a-u-{i}", transport="tcp", order=0,
            ))
        session.commit()
        r = client.get("/api/nodes/page?direction=asc&limit=3", headers=auth_headers)
        ids = [n["id"] for n in r.json()["items"]]
        assert ids == sorted(ids)  # ascending

    def test_direction_respects_order_column(self, client, admin_user, auth_headers, session):
        """Manual reorder (non-zero `order`) wins over id direction —
        ensures drag-to-reorder isn't undone by the new direction param."""
        from app.models import Node
        # Insert in id order [1,2,3] but set explicit order [20,10,30]:
        # expected sort by (order ASC, id DESC) = id 2 (order=10),
        # id 1 (order=20), id 3 (order=30).
        n1 = Node(name="r1", protocol="vless", address="5.0.0.1", port=443, uuid="r1", transport="tcp", order=20)
        n2 = Node(name="r2", protocol="vless", address="5.0.0.2", port=443, uuid="r2", transport="tcp", order=10)
        n3 = Node(name="r3", protocol="vless", address="5.0.0.3", port=443, uuid="r3", transport="tcp", order=30)
        for n in (n1, n2, n3):
            session.add(n)
        session.commit()
        r = client.get("/api/nodes/page?direction=desc", headers=auth_headers)
        names = [n["name"] for n in r.json()["items"]]
        assert names == ["r2", "r1", "r3"]

    def test_stable_order(self, client, admin_user, auth_headers, session):
        # Same `order` value → tiebreak on `id` so paging doesn't reshuffle.
        from app.models import Node
        for i in range(5):
            session.add(Node(
                name=f"x-{i}", protocol="vless", address=f"2.2.2.{i}",
                port=443, uuid=f"u-{i}", transport="tcp", order=0,
            ))
        session.commit()
        ids_p1 = [n["id"] for n in client.get(
            "/api/nodes/page?limit=2&offset=0", headers=auth_headers,
        ).json()["items"]]
        ids_p2 = [n["id"] for n in client.get(
            "/api/nodes/page?limit=2&offset=2", headers=auth_headers,
        ).json()["items"]]
        # Pages don't overlap
        assert set(ids_p1).isdisjoint(set(ids_p2))


# ── JSON export / import (full-fidelity backup) ──────────────────────────────

class TestNodeExportImportJSON:
    def test_export_basic(self, client, admin_user, auth_headers, sample_node):
        resp = client.get("/api/nodes/export-json", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "pitun-nodes-export"
        assert body["version"] == 1
        assert body["count"] == 1
        assert len(body["nodes"]) == 1
        assert body["nodes"][0]["name"] == "Test VLESS"
        # Filename header present for browser download
        assert "attachment" in resp.headers["content-disposition"]

    def test_import_roundtrip(self, client, admin_user, auth_headers, sample_node):
        # Export current state
        export = client.get("/api/nodes/export-json", headers=auth_headers).json()
        # Wipe via replace=true
        resp = client.post(
            "/api/nodes/import-json?replace=true",
            json=export,
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == 1
        assert body["skipped"] == 0
        assert body["nodes"][0]["name"] == "Test VLESS"
        # Existing node was replaced — only 1 node in DB
        list_resp = client.get("/api/nodes", headers=auth_headers).json()
        assert len(list_resp) == 1

    def test_import_dedup(self, client, admin_user, auth_headers, sample_node):
        # Build an export bundle containing the existing node twice,
        # plus one new
        export = client.get("/api/nodes/export-json", headers=auth_headers).json()
        export["nodes"].append({**export["nodes"][0], "name": "Duplicate"})  # same address+port+protocol+uuid
        export["nodes"].append({
            "name": "Brand new", "protocol": "vless", "address": "9.9.9.9",
            "port": 443, "uuid": "fresh-uuid",
        })
        resp = client.post("/api/nodes/import-json", json=export, headers=auth_headers)
        body = resp.json()
        # Existing node + duplicate dedup'd, new one added
        assert body["imported"] == 1
        assert body["skipped"] == 2

    def test_import_rejects_wrong_kind(self, client, admin_user, auth_headers):
        bad = {"kind": "something-else", "version": 1, "nodes": []}
        resp = client.post("/api/nodes/import-json", json=bad, headers=auth_headers)
        assert resp.status_code == 400

    def test_import_rejects_unknown_version(self, client, admin_user, auth_headers):
        bad = {"kind": "pitun-nodes-export", "version": 999, "nodes": []}
        resp = client.post("/api/nodes/import-json", json=bad, headers=auth_headers)
        assert resp.status_code == 400

    def test_import_per_row_errors(self, client, admin_user, auth_headers):
        bundle = {
            "kind": "pitun-nodes-export", "version": 1,
            "nodes": [
                {"name": "valid", "protocol": "vless", "address": "1.1.1.1",
                 "port": 443, "uuid": "x"},
                {"name": "bad-protocol", "protocol": "INVALID", "address": "2.2.2.2",
                 "port": 443},
            ],
        }
        resp = client.post("/api/nodes/import-json", json=bundle, headers=auth_headers)
        body = resp.json()
        assert body["imported"] == 1
        assert len(body["errors"]) == 1
        assert "bad-protocol" in body["errors"][0]


class TestApplyCountryFlags:
    """Nodes that already existed when the GeoLite2 database was added keep
    the name they were created with — enrichment happens as a node is
    written. This is the one-off that brings them in line."""

    class _R:
        def get(self, ip):
            return {"country": {"iso_code": "NL"}} if ip == "5.6.7.8" else None

        def close(self):
            pass

    @pytest.fixture
    def no_geoip_yet(self, monkeypatch):
        """Start with no database installed — the state a node created before
        the operator added GeoLite2 was written in. Hand back a switch that
        turns it on, so the test can reproduce "added the mmdb afterwards"."""
        from app.core import geoip_lookup as g
        g.reset()
        monkeypatch.setattr(g, "_reader", None)
        monkeypatch.setattr(g, "_reader_loaded", True)

        def enable():
            monkeypatch.setattr(g, "_reader", self._R())
            monkeypatch.setattr(g, "_reader_loaded", True)

        yield enable
        g.reset()

    @pytest.fixture
    def geoip(self, monkeypatch):
        from app.core import geoip_lookup as g
        g.reset()
        monkeypatch.setattr(g, "_reader", self._R())
        monkeypatch.setattr(g, "_reader_loaded", True)
        yield
        g.reset()

    def _make(self, client, auth_headers, name, address):
        r = client.post("/api/nodes", headers=auth_headers, json={
            "name": name, "protocol": "vless", "address": address,
            "port": 443, "uuid": "u",
        })
        assert r.status_code == 201, r.text
        return r.json()["id"]

    def test_a_node_written_before_the_database_existed_gets_its_flag(
        self, client, admin_user, auth_headers, no_geoip_yet,
    ):
        nid = self._make(client, auth_headers, "deployed-node", "5.6.7.8")
        got = client.get(f"/api/nodes/{nid}", headers=auth_headers).json()
        assert got["name"] == "deployed-node", "no database yet — no flag"

        no_geoip_yet()          # operator drops GeoLite2-Country.mmdb in
        r = client.post("/api/nodes/apply-country-flags", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["renamed"] == 1
        got = client.get(f"/api/nodes/{nid}", headers=auth_headers).json()
        assert got["name"] == "🇳🇱 deployed-node"

    def test_a_node_created_with_the_database_present_is_already_flagged(
        self, client, admin_user, auth_headers, geoip,
    ):
        """The write-time hook is the actual fix; the backfill only exists
        for what predates it."""
        nid = self._make(client, auth_headers, "fresh-node", "5.6.7.8")
        got = client.get(f"/api/nodes/{nid}", headers=auth_headers).json()
        assert got["name"] == "🇳🇱 fresh-node"

        r = client.post("/api/nodes/apply-country-flags", headers=auth_headers)
        assert r.json()["renamed"] == 0, "nothing left to do"

    def test_a_node_whose_country_is_unknown_keeps_its_name(
        self, client, admin_user, auth_headers, geoip,
    ):
        nid = self._make(client, auth_headers, "mystery", "203.0.113.9")
        client.post("/api/nodes/apply-country-flags", headers=auth_headers)
        got = client.get(f"/api/nodes/{nid}", headers=auth_headers).json()
        assert got["name"] == "mystery"

    def test_without_a_database_it_says_so(self, client, admin_user, auth_headers):
        from app.core import geoip_lookup as g
        g.reset()
        r = client.post("/api/nodes/apply-country-flags", headers=auth_headers)
        assert r.status_code == 400
        assert "GeoLite2" in r.json()["detail"]
