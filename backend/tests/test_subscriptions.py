"""Tests for subscription CRUD, cascade delete."""
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
