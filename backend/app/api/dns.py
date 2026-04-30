"""DNS management endpoints."""
import asyncio
import logging
import socket
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import select, func, delete
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import DNSRule, DNSQueryLog, Settings as DBSettings
from app.schemas import (
    DNSRuleCreate,
    DNSRuleRead,
    DNSRuleUpdate,
    DNSSettingsRead,
    DNSSettingsUpdate,
    DNSTestRequest,
    DNSTestResult,
    DNSQueryLogRead,
    DNSQueryStats,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/dns", tags=["dns"])

_DNS_SETTING_KEYS = {
    "dns_mode",
    "dns_upstream",
    "dns_upstream_secondary",
    "dns_fallback",
    "dns_port",
    "fakedns_enabled",
    "fakedns_pool",
    "fakedns_pool_size",
    "dns_sniffing",
    "bypass_cn_dns",
    "bypass_ru_dns",
    "dns_disable_fallback",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_settings_map(session: AsyncSession) -> dict:
    rows = (await session.exec(select(DBSettings))).all()
    return {r.key: r.value for r in rows}


def _settings_map_to_dns(m: dict) -> DNSSettingsRead:
    return DNSSettingsRead(
        dns_mode=m.get("dns_mode", "plain"),
        dns_upstream=m.get("dns_upstream", "8.8.8.8"),
        dns_upstream_secondary=m.get("dns_upstream_secondary") or None,
        dns_fallback=m.get("dns_fallback") or None,
        dns_port=int(m.get("dns_port", 5353)),
        fakedns_enabled=m.get("fakedns_enabled", "false").lower() == "true",
        fakedns_pool=m.get("fakedns_pool", "198.18.0.0/15"),
        fakedns_pool_size=int(m.get("fakedns_pool_size", 65535)),
        dns_sniffing=m.get("dns_sniffing", "true").lower() == "true",
        bypass_cn_dns=m.get("bypass_cn_dns", "false").lower() == "true",
        bypass_ru_dns=m.get("bypass_ru_dns", "false").lower() == "true",
        dns_disable_fallback=m.get("dns_disable_fallback", "true").lower() == "true",
    )


async def _upsert_setting(session: AsyncSession, key: str, value: str) -> None:
    stmt = select(DBSettings).where(DBSettings.key == key)
    existing = (await session.exec(stmt)).first()
    if existing:
        existing.value = value
    else:
        session.add(DBSettings(key=key, value=value))


# ── DNS Settings ──────────────────────────────────────────────────────────────

@router.get("/settings", response_model=DNSSettingsRead)
async def get_dns_settings(session: AsyncSession = Depends(get_session)):
    m = await _get_settings_map(session)
    return _settings_map_to_dns(m)


@router.patch("/settings", response_model=DNSSettingsRead)
async def update_dns_settings(
    body: DNSSettingsUpdate,
    session: AsyncSession = Depends(get_session),
):
    updates = body.model_dump(exclude_none=True)
    for field, val in updates.items():
        if field in _DNS_SETTING_KEYS:
            await _upsert_setting(session, field, str(val).lower() if isinstance(val, bool) else str(val))
    await session.commit()
    m = await _get_settings_map(session)
    return _settings_map_to_dns(m)


# ── DNS Rules ─────────────────────────────────────────────────────────────────

@router.get("/rules", response_model=List[DNSRuleRead])
async def list_dns_rules(session: AsyncSession = Depends(get_session)):
    rules = (await session.exec(select(DNSRule).order_by(DNSRule.order))).all()
    return rules


@router.post("/rules", response_model=DNSRuleRead, status_code=201)
async def create_dns_rule(
    body: DNSRuleCreate,
    session: AsyncSession = Depends(get_session),
):
    rule = DNSRule(**body.model_dump())
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


@router.put("/rules/{rule_id}", response_model=DNSRuleRead)
async def update_dns_rule(
    rule_id: int,
    body: DNSRuleUpdate,
    session: AsyncSession = Depends(get_session),
):
    rule = await session.get(DNSRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="DNS rule not found")
    updates = body.model_dump(exclude_none=True)
    for field, val in updates.items():
        setattr(rule, field, val)
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_dns_rule(
    rule_id: int,
    session: AsyncSession = Depends(get_session),
):
    rule = await session.get(DNSRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="DNS rule not found")
    await session.delete(rule)
    await session.commit()


@router.post("/rules/reorder", status_code=204)
async def reorder_dns_rules(
    ids: List[int],
    session: AsyncSession = Depends(get_session),
):
    """Reorder DNS rules. Matches the 204 contract of other reorder endpoints
    (routing rules, nodes) — no body on success."""
    for idx, rule_id in enumerate(ids):
        rule = await session.get(DNSRule, rule_id)
        if rule:
            rule.order = idx * 10
            session.add(rule)
    await session.commit()


# ── DNS Query Log ─────────────────────────────────────────────────────────────

@router.get("/queries", response_model=List[DNSQueryLogRead])
async def list_dns_queries(
    domain: Optional[str] = Query(default=None, description="Filter by domain substring"),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    cache_only: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(DNSQueryLog).order_by(DNSQueryLog.timestamp.desc())
    if domain:
        stmt = stmt.where(DNSQueryLog.domain.contains(domain))
    if cache_only:
        stmt = stmt.where(DNSQueryLog.cache_hit == True)  # noqa: E712
    stmt = stmt.offset(offset).limit(limit)
    rows = (await session.exec(stmt)).all()
    return rows


@router.delete("/queries", status_code=204)
async def clear_dns_queries(session: AsyncSession = Depends(get_session)):
    await session.exec(delete(DNSQueryLog))
    await session.commit()


@router.get("/queries/stats", response_model=DNSQueryStats)
async def dns_query_stats(session: AsyncSession = Depends(get_session)):
    total = (await session.exec(select(func.count()).select_from(DNSQueryLog))).one()
    unique = (await session.exec(
        select(func.count(func.distinct(DNSQueryLog.domain))).select_from(DNSQueryLog)
    )).one()
    cache_hits = (await session.exec(
        select(func.count()).select_from(DNSQueryLog).where(DNSQueryLog.cache_hit == True)  # noqa: E712
    )).one()

    cache_hit_rate = (cache_hits / total) if total > 0 else 0.0

    top_stmt = (
        select(DNSQueryLog.domain, func.count(DNSQueryLog.id).label("count"))
        .group_by(DNSQueryLog.domain)
        .order_by(func.count(DNSQueryLog.id).desc())
        .limit(10)
    )
    top_rows = (await session.exec(top_stmt)).all()
    top_domains = [{"domain": row[0], "count": row[1]} for row in top_rows]

    one_hour_ago = datetime.now(tz=timezone.utc) - timedelta(hours=1)
    queries_last_hour = (await session.exec(
        select(func.count())
        .select_from(DNSQueryLog)
        .where(DNSQueryLog.timestamp >= one_hour_ago)
    )).one()

    return DNSQueryStats(
        total_queries=total,
        unique_domains=unique,
        cache_hit_rate=cache_hit_rate,
        top_domains=top_domains,
        queries_last_hour=queries_last_hour,
    )


# ── DNS Test ──────────────────────────────────────────────────────────────────

async def _resolve_plain(domain: str, server: Optional[str] = None) -> tuple[list[str], int]:
    start = time.monotonic()
    try:
        if server:
            try:
                import dns.resolver  # type: ignore
                resolver = dns.resolver.Resolver(configure=False)
                resolver.nameservers = [server]
                answer = resolver.resolve(domain, "A")
                ips = [str(rdata) for rdata in answer]
                elapsed = int((time.monotonic() - start) * 1000)
                return ips, elapsed
            except ImportError:
                pass

        loop = asyncio.get_event_loop()
        infos = await loop.getaddrinfo(domain, None, type=socket.SOCK_STREAM)
        ips = list({info[4][0] for info in infos})
        elapsed = int((time.monotonic() - start) * 1000)
        return ips, elapsed
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        raise RuntimeError(str(exc)) from exc


def _parse_dns_a_records(data: bytes, txid: bytes) -> list[str]:
    import struct
    if len(data) < 12 or data[:2] != txid:
        return []
    flags = struct.unpack('>H', data[2:4])[0]
    if not (flags & 0x8000):
        return []
    rcode = flags & 0x000F
    if rcode != 0:
        raise RuntimeError(f'DNS RCODE={rcode}')
    qdcount = struct.unpack('>H', data[4:6])[0]
    ancount = struct.unpack('>H', data[6:8])[0]
    offset = 12
    for _ in range(qdcount):
        while offset < len(data):
            n = data[offset]
            if n & 0xC0 == 0xC0:
                offset += 2
                break
            offset += 1
            if n == 0:
                break
            offset += n
        offset += 4
    ips = []
    for _ in range(ancount):
        if offset >= len(data):
            break
        if data[offset] & 0xC0 == 0xC0:
            offset += 2
        else:
            while offset < len(data):
                n = data[offset]
                offset += 1
                if n == 0:
                    break
                offset += n
        if offset + 10 > len(data):
            break
        rtype, _, _, rdlen = struct.unpack('>HHIH', data[offset:offset + 10])
        offset += 10
        if rtype == 1 and rdlen == 4 and offset + 4 <= len(data):
            ips.append('.'.join(str(b) for b in data[offset:offset + 4]))
        offset += rdlen
    return ips


async def _resolve_via_xray(domain: str) -> tuple[list[str], int]:
    """Send DNS query to xray DNS inbound — all DNS rules apply."""
    import os
    from app.config import settings as app_cfg
    dns_port = app_cfg.dns_port
    start = time.monotonic()

    try:
        import dns.resolver  # type: ignore
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = ['127.0.0.1']
        resolver.port = dns_port
        resolver.timeout = 5
        resolver.lifetime = 5
        answer = resolver.resolve(domain, 'A')
        ips = [str(rdata) for rdata in answer]
        return ips, int((time.monotonic() - start) * 1000)
    except ImportError:
        pass

    txid = os.urandom(2)
    header = txid + b'\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00'
    question = b''
    for label in domain.rstrip('.').split('.'):
        lb = label.encode()
        question += bytes([len(lb)]) + lb
    question += b'\x00\x00\x01\x00\x01'
    packet = header + question

    loop = asyncio.get_running_loop()
    future: asyncio.Future[bytes] = loop.create_future()

    class _Proto(asyncio.DatagramProtocol):
        def datagram_received(self, data, addr):
            if not future.done():
                future.set_result(data)
        def error_received(self, exc):
            if not future.done():
                future.set_exception(exc)

    transport, _ = await loop.create_datagram_endpoint(
        _Proto, remote_addr=('127.0.0.1', dns_port)
    )
    try:
        transport.sendto(packet)
        response = await asyncio.wait_for(asyncio.shield(future), timeout=5.0)
    finally:
        transport.close()

    ips = _parse_dns_a_records(response, txid)
    return ips, int((time.monotonic() - start) * 1000)


async def _resolve_doh(domain: str, server: str) -> tuple[list[str], int]:
    start = time.monotonic()
    if not server.startswith("http"):
        server = f"https://{server}/dns-query"
    elif not server.endswith("/dns-query") and "/dns-query" not in server:
        server = server.rstrip("/") + "/dns-query"

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(
            server,
            params={"name": domain, "type": "A"},
            headers={"Accept": "application/dns-json"},
        )
        resp.raise_for_status()
        data = resp.json()

    answers = data.get("Answer", [])
    ips = [a["data"] for a in answers if a.get("type") == 1]
    elapsed = int((time.monotonic() - start) * 1000)
    return ips, elapsed


@router.post("/test-xray", response_model=DNSTestResult)
async def test_dns_via_xray(body: DNSTestRequest, session: AsyncSession = Depends(get_session)):
    from app.config import settings as app_cfg
    domain = body.domain.strip().rstrip('.')
    server_label = f'xray:{app_cfg.dns_port}'
    try:
        queried_at = datetime.now(timezone.utc)
        ips, latency = await _resolve_via_xray(domain)

        await asyncio.sleep(0.4)

        cutoff = queried_at - timedelta(seconds=1)
        log_entry = (await session.exec(
            select(DNSQueryLog)
            .where(DNSQueryLog.domain == domain)
            .where(DNSQueryLog.timestamp >= cutoff)
            .order_by(DNSQueryLog.id.desc())
            .limit(1)
        )).first()

        if log_entry:
            server_label = log_entry.server_used

        return DNSTestResult(resolved_ips=ips, latency_ms=latency, server_used=server_label)
    except Exception as exc:
        return DNSTestResult(resolved_ips=[], latency_ms=0, server_used=server_label, error=str(exc))


@router.post("/test", response_model=DNSTestResult)
async def test_dns(body: DNSTestRequest):
    domain = body.domain.strip().rstrip(".")
    server = (body.server or "").strip()

    if server:
        from urllib.parse import urlparse
        import ipaddress
        host = urlparse(server).hostname if server.startswith("http") else server
        if host:
            try:
                ip = ipaddress.ip_address(host)
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    raise HTTPException(400, "DNS server must not be a private address")
            except ValueError:
                pass

    is_doh = server.startswith("https://") or server.startswith("http://")

    try:
        if is_doh:
            ips, latency = await _resolve_doh(domain, server)
            server_used = server
        else:
            ips, latency = await _resolve_plain(domain, server or None)
            server_used = server or "system"

        return DNSTestResult(
            resolved_ips=ips,
            latency_ms=latency,
            server_used=server_used,
        )
    except Exception as exc:
        return DNSTestResult(
            resolved_ips=[],
            latency_ms=0,
            server_used=server or "system",
            error=str(exc),
        )
