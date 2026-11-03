"""Domain statistics: browse what the network looks up, clean it up, and turn a
selection of domains (or the IPs they resolved to) into routing rules."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import or_
from sqlmodel import delete, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.routing import _auto_reload_xray, _validate_action, _validate_routing_set_id
from app.core import domain_stats as ds
from app.database import get_session
from app.models import DomainStat, RoutingRule, Settings as DBSettings

router = APIRouter(prefix="/domain-stats", tags=["domain-stats"])

MAX_RULE_VALUES = 5000
_SORTS = {
    "hits": DomainStat.hits, "last_seen": DomainStat.last_seen,
    "first_seen": DomainStat.first_seen, "domain": DomainStat.domain,
}
# second-level labels that are part of a public suffix (co.uk, com.au, ...)
_COMPOUND = {"co", "com", "org", "net", "gov", "edu", "ac", "or", "ne", "go"}


def parent_domain(domain: str) -> str:
    """`a.b.example.com` -> `example.com`; `x.example.co.uk` -> `example.co.uk`.
    A heuristic, not the public-suffix list: good enough to suggest a rule."""
    parts = domain.split(".")
    if len(parts) <= 2:
        return domain
    if len(parts[-1]) == 2 and parts[-2] in _COMPOUND and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _filters(q: Optional[str], min_hits: int, since_days: Optional[int]):
    conds = []
    if q and q.strip():
        needle = q.strip().lower()
        conds.append(or_(DomainStat.domain.contains(needle, autoescape=True), DomainStat.ips.contains(needle, autoescape=True)))
    if min_hits > 1:
        conds.append(DomainStat.hits >= min_hits)
    if since_days:
        conds.append(DomainStat.last_seen >= datetime.now(timezone.utc) - timedelta(days=since_days))
    return conds


class StatRow(BaseModel):
    domain: str
    hits: int
    first_seen: datetime
    last_seen: datetime
    ips: List[str]


def _row(r: DomainStat) -> StatRow:
    try:
        ips = json.loads(r.ips or "[]")
    except ValueError:
        ips = []
    return StatRow(domain=r.domain, hits=r.hits, first_seen=r.first_seen, last_seen=r.last_seen, ips=ips)


async def _log_level_ok(session: AsyncSession) -> bool:
    row = (await session.exec(select(DBSettings).where(DBSettings.key == "log_level"))).first()
    return ((row.value if row else "warning") or "warning").lower() in ("info", "debug")


@router.get("")
async def list_stats(
    q: Optional[str] = None,
    sort: Literal["hits", "last_seen", "first_seen", "domain"] = "hits",
    order: Literal["asc", "desc"] = "desc",
    min_hits: int = Query(1, ge=1),
    since_days: Optional[int] = Query(None, ge=1, le=3650),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    await ds.flush()          # what the page shows should include the last minute
    conds = _filters(q, min_hits, since_days)
    base = select(DomainStat)
    count = select(func.count()).select_from(DomainStat)
    for c in conds:
        base, count = base.where(c), count.where(c)
    col = _SORTS[sort]
    base = base.order_by(col.asc() if order == "asc" else col.desc(), DomainStat.id.asc())
    rows = (await session.exec(base.offset(offset).limit(limit))).all()
    return {
        "items": [_row(r) for r in rows],
        "total": (await session.exec(count)).one(),
        "summary": await ds.summary(session),
        "config": await ds.get_config(session),
        "log_level_ok": await _log_level_ok(session),
    }


class ConfigBody(BaseModel):
    enabled: Optional[bool] = None
    retention_days: Optional[int] = Field(None, ge=0, le=3650)
    max_rows: Optional[int] = Field(None, ge=100, le=1_000_000)


@router.put("/config")
async def update_config(body: ConfigBody, session: AsyncSession = Depends(get_session)):
    cfg = await ds.set_config(session, **body.model_dump(exclude_unset=True))
    if body.max_rows is not None or body.retention_days is not None:
        await ds.prune(session, retention_days=int(cfg["retention_days"]), max_rows=int(cfg["max_rows"]))
    return cfg


# ── cleaning ─────────────────────────────────────────────────────────────────

class PruneBody(BaseModel):
    older_than_days: Optional[int] = Field(None, ge=1, le=3650)
    keep_top: Optional[int] = Field(None, ge=0, le=1_000_000)
    max_bytes: Optional[int] = Field(None, ge=1024)
    domains: Optional[List[str]] = None
    everything: bool = False


@router.post("/prune")
async def prune_stats(body: PruneBody, session: AsyncSession = Depends(get_session)):
    """Remove rows by hand. Exactly one criterion per call: everything, a list
    of domains, rows older than N days, keep only the top N by hits, or shrink
    to about `max_bytes`."""
    await ds.flush()
    chosen = sum([body.everything, bool(body.domains), body.older_than_days is not None,
                  body.keep_top is not None, body.max_bytes is not None])
    if chosen != 1:
        raise HTTPException(422, "Choose exactly one of: everything, domains, older_than_days, keep_top, max_bytes")
    removed = 0
    if body.everything:
        removed = (await session.exec(delete(DomainStat))).rowcount or 0
        await session.commit()
    elif body.domains:
        names = [d.strip().lower() for d in body.domains if d and d.strip()][:MAX_RULE_VALUES]
        removed = (await session.exec(delete(DomainStat).where(DomainStat.domain.in_(names)))).rowcount or 0
        await session.commit()
    elif body.older_than_days is not None:
        removed = await ds.prune(session, retention_days=body.older_than_days)
    elif body.keep_top is not None:
        if body.keep_top == 0:
            removed = (await session.exec(delete(DomainStat))).rowcount or 0
            await session.commit()
        else:
            removed = await ds.prune(session, max_rows=body.keep_top)
    else:
        s = await ds.summary(session)
        if s["approx_bytes"] > body.max_bytes and s["rows"]:
            per_row = max(s["approx_bytes"] / s["rows"], 1)
            target = max(int(body.max_bytes / per_row), 1)
            removed = await ds.prune(session, max_rows=target)
    return {"removed": removed, "summary": await ds.summary(session)}


# ── rules from domains / IPs ─────────────────────────────────────────────────

class RuleFromStatsBody(BaseModel):
    domains: Optional[List[str]] = None
    # Or: everything matching these filters (so "all 40 results for 'youtube'"
    # does not need 40 ids sent over).
    query: Optional[str] = None
    min_hits: int = Field(1, ge=1)
    since_days: Optional[int] = Field(None, ge=1, le=3650)

    use: Literal["domains", "ips"] = "domains"
    use_parent: bool = False
    mode: Literal["new", "append"] = "new"
    rule_id: Optional[int] = None
    name: Optional[str] = Field(None, max_length=120)
    action: str = "proxy"
    routing_set_id: Optional[int] = None


@router.post("/rules")
async def create_rules(
    body: RuleFromStatsBody, background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
):
    """Turn selected domains (or the IPs they resolved to) into a routing rule:
    a new one, or appended to an existing domain / dst_ip rule."""
    rule_type = "domain" if body.use == "domains" else "dst_ip"

    if body.domains:
        rows = (await session.exec(
            select(DomainStat).where(DomainStat.domain.in_([d.strip().lower() for d in body.domains][:MAX_RULE_VALUES]))
        )).all()
    elif body.query is not None:
        q = select(DomainStat)
        for c in _filters(body.query, body.min_hits, body.since_days):
            q = q.where(c)
        rows = (await session.exec(q.order_by(DomainStat.hits.desc()).limit(MAX_RULE_VALUES))).all()
    else:
        raise HTTPException(422, "Give `domains` or a `query`")

    if body.use == "domains":
        values = [parent_domain(r.domain) if body.use_parent else r.domain for r in rows]
    else:
        values = []
        for r in rows:
            try:
                values.extend(json.loads(r.ips or "[]"))
            except ValueError:
                pass
    seen, uniq = set(), []
    for v in values:
        k = v.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(v)
    if not uniq:
        raise HTTPException(422, "Nothing to add" + (" — none of those domains has a recorded IP" if body.use == "ips" else ""))

    if body.mode == "append":
        if body.rule_id is None:
            raise HTTPException(422, "rule_id is required to append")
        rule = await session.get(RoutingRule, body.rule_id)
        if rule is None:
            raise HTTPException(404, "Rule not found")
        if rule.rule_type != rule_type:
            raise HTTPException(409, f"That rule matches {rule.rule_type}, not {rule_type}")
        existing = [v.strip() for v in (rule.match_value or "").split(",") if v.strip()]
        have = {v.lower().removeprefix("domain:") for v in existing}
        added = [v for v in uniq if v.lower() not in have]
        rule_id = rule.id          # read before commit expires the row
        rule.match_value = ",".join(existing + added)
        session.add(rule)
        await session.commit()
        await _auto_reload_xray(session, background_tasks)
        return {"mode": "append", "rule_id": rule_id, "added": len(added), "already_present": len(uniq) - len(added)}

    await _validate_action(session, body.action)
    await _validate_routing_set_id(session, body.routing_set_id)
    label = "Domains" if body.use == "domains" else "IPs"
    name = (body.name or "").strip() or f"{label}: {uniq[0]}" + (f" +{len(uniq) - 1}" if len(uniq) > 1 else "")
    rule = RoutingRule(
        name=name, enabled=True, rule_type=rule_type, match_value=",".join(uniq),
        action=body.action, order=100, routing_set_id=body.routing_set_id,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    await _auto_reload_xray(session, background_tasks)
    return {"mode": "new", "rule_id": rule.id, "added": len(uniq), "already_present": 0}
