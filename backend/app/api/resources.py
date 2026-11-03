"""What PiTun costs the box, and optional limits on its parts."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import proc_stats, resource_limits as rl
from app.database import get_session

router = APIRouter(prefix="/resources", tags=["resources"])


@router.get("/processes")
async def processes(refresh: bool = Query(False)):
    """xray, the backend and every PiTun container, with CPU (% of one core),
    memory and the memory limit when there is one."""
    return await proc_stats.collect(force=refresh)


class Target(BaseModel):
    mem_mb: Optional[float] = Field(None, ge=0)
    cpus: Optional[float] = Field(None, ge=0)


class LimitsBody(BaseModel):
    backend: Target = Target()
    frontend: Target = Target()
    nginx: Target = Target()
    naive: Target = Target()
    xray_mem_mb: Optional[float] = Field(None, ge=0)


def _description() -> Dict[str, Any]:
    return {
        "host": rl.host_limits(),
        "targets": {k: {"label": v["label"], "min_mb": v["min_mb"]} for k, v in rl.TARGETS.items()},
        "xray_min_mb": rl.XRAY_MIN_MB,
    }


@router.get("/limits")
async def get_limits(session: AsyncSession = Depends(get_session)):
    saved = await rl.load(session)
    try:
        effective = await rl.current()
        docker_error = None
    except Exception:  # noqa: BLE001
        effective, docker_error = {}, "Docker is not reachable from the backend"
    return {"limits": saved, "effective": effective, "docker_error": docker_error, **_description()}


@router.put("/limits")
async def put_limits(body: LimitsBody, session: AsyncSession = Depends(get_session)):
    """Save and apply. An empty / zero value means no limit. xray's memory cap
    is read when xray starts, so it needs an xray restart to take effect."""
    raw = body.model_dump()
    for target in rl.TARGETS:
        for key in ("mem_mb", "cpus"):
            if not raw[target][key]:
                raw[target][key] = None
    if not raw["xray_mem_mb"]:
        raw["xray_mem_mb"] = None
    try:
        limits = rl.validate(raw)
    except rl.LimitError as exc:
        raise HTTPException(422, detail=str(exc))
    before = await rl.load(session)
    await rl.save(session, limits)
    try:
        results = await rl.apply(limits)
        docker_error = None
    except Exception:  # noqa: BLE001
        results, docker_error = [], "Docker is not reachable from the backend"
    return {
        "limits": limits,
        "results": results,
        "docker_error": docker_error,
        "xray_restart_needed": before.get("xray_mem_mb") != limits.get("xray_mem_mb"),
    }
