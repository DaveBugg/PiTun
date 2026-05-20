"""Network configuration API (v1.3.3).

Endpoints for the Settings → Network page.

Read paths:
    GET  /api/network/state           — current snapshot
    GET  /api/network/backups         — list of saved rollback points
    GET  /api/network/probe?ip=...    — pre-apply reachability check

Mutation paths (gateway / DNS only — see core/network_apply for the
scope rationale):
    POST /api/network/apply           — backup + apply gateway/DNS
    POST /api/network/rollback        — restore a backup (default: most recent)

Auth: every endpoint inherits the global auth dependency wired in
main.py via ``app.include_router(network.router, dependencies=_auth)``.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core import network_config
from app.core import network_apply

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/network", tags=["network"])


# ── Schemas ────────────────────────────────────────────────────────────────

class ApplyBody(BaseModel):
    # Both optional — empty body is rejected in core.apply (no-op
    # surfaces as a 400 with a useful message). Mixing the two is
    # the common case: "set gateway to X, leave DNS alone".
    gateway: Optional[str] = Field(default=None, description="New default-route gateway IPv4")
    dns: Optional[List[str]] = Field(default=None, description="New DNS server list (replaces existing)")


class RollbackBody(BaseModel):
    # Empty = roll back the most recent backup.
    backup_id: Optional[str] = Field(default=None, description="Specific backup id; defaults to newest")


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/state")
def get_network_state() -> dict:
    """Current host network configuration snapshot.

    Pure read, ~50ms. Safe to poll from the UI.
    """
    state = network_config.read_state()
    logger.debug(
        "network state: iface=%r manager=%r mode=%r gw=%r warnings=%d",
        state.interface, state.manager, state.mode, state.gateway,
        len(state.warnings),
    )
    return state.to_dict()


@router.get("/backups")
def get_backups() -> dict:
    """List saved rollback points, newest first."""
    items = network_apply.list_backups()
    return {"items": items, "count": len(items)}


@router.delete("/backups/{backup_id}")
def delete_backup(backup_id: str) -> dict:
    """Delete one backup. Idempotent — gone-already is treated as ok."""
    try:
        network_apply.delete_backup(backup_id)
    except network_apply.NetworkApplyError as e:
        raise HTTPException(400, detail=str(e))
    return {"ok": True, "id": backup_id}


@router.delete("/backups")
def clear_backups() -> dict:
    """Wipe every backup. UI confirms first."""
    try:
        removed = network_apply.delete_all_backups()
    except network_apply.NetworkApplyError as e:
        raise HTTPException(400, detail=str(e))
    return {"ok": True, "removed": removed}


@router.get("/probe")
def probe(ip: str = Query(..., description="Candidate gateway IPv4 address")) -> dict:
    """Quick reachability check on a candidate gateway.

    Frontend uses this BEFORE submitting an apply — refusing to even
    attempt the change if the new gateway can't be pinged saves the
    operator from a bad reapply that leaves them without internet.
    """
    try:
        return network_apply.probe_gateway(ip)
    except network_apply.NetworkApplyError as e:
        raise HTTPException(400, detail=str(e))


@router.post("/apply")
def apply_changes(body: ApplyBody) -> dict:
    """Apply gateway and/or DNS changes.

    Captures a backup of the current config FIRST so /rollback can
    return to this state at any time. Apply itself uses ``ip route
    replace`` + /etc/resolv.conf rewrite for immediate effect, plus
    edits the persistent manager config (interfaces / nmconnection)
    so the change survives reboot.
    """
    try:
        backup = network_apply.apply(
            network_apply.ApplyRequest(gateway=body.gateway, dns=body.dns),
        )
    except network_apply.NetworkApplyError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("network apply failed unexpectedly")
        raise HTTPException(500, detail=f"Internal error during apply: {e}")

    new_state = network_config.read_state()
    return {
        "ok": True,
        "backup": {
            "id": backup.id,
            "created_at": backup.created_at,
            "manager": backup.manager,
            "live_state": backup.live_state,
        },
        "new_state": new_state.to_dict(),
    }


@router.post("/rollback")
def rollback(body: RollbackBody) -> dict:
    """Restore a backup.

    Default (no body or empty backup_id) → newest backup. Explicit
    id rolls back to that specific snapshot — useful for going
    further back than the immediately preceding state.
    """
    try:
        backup = network_apply.rollback(body.backup_id)
    except network_apply.NetworkApplyError as e:
        raise HTTPException(400, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.exception("network rollback failed unexpectedly")
        raise HTTPException(500, detail=f"Internal error during rollback: {e}")

    new_state = network_config.read_state()
    return {
        "ok": True,
        "restored_from": {
            "id": backup.id,
            "created_at": backup.created_at,
            "live_state": backup.live_state,
        },
        "new_state": new_state.to_dict(),
    }
