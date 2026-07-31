"""Routing rules CRUD + ARP device list."""
import asyncio
import json
import logging
import re
import subprocess
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.dataplane import dispatch_dataplane
from app.database import get_session
from app.models import BalancerGroup, Node, RoutingRule, RoutingSet
from app.schemas import (
    ArpDevice, BulkRuleCreate, BulkRuleResult, RoutingRuleCreate, RoutingRuleRead, RoutingRuleUpdate,
    RoutingPortRule, RoutingImportDestination, RoutingImportPreviewRequest, RoutingImportPreviewResult,
    RoutingImportConflict, RoutingImportCommitRequest, RoutingImportCommitResult,
)


async def _validate_routing_set_id(
    session: AsyncSession, set_id: Optional[int]
) -> None:
    """Reject rule saves that reference a non-existent RoutingSet.

    NULL is always valid (it means "global rule"). Otherwise we require
    the target set to actually exist so the user can't orphan a rule by
    pointing it at id=9999.
    """
    if set_id is None:
        return
    rs = await session.get(RoutingSet, set_id)
    if rs is None:
        raise HTTPException(400, f"Rule references missing routing set {set_id}")


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


def _validate_geo_tags(rule_type: str, match_value: str) -> None:
    """Reject rule saves that reference a `geosite:X` or `geoip:X` tag
    absent from the currently loaded `.dat` files.

    Two shapes are checked:
      * `rule_type` ∈ {"geosite", "geoip"} — entire match_value is a
        comma-separated list of plain tags (no `geosite:` prefix).
      * `rule_type == "domain"` — match_value may contain mixed
        entries like `domain:foo,geosite:cn,geoip:ru`; we extract
        only the geo-prefixed ones and validate those.

    Fail-open semantics: if the corresponding tag cache is empty
    (file missing / parse error / not yet loaded), we DON'T reject.
    Better to let xray be the validator-of-last-resort than to brick
    the rule-CRUD surface on a startup edge case. The Layout banner
    written from `last_xray_validation_error` (also v1.2.7) covers
    the missed cases.
    """
    if not match_value:
        return
    from app.core.geo import AVAILABLE_GEOSITE_TAGS, AVAILABLE_GEOIP_TAGS

    # Collect (kind, raw_tag) pairs to validate.
    to_check: list[tuple[str, str]] = []
    parts = [p.strip() for p in match_value.split(",") if p.strip()]
    if rule_type == "geosite":
        for p in parts:
            to_check.append(("geosite", p))
    elif rule_type == "geoip":
        for p in parts:
            to_check.append(("geoip", p))
    elif rule_type == "domain":
        for p in parts:
            if p.startswith("geosite:"):
                to_check.append(("geosite", p[len("geosite:"):]))
            elif p.startswith("geoip:"):
                to_check.append(("geoip", p[len("geoip:"):]))
            # `domain:...`, plain hostnames, regex matches — not our job
    else:
        return

    if not to_check:
        return

    missing: list[str] = []
    for kind, tag in to_check:
        tag_lc = tag.lower()
        cache = AVAILABLE_GEOSITE_TAGS if kind == "geosite" else AVAILABLE_GEOIP_TAGS
        if not cache:
            # Cache empty for this kind — fail-open (see docstring).
            return
        if tag_lc not in cache:
            missing.append(f"{kind}:{tag}")

    if missing:
        joined = ", ".join(missing)
        raise HTTPException(
            400,
            (
                f"Tag(s) not found in current .dat: {joined}. "
                "Switch the geo profile (Settings → GeoData → Update) to one that "
                "includes these categories, or pick a different tag."
            ),
        )

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/routing", tags=["routing"])


