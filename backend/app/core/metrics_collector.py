"""Background system metrics collector — CPU, RAM, disk, network."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import psutil
from sqlalchemy import delete
from sqlmodel import col
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine
from app.models import SystemMetric

logger = logging.getLogger(__name__)

COLLECT_INTERVAL_SEC = 60
RETENTION_DAYS = 3
_CLEANUP_EVERY_N = 60  # run cleanup once per 60 collections (~1 hour)


def _read_system_metrics():
    """Synchronous psutil reads — runs in a thread to avoid blocking the event loop.

    psutil.cpu_percent(interval=1) calls time.sleep(1) internally.
    """
    cpu = psutil.cpu_percent(interval=1)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    net = psutil.net_io_counters()
    return cpu, mem, disk, net


class MetricsCollector:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._collect_count = 0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
            logger.info("Metrics collector started (interval=%ds, retention=%dd)",
                        COLLECT_INTERVAL_SEC, RETENTION_DAYS)

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        try:
            await asyncio.sleep(5)
            while self._running:
                try:
                    await self._collect()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("Metrics collection error: %s", exc)
                try:
                    await asyncio.sleep(COLLECT_INTERVAL_SEC)
                except asyncio.CancelledError:
                    break
        except asyncio.CancelledError:
            pass

    async def _collect(self) -> None:
        # Read system metrics in a thread to avoid blocking the event loop
        # (psutil.cpu_percent sleeps for 1 second)
        cpu, mem, disk, net = await asyncio.to_thread(_read_system_metrics)

        metric = SystemMetric(
            ts=datetime.now(timezone.utc),
            cpu_percent=cpu,
            ram_used_mb=round(mem.used / (1024 * 1024), 1),
            ram_total_mb=round(mem.total / (1024 * 1024), 1),
            disk_used_gb=round(disk.used / (1024 ** 3), 2),
            disk_total_gb=round(disk.total / (1024 ** 3), 2),
            net_sent_bytes=net.bytes_sent,
            net_recv_bytes=net.bytes_recv,
        )

        async with AsyncSession(get_async_engine()) as session:
            session.add(metric)

            # Bulk-delete old metrics once per hour instead of every collection
            self._collect_count += 1
            if self._collect_count >= _CLEANUP_EVERY_N:
                self._collect_count = 0
                cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
                await session.exec(
                    delete(SystemMetric).where(col(SystemMetric.ts) < cutoff)  # type: ignore[arg-type]
                )

            await session.commit()


metrics_collector = MetricsCollector()
