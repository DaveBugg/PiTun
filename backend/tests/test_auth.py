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


class TestLockout:
    """Per-account brute-force lockout on /auth/login."""

    def test_locks_after_max_attempts(self, client, admin_user):
        # 5 misses trip the lock; the 6th try — even with the RIGHT
        # password — is refused with 429 + Retry-After.
        for _ in range(5):
            r = client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
            assert r.status_code == 401
        r = client.post("/api/auth/login", json={"username": "admin", "password": "password"})
        assert r.status_code == 429
        assert "retry-after" in {k.lower() for k in r.headers}

    def test_success_resets_counter(self, client, admin_user):
        # 4 misses (under threshold), then a good login clears the count…
        for _ in range(4):
            assert client.post(
                "/api/auth/login", json={"username": "admin", "password": "wrong"}
            ).status_code == 401
        assert client.post(
            "/api/auth/login", json={"username": "admin", "password": "password"}
        ).status_code == 200
        # …so it takes a fresh 5 to lock — 4 more misses stay 401, not 429.
        for _ in range(4):
            assert client.post(
                "/api/auth/login", json={"username": "admin", "password": "wrong"}
            ).status_code == 401

    def test_locked_rejects_even_correct_password(self, client, admin_user):
        for _ in range(5):
            client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        r = client.post("/api/auth/login", json={"username": "admin", "password": "password"})
        assert r.status_code == 429

    def test_lock_expires_allows_login(self, client, admin_user, session):
        from datetime import datetime, timezone, timedelta
        from app.models import User
        for _ in range(5):
            client.post("/api/auth/login", json={"username": "admin", "password": "wrong"})
        # Fast-forward past the lock window.
        u = session.get(User, admin_user.id)
        u.lock_until = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.add(u)
        session.commit()
        r = client.post("/api/auth/login", json={"username": "admin", "password": "password"})
        assert r.status_code == 200

    def test_unknown_user_never_locks(self, client):
        # Hammering a nonexistent username stays 401 — no lockout state is
        # created for it (and the response never differs from a real miss).
        for _ in range(7):
            r = client.post("/api/auth/login", json={"username": "ghost", "password": "x"})
            assert r.status_code == 401


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
