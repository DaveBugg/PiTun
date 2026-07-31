"""Routing sets CRUD + bulk device assignment.

A `RoutingSet` is a named group of routing rules that applies only to a
selected list of devices. The default state of PiTun has a single
global rule list applied to all traffic; this module lets the operator
overlay per-set rules on top.

Schema-level details live in `app/models.py:RoutingSet`. Runtime
plumbing (nftables fwmark + xray `attrs.mark` matching) lives in
`app/core/nftables.py` and `app/core/config_gen.py` — see those for
how a saved set actually changes traffic flow.

Endpoint summary
----------------
* `GET    /api/routing-sets/`                          — list all sets
* `POST   /api/routing-sets/`                          — create (fwmark auto-allocated)
* `GET    /api/routing-sets/{id}`                      — read one
* `PATCH  /api/routing-sets/{id}`                      — rename / reorder / re-describe
* `DELETE /api/routing-sets/{id}?cascade=move-to-global` — delete; without the
  `cascade` flag, refuses if devices or rules still reference the set
* `POST   /api/routing-sets/{id}/devices/bulk`         — assign N device IDs
  to this set in one transaction; `set_id` can be the path-id or null
  (via body `routing_set_id`) to unassign
"""
import logging
from typing import List, Literal, Optional

from fastapi import BackgroundTasks, APIRouter, Depends, HTTPException, Query
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.dataplane import dispatch_dataplane
from app.database import get_session
from app.models import Device, RoutingRule, RoutingSet
from app.schemas import (
    DeviceBulkSetAssign,
    RoutingSetCapacity,
    RoutingSetCreate,
    RoutingSetRead,
    RoutingSetUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/routing-sets", tags=["routing-sets"])


# Reserved TPROXY port range for per-set xray inbounds. Chosen at the
# very top of the 16-bit port space:
#   - Above Linux ephemeral range (default 32768..60999) so kernel
#     won't accidentally pick one of these as a source port for an
#     outbound connect().
#   - Nothing well-known or registered lives here.
#   - High enough to be visually distinct from PiTun's existing
#     listener ports (7893/7894/5353/1080/8080/8000) → easier to
#     grep in netstat output and config diffs.
#
# One port per set covers BOTH TCP and UDP (xray's dokodemo-door with
# `network: "tcp,udp"` works because Linux treats TCP and UDP as
# independent port-namespaces — a single port number can be bound by
# both protocols simultaneously). 36 ports → 36 max sets, far beyond
# any home-LAN need.
TPROXY_PORT_MIN = 65500
TPROXY_PORT_MAX = 65535
MAX_ROUTING_SETS = TPROXY_PORT_MAX - TPROXY_PORT_MIN + 1  # 36


def _host_port_free(port: int) -> bool:
    """Best-effort check: is this TCP port currently free to bind on the host?

    Used by `_allocate_tproxy_port` to skip a port that the operator
    happens to have something else listening on (homelab service in
    the 65500+ range, unlikely but possible). Without this guard
    xray would silently fail to start its per-set inbound with
    "address already in use" → silent feature break.

    Defensive: socket creation may fail in unusual environments (no
    AF_INET, sandboxed test runner) — treat any error as "assume
    free" so the test suite doesn't break in those envs. The
    backend container itself runs with host network, so this check
    sees the real listener landscape.
    """
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Intentionally NO SO_REUSEADDR. With REUSEADDR=1, two sockets
        # can bind to disjoint addresses on the same port (e.g. one
        # listens on 127.0.0.1, our probe on 0.0.0.0 succeeds anyway).
        # That hides a real conflict — when xray later tries to
        # actually start its listener on 0.0.0.0:<port>, it'd race.
        # A bare bind is the honest "is this port truly free?" test.
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False
        finally:
            s.close()
    except Exception:
        return True  # fail-open — never block allocation on env quirks


async def _allocate_tproxy_port(session: AsyncSession) -> int:
    """Return the next free TPROXY port in [TPROXY_PORT_MIN, TPROXY_PORT_MAX].

    Strategy: scan for the lowest free slot in the range. Append-only
    `MAX+1` would be slightly cheaper but doesn't behave well across
    delete-then-recreate cycles when we're near the limit. A linear
    scan over 36 ints is irrelevant cost-wise.

    Also runs a bind-probe on each candidate so we skip ports that
    are already in use by some other host-level service (avoids the
    silent "xray failed to start its inbound" failure mode).
    """
    used = set((await session.exec(select(RoutingSet.tproxy_port))).all())
    for port in range(TPROXY_PORT_MIN, TPROXY_PORT_MAX + 1):
        if port in used:
            continue
        if not _host_port_free(port):
            logger.warning(
                "Skipping TPROXY port %d for new routing set — already bound on host",
                port,
            )
            continue
        return port
    raise HTTPException(
        409,
        (
            f"Routing-set limit reached: cannot create more than "
            f"{MAX_ROUTING_SETS} sets (TPROXY port range "
            f"[{TPROXY_PORT_MIN}..{TPROXY_PORT_MAX}] is fully allocated, "
            "or those ports are bound by other services). "
            "Delete an unused set first."
        ),
    )


async def _auto_reload_dataplane(
    session: AsyncSession, background_tasks: Optional[BackgroundTasks] = None,
) -> None:
    """Re-apply BOTH layers after a routing-set / membership change.

    Per-set routing spans two layers that must stay in sync:
      1. xray config — the per-set `tproxy-set-<id>` inbound + its rules
         (regenerated by `_regenerate_and_write`).
      2. nftables — the per-set MAC set + TPROXY redirect that actually
         steers a member device's traffic INTO that inbound (applied by
         `_apply_nftables`).

    Changing only xray (the pre-1.4.0 bug) left nftables stale: the
    inbound listened on its port but no traffic was ever redirected
    there, so the device silently kept hitting the default inbound and
    the set's rules never applied. We MUST re-apply nftables too.

    Order matters: regenerate + reload xray FIRST so the per-set inbound
    is live, THEN re-apply nftables so its redirect points at a port
    that's already listening (no dropped packets in the gap).

    No-op when xray isn't running — `/system/start` will build both
    layers from scratch on the next start.
    """
    try:
        from app.core.xray import xray_manager
        if not xray_manager.is_running:
            return
        from app.api.system import (
            _regenerate_and_write, _apply_nftables, _load_settings_map,
        )
        await _regenerate_and_write(session)
        # Deferred when the caller has a response to protect: the reload
        # restarts xray, dropping every connection it carries — including
        # the browser that issued this request. `apply_dataplane` keeps
        # the xray-then-nftables order, and rebuilding nftables from
        # current DB state naturally picks up added/removed set members.
        await dispatch_dataplane(background_tasks, "reload")
    except Exception as exc:
        logger.warning("Auto-reload after routing-set change failed: %s", exc)


# Backwards-compatible alias — older call-sites in this module reference
# `_auto_reload_xray`. Keep the name working so a stray import doesn't
# break; new code should call `_auto_reload_dataplane` directly.
_auto_reload_xray = _auto_reload_dataplane


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[RoutingSetRead])
async def list_routing_sets(session: AsyncSession = Depends(get_session)):
    """List all routing sets in render order."""
    rows = (await session.exec(
        select(RoutingSet).order_by(RoutingSet.order, RoutingSet.id)
    )).all()
    return list(rows)


