"""Whole-box configuration backup / restore.

We already export Nodes, Servers, Routing and UA templates individually; this
is the "one file that rebuilds the box" bundle on top of those — Settings, DNS
rules, balancer groups, node circles, devices and subscriptions included, so a
reinstall (or a move to new hardware) doesn't mean re-entering every screen.

Two deliberate differences from the usual "just upsert it" restore:

* **Secrets are opt-in** (`include_secrets=false` by default), matching the
  Servers export. A backup you can hand to someone for debugging shouldn't leak
  node UUIDs and subscription URLs by default.
* **Restore is preview-then-commit**, the same shape as the routing import. A
  restore rewrites config the whole LAN depends on, so the operator gets to see
  the counts and conflicts before anything is written.

Runtime/derived tables are intentionally NOT in the bundle: DNS query logs,
system metrics, events and jobs are history (huge, worthless in a restore),
and users are credentials, not config. Panel-side state (x-ui clients, chain
clients, deployments) is owned by the remote panels and re-synced from them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import APP_VERSION
from app.database import get_session
from app.models import (
    BalancerGroup,
    Device,
    DNSRule,
    Node,
    NodeCircle,
    RoutingRule,
    RoutingSet,
    Settings as DBSettings,
    Subscription,
    UserAgentTemplate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system/backup", tags=["backup"])

BUNDLE_KIND = "pitun-config-backup"
BUNDLE_VERSION = 1

# Per-table secret columns, blanked unless `include_secrets=true`. Keep this in
# sync when a model gains a credential field — a missed entry silently leaks.
# `reality_pbk` is the peer's PUBLIC key and stays — redacting it would break
# a restored node for no benefit.
_SECRET_FIELDS: Dict[str, tuple[str, ...]] = {
    "nodes": ("uuid", "password", "wg_private_key", "wg_preshared_key"),
    "subscriptions": ("url",),
}

# Section name → (model, order-by column). Order matters on restore only in
# that routing sets must exist before rules that reference them.
_SECTIONS: List[tuple[str, Any, Any]] = [
    ("settings", DBSettings, DBSettings.key),
    ("subscriptions", Subscription, Subscription.id),
    ("nodes", Node, Node.id),
    ("routing_sets", RoutingSet, RoutingSet.id),
    ("routing_rules", RoutingRule, RoutingRule.order),
    ("dns_rules", DNSRule, DNSRule.id),
    ("balancer_groups", BalancerGroup, BalancerGroup.id),
    ("node_circles", NodeCircle, NodeCircle.id),
    ("devices", Device, Device.id),
    ("ua_templates", UserAgentTemplate, UserAgentTemplate.id),
]


def _row_to_dict(row: Any, section: str, include_secrets: bool) -> Dict[str, Any]:
    data = row.model_dump(mode="json")
    if not include_secrets:
        for field in _SECRET_FIELDS.get(section, ()):
            if data.get(field):
                data[field] = ""
    return data


@router.get("")
async def export_backup(
    include_secrets: bool = Query(
        False,
        description="Include node credentials and subscription URLs. Off by "
                    "default so a shared backup doesn't leak them.",
    ),
    session: AsyncSession = Depends(get_session),
):
    """Download the whole configuration as one JSON bundle."""
    sections: Dict[str, List[Dict[str, Any]]] = {}
    for name, model, order_col in _SECTIONS:
        rows = (await session.exec(select(model).order_by(order_col))).all()
        sections[name] = [_row_to_dict(r, name, include_secrets) for r in rows]

    payload = {
        "kind": BUNDLE_KIND,
        "version": BUNDLE_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pitun_version": APP_VERSION,
        "secrets_included": include_secrets,
        "counts": {k: len(v) for k, v in sections.items()},
        "sections": sections,
    }
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": f'attachment; filename="pitun-backup-{stamp}.json"',
        },
    )


class RestoreRequest(BaseModel):
    bundle: Dict[str, Any]
    # "merge" adds/updates rows and leaves everything else alone.
    # "replace" additionally deletes rows of the restored sections that the
    # bundle doesn't contain — a true "make the box look like this file".
    mode: str = "merge"
    # Restore only these sections (empty = all sections present in the bundle).
    sections: Optional[List[str]] = None


class SectionPlan(BaseModel):
    section: str
    incoming: int
    existing: int
    would_add: int
    would_update: int
    would_delete: int


class RestorePlan(BaseModel):
    ok: bool
    kind: Optional[str] = None
    bundle_version: Optional[int] = None
    pitun_version: Optional[str] = None
    exported_at: Optional[str] = None
    secrets_included: bool = False
    mode: str = "merge"
    plan: List[SectionPlan] = []
    warnings: List[str] = []


def _validate_envelope(bundle: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(bundle, dict) or bundle.get("kind") != BUNDLE_KIND:
        raise HTTPException(400, "Not a PiTun config backup (kind mismatch)")
    version = bundle.get("version")
    if version != BUNDLE_VERSION:
        raise HTTPException(
            400, f"Unsupported backup version {version!r} (this build reads v{BUNDLE_VERSION})"
        )
    sections = bundle.get("sections")
    if not isinstance(sections, dict):
        raise HTTPException(400, "Backup has no 'sections' object")
    return sections


def _natural_key(section: str, row: Dict[str, Any]) -> Any:
    """Identity used to match an incoming row against an existing one.

    `settings` is keyed by its unique `key` column; everything else by id,
    which is what the bundle carries and what cross-section references
    (routing_rules.set_id, node_circles.node_ids, …) point at.
    """
    return row.get("key") if section == "settings" else row.get("id")


async def _build_plan(
    session: AsyncSession, sections: Dict[str, Any], mode: str,
    only: Optional[List[str]],
) -> tuple[List[SectionPlan], List[str]]:
    plan: List[SectionPlan] = []
    warnings: List[str] = []

    known = {name for name, _, _ in _SECTIONS}
    for unknown in set(sections) - known:
        warnings.append(f"Section '{unknown}' is not known to this build — it will be ignored.")

    for name, model, order_col in _SECTIONS:
        if name not in sections:
            continue
        if only and name not in only:
            continue
        incoming = sections.get(name) or []
        if not isinstance(incoming, list):
            warnings.append(f"Section '{name}' is not a list — ignored.")
            continue

        existing_rows = (await session.exec(select(model))).all()
        existing_keys = {
            (r.key if name == "settings" else r.id) for r in existing_rows
        }
        incoming_keys = {_natural_key(name, r) for r in incoming if isinstance(r, dict)}

        would_update = len(incoming_keys & existing_keys)
        would_add = len(incoming_keys - existing_keys)
        would_delete = len(existing_keys - incoming_keys) if mode == "replace" else 0

        plan.append(SectionPlan(
            section=name, incoming=len(incoming), existing=len(existing_rows),
            would_add=would_add, would_update=would_update, would_delete=would_delete,
        ))

    if not sections.get("nodes") and sections.get("node_circles"):
        warnings.append(
            "Bundle restores node circles but no nodes — circle membership may "
            "point at ids that don't exist here."
        )
    return plan, warnings


@router.post("/preview", response_model=RestorePlan)
async def preview_restore(
    req: RestoreRequest, session: AsyncSession = Depends(get_session),
):
    """Report what a restore would change, without writing anything."""
    sections = _validate_envelope(req.bundle)
    if req.mode not in ("merge", "replace"):
        raise HTTPException(400, "mode must be 'merge' or 'replace'")

    plan, warnings = await _build_plan(session, sections, req.mode, req.sections)
    if not req.bundle.get("secrets_included", False):
        warnings.append(
            "This backup was exported WITHOUT secrets — node credentials and "
            "subscription URLs are blank and will not overwrite existing ones."
        )
    return RestorePlan(
        ok=True,
        kind=req.bundle.get("kind"),
        bundle_version=req.bundle.get("version"),
        pitun_version=req.bundle.get("pitun_version"),
        exported_at=req.bundle.get("exported_at"),
        secrets_included=bool(req.bundle.get("secrets_included", False)),
        mode=req.mode,
        plan=plan,
        warnings=warnings,
    )


@router.post("/restore", response_model=RestorePlan)
async def commit_restore(
    req: RestoreRequest, session: AsyncSession = Depends(get_session),
):
    """Apply the bundle. Run `/preview` first — this writes."""
    sections = _validate_envelope(req.bundle)
    if req.mode not in ("merge", "replace"):
        raise HTTPException(400, "mode must be 'merge' or 'replace'")

    plan, warnings = await _build_plan(session, sections, req.mode, req.sections)
    secrets_included = bool(req.bundle.get("secrets_included", False))

    for name, model, _order in _SECTIONS:
        if name not in sections:
            continue
        if req.sections and name not in req.sections:
            continue
        incoming = sections.get(name) or []
        if not isinstance(incoming, list):
            continue

        existing_rows = (await session.exec(select(model))).all()
        by_key = {(r.key if name == "settings" else r.id): r for r in existing_rows}
        seen: set = set()

        for raw in incoming:
            if not isinstance(raw, dict):
                continue
            key = _natural_key(name, raw)
            seen.add(key)
            data = dict(raw)
            # A secret-less backup must not blank out working credentials.
            if not secrets_included:
                for field in _SECRET_FIELDS.get(name, ()):
                    data.pop(field, None)
            row = by_key.get(key)
            if row is None:
                try:
                    session.add(model(**data))
                except TypeError as exc:  # unknown column from a newer build
                    warnings.append(f"{name}: skipped a row this build can't read ({exc})")
            else:
                for field, value in data.items():
                    if field == "id":
                        continue
                    if hasattr(row, field):
                        setattr(row, field, value)
                session.add(row)

        if req.mode == "replace":
            for key, row in by_key.items():
                if key not in seen:
                    await session.delete(row)

    await session.commit()
    logger.info(
        "Config restore committed (mode=%s, sections=%s)",
        req.mode, ", ".join(p.section for p in plan),
    )

    # Config the dataplane reads just changed under xray's feet — re-apply.
    try:
        from app.api.system import _apply_nftables, _load_settings_map, _regenerate_and_write
        from app.core.xray import xray_manager
        if xray_manager.is_running:
            await _regenerate_and_write(session)
            await _apply_nftables(session, await _load_settings_map(session))
            await xray_manager.reload()
    except Exception as exc:  # noqa: BLE001 — restore already succeeded
        warnings.append(f"Config restored, but re-applying the dataplane failed: {exc}")
        logger.warning("Restore: dataplane re-apply failed: %s", exc)

    from app.core.events import record_event
    await record_event(
        category="config.restored",
        severity="warning",
        title="Configuration restored from backup",
        details=(
            f"Mode '{req.mode}'. Sections: "
            + ", ".join(f"{p.section} (+{p.would_add}/~{p.would_update}/-{p.would_delete})"
                        for p in plan)
        ),
    )
    return RestorePlan(
        ok=True, kind=req.bundle.get("kind"), bundle_version=req.bundle.get("version"),
        pitun_version=req.bundle.get("pitun_version"), exported_at=req.bundle.get("exported_at"),
        secrets_included=secrets_included, mode=req.mode, plan=plan, warnings=warnings,
    )