async def _auto_reload_xray(
    session: AsyncSession, background_tasks: Optional[BackgroundTasks] = None,
) -> None:
    """Re-apply BOTH dataplane layers after a rule change.

    xray alone is not enough. `mac` rules never reach the xray config at
    all (config_gen skips them — MAC is an L2 property nftables owns), and
    `dst_ip + direct` rules become an nftables bypass set. Reloading only
    xray meant a new MAC bypass silently did nothing, and — worse — a
    DELETED one kept bypassing the proxy until the next restart.

    Order is load-bearing and matches `routing_sets._auto_reload_dataplane`:
    xray first so its inbounds are live, nftables second so redirects point
    at a listening port.

    The restart runs AFTER the response when `background_tasks` is given: a
    reload drops every connection xray carries, including the operator's
    own browser session when the UI is reached through the box, which used
    to kill the very request that asked for the change. See
    core/dataplane.py.

    No-op when xray isn't running — `/system/start` builds both layers.
    """
    try:
        from app.core.xray import xray_manager
        if not xray_manager.is_running:
            return
        from app.api.system import _regenerate_and_write
        await _regenerate_and_write(session)
        await dispatch_dataplane(background_tasks, "reload")
    except Exception as exc:
        logger.warning("Auto-reload after rule change failed: %s", exc)


# ── Rules CRUD ────────────────────────────────────────────────────────────────

@router.get("/rules", response_model=List[RoutingRuleRead])
async def list_rules(
    routing_set_id: Optional[str] = None,
    session: AsyncSession = Depends(get_session),
):
    """List routing rules.

    `routing_set_id` accepts:
      * numeric id  → only rules belonging to that set
      * `"null"`    → only global rules (set_id IS NULL)
      * omitted     → all rules (legacy behaviour, what callers without
        per-set awareness expect)
    """
    stmt = select(RoutingRule).order_by(RoutingRule.order, RoutingRule.id)
    if routing_set_id is not None:
        if routing_set_id == "null":
            stmt = stmt.where(RoutingRule.routing_set_id.is_(None))
        else:
            try:
                sid = int(routing_set_id)
            except ValueError:
                raise HTTPException(
                    400, "routing_set_id must be an integer or the literal 'null'"
                )
            stmt = stmt.where(RoutingRule.routing_set_id == sid)
    rules = (await session.exec(stmt)).all()
    return list(rules)


# ── Auto-disabled rules surface (since v1.2.7) ───────────────────────────────
#
# When `_regenerate_and_write` self-heals after xray rejects the config
# due to a missing geosite/geoip tag, it disables the offending
# RoutingRule rows and appends entries to the `auto_disabled_rules`
# Settings key. The frontend reads `/auto-disabled` to render a banner
# above the rules table.

@router.get("/auto-disabled")
async def list_auto_disabled(session: AsyncSession = Depends(get_session)):
    """Return the JSON list of rules auto-disabled by the self-heal
    pass. Empty list when there's nothing to surface.
    """
    from app.models import Settings as DBSettings
    row = (await session.exec(
        select(DBSettings).where(DBSettings.key == "auto_disabled_rules")
    )).first()
    if not row or not row.value:
        return {"items": []}
    try:
        items = json.loads(row.value)
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []
    return {"items": items}


@router.post("/auto-disabled/{rule_id}/re-enable", status_code=204)
async def re_enable_auto_disabled(
    rule_id: int,
    background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)
):
    """Re-enable a rule and remove it from the auto-disabled inbox.
    Forces the user to acknowledge they understand the rule will
    re-trigger validation; if the underlying tag is still missing in
    the .dat, the next config write will self-disable it again.
    """
    from app.models import Settings as DBSettings
    rule = await session.get(RoutingRule, rule_id)
    if rule is not None:
        rule.enabled = True
        session.add(rule)

    row = (await session.exec(
        select(DBSettings).where(DBSettings.key == "auto_disabled_rules")
    )).first()
    if row and row.value:
        try:
            items = json.loads(row.value)
            if isinstance(items, list):
                items = [it for it in items if it.get("rule_id") != rule_id]
                row.value = json.dumps(items)
                session.add(row)
        except Exception:
            pass

    await session.commit()
    await _auto_reload_xray(session, background_tasks)


