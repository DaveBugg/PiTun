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
