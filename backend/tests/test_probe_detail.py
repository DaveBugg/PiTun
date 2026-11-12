"""An httpx error with an empty message must still say what failed."""
import httpx
import pytest


@pytest.mark.asyncio
async def test_empty_exception_text_still_gives_a_detail(monkeypatch):
    from app.core import speedtest as st

    class Boom:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def get(self, url): raise httpx.ConnectError("")

    monkeypatch.setattr(httpx, "AsyncClient", Boom)
    ok, _lat, detail = await st._probe_reachable(1, rounds=1)
    assert ok is False and "ConnectError" in detail
