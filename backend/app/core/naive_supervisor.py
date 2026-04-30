"""
Naive sidecar supervisor.

Listens to Docker events for `label=pitun=naive` containers in a worker
thread, and on `die` pushes a restart request onto an asyncio queue. The
event loop task drains the queue and calls `naive_manager.start_node()`
to bring the sidecar back up.

Why this exists
---------------
Without a supervisor, a crashed naive container is only noticed at the
next HealthChecker tick (~30 s). For NodeCircle or manually-activated
naive nodes this means 30 s of no proxy. Docker events arrive in tens of
milliseconds, so reactive restart is ~2 orders of magnitude faster.

Why not docker's own `restart: unless-stopped`
----------------------------------------------
The sidecar *does* have `restart_policy = unless-stopped`, so Docker will
retry on its own. But Docker's restart policy doesn't:
  - re-read our `/etc/pitun/naive/<id>.json` (which may have been mutated
    by the user via the API while the container was dying),
  - call back into the backend to update node state,
  - respect PiTun's "node disabled" semantics — a disabled node must stay
    down even if Docker happily restarts it.

Rate limiting
-------------
If a node dies more than `MAX_RESTARTS_PER_WINDOW` times inside
`RESTART_WINDOW_SEC`, the supervisor logs a warning and stops restarting
that node. The user can manually restart via the API to reset the counter.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Optional

from app.core.naive_manager import naive_manager

logger = logging.getLogger(__name__)

# Public knobs (kept module-level so tests can monkeypatch easily)
RESTART_WINDOW_SEC = 60          # measuring window
MAX_RESTARTS_PER_WINDOW = 5      # after this, stop retrying
RECONNECT_BACKOFF_SEC = 5        # sleep after event stream error

_LABEL_KEY = "pitun"
_LABEL_VAL = "naive"
_NODE_ID_LABEL = "pitun_node_id"


class NaiveSupervisor:
    """Docker events → restart orchestrator for naive sidecars."""

    # Don't surface sidecar restart events during the first N seconds after
    # the supervisor starts: container reboots / backend deploys generate
    # a flurry of "died" events that aren't actually crashes the user
    # cares about, just churn from `naive_manager.sync_all` reconciling
    # state on boot.
    _BOOT_GRACE_SEC = 30

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop_evt: Optional[asyncio.Event] = None
        self._thread: Optional[threading.Thread] = None
        self._thread_stop = threading.Event()
        self._started_at: Optional[float] = None
        # node_id → deque[timestamp] of recent restart attempts
        self._restart_history: Dict[int, Deque[float]] = {}
        # Dropped-event counters for observability. `dropped_events` is
        # incremented each time the asyncio queue bridge rejects an event
        # (full/slow consumer). Shown on debug endpoint; if this ticks up
        # during normal operation, it means the supervisor is falling behind
        # and events are being lost.
        self._dropped_events = 0

    # ── Public lifecycle ─────────────────────────────────────────────────────

    def start(self) -> None:
        """Non-blocking: spawns the event-loop task."""
        if self._task and not self._task.done():
            return
        loop = asyncio.get_event_loop()
        self._stop_evt = asyncio.Event()
        self._started_at = time.monotonic()
        self._task = loop.create_task(self._run(), name="naive-supervisor")
        logger.info("NaiveSupervisor started")

    def stop(self) -> None:
        """Signal the event loop task to exit. Safe to call multiple times.
        Blocks the worker thread via its own event; does NOT join (shutdown
        should complete quickly and we don't want to hang on a slow daemon)."""
        if self._stop_evt:
            self._stop_evt.set()
        self._thread_stop.set()
        if self._task and not self._task.done():
            self._task.cancel()
        logger.info("NaiveSupervisor stopping")

    # ── Event loop ──────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Outer loop: keep the events stream alive across docker-daemon
        hiccups (socket hangups, restarts, etc.)."""
        assert self._stop_evt is not None
        while not self._stop_evt.is_set():
            try:
                await self._stream_events_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning("NaiveSupervisor events stream failed: %s", exc)
            # Back off before reconnecting. Allow cancellation during sleep.
            try:
                await asyncio.wait_for(self._stop_evt.wait(), timeout=RECONNECT_BACKOFF_SEC)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                raise

    async def _stream_events_once(self) -> None:
        """One full lifetime of the docker events stream. Returns when the
        stream ends / errors — the outer _run loop will reconnect."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        # Reset thread stop so a restart of this method after a reconnect
        # gets a fresh thread.
        self._thread_stop = threading.Event()

        def _worker() -> None:
            """Blocking iterator in a worker thread. Events pushed to the
            asyncio queue via run_coroutine_threadsafe."""
            try:
                client = naive_manager._get_client()
                # docker-py filters: only container die events with our label
                stream = client.events(
                    decode=True,
                    filters={
                        "label": f"{_LABEL_KEY}={_LABEL_VAL}",
                        "type": "container",
                        "event": "die",
                    },
                )
                for ev in stream:
                    if self._thread_stop.is_set():
                        break
                    try:
                        fut = asyncio.run_coroutine_threadsafe(queue.put(ev), loop)
                        # Don't block for long; drop event if queue is jammed
                        fut.result(timeout=2.0)
                    except Exception:
                        # Atomic-enough increment — we're in a worker thread
                        # but a stale read on the health endpoint is fine.
                        self._dropped_events += 1
                        logger.debug(
                            "NaiveSupervisor: dropped event (total=%d, queue full?)",
                            self._dropped_events,
                        )
            except Exception as exc:
                logger.debug("NaiveSupervisor worker thread exiting: %s", exc)

        self._thread = threading.Thread(
            target=_worker, name="naive-supervisor-docker", daemon=True,
        )
        self._thread.start()

        # Drain queue until stop is signalled or the worker thread dies.
        assert self._stop_evt is not None
        while not self._stop_evt.is_set():
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                if not self._thread.is_alive():
                    # Worker died: surface to outer reconnect loop
                    raise RuntimeError("events stream thread exited")
                continue
            await self._handle_event(ev)

        # Cleanup: tell worker to stop; we don't join (could be blocked on
        # a slow daemon read).
        self._thread_stop.set()

    # ── Event handling ──────────────────────────────────────────────────────

    async def _handle_event(self, event: Dict[str, Any]) -> None:
        """Dispatch a single docker event to the restart logic."""
        # We filtered on type=container&event=die so these fields should be
        # present — but guard against malformed events just in case.
        actor = event.get("Actor") or {}
        attrs = actor.get("Attributes") or {}
        label = attrs.get(_NODE_ID_LABEL)
        if not label:
            return
        try:
            node_id = int(label)
        except ValueError:
            logger.debug("NaiveSupervisor: non-integer node_id label %r", label)
            return

        exit_code = attrs.get("exitCode", "?")
        name = attrs.get("name", f"node-{node_id}")
        logger.info(
            "Naive sidecar for node %d died (name=%s exit=%s) — attempting restart",
            node_id, name, exit_code,
        )

        if not self._should_restart(node_id):
            logger.warning(
                "Naive node %d restarted too many times in %ds — giving up until manual intervention",
                node_id, RESTART_WINDOW_SEC,
            )
            in_boot_window = (
                self._started_at is not None
                and (time.monotonic() - self._started_at) < self._BOOT_GRACE_SEC
            )
            if not in_boot_window:
                from app.core.events import record_event
                await record_event(
                    category="sidecar.gave_up",
                    severity="error",
                    title=f"Sidecar gave up: node #{node_id}",
                    details=(
                        f"Naive sidecar (container={name!r}) crashed too many times in "
                        f"{RESTART_WINDOW_SEC}s; supervisor stopped retrying. "
                        "Restart manually from the node card once the upstream issue is resolved."
                    ),
                    entity_id=node_id,
                    # Don't re-emit if user keeps the broken node enabled — once
                    # per hour is plenty.
                    dedup_window_sec=3600,
                )
            return

        await self._restart_node(node_id)

    def _should_restart(self, node_id: int) -> bool:
        """Sliding-window rate limiter. Returns False if this node has
        hit its cap. Also records the current timestamp on approval."""
        now = time.monotonic()
        history = self._restart_history.setdefault(node_id, deque())
        # Drop entries outside the window
        cutoff = now - RESTART_WINDOW_SEC
        while history and history[0] < cutoff:
            history.popleft()
        if len(history) >= MAX_RESTARTS_PER_WINDOW:
            return False
        history.append(now)
        return True

    async def _restart_node(self, node_id: int) -> None:
        """Fetch the node from the DB and call naive_manager.start_node().
        Respects 'disabled' state: a disabled node is left down.
        """
        from app.database import get_async_engine
        from app.models import Node
        from sqlmodel.ext.asyncio.session import AsyncSession

        try:
            async with AsyncSession(get_async_engine()) as session:
                node = await session.get(Node, node_id)
            if node is None:
                logger.info("Node %d no longer exists — not restarting", node_id)
                return
            if not node.enabled:
                logger.info("Node %d is disabled — leaving container down", node_id)
                return
            if node.protocol != "naive":
                logger.info(
                    "Node %d protocol is %r, not naive — skipping restart",
                    node_id, node.protocol,
                )
                return
            await naive_manager.start_node(node)
            logger.info("Naive sidecar for node %d restarted", node_id)
            # Skip the event during the boot grace window — backend
            # restarts trigger a churn of die events from sync_all that
            # aren't real crashes worth alerting on.
            in_boot_window = (
                self._started_at is not None
                and (time.monotonic() - self._started_at) < self._BOOT_GRACE_SEC
            )
            if not in_boot_window:
                from app.core.events import record_event
                # 5-minute dedup so a node that flaps doesn't spam the feed —
                # the give-up event still fires when the rate limiter trips,
                # so persistent failures aren't hidden.
                await record_event(
                    category="sidecar.restarted",
                    severity="info",
                    title=f"Sidecar restarted: '{node.name}'",
                    details=f"Naive sidecar container for node #{node_id} crashed and was auto-restarted",
                    entity_id=node_id,
                    dedup_window_sec=300,
                )
        except Exception as exc:
            logger.warning("NaiveSupervisor: restart of node %d failed: %s", node_id, exc)


# Singleton used by app lifespan
naive_supervisor = NaiveSupervisor()