@router.delete("/auto-disabled/{rule_id}", status_code=204)
async def delete_auto_disabled(
    rule_id: int,
    background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)
):
    """Permanently delete an auto-disabled rule and remove from the
    inbox. The "this rule will never come back" path for users who
    don't intend to switch geo profiles.
    """
    from app.models import Settings as DBSettings
    rule = await session.get(RoutingRule, rule_id)
    if rule is not None:
        await session.delete(rule)

    row = (await session.exec(
        select(DBSettings).where(DBSettings.key == "auto_disabled_rules")
    )).first()
    if row and row.value:
        try:
            items = json.loads(row.value)
            if isinstance(items, list):
                items = [it for it in items if it.get("rule_id") != rule_id]
                row.value = json.dumps(items)
                session.add(row)
        except Exception:
            pass

    await session.commit()
    await _auto_reload_xray(session, background_tasks)


@router.post("/auto-disabled/dismiss", status_code=204)
async def dismiss_auto_disabled_all(session: AsyncSession = Depends(get_session)):
    """Clear the entire auto-disabled inbox without touching the rule
    rows. The rules stay disabled in the DB; the banner just goes
    away. Use when the admin has decided "I'll deal with this later"
    and doesn't want a persistent yellow banner.
    """
    from app.models import Settings as DBSettings
    row = (await session.exec(
        select(DBSettings).where(DBSettings.key == "auto_disabled_rules")
    )).first()
    if row:
        row.value = ""
        session.add(row)
        await session.commit()


