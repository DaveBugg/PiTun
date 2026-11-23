"""Per-install identity.

A short random id stored in Settings, generated once. Chain inbounds on
shared x-ui panels carry it in their remark (`[pitun:<id>]`) so two
PiTun installs driving the same VPS can tell their own rows from a
foreign install's — see the cross-box trap in xui_chain.
"""
from __future__ import annotations

import re
import uuid
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Settings

INSTALL_ID_KEY = "install_id"

_MARK_RE = re.compile(r"\[pitun:([0-9a-f]{6,16})\]")


async def get_install_id(session: AsyncSession) -> str:
    """Get-or-create this install's id (12 hex chars). Commits."""
    row = (
        await session.exec(select(Settings).where(Settings.key == INSTALL_ID_KEY))
    ).first()
    if row and row.value:
        return row.value
    value = uuid.uuid4().hex[:12]
    session.add(Settings(key=INSTALL_ID_KEY, value=value))
    await session.commit()
    return value


def mark_remark(remark: str, install_id: str) -> str:
    """Append the ownership mark to an inbound remark (idempotent)."""
    if not install_id or _MARK_RE.search(remark):
        return remark
    return f"{remark} [pitun:{install_id}]"


def remark_owner(remark: str) -> Optional[str]:
    """The install id embedded in a remark, or None for legacy rows."""
    m = _MARK_RE.search(remark or "")
    return m.group(1) if m else None


def strip_mark(remark: str) -> str:
    """Remark without the ownership mark — for equality checks."""
    return _MARK_RE.sub("", remark or "").rstrip()
