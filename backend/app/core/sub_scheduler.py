"""Background scheduler for auto-updating subscriptions."""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine
from app.models import Subscription

logger = logging.getLogger(__name__)

_CHECK_INTERVAL = 60


class SubscriptionScheduler:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("Subscription scheduler started")

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._tick()
            except Exception as exc:
                logger.exception("Subscription scheduler error: %s", exc)
            await asyncio.sleep(_CHECK_INTERVAL)

    async def _tick(self) -> None:
        async with AsyncSession(get_async_engine()) as session:
            subs = (await session.exec(
                select(Subscription).where(Subscription.auto_update == True)  # noqa: E712
            )).all()
            sub_data = [
                {"id": s.id, "enabled": s.enabled, "name": s.name,
                 "last_updated": s.last_updated, "update_interval": s.update_interval}
                for s in subs
            ]

        now = datetime.now(tz=timezone.utc)
        for sub in sub_data:
            if not sub["enabled"]:
                continue

            if sub["last_updated"] is None:
                due = True
            else:
                last = sub["last_updated"]
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                elapsed = (now - last).total_seconds()
                due = elapsed >= sub["update_interval"]

            if due:
                logger.info(
                    "Auto-refreshing subscription %d (%s)", sub["id"], sub["name"]
                )
                await self._refresh(sub["id"])

    async def _refresh(self, sub_id: int) -> None:
        from app.api.subscriptions import _fetch_subscription
        try:
            await _fetch_subscription(sub_id)
        except Exception as exc:
            logger.error("Auto-refresh failed for subscription %d: %s", sub_id, exc)


subscription_scheduler = SubscriptionScheduler()
