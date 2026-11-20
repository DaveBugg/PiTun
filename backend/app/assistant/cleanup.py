"""Housekeeping for the assistant, run hourly: screenshots older than the
retention are removed from stored messages, and per-chat locks of idle
chats are dropped."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

FIRST_DELAY_S = 300
INTERVAL_S = 3600


def prune_locks() -> int:
    """Drop the locks of chats that are not answering right now."""
    from app.assistant import agent
    idle = [cid for cid, lock in agent._locks.items() if not lock.locked()]
    for cid in idle:
        agent._locks.pop(cid, None)
    return len(idle)


async def strip_old_images(session: AsyncSession, days: int) -> int:
    """Remove screenshots from user messages older than `days`. The message
    keeps its text and a count of what was removed. -> messages changed."""
    from app.models import AssistantMessage
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=max(1, days))
    rows = (await session.exec(select(AssistantMessage).where(
        AssistantMessage.role == "user", AssistantMessage.content.contains('"images"')))).all()
    changed = 0
    for row in rows:
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=timezone.utc)
        if created >= cutoff:
            continue
        try:
            msg = json.loads(row.content)
        except ValueError:
            continue
        images = msg.pop("images", None)
        if not images:
            continue
        msg["images_removed"] = int(msg.get("images_removed") or 0) + len(images)
        row.content = json.dumps(msg, ensure_ascii=False)
        session.add(row)
        changed += 1
    if changed:
        await session.commit()
    return changed


async def run_once() -> dict:
    from app.assistant import settings as asettings
    from app.database import get_async_engine
    async with AsyncSession(get_async_engine()) as session:
        s = await asettings.load(session)
        stripped = await strip_old_images(session, s.screenshot_days)
    return {"screenshots_removed_from": stripped, "locks_pruned": prune_locks()}


async def cleanup_loop() -> None:
    await asyncio.sleep(FIRST_DELAY_S)
    while True:
        try:
            res = await run_once()
            if res["screenshots_removed_from"]:
                logger.info("Assistant cleanup: screenshots removed from %d message(s)",
                            res["screenshots_removed_from"])
        except Exception as exc:  # noqa: BLE001 — housekeeping, never fatal
            logger.debug("assistant cleanup failed: %s", exc)
        await asyncio.sleep(INTERVAL_S)
