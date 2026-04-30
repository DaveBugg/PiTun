"""Background auto-updater for xray geo data (geoip.dat / geosite.dat).

Without this, the geo files only get refreshed when the user manually
clicks "Update" on the GeoData page or runs `scripts/update_geo.sh`.
On a long-running install they drift months out of date — the
Loyalsoldier ruleset is updated weekly upstream, so a 1-month-old file
already misses Telegram CDN ranges, new geosite categories, etc.

Design — same shape as `sub_scheduler` and `circle_scheduler`:
- Singleton task started from `app.main.lifespan`
- Wakes once per `_CHECK_INTERVAL` seconds (1 hour by default — cheap)
- Reads two settings on each tick (so toggles take effect without
  restarting the scheduler):
    geo_auto_update          on/off              default "true"
    geo_update_interval_days   N days             default 7
- If interval has elapsed since last successful update (mtime of
  the geoip.dat file on disk), kicks off the same `update_geoip` /
  `update_geosite` / `update_mmdb` coroutines that the API endpoint
  uses. Errors are logged at WARNING level — next tick retries.
"""
import asyncio
import logging
import os
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.database import get_async_engine
from app.models import Settings as DBSettings

logger = logging.getLogger(__name__)

# How often the scheduler wakes up to check whether an update is due.
# 1 hour is fine — the actual download cadence is governed by
# `geo_update_interval_days`. A shorter check window only helps if the
# user just changed the interval setting.
_CHECK_INTERVAL_SEC = 3600

# Default behaviour when the DB settings are absent (e.g. fresh install).
_DEFAULT_AUTO = True
_DEFAULT_INTERVAL_DAYS = 1   # daily — Loyalsoldier ruleset releases ~weekly
                              #         but gets minor edits more often;
                              #         user prefers daily check.

# Off-peak window (local time of the backend container) during which the
# auto-update is allowed to run. 04:00–06:00 by default — quiet on most
# home networks. Outside the window, the scheduler tick is a no-op even
# if the file is overdue. The user can still trigger an immediate update
# via the GeoData page → "Update" button (separate code path).
#
# NB: container TZ is whatever Docker provides — typically UTC. If your
# 4-6 AM should be local-Moscow / -PST / etc., set `TZ=Europe/Moscow` in
# the backend service of docker-compose.yml.
_DEFAULT_WINDOW_START = 4
_DEFAULT_WINDOW_END = 6


class GeoScheduler:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running = False

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop(), name="geo-scheduler")
            logger.info(
                "Geo scheduler started (check every %ds, default interval %dd)",
                _CHECK_INTERVAL_SEC, _DEFAULT_INTERVAL_DAYS,
            )

    def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        # First tick: wait 5 minutes after boot before considering an update.
        # Lets the user's normal startup sequence settle first (xray, nftables,
        # subscription fetch) instead of slamming a 50 MB download in parallel.
        await asyncio.sleep(300)
        while self._running:
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Geo scheduler tick error: %s", exc)
            await asyncio.sleep(_CHECK_INTERVAL_SEC)

    async def _tick(self) -> None:
        async with AsyncSession(get_async_engine()) as session:
            settings_map = {
                row.key: row.value
                for row in (await session.exec(select(DBSettings))).all()
            }
        auto = settings_map.get("geo_auto_update", str(_DEFAULT_AUTO).lower()).lower() == "true"
        if not auto:
            return
        try:
            interval_days = int(settings_map.get("geo_update_interval_days") or _DEFAULT_INTERVAL_DAYS)
        except (TypeError, ValueError):
            interval_days = _DEFAULT_INTERVAL_DAYS
        try:
            window_start = int(settings_map.get("geo_update_window_start") or _DEFAULT_WINDOW_START)
            window_end = int(settings_map.get("geo_update_window_end") or _DEFAULT_WINDOW_END)
        except (TypeError, ValueError):
            window_start, window_end = _DEFAULT_WINDOW_START, _DEFAULT_WINDOW_END

        # `geoip.dat` is the canonical "have we updated recently" marker —
        # all 3 files (geoip / geosite / mmdb) are fetched together, so
        # checking one is enough.
        if not os.path.exists(settings.xray_geoip_path):
            # Initial download bypasses the off-peak window — the user
            # presumably wants the proxy usable on first launch, not the
            # next morning at 4 AM.
            logger.info("Geo scheduler: %s missing — initial download (bypassing window)", settings.xray_geoip_path)
            await self._update_all()
            return

        age_days = (time.time() - os.path.getmtime(settings.xray_geoip_path)) / 86_400
        if age_days < interval_days:
            return

        # Age-based threshold met — only proceed if we're inside the
        # allowed update window. Use the user-configured `timezone`
        # setting (Settings page → Timezone) rather than the container's
        # TZ env var, so the schedule means what the user intends without
        # a docker-compose edit. Falls back to UTC if the saved value
        # doesn't resolve as a valid IANA zone (typo / removed entry).
        tz_name = settings_map.get("timezone") or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            logger.warning("Geo scheduler: unknown timezone %r, falling back to UTC", tz_name)
            tz = ZoneInfo("UTC")
        cur_hour = datetime.now(tz).hour
        in_window = window_start <= cur_hour < window_end
        if not in_window:
            return

        logger.info(
            "Geo scheduler: geoip.dat is %.1fd old (>=%dd), in window %02d:00-%02d:00 %s — updating",
            age_days, interval_days, window_start, window_end, tz_name,
        )
        await self._update_all()

    async def _update_all(self) -> None:
        from app.core.geo import update_geoip, update_geosite, update_mmdb
        # Run them in parallel; if one fails, the others still try.
        results = await asyncio.gather(
            update_geoip(),
            update_geosite(),
            update_mmdb(),
            return_exceptions=True,
        )
        names = ("geoip", "geosite", "mmdb")
        any_success = False
        succeeded = []
        failed = []
        for name, r in zip(names, results):
            if isinstance(r, Exception):
                logger.warning("Geo scheduler: %s update failed: %s", name, r)
                failed.append(name)
            else:
                logger.info("Geo scheduler: %s updated", name)
                any_success = True
                succeeded.append(name)

        # Single rollup event per scheduler run (3 files share the same
        # cadence, so 3 separate events would just clutter the feed).
        from app.core.events import record_event
        if succeeded and not failed:
            await record_event(
                category="geo.updated",
                severity="info",
                title="GeoData updated",
                details=f"Refreshed: {', '.join(succeeded)}",
            )
        elif succeeded and failed:
            await record_event(
                category="geo.updated",
                severity="warning",
                title="GeoData partially updated",
                details=f"Refreshed: {', '.join(succeeded)}; failed: {', '.join(failed)}",
            )
        elif failed:
            await record_event(
                category="geo.failed",
                severity="error",
                title="GeoData update failed",
                details=f"Failed: {', '.join(failed)}",
                dedup_window_sec=3600,
            )

        # Reload xray if it's running and at least one file refreshed.
        # xray reads geoip.dat / geosite.dat at startup AND when a config
        # reload pulls new rule lookups; without a reload the new ranges
        # don't take effect until the next manual restart. Reload is much
        # cheaper than a full restart — old connections aren't dropped.
        if any_success:
            try:
                from app.core.xray import xray_manager
                if xray_manager.is_running:
                    await xray_manager.reload()
                    logger.info("Geo scheduler: xray reloaded to pick up new geo data")
            except Exception as exc:
                logger.warning("Geo scheduler: xray reload failed: %s", exc)


geo_scheduler = GeoScheduler()
