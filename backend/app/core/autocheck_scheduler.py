"""Background auto-speedtest sweep.

Keeps `node.speed_mbps` / `speed_tested_at` fresh so the NodeCircle
`best` / `min_speed_mbps` filters and the UI staleness colour work without
the operator testing each node by hand.

One global config (`AutoCheckConfig`, row id=1). Speed only — HealthChecker
already covers online-ness. Sequential (a speedtest saturates the uplink),
with a per-node staleness guard so a node measured within the interval is
skipped. Errors are isolated per node — one bad node never aborts the sweep.

See docs/fork-analysis-romuss-pitunya.md #4.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine
from app.models import AutoCheckConfig, Node

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 60  # scheduler wakeup cadence (seconds)


async def get_or_create_config(session: AsyncSession) -> AutoCheckConfig:
    """Fetch the singleton config row (id=1), creating a disabled default."""
    cfg = await session.get(AutoCheckConfig, 1)
    if cfg is None:
        cfg = AutoCheckConfig(id=1)
        session.add(cfg)
        await session.commit()
        await session.refresh(cfg)
    return cfg


async def resolve_scope_node_ids(session: AsyncSession, kind: str, value: str) -> List[int]:
    """Enabled node ids matching the scope. Unknown/malformed scope → []."""
    stmt = select(Node).where(Node.enabled == True)  # noqa: E712
    if kind == "subscription":
        try:
            stmt = stmt.where(Node.subscription_id == int(value))
        except (ValueError, TypeError):
            return []
    elif kind == "group":
        stmt = stmt.where(Node.group == value)
    elif kind == "nodes":
        try:
            ids = [int(x) for x in json.loads(value or "[]")]
        except (ValueError, TypeError):
            return []
        if not ids:
            return []
        stmt = stmt.where(Node.id.in_(ids))  # type: ignore[attr-defined]
    # "all" or anything unrecognised → every enabled node.
    # Newest nodes first (highest id → down to 1): a fresh import gets
    # measured soonest, and the oldest/most-established nodes last.
    stmt = stmt.order_by(Node.id.desc())  # type: ignore[attr-defined]
    nodes = (await session.exec(stmt)).all()
    return [n.id for n in nodes if n.id is not None]


def _as_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class AutoCheckScheduler:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._sweeping = False  # guard against overlapping sweeps

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    @property
    def is_sweeping(self) -> bool:
        return self._sweeping

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001 — loop must survive a bad tick
                logger.warning("AutoCheck tick error: %s", exc)
            await asyncio.sleep(_CHECK_INTERVAL)

    async def _tick(self) -> None:
        async with AsyncSession(get_async_engine()) as session:
            cfg = await get_or_create_config(session)
            if not cfg.enabled or self._sweeping:
                return
            now = datetime.now(timezone.utc)
            interval = timedelta(minutes=max(1, cfg.interval_minutes))
            if cfg.last_sweep is not None and now < _as_utc(cfg.last_sweep) + interval:
                return  # not due yet
        await self.run_sweep()

    async def run_sweep(
        self, scope_kind: Optional[str] = None, scope_value: Optional[str] = None,
        force: bool = False,
    ) -> dict:
        """Speed-test every scoped node (sequential, staleness-guarded).
        Returns a summary dict. Safe to call directly (API 'run now').

        `scope_kind`/`scope_value` override the saved config scope for this
        run — the Nodes "Speed All" button passes `"all"` so it always sweeps
        every node regardless of the auto-check config. The scheduler passes
        None to use the saved scope. Either way we stamp `last_sweep`, so a
        manual run pushes the next scheduled sweep out by one interval and the
        two never collide."""
        if self._sweeping:
            return {"status": "already_running"}
        self._sweeping = True
        started = datetime.now(timezone.utc)
        tested = skipped = failed = 0
        try:
            async with AsyncSession(get_async_engine()) as session:
                cfg = await get_or_create_config(session)
                # Read the fields we need BEFORE committing — `await commit()`
                # expires the ORM attributes, and touching them afterwards would
                # trigger a sync lazy-load on the async connection (MissingGreenlet).
                eff_kind = scope_kind if scope_kind is not None else cfg.scope_kind
                eff_value = scope_value if scope_value is not None else cfg.scope_value
                interval = timedelta(minutes=max(1, cfg.interval_minutes))
                # Stamp the sweep start up front so a concurrent tick sees
                # "not due" and we never double-schedule.
                cfg.last_sweep = started
                session.add(cfg)
                await session.commit()
                node_ids = await resolve_scope_node_ids(session, eff_kind, eff_value)

            from app.core.speedtest import apply_exit, speedtest_node as _speedtest
            for nid in node_ids:
                async with AsyncSession(get_async_engine()) as session:
                    node = await session.get(Node, nid)
                    if not node or not node.enabled:
                        continue
                    # Staleness guard: skip a node measured within the interval
                    # (e.g. tested manually a moment ago). `force` (manual
                    # "Speed All") bypasses it so every node is re-tested.
                    if not force and node.speed_tested_at is not None and \
                            datetime.now(timezone.utc) < _as_utc(node.speed_tested_at) + interval:
                        skipped += 1
                        continue
                    mbps = mx = None
                    try:
                        result = await _speedtest(node)
                        mbps = result.get("download_mbps")
                        mx = result.get("max_mbps")
                        # The sweep is the only check most nodes ever get, so
                        # it is what keeps their flag honest — an exit that
                        # moved country is picked up here without anyone
                        # pressing anything.
                        apply_exit(node, result)
                    except Exception as exc:  # noqa: BLE001 — isolate per node
                        logger.info("AutoCheck: node %d speedtest error: %s", nid, exc)
                    # Stamp the check time either way. On failure we clear the
                    # reading but keep the timestamp, so the UI shows a "no speed"
                    # badge (with age) instead of a blank row — a node that
                    # couldn't be measured should say so, not look untested.
                    # `force` (manual Speed All) bypasses the staleness guard
                    # above, so a failed node is always retried by hand.
                    if mbps:
                        node.speed_mbps = float(mbps)
                        node.speed_max_mbps = float(mx) if mx is not None else None
                        tested += 1
                    else:
                        node.speed_mbps = None
                        node.speed_max_mbps = None
                        failed += 1
                    node.speed_tested_at = datetime.now(timezone.utc)
                    session.add(node)
                    await session.commit()

            logger.info(
                "AutoCheck sweep done: tested=%d skipped=%d failed=%d (scope=%d)",
                tested, skipped, failed, len(node_ids),
            )
            return {
                "status": "done",
                "tested": tested,
                "skipped": skipped,
                "failed": failed,
                "total": len(node_ids),
            }
        finally:
            self._sweeping = False


autocheck_scheduler = AutoCheckScheduler()
