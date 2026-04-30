"""Tests for authentication endpoints."""
import pytest


class TestLogin:
    def test_login_success(self, client, admin_user):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "password"})
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_wrong_password(self, client, admin_user):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        assert resp.status_code == 401

    def test_login_nonexistent_user(self, client):
        resp = client.post("/api/auth/login", json={"username": "nobody", "password": "password"})
        assert resp.status_code == 401


class TestProtectedEndpoints:
    def test_protected_endpoint_no_token(self, client, admin_user, default_settings):
        resp = client.get("/api/system/status")
        assert resp.status_code == 401

    def test_protected_endpoint_with_token(self, client, admin_user, auth_headers):
        # Use /api/nodes which is simpler and doesn't call xray/nftables
        resp = client.get("/api/nodes", headers=auth_headers)
        assert resp.status_code == 200

    def test_protected_endpoint_invalid_token(self, client, admin_user, default_settings):
        resp = client.get("/api/system/status", headers={"Authorization": "Bearer garbage.token.here"})
        assert resp.status_code == 401


class TestChangePassword:
    def test_change_password(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/auth/change-password",
            json={"current_password": "password", "new_password": "newpass123"},
            headers=auth_headers,
        )
        assert resp.status_code == 204

        # Login with new password should succeed
        resp2 = client.post("/api/auth/login", json={"username": "admin", "password": "newpass123"})
        assert resp2.status_code == 200

    def test_change_password_wrong_current(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/auth/change-password",
            json={"current_password": "wrong", "new_password": "newpass123"},
            headers=auth_headers,
        )
        assert resp.status_code == 400


class TestMe:
    def test_me(self, client, admin_user, auth_headers):
        resp = client.get("/api/auth/me", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["username"] == "admin"
        assert "id" in data
