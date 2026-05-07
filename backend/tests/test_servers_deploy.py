"""Tests for `POST /api/servers/{id}/deploy` — Phase 2.2 of v1.3.0.

Phase 2.2 changed the endpoint from sync (block ~5 min, return final
state) to async-spawn (return 202 + job_id, work happens in a
JobManager task). These tests cover the synchronous pre-flight
contract — validation + slot-busy 409 + the 202 response shape.

The async runner's success/failure mapping (deployed / deployed_no_uri /
failed) is exercised end-to-end by `test_jobs_manager.py` against
`JobManager.start_deploy` directly, since reconstructing the exact
asyncio race between submit + finalize through TestClient is fragile.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.core.ssh import DeployResult
from app.models import Server


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


@pytest.fixture
def reset_job_manager():
    """Reset the module-level `job_manager` singleton's RAM state so
    leftover slot locks from prior tests don't bleed across.
    """
    from app.core.jobs import job_manager
    # Snapshot
    old_slots = dict(job_manager._slot_busy)
    old_tasks = dict(job_manager._tasks)
    old_buffers = dict(job_manager._buffers)
    old_subs = dict(job_manager._subscribers)

    job_manager._slot_busy.clear()
    job_manager._tasks.clear()
    job_manager._buffers.clear()
    job_manager._subscribers.clear()

    yield job_manager

    # Restore (so other tests using the same singleton aren't broken)
    job_manager._slot_busy.clear()
    job_manager._slot_busy.update(old_slots)
    job_manager._tasks.clear()
    job_manager._tasks.update(old_tasks)
    job_manager._buffers.clear()
    job_manager._buffers.update(old_buffers)
    job_manager._subscribers.clear()
    job_manager._subscribers.update(old_subs)


def _patch_streaming(canned: DeployResult):
    """Replace `exec_remote_script_streaming` with a fast mock that
    completes "instantly" — keeps the spawned background runner from
    actually trying SSH. Patches the source module since the endpoint
    does a function-local import (lazy, runs inside the runner closure).
    """
    async def fake(*args, **kwargs):
        # Pump a single line through on_line so the buffer isn't empty
        on_line = kwargs.get("on_line")
        if on_line is not None:
            await on_line("stdout", "[fake] starting")
            await on_line("stdout", "[fake] done")
        return canned

    return patch("app.core.ssh.exec_remote_script_streaming", new=fake)


def _success_result(uri: str = "naive+https://pitun:hunter2@vps.example.com:443/?padding=1#vps") -> DeployResult:
    return DeployResult(
        ok=True,
        exit_code=0,
        stdout=f"Provisioning Caddy...\nDONE\nURI={uri}\n",
        stderr="",
        duration_sec=180.5,
        connect_latency_ms=42,
    )


# ── Validation paths (synchronous — these must fail before spawning a job) ───


class TestDeployValidation:
    def test_unknown_server_returns_404(
        self, client, admin_user, auth_headers, reset_job_manager
    ):
        resp = client.post(
            "/api/servers/99999/deploy",
            json={"protocol": "naive", "config": {"domain": "x", "email": "y"}},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_unsupported_protocol_returns_422(
        self, client, admin_user, auth_headers, server, reset_job_manager
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
        self, client, admin_user, auth_headers, server, reset_job_manager
    ):
        resp = client.post(
            f"/api/servers/{server.id}/deploy",
            json={"protocol": "naive", "config": {"email": "me@x"}},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "domain" in resp.json()["detail"].lower()

    def test_naive_missing_email_returns_400(
        self, client, admin_user, auth_headers, server, reset_job_manager
    ):
        resp = client.post(
            f"/api/servers/{server.id}/deploy",
            json={"protocol": "naive", "config": {"domain": "x.example.com"}},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "email" in resp.json()["detail"].lower()


# ── 202 Accepted shape ───────────────────────────────────────────────────────


class TestDeployAccepted:
    def test_deploy_returns_202_with_job_id(
        self, client, admin_user, auth_headers, server,
        default_settings, reset_job_manager,
    ):
        canned = _success_result()
        with _patch_streaming(canned):
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

        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert "job_id" in body
        assert isinstance(body["job_id"], str)
        assert len(body["job_id"]) == 32  # uuid hex
        assert body["server_id"] == server.id
        assert body["protocol"] == "naive"


# ── Slot-busy 409 ────────────────────────────────────────────────────────────


class TestSlotBusy:
    def test_second_deploy_on_same_slot_returns_409(
        self, client, admin_user, auth_headers, server,
        default_settings, reset_job_manager,
    ):
        # Pre-populate the slot lock to simulate "deploy already running"
        # without actually spawning a long-running asyncio task that
        # would interleave with the second request unpredictably.
        from app.core.jobs import job_manager
        job_manager._slot_busy[(server.id, "naive")] = True

        resp = client.post(
            f"/api/servers/{server.id}/deploy",
            json={
                "protocol": "naive",
                "config": {"domain": "x.example.com", "email": "me@x"},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 409, resp.text
        assert "already running" in resp.json()["detail"].lower() or \
               "naive" in resp.json()["detail"].lower()


# ── Authentication ───────────────────────────────────────────────────────────


def test_deploy_requires_auth(client, server, reset_job_manager):
    resp = client.post(
        f"/api/servers/{server.id}/deploy",
        json={"protocol": "naive", "config": {"domain": "x", "email": "y"}},
    )
    assert resp.status_code == 401
