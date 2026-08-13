"""Device management: CRUD, scan, bulk policy update."""
import logging
from typing import List, Optional

from fastapi import BackgroundTasks, APIRouter, Depends, HTTPException, Query
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import Device
from app.schemas import DeviceBulkUpdate, DeviceRead, DeviceScanResult, DeviceUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/devices", tags=["devices"])


@router.get("", response_model=List[DeviceRead])
async def list_devices(
    online_only: bool = Query(False),
    policy: Optional[str] = Query(None, description="Filter by routing_policy"),
    routing_set_id: Optional[str] = Query(
        None,
        description=(
            "Filter by routing_set_id. Pass a numeric id to list members "
            "of that set, or the literal string 'null' to list unassigned "
            "(global-only) devices."
        ),
    ),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Device).order_by(Device.last_seen.desc())
    if online_only:
        stmt = stmt.where(Device.is_online == True)  # noqa: E712
    if policy:
        stmt = stmt.where(Device.routing_policy == policy)
    if routing_set_id is not None:
        if routing_set_id == "null":
            stmt = stmt.where(Device.routing_set_id.is_(None))
        else:
            try:
                sid = int(routing_set_id)
            except ValueError:
                raise HTTPException(
                    400, "routing_set_id must be an integer or the literal 'null'"
                )
            stmt = stmt.where(Device.routing_set_id == sid)
    return list((await session.exec(stmt)).all())


@router.get("/{device_id}", response_model=DeviceRead)
async def get_device(device_id: int, session: AsyncSession = Depends(get_session)):
    dev = await session.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "Device not found")
    return dev


@router.patch("/{device_id}", response_model=DeviceRead)
async def update_device(
    device_id: int, data: DeviceUpdate,
    background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)
):
    dev = await session.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "Device not found")

    patch = data.model_dump(exclude_unset=True)

    # A DHCP reservation is only meaningful as a valid IPv4 address; "" clears
    # it. Validated here rather than at apply time so a typo is rejected while
    # the operator is looking at the form, not silently dropped later when the
    # router config is generated.
    if "dhcp_reserved_ip" in patch:
        raw = (patch["dhcp_reserved_ip"] or "").strip()
        if raw:
            import ipaddress
            try:
                ipaddress.IPv4Address(raw)
            except ValueError:
                raise HTTPException(
                    400, f"'{raw}' is not a valid IPv4 address for a DHCP reservation",
                )
            # Two devices on one address makes dnsmasq refuse to start, which
            # takes DHCP down for the whole LAN. There is a unique index
            # behind this, but an IntegrityError surfaces as a 500 — say which
            # device already holds it instead.
            clash = (await session.exec(
                select(Device).where(
                    Device.dhcp_reserved_ip == raw, Device.id != device_id,
                )
            )).first()
            if clash:
                raise HTTPException(
                    400,
                    f"{raw} is already reserved for "
                    f"{clash.name or clash.hostname or clash.mac}. Two devices "
                    f"sharing a reserved address stops the DHCP server from "
                    f"starting at all.",
                )
            patch["dhcp_reserved_ip"] = raw
        else:
            patch["dhcp_reserved_ip"] = None

    # Validate target routing set exists (if assigning, not unassigning).
    if "routing_set_id" in patch and patch["routing_set_id"] is not None:
        from app.models import RoutingSet
        target = await session.get(RoutingSet, patch["routing_set_id"])
        if target is None:
            raise HTTPException(
                404, f"Routing set {patch['routing_set_id']} not found"
            )

    # exclude+set conflict guard: excluded devices bypass TPROXY entirely
    # at the nftables layer (their MAC goes into `bypass_mac` which
    # `return`s before any per-set redirect fires). A set assignment on
    # such a device is silently dead. Surface a clear 400 instead of
    # the silent-no-effect trap. The UI already greys out the Set
    # dropdown for excluded devices; this is defence-in-depth for
    # direct API access.
    final_policy = patch.get("routing_policy", dev.routing_policy)
    final_set_id = patch.get("routing_set_id", dev.routing_set_id)
    if final_policy == "exclude" and final_set_id is not None:
        raise HTTPException(
            400,
            (
                "Device cannot be both routing_policy='exclude' AND assigned "
                "to a routing set — excluded devices bypass TPROXY entirely, "
                "so any set rules would silently never apply. Either change "
                "the policy first, or pass routing_set_id=null."
            ),
        )

    routing_membership_changed = (
        "routing_set_id" in patch and patch["routing_set_id"] != dev.routing_set_id
    )

    for k, v in patch.items():
        setattr(dev, k, v)
    session.add(dev)
    await session.commit()
    await session.refresh(dev)

    # Single-device reassignment changes which set the device's traffic
    # falls into → nftables MAC sets need to be re-rendered. routing_policy
    # changes already trigger their own reload path (see system.py), so
    # we only need to handle the routing_set_id case here.
    if routing_membership_changed:
        from app.api.routing_sets import _auto_reload_dataplane
        await _auto_reload_dataplane(session, background_tasks)

    return dev


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: int, session: AsyncSession = Depends(get_session)):
    dev = await session.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "Device not found")
    await session.delete(dev)
    await session.commit()


@router.post("/bulk-policy", status_code=204)
async def bulk_update_policy(
    body: DeviceBulkUpdate,
    background_tasks: BackgroundTasks, session: AsyncSession = Depends(get_session)
):
    membership_changes = 0
    for did in body.device_ids:
        dev = await session.get(Device, did)
        if not dev:
            continue
        dev.routing_policy = body.routing_policy
        # Excluded devices bypass TPROXY → any set assignment is dead
        # weight. Auto-null the set_id to keep the two states
        # consistent (mirrors the UI greying-out the Set dropdown for
        # excluded rows). User can re-assign later if they un-exclude.
        if body.routing_policy == "exclude" and dev.routing_set_id is not None:
            dev.routing_set_id = None
            membership_changes += 1
        session.add(dev)
    await session.commit()

    # If any device lost its set membership, re-render nftables so the
    # affected MACs disappear from the per-set redirect.
    if membership_changes > 0:
        from app.api.routing_sets import _auto_reload_dataplane
        await _auto_reload_dataplane(session, background_tasks)


@router.post("/scan", response_model=DeviceScanResult)
async def scan_devices():
    from app.core.device_scanner import scan_and_update_devices
    result = await scan_and_update_devices()
    return DeviceScanResult(**result)


@router.post("/reset-all-policies", status_code=204)
async def reset_all_policies(session: AsyncSession = Depends(get_session)):
    devices = (await session.exec(select(Device))).all()
    for dev in devices:
        dev.routing_policy = "default"
        session.add(dev)
    await session.commit()
