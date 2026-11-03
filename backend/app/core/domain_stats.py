"""Opt-in domain statistics.

A compact per-domain counter (`domainstat`: domain, hits, first/last seen, the
last few resolved IPs) fed from xray's DNS log lines, kept small on purpose:

* hits are counted in memory and flushed in batches, so a busy network does
  not turn into a database write per query;
* the table is capped (rows) and aged out (days), and the operator can clear it
  by hand, by age, down to the top N, or per domain;
* nothing is collected unless `collect_domain_stats` is on — it is a list of
  what the household looks up, so it is off by default.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from sqlalchemy import case
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import delete, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine
from app.models import DomainStat, Settings as DBSettings

log = logging.getLogger("pitun.domain_stats")

KEY_ENABLED = "collect_domain_stats"
KEY_RETENTION = "domain_stats_retention_days"
KEY_MAX_ROWS = "domain_stats_max_rows"

DEFAULT_RETENTION_DAYS = 90
DEFAULT_MAX_ROWS = 20_000
MAX_PENDING = 20_000          # distinct domains buffered between flushes
FLUSH_INTERVAL_SEC = 60
PRUNE_INTERVAL_SEC = 3600
_BATCH = 400
_KEEP_IPS = 4
_FAKEDNS = ipaddress.ip_network("198.18.0.0/15")

# domain -> [hits, last_seen, ips]
_pending: Dict[str, list] = {}
_dropped = 0


# ── normalising what goes in ─────────────────────────────────────────────────

def clean_domain(raw: str) -> Optional[str]:
    d = (raw or "").strip().lower().rstrip(".")
    if not d or len(d) > 253 or "." not in d or " " in d:
        return None
    if d.endswith(".arpa") or d.endswith(".local") or d.endswith(".lan") or d.endswith(".localdomain"):
        return None
    try:
        ipaddress.ip_address(d)
        return None
    except ValueError:
        return d


def _usable_ips(ips: Iterable[str]) -> List[str]:
    out: List[str] = []
    for raw in ips:
        try:
            ip = ipaddress.ip_address(str(raw).strip())
        except ValueError:
            continue
        if ip.version == 4 and ip in _FAKEDNS:
            continue                      # fakedns pool, not a real address
        if ip.is_private or ip.is_loopback or ip.is_unspecified or ip.is_link_local:
            continue
        s = str(ip)
        if s not in out:
            out.append(s)
        if len(out) >= _KEEP_IPS:
            break
    return out


def record(domain: str, ips: Iterable[str] = ()) -> bool:
    """Count one lookup in memory. Cheap, non-blocking, never raises."""
    global _dropped
    d = clean_domain(domain)
    if d is None:
        return False
    slot = _pending.get(d)
    if slot is None:
        if len(_pending) >= MAX_PENDING:
            _dropped += 1
            return False
        slot = _pending[d] = [0, None, []]
    slot[0] += 1
    slot[1] = datetime.now(timezone.utc)
    fresh = _usable_ips(ips)
    if fresh:
        slot[2] = fresh
    return True


# ── settings ─────────────────────────────────────────────────────────────────

async def _get(session: AsyncSession, key: str, default: str) -> str:
    row = (await session.exec(select(DBSettings).where(DBSettings.key == key))).first()
    return row.value if row and row.value is not None else default


async def _set(session: AsyncSession, key: str, value: str) -> None:
    row = (await session.exec(select(DBSettings).where(DBSettings.key == key))).first()
    if row is None:
        session.add(DBSettings(key=key, value=value))
    else:
        row.value = value
        session.add(row)


def _int(raw: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(raw)))
    except (TypeError, ValueError):
        return default


async def get_config(session: AsyncSession) -> Dict[str, object]:
    return {
        "enabled": (await _get(session, KEY_ENABLED, "false")).lower() == "true",
        "retention_days": _int(await _get(session, KEY_RETENTION, str(DEFAULT_RETENTION_DAYS)), DEFAULT_RETENTION_DAYS, 0, 3650),
        "max_rows": _int(await _get(session, KEY_MAX_ROWS, str(DEFAULT_MAX_ROWS)), DEFAULT_MAX_ROWS, 100, 1_000_000),
    }


async def set_config(
    session: AsyncSession, *, enabled: Optional[bool] = None,
    retention_days: Optional[int] = None, max_rows: Optional[int] = None,
) -> Dict[str, object]:
    if enabled is not None:
        await _set(session, KEY_ENABLED, "true" if enabled else "false")
    if retention_days is not None:
        await _set(session, KEY_RETENTION, str(_int(str(retention_days), DEFAULT_RETENTION_DAYS, 0, 3650)))
    if max_rows is not None:
        await _set(session, KEY_MAX_ROWS, str(_int(str(max_rows), DEFAULT_MAX_ROWS, 100, 1_000_000)))
    await session.commit()
    global _enabled_checked
    _enabled_checked = 0.0               # re-read on the next DNS line
    return await get_config(session)


_enabled = False
_enabled_checked = 0.0
_ENABLED_TTL = 30.0


async def collecting() -> bool:
    """Cached 'is collection on' for the hot DNS-line path."""
    global _enabled, _enabled_checked
    now = time.time()
    if now - _enabled_checked > _ENABLED_TTL:
        try:
            async with AsyncSession(get_async_engine()) as session:
                _enabled = (await _get(session, KEY_ENABLED, "false")).lower() == "true"
        except Exception:  # noqa: BLE001 - keep the last known answer
            pass
        _enabled_checked = now
    return _enabled


# ── flush / prune ────────────────────────────────────────────────────────────

async def flush() -> int:
    """Write the buffered counters. Returns the number of domains written."""
    global _pending
    if not _pending:
        return 0
    batch, _pending = _pending, {}
    rows = [
        {
            "domain": d, "hits": s[0], "first_seen": s[1], "last_seen": s[1],
            "ips": json.dumps(s[2]),
        }
        for d, s in batch.items() if s[1] is not None
    ]
    try:
        async with AsyncSession(get_async_engine()) as session:
            for i in range(0, len(rows), _BATCH):
                chunk = rows[i:i + _BATCH]
                stmt = sqlite_insert(DomainStat).values(chunk)
                ex = stmt.excluded
                stmt = stmt.on_conflict_do_update(
                    index_elements=["domain"],
                    set_={
                        "hits": DomainStat.hits + ex.hits,
                        "last_seen": ex.last_seen,
                        "ips": case((ex.ips != "[]", ex.ips), else_=DomainStat.ips),
                    },
                )
                await session.exec(stmt)
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("domain stats flush failed: %s", exc)
        # put the counts back rather than losing them
        for d, s in batch.items():
            cur = _pending.setdefault(d, [0, None, []])
            cur[0] += s[0]
            cur[1] = s[1] or cur[1]
            cur[2] = s[2] or cur[2]
        return 0
    return len(rows)


async def prune(session: AsyncSession, *, retention_days: Optional[int] = None, max_rows: Optional[int] = None) -> int:
    """Age out old rows, then trim to `max_rows` dropping the least-used first."""
    removed = 0
    if retention_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        res = await session.exec(delete(DomainStat).where(DomainStat.last_seen < cutoff))
        removed += res.rowcount or 0
    if max_rows:
        total = (await session.exec(select(func.count()).select_from(DomainStat))).one()
        if total > max_rows:
            ids = list((await session.exec(
                select(DomainStat.id).order_by(DomainStat.hits.asc(), DomainStat.last_seen.asc()).limit(total - max_rows)
            )).all())
            if ids:
                res = await session.exec(delete(DomainStat).where(DomainStat.id.in_(ids)))
                removed += res.rowcount or 0
    await session.commit()
    return removed


async def summary(session: AsyncSession) -> Dict[str, object]:
    rows, hits, size, oldest, newest = (await session.exec(
        select(
            func.count(), func.coalesce(func.sum(DomainStat.hits), 0),
            func.coalesce(func.sum(func.length(DomainStat.domain) + func.length(DomainStat.ips)), 0),
            func.min(DomainStat.first_seen), func.max(DomainStat.last_seen),
        )
    )).one()
    # per row: the two timestamps, the integer columns, and the unique index on
    # the domain (stored a second time).
    approx = int(rows) * 72 + int(size) * 2
    return {
        "rows": int(rows), "total_hits": int(hits), "approx_bytes": approx,
        "oldest": oldest.isoformat() if oldest else None,
        "newest": newest.isoformat() if newest else None,
        "pending": len(_pending), "dropped": _dropped,
    }


# ── background loop ──────────────────────────────────────────────────────────

class DomainStatsScheduler:
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
        last_prune = 0.0
        try:
            while self._running:
                await asyncio.sleep(FLUSH_INTERVAL_SEC)
                try:
                    await flush()
                    if time.time() - last_prune > PRUNE_INTERVAL_SEC:
                        last_prune = time.time()
                        async with AsyncSession(get_async_engine()) as session:
                            cfg = await get_config(session)
                            await prune(session, retention_days=int(cfg["retention_days"]), max_rows=int(cfg["max_rows"]))
                except Exception as exc:  # noqa: BLE001 - the loop must survive
                    log.warning("domain stats loop error: %s", exc)
        except asyncio.CancelledError:
            await flush()                  # do not lose the buffer on shutdown


domain_stats_scheduler = DomainStatsScheduler()
