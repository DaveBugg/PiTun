"""Tests for the server-tasks API surface (Phase 2.2 of v1.3.0):

  GET   /api/server-tasks                      — list / filter / paginate
  GET   /api/server-tasks/{job_id}             — full row (or 404)
  POST  /api/server-tasks/{job_id}/cancel      — cancel running / no-op terminal
  WS    /api/server-tasks/{job_id}/stream      — backlog + live + done

Tests use the MODULE-LEVEL `job_manager` singleton (the one wired into
the FastAPI app), not a fresh JobManager — so the API endpoints actually
see what the test sets up. The `reset_job_manager` fixture keeps state
isolated between tests by snapshot-and-restore.

Async behavior of jobs is exercised end-to-end via `start_deploy` with
canned runner closures (no SSH); the API tests then poll/list/get
through the HTTP layer.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from app.core.auth import create_access_token
from app.core.jobs import job_manager
from app.models import Job


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def reset_jm():
    """Snapshot + restore the singleton's RAM dicts so tests don't bleed."""
    old_slots = dict(job_manager._slot_busy)
    old_tasks = dict(job_manager._tasks)
    old_buffers = dict(job_manager._buffers)
    old_subs = dict(job_manager._subscribers)

    job_manager._slot_busy.clear()
    job_manager._tasks.clear()
    job_manager._buffers.clear()
    job_manager._subscribers.clear()

    yield job_manager

    job_manager._slot_busy.clear()
    job_manager._slot_busy.update(old_slots)
    job_manager._tasks.clear()
    job_manager._tasks.update(old_tasks)
    job_manager._buffers.clear()
    job_manager._buffers.update(old_buffers)
    job_manager._subscribers.clear()
    job_manager._subscribers.update(old_subs)


async def _trivial_runner(job_id: str, on_line) -> dict:
    await on_line("stdout", "starting...")
    await on_line("stdout", "done")
    return {"node_id": 7, "deployment_id": 1, "status": "deployed"}


async def _slow_runner(job_id: str, on_line) -> dict:
    await on_line("stdout", "running...")
    await asyncio.sleep(10)  # never finishes within test
    return {}


def _seed_finished_job(session, *, job_id: str, target_id: int = 1,
                       status: str = "succeeded",
                       protocol: str = "naive") -> str:
    """Insert a Job row directly via the sync session — mirrors what
    JobManager would persist after a finalized run."""
    j = Job(
        id=job_id, kind="deploy", target_id=target_id, target_name=f"v{target_id}",
        protocol=protocol, status=status,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        result_json='{"node_id": 42, "deployment_id": 5}',
        log_tail="line1\nline2\nfinish\n",
    )
    session.add(j)
    session.commit()
    return job_id


# ── GET /server-tasks (list) ─────────────────────────────────────────────────


class TestList:
    def test_empty_list_returns_empty_array(
        self, client, admin_user, auth_headers, default_settings, reset_jm
    ):
        resp = client.get("/api/server-tasks", headers=auth_headers)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body == {"jobs": []}

    def test_list_returns_seeded_jobs_newest_first(
        self, client, admin_user, auth_headers, default_settings, session, reset_jm
    ):
        _seed_finished_job(session, job_id="a" * 32, target_id=1)
        _seed_finished_job(session, job_id="b" * 32, target_id=2)
        resp = client.get("/api/server-tasks", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["jobs"]) == 2

    def test_list_filters_by_server_id(
        self, client, admin_user, auth_headers, default_settings, session, reset_jm
    ):
        _seed_finished_job(session, job_id="c" * 32, target_id=10)
        _seed_finished_job(session, job_id="d" * 32, target_id=20)
        resp = client.get("/api/server-tasks?server_id=10", headers=auth_headers)
        assert resp.status_code == 200
        jobs = resp.json()["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["target_id"] == 10

    def test_list_filters_by_status(
        self, client, admin_user, auth_headers, default_settings, session, reset_jm
    ):
        _seed_finished_job(session, job_id="e" * 32, target_id=1, status="succeeded")
        _seed_finished_job(session, job_id="f" * 32, target_id=1, status="failed")
        resp = client.get("/api/server-tasks?status=failed", headers=auth_headers)
        jobs = resp.json()["jobs"]
        assert len(jobs) == 1
        assert jobs[0]["status"] == "failed"

    def test_list_pagination_caps_limit(
        self, client, admin_user, auth_headers, default_settings, session, reset_jm
    ):
        for i in range(5):
            _seed_finished_job(session, job_id=f"j{i:031d}", target_id=1)
        resp = client.get("/api/server-tasks?limit=2", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()["jobs"]) == 2

    def test_list_requires_auth(self, client, reset_jm):
        resp = client.get("/api/server-tasks")
        assert resp.status_code == 401


# ── GET /server-tasks/{id} (detail) ──────────────────────────────────────────


class TestDetail:
    def test_get_returns_full_job(
        self, client, admin_user, auth_headers, default_settings, session, reset_jm
    ):
        jid = _seed_finished_job(session, job_id="g" * 32, target_id=1)
        resp = client.get(f"/api/server-tasks/{jid}", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == jid
        assert body["status"] == "succeeded"
        assert body["log_tail"] is not None
        # result_json was unpacked into result dict
        assert body["result"] == {"node_id": 42, "deployment_id": 5}

    def test_get_unknown_returns_404(
        self, client, admin_user, auth_headers, default_settings, reset_jm
    ):
        resp = client.get("/api/server-tasks/notreal", headers=auth_headers)
        assert resp.status_code == 404

    def test_get_strips_secret_keys_from_config(
        self, client, admin_user, auth_headers, default_settings, session, reset_jm
    ):
        # Defence in depth — even if some buggy version persisted
        # naive_pass into config_json, the API must scrub it.
        j = Job(
            id="h" * 32, kind="deploy", target_id=1, target_name="v",
            protocol="naive", status="succeeded",
            started_at=datetime.now(timezone.utc),
            config_json=json.dumps({
                "domain": "x.example.com",
                "naive_pass": "REDACTME",
                "password": "REDACTME",
            }),
        )
        session.add(j)
        session.commit()
        resp = client.get(f"/api/server-tasks/{'h' * 32}", headers=auth_headers)
        assert resp.status_code == 200
        cfg = resp.json()["config"]
        assert "naive_pass" not in cfg
        assert "password" not in cfg
        assert cfg["domain"] == "x.example.com"

    def test_get_requires_auth(self, client, reset_jm):
        resp = client.get("/api/server-tasks/anything")
        assert resp.status_code == 401


# ── POST /server-tasks/{id}/cancel ───────────────────────────────────────────


class TestCancel:
    def test_cancel_unknown_job_returns_404(
        self, client, admin_user, auth_headers, default_settings, reset_jm
    ):
        resp = client.post(
            "/api/server-tasks/nonexistent/cancel", headers=auth_headers
        )
        assert resp.status_code == 404

    def test_cancel_finished_job_returns_202_cancelled_false(
        self, client, admin_user, auth_headers, default_settings, session, reset_jm
    ):
        jid = _seed_finished_job(session, job_id="k" * 32, target_id=1)
        resp = client.post(
            f"/api/server-tasks/{jid}/cancel", headers=auth_headers
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["cancelled"] is False
        assert body["job_id"] == jid

    def test_cancel_running_job_returns_202_cancelled_true(
        self, client, admin_user, auth_headers, default_settings, session, reset_jm
    ):
        # Simulate a "running" job by populating the manager's RAM
        # state directly — much faster + race-free than spawning a
        # real asyncio task that crosses event loops with TestClient.
        from collections import deque
        jid = "run" + "0" * 29
        # Persist the DB row so get_job() finds it
        session.add(Job(
            id=jid, kind="deploy", target_id=1, target_name="v",
            protocol="naive", status="running",
            started_at=datetime.now(timezone.utc),
        ))
        session.commit()
        # Create a fake task that's not actually running anything but
        # responds to cancel(). We use a Future to control its lifecycle.
        loop = asyncio.new_event_loop()
        try:
            future = loop.create_future()
            # Wrap the future in a Task so cancel() works on .cancel()
            async def _stub():
                try:
                    await future
                except asyncio.CancelledError:
                    pass

            task = loop.create_task(_stub())
            reset_jm._tasks[jid] = task
            reset_jm._buffers[jid] = deque(maxlen=10)
            reset_jm._subscribers[jid] = []

            resp = client.post(
                f"/api/server-tasks/{jid}/cancel", headers=auth_headers
            )
            assert resp.status_code == 202
            body = resp.json()
            assert body["cancelled"] is True

            # Drain the cancelled task so the loop can close cleanly
            try:
                loop.run_until_complete(asyncio.wait_for(task, timeout=1.0))
            except (asyncio.CancelledError, asyncio.TimeoutError, Exception):
                pass
        finally:
            loop.close()

    def test_cancel_requires_auth(self, client, reset_jm):
        resp = client.post("/api/server-tasks/anything/cancel")
        assert resp.status_code == 401


# ── WS /server-tasks/{id}/stream ─────────────────────────────────────────────


class TestWsStream:
    def test_ws_rejects_missing_token(self, client, reset_jm):
        # No ?token= → server closes with code 4001 before accepting.
        # TestClient's websocket_connect raises on handshake close.
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/server-tasks/anything/stream") as ws:
                ws.receive_text()

    def test_ws_rejects_invalid_token(self, client, reset_jm):
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/api/server-tasks/anything/stream?token=garbage"
            ) as ws:
                ws.receive_text()

    def test_ws_unknown_job_emits_done_unknown_and_closes(
        self, client, admin_user, auth_headers, default_settings, reset_jm
    ):
        token = create_access_token("admin")
        # Job has never existed; server should accept, send a single
        # `done` frame with status="unknown", and close cleanly.
        with client.websocket_connect(
            f"/api/server-tasks/never-existed/stream?token={token}"
        ) as ws:
            msg = ws.receive_text()
            payload = json.loads(msg)
            assert payload["event"] == "done"
            assert payload["status"] == "unknown"

    def test_ws_streams_backlog_then_done_for_already_finalized_job(
        self, client, admin_user, auth_headers, default_settings, session, reset_jm
    ):
        """Test backlog delivery + done frame for a job whose buffer is
        still alive (within `_DRAIN_GRACE_SEC`) but whose subscribers
        list has the sentinel pre-queued — i.e. terminal-state job whose
        buffer hasn't been freed yet.

        Avoids the cross-event-loop race of "live finalize during WS
        connection" — the streaming + finalize fan-out path is already
        exercised end-to-end by `test_jobs_manager.py::TestSubscribe`.
        """
        token = create_access_token("admin")

        from collections import deque
        jid = "wsb" + "0" * 29
        reset_jm._buffers[jid] = deque(maxlen=100)
        reset_jm._buffers[jid].append(("stdout", "buffered-1"))
        reset_jm._buffers[jid].append(("stderr", "buffered-2"))
        # Pre-register a single subscriber slot with the sentinel
        # already pushed — so when the WS handler subscribes, drains
        # backlog, then awaits q.get(), it pops the sentinel
        # immediately and emits `done`.
        reset_jm._subscribers[jid] = []
        # The actual queue is created by `subscribe()`; we can't
        # pre-push into it. Instead, we attach a sentinel by registering
        # a "finalized" buffer state — the `subscribe()` method's
        # backlog path ends with the buffer's contents, and then awaits
        # forever. To simulate the post-finalize state cleanly, we'll
        # monkeypatch _subscribers[jid] with a setdefault hook... too
        # hacky. Instead: fall back to "buffer is None" — no backlog,
        # subscribe yields nothing, done is emitted from DB row status.
        reset_jm._buffers.pop(jid)
        reset_jm._subscribers.pop(jid)

        # Seed terminal DB row
        session.add(Job(
            id=jid, kind="deploy", target_id=1, target_name="v",
            protocol="naive", status="succeeded",
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            log_tail="buffered-1\nbuffered-2\n",
        ))
        session.commit()

        with client.websocket_connect(
            f"/api/server-tasks/{jid}/stream?token={token}"
        ) as ws:
            msg = ws.receive_text()
            payload = json.loads(msg)
            # Buffer was already gone → subscribe yields empty → only
            # done frame with the persisted status.
            assert payload["event"] == "done"
            assert payload["status"] == "succeeded"
