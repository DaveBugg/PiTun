"""Hourly re-check of deployed chains.

A panel reinstall or a hand-deleted inbound leaves `ProxyChain.status` at
`deployed` while exported clients fail. The on-demand healthcheck already knows
how to tell; this just runs it on a timer and flags the chain when it drifts.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine
from app.models import ProxyChain, Settings as DBSettings

logger = logging.getLogger(__name__)

_INTERVAL_SEC = 3600
_FIRST_DELAY_SEC = 600  # let the box and the panels settle after a boot


async def _enabled() -> bool:
    async with AsyncSession(get_async_engine()) as session:
        row = (await session.exec(
            select(DBSettings).where(DBSettings.key == "chain_drift_check")
        )).first()
    return row is None or row.value.lower() != "false"


class ChainDriftScheduler:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()

    async def _loop(self) -> None:
        try:
            await asyncio.sleep(_FIRST_DELAY_SEC)
            while self._running:
                try:
                    await self.run_once()
                except Exception as exc:  # noqa: BLE001 — the loop must survive a bad pass
                    from app.core import error_log
                    error_log.record("chain_drift.run", exc)
                await asyncio.sleep(_INTERVAL_SEC)
        except asyncio.CancelledError:
            return

    async def run_once(self) -> int:
        """Check every deployed/degraded chain. Returns how many were checked."""
        if not await _enabled():
            return 0
        from app.api.xui import healthcheck_chain
        from app.core.events import record_event

        async with AsyncSession(get_async_engine()) as session:
            chains = list((await session.exec(
                select(ProxyChain.id, ProxyChain.name, ProxyChain.status)
                .where(ProxyChain.status.in_(("deployed", "degraded")))
            )).all())

        checked = 0
        for chain_id, name, before in chains:
            try:
                async with AsyncSession(get_async_engine()) as session:
                    await healthcheck_chain(chain_id, session)
                    after = (await session.get(ProxyChain, chain_id)).status
            except Exception as exc:  # noqa: BLE001 — one unreachable panel must not stop the rest
                from app.core import error_log
                error_log.record("chain_drift.check", exc, chain_id=chain_id)
                continue
            checked += 1
            if before == "deployed" and after == "degraded":
                await record_event(
                    category="chain.degraded",
                    severity="warning",
                    title=f"Chain '{name}' no longer passes its healthcheck",
                    details="A panel inbound or the relay route is missing — open the chain and run the healthcheck.",
                    entity_id=chain_id,
                    dedup_window_sec=6 * 3600,
                )
        return checked


chain_drift_scheduler = ChainDriftScheduler()