@router.post("", response_model=RoutingSetRead, status_code=201)
async def create_routing_set(
    body: RoutingSetCreate, session: AsyncSession = Depends(get_session)
):
    """Create a new routing set. fwmark is auto-allocated."""
    # Pre-flight uniqueness check so the user gets a clean 400 instead of
    # a 500 from the DB UNIQUE constraint hitting.
    existing = (await session.exec(
        select(RoutingSet).where(RoutingSet.name == body.name)
    )).first()
    if existing is not None:
        raise HTTPException(400, f"A routing set named {body.name!r} already exists")

    tproxy_port = await _allocate_tproxy_port(session)
    rs = RoutingSet(
        name=body.name,
        description=body.description,
        order=body.order,
        tproxy_port=tproxy_port,
    )
    session.add(rs)
    await session.commit()
    await session.refresh(rs)
    logger.info(
        "Created routing set %r (id=%d, tproxy_port=%d)",
        rs.name, rs.id, rs.tproxy_port,
    )
    return rs


@router.get("/capacity", response_model=RoutingSetCapacity)
async def get_capacity(session: AsyncSession = Depends(get_session)):
    """Report how many routing sets fit into the TPROXY port range.

    Frontend uses this to disable the "+ New set" button when the
    limit is reached, and to surface a counter ("5/36 used") in the
    Routing page header. Mounted under `/capacity` (and declared
    BEFORE `/{set_id}`) so FastAPI's path-matching reads it as a
    static path, not as a `set_id="capacity"` lookup.
    """
    used = (await session.exec(select(RoutingSet))).all()
    used_count = len(list(used))
    return RoutingSetCapacity(
        used=used_count,
        maximum=MAX_ROUTING_SETS,
        available=MAX_ROUTING_SETS - used_count,
    )


@router.get("/{set_id}", response_model=RoutingSetRead)
async def get_routing_set(set_id: int, session: AsyncSession = Depends(get_session)):
    rs = await session.get(RoutingSet, set_id)
    if rs is None:
        raise HTTPException(404, "Routing set not found")
    return rs


