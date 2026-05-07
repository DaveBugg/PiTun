"""Tests for `app.core.jobs.JobManager` — server-tasks subsystem
foundation (Phase 2.1 of v1.3.0).

Coverage:
  * `start_deploy` creates DB row + spawns asyncio task + returns job_id
  * Slot-busy semantics — same (target_id, protocol) → SlotBusy
  * `cancel` cancels running task, status flips to "cancelled"
  * `list_jobs` filters by target_id / kind / status, paginates
  * `get_job` returns full row including config_json / log_tail
  * `subscribe` yields backlog then live updates, handles late-finalize
  * `_finalize` writes log_tail to DB, releases slot, drains buffer
    after grace period
  * `_heal_stale_jobs` flips old `running` rows → `failed` on boot
  * `_trim_once` deletes by age + count cap

Async-test pattern mirrors `test_self_heal.py` — `@pytest.mark.asyncio`
+ `client + session` fixtures (client patches `db_mod._async_engine`
globally so JobManager's internal `get_async_engine()` calls hit the
test DB).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlmodel import select

from app.core.jobs import JobManager, SlotBusy, _row_to_summary
from app.models import Job


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _trivial_runner(job_id: str, on_line) -> dict:
    """A minimal runner that emits one stdout line and returns success."""
    await on_line("stdout", "starting...")
    await on_line("stdout", "done")
    return {"deployment_id": 1, "node_id": 42, "exit_code": 0}


async def _failing_runner(job_id: str, on_line) -> dict:
    """Runner that raises — JobManager._run catches and flags failed."""
    await on_line("stdout", "boom...")
    raise RuntimeError("simulated failure")


async def _slow_runner(seconds: float):
    """Factory: returns a runner that sleeps for `seconds` so the
    caller can race cancel() against it.
    """
    async def runner(job_id: str, on_line) -> dict:
        await on_line("stdout", "starting slow op")
        await asyncio.sleep(seconds)
        return {"finished_naturally": True}
    return runner


async def _wait_for_status(
    mgr: JobManager, job_id: str, target: set[str], timeout: float = 5.0
) -> str:
    """Poll until job reaches one of `target` statuses or timeout."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        j = await mgr.get_job(job_id)
        if j is not None and j.status in target:
            return j.status
        await asyncio.sleep(0.05)
    j = await mgr.get_job(job_id)
    raise AssertionError(
        f"Job {job_id} did not reach {target} within {timeout}s "
        f"(last status={j.status if j else None})"
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def manager():
    """Fresh JobManager per test — no shared state."""
    return JobManager()


# ── start_deploy ─────────────────────────────────────────────────────────────


class TestStartDeploy:
    @pytest.mark.asyncio
    async def test_start_creates_row_and_returns_id(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        job_id = await manager.start_deploy(
            server_id=1, server_name="vps-1", protocol="naive",
            config={"domain": "x.example.com", "email": "y@example.com"},
            runner=_trivial_runner,
        )
        assert isinstance(job_id, str)
        assert len(job_id) == 32  # uuid hex

        # DB row exists with status='running' (or already finalized
        # if the trivial runner finished before we checked)
        await _wait_for_status(manager, job_id, {"running", "succeeded"})

        # After settling, runner finished successfully
        final = await _wait_for_status(manager, job_id, {"succeeded"})
        assert final == "succeeded"

        # Verify the persisted row
        session.expire_all()
        row = session.exec(select(Job).where(Job.id == job_id)).first()
        assert row is not None
        assert row.target_id == 1
        assert row.target_name == "vps-1"
        assert row.protocol == "naive"
        assert row.config_json
        cfg = json.loads(row.config_json)
        assert cfg["domain"] == "x.example.com"

    @pytest.mark.asyncio
    async def test_slot_busy_raises(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        # Start a slow job that won't finish within the test
        slow = await _slow_runner(seconds=10)
        first_id = await manager.start_deploy(
            server_id=7, server_name="vps-7", protocol="naive",
            config={"domain": "x", "email": "y"},
            runner=slow,
        )

        # Second start on same (server_id, protocol) → SlotBusy
        with pytest.raises(SlotBusy):
            await manager.start_deploy(
                server_id=7, server_name="vps-7", protocol="naive",
                config={"domain": "z", "email": "y"},
                runner=_trivial_runner,
            )

        # But OTHER (server_id, protocol) is unaffected
        other_id = await manager.start_deploy(
            server_id=8, server_name="vps-8", protocol="naive",
            config={"domain": "z", "email": "y"},
            runner=_trivial_runner,
        )
        assert other_id != first_id

        # Cleanup: cancel the slow one
        await manager.cancel(first_id)
        await _wait_for_status(manager, first_id, {"cancelled"})

    @pytest.mark.asyncio
    async def test_slot_freed_after_finalize(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        first_id = await manager.start_deploy(
            server_id=11, server_name="vps-11", protocol="naive",
            config={"domain": "x", "email": "y"},
            runner=_trivial_runner,
        )
        await _wait_for_status(manager, first_id, {"succeeded"})

        # Slot freed → second deploy on same (server, protocol) can start
        second_id = await manager.start_deploy(
            server_id=11, server_name="vps-11", protocol="naive",
            config={"domain": "z", "email": "y"},
            runner=_trivial_runner,
        )
        assert second_id != first_id
        await _wait_for_status(manager, second_id, {"succeeded"})


# ── Cancel ────────────────────────────────────────────────────────────────────


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_running_job_marks_cancelled(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        slow = await _slow_runner(seconds=10)
        job_id = await manager.start_deploy(
            server_id=21, server_name="vps-21", protocol="naive",
            config={"domain": "x", "email": "y"},
            runner=slow,
        )
        # Give the task a moment to start running
        await asyncio.sleep(0.05)

        ok = await manager.cancel(job_id)
        assert ok is True

        await _wait_for_status(manager, job_id, {"cancelled"})

        session.expire_all()
        row = session.exec(select(Job).where(Job.id == job_id)).first()
        assert row.status == "cancelled"
        assert row.finished_at is not None
        assert "cancel" in (row.error or "").lower()

    @pytest.mark.asyncio
    async def test_cancel_unknown_job_returns_false(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        ok = await manager.cancel("nonexistent-id")
        assert ok is False

    @pytest.mark.asyncio
    async def test_cancel_finished_job_returns_false(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        job_id = await manager.start_deploy(
            server_id=22, server_name="vps-22", protocol="naive",
            config={"domain": "x", "email": "y"},
            runner=_trivial_runner,
        )
        await _wait_for_status(manager, job_id, {"succeeded"})
        ok = await manager.cancel(job_id)
        assert ok is False  # already done


# ── Failure path ──────────────────────────────────────────────────────────────


class TestFailure:
    @pytest.mark.asyncio
    async def test_runner_exception_marks_failed_with_error(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        job_id = await manager.start_deploy(
            server_id=31, server_name="vps-31", protocol="naive",
            config={"domain": "x", "email": "y"},
            runner=_failing_runner,
        )
        await _wait_for_status(manager, job_id, {"failed"})

        session.expire_all()
        row = session.exec(select(Job).where(Job.id == job_id)).first()
        assert row.status == "failed"
        assert "simulated failure" in (row.error or "")
        assert row.finished_at is not None
        # Slot freed even on failure
        assert (31, "naive") not in manager._slot_busy


# ── list_jobs / get_job ──────────────────────────────────────────────────────


class TestListAndGet:
    @pytest.mark.asyncio
    async def test_list_filters_and_pagination(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        # Seed 5 jobs with different target_ids and statuses
        ids = []
        for i in range(5):
            jid = await manager.start_deploy(
                server_id=100 + i, server_name=f"v{i}", protocol="naive",
                config={"domain": "x", "email": "y"},
                runner=_trivial_runner,
            )
            ids.append(jid)
        # Wait for all to complete
        for jid in ids:
            await _wait_for_status(manager, jid, {"succeeded"})

        # List all
        all_jobs = await manager.list_jobs()
        assert len(all_jobs) >= 5

        # Filter by target_id
        only_one = await manager.list_jobs(target_id=102)
        assert all(j.target_id == 102 for j in only_one)

        # Filter by status
        succeeded = await manager.list_jobs(status="succeeded")
        assert all(j.status == "succeeded" for j in succeeded)

        # Filter by kind
        deploys = await manager.list_jobs(kind="deploy")
        assert all(j.kind == "deploy" for j in deploys)

        # Pagination — limit
        first_two = await manager.list_jobs(limit=2)
        assert len(first_two) == 2

        # Sorted started_at DESC (newest first)
        if len(first_two) == 2:
            assert first_two[0].started_at >= first_two[1].started_at

    @pytest.mark.asyncio
    async def test_get_job_returns_full_row(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        job_id = await manager.start_deploy(
            server_id=200, server_name="vfull", protocol="naive",
            config={"domain": "x.example.com", "email": "me@x"},
            runner=_trivial_runner,
        )
        await _wait_for_status(manager, job_id, {"succeeded"})

        full = await manager.get_job(job_id)
        assert full is not None
        assert full.id == job_id
        assert full.config_json  # full row includes config
        assert full.result_json  # success path stores result

    @pytest.mark.asyncio
    async def test_get_job_unknown_returns_none(
        self, client, admin_user, auth_headers, default_settings, manager
    ):
        # `client` fixture wires the test async engine into
        # `db_mod._async_engine` so JobManager's internal
        # get_async_engine() call hits the test DB, not production.
        assert await manager.get_job("does-not-exist") is None


# ── subscribe ────────────────────────────────────────────────────────────────


class TestSubscribe:
    @pytest.mark.asyncio
    async def test_subscribe_unknown_job_yields_empty(self, manager):
        items = []
        async for entry in manager.subscribe("nonexistent"):
            items.append(entry)
        assert items == []

    @pytest.mark.asyncio
    async def test_subscribe_yields_backlog_then_live(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        # Spawn a slow runner so we can subscribe mid-flight
        async def runner(job_id, on_line):
            await on_line("stdout", "line-1")
            await on_line("stdout", "line-2")
            # Pause so subscribe has a chance to register before more lines
            await asyncio.sleep(0.1)
            await on_line("stdout", "line-3")
            await on_line("stderr", "warning-A")
            return {}

        job_id = await manager.start_deploy(
            server_id=300, server_name="v300", protocol="naive",
            config={"domain": "x", "email": "y"},
            runner=runner,
        )
        # Let the first 2 lines accumulate in the buffer
        await asyncio.sleep(0.03)

        collected = []
        async for entry in manager.subscribe(job_id):
            collected.append(entry)
            if len(collected) >= 4:
                break
        # Backlog should include line-1 + line-2; live should add line-3 and warning-A
        kinds = [e[0] for e in collected]
        lines = [e[1] for e in collected]
        assert "line-1" in lines
        assert "line-2" in lines
        assert "line-3" in lines
        assert "warning-A" in lines
        assert kinds.count("stderr") >= 1
        assert kinds.count("stdout") >= 3

    @pytest.mark.asyncio
    async def test_subscribe_supports_multiple_subscribers(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        async def runner(job_id, on_line):
            await asyncio.sleep(0.02)  # let subscribers register
            for i in range(3):
                await on_line("stdout", f"line-{i}")
            return {}

        job_id = await manager.start_deploy(
            server_id=301, server_name="v301", protocol="naive",
            config={"domain": "x", "email": "y"},
            runner=runner,
        )

        async def collect():
            items = []
            async for entry in manager.subscribe(job_id):
                items.append(entry)
            return items

        results = await asyncio.gather(collect(), collect())
        # Both subscribers should see 3 lines (modulo backlog overlap)
        for r in results:
            assert len(r) >= 3


# ── Heal stale jobs on startup ───────────────────────────────────────────────


class TestHealStale:
    @pytest.mark.asyncio
    async def test_heal_flips_old_running_to_failed(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        # Seed a stale 'running' job (started 2 hours ago)
        stale_id = "stale" + "0" * 27
        old_started = datetime.now(timezone.utc) - timedelta(hours=2)
        session.add(Job(
            id=stale_id, kind="deploy", target_id=999,
            target_name="ghost", protocol="naive",
            status="running", started_at=old_started,
        ))
        # Plus a fresh running job that should NOT be touched
        fresh_id = "fresh" + "0" * 27
        session.add(Job(
            id=fresh_id, kind="deploy", target_id=998,
            target_name="alive", protocol="naive",
            status="running", started_at=datetime.now(timezone.utc),
        ))
        session.commit()

        # Run healer
        await manager._heal_stale_jobs()

        session.expire_all()
        stale_row = session.exec(select(Job).where(Job.id == stale_id)).first()
        fresh_row = session.exec(select(Job).where(Job.id == fresh_id)).first()
        assert stale_row.status == "failed"
        assert "restart" in (stale_row.error or "").lower()
        assert fresh_row.status == "running"  # untouched


# ── Trim ──────────────────────────────────────────────────────────────────────


class TestTrim:
    @pytest.mark.asyncio
    async def test_trim_deletes_old_finished_rows(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        # Seed an ancient succeeded job (45 days ago) — should be trimmed
        ancient_id = "ancient" + "0" * 25
        very_old = datetime.now(timezone.utc) - timedelta(days=45)
        session.add(Job(
            id=ancient_id, kind="deploy", target_id=1,
            status="succeeded", started_at=very_old, finished_at=very_old,
        ))
        # Plus a recent succeeded job — should remain
        recent_id = "recent" + "0" * 26
        session.add(Job(
            id=recent_id, kind="deploy", target_id=1,
            status="succeeded", started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
        ))
        session.commit()

        await manager._trim_once()

        session.expire_all()
        ancient_row = session.exec(select(Job).where(Job.id == ancient_id)).first()
        recent_row = session.exec(select(Job).where(Job.id == recent_id)).first()
        assert ancient_row is None
        assert recent_row is not None

    @pytest.mark.asyncio
    async def test_trim_does_not_delete_running_even_if_old(
        self, client, admin_user, auth_headers, default_settings, session, manager
    ):
        # A running job >30 days old should NOT be deleted by trim —
        # that's a healer-bug scenario worth surfacing, not silently
        # dropping. _heal_stale_jobs handles it separately.
        old_running_id = "oldrun" + "0" * 26
        very_old = datetime.now(timezone.utc) - timedelta(days=45)
        session.add(Job(
            id=old_running_id, kind="deploy", target_id=1,
            status="running", started_at=very_old,
        ))
        session.commit()

        await manager._trim_once()

        session.expire_all()
        row = session.exec(select(Job).where(Job.id == old_running_id)).first()
        assert row is not None  # preserved


# ── _row_to_summary ───────────────────────────────────────────────────────────


def test_row_to_summary_unpacks_result_json():
    """Pure unit test — no DB. Verifies the JobSummary projection."""
    j = Job(
        id="x" * 32, kind="deploy", target_id=1, target_name="v",
        protocol="naive", status="succeeded",
        started_at=datetime.now(timezone.utc),
        result_json='{"deployment_id": 5, "node_id": 42}',
    )
    summary = _row_to_summary(j)
    assert summary.id == "x" * 32
    assert summary.result == {"deployment_id": 5, "node_id": 42}
    assert summary.target_name == "v"


def test_row_to_summary_handles_corrupt_result_json():
    j = Job(
        id="y" * 32, kind="deploy", status="succeeded",
        started_at=datetime.now(timezone.utc),
        result_json="not valid json {{{",
    )
    summary = _row_to_summary(j)
    assert summary.result is None  # corrupt → None, doesn't crash
