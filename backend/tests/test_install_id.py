"""install_id: per-install identity + remark ownership marks."""
import asyncio

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.install_id import (
    get_install_id,
    mark_remark,
    remark_owner,
    strip_mark,
)
from app.database import get_async_engine


def test_get_install_id_is_stable(client):
    async def run():
        async with AsyncSession(get_async_engine()) as s:
            first = await get_install_id(s)
            assert len(first) == 12
            assert all(c in "0123456789abcdef" for c in first)
            assert await get_install_id(s) == first

    asyncio.run(run())


def test_mark_remark_roundtrip():
    marked = mark_remark("VPN-ALPHA", "abc123def456")
    assert marked == "VPN-ALPHA [pitun:abc123def456]"
    assert remark_owner(marked) == "abc123def456"
    assert strip_mark(marked) == "VPN-ALPHA"


def test_mark_remark_is_idempotent():
    once = mark_remark("VPN-ALPHA", "abc123def456")
    assert mark_remark(once, "abc123def456") == once


def test_remark_owner_handles_legacy_and_junk():
    assert remark_owner("VPN-ALPHA") is None
    assert remark_owner("") is None
    assert remark_owner("x [pitun:zz]") is None  # not hex
    assert remark_owner("x [pitun:0123456789abcdef]") == "0123456789abcdef"
