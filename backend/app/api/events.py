"""Recent Events API.

Read-only feed of background state-transitions (failover, sidecar
restart, geo update, etc.) populated by `app.core.events.record_event`.
The Dashboard "Recent Events" card polls `/api/events?limit=8`.
"""
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Query
from sqlmodel import select, delete
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine
from app.models import Event
from app.schemas import EventRead

router = APIRouter(prefix="/events", tags=["events"])


@router.get("", response_model=List[EventRead])
async def list_events(
    limit: int = Query(50, ge=1, le=200),
    category: Optional[str] = None,
    severity: Optional[str] = None,
    since: Optional[datetime] = None,
):
    """Return the most recent events, newest first.

    Optional filters:
      - `category`: dotted code prefix (e.g. "failover" matches
        "failover.switched"). Falls back to exact match if no dot.
      - `severity`: "info" | "warning" | "error".
      - `since`: ISO timestamp; only events at or after this time.
    """
    async with AsyncSession(get_async_engine()) as session:
        stmt = select(Event)
        if category:
            # Allow `category=failover` to match all failover.* codes.
            if "." in category:
                stmt = stmt.where(Event.category == category)
            else:
                stmt = stmt.where(Event.category.like(f"{category}.%") | (Event.category == category))
        if severity:
            stmt = stmt.where(Event.severity == severity)
        if since:
            stmt = stmt.where(Event.timestamp >= since)
        stmt = stmt.order_by(Event.timestamp.desc()).limit(limit)
        rows = list((await session.exec(stmt)).all())
    return rows


@router.delete("")
async def clear_events():
    """Wipe all events. Same auth gate as the rest of the admin API."""
    async with AsyncSession(get_async_engine()) as session:
        await session.exec(delete(Event))
        await session.commit()
    return {"cleared": True}
