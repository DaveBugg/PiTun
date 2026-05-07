"""Server-tasks subsystem (since v1.3.0).

A `Job` represents a long-running async operation triggered from the
UI (currently: SSH-driven proxy auto-deploy on a registered Server).
The manager persists metadata to DB, holds a RAM-only live log
buffer for in-flight jobs, supports multiple WS subscribers per job,
and offers per-(target, protocol) slot locks so clicking "Install"
twice on the same Server doesn't kick off two parallel installs.

Hybrid persistence:
  * DB row (`models.Job`) — kind, target_id, status, started/finished,
    error, result, log_tail. Survives restart, queryable for the
    `/server-tasks` UI.
  * RAM (`_buffers`, `_subscribers`, `_tasks`, `_slot_locks`) —
    in-flight log lines, open WS connections, asyncio.Task handles
    for cancel, slot mutexes. Lost on restart; that's fine for log
    backlog (the script keeps running on the remote VPS regardless,
    and the eventual ServerDeployment row update still happens).

Stale-running healer: on backend startup, any DB job in `running`
status whose `started_at` is older than ~1 h is re-flagged `failed`
("backend restarted during deploy"). Without this, a restart in the
middle of a 5-min deploy leaves the row stuck at `running` forever
even though no async task exists to drive it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Awaitable, Callable, Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine
from app.models import Job

logger = logging.getLogger(__name__)


# ── Tunables (kept module-level for monkeypatching from tests) ───────────────


# Live log buffer cap per running job. Realistic naive deploy emits
# ~500-1000 lines (apt + caddy + certbot + naive). 2000-line cap leaves
# headroom; older lines fall off the deque tail. Ring buffer = bounded
# memory footprint (~2 MB max per job at typical line lengths).
_BUFFER_LINES = 2000

# Tail captured to `Job.log_tail` at finalization. ~4 KB → enough to
# show "what was on screen when it ended" without bloating the DB.
_LOG_TAIL_BYTES = 4 * 1024

# After a job transitions to a terminal status, keep the RAM buffer +
# subscribers alive for this many seconds so any open WS clients
# still have time to drain the backlog and see the `done` event before
# the buffer is freed.
_DRAIN_GRACE_SEC = 30.0

# Stale-job healer: any `running` job whose `started_at` is older than
# this on backend startup is flagged as a restart casualty.
_STALE_RUNNING_THRESHOLD = timedelta(hours=1)

# Trim policy: delete the oldest jobs once we exceed this count, AND
# delete any jobs older than `_TRIM_MAX_AGE` regardless of count. Both
# applied each pass.
_TRIM_MAX_COUNT = 500
_TRIM_MAX_AGE = timedelta(days=30)
_TRIM_INTERVAL_SEC = 3600.0  # once an hour


# ── Public types ──────────────────────────────────────────────────────────────


@dataclass
class JobSummary:
    """Lightweight read shape for `GET /server-tasks` responses.
    Mirrors the DB row but without the heavy fields (config_json,
    log_tail) so list calls stay cheap.
    """
    id: str
    kind: str
    target_id: Optional[int]
    target_name: Optional[str]
    protocol: Optional[str]
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    error: Optional[str]
    # Result is small (a few hundred bytes for deploy) — include in
    # list view so the "Open Node →" button has node_id without
    # a follow-up GET.
    result: Optional[dict]


class SlotBusy(Exception):
    """Raised by `start_deploy` when the (target_id, protocol) slot
    is already running. Caller (api/servers.py) translates to HTTP 409.
    """


# ── JobManager ────────────────────────────────────────────────────────────────


class JobManager:
    """Process-local registry — single instance bound to the FastAPI
    app's lifespan. Not safe across multiple backend processes (we run
    a single uvicorn worker; if that ever changes, this whole thing
    needs to move to Redis or similar).
    """

    def __init__(self) -> None:
        # job_id → asyncio.Task running the deploy coroutine
        self._tasks: dict[str, asyncio.Task] = {}
        # job_id → bounded deque of (kind, line) tuples; kind ∈
        # {"stdout", "stderr"}. Created on start, freed on drain
        # grace period after finalization.
        self._buffers: dict[str, deque[tuple[str, str]]] = {}
        # job_id → list of asyncio.Queue subscribers (multiple WS
        # clients per job). Each queue receives (kind, line) tuples
        # plus a sentinel `None` on finalization.
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        # (target_id, protocol) → bool — set if a job is currently
        # holding the slot. We use a dict not asyncio.Lock because
        # we only want to fail-fast (raise SlotBusy) not block-wait.
        self._slot_busy: dict[tuple[int, str], bool] = {}
        # Background task handle for the trim loop. None until start().
        self._trim_task: Optional[asyncio.Task] = None
        self._stop_evt = asyncio.Event()

    # ── Lifecycle ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Boot-time hook: heal stale running jobs, kick off trim loop."""
        await self._heal_stale_jobs()
        self._stop_evt = asyncio.Event()
        self._trim_task = asyncio.create_task(self._trim_loop())

    async def stop(self) -> None:
        """Shutdown hook: stop the trim loop. Running deploy tasks
        are NOT cancelled — they finalize on their own (or the
        outgoing process tears them down)."""
        self._stop_evt.set()
        if self._trim_task and not self._trim_task.done():
            self._trim_task.cancel()
            try:
                await self._trim_task
            except (asyncio.CancelledError, Exception):
                pass

    async def _heal_stale_jobs(self) -> None:
        """On startup, flip any `running` rows that are too old to
        `failed` — these are leftover from a backend that died mid-deploy.
        """
        cutoff = datetime.now(timezone.utc) - _STALE_RUNNING_THRESHOLD
        async with AsyncSession(get_async_engine()) as session:
            stale = (await session.exec(
                select(Job)
                .where(Job.status == "running")
                .where(Job.started_at < cutoff)
            )).all()
            if not stale:
                return
            for j in stale:
                j.status = "failed"
                j.finished_at = datetime.now(timezone.utc)
                j.error = "backend restarted during deploy (stale running job)"
                session.add(j)
            await session.commit()
            logger.warning(
                "JobManager: healed %d stale running job(s) on boot", len(stale),
            )

    async def _trim_loop(self) -> None:
        """Background task: every hour, prune Job rows by age and count."""
        while not self._stop_evt.is_set():
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=_TRIM_INTERVAL_SEC)
                # Stop signal received
                return
            except asyncio.TimeoutError:
                pass  # normal interval tick — proceed to trim
            try:
                await self._trim_once()
            except Exception as exc:  # noqa: BLE001
                logger.warning("JobManager trim pass failed (non-fatal): %s", exc)

    async def _trim_once(self) -> None:
        """One pass of the retention policy: by age, then by count."""
        cutoff = datetime.now(timezone.utc) - _TRIM_MAX_AGE
        async with AsyncSession(get_async_engine()) as session:
            old = (await session.exec(
                select(Job).where(Job.started_at < cutoff)
            )).all()
            # Snapshot the deletable count BEFORE commit — post-commit
            # attribute access on `j.status` would lazy-load via the
            # closed session and trip MissingGreenlet (same pattern as
            # v1.2.7 self-heal fix).
            deletable_count = sum(1 for j in old if j.status != "running")
            for j in old:
                # Don't delete a still-running row even if it's >30d
                # — that's a healer bug and worth surfacing, not silently
                # dropping. Leave it alone.
                if j.status == "running":
                    continue
                await session.delete(j)
            if deletable_count:
                await session.commit()
                logger.info("JobManager: trimmed %d job(s) older than %s",
                            deletable_count, _TRIM_MAX_AGE)

            # Count-based trim: keep newest _TRIM_MAX_COUNT.
            total = len((await session.exec(select(Job.id))).all())
            if total > _TRIM_MAX_COUNT:
                excess = total - _TRIM_MAX_COUNT
                # Oldest finished jobs first (running rows excluded)
                victims = (await session.exec(
                    select(Job)
                    .where(Job.status != "running")
                    .order_by(Job.started_at)
                    .limit(excess)
                )).all()
                victims_count = len(victims)
                for j in victims:
                    await session.delete(j)
                if victims_count:
                    await session.commit()
                    logger.info("JobManager: trimmed %d job(s) over count cap %d",
                                victims_count, _TRIM_MAX_COUNT)

    # ── Public API ───────────────────────────────────────────────────────────

    async def start_deploy(
        self,
        *,
        server_id: int,
        server_name: str,
        protocol: str,
        config: dict,
        runner: Callable[[str, Callable[[str, str], Awaitable[None]]], Awaitable[dict]],
    ) -> str:
        """Spawn a deploy job. Returns the new job_id (uuid hex).

        `runner` is a coroutine that does the actual SSH work; it
        accepts the new job_id and an `on_line(kind, line)` callback,
        and returns a final result dict. Decoupling the runner keeps
        JobManager protocol-agnostic — `api/servers.py` builds a
        runner that wraps `core.ssh.exec_remote_script_streaming`
        + URI parse + Node creation + ServerDeployment upsert.

        Raises `SlotBusy` if `(server_id, protocol)` already has a
        running deploy. Caller maps to HTTP 409.
        """
        slot = (server_id, protocol)
        if self._slot_busy.get(slot):
            raise SlotBusy(
                f"Deploy already running on server_id={server_id} protocol={protocol!r}"
            )

        job_id = secrets.token_hex(16)  # 32 hex chars
        now = datetime.now(timezone.utc)

        # Persist the row BEFORE spawning the task — an early task
        # failure must still leave a discoverable record. Status
        # starts as `running`; the task updates to terminal at end.
        async with AsyncSession(get_async_engine()) as session:
            session.add(Job(
                id=job_id,
                kind="deploy",
                target_id=server_id,
                target_name=server_name,
                protocol=protocol,
                status="running",
                config_json=json.dumps(config),
                started_at=now,
            ))
            await session.commit()

        # Mark slot busy + initialise RAM buffers
        self._slot_busy[slot] = True
        self._buffers[job_id] = deque(maxlen=_BUFFER_LINES)
        self._subscribers[job_id] = []

        # Spawn the worker task
        self._tasks[job_id] = asyncio.create_task(
            self._run(job_id, slot, runner)
        )
        return job_id

    async def _run(
        self,
        job_id: str,
        slot: tuple[int, str],
        runner: Callable[[str, Callable[[str, str], Awaitable[None]]], Awaitable[dict]],
    ) -> None:
        """Outer wrapper around the user-supplied runner. Handles
        finalization, error capture, slot release, buffer drain.
        """
        async def on_line(kind: str, line: str) -> None:
            await self._append_line(job_id, kind, line)

        result: Optional[dict] = None
        error: Optional[str] = None
        status = "succeeded"

        try:
            result = await runner(job_id, on_line)
        except asyncio.CancelledError:
            status = "cancelled"
            error = "Cancelled by operator"
            # Don't re-raise — we want the finalize block to run.
            # Note: the script on the remote VPS keeps running; we
            # only abandoned the local task that was pumping its
            # output into our buffer.
        except Exception as exc:  # noqa: BLE001
            status = "failed"
            error = str(exc).strip() or exc.__class__.__name__
            logger.exception("Job %s crashed: %s", job_id, error)

        await self._finalize(job_id, slot, status, error, result)

    async def _append_line(self, job_id: str, kind: str, line: str) -> None:
        """Push a (kind, line) tuple into the buffer + fan out to subscribers."""
        buf = self._buffers.get(job_id)
        if buf is None:
            return
        buf.append((kind, line))
        # Fan out to subscribers — non-blocking; if a queue is full
        # the slow subscriber drops the line. We don't bother with
        # backpressure for log streaming.
        subs = self._subscribers.get(job_id, [])
        for q in list(subs):
            try:
                q.put_nowait((kind, line))
            except asyncio.QueueFull:
                pass

    async def _finalize(
        self,
        job_id: str,
        slot: tuple[int, str],
        status: str,
        error: Optional[str],
        result: Optional[dict],
    ) -> None:
        """Persist terminal state, capture log_tail, signal subscribers,
        free the slot, schedule buffer drain.
        """
        # Build the log_tail from current buffer (last _LOG_TAIL_BYTES
        # of combined stdout+stderr lines).
        buf = self._buffers.get(job_id, deque())
        combined = "\n".join(line for _kind, line in buf)
        if len(combined) > _LOG_TAIL_BYTES:
            combined = combined[-_LOG_TAIL_BYTES:]

        finished_at = datetime.now(timezone.utc)

        async with AsyncSession(get_async_engine()) as session:
            j = await session.get(Job, job_id)
            if j is not None:
                j.status = status
                j.finished_at = finished_at
                j.error = error
                j.result_json = json.dumps(result) if result else None
                j.log_tail = combined or None
                session.add(j)
                await session.commit()

        # Signal subscribers with a sentinel — they treat None as
        # "no more lines, emit done event, close".
        subs = self._subscribers.get(job_id, [])
        for q in list(subs):
            try:
                q.put_nowait(None)
            except asyncio.QueueFull:
                pass

        # Free slot immediately so a retry can start
        self._slot_busy.pop(slot, None)
        self._tasks.pop(job_id, None)

        # Buffer + subscriber dict freed after grace period — gives
        # any open WS clients time to drain backlog + done event.
        async def _drain():
            await asyncio.sleep(_DRAIN_GRACE_SEC)
            self._buffers.pop(job_id, None)
            self._subscribers.pop(job_id, None)
        asyncio.create_task(_drain())

    # ── Read API ─────────────────────────────────────────────────────────────

    async def list_jobs(
        self,
        *,
        target_id: Optional[int] = None,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[JobSummary]:
        """Paginated list with optional filters. Sorted started_at DESC."""
        # Caller-provided limits guarded
        limit = max(1, min(limit, 200))
        offset = max(0, offset)

        async with AsyncSession(get_async_engine()) as session:
            stmt = select(Job)
            if target_id is not None:
                stmt = stmt.where(Job.target_id == target_id)
            if kind is not None:
                stmt = stmt.where(Job.kind == kind)
            if status is not None:
                stmt = stmt.where(Job.status == status)
            stmt = stmt.order_by(Job.started_at.desc()).offset(offset).limit(limit)
            rows = (await session.exec(stmt)).all()
            return [_row_to_summary(r) for r in rows]

    async def get_job(self, job_id: str) -> Optional[Job]:
        """Single job, full row including log_tail / config_json."""
        async with AsyncSession(get_async_engine()) as session:
            return await session.get(Job, job_id)

    async def cancel(self, job_id: str) -> bool:
        """Cancel a running job. No-op if already terminal. Returns
        True if the cancel was issued, False if job didn't exist or
        was already done.

        Important: this `task.cancel()`s the LOCAL coroutine. The
        remote SSH process keeps running on the VPS — we don't have a
        portable "kill remote" primitive in asyncssh, and even if we
        did, killing a half-installed Caddy / certbot leaves the VPS
        in a worse state than letting the script complete. The
        ServerDeployment row WILL still get its terminal update if
        the script finishes after we cancel; we just won't be
        listening locally.
        """
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    async def subscribe(
        self, job_id: str
    ) -> AsyncIterator[Optional[tuple[str, str]]]:
        """Yield (kind, line) tuples — first the buffered backlog,
        then live updates as they arrive. A `None` value signals
        finalization; the caller (WS handler) emits the `done`
        event and closes after seeing it.

        On unknown / already-fully-drained job_id: yields nothing
        (empty iterator). Caller gets immediate StopIteration.
        """
        buf = self._buffers.get(job_id)
        if buf is None:
            # Either never existed or already drained past grace period.
            return

        # Snapshot backlog, register as subscriber, then drain queue.
        backlog = list(buf)
        q: asyncio.Queue = asyncio.Queue(maxsize=4096)
        subs = self._subscribers.setdefault(job_id, [])
        subs.append(q)

        try:
            for entry in backlog:
                yield entry
            while True:
                item = await q.get()
                if item is None:
                    # Sentinel: finalization happened, drain remaining
                    # queued items (if any) then stop.
                    while not q.empty():
                        rest = q.get_nowait()
                        if rest is None:
                            break
                        yield rest
                    return
                yield item
        finally:
            # Always remove ourselves from the fan-out list
            try:
                subs.remove(q)
            except ValueError:
                pass


def _row_to_summary(r: Job) -> JobSummary:
    result: Optional[dict] = None
    if r.result_json:
        try:
            result = json.loads(r.result_json)
        except Exception:
            result = None
    return JobSummary(
        id=r.id,
        kind=r.kind,
        target_id=r.target_id,
        target_name=r.target_name,
        protocol=r.protocol,
        status=r.status,
        started_at=r.started_at,
        finished_at=r.finished_at,
        error=r.error,
        result=result,
    )


# Module-level singleton. Started in `app/main.py:lifespan`.
job_manager = JobManager()
