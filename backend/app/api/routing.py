"""Routing rules CRUD + ARP device list."""
import asyncio
import json
import logging
import re
import subprocess
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import BalancerGroup, Node, RoutingRule
from app.schemas import ArpDevice, BulkRuleCreate, BulkRuleResult, RoutingRuleCreate, RoutingRuleRead, RoutingRuleUpdate


async def _validate_action(session: AsyncSession, action: str) -> None:
    """Validate `action` references an actual row for `node:<id>` / `balancer:<id>`.

    Orphan references previously slipped through: the user could delete
    balancer #5, then create a `balancer:5` rule, and xray would silently
    refuse to apply the rule at the next reload — visible only as a log
    warning. Fail fast at the API boundary instead.
    """
    if action.startswith("node:"):
        try:
            nid = int(action.split(":", 1)[1])
        except (IndexError, ValueError):
            raise HTTPException(400, f"Malformed action {action!r}: expected node:<id>")
        node = await session.get(Node, nid)
        if node is None:
            raise HTTPException(400, f"Action references missing node {nid}")
    elif action.startswith("balancer:"):
        try:
            bid = int(action.split(":", 1)[1])
        except (IndexError, ValueError):
            raise HTTPException(400, f"Malformed action {action!r}: expected balancer:<id>")
        bg = await session.get(BalancerGroup, bid)
        if bg is None:
            raise HTTPException(400, f"Action references missing balancer {bid}")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/routing", tags=["routing"])


async def _auto_reload_xray(session: AsyncSession) -> None:
    """Regenerate xray config and reload if running. Called after rule changes."""
    try:
        from app.core.xray import xray_manager
        if not xray_manager.is_running:
            return
        from app.api.system import _regenerate_and_write
        await _regenerate_and_write(session)
        await xray_manager.reload()
    except Exception as exc:
        logger.warning("Auto-reload after rule change failed: %s", exc)


# ── Rules CRUD ────────────────────────────────────────────────────────────────

@router.get("/rules", response_model=List[RoutingRuleRead])
async def list_rules(session: AsyncSession = Depends(get_session)):
    rules = (await session.exec(select(RoutingRule).order_by(RoutingRule.order, RoutingRule.id))).all()
    return list(rules)


@router.post("/rules", response_model=RoutingRuleRead, status_code=201)
async def create_rule(data: RoutingRuleCreate, session: AsyncSession = Depends(get_session)):
    await _validate_action(session, data.action)
    rule = RoutingRule(**data.model_dump())
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    await _auto_reload_xray(session)
    return rule


@router.get("/rules/{rule_id}", response_model=RoutingRuleRead)
async def get_rule(rule_id: int, session: AsyncSession = Depends(get_session)):
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    return rule


@router.patch("/rules/{rule_id}", response_model=RoutingRuleRead)
async def update_rule(rule_id: int, data: RoutingRuleUpdate, session: AsyncSession = Depends(get_session)):
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    patch = data.model_dump(exclude_unset=True)
    if "action" in patch and patch["action"] is not None:
        await _validate_action(session, patch["action"])
    for k, v in patch.items():
        setattr(rule, k, v)
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    await _auto_reload_xray(session)
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int, session: AsyncSession = Depends(get_session)):
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    await session.delete(rule)
    await session.commit()
    await _auto_reload_xray(session)


@router.delete("/rules", status_code=204)
async def delete_all_rules(session: AsyncSession = Depends(get_session)):
    """Delete ALL routing rules."""
    rules = (await session.exec(select(RoutingRule))).all()
    for r in rules:
        await session.delete(r)
    await session.commit()
    await _auto_reload_xray(session)


@router.post("/rules/delete-batch", status_code=204)
async def delete_batch_rules(ids: List[int], session: AsyncSession = Depends(get_session)):
    """Delete multiple rules by ID list."""
    for rid in ids:
        rule = await session.get(RoutingRule, rid)
        if rule:
            await session.delete(rule)
    await session.commit()
    await _auto_reload_xray(session)


