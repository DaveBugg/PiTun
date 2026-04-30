"""Parse and store DNS resolution events from xray log output."""
import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import select, func, delete
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine

log = logging.getLogger("pitun.dns_logger")

# Retention policy for DNSQueryLog:
#   - Time-based: anything older than DNS_LOG_MAX_AGE_HOURS is pruned.
#   - Hard cap:  if row count still exceeds DNS_LOG_HARD_CAP, oldest rows are
#     dropped until we're under the cap (e.g. if the user bursts >50k queries
#     in <24h).
# Previously `_trim_dns_log()` ran synchronously after every insert, issuing
# a full-table COUNT(*) on the hot path — at 100+ DNS/sec that starves SQLite
# and stalls the xray log parser. Now the trim runs on a background task.
DNS_LOG_MAX_AGE_HOURS = 48
DNS_LOG_HARD_CAP = 50000
DNS_LOG_TRIM_INTERVAL_SEC = 300   # 5 minutes

# xray v26 format:
#   [Info] app/dns: UDP:94.140.14.14:53 got answer: youtube.com. TypeA -> [1.2.3.4 5.6.7.8], rtt: 74ms
#   [Debug] app/dns: UDP:94.140.14.14:53 got answer: youtube.com. TypeA -> [1.2.3.4], rtt: 5ms (cached)
#   [Info] app/dns: domain youtube.com will use DNS in order: [...]  (rule match, no IPs — skip)
PATTERNS = [
    re.compile(
        r'app/dns: \w+:([^:]+):\d+ got answer: (\S+)\s+Type(\w+) -> \[([^\]]*)\]'
    ),
    # cache hit (may appear as "got answer" with "(cached)" suffix — covered above,
    # but keep old-style pattern as fallback)
    re.compile(r'app/dns: .*cache[^:]*:\s*([^\s]+)\s*->\s*([^\s,\[]+)'),
    # fakedns
    re.compile(r'app/dns: .*[Ff]ake[^:]*:\s*([^\s]+)\s*->\s*([^\s,\[]+)'),
]


def parse_dns_line(line: str) -> "Optional[object]":
    from app.models import DNSQueryLog

    m = PATTERNS[0].search(line)
    if m:
        server, domain, qtype, ips_str = m.groups()
        domain = domain.rstrip(".")
        if "://" in domain:
            return None
        # v26 IPs are space-separated; strip trailing comma/whitespace
        ips = [ip.strip().rstrip(",") for ip in ips_str.split() if ip.strip()]
        cache_hit = "(cached)" in line
        return DNSQueryLog(
            domain=domain,
            resolved_ips=json.dumps(ips),
            server_used="cache" if cache_hit else server,
            query_type=qtype,
            cache_hit=cache_hit,
        )

    m = PATTERNS[1].search(line)
    if m:
        domain, ip = m.groups()
        return DNSQueryLog(
            domain=domain.rstrip("."),
            resolved_ips=json.dumps([ip]),
            server_used="cache",
            cache_hit=True,
        )

    m = PATTERNS[2].search(line)
    if m:
        domain, fake_ip = m.groups()
        return DNSQueryLog(
            domain=domain.rstrip("."),
            resolved_ips=json.dumps([fake_ip]),
            server_used="fakedns",
            cache_hit=False,
        )

    return None


async def process_log_line(line: str) -> None:
    if "app/dns:" not in line:
        return
    entry = parse_dns_line(line)
    if entry is None:
        return
    try:
        async with AsyncSession(get_async_engine()) as session:
            session.add(entry)
            await session.commit()
    except Exception as e:
        log.debug("dns log store error: %s", e)


# ── Background trim task ─────────────────────────────────────────────────────

_trim_task: Optional[asyncio.Task] = None


async def _trim_once() -> None:
    """One pass of DNSQueryLog cleanup: age-based purge + hard-cap fallback."""
    from app.models import DNSQueryLog

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=DNS_LOG_MAX_AGE_HOURS)
    try:
        async with AsyncSession(get_async_engine()) as session:
            # 1) Age-based purge — bulk delete, no intermediate SELECT.
            await session.exec(
                delete(DNSQueryLog).where(DNSQueryLog.timestamp < cutoff)
            )
            await session.commit()

            # 2) Hard-cap fallback. Single count, only if the cap might matter
            # (e.g. user just ran a massive burst).
            count = (await session.exec(select(func.count()).select_from(DNSQueryLog))).one()
            if count > DNS_LOG_HARD_CAP:
                oldest_ids = list((await session.exec(
                    select(DNSQueryLog.id)
                    .order_by(DNSQueryLog.timestamp.asc())
                    .limit(count - DNS_LOG_HARD_CAP)
                )).all())
                if oldest_ids:
                    await session.exec(
                        delete(DNSQueryLog).where(DNSQueryLog.id.in_(oldest_ids))
                    )
                    await session.commit()
                    log.info(
                        "dns log: hard-cap trim removed %d rows (total was %d)",
                        len(oldest_ids), count,
                    )
    except Exception as e:
        log.warning("dns log trim error: %s", e)


async def _trim_loop() -> None:
    while True:
        try:
            await _trim_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("dns log trim loop error: %s", exc)
        await asyncio.sleep(DNS_LOG_TRIM_INTERVAL_SEC)


def start_trim_task() -> None:
    """Launch the background trim loop. Idempotent (safe to call twice)."""
    global _trim_task
    if _trim_task and not _trim_task.done():
        return
    _trim_task = asyncio.create_task(_trim_loop(), name="dns-log-trim")
    log.info(
        "dns log trim task started (retention=%dh, hard_cap=%d, interval=%ds)",
        DNS_LOG_MAX_AGE_HOURS, DNS_LOG_HARD_CAP, DNS_LOG_TRIM_INTERVAL_SEC,
    )


def stop_trim_task() -> None:
    """Cancel the background trim loop."""
    global _trim_task
    if _trim_task and not _trim_task.done():
        _trim_task.cancel()
    _trim_task = None
