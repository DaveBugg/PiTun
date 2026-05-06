"""Server-agnostic install scripts.

Companion to `/api/servers/{id}/naive-install-script` for the case where
the user wants the script *before* (or instead of) registering a server
in the Servers tab. Same generator, no `server_id`, generic header.

Phase 1 ships only the NaiveProxy script. Phase 3 will add WireGuard /
Hysteria-2 / etc., each as another endpoint here.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from app.api.servers import build_naive_install_script

router = APIRouter(prefix="/scripts", tags=["scripts"])


@router.get("/naive-install", response_class=PlainTextResponse)
async def naive_install(
    domain: str = Query(..., description="DNS A-record pointing at the VPS"),
    email: str = Query(..., description="Let's Encrypt registration email"),
    naive_user: Optional[str] = Query(None, description="Defaults to 'pitun'"),
    naive_pass: Optional[str] = Query(None, description="Auto-generated if absent"),
):
    """Server-agnostic NaiveProxy install bootstrap.

    Identical to the per-server endpoint but without the server-name line
    in the header comment. Useful for "I don't have a VPS registered in
    PiTun yet, just give me the script".
    """
    filename = "naive-install.sh"
    script = build_naive_install_script(
        domain=domain,
        email=email,
        naive_user=naive_user,
        naive_pass=naive_pass,
        server_label=None,
        suggested_filename=filename,
    )
    return PlainTextResponse(
        content=script,
        media_type="text/x-shellscript",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
