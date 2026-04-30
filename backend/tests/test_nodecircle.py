"""Tests for NodeCircle CRUD and validation."""
import json
import pytest


class TestNodeCircleList:
    def test_list_empty(self, client, admin_user, auth_headers):
        resp = client.get("/api/nodecircle", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_data(self, client, admin_user, auth_headers, sample_circle):
        resp = client.get("/api/nodecircle", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Test Circle"


class TestNodeCircleCreate:
    def test_create(self, client, admin_user, auth_headers, sample_node):
        resp = client.post(
            "/api/nodecircle",
            json={
                "name": "New Circle",
                "node_ids": [sample_node.id],
                "mode": "sequential",
                "interval_min": 10,
                "interval_max": 30,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "New Circle"
        assert data["node_ids"] == [sample_node.id]
        assert data["mode"] == "sequential"

    def test_create_random_mode(self, client, admin_user, auth_headers, sample_node):
        resp = client.post(
            "/api/nodecircle",
            json={"name": "Random Circle", "node_ids": [sample_node.id], "mode": "random"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["mode"] == "random"

    def test_create_invalid_mode(self, client, admin_user, auth_headers, sample_node):
        resp = client.post(
            "/api/nodecircle",
            json={"name": "Bad", "node_ids": [sample_node.id], "mode": "invalid"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_create_interval_min_too_low(self, client, admin_user, auth_headers, sample_node):
        resp = client.post(
            "/api/nodecircle",
            json={
                "name": "Bad Interval",
                "node_ids": [sample_node.id],
                "interval_min": 0,
                "interval_max": 10,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_create_interval_max_less_than_min(self, client, admin_user, auth_headers, sample_node):
        resp = client.post(
            "/api/nodecircle",
            json={
                "name": "Reversed",
                "node_ids": [sample_node.id],
                "interval_min": 20,
                "interval_max": 5,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestNodeCircleGet:
    def test_get(self, client, admin_user, auth_headers, sample_circle):
        resp = client.get(f"/api/nodecircle/{sample_circle.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Circle"

    def test_get_not_found(self, client, admin_user, auth_headers):
        resp = client.get("/api/nodecircle/9999", headers=auth_headers)
        assert resp.status_code == 404


class TestNodeCircleUpdate:
    def test_update_name(self, client, admin_user, auth_headers, sample_circle):
        resp = client.patch(
            f"/api/nodecircle/{sample_circle.id}",
            json={"name": "Renamed"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_update_mode(self, client, admin_user, auth_headers, sample_circle):
        resp = client.patch(
            f"/api/nodecircle/{sample_circle.id}",
            json={"mode": "random"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["mode"] == "random"

    def test_update_invalid_mode(self, client, admin_user, auth_headers, sample_circle):
        resp = client.patch(
            f"/api/nodecircle/{sample_circle.id}",
            json={"mode": "broken"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_update_not_found(self, client, admin_user, auth_headers):
        resp = client.patch("/api/nodecircle/9999", json={"name": "x"}, headers=auth_headers)
        assert resp.status_code == 404


class TestNodeCircleDelete:
    def test_delete(self, client, admin_user, auth_headers, sample_circle):
        resp = client.delete(f"/api/nodecircle/{sample_circle.id}", headers=auth_headers)
        assert resp.status_code == 204

        resp2 = client.get(f"/api/nodecircle/{sample_circle.id}", headers=auth_headers)
        assert resp2.status_code == 404

    def test_delete_not_found(self, client, admin_user, auth_headers):
        resp = client.delete("/api/nodecircle/9999", headers=auth_headers)
        assert resp.status_code == 404
