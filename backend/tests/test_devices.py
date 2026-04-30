"""Tests for device management API: CRUD, bulk policy, filters."""
import pytest


class TestDeviceList:
    def test_list_empty(self, client, admin_user, auth_headers):
        resp = client.get("/api/devices", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_all(self, client, admin_user, auth_headers, multiple_devices):
        resp = client.get("/api/devices", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 3

    def test_list_online_only(self, client, admin_user, auth_headers, multiple_devices):
        resp = client.get("/api/devices?online_only=true", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert all(d["is_online"] for d in data)

    def test_list_filter_by_policy(self, client, admin_user, auth_headers, multiple_devices):
        resp = client.get("/api/devices?policy=include", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["routing_policy"] == "include"


class TestDeviceGet:
    def test_get_device(self, client, admin_user, auth_headers, sample_device):
        resp = client.get(f"/api/devices/{sample_device.id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["mac"] == "aa:bb:cc:dd:ee:01"
        assert data["name"] == "Test Phone"

    def test_get_device_not_found(self, client, admin_user, auth_headers):
        resp = client.get("/api/devices/9999", headers=auth_headers)
        assert resp.status_code == 404


class TestDeviceUpdate:
    def test_update_name(self, client, admin_user, auth_headers, sample_device):
        resp = client.patch(
            f"/api/devices/{sample_device.id}",
            json={"name": "My Laptop"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "My Laptop"

    def test_update_routing_policy(self, client, admin_user, auth_headers, sample_device):
        resp = client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_policy": "include"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["routing_policy"] == "include"

    def test_update_invalid_policy(self, client, admin_user, auth_headers, sample_device):
        resp = client.patch(
            f"/api/devices/{sample_device.id}",
            json={"routing_policy": "invalid"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_update_not_found(self, client, admin_user, auth_headers):
        resp = client.patch("/api/devices/9999", json={"name": "x"}, headers=auth_headers)
        assert resp.status_code == 404


class TestDeviceDelete:
    def test_delete_device(self, client, admin_user, auth_headers, sample_device):
        resp = client.delete(f"/api/devices/{sample_device.id}", headers=auth_headers)
        assert resp.status_code == 204

        resp2 = client.get(f"/api/devices/{sample_device.id}", headers=auth_headers)
        assert resp2.status_code == 404

    def test_delete_not_found(self, client, admin_user, auth_headers):
        resp = client.delete("/api/devices/9999", headers=auth_headers)
        assert resp.status_code == 404


class TestBulkPolicy:
    def test_bulk_update_policy(self, client, admin_user, auth_headers, multiple_devices):
        ids = [d.id for d in multiple_devices]
        resp = client.post(
            "/api/devices/bulk-policy",
            json={"device_ids": ids, "routing_policy": "exclude"},
            headers=auth_headers,
        )
        assert resp.status_code == 204

        resp2 = client.get("/api/devices?policy=exclude", headers=auth_headers)
        assert len(resp2.json()) == 3

    def test_bulk_update_invalid_policy(self, client, admin_user, auth_headers, sample_device):
        resp = client.post(
            "/api/devices/bulk-policy",
            json={"device_ids": [sample_device.id], "routing_policy": "bad"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestResetPolicies:
    def test_reset_all_policies(self, client, admin_user, auth_headers, multiple_devices):
        resp = client.post("/api/devices/reset-all-policies", headers=auth_headers)
        assert resp.status_code == 204

        resp2 = client.get("/api/devices", headers=auth_headers)
        for d in resp2.json():
            assert d["routing_policy"] == "default"
