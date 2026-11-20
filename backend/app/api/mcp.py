"""MCP endpoint (/api/mcp). Mounted without the panel's login: MCP clients
authenticate with their own tokens (Settings -> Assistant -> MCP)."""
from __future__ import annotations

import json
from types import SimpleNamespace
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlmodel.ext.asyncio.session import AsyncSession

from app.assistant import mcp_server
from app.assistant import settings as asettings
from app.database import get_session

router = APIRouter(tags=["mcp"])


def _origin_ok(request: Request) -> bool:
    """Browsers send Origin; a page on another site must not drive the box
    (DNS rebinding). Clients outside a browser send none."""
    origin = request.headers.get("origin")
    if not origin:
        return True
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    return bool(host) and urlparse(origin).netloc.lower() == host.lower()


@router.post("/mcp")
async def mcp_post(request: Request, session: AsyncSession = Depends(get_session)):
    s = await asettings.load(session)
    if not s.mcp_enabled:
        return JSONResponse({"error": "the MCP server is off (Settings -> Assistant -> MCP)"}, status_code=404)
    if not _origin_ok(request):
        return JSONResponse({"error": "origin not allowed"}, status_code=403)
    auth = request.headers.get("authorization") or ""
    token = await mcp_server.token_row(session, auth[7:].strip() if auth.lower().startswith("bearer ") else "")
    if token is None:
        return JSONResponse({"error": "missing or invalid MCP token"}, status_code=401,
                            headers={"WWW-Authenticate": 'Bearer realm="pitun-mcp"'})
    try:
        body = json.loads(await request.body() or b"null")
    except ValueError:
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}})
    # A plain snapshot: the row's attributes expire on every commit below,
    # and lazy reloads are not allowed in an async session.
    snap = SimpleNamespace(id=token.id, name=token.name, scope=token.scope, created_by=token.created_by)
    token.last_used_at = datetime.now(tz=timezone.utc)
    session.add(token)
    await session.commit()
    token = snap

    if isinstance(body, list):  # batch (older protocol versions)
        out = [r for r in [await mcp_server.handle(session, token, m) for m in body] if r is not None]
        return JSONResponse(out) if out else Response(status_code=202)
    res = await mcp_server.handle(session, token, body)
    if res is None:
        return Response(status_code=202)
    return JSONResponse(res)


@router.get("/mcp")
async def mcp_get():
    # No server-initiated stream: every answer comes back on the POST.
    return Response(status_code=405, headers={"Allow": "POST"})


@router.delete("/mcp")
async def mcp_delete():
    return Response(status_code=405, headers={"Allow": "POST"})
