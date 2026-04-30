"""Device management: CRUD, scan, bulk policy update."""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
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
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Device).order_by(Device.last_seen.desc())
    if online_only:
        stmt = stmt.where(Device.is_online == True)  # noqa: E712
    if policy:
        stmt = stmt.where(Device.routing_policy == policy)
    return list((await session.exec(stmt)).all())


@router.get("/{device_id}", response_model=DeviceRead)
async def get_device(device_id: int, session: AsyncSession = Depends(get_session)):
    dev = await session.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "Device not found")
    return dev


@router.patch("/{device_id}", response_model=DeviceRead)
async def update_device(
    device_id: int, data: DeviceUpdate, session: AsyncSession = Depends(get_session)
):
    dev = await session.get(Device, device_id)
    if not dev:
        raise HTTPException(404, "Device not found")
    for k, v in data.model_dump(exclude_unset=True).items():
        setattr(dev, k, v)
    session.add(dev)
    await session.commit()
    await session.refresh(dev)
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
    body: DeviceBulkUpdate, session: AsyncSession = Depends(get_session)
):
    for did in body.device_ids:
        dev = await session.get(Device, did)
        if dev:
            dev.routing_policy = body.routing_policy
            session.add(dev)
    await session.commit()


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
