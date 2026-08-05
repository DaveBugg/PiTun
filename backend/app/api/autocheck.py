"""Auto-speedtest config (singleton) + manual 'run now' trigger."""
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.schemas import AutoCheckRead, AutoCheckUpdate
from app.core.autocheck_scheduler import get_or_create_config, autocheck_scheduler

router = APIRouter(prefix="/autocheck", tags=["autocheck"])


def _with_status(cfg) -> AutoCheckRead:
    data = AutoCheckRead.model_validate(cfg)
    data.is_sweeping = autocheck_scheduler.is_sweeping
    return data


@router.get("", response_model=AutoCheckRead)
async def get_autocheck(session: AsyncSession = Depends(get_session)):
    return _with_status(await get_or_create_config(session))


@router.put("", response_model=AutoCheckRead)
async def update_autocheck(body: AutoCheckUpdate, session: AsyncSession = Depends(get_session)):
    cfg = await get_or_create_config(session)
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(cfg, k, v)
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return _with_status(cfg)


@router.post("/run")
async def run_autocheck_now(
    scope_kind: Optional[str] = None, scope_value: str = "", force: bool = False,
):
    """Kick off a sweep immediately in the background — a full sweep can take
    minutes, so we return right away rather than block the request.

    `scope_kind` overrides the saved config scope for this run (the Nodes
    "Speed All" button passes `all`); omit it to use the configured scope.
    `force=true` re-tests every node, ignoring the staleness guard — used by
    "Speed All" so a manual run always refreshes speeds."""
    if autocheck_scheduler.is_sweeping:
        return {"status": "already_running"}
    asyncio.create_task(
        autocheck_scheduler.run_sweep(scope_kind, scope_value or None, force)
    )
    return {"status": "started"}
