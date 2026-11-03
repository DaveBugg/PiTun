"""The `xui` deployment row that makes a server show "x-ui configured" and offers
its uninstall.

A panel PiTun installs gets that row from the deploy job. A panel that arrives
any other way (an `xui://` address, a servers import, a restore) is a
registration only, so its server looked like a plain VPS: no badge and no way
to uninstall the panel. This creates the row for those too, and removes it when
the registration goes.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import ServerDeployment, XuiServer

logger = logging.getLogger(__name__)

PROTOCOL = "xui"


def _config(xs: XuiServer) -> str:
    return json.dumps({"domain": xs.domain, "mode": xs.mode, "xui_server_id": xs.id})


async def ensure_xui_deployment(session: AsyncSession, xs: XuiServer) -> bool:
    """Make sure the server of `xs` has an `xui` deployment row. Returns True
    when one was added. Does not commit; `xs.id` must be set (flush first)."""
    existing = (await session.exec(
        select(ServerDeployment)
        .where(ServerDeployment.server_id == xs.server_id)
        .where(ServerDeployment.protocol == PROTOCOL)
    )).first()
    if existing is not None:
        return False
    session.add(ServerDeployment(
        server_id=xs.server_id, protocol=PROTOCOL, config_json=_config(xs), status="deployed",
    ))
    return True


async def drop_xui_deployment(session: AsyncSession, server_id: Optional[int]) -> None:
    """Remove the `xui` deployment row of a server whose panel registration is gone."""
    if server_id is None:
        return
    for dep in (await session.exec(
        select(ServerDeployment)
        .where(ServerDeployment.server_id == server_id)
        .where(ServerDeployment.protocol == PROTOCOL)
    )).all():
        await session.delete(dep)


async def backfill() -> int:
    """Give every registered panel its deployment row (panels imported before
    this existed). Cheap and idempotent; run at startup."""
    from app.database import get_async_engine

    added = 0
    async with AsyncSession(get_async_engine()) as s:
        for xs in (await s.exec(select(XuiServer))).all():
            if await ensure_xui_deployment(s, xs):
                added += 1
        if added:
            await s.commit()
            logger.info("Marked %d imported panel(s) as configured on their server", added)
    return added
