"""Tests for `POST /api/servers/{id}/deploy` — Phase 1 of v1.3.0 auto-deploy.

We mock `core.ssh.exec_remote_script` so tests don't actually SSH
anywhere. Each test injects a canned `DeployResult` and verifies the
endpoint's downstream behaviour: status mapping, Node creation,
ServerDeployment upsert, response shape, error paths.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from app.core.ssh import DeployResult
from app.models import Node, Server, ServerDeployment


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def server(session):
    """A registered Server row to deploy against."""
    s = Server(
        name="VPS Frankfurt",
        host="vps.example.com",
        port=22,
        user="root",
        auth_type="password",
        password="rootpw",
    )
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _mk_success_result(uri: str = "naive+https://pitun:hunter2@vps.example.com:443/?padding=1#vps") -> DeployResult:
    return DeployResult(
        ok=True,
        exit_code=0,
        stdout=f"Provisioning Caddy...\nDONE\nURI={uri}\n",
        stderr="",
        duration_sec=180.5,
        connect_latency_ms=42,
    )


def _mk_failure_result(error: str = "script exit=1: domain validation failed") -> DeployResult:
    return DeployResult(
        ok=False,
        exit_code=1,
        stdout="starting...\n",
        stderr="ERROR: domain validation failed\n",
        duration_sec=12.3,
        error=error,
        connect_latency_ms=42,
    )


def _patch_exec(canned: DeployResult):
    """Context manager that replaces exec_remote_script with an
    AsyncMock returning `canned`. Patches BOTH the source module and
    the imported reference inside api/servers.py since the endpoint
    does a function-local import (`from app.core.ssh import exec_remote_script`).
    """
    return patch("app.core.ssh.exec_remote_script", new_callable=AsyncMock, return_value=canned)


# ── Validation paths ─────────────────────────────────────────────────────────


class TestDeployValidation:
    def test_unknown_server_returns_404(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/servers/99999/deploy",
            json={"protocol": "naive", "config": {"domain": "x", "email": "y"}},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_unsupported_protocol_returns_422(
        self, client, admin_user, auth_headers, server
    ):
        # `wireguard` rejected at the Pydantic validator level
        resp = client.post(
            f"/api/servers/{server.id}/deploy",
            json={"protocol": "wireguard", "config": {}},
            headers=auth_headers,
        )
        # Pydantic validator → 422 (FastAPI standard for body validation)
        assert resp.status_code == 422

    def test_naive_missing_domain_returns_400(
        self, client, admin_user, auth_headers, server
    ):
        # `build_naive_env` raises ValueError → 400
        resp = client.post(
            f"/api/servers/{server.id}/deploy",
            json={"protocol": "naive", "config": {"email": "me@x"}},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "domain" in resp.json()["detail"].lower()

    def test_naive_missing_email_returns_400(
        self, client, admin_user, auth_headers, server
    ):
        resp = client.post(
            f"/api/servers/{server.id}/deploy",
            json={"protocol": "naive", "config": {"domain": "x.example.com"}},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "email" in resp.json()["detail"].lower()


# ── Happy path: deployed + Node row created ──────────────────────────────────


class TestDeploySuccess:
    def test_deploy_creates_node_and_deployment(
        self, client, admin_user, auth_headers, server, session
    ):
        canned = _mk_success_result()
        with _patch_exec(canned):
            resp = client.post(
                f"/api/servers/{server.id}/deploy",
                json={
                    "protocol": "naive",
                    "config": {
                        "domain": "vps.example.com",
                        "email": "me@example.com",
                    },
                },
                headers=auth_headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "deployed"
        assert body["exit_code"] == 0
        assert body["parsed_uri"].startswith("naive+https://")
        assert body["node_id"] is not None
        assert body["deployment_id"] is not None
        assert body["error"] is None
        assert body["connect_latency_ms"] == 42
        assert body["duration_sec"] == 180.5

        # Node row created from URI
        from sqlmodel import select
        session.expire_all()
        nodes = session.exec(select(Node).where(Node.id == body["node_id"])).all()
        assert len(nodes) == 1
        assert nodes[0].protocol == "naive"
        assert nodes[0].address == "vps.example.com"
        assert nodes[0].port == 443

        # ServerDeployment upserted
        deps = session.exec(
            select(ServerDeployment).where(ServerDeployment.server_id == server.id)
        ).all()
        assert len(deps) == 1
        assert deps[0].protocol == "naive"
        assert deps[0].status == "deployed"
        assert deps[0].last_node_id == body["node_id"]
        cfg = json.loads(deps[0].config_json)
        assert cfg["domain"] == "vps.example.com"
        # naive_pass NOT persisted in deployment config (it lives on the
        # Node URI now; duplicating in plain text would be CWE-312).
        assert "naive_pass" not in cfg

    def test_deploy_no_uri_in_stdout_status_deployed_no_uri(
        self, client, admin_user, auth_headers, server, session
    ):
        # Script exits 0 but doesn't emit the `URI=` contract line —
        # script bug or older version. We persist the deployment
        # (status='deployed_no_uri') but DON'T create a Node — admin
        # must add it manually from whatever stdout shows.
        canned = DeployResult(
            ok=True, exit_code=0,
            stdout="Provisioning... DONE (no URI line printed)\n",
            stderr="",
            duration_sec=120.0,
        )
        with _patch_exec(canned):
            resp = client.post(
                f"/api/servers/{server.id}/deploy",
                json={
                    "protocol": "naive",
                    "config": {"domain": "vps.example.com", "email": "me@x"},
                },
                headers=auth_headers,
            )

        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "deployed_no_uri"
        assert body["node_id"] is None
        assert body["parsed_uri"] is None
        # Deployment row still created so admin sees what happened
        assert body["deployment_id"] is not None

    def test_deploy_upserts_existing_deployment(
        self, client, admin_user, auth_headers, server, session
    ):
        # Pre-seed an existing deployment (from a previous run that
        # failed). New successful deploy should UPDATE it, not insert.
        from sqlmodel import select
        existing = ServerDeployment(
            server_id=server.id,
            protocol="naive",
            config_json=json.dumps({"domain": "old.example.com"}),
            status="failed",
        )
        session.add(existing)
        session.commit()
        session.refresh(existing)
        existing_id = existing.id

        canned = _mk_success_result()
        with _patch_exec(canned):
            resp = client.post(
                f"/api/servers/{server.id}/deploy",
                json={
                    "protocol": "naive",
                    "config": {"domain": "new.example.com", "email": "me@x"},
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # Same deployment row, updated state
        assert body["deployment_id"] == existing_id
        assert body["status"] == "deployed"

        # Verify single row in DB (no duplicate insert)
        session.expire_all()
        deps = session.exec(
            select(ServerDeployment).where(ServerDeployment.server_id == server.id)
        ).all()
        assert len(deps) == 1
        assert deps[0].id == existing_id
        # Status flipped, last_node_id populated
        assert deps[0].status == "deployed"
        assert deps[0].last_node_id == body["node_id"]
        # Config updated to the new run's values
        cfg = json.loads(deps[0].config_json)
        assert cfg["domain"] == "new.example.com"


# ── Failure paths ────────────────────────────────────────────────────────────


class TestDeployFailure:
    def test_script_nonzero_exit_recorded_as_failed(
        self, client, admin_user, auth_headers, server, session
    ):
        from sqlmodel import select
        canned = _mk_failure_result()
        with _patch_exec(canned):
            resp = client.post(
                f"/api/servers/{server.id}/deploy",
                json={
                    "protocol": "naive",
                    "config": {"domain": "x.example.com", "email": "me@x"},
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200, resp.text  # API call succeeded
        body = resp.json()
        assert body["status"] == "failed"
        assert body["exit_code"] == 1
        assert body["node_id"] is None
        assert body["parsed_uri"] is None
        assert "domain validation failed" in body["error"]
        # Deployment row exists (audit trail) with status='failed'
        session.expire_all()
        deps = session.exec(
            select(ServerDeployment).where(ServerDeployment.server_id == server.id)
        ).all()
        assert len(deps) == 1
        assert deps[0].status == "failed"
        assert deps[0].last_node_id is None

    def test_ssh_error_recorded_as_failed(
        self, client, admin_user, auth_headers, server, session
    ):
        canned = DeployResult(
            ok=False,
            exit_code=-1,
            stdout="",
            stderr="",
            duration_sec=8.0,
            error="TCP: Connection refused",
        )
        with _patch_exec(canned):
            resp = client.post(
                f"/api/servers/{server.id}/deploy",
                json={
                    "protocol": "naive",
                    "config": {"domain": "x.example.com", "email": "me@x"},
                },
                headers=auth_headers,
            )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "failed"
        assert body["error"].startswith("TCP:")

    def test_truncated_stdout_tail_in_response(
        self, client, admin_user, auth_headers, server
    ):
        # The endpoint returns last 2000 chars of stdout, not the full
        # blob. Verify the cap is applied.
        big = "x" * 5000 + "\nURI=naive+https://u:p@vps:443\n"
        canned = DeployResult(
            ok=True, exit_code=0, stdout=big, stderr="", duration_sec=1.0,
        )
        with _patch_exec(canned):
            resp = client.post(
                f"/api/servers/{server.id}/deploy",
                json={
                    "protocol": "naive",
                    "config": {"domain": "vps.example.com", "email": "me@x"},
                },
                headers=auth_headers,
            )
        body = resp.json()
        assert len(body["stdout_tail"]) <= 2000
        # The URI line is at the end → must be in the tail
        assert "URI=naive+https" in body["stdout_tail"]


# ── Authentication ───────────────────────────────────────────────────────────


def test_deploy_requires_auth(client, server):
    resp = client.post(
        f"/api/servers/{server.id}/deploy",
        json={"protocol": "naive", "config": {"domain": "x", "email": "y"}},
    )
    assert resp.status_code == 401
