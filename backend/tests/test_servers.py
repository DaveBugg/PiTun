"""Tests for the Servers API.

Phase 1 scope: CRUD + write-only secret semantics + naive-install-script
generator. The actual SSH probe (`/test`, `/test-all`) is exercised
indirectly — we mock asyncssh so the test doesn't try to connect to a
live host. The real network round-trip is covered by manual e2e on the
Debian VM (see `notes.md` deploy log).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(name="sample_server")
def sample_server_fixture(session):
    from app.models import Server
    s = Server(
        name="My VPS",
        description="Hetzner FRA-1, naive endpoint",
        host="vps1.example.com",
        port=22,
        user="root",
        auth_type="password",
        password="s3cret",
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


# ── CRUD ─────────────────────────────────────────────────────────────────────

class TestServerCRUD:
    def test_list_empty(self, client, admin_user, auth_headers):
        resp = client.get("/api/servers", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_with_password(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/servers",
            json={
                "name": "vps-1",
                "host": "1.2.3.4",
                "user": "root",
                "auth_type": "password",
                "password": "topsecret",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        # Password must NOT round-trip back through the API.
        assert "password" not in data or data.get("password") in (None, "", False)
        assert data["has_password"] is True
        assert data["has_private_key"] is False
        assert data["status"] == "unknown"

    def test_create_with_key(self, client, admin_user, auth_headers):
        # Bogus key body — we just check storage, not validity (validity
        # is only checked when we actually try to connect).
        fake_key = "-----BEGIN OPENSSH PRIVATE KEY-----\nfake\n-----END OPENSSH PRIVATE KEY-----\n"
        resp = client.post(
            "/api/servers",
            json={
                "name": "vps-key",
                "host": "5.6.7.8",
                "auth_type": "key",
                "private_key": fake_key,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["has_password"] is False
        assert data["has_private_key"] is True

    def test_create_invalid_auth_type(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/servers",
            json={"name": "x", "host": "1.1.1.1", "auth_type": "magic"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_create_invalid_port(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/servers",
            json={"name": "x", "host": "1.1.1.1", "port": 0},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_get_one(self, client, admin_user, auth_headers, sample_server):
        resp = client.get(f"/api/servers/{sample_server.id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "My VPS"
        assert data["host"] == "vps1.example.com"
        assert data["has_password"] is True

    def test_get_404(self, client, admin_user, auth_headers):
        resp = client.get("/api/servers/9999", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete(self, client, admin_user, auth_headers, sample_server):
        resp = client.delete(f"/api/servers/{sample_server.id}", headers=auth_headers)
        assert resp.status_code == 204
        resp2 = client.get(f"/api/servers/{sample_server.id}", headers=auth_headers)
        assert resp2.status_code == 404


# ── PATCH semantics ──────────────────────────────────────────────────────────

class TestServerUpdate:
    def test_update_simple_field(self, client, admin_user, auth_headers, sample_server):
        resp = client.patch(
            f"/api/servers/{sample_server.id}",
            json={"description": "renamed"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["description"] == "renamed"

    def test_password_empty_string_keeps_existing(
        self, client, admin_user, auth_headers, sample_server, session
    ):
        """Empty-string secret means "leave unchanged" — protects against
        forms that submit blank fields when the user just wanted to edit
        the name."""
        resp = client.patch(
            f"/api/servers/{sample_server.id}",
            json={"name": "renamed", "password": ""},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        # Re-read from DB and check the secret is still there.
        from app.models import Server
        session.expire_all()
        fresh = session.get(Server, sample_server.id)
        assert fresh.password == "s3cret"

    def test_password_explicit_null_clears(
        self, client, admin_user, auth_headers, sample_server, session
    ):
        resp = client.patch(
            f"/api/servers/{sample_server.id}",
            json={"password": None},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        from app.models import Server
        session.expire_all()
        fresh = session.get(Server, sample_server.id)
        assert fresh.password is None
        # has_password should now be False in the API view
        view = client.get(
            f"/api/servers/{sample_server.id}", headers=auth_headers
        ).json()
        assert view["has_password"] is False

    def test_password_replace(
        self, client, admin_user, auth_headers, sample_server, session
    ):
        resp = client.patch(
            f"/api/servers/{sample_server.id}",
            json={"password": "newer-secret"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        from app.models import Server
        session.expire_all()
        fresh = session.get(Server, sample_server.id)
        assert fresh.password == "newer-secret"


# ── /test endpoint ───────────────────────────────────────────────────────────

class TestServerProbe:
    def test_test_endpoint_success(
        self, client, admin_user, auth_headers, sample_server
    ):
        from app.core.ssh import SSHTestResult

        ok_result = SSHTestResult(
            ok=True, latency_ms=42,
            remote_info="Linux fake 6.1.0\n---\nDebian 12",
        )
        with patch(
            "app.api.servers.test_ssh_connection",
            new=AsyncMock(return_value=ok_result),
        ):
            resp = client.post(
                f"/api/servers/{sample_server.id}/test", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["latency_ms"] == 42
        assert "Debian 12" in data["remote_info"]

        # And the row's status was persisted.
        view = client.get(
            f"/api/servers/{sample_server.id}", headers=auth_headers
        ).json()
        assert view["status"] == "online"
        assert view["latency_ms"] == 42

    def test_test_endpoint_failure(
        self, client, admin_user, auth_headers, sample_server
    ):
        from app.core.ssh import SSHTestResult

        fail_result = SSHTestResult(ok=False, error="connection refused")
        with patch(
            "app.api.servers.test_ssh_connection",
            new=AsyncMock(return_value=fail_result),
        ):
            resp = client.post(
                f"/api/servers/{sample_server.id}/test", headers=auth_headers
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is False
        assert data["error"] == "connection refused"

        view = client.get(
            f"/api/servers/{sample_server.id}", headers=auth_headers
        ).json()
        assert view["status"] == "offline"
        assert view["last_check_error"] == "connection refused"


# ── Naive install script generator ───────────────────────────────────────────

class TestNaiveInstallScript:
    def test_generated_script_contains_creds(
        self, client, admin_user, auth_headers, sample_server
    ):
        resp = client.get(
            f"/api/servers/{sample_server.id}/naive-install-script",
            params={
                # Pick values that include a space to force shlex to quote
                # them — that way we exercise the quoting branch and don't
                # depend on shlex's "safe-string" passthrough behaviour.
                "domain": "proxy.example.com",
                "email": "me@example.com",
                "naive_user": "my user",
                "naive_pass": "pass with spaces",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.text
        # Each env var is on its own line, value present in some quoted form.
        assert "export DOMAIN=" in body and "proxy.example.com" in body
        assert "export EMAIL=" in body and "me@example.com" in body
        assert "export NAIVE_USER=" in body and "my user" in body
        assert "export NAIVE_PASS=" in body and "pass with spaces" in body
        # Spaces force shlex to single-quote the whole value.
        assert "'my user'" in body
        assert "'pass with spaces'" in body
        # The bootstrap fetches the canonical setup-naive-server.sh.
        assert "setup-naive-server.sh" in body
        # Filename header for the browser download.
        assert "attachment" in resp.headers["content-disposition"]

    def test_generated_script_autogenerates_credentials(
        self, client, admin_user, auth_headers, sample_server
    ):
        resp = client.get(
            f"/api/servers/{sample_server.id}/naive-install-script",
            params={"domain": "p.example.com", "email": "me@example.com"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.text
        # `pitun` has no shell metacharacters, shlex emits it unquoted.
        assert "export NAIVE_USER=pitun" in body
        # Auto-generated password is `secrets.token_urlsafe(24)` — URL-safe
        # base64 (A-Z, a-z, 0-9, _, -). 24 random bytes → 32 chars output.
        # Match the assignment line and check value is non-empty.
        import re
        m = re.search(r"export NAIVE_PASS=(\S+)", body)
        assert m is not None, "NAIVE_PASS line missing"
        value = m.group(1).strip("'\"")
        assert len(value) >= 16, f"auto-generated pass too short: {value!r}"

    def test_404_for_unknown_server(self, client, admin_user, auth_headers):
        resp = client.get(
            "/api/servers/9999/naive-install-script",
            params={"domain": "x.example.com", "email": "x@example.com"},
            headers=auth_headers,
        )
        assert resp.status_code == 404


# ── WireGuard manual install script ──────────────────────────────────────────
#
# Sister of the naive script generator. Wraps `setup-wireguard-server.sh`
# with env-var exports for the install sub-command (bootstraps server +
# adds first peer in one go).


class TestWireguardInstallScript:
    def test_per_server_basic(self, client, admin_user, auth_headers, sample_server):
        resp = client.get(
            f"/api/servers/{sample_server.id}/wireguard-install-script",
            params={
                "client_name": "phone-1",
                "server_port": 51821,
                "dns_1": "8.8.8.8",
                "allowed_ips": "10.0.0.0/8",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.text
        # Sub-command dispatch + env vars present.
        assert "PITUN_WG_SUBCOMMAND" in body and '"install"' in body
        assert "export CLIENT_NAME=phone-1" in body
        assert "export SERVER_PORT=51821" in body
        assert "export DNS_1=8.8.8.8" in body
        assert "export ALLOWED_IPS=10.0.0.0/8" in body
        # The bootstrap fetches the canonical setup-wireguard-server.sh.
        assert "setup-wireguard-server.sh" in body
        # Per-server filename suffix.
        assert f'filename="wireguard-install-{sample_server.id}.sh"' in \
            resp.headers["content-disposition"]

    def test_defaults_when_fields_absent(
        self, client, admin_user, auth_headers, sample_server
    ):
        resp = client.get(
            f"/api/servers/{sample_server.id}/wireguard-install-script",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.text
        # client_name still gets a default since `install` always needs
        # one peer to create.
        assert 'export CLIENT_NAME="client1"' in body
        # Optional knobs (port/dns/allowed_ips) NOT injected — the
        # underlying script's defaults take over. We assert their
        # absence so the user can still override them via env on the
        # command line if they want.
        assert "export SERVER_PORT" not in body
        assert "export DNS_1" not in body
        assert "export ALLOWED_IPS" not in body

    def test_404_for_unknown_server(self, client, admin_user, auth_headers):
        resp = client.get(
            "/api/servers/9999/wireguard-install-script",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_manual_endpoint(self, client, admin_user, auth_headers):
        # Server-agnostic variant under /scripts/wireguard-install
        resp = client.get(
            "/api/scripts/wireguard-install",
            params={"client_name": "phone-2", "server_port": 51820},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.text
        assert "manual / unregistered server" in body
        assert "export CLIENT_NAME=phone-2" in body
        assert "export SERVER_PORT=51820" in body
        assert 'filename="wireguard-install.sh"' in \
            resp.headers["content-disposition"]


# ── Manual (server-agnostic) install scripts ─────────────────────────────────
#
# Companion endpoint at /api/scripts/naive-install — same generator, no
# server_id required. Used by the "Manual scripts" cards on the Servers
# page so users can grab the bootstrap before registering a VPS.

class TestManualInstallScript:
    def test_manual_script_basic(self, client, admin_user, auth_headers):
        resp = client.get(
            "/api/scripts/naive-install",
            params={"domain": "p.example.com", "email": "me@example.com"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.text
        # Header reflects manual mode — no server name reference.
        assert "manual / unregistered server" in body
        # Same env-var contract as the per-server endpoint.
        assert "export DOMAIN=p.example.com" in body
        assert "export EMAIL=me@example.com" in body
        assert "export NAIVE_USER=pitun" in body
        assert "export NAIVE_PASS=" in body
        # Filename is generic (no server id suffix).
        assert 'filename="naive-install.sh"' in resp.headers["content-disposition"]

    def test_manual_script_uses_explicit_credentials(
        self, client, admin_user, auth_headers
    ):
        resp = client.get(
            "/api/scripts/naive-install",
            params={
                "domain": "p.example.com",
                "email": "me@example.com",
                "naive_user": "alice",
                "naive_pass": "wonderland",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        body = resp.text
        assert "export NAIVE_USER=alice" in body
        assert "export NAIVE_PASS=wonderland" in body

    def test_manual_script_requires_auth(self, client):
        resp = client.get(
            "/api/scripts/naive-install",
            params={"domain": "p.example.com", "email": "me@example.com"},
        )
        assert resp.status_code == 401


# ── Server deployments (persistent install plans) ────────────────────────────
#
# Frontend saves a deployment row before downloading the per-server
# install script, so credentials are remembered for re-edit and for
# one-click "Create node from this deployment".

class TestServerDeployments:
    NAIVE_CONFIG = {
        "domain": "proxy.example.com",
        "email": "me@example.com",
        "naive_user": "alice",
        "naive_pass": "hunter2hunter2",
    }

    def _put_deployment(self, client, headers, server_id, protocol="naive", config=None):
        return client.put(
            f"/api/servers/{server_id}/deployments/{protocol}",
            json={"protocol": protocol, "config": config or self.NAIVE_CONFIG},
            headers=headers,
        )

    def test_list_empty(self, client, admin_user, auth_headers, sample_server):
        resp = client.get(
            f"/api/servers/{sample_server.id}/deployments", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_upsert_creates_new(
        self, client, admin_user, auth_headers, sample_server
    ):
        resp = self._put_deployment(client, auth_headers, sample_server.id)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["protocol"] == "naive"
        assert body["config"]["domain"] == "proxy.example.com"
        assert body["config"]["naive_user"] == "alice"
        assert body["config"]["naive_pass"] == "hunter2hunter2"
        assert body["status"] == "configured"
        assert body["last_node_id"] is None

    def test_upsert_updates_existing(
        self, client, admin_user, auth_headers, sample_server
    ):
        # Insert
        first = self._put_deployment(client, auth_headers, sample_server.id).json()
        # Update with different password
        new_config = {**self.NAIVE_CONFIG, "naive_pass": "rotated-pass"}
        second = self._put_deployment(
            client, auth_headers, sample_server.id, config=new_config
        ).json()
        # Same row id, password replaced
        assert first["id"] == second["id"]
        assert second["config"]["naive_pass"] == "rotated-pass"

    def test_path_protocol_must_match_body(
        self, client, admin_user, auth_headers, sample_server
    ):
        resp = client.put(
            f"/api/servers/{sample_server.id}/deployments/naive",
            json={"protocol": "wireguard", "config": {}},
            headers=auth_headers,
        )
        # Either path/body mismatch (400) or unknown protocol (422). Both
        # are acceptable rejections; we just want NOT a 200.
        assert resp.status_code in (400, 422)

    def test_unknown_protocol_rejected(
        self, client, admin_user, auth_headers, sample_server
    ):
        # `wireguard` was added to the protocol whitelist in beta.4 — use
        # a placeholder name for the negative test instead.
        resp = client.put(
            f"/api/servers/{sample_server.id}/deployments/shadowsocks",
            json={"protocol": "shadowsocks", "config": {}},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_list_returns_existing(
        self, client, admin_user, auth_headers, sample_server
    ):
        self._put_deployment(client, auth_headers, sample_server.id)
        resp = client.get(
            f"/api/servers/{sample_server.id}/deployments", headers=auth_headers
        )
        assert resp.status_code == 200
        rows = resp.json()
        assert len(rows) == 1
        assert rows[0]["protocol"] == "naive"

    def test_delete(self, client, admin_user, auth_headers, sample_server):
        self._put_deployment(client, auth_headers, sample_server.id)
        resp = client.delete(
            f"/api/servers/{sample_server.id}/deployments/naive", headers=auth_headers
        )
        assert resp.status_code == 204
        # Subsequent list empty
        rows = client.get(
            f"/api/servers/{sample_server.id}/deployments", headers=auth_headers
        ).json()
        assert rows == []

    def test_delete_404_when_missing(
        self, client, admin_user, auth_headers, sample_server
    ):
        resp = client.delete(
            f"/api/servers/{sample_server.id}/deployments/naive", headers=auth_headers
        )
        assert resp.status_code == 404

    def test_404_on_unknown_server(self, client, admin_user, auth_headers):
        resp = self._put_deployment(client, auth_headers, server_id=9999)
        assert resp.status_code == 404


class TestCreateNodeFromDeployment:
    NAIVE_CONFIG = {
        "domain": "proxy.example.com",
        "email": "me@example.com",
        "naive_user": "alice",
        "naive_pass": "hunter2hunter2",
    }

    def test_create_node_happy_path(
        self, client, admin_user, auth_headers, sample_server
    ):
        # Save deployment first
        client.put(
            f"/api/servers/{sample_server.id}/deployments/naive",
            json={"protocol": "naive", "config": self.NAIVE_CONFIG},
            headers=auth_headers,
        )
        # Create node from it. The /api/nodes pipeline tries to spin up a
        # naive sidecar container — patch the helpers used by the
        # create-node endpoint to no-ops so the test doesn't need docker.
        # The naive sidecar helpers are imported inline inside the API
        # function (lazy import) — patch them at their source module so
        # the import-time lookup hits the mocks.
        from unittest.mock import AsyncMock, patch
        with (
            patch("app.api.nodes._ensure_naive_port", new=AsyncMock(return_value=None)),
            patch("app.api.nodes._sync_naive_sidecar", new=AsyncMock(return_value=None)),
            patch("app.api.nodes._refresh_naive_tproxy_bypass", new=AsyncMock(return_value=None)),
        ):
            resp = client.post(
                f"/api/servers/{sample_server.id}/deployments/naive/create-node",
                headers=auth_headers,
            )
        # 200 since the endpoint is now idempotent (was 201 prior to
        # the v1.3.0-beta.5 fix); both first-create and existing-return
        # paths use 200 — the response_model + body identify the row.
        assert resp.status_code == 200, resp.text
        node = resp.json()
        assert node["protocol"] == "naive"
        assert node["address"] == "proxy.example.com"  # took domain, not server.host
        assert node["port"] == 443
        assert node["uuid"] == "alice"
        assert node["password"] == "hunter2hunter2"
        assert node["sni"] == "proxy.example.com"
        assert node["tls"] == "tls"
        assert node["server_id"] == sample_server.id

        # Deployment now reflects deployed status + linked node.
        deps = client.get(
            f"/api/servers/{sample_server.id}/deployments", headers=auth_headers
        ).json()
        assert deps[0]["status"] == "deployed"
        assert deps[0]["last_node_id"] == node["id"]

        # Idempotency — a second POST returns the SAME node row, not a duplicate.
        with (
            patch("app.api.nodes._ensure_naive_port", new=AsyncMock(return_value=None)),
            patch("app.api.nodes._sync_naive_sidecar", new=AsyncMock(return_value=None)),
            patch("app.api.nodes._refresh_naive_tproxy_bypass", new=AsyncMock(return_value=None)),
        ):
            resp2 = client.post(
                f"/api/servers/{sample_server.id}/deployments/naive/create-node",
                headers=auth_headers,
            )
        assert resp2.status_code == 200
        assert resp2.json()["id"] == node["id"]

    def test_create_node_404_no_deployment(
        self, client, admin_user, auth_headers, sample_server
    ):
        resp = client.post(
            f"/api/servers/{sample_server.id}/deployments/naive/create-node",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_create_node_falls_back_to_server_host_if_no_domain(
        self, client, admin_user, auth_headers, sample_server
    ):
        # No domain in config — node.address should fall back to server.host
        client.put(
            f"/api/servers/{sample_server.id}/deployments/naive",
            json={"protocol": "naive", "config": {"naive_user": "u", "naive_pass": "p"}},
            headers=auth_headers,
        )
        from unittest.mock import AsyncMock, patch
        with (
            patch("app.api.nodes._ensure_naive_port", new=AsyncMock(return_value=None)),
            patch("app.api.nodes._sync_naive_sidecar", new=AsyncMock(return_value=None)),
            patch("app.api.nodes._refresh_naive_tproxy_bypass", new=AsyncMock(return_value=None)),
        ):
            resp = client.post(
                f"/api/servers/{sample_server.id}/deployments/naive/create-node",
                headers=auth_headers,
            )
        assert resp.status_code == 200
        assert resp.json()["address"] == sample_server.host


# ── Auth boundary ────────────────────────────────────────────────────────────

class TestServersRequireAuth:
    def test_list_without_token(self, client):
        resp = client.get("/api/servers")
        assert resp.status_code == 401

    def test_create_without_token(self, client):
        resp = client.post(
            "/api/servers",
            json={"name": "x", "host": "1.1.1.1"},
        )
        assert resp.status_code == 401


# ── JSON export / import ────────────────────────────────────────────────────

class TestServerExportImportJSON:
    def test_export_strips_secrets_by_default(self, client, admin_user, auth_headers, sample_server):
        resp = client.get("/api/servers/export-json", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "pitun-servers-export"
        assert body["include_secrets"] is False
        assert body["count"] == 1
        s = body["servers"][0]
        # Secret fields must NOT be present (or must be None) when not opted in
        assert s.get("password") in (None, "")
        assert s.get("private_key") in (None, "")
        # Non-secret fields ARE present
        assert s["name"] == "My VPS"
        assert s["host"] == "vps1.example.com"

    def test_export_with_secrets_opt_in(self, client, admin_user, auth_headers, sample_server):
        resp = client.get(
            "/api/servers/export-json?include_secrets=true", headers=auth_headers
        )
        body = resp.json()
        assert body["include_secrets"] is True
        s = body["servers"][0]
        assert s["password"] == "s3cret"

    def test_import_no_secrets_creates_servers_with_null_creds(
        self, client, admin_user, auth_headers, session
    ):
        # Build bundle with two servers, no secrets
        bundle = {
            "kind": "pitun-servers-export", "version": 1,
            "include_secrets": False,
            "servers": [
                {"name": "EU-1", "host": "eu1.example.com", "port": 22,
                 "user": "root", "auth_type": "password"},
                {"name": "US-1", "host": "us1.example.com", "port": 2222,
                 "user": "admin", "auth_type": "password"},
            ],
        }
        resp = client.post("/api/servers/import-json", json=bundle, headers=auth_headers)
        body = resp.json()
        assert body["imported"] == 2
        assert body["has_secrets"] is False

    def test_import_with_secrets_roundtrip(
        self, client, admin_user, auth_headers, sample_server, session
    ):
        # Export with secrets
        export = client.get(
            "/api/servers/export-json?include_secrets=true", headers=auth_headers
        ).json()
        # Wipe and re-import
        resp = client.post(
            "/api/servers/import-json?replace=true",
            json=export,
            headers=auth_headers,
        )
        body = resp.json()
        assert body["imported"] == 1
        # Confirm via direct DB read that password was preserved
        from app.models import Server
        from sqlmodel import select
        session.expire_all()
        rows = session.exec(select(Server)).all()
        assert len(rows) == 1
        assert rows[0].password == "s3cret"

    def test_import_rejects_wrong_kind(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/servers/import-json",
            json={"kind": "wrong", "version": 1, "servers": []},
            headers=auth_headers,
        )
        assert resp.status_code == 400


# ── v1.2.7: Bundle envelope v2 — deployments round-trip ──────────────────────


class TestEnvelopeV2Deployments:
    """Server JSON Export/Import bundle envelope was bumped from v1
    (servers only) to v2 (servers + nested per-protocol deployments)
    in v1.2.5 / v1.2.7. Verify both export shape and import behaviour.

    v1 envelopes (no `deployments` key) must still import gracefully —
    they just produce empty deployments per server.
    """

    def test_export_uses_the_current_envelope_version(
        self, client, admin_user, auth_headers, sample_server
    ):
        """v3 added the nested panel registration. Older bundles simply
        carry no panel and restore exactly as they did."""
        resp = client.get("/api/servers/export-json", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["version"] == 3

    def test_export_nests_deployments_under_server(
        self, client, admin_user, auth_headers, sample_server, session
    ):
        # Seed a deployment for the sample server
        from app.models import ServerDeployment
        import json
        d = ServerDeployment(
            server_id=sample_server.id,
            protocol="naive",
            config_json=json.dumps({
                "domain": "naive.example.com",
                "naive_user": "vasya",
                "naive_pass": "abc123",
                "email": "vasya@example.com",
            }),
            status="deployed",
        )
        session.add(d)
        session.commit()

        resp = client.get("/api/servers/export-json", headers=auth_headers)
        body = resp.json()
        assert body["count"] == 1
        s = body["servers"][0]
        assert "deployments" in s
        assert len(s["deployments"]) == 1
        dep = s["deployments"][0]
        assert dep["protocol"] == "naive"
        assert dep["status"] == "deployed"
        # `config` was deserialised back from JSON and re-nested
        assert dep["config"]["domain"] == "naive.example.com"
        assert dep["config"]["naive_user"] == "vasya"
        # `last_node_id` deliberately NOT exported (Node ids don't
        # survive instance migration; re-link via "Create Node" click)
        assert "last_node_id" not in dep

    def test_v2_round_trip_preserves_deployments(
        self, client, admin_user, auth_headers, sample_server, session
    ):
        # Seed deployment, export with secrets, wipe, re-import
        from app.models import ServerDeployment, Server
        import json
        from sqlmodel import select

        session.add(ServerDeployment(
            server_id=sample_server.id,
            protocol="naive",
            config_json=json.dumps({"domain": "n.example.com", "naive_user": "u"}),
            status="configured",
        ))
        session.commit()

        bundle = client.get(
            "/api/servers/export-json?include_secrets=true",
            headers=auth_headers,
        ).json()

        # Wipe + import
        resp = client.post(
            "/api/servers/import-json?replace=true",
            json=bundle,
            headers=auth_headers,
        )
        body = resp.json()
        assert body["imported"] == 1
        assert body["deployments_restored"] == 1

        session.expire_all()
        servers = session.exec(select(Server)).all()
        assert len(servers) == 1
        deps = session.exec(
            select(ServerDeployment).where(ServerDeployment.server_id == servers[0].id)
        ).all()
        assert len(deps) == 1
        assert deps[0].protocol == "naive"
        assert deps[0].status == "configured"
        cfg = json.loads(deps[0].config_json)
        assert cfg["domain"] == "n.example.com"
        # last_node_id intentionally None — wasn't in the bundle
        assert deps[0].last_node_id is None

    def test_v1_envelope_imports_with_empty_deployments(
        self, client, admin_user, auth_headers, session
    ):
        # Older PiTun produced v1 envelopes (no `deployments` key).
        # Importing such a bundle must not crash; deployments end up
        # empty for those servers.
        from app.models import Server, ServerDeployment
        from sqlmodel import select

        v1_bundle = {
            "kind": "pitun-servers-export",
            "version": 1,
            "include_secrets": False,
            "servers": [
                {"name": "OldSrv", "host": "old.example.com", "port": 22,
                 "user": "root", "auth_type": "password"},
            ],
        }
        resp = client.post(
            "/api/servers/import-json", json=v1_bundle, headers=auth_headers
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["imported"] == 1
        # Counter for v2 only — v1 imports should report 0 deps restored
        assert body["deployments_restored"] == 0

        session.expire_all()
        servers = session.exec(select(Server)).all()
        assert len(servers) == 1
        deps = session.exec(
            select(ServerDeployment).where(ServerDeployment.server_id == servers[0].id)
        ).all()
        assert deps == []

    def test_unknown_envelope_version_rejected(self, client, admin_user, auth_headers):
        # Future v3 schema must NOT silently degrade to v2 — that
        # would risk dropping fields a future maintainer added.
        # The envelope-version check should reject anything outside
        # {1, 2}.
        resp = client.post(
            "/api/servers/import-json",
            json={"kind": "pitun-servers-export", "version": 99, "servers": []},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_import_skips_malformed_deployments(
        self, client, admin_user, auth_headers, session
    ):
        # Defense: a bundle with malformed deployment entries (missing
        # `protocol`, non-dict config, etc.) should still import the
        # server itself, just skipping the bad deployment row.
        from app.models import Server, ServerDeployment
        from sqlmodel import select

        bundle = {
            "kind": "pitun-servers-export",
            "version": 2,
            "include_secrets": False,
            "servers": [
                {
                    "name": "MixedQuality", "host": "x.example.com",
                    "port": 22, "user": "root", "auth_type": "password",
                    "deployments": [
                        {"protocol": "naive", "config": {"domain": "n"}, "status": "configured"},
                        # Missing protocol → skipped
                        {"config": {"hello": "world"}},
                        # Non-dict config → coerced to empty dict
                        {"protocol": "wireguard", "config": "not a dict", "status": "configured"},
                        # Non-dict entry → skipped
                        "not a dict",
                    ],
                },
            ],
        }
        resp = client.post(
            "/api/servers/import-json", json=bundle, headers=auth_headers,
        )
        body = resp.json()
        assert resp.status_code == 200, body
        # The server itself imported fine
        assert body["imported"] == 1
        # 2 valid deployments restored (naive + wireguard with coerced config)
        assert body["deployments_restored"] == 2

        # Verify in DB
        session.expire_all()
        srv = session.exec(select(Server)).first()
        deps = session.exec(
            select(ServerDeployment).where(ServerDeployment.server_id == srv.id)
        ).all()
        assert len(deps) == 2
        protocols = {d.protocol for d in deps}
        assert protocols == {"naive", "wireguard"}


class TestPanelTravelsWithTheServer:
    """A server exported with its secrets and restored elsewhere used to
    arrive with x-ui plainly installed and the X-ui page empty: the panel
    registration lives in its own table and was left behind, so the operator
    reconnected it by hand on every migration."""

    def _seed_panel(self, session, server_id):
        from app.models import XuiServer
        row = XuiServer(
            server_id=server_id, api_token="tok-abc",
            panel_user="admin", panel_pass="s3cret",
            panel_port=2053, panel_basepath="/xyz", mode="bare",
        )
        session.add(row)
        session.commit()
        return row

    def test_export_with_secrets_carries_the_panel(
        self, client, admin_user, auth_headers, sample_server, session,
    ):
        self._seed_panel(session, sample_server.id)
        body = client.get("/api/servers/export-json?include_secrets=true",
                          headers=auth_headers).json()
        panel = body["servers"][0]["panel"]
        assert panel["panel_port"] == 2053
        assert panel["panel_basepath"] == "/xyz"
        assert panel["api_token"] == "tok-abc"
        assert panel["panel_pass"] == "s3cret"

    def test_export_without_secrets_keeps_the_shape_and_drops_the_keys(
        self, client, admin_user, auth_headers, sample_server, session,
    ):
        """Saying "a panel was here" is useful; handing out its token in a
        file meant for sharing is not — same rule as the SSH credentials."""
        self._seed_panel(session, sample_server.id)
        body = client.get("/api/servers/export-json", headers=auth_headers).json()
        panel = body["servers"][0]["panel"]
        assert panel["panel_port"] == 2053
        assert "api_token" not in panel
        assert "panel_pass" not in panel

    def test_a_server_without_a_panel_carries_none(
        self, client, admin_user, auth_headers, sample_server,
    ):
        body = client.get("/api/servers/export-json", headers=auth_headers).json()
        assert "panel" not in body["servers"][0]

    def test_import_restores_the_panel(
        self, client, admin_user, auth_headers, session,
    ):
        from app.models import XuiServer
        bundle = {
            "kind": "pitun-servers-export", "version": 3,
            "include_secrets": True,
            "servers": [{
                "name": "vps-with-panel", "host": "198.51.100.9", "port": 22,
                "user": "root", "auth_type": "password", "password": "pw",
                "deployments": [],
                "panel": {
                    "panel_port": 2053, "panel_basepath": "/xyz", "mode": "bare",
                    "panel_user": "admin", "panel_pass": "s3cret",
                    "api_token": "tok-abc",
                },
            }],
        }
        r = client.post("/api/servers/import-json", json=bundle, headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["panels_restored"] == 1

        rows = session.query(XuiServer).all() if hasattr(session, "query") else None
        if rows is None:
            from sqlmodel import select
            rows = session.exec(select(XuiServer)).all()
        assert len(rows) == 1
        assert rows[0].api_token == "tok-abc"
        assert rows[0].panel_port == 2053

    def test_a_panel_with_no_credentials_is_counted_not_dropped(
        self, client, admin_user, auth_headers,
    ):
        """A secret-stripped bundle says a panel was there but carries
        nothing to authenticate with. The operator should be told, not left
        wondering why the X-ui page is empty again."""
        bundle = {
            "kind": "pitun-servers-export", "version": 3,
            "include_secrets": False,
            "servers": [{
                "name": "vps-stripped", "host": "198.51.100.10", "port": 22,
                "user": "root", "auth_type": "password",
                "deployments": [],
                "panel": {"panel_port": 2053, "panel_basepath": "/xyz",
                          "mode": "bare", "panel_user": "admin"},
            }],
        }
        r = client.post("/api/servers/import-json", json=bundle, headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["panels_restored"] == 0
        assert r.json()["panels_without_credentials"] == 1

    def test_older_bundles_still_import(self, client, admin_user, auth_headers):
        bundle = {
            "kind": "pitun-servers-export", "version": 2,
            "servers": [{"name": "old", "host": "198.51.100.11", "port": 22,
                         "user": "root", "auth_type": "password",
                         "deployments": []}],
        }
        r = client.post("/api/servers/import-json", json=bundle, headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["imported"] == 1
        assert r.json()["panels_restored"] == 0
