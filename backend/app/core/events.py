"""Recent Events recorder.

Centralized helper for inserting `Event` rows from background loops.
Surfaced in the UI by the Dashboard "Recent Events" card.

Design rules:
- `record_event(...)` is fire-and-forget — it owns its own session,
  swallows all exceptions, and never raises. Callers don't have to
  wrap it in try/except or pass a session in. This matches
  `dns_logger.process_log_line` so the various background loops
  (HealthChecker, NaiveSupervisor, CircleScheduler, GeoScheduler,
  subscription updater) need exactly one extra line each.
- Optional `dedup_window_sec` skips writes if the most recent event
  with the same `(category, entity_id)` is younger than the window.
  Used by chronically-failing sources (subscription.failed retry
  storms, sidecar.gave_up after-supervisor) so the feed doesn't
  drown out other events.
- Trim policy mirrors `dns_logger`: age cutoff + hard cap, single
  background task started in lifespan. Constants are module-level
  here, not Settings table — phase 1 keeps the DB clean. Promote
  later if a user actually wants to tune them.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import select, func, delete, and_
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine

log = logging.getLogger("pitun.events")

# Retention: 7 days OR 1000 rows, whichever is smaller. The Recent Events
# feed is meant to surface "what changed lately?" — older history can be
# rebuilt from container logs if anyone really needs it.
EVENTS_MAX_AGE_HOURS = 168          # 7 days
EVENTS_HARD_CAP = 1000
EVENTS_TRIM_INTERVAL_SEC = 600      # 10 min


# ── Recording ────────────────────────────────────────────────────────────────


async def record_event(
    *,
    category: str,
    severity: str,
    title: str,
    details: Optional[str] = None,
    entity_id: Optional[int] = None,
    dedup_window_sec: Optional[int] = None,
) -> None:
    """Insert one Event row. Never raises.

    `category`: dotted free-text code, e.g. "failover.switched". Frontend
    maps this to a localized label + icon.
    `severity`: "info" | "warning" | "error".
    `title`/`details`: ASCII English. Free-form details (e.g. an exception
    message) are shown verbatim in the UI.
    `entity_id`: optional id of the related Node / NodeCircle / Subscription.
    `dedup_window_sec`: if set, skip the insert when the latest event with
    the same (category, entity_id) is younger than the window. Useful for
    failure events that retry on a tight schedule.
    """
    from app.models import Event

    try:
        async with AsyncSession(get_async_engine()) as session:
            if dedup_window_sec is not None:
                cutoff = datetime.now(tz=timezone.utc) - timedelta(seconds=dedup_window_sec)
                # Compose the WHERE in a way that's correct for both
                # entity_id=None (compare to NULL) and entity_id=int.
                clauses = [Event.category == category, Event.timestamp >= cutoff]
                if entity_id is None:
                    clauses.append(Event.entity_id.is_(None))
                else:
                    clauses.append(Event.entity_id == entity_id)
                latest = (await session.exec(
                    select(Event).where(and_(*clauses)).limit(1)
                )).first()
                if latest is not None:
                    return  # within dedup window — skip silently

            session.add(Event(
                category=category,
                severity=severity,
                title=title,
                details=details,
                entity_id=entity_id,
            ))
            await session.commit()
    except Exception as exc:
        log.warning("event record failed (category=%s): %s", category, exc)


def fire_and_forget(coro) -> None:
    """Schedule `record_event(...)` from a sync caller (or anywhere we
    don't want to await the DB write).

    Two reasons not to just `await record_event(...)` directly:
    1. Most callers are inside hot loops (active health probe, sidecar
       supervisor) where a slow DB write could stall the next iteration.
    2. Some callers are sync helpers (xray subprocess monitors) that
       can't `await` at all.

    Trade-off: events scheduled right before shutdown may be lost.
    Acceptable for phase 1 — these are notifications, not audit logs.
    """
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(coro)
    except RuntimeError:
        # No running loop (e.g. called during interpreter shutdown). The
        # coroutine is dropped; not worth surfacing as an error since the
        # event would have been lost on shutdown anyway.
        coro.close()


# ── Background trim task ─────────────────────────────────────────────────────


_trim_task: Optional[asyncio.Task] = None


async def _trim_once() -> None:
    """One pass: age-based purge + hard-cap fallback."""
    from app.models import Event

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=EVENTS_MAX_AGE_HOURS)
    try:
        async with AsyncSession(get_async_engine()) as session:
            await session.exec(delete(Event).where(Event.timestamp < cutoff))
            await session.commit()

            count = (await session.exec(select(func.count()).select_from(Event))).one()
            if count > EVENTS_HARD_CAP:
                oldest_ids = list((await session.exec(
                    select(Event.id)
                    .order_by(Event.timestamp.asc())
                    .limit(count - EVENTS_HARD_CAP)
                )).all())
                if oldest_ids:
                    await session.exec(delete(Event).where(Event.id.in_(oldest_ids)))
                    await session.commit()
                    log.info(
                        "events: hard-cap trim removed %d rows (total was %d)",
                        len(oldest_ids), count,
                    )
    except Exception as exc:
        log.warning("event trim error: %s", exc)


async def _trim_loop() -> None:
    while True:
        try:
            await _trim_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("event trim loop error: %s", exc)
        await asyncio.sleep(EVENTS_TRIM_INTERVAL_SEC)


def start_trim_task() -> None:
    """Launch the trim loop. Idempotent."""
    global _trim_task
    if _trim_task and not _trim_task.done():
        return
    _trim_task = asyncio.create_task(_trim_loop(), name="events-trim")
    log.info(
        "events trim task started (retention=%dh, hard_cap=%d, interval=%ds)",
        EVENTS_MAX_AGE_HOURS, EVENTS_HARD_CAP, EVENTS_TRIM_INTERVAL_SEC,
    )


def stop_trim_task() -> None:
    """Cancel the trim loop."""
    global _trim_task
    if _trim_task and not _trim_task.done():
        _trim_task.cancel()
    _trim_task = None
