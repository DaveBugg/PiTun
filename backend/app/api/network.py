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

from app.database import get_session
from fastapi import Depends
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


@router.get("/interfaces")
def get_interfaces() -> dict:
    """Physical NICs plus whether this box could act as a full router.

    `router_capable` is the gate the UI uses: router mode needs a WAN and a
    LAN, so it is only offered on hardware with two or more real NICs.
    Virtual/container/tunnel interfaces are filtered out, so the count means
    what it says.
    """
    ifaces = network_config.list_interfaces()
    return {
        "items": ifaces,
        "count": len(ifaces),
        "router_capable": len(ifaces) >= 2,
    }


@router.get("/router-mode")
async def router_mode_status(session=Depends(get_session)) -> dict:
    """Configured mode plus what the dataplane is actually running."""
    from app.core import router_mode
    return await router_mode.status(session)


@router.post("/router-mode/confirm")
async def confirm_router_mode(session=Depends(get_session)) -> dict:
    """Tell the watchdog the network still works, cancelling the auto-revert.

    Deliberately a separate action rather than something the apply implies:
    the point is that a human confirms from a machine that can still reach the
    panel. An apply that confirms itself proves nothing.
    """
    from app.core import router_watchdog
    ok = await router_watchdog.confirm(session)
    return {"confirmed": ok}


@router.get("/router-mode/pending")
async def router_mode_pending(session=Depends(get_session)) -> dict:
    """Seconds remaining before an unconfirmed apply is rolled back."""
    from app.core import router_watchdog
    from datetime import datetime, timezone
    deadline = await router_watchdog.pending(session)
    if deadline is None:
        return {"pending": False, "seconds_left": 0}
    left = (deadline - datetime.now(timezone.utc)).total_seconds()
    return {
        "pending": True,
        "deadline": deadline.isoformat(),
        "seconds_left": max(0, int(left)),
    }


@router.get("/router-mode/diagnose")
async def router_mode_diagnose() -> dict:
    """Read the WAN counters and explain them.

    Exists because the two rules the uplink depends on — DHCP replies and the
    ICMP that PMTU discovery needs — fail silently. Without this the operator
    is left with "the internet is broken" and no way to tell which.
    """
    from app.core import router_mode
    return {"findings": await router_mode.diagnose_wan()}


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


async def _refuse_in_router_mode(session) -> None:
    """Block host-network edits while PiTun is the router.

    These endpoints rewrite the default route and the manager's persistent
    config. In gateway mode that only affects the box itself. In router mode
    the box IS the route for every LAN client, so one click from a Settings
    tab left open since before the switch takes the whole network off the
    internet — and the change survives a reboot. Router mode owns the uplink
    through its own WAN settings; there is no reason for both paths to be live.
    """
    from sqlmodel import select as _sel
    from app.models import Settings as _S
    row = (await session.exec(_sel(_S).where(_S.key == "operating_mode"))).first()
    if row and row.value == "router":
        raise HTTPException(
            409,
            "This box is running as a router, so its uplink is managed under "
            "Settings → Network → Internet connection. Changing the host route "
            "here would cut every LAN client off. Switch back to gateway mode "
            "first if you really need this.",
        )


@router.post("/apply")
async def apply_changes(body: ApplyBody, session=Depends(get_session)) -> dict:
    """Apply gateway and/or DNS changes.

    Captures a backup of the current config FIRST so /rollback can
    return to this state at any time. Apply itself uses ``ip route
    replace`` + /etc/resolv.conf rewrite for immediate effect, plus
    edits the persistent manager config (interfaces / nmconnection)
    so the change survives reboot.
    """
    await _refuse_in_router_mode(session)
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
async def rollback(
    body: Optional[RollbackBody] = None, session=Depends(get_session),
) -> dict:
    """Restore a backup.

    Default (no body or empty backup_id) → newest backup. Explicit
    id rolls back to that specific snapshot — useful for going
    further back than the immediately preceding state.

    `body` is Optional at the FastAPI signature level too — `POST`
    with no body was returning 422 ("Field required") until v1.3.5
    even though the docstring promised it would work. The "Rollback"
    button in the UI sends a body-less POST, so this 422 was a real
    UX paper cut.
    """
    await _refuse_in_router_mode(session)
    backup_id = body.backup_id if body is not None else None
    try:
        backup = network_apply.rollback(backup_id)
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