@router.post("/rules", response_model=RoutingRuleRead, status_code=201)
async def create_rule(data: RoutingRuleCreate,
    background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    await _validate_action(session, data.action)
    _validate_geo_tags(data.rule_type, data.match_value or "")
    await _validate_routing_set_id(session, data.routing_set_id)
    rule = RoutingRule(**data.model_dump())
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    await _auto_reload_xray(session, background_tasks)
    return rule


@router.get("/rules/{rule_id}", response_model=RoutingRuleRead)
async def get_rule(rule_id: int, session: AsyncSession = Depends(get_session)):
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    return rule


@router.patch("/rules/{rule_id}", response_model=RoutingRuleRead)
async def update_rule(rule_id: int, data: RoutingRuleUpdate,
    background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    patch = data.model_dump(exclude_unset=True)
    if "action" in patch and patch["action"] is not None:
        await _validate_action(session, patch["action"])
    if "routing_set_id" in patch:
        await _validate_routing_set_id(session, patch["routing_set_id"])
    # Validate geo tags against the resulting (post-patch) shape.
    # Whichever of (rule_type, match_value) the patch supplies, fall
    # back to the existing row for the other side.
    new_rule_type = patch.get("rule_type", rule.rule_type)
    new_match_value = patch.get("match_value", rule.match_value)
    if new_rule_type in {"geosite", "geoip", "domain"}:
        _validate_geo_tags(new_rule_type, new_match_value or "")
    for k, v in patch.items():
        setattr(rule, k, v)
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    await _auto_reload_xray(session, background_tasks)
    return rule


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: int,
    background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    rule = await session.get(RoutingRule, rule_id)
    if not rule:
        raise HTTPException(404, "Rule not found")
    await session.delete(rule)
    await session.commit()
    await _auto_reload_xray(session, background_tasks)


@router.delete("/rules", status_code=204)
async def delete_all_rules(
    background_tasks: BackgroundTasks,session: AsyncSession = Depends(get_session)):
    """Delete ALL routing rules."""
    rules = (await session.exec(select(RoutingRule))).all()
    for r in rules:
        await session.delete(r)
    await session.commit()
    await _auto_reload_xray(session, background_tasks)


@router.post("/rules/delete-batch", status_code=204)
async def delete_batch_rules(ids: List[int],
    background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    """Delete multiple rules by ID list."""
    for rid in ids:
        rule = await session.get(RoutingRule, rid)
        if rule:
            await session.delete(rule)
    await session.commit()
    await _auto_reload_xray(session, background_tasks)


@router.post("/rules/bulk", response_model=BulkRuleResult, status_code=201)
async def bulk_create_rules(body: BulkRuleCreate,
    background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
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
    await _auto_reload_xray(session, background_tasks)
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
    background_tasks: BackgroundTasks,
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
    await _auto_reload_xray(session, background_tasks)
    logger.info("V2Ray import: %d rules imported, %d skipped (mode=%s)", imported, skipped, body.mode)
    return V2RayImportResult(imported=imported, skipped=skipped, rule_ids=rule_ids)


# ── Set-aware native import (preview + commit) ───────────────────────────────
#
# The V2Ray path above is flat + lossy (no mac/geosite/node: actions, no set
# membership). The native path round-trips every RoutingRule field and lets
# the operator choose a destination (global / existing set / new set) plus
# resolve duplicate/conflict situations so the resulting ruleset stays sane
# for xray's first-match-wins evaluation.


def _norm_match(match_value: str) -> str:
    """Canonical form of a match_value for equality: comma-split, trim,
    lowercase, dedupe, sort. So `B.com, a.com` == `a.com,b.com`."""
    toks = sorted({t.strip().lower() for t in (match_value or "").split(",") if t.strip()})
    return ",".join(toks)


def _imp_key(rule_type: str, match_value: str) -> str:
    return f"{rule_type}|{_norm_match(match_value)}"


async def _action_importable(session: AsyncSession, action: str) -> bool:
    """Non-raising variant of `_validate_action` — True if the action is
    usable on THIS box (proxy/direct/block always; node:/balancer: only
    when the target row exists)."""
    if action in ("proxy", "direct", "block"):
        return True
    if action.startswith("node:"):
        try:
            return (await session.get(Node, int(action.split(":", 1)[1]))) is not None
        except (ValueError, IndexError):
            return False
    if action.startswith("balancer:"):
        try:
            return (await session.get(BalancerGroup, int(action.split(":", 1)[1]))) is not None
        except (ValueError, IndexError):
            return False
    return False


async def _filter_importable(
    session: AsyncSession, rules: List[RoutingPortRule]
) -> tuple[List[RoutingPortRule], int]:
    """Drop rules that can't apply on this box (action references a node/
    balancer that doesn't exist here, or a geosite/geoip tag absent from
    the loaded .dat). Returns (keepable, dropped_count). Keeps the import
    resilient instead of 400-ing the whole batch on one stray rule."""
    keep: List[RoutingPortRule] = []
    dropped = 0
    for inc in rules:
        if not await _action_importable(session, inc.action):
            dropped += 1
            continue
        try:
            _validate_geo_tags(inc.rule_type, inc.match_value or "")
        except HTTPException:
            dropped += 1
            continue
        keep.append(inc)
    return keep, dropped


def _analyze_import(existing: List[RoutingRule], incoming: List[RoutingPortRule]):
    """Bucket `incoming` against `existing` destination rules.

    Returns (will_add, identical, conflicts, collapsed) where:
      * will_add   — RoutingPortRule with no key match in the destination
      * identical  — key match AND same action (already present → skip)
      * conflicts  — key match, DIFFERENT action: list of dicts carrying the
                     incoming rule + the existing action/ids for resolution
      * collapsed  — count of later incoming rows sharing a key already seen
                     in the file (first-wins dedupe within the import itself)
    """
    emap: Dict[str, Dict[str, Any]] = {}
    for r in existing:
        slot = emap.setdefault(_imp_key(r.rule_type, r.match_value),
                               {"action": r.action, "ids": []})
        slot["ids"].append(r.id)

    seen: set[str] = set()
    will_add: List[RoutingPortRule] = []
    identical: List[RoutingPortRule] = []
    conflicts: List[Dict[str, Any]] = []
    collapsed = 0
    for inc in incoming:
        k = _imp_key(inc.rule_type, inc.match_value)
        if k in seen:
            collapsed += 1
            continue
        seen.add(k)
        slot = emap.get(k)
        if slot is None:
            will_add.append(inc)
        elif slot["action"] == inc.action:
            identical.append(inc)
        else:
            conflicts.append({"key": k, "inc": inc,
                              "existing_action": slot["action"],
                              "existing_ids": list(slot["ids"])})
    return will_add, identical, conflicts, collapsed


async def _resolve_import_dest(
    session: AsyncSession, dest: RoutingImportDestination, *, create: bool
) -> tuple[Optional[int], str, bool, List[RoutingRule]]:
    """Resolve the destination → (set_id|None, label, exists, existing_rules).

    `create=False` (preview): a "new set" destination is reported as
    not-yet-existing with an empty rule list (we never write on preview).
    `create=True` (commit): the new set is actually created here."""
    if dest.kind == "global":
        rows = (await session.exec(
            select(RoutingRule).where(RoutingRule.routing_set_id.is_(None))
        )).all()
        return None, "Global", True, list(rows)

    # kind == "set"
    if dest.set_id is not None:
        rs = await session.get(RoutingSet, dest.set_id)
        if rs is None:
            raise HTTPException(400, f"Routing set {dest.set_id} not found")
        rows = (await session.exec(
            select(RoutingRule).where(RoutingRule.routing_set_id == rs.id)
        )).all()
        return rs.id, f"Set: {rs.name}", True, list(rows)

    name = (dest.new_set_name or "").strip()
    if not name:
        raise HTTPException(400, "new_set_name is required when creating a set")
    dup = (await session.exec(select(RoutingSet).where(RoutingSet.name == name))).first()
    if dup is not None:
        raise HTTPException(400, f"A routing set named {name!r} already exists")
    if not create:
        return None, f"New set: {name}", False, []

    from app.api.routing_sets import _allocate_tproxy_port
    port = await _allocate_tproxy_port(session)
    rs = RoutingSet(name=name, description=dest.new_set_description, tproxy_port=port, order=0)
    session.add(rs)
    await session.commit()
    await session.refresh(rs)
    logger.info("Import created routing set %r (id=%d, port=%d)", rs.name, rs.id, rs.tproxy_port)
    return rs.id, f"Set: {rs.name}", True, []


@router.post("/import/preview", response_model=RoutingImportPreviewResult)
async def import_preview(
    body: RoutingImportPreviewRequest, session: AsyncSession = Depends(get_session)
):
    """Dry-run an import: report what would be added, what's already there
    (identical), in-file duplicates, unusable rules, and action CONFLICTS
    (same match, different action) for the user to resolve. No writes."""
    keep, invalid = await _filter_importable(session, body.rules)
    set_id, label, exists, existing = await _resolve_import_dest(
        session, body.destination, create=False
    )
    will_add, identical, conflicts, collapsed = _analyze_import(existing, keep)
    return RoutingImportPreviewResult(
        destination_label=label,
        destination_exists=exists,
        total_in_file=len(body.rules),
        will_add=len(will_add),
        identical_skipped=len(identical),
        collapsed_duplicates=collapsed,
        invalid_skipped=invalid,
        conflicts=[
            RoutingImportConflict(
                key=c["key"], rule_type=c["inc"].rule_type, match_value=c["inc"].match_value,
                incoming_action=c["inc"].action, incoming_name=c["inc"].name or "",
                existing_action=c["existing_action"], existing_rule_ids=c["existing_ids"],
            )
            for c in conflicts
        ],
    )


@router.post("/import/commit", response_model=RoutingImportCommitResult, status_code=201)
async def import_commit(
    body: RoutingImportCommitRequest,
    background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)
):
    """Apply an import (append semantics). Identical rules are skipped;
    conflicts follow per-key `resolutions` ('keep' = leave existing,
    'replace' = delete existing + use incoming), defaulting to
    `default_conflict_choice`. Creates the destination set if requested."""
    keep, invalid = await _filter_importable(session, body.rules)
    set_id, label, _exists, existing = await _resolve_import_dest(
        session, body.destination, create=True
    )
    will_add, identical, conflicts, collapsed = _analyze_import(existing, keep)

    res_map = {r.key: r.choice for r in body.resolutions}
    base_order = max([r.order for r in existing], default=0)

    def _next() -> int:
        nonlocal base_order
        base_order += 10
        return base_order

    added = replaced = skipped_conflicts = 0

    for inc in will_add:
        session.add(RoutingRule(
            name=inc.name or "Imported", enabled=inc.enabled, rule_type=inc.rule_type,
            match_value=inc.match_value, action=inc.action, order=_next(), routing_set_id=set_id,
        ))
        added += 1

    for c in conflicts:
        choice = res_map.get(c["key"], body.default_conflict_choice)
        if choice == "replace":
            for rid in c["existing_ids"]:
                row = await session.get(RoutingRule, rid)
                if row is not None:
                    await session.delete(row)
            inc = c["inc"]
            session.add(RoutingRule(
                name=inc.name or "Imported", enabled=inc.enabled, rule_type=inc.rule_type,
                match_value=inc.match_value, action=inc.action, order=_next(), routing_set_id=set_id,
            ))
            replaced += 1
        else:
            skipped_conflicts += 1

    await session.commit()
    await _auto_reload_xray(session, background_tasks)
    set_name = label[len("Set: "):] if label.startswith("Set: ") else None
    logger.info(
        "Routing import → %s: +%d added, %d identical, %d replaced, %d conflicts skipped, "
        "%d invalid, %d collapsed", label, added, len(identical), replaced,
        skipped_conflicts, invalid, collapsed,
    )
    return RoutingImportCommitResult(
        set_id=set_id, set_name=set_name, added=added, skipped_identical=len(identical),
        replaced=replaced, skipped_conflicts=skipped_conflicts, invalid_skipped=invalid,
        collapsed_duplicates=collapsed,
    )


@router.post("/rules/reorder", status_code=204)
async def reorder_rules(ids: List[int],
    background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)):
    """Set the order of rules by providing an ordered list of IDs."""
    for idx, rule_id in enumerate(ids):
        rule = await session.get(RoutingRule, rule_id)
        if rule:
            rule.order = idx * 10
            session.add(rule)
    await session.commit()
    await _auto_reload_xray(session, background_tasks)


# ── ARP devices ───────────────────────────────────────────────────────────────

@router.get("/devices", response_model=List[ArpDevice])
async def list_devices(session: AsyncSession = Depends(get_session)):
    devices = await _get_arp_table()
    rules = list((await session.exec(
        select(RoutingRule)
        .where(RoutingRule.enabled == True)
        .order_by(RoutingRule.order)
    )).all())

    # v1.4: enrich ARP entries with routing-set membership (read-only).
    # Build a MAC → (set_id, set_name) map from the Device table joined
    # with RoutingSet. Single small query — Device table on a home LAN
    # is < 100 rows. The ARP view stays SCAN-driven (live `ip neigh`),
    # so a MAC that hasn't been recorded as a Device yet just has no
    # badge — no harm.
    from app.models import Device, RoutingSet
    db_devices = list((await session.exec(
        select(Device).where(Device.routing_set_id.is_not(None))
    )).all())
    set_by_id: dict[int, RoutingSet] = {}
    if db_devices:
        set_ids = {d.routing_set_id for d in db_devices if d.routing_set_id is not None}
        rs_rows = list((await session.exec(
            select(RoutingSet).where(RoutingSet.id.in_(set_ids))
        )).all())
        set_by_id = {rs.id: rs for rs in rs_rows}
    mac_to_set: dict[str, RoutingSet] = {}
    for d in db_devices:
        if d.mac and d.routing_set_id in set_by_id:
            mac_to_set[d.mac.lower()] = set_by_id[d.routing_set_id]

    for device in devices:
        device.rule_action = _match_device_rule(device, rules)
        rs = mac_to_set.get(device.mac.lower())
        if rs is not None:
            device.routing_set_id = rs.id
            device.routing_set_name = rs.name

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