@router.patch("/{set_id}", response_model=RoutingSetRead)
async def update_routing_set(
    set_id: int,
    body: RoutingSetUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Rename, reorder, or update description. fwmark is immutable."""
    rs = await session.get(RoutingSet, set_id)
    if rs is None:
        raise HTTPException(404, "Routing set not found")

    patch = body.model_dump(exclude_unset=True)

    # If renaming, enforce uniqueness ourselves for a clean 400.
    if "name" in patch and patch["name"] != rs.name:
        clash = (await session.exec(
            select(RoutingSet).where(RoutingSet.name == patch["name"])
        )).first()
        if clash is not None:
            raise HTTPException(
                400, f"A routing set named {patch['name']!r} already exists"
            )

    for key, value in patch.items():
        setattr(rs, key, value)
    session.add(rs)
    await session.commit()
    await session.refresh(rs)
    return rs


@router.delete("/{set_id}", status_code=204)
async def delete_routing_set(
    set_id: int,
    background_tasks: BackgroundTasks,
    cascade: Optional[Literal["move-to-global", "delete"]] = Query(
        None,
        description=(
            "How to handle dependents:\n"
            "  * 'move-to-global' — NULL routing_set_id on dependent devices "
            "AND rules (devices fall back to global-only, rules become global).\n"
            "  * 'delete' — DELETE the dependent rules outright; devices are "
            "still NULL'd (a physical device row is never deleted — it just "
            "becomes unassigned).\n"
            "If absent and dependents exist, the call returns 409."
        ),
    ),
    session: AsyncSession = Depends(get_session),
):
    """Delete a routing set.

    Default behaviour is **refuse** if any Device or RoutingRule still
    references the set — the caller has to either reassign them or pass
    `?cascade=move-to-global` (keep the rules, move them to Global) or
    `?cascade=delete` (drop the rules with the set). Devices are always
    moved to Global, never deleted, since they're physical LAN entries
    that would just re-appear on the next scan.
    """
    rs = await session.get(RoutingSet, set_id)
    if rs is None:
        raise HTTPException(404, "Routing set not found")

    dep_devices = (await session.exec(
        select(Device).where(Device.routing_set_id == set_id)
    )).all()
    dep_rules = (await session.exec(
        select(RoutingRule).where(RoutingRule.routing_set_id == set_id)
    )).all()

    if (dep_devices or dep_rules) and cascade not in ("move-to-global", "delete"):
        raise HTTPException(
            409,
            (
                f"Routing set {rs.name!r} still has {len(dep_devices)} device(s) "
                f"and {len(dep_rules)} rule(s) assigned. Reassign them first, or "
                "pass ?cascade=move-to-global (keep rules → Global) or "
                "?cascade=delete (drop the rules)."
            ),
        )

    # Devices always fall back to Global (never deleted — they're physical).
    for dev in dep_devices:
        dev.routing_set_id = None
        session.add(dev)

    deleted_rules = 0
    if cascade == "delete":
        for r in dep_rules:
            await session.delete(r)
            deleted_rules += 1
    else:  # move-to-global (or no dependents → no-op)
        for r in dep_rules:
            r.routing_set_id = None
            session.add(r)

    await session.delete(rs)
    await session.commit()
    logger.info(
        "Deleted routing set id=%d (%d devices → global, %d rules %s)",
        set_id, len(dep_devices), len(dep_rules),
        "DELETED" if cascade == "delete" else "moved to global",
    )
    await _auto_reload_dataplane(session, background_tasks)


# ── Bulk device assignment ────────────────────────────────────────────────────

@router.post("/devices/bulk", status_code=204)
async def bulk_assign_devices(
    body: DeviceBulkSetAssign,
    background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)
):
    """Bulk-assign N devices to a routing set (or unassign with null).

    Body shape: ``{"device_ids": [5, 7, 12], "routing_set_id": 2}``
    or ``{"device_ids": [5, 7], "routing_set_id": null}`` to unassign.

    Note: Mounted under `/routing-sets/devices/bulk` rather than
    `/routing-sets/{id}/devices/bulk` so the unassign-to-null case has
    a natural URL (no fake "set 0" or "set null" path segment).
    """
    if not body.device_ids:
        raise HTTPException(400, "device_ids must not be empty")

    # Validate target set exists (if non-null) before touching anything.
    if body.routing_set_id is not None:
        target = await session.get(RoutingSet, body.routing_set_id)
        if target is None:
            raise HTTPException(404, f"Routing set {body.routing_set_id} not found")

    updated = 0
    missing: list[int] = []
    excluded: list[int] = []  # excluded-policy devices reject set assignment
    for did in body.device_ids:
        dev = await session.get(Device, did)
        if dev is None:
            missing.append(did)
            continue
        # Excluded devices bypass TPROXY at the nftables layer, so a set
        # assignment would silently never apply. Reject the bulk so the
        # caller knows nothing happened — unassign (set_id=null) is
        # always allowed because it clears stale state.
        if body.routing_set_id is not None and dev.routing_policy == "exclude":
            excluded.append(did)
            continue
        dev.routing_set_id = body.routing_set_id
        session.add(dev)
        updated += 1

    if missing or excluded:
        # Roll back the partial assignment — we don't want a half-applied
        # bulk action when the caller passed a typo or a conflicting
        # policy combo.
        await session.rollback()
        if missing:
            raise HTTPException(404, f"Device id(s) not found: {missing}")
        raise HTTPException(
            400,
            (
                f"Device id(s) have routing_policy='exclude': {excluded}. "
                "Excluded devices bypass TPROXY entirely, so a set assignment "
                "would have no effect. Change their policy first, or omit "
                "them from the bulk-assign list."
            ),
        )

    await session.commit()
    logger.info(
        "Bulk-assigned %d device(s) to routing set %s",
        updated, body.routing_set_id,
    )
    await _auto_reload_dataplane(session, background_tasks)