@router.post("/rules/bulk", response_model=BulkRuleResult, status_code=201)
async def bulk_create_rules(body: BulkRuleCreate, session: AsyncSession = Depends(get_session)):
    raw = body.values.replace("\r\n", "\n").replace("\r", "\n")
    parts = []
    for line in raw.split("\n"):
        for part in line.split(","):
            val = part.strip()
            if val:
                parts.append(val)

    if not parts:
        raise HTTPException(400, "No values provided")

    match_value = ",".join(parts)
    name = f"{body.rule_type}:{body.action} ({len(parts)} values)"

    rule = RoutingRule(
        name=name,
        enabled=body.enabled,
        rule_type=body.rule_type,
        match_value=match_value,
        action=body.action,
        order=100,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    await _auto_reload_xray(session)
    return BulkRuleResult(created=len(parts), rule_ids=[rule.id])


# ── V2Ray JSON import ────────────────────────────────────────────────────────

class V2RayImportRequest(BaseModel):
    rules: List[Dict[str, Any]]
    mode: str = "as_is"
    clear_existing: bool = False


class V2RayImportResult(BaseModel):
    imported: int
    skipped: int
    rule_ids: List[int]


def _v2ray_action(tag: str, mode: str) -> Optional[str]:
    mapping = {"proxy": "proxy", "direct": "direct", "block": "block"}
    invert = {"proxy": "direct", "direct": "proxy", "block": "block"}
    source = invert if mode == "invert" else mapping
    return source.get(tag)


@router.post("/rules/import-v2ray", response_model=V2RayImportResult, status_code=201)
async def import_v2ray_rules(
    body: V2RayImportRequest,
    session: AsyncSession = Depends(get_session),
):
    if body.mode not in ("as_is", "invert"):
        raise HTTPException(400, "mode must be 'as_is' or 'invert'")

    if body.clear_existing:
        old = (await session.exec(select(RoutingRule))).all()
        for r in old:
            await session.delete(r)

    imported = 0
    skipped = 0
    rule_ids: List[int] = []

    for idx, v2rule in enumerate(body.rules):
        tag = v2rule.get("outboundTag", "")
        action = _v2ray_action(tag, body.mode)
        if not action:
            skipped += 1
            continue

        enabled = v2rule.get("enabled", True)
        remarks = v2rule.get("remarks", "")

        domains = v2rule.get("domain")
        if domains and isinstance(domains, list):
            rule = RoutingRule(
                name=remarks or f"Import #{idx+1} (domain)",
                enabled=enabled,
                rule_type="domain",
                match_value=",".join(domains),
                action=action,
                order=(idx + 1) * 10,
            )
            session.add(rule)
            await session.flush()
            rule_ids.append(rule.id)
            imported += 1

        ips = v2rule.get("ip")
        if ips and isinstance(ips, list):
            rule = RoutingRule(
                name=remarks or f"Import #{idx+1} (ip)",
                enabled=enabled,
                rule_type="dst_ip",
                match_value=",".join(ips),
                action=action,
                order=(idx + 1) * 10 + 1,
            )
            session.add(rule)
            await session.flush()
            rule_ids.append(rule.id)
            imported += 1

        port = v2rule.get("port")
        if port and isinstance(port, str):
            rule = RoutingRule(
                name=remarks or f"Import #{idx+1} (port)",
                enabled=enabled,
                rule_type="port",
                match_value=port,
                action=action,
                order=(idx + 1) * 10 + 2,
            )
            session.add(rule)
            await session.flush()
            rule_ids.append(rule.id)
            imported += 1

        if not domains and not ips and not port:
            skipped += 1

    await session.commit()
    await _auto_reload_xray(session)
    logger.info("V2Ray import: %d rules imported, %d skipped (mode=%s)", imported, skipped, body.mode)
    return V2RayImportResult(imported=imported, skipped=skipped, rule_ids=rule_ids)


@router.post("/rules/reorder", status_code=204)
async def reorder_rules(ids: List[int], session: AsyncSession = Depends(get_session)):
    """Set the order of rules by providing an ordered list of IDs."""
    for idx, rule_id in enumerate(ids):
        rule = await session.get(RoutingRule, rule_id)
        if rule:
            rule.order = idx * 10
            session.add(rule)
    await session.commit()
    await _auto_reload_xray(session)


# ── ARP devices ───────────────────────────────────────────────────────────────

@router.get("/devices", response_model=List[ArpDevice])
async def list_devices(session: AsyncSession = Depends(get_session)):
    devices = await _get_arp_table()
    rules = list((await session.exec(
        select(RoutingRule)
        .where(RoutingRule.enabled == True)
        .order_by(RoutingRule.order)
    )).all())

    for device in devices:
        device.rule_action = _match_device_rule(device, rules)

    return devices


async def _get_arp_table() -> List[ArpDevice]:
    """Parse /proc/net/arp or ip neigh output."""
    devices: List[ArpDevice] = []
    try:
        proc = await asyncio.create_subprocess_exec(
            "ip", "neigh",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        mac_re = re.compile(r"(\d+\.\d+\.\d+\.\d+)\s+\S+\s+\S+\s+lladdr\s+([0-9a-f:]{17})", re.I)
        for line in stdout.decode().splitlines():
            m = mac_re.search(line)
            if m:
                devices.append(ArpDevice(ip=m.group(1), mac=m.group(2).lower()))
    except Exception:
        try:
            with open("/proc/net/arp") as f:
                lines = f.readlines()[1:]
            for line in lines:
                parts = line.split()
                if len(parts) >= 4 and parts[3] != "00:00:00:00:00:00":
                    devices.append(ArpDevice(ip=parts[0], mac=parts[3].lower()))
        except Exception:
            pass

    return devices


def _match_device_rule(device: ArpDevice, rules: List[RoutingRule]) -> Optional[str]:
    for rule in rules:
        values = [v.strip() for v in rule.match_value.split(",")]
        if rule.rule_type == "mac" and device.mac in [v.lower() for v in values]:
            return rule.action
        if rule.rule_type == "src_ip" and device.ip in values:
            return rule.action
    return None
