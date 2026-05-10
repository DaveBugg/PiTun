"""Server-agnostic install scripts.

Companion to `/api/servers/{id}/<protocol>-install-script` for the case
where the user wants the script *before* (or instead of) registering a
server in the Servers tab. Same generator(s), no `server_id`, generic
header.

  - `naive-install` — Caddy + naive_forwardproxy (single-tunnel)
  - `wireguard-install` — wg-quick + first peer (multi-client)

Adding a new protocol = thin wrapper here + `build_<proto>_install_script`
in `app/api/servers.py`. Hysteria-2 / XTLS would slot in similarly.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from app.api.servers import (
    build_naive_install_script,
    build_wireguard_install_script,
)

router = APIRouter(prefix="/scripts", tags=["scripts"])


@router.get("/naive-install", response_class=PlainTextResponse)
async def naive_install(
    domain: str = Query(..., description="DNS A-record pointing at the VPS"),
    email: str = Query(..., description="Let's Encrypt registration email"),
    naive_user: Optional[str] = Query(None, description="Defaults to 'pitun'"),
    naive_pass: Optional[str] = Query(None, description="Auto-generated if absent"),
    template_id: Optional[str] = Query(None, description="Decoy template id (see /api/templates)"),
    install_php: bool = Query(False, description="Provision hardened php-fpm for dynamic decoys"),
    ssh_port: Optional[int] = Query(None, description="Move SSH listener to this port (1-65535, 22=no-op)"),
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
        template_id=template_id,
        install_php=install_php,
        ssh_port=ssh_port,
    )
    return PlainTextResponse(
        content=script,
        media_type="text/x-shellscript",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/wireguard-install", response_class=PlainTextResponse)
async def wireguard_install(
    client_name: Optional[str] = Query(None, description="First peer name (defaults to 'client1')"),
    server_port: Optional[int] = Query(None, description="UDP port (default 51820)"),
    dns_1: Optional[str] = Query(None, description="Primary DNS for clients (default 1.1.1.1)"),
    dns_2: Optional[str] = Query(None, description="Secondary DNS for clients (default 1.0.0.1)"),
    allowed_ips: Optional[str] = Query(None, description="Default 0.0.0.0/0,::/0"),
    ssh_port: Optional[int] = Query(None, description="Move SSH listener to this port (1-65535, 22=no-op)"),
):
    """Server-agnostic WireGuard install bootstrap. Bootstraps the
    server (apt, sysctl, keypair, wg-quick@wg0) AND adds the first
    peer in one go. Subsequent peers can be added by re-running this
    same script with a different `CLIENT_NAME`."""
    filename = "wireguard-install.sh"
    script = build_wireguard_install_script(
        client_name=client_name,
        server_port=server_port,
        dns_1=dns_1,
        dns_2=dns_2,
        allowed_ips=allowed_ips,
        server_label=None,
        suggested_filename=filename,
        ssh_port=ssh_port,
    )
    return PlainTextResponse(
        content=script,
        media_type="text/x-shellscript",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
