"""REST endpoints for managing x-ui-pro / 3x-ui panels (since v1.3.0-beta.7).

Surface:
  GET    /api/xui/presets                                   list inbound presets
  GET    /api/xui/servers                                   list registered XuiServers
  POST   /api/xui/servers/import                            register from a `xui://` URI
  GET    /api/xui/servers/{id}                              detail
  DELETE /api/xui/servers/{id}                              unregister (panel untouched)
  POST   /api/xui/servers/{id}/probe                        re-test the Bearer token
  GET    /api/xui/servers/{id}/inbounds                     live list from the panel
  POST   /api/xui/servers/{id}/inbounds                     create from preset+values
  DELETE /api/xui/servers/{id}/inbounds/{inbound_id}        delete via the panel API
  POST   /api/xui/servers/{id}/inbounds/{inbound_id}/clients add client (auto pi-XXXX label)
  DELETE /api/xui/servers/{id}/inbounds/{inbound_id}/clients/{client_uuid}
  POST   /api/xui/clients/{xui_client_id}/export-node       create Node from this client

What lives here vs `app/api/servers.py`:
  * `servers.py` owns SSH-driven deploy/uninstall (`POST /servers/{id}/deploy`).
    Once a deploy emits `URI=xui://...` and the runner persists a XuiServer
    row, the rest of panel management — inbound CRUD, client creation,
    node export — happens HERE via the Bearer API.
  * No state mutation on the panel goes through SSH after install. Everything
    runs through `XuiClient` (the API wrapper) → `/panel/api/*` → panel.

Threat model: same as the rest of PiTun — LAN-only, auth-gated at the
FastAPI layer (auth dependency wired in `main.py`). The Bearer token in
the DB is plaintext (matches Server.password handling — see
SECURITY.md for the documented stance).
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.core.xui_api import XuiAPIError, XuiClient, parse_inbound_field
from app.core.xui_chain import (
    ChainCreateDraft,
    ChannelDraft,
    _exit_tag,
    _relay_tag,
    _ufw_chain_ports,
    orchestrate_add_client,
    orchestrate_create,
    orchestrate_delete,
    orchestrate_delete_channel,
    orchestrate_delete_client,
)
from app.core.uri_formatter import inbound_client_to_uri
from app.core.xui_presets import PRESETS, InboundPreset, get_preset, list_presets
from app.core.xui_uri import parse_xui_uri
from app.models import (
    ChainChannel,
    ChainClient,
    ChainClientChannel,
    Node,
    ProxyChain,
    Server,
    XuiClient as XuiClientModel,
    XuiServer,
)

logger = logging.getLogger(__name__)
from app.core.net import read_direct

# `read_direct` latches `?direct=` into a request-scoped flag, so every
# XuiClient built while serving an /xui request honours the "Direct
# connection" toggle — even endpoints that don't pass `direct` explicitly.
router = APIRouter(
    prefix="/xui", tags=["xui"], dependencies=[Depends(read_direct)],
)


# ── Schemas ─────────────────────────────────────────────────────────────────


class PresetFieldRead(BaseModel):
    name: str
    label: str
    type: str
    required: bool
    default: Optional[str] = None
    help: str = ""
    choices: Optional[List[str]] = None
    placeholder: str = ""
    # Runtime-resolved default hint: when set, the frontend pre-fills
    # the input from the matching panel attribute instead of `default`.
    # Currently supported: "panel_domain".
    default_from: Optional[str] = None


class PresetRead(BaseModel):
    id: str
    label: str
    description: str
    needs_domain: bool
    supports_reality: bool
    protocol: str
    fields: List[PresetFieldRead]


def _preset_to_read(p: InboundPreset) -> PresetRead:
    return PresetRead(
        id=p.id, label=p.label, description=p.description,
        needs_domain=p.needs_domain, supports_reality=p.supports_reality,
        protocol=p.protocol,
        fields=[
            PresetFieldRead(
                name=f.name, label=f.label, type=f.type, required=f.required,
                default=f.default, help=f.help, choices=f.choices,
                placeholder=f.placeholder, default_from=f.default_from,
            )
            for f in p.fields
        ],
    )


class XuiServerRead(BaseModel):
    id: int
    server_id: int
    server_name: str
    server_host: str
    panel_port: int
    panel_basepath: str
    panel_user: str        # exposed so the UI can render the "Open panel" link
    domain: Optional[str] = None
    mode: str
    last_check: Optional[datetime] = None
    last_check_error: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    # api_token + panel_pass intentionally omitted — read-only panel
    # access for the UI uses panel_user + a separate "show password"
    # endpoint, never bulk-listed.


class XuiServerImportBody(BaseModel):
    """Register an x-ui panel against an existing Server row.

    Either `uri` (the `xui://...` line emitted by setup-xui-server.sh's
    output) or all of `api_token / panel_port / panel_basepath /
    panel_user / panel_pass` directly.
    """
    server_id: int
    uri: Optional[str] = None
    api_token: Optional[str] = None
    panel_port: Optional[int] = None
    panel_basepath: Optional[str] = None
    panel_user: Optional[str] = None
    panel_pass: Optional[str] = None
    domain: Optional[str] = None
    mode: Optional[str] = None  # bare | xui-pro


class InboundCreateBody(BaseModel):
    preset_id: str
    values: Dict[str, Any]
    # Optional remark override — if not in `values`, comes from here.
    remark: Optional[str] = None


class ClientCreateBody(BaseModel):
    """Add a client to an existing inbound. The label is auto-generated
    `pi-XXXXXXXX` so /sync can identify PiTun-managed clients; pass
    your own only if you know what you're doing."""
    label: Optional[str] = None
    # Free-form per-protocol overrides (e.g. flow / email / subId)
    # forwarded into the panel's client object.
    extras: Dict[str, Any] = {}


# ── Helpers ─────────────────────────────────────────────────────────────────


def _xui_to_read(xs: XuiServer, srv: Server) -> XuiServerRead:
    return XuiServerRead(
        id=xs.id, server_id=xs.server_id,
        server_name=srv.name, server_host=srv.host,
        panel_port=xs.panel_port, panel_basepath=xs.panel_basepath,
        panel_user=xs.panel_user,
        domain=xs.domain, mode=xs.mode,
        last_check=xs.last_check, last_check_error=xs.last_check_error,
        created_at=xs.created_at, updated_at=xs.updated_at,
    )


def _api_base_url(xs: XuiServer, srv: Server) -> str:
    """Compose the panel's API base URL from the parts we stored.

    Bare mode:    `http://<server.host>:<panel_port><basepath>` — no
                  domain, no LE cert; setup-xui-server.sh actively
                  clears the panel's webCertFile/webCertKey so the
                  panel serves plain HTTP. PiTun is the only intended
                  caller; the api_token is the auth gate, and bare
                  mode is for Reality-only setups where the user
                  isn't exposing a fakesite anyway.
    xui-pro mode: `https://<domain>:<panel_port><basepath>` — since
                  v1.3.0-beta.7 setup-xui-server.sh wires the LE cert
                  into the panel's webCertFile / webCertKey settings,
                  so the panel itself terminates TLS with a real cert
                  on its random high port. Apex-domain nginx is a
                  separate concern (fakesite on :443).
    The XuiClient verify_tls flag stays False in xui-pro mode — the
    cert is real but SNI on the panel port may not match (some VPS
    providers' reverse DNS), so we skip strict verification.
    """
    if xs.mode == "xui-pro" and xs.domain:
        return f"https://{xs.domain}:{xs.panel_port}{xs.panel_basepath}"
    return f"http://{srv.host}:{xs.panel_port}{xs.panel_basepath}"


async def _get_xs_or_404(
    server_id: int, session: AsyncSession,
) -> tuple[XuiServer, Server]:
    xs = (await session.exec(
        select(XuiServer).where(XuiServer.id == server_id),
    )).first()
    if xs is None:
        raise HTTPException(404, detail=f"XuiServer id={server_id} not found")
    srv = await session.get(Server, xs.server_id)
    if srv is None:
        # FK should keep this from happening, but defensive.
        raise HTTPException(500, detail=f"Server id={xs.server_id} missing for xui id={server_id}")
    return xs, srv


def _build_client(*, label: str, uuid: str, extras: Dict[str, Any]) -> Dict[str, Any]:
    """Build a panel-shape client dict for vless/trojan addClient.

    Trojan inbounds expect `password` instead of `id`. The caller
    knows the inbound protocol and passes that via `extras["protocol"]`
    (popped before forwarding) — keeps the build symmetric to the
    inbound presets without leaking the protocol distinction up the
    stack."""
    proto = extras.pop("protocol", "vless")
    base: Dict[str, Any] = {
        "email": label,
        "limitIp": 0, "totalGB": 0, "expiryTime": 0,
        "enable": True, "tgId": "", "subId": "",
    }
    if proto == "trojan":
        base["password"] = extras.pop("password", None) or secrets.token_urlsafe(20)
    else:
        base["id"] = uuid
        base["flow"] = extras.pop("flow", "")
    base.update(extras)
    return base


# ── Endpoints ───────────────────────────────────────────────────────────────


@router.get("/presets", response_model=List[PresetRead])
async def list_inbound_presets():
    """Return the 6 wired-in inbound presets with their field schemas."""
    return [_preset_to_read(p) for p in list_presets()]


@router.get("/servers", response_model=List[XuiServerRead])
async def list_xui_servers(session: AsyncSession = Depends(get_session)):
    rows = (await session.exec(select(XuiServer))).all()
    out: List[XuiServerRead] = []
    for xs in rows:
        srv = await session.get(Server, xs.server_id)
        if srv is None:
            continue
        out.append(_xui_to_read(xs, srv))
    return out


async def _best_effort_policy(xs: XuiServer, srv: Server) -> None:
    """Apply the recommended xray timeouts, never failing the caller.

    A panel PiTun manages should not sit on xray's defaults (`connIdle`
    300 s kills idle pooled connections; the half-close timers cut
    streaming responses). Registration is the natural moment to fix that,
    and the merge preserves everything the operator already configured.
    """
    from app.api.system import _load_settings_map
    from app.core.xui_policy import apply_policy_to_panel

    try:
        from app.database import get_async_engine
        from sqlmodel.ext.asyncio.session import AsyncSession as _AS
        async with _AS(get_async_engine()) as s:
            settings_map = await _load_settings_map(s)
        res = await apply_policy_to_panel(
            base_url=_api_base_url(xs, srv), api_token=xs.api_token,
            panel_user=xs.panel_user, panel_pass=xs.panel_pass,
            settings_map=settings_map,
        )
        logger.info("Panel %s policy: %s", xs.id, res.detail)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Panel %s: could not apply xray policy: %s — use the "
            "apply-policy action once the panel is reachable", xs.id, exc,
        )


@router.post("/servers/import", response_model=XuiServerRead, status_code=201)
async def import_xui_server(
    body: XuiServerImportBody,
    apply_policy: bool = True,
    session: AsyncSession = Depends(get_session),
):
    """Register a panel against an existing Server row.

    Two input shapes accepted:
      1. `uri` — paste the `xui://...` line emitted by the install
         script. We parse it via xui_uri.parse_xui_uri.
      2. Discrete fields — for cases where the URI was lost / the
         user re-deploys with known values.

    The endpoint probes the panel before persisting — refuses to
    store a row whose Bearer token is already invalid. That keeps
    the UI from displaying broken servers.
    """
    srv = await session.get(Server, body.server_id)
    if srv is None:
        raise HTTPException(404, detail=f"Server id={body.server_id} not found")

    # Decide config source: parsed URI > discrete fields.
    if body.uri:
        cfg = parse_xui_uri(body.uri)
        if cfg is None:
            raise HTTPException(400, detail="Invalid xui:// URI — couldn't parse")
        api_token = cfg.api_token
        panel_port = cfg.port
        panel_basepath = cfg.basepath
        panel_user = cfg.panel_user
        panel_pass = cfg.panel_pass
        domain = cfg.domain
        mode = cfg.mode
    else:
        # The API token is what every Bearer call needs, and until now the
        # only thing that produced one was our install script — so a panel
        # someone installed by hand, or one whose `xui://` line was lost,
        # could not be registered at all. The operator has the credentials
        # they log in with; those are enough, and the token is fetched from
        # the panel below exactly as the script does.
        if not (body.panel_port and body.panel_basepath
                and body.panel_user and body.panel_pass):
            raise HTTPException(
                400,
                detail="Provide either `uri` or panel_port + panel_basepath + "
                       "panel_user + panel_pass (api_token is fetched from the "
                       "panel when you don't have it).",
            )
        api_token = body.api_token
        panel_port = int(body.panel_port)
        panel_basepath = body.panel_basepath
        if not panel_basepath.startswith("/"):
            panel_basepath = "/" + panel_basepath
        if panel_basepath != "/" and panel_basepath.endswith("/"):
            panel_basepath = panel_basepath[:-1]
        panel_user = body.panel_user
        panel_pass = body.panel_pass
        domain = body.domain or None
        mode = body.mode or ("xui-pro" if domain else "bare")
        if mode not in ("bare", "xui-pro"):
            raise HTTPException(400, detail=f"mode must be 'bare' or 'xui-pro', got {mode!r}")

    # Probe the token + URL before committing the row. Mirror of
    # `_api_base_url` — xui-pro panel HTTPS (LE cert), bare panel HTTP.
    if mode == "xui-pro" and domain:
        base_url = f"https://{domain}:{panel_port}{panel_basepath}"
    else:
        base_url = f"http://{srv.host}:{panel_port}{panel_basepath}"

    # No token given: log in as the operator would and ask the panel for
    # one. Done before the probe below, so the row is still only written
    # once something has actually authenticated against the panel.
    if not api_token:
        try:
            async with XuiClient(
                base_url=base_url, api_token="", verify_tls=False,
                panel_user=panel_user, panel_pass=panel_pass,
            ) as bootstrap:
                api_token = await bootstrap.ensure_api_token()
        except XuiAPIError as exc:
            raise HTTPException(
                400,
                detail=(
                    f"Could not get an API token from the panel at {base_url} "
                    f"({exc.kind}): {exc}. Check the port, the base path and "
                    f"the panel login."
                ),
            )

    async with XuiClient(
        base_url=base_url, api_token=api_token, verify_tls=False,
    ) as client:
        try:
            await client.probe()
        except XuiAPIError as exc:
            raise HTTPException(
                400,
                detail=f"Panel probe failed ({exc.kind}): {exc}. "
                       "Re-deploy the panel or paste a fresh URI.",
            )

    now = datetime.now(timezone.utc)

    # Upsert by server_id. UniqueConstraint enforces this at the DB
    # layer too — if we ever end up here with a duplicate it's a bug.
    existing = (await session.exec(
        select(XuiServer).where(XuiServer.server_id == body.server_id),
    )).first()
    if existing is not None:
        existing.api_token = api_token
        existing.panel_user = panel_user
        existing.panel_pass = panel_pass
        existing.panel_port = panel_port
        existing.panel_basepath = panel_basepath
        existing.domain = domain
        existing.mode = mode
        existing.last_check = now
        existing.last_check_error = None
        existing.updated_at = now
        session.add(existing)
        await session.commit()
        await session.refresh(existing)
        await session.refresh(srv)
        if apply_policy:
            await _best_effort_policy(existing, srv)
        return _xui_to_read(existing, srv)

    xs = XuiServer(
        server_id=body.server_id,
        api_token=api_token,
        panel_user=panel_user,
        panel_pass=panel_pass,
        panel_port=panel_port,
        panel_basepath=panel_basepath,
        domain=domain,
        mode=mode,
        last_check=now,
    )
    session.add(xs)
    await session.commit()
    await session.refresh(xs)
    await session.refresh(srv)
    if apply_policy:
        await _best_effort_policy(xs, srv)
    return _xui_to_read(xs, srv)


@router.get("/servers/{xui_server_id}", response_model=XuiServerRead)
async def get_xui_server(
    xui_server_id: int, session: AsyncSession = Depends(get_session),
):
    xs, srv = await _get_xs_or_404(xui_server_id, session)
    return _xui_to_read(xs, srv)


@router.delete("/servers/{xui_server_id}", status_code=204)
async def delete_xui_server(
    xui_server_id: int, session: AsyncSession = Depends(get_session),
):
    """Unregister the panel from PiTun. Does NOT touch the panel
    itself — for that, run `POST /servers/{id}/uninstall/xui` which
    SSHs in and runs uninstall-xui-server.sh."""
    xs = (await session.exec(
        select(XuiServer).where(XuiServer.id == xui_server_id),
    )).first()
    if xs is None:
        raise HTTPException(404, detail=f"XuiServer id={xui_server_id} not found")
    await session.delete(xs)
    await session.commit()


@router.post("/servers/{xui_server_id}/probe", response_model=XuiServerRead)
async def probe_xui_server(
    xui_server_id: int,
    direct: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """Re-test the Bearer token. Updates last_check / last_check_error
    on the row regardless of outcome so the UI badge reflects the
    truth. `direct=true` dials the panel off the active node's tunnel."""
    xs, srv = await _get_xs_or_404(xui_server_id, session)
    base_url = _api_base_url(xs, srv)
    now = datetime.now(timezone.utc)
    async with XuiClient(
        base_url=base_url, api_token=xs.api_token, verify_tls=False,
        direct=direct,
    ) as client:
        try:
            await client.probe()
            xs.last_check = now
            xs.last_check_error = None
        except XuiAPIError as exc:
            xs.last_check = now
            xs.last_check_error = f"{exc.kind}: {str(exc)[:300]}"
    session.add(xs)
    await session.commit()
    # Re-hydrate both rows post-commit. `session.commit()` expires every
    # ORM instance in the session, and `_xui_to_read` reads srv.name +
    # srv.host — touching those without a refresh triggers a sync lazy-
    # load callback inside the async session, which 500s.
    await session.refresh(xs)
    await session.refresh(srv)
    return _xui_to_read(xs, srv)


# ── XuiServer fakesite (random / upload) ───────────────────────────────────


_FAKESITE_ROTATE_SCRIPT = r"""#!/usr/bin/env bash
set -euo pipefail

# Pick a random template from /root/randomfakehtml-master and copy it
# into nginx's served root. Mirror of the upstream x-ui-pro.sh
# `-RandomTemplate y` flow but scoped: no panel restart, no domain
# fiddling, no extra prompts — just file shuffle + permissions.

ARCHIVE_DIR="/root/randomfakehtml-master"
NGINX_ROOT="/var/www/html"

if ! command -v unzip >/dev/null 2>&1; then
    echo "[pitun] installing unzip…"
    apt-get update -qq
    apt-get install -y -qq unzip
fi

if [ ! -d "$ARCHIVE_DIR" ] || [ -z "$(ls -A "$ARCHIVE_DIR" 2>/dev/null)" ]; then
    echo "[pitun] randomfakehtml master archive missing — re-downloading…"
    cd /root
    rm -rf randomfakehtml-master randomfakehtml-master.zip
    wget -qO randomfakehtml-master.zip \
        https://github.com/GFW4Fun/randomfakehtml/archive/refs/heads/master.zip
    unzip -q randomfakehtml-master.zip
    rm -f randomfakehtml-master.zip
fi

# Pick a random sub-template. `find -mindepth 1 -maxdepth 1 -type d`
# + shuffle gives uniform sampling without depending on python/jq.
template=$(find "$ARCHIVE_DIR" -mindepth 1 -maxdepth 1 -type d \
    | shuf -n 1)
[ -z "$template" ] && { echo "[pitun] archive contains no template dirs" >&2; exit 1; }

name=$(basename "$template")
echo "[pitun] rotating fakesite → $name"

mkdir -p "$NGINX_ROOT"
# Wipe the served root (NOT the parent — don't touch /var/www itself
# or other vhosts that might share it).
find "$NGINX_ROOT" -mindepth 1 -delete

cp -r "$template"/* "$NGINX_ROOT"/ 2>/dev/null || true
# Some templates ship hidden files — pick them up too.
shopt -s dotglob nullglob
cp -r "$template"/.* "$NGINX_ROOT"/ 2>/dev/null || true
shopt -u dotglob nullglob

chown -R www-data:www-data "$NGINX_ROOT"
find "$NGINX_ROOT" -type d -exec chmod 755 {} +
find "$NGINX_ROOT" -type f -exec chmod 644 {} +

# Force nginx to drop any sendfile-cached references to the old files.
nginx -t >/dev/null 2>&1 && nginx -s reload || true

echo "PITUN_FAKESITE_NAME=$name"
"""


_FAKESITE_UPLOAD_SCRIPT_TEMPLATE = r"""#!/usr/bin/env bash
set -euo pipefail

# Replace the served fakesite root with the contents of a zip the
# operator uploaded. The zip lands at $ZIP_PATH; we validate, extract
# to a staging dir, swap it in, then drop the zip + staging dir.

ZIP_PATH="__ZIP_PATH__"
NGINX_ROOT="/var/www/html"
STAGE_DIR="$(mktemp -d /tmp/pitun-fakesite-XXXXXX)"

if ! command -v unzip >/dev/null 2>&1; then
    echo "[pitun] installing unzip…"
    apt-get update -qq
    apt-get install -y -qq unzip
fi

if [ ! -s "$ZIP_PATH" ]; then
    echo "[pitun] upload missing: $ZIP_PATH" >&2
    exit 1
fi

# Reject anything pathologically large (>100 MB). A real fakesite is
# rarely past ~20 MB and a 100 MB+ archive on a 10 GB / 1 GB RAM VPS
# is asking for trouble.
zip_bytes=$(stat -c %s "$ZIP_PATH" 2>/dev/null || stat -f %z "$ZIP_PATH" 2>/dev/null || echo 0)
if [ "$zip_bytes" -gt 104857600 ]; then
    echo "[pitun] zip too large: $zip_bytes bytes (cap 100 MB)" >&2
    exit 1
fi

unzip -q -o "$ZIP_PATH" -d "$STAGE_DIR"

# If the zip wraps everything in a single top-level directory (the
# common GitHub-style export), drop into it so we don't end up with
# /var/www/html/<single-dir>/index.html. Otherwise keep the contents
# flat.
inner_count=$(find "$STAGE_DIR" -mindepth 1 -maxdepth 1 | wc -l)
if [ "$inner_count" = "1" ]; then
    only=$(find "$STAGE_DIR" -mindepth 1 -maxdepth 1)
    if [ -d "$only" ]; then
        SRC="$only"
    else
        SRC="$STAGE_DIR"
    fi
else
    SRC="$STAGE_DIR"
fi

# Require an index.html anywhere — otherwise nginx would just 404
# the apex and the operator gets confused.
if ! find "$SRC" -maxdepth 2 -iname 'index.html' | grep -q .; then
    echo "[pitun] zip has no index.html at the top level" >&2
    rm -rf "$STAGE_DIR" "$ZIP_PATH"
    exit 1
fi

mkdir -p "$NGINX_ROOT"
find "$NGINX_ROOT" -mindepth 1 -delete
cp -r "$SRC"/* "$NGINX_ROOT"/ 2>/dev/null || true
shopt -s dotglob nullglob
cp -r "$SRC"/.* "$NGINX_ROOT"/ 2>/dev/null || true
shopt -u dotglob nullglob

chown -R www-data:www-data "$NGINX_ROOT"
find "$NGINX_ROOT" -type d -exec chmod 755 {} +
find "$NGINX_ROOT" -type f -exec chmod 644 {} +

nginx -t >/dev/null 2>&1 && nginx -s reload || true

rm -rf "$STAGE_DIR" "$ZIP_PATH"
echo "PITUN_FAKESITE_UPLOADED=ok"
"""


class FakesiteRotateResponse(BaseModel):
    ok: bool
    name: Optional[str] = None
    detail: Optional[str] = None


def _ssh_creds(srv: Server) -> Dict[str, Any]:
    if srv.auth_type == "password" and srv.password:
        return {"password": srv.password}
    if srv.auth_type == "key" and srv.private_key:
        return {"private_key": srv.private_key}
    return {}


@router.post(
    "/servers/{xui_server_id}/fakesite/rotate",
    response_model=FakesiteRotateResponse,
)
async def rotate_fakesite(
    xui_server_id: int, session: AsyncSession = Depends(get_session),
):
    """Pick a random template from the bundled `randomfakehtml-master`
    archive (downloaded by setup-xui-server.sh) and swap it into
    nginx's served root. Re-fetches the archive on demand if it's
    missing (e.g. operator cleaned `/root` to free disk).

    Only meaningful in xui-pro mode — bare-mode panels run no nginx
    fronting and have no fakesite to rotate. Returns 400 in that case.
    """
    try:
        session.sync_session.expire_on_commit = False
    except Exception:  # noqa: BLE001
        pass
    xs, srv = await _get_xs_or_404(xui_server_id, session)
    if xs.mode != "xui-pro":
        raise HTTPException(
            400,
            detail=(
                "Fakesite rotation requires xui-pro mode (nginx + LE "
                "cert fronting :443). Bare panels serve no fakesite."
            ),
        )
    creds = _ssh_creds(srv)
    if not creds:
        raise HTTPException(
            400, detail="Server row has no SSH credentials.",
        )

    from app.core.ssh import exec_remote_script
    result = await exec_remote_script(
        host=srv.host,
        port=srv.port or 22,
        username=srv.user or "root",
        script_content=_FAKESITE_ROTATE_SCRIPT,
        timeout=120.0,
        **creds,
    )

    name: Optional[str] = None
    for line in (result.stdout or "").splitlines():
        if line.startswith("PITUN_FAKESITE_NAME="):
            name = line.split("=", 1)[1].strip() or None

    if not result.ok:
        raise HTTPException(
            502,
            detail=(
                (result.error or f"exit {result.exit_code}")
                + (f": {result.stderr[:300]}" if result.stderr else "")
            ),
        )
    return FakesiteRotateResponse(ok=True, name=name, detail=None)


@router.post(
    "/servers/{xui_server_id}/fakesite/upload",
    response_model=FakesiteRotateResponse,
)
async def upload_fakesite(
    xui_server_id: int,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
):
    """Replace the served fakesite root with the contents of an
    operator-uploaded ZIP. Same SCP+exec pattern as the manual-script
    upload path for naive templates: bytes land in /tmp on the VPS,
    a shell script validates + extracts + swaps + chowns + nginx -s
    reloads, then cleans up.

    Validation: zip ≤100 MB, must contain `index.html` within the
    first two levels (so nginx has something to serve at /).
    """
    try:
        session.sync_session.expire_on_commit = False
    except Exception:  # noqa: BLE001
        pass
    xs, srv = await _get_xs_or_404(xui_server_id, session)
    if xs.mode != "xui-pro":
        raise HTTPException(
            400,
            detail="Fakesite upload requires xui-pro mode.",
        )
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(400, detail="Upload must be a .zip file.")
    blob = await file.read()
    if not blob:
        raise HTTPException(400, detail="Empty upload.")
    if len(blob) > 100 * 1024 * 1024:
        raise HTTPException(
            400, detail=f"Upload too large: {len(blob)} bytes (cap 100 MB).",
        )
    creds = _ssh_creds(srv)
    if not creds:
        raise HTTPException(
            400, detail="Server row has no SSH credentials.",
        )

    # Upload the bytes to a fixed path on the VPS, then run the
    # extractor script against that path. Reuse `exec_remote_script`'s
    # asyncssh client: a one-shot upload via its SFTP channel keeps
    # us on the same SO_MARK-tagged socket that tproxy bypasses.
    from app.core.ssh import exec_remote_script, upload_file_to_remote
    remote_zip = f"/tmp/pitun-fakesite-{xui_server_id}-{secrets.token_hex(4)}.zip"
    try:
        await upload_file_to_remote(
            host=srv.host,
            port=srv.port or 22,
            username=srv.user or "root",
            remote_path=remote_zip,
            content=blob,
            timeout=120.0,
            **creds,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, detail=f"SFTP upload failed: {exc}")

    script = _FAKESITE_UPLOAD_SCRIPT_TEMPLATE.replace("__ZIP_PATH__", remote_zip)
    result = await exec_remote_script(
        host=srv.host,
        port=srv.port or 22,
        username=srv.user or "root",
        script_content=script,
        timeout=120.0,
        **creds,
    )
    if not result.ok:
        raise HTTPException(
            502,
            detail=(
                (result.error or f"exit {result.exit_code}")
                + (f": {result.stderr[:300]}" if result.stderr else "")
            ),
        )
    return FakesiteRotateResponse(ok=True, name="(uploaded)", detail=None)


# ── XuiServer healthcheck ──────────────────────────────────────────────────


class XuiServerHealthCheckResult(BaseModel):
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: Optional[str] = None


class XuiServerHealthCheckResponse(BaseModel):
    xui_server_id: int
    ok: bool
    checks: List[XuiServerHealthCheckResult]


_HEALTH_SCRIPT = r"""#!/usr/bin/env bash
# Single-pass remote probe — collects everything we want in one SSH
# round-trip + emits each result on a separate line prefixed by a tag
# the backend parses with `<tag>|<ok|warn|fail>|<detail>`. Best-effort
# everywhere: a missing binary or service falls back to a warn line
# rather than aborting the whole script.

emit() { printf '%s\n' "$1|$2|$3"; }

# nginx — only relevant when the panel was installed in xui-pro mode
# (fronted by nginx + Let's Encrypt). Bare panels run a self-signed
# panel HTTPS straight off the Go binary's port, no nginx involved —
# checking it would always emit a noisy "inactive" warning on a
# perfectly healthy bare install.
if [ "${HEALTHCHECK_MODE:-bare}" = "xui-pro" ]; then
  if command -v systemctl >/dev/null 2>&1; then
    st=$(systemctl is-active nginx 2>/dev/null || true)
    if [ "$st" = "active" ]; then
      ver=$(nginx -v 2>&1 | head -c 80 | tr -d '\n')
      emit nginx ok "$ver"
    elif [ -z "$st" ] || [ "$st" = "inactive" ] || [ "$st" = "failed" ]; then
      emit nginx fail "systemctl says: $st"
    else
      emit nginx warn "state=$st"
    fi
  else
    emit nginx warn "systemctl not found"
  fi
fi

# unzip — used by fakesite rotation. Warn (not fail) if missing; the
# rotation endpoint will install on demand.
if command -v unzip >/dev/null 2>&1; then
  emit unzip ok "$(unzip -v 2>&1 | head -1 | head -c 60)"
else
  emit unzip warn "unzip not installed (fakesite rotation will install on demand)"
fi

# UFW
if command -v ufw >/dev/null 2>&1; then
  ufw_st=$(ufw status 2>/dev/null | head -1 | tr -d '\r')
  case "$ufw_st" in
    *active*) emit ufw ok "$ufw_st" ;;
    *inactive*) emit ufw warn "$ufw_st — ports not gated; OK if your provider firewalls" ;;
    *) emit ufw warn "$ufw_st" ;;
  esac
else
  emit ufw warn "ufw not installed"
fi

# Disk free on /
df_out=$(df -BM / 2>/dev/null | awk 'NR==2 {gsub(/M/,"",$4); gsub(/%/,"",$5); print $4" "$5}')
if [ -n "$df_out" ]; then
  free_mb=${df_out% *}
  used_pct=${df_out#* }
  if [ "${used_pct:-0}" -ge 95 ] 2>/dev/null; then
    emit disk fail "${free_mb}M free, ${used_pct}% used"
  elif [ "${used_pct:-0}" -ge 85 ] 2>/dev/null; then
    emit disk warn "${free_mb}M free, ${used_pct}% used"
  else
    emit disk ok "${free_mb}M free, ${used_pct}% used"
  fi
else
  emit disk warn "df parse failed"
fi

# Memory free %
mem_pct=$(awk '/MemAvailable/{a=$2} /MemTotal/{t=$2} END{ if (t>0) printf "%d", a*100/t }' /proc/meminfo 2>/dev/null)
if [ -n "$mem_pct" ]; then
  if [ "$mem_pct" -lt 10 ]; then
    emit mem warn "${mem_pct}% available"
  else
    emit mem ok "${mem_pct}% available"
  fi
else
  emit mem warn "/proc/meminfo unavailable"
fi

# TLS cert expiry — only when a panel domain + matching LE cert exist.
# The setup script stores certs under /etc/letsencrypt/live/<apex>;
# we accept any matching CN/SAN. Quiet skip when no domain installed.
DOMAIN="${HEALTHCHECK_DOMAIN:-}"
if [ -n "$DOMAIN" ]; then
  found=""
  for dir in /etc/letsencrypt/live/*/; do
    cert="${dir}fullchain.pem"
    [ -r "$cert" ] || continue
    if openssl x509 -in "$cert" -noout -ext subjectAltName 2>/dev/null | grep -qi "DNS:$DOMAIN\b"; then
      found="$cert"
      break
    fi
    cn=$(openssl x509 -in "$cert" -noout -subject 2>/dev/null | sed -n 's/.*CN *= *\([^,]*\).*/\1/p' | tr -d ' ')
    if [ "$cn" = "$DOMAIN" ]; then
      found="$cert"
      break
    fi
  done
  if [ -n "$found" ]; then
    end=$(openssl x509 -in "$found" -noout -enddate 2>/dev/null | cut -d= -f2)
    end_s=$(date -d "$end" +%s 2>/dev/null || echo 0)
    now_s=$(date +%s)
    days_left=$(( (end_s - now_s) / 86400 ))
    if [ "$days_left" -lt 0 ]; then
      emit cert fail "expired ${days_left#-} days ago"
    elif [ "$days_left" -lt 14 ]; then
      emit cert warn "expires in ${days_left} day(s) — renew soon"
    else
      emit cert ok "valid for ${days_left} more day(s)"
    fi
  else
    emit cert warn "no LE cert matching $DOMAIN under /etc/letsencrypt/live"
  fi
fi
"""


@router.post(
    "/servers/{xui_server_id}/healthcheck",
    response_model=XuiServerHealthCheckResponse,
)
async def healthcheck_xui_server(
    xui_server_id: int, session: AsyncSession = Depends(get_session),
):
    """Run a multi-layer probe against the panel + VPS and return a
    structured pass/fail report.

    Three layers:
      1. Panel API (Bearer) — `inbounds/list` proves the token works.
      2. Panel-internal `/panel/api/server/status` — xray state +
         system snapshot (cpu / mem / uptime / disk).
      3. SSH-driven VPS probe — nginx state, ufw, disk %, mem %,
         cert expiry (for domain-mode panels). Best-effort: every
         remote check survives missing binaries with a `warn` result,
         and the whole layer is skipped if the SSH session can't be
         opened (the panel-side checks still surface).

    The endpoint is read-only — no state is mutated.
    """
    try:
        session.sync_session.expire_on_commit = False
    except Exception:  # noqa: BLE001
        pass
    xs, srv = await _get_xs_or_404(xui_server_id, session)

    checks: List[XuiServerHealthCheckResult] = []

    def _add(name: str, status: str, detail: Optional[str] = None) -> None:
        checks.append(
            XuiServerHealthCheckResult(name=name, status=status, detail=detail),
        )

    # Layer 1+2: panel-side checks via Bearer API.
    base_url = _api_base_url(xs, srv)
    xray_state = ""
    try:
        async with XuiClient(
            base_url=base_url, api_token=xs.api_token, verify_tls=False,
        ) as client:
            try:
                await client.probe()
                _add(
                    "Panel API reachable", "ok",
                    f"{srv.host}:{xs.panel_port}{xs.panel_basepath}",
                )
            except XuiAPIError as exc:
                _add("Panel API reachable", "fail", f"{exc.kind}: {exc}")
                # No point chasing the rest of the panel checks if the
                # token's dead. Skip to SSH layer.
                raise

            try:
                body = await client._request("GET", "/panel/api/server/status")
                obj = body.get("obj") or {}
                xray = obj.get("xray") or {}
                xray_state = xray.get("state") or ""
                if xray_state == "running":
                    _add("Xray running", "ok", xray.get("version") or "")
                else:
                    err = xray.get("errorMsg") or xray_state or "not running"
                    _add("Xray running", "fail", str(err)[:300])
                cpu_pct = obj.get("cpu")
                mem = obj.get("mem") or {}
                mem_total = mem.get("total") or 0
                mem_used = mem.get("current") or 0
                disk = obj.get("disk") or {}
                disk_total = disk.get("total") or 0
                disk_used = disk.get("current") or 0
                _add(
                    "Panel host snapshot", "ok",
                    "cpu={cpu}% · mem={mu}/{mt} MB · disk={du}/{dt} MB".format(
                        cpu=int(cpu_pct or 0),
                        mu=int((mem_used or 0) / 1024 / 1024),
                        mt=int((mem_total or 0) / 1024 / 1024),
                        du=int((disk_used or 0) / 1024 / 1024),
                        dt=int((disk_total or 0) / 1024 / 1024),
                    ),
                )
            except XuiAPIError as exc:
                _add(
                    "Panel /server/status", "warn",
                    f"{exc.kind}: {exc}",
                )
    except XuiAPIError:
        # Already surfaced as a fail row above; carry on to SSH layer.
        pass

    # Layer 3: SSH-driven VPS probe. Optional — skip when the Server
    # row has neither password nor key. Best-effort.
    if srv.auth_type == "password" and srv.password:
        creds: Dict[str, Any] = {"password": srv.password}
    elif srv.auth_type == "key" and srv.private_key:
        creds = {"private_key": srv.private_key}
    else:
        creds = {}

    if creds:
        from app.core.ssh import exec_remote_script
        try:
            result = await exec_remote_script(
                host=srv.host,
                port=srv.port or 22,
                username=srv.user or "root",
                script_content=_HEALTH_SCRIPT,
                env={
                    "HEALTHCHECK_DOMAIN": xs.domain or "",
                    "HEALTHCHECK_MODE": xs.mode or "bare",
                },
                timeout=20.0,
                **creds,
            )
        except Exception as exc:  # noqa: BLE001
            _add("VPS SSH probe", "warn", f"connection failed: {exc}")
            result = None

        if result is not None:
            if not result.ok and not result.stdout:
                _add(
                    "VPS SSH probe", "warn",
                    (result.error or "exit "
                     f"{result.exit_code}")[:200],
                )
            else:
                for raw_line in (result.stdout or "").splitlines():
                    line = raw_line.strip()
                    if not line or "|" not in line:
                        continue
                    parts = line.split("|", 2)
                    if len(parts) < 2:
                        continue
                    tag, status = parts[0], parts[1]
                    detail = parts[2] if len(parts) > 2 else None
                    pretty = {
                        "nginx": "nginx running",
                        "unzip": "unzip available",
                        "ufw": "UFW status",
                        "disk": "Disk free",
                        "mem": "Memory free",
                        "cert": "TLS cert (Let's Encrypt)",
                    }.get(tag, tag)
                    if status not in ("ok", "warn", "fail"):
                        status = "warn"
                    _add(pretty, status, detail or None)
    else:
        _add(
            "VPS SSH probe", "warn",
            "no SSH credentials on Server row — set auth_type+password/key",
        )

    ok = all(c.status == "ok" for c in checks)
    return XuiServerHealthCheckResponse(
        xui_server_id=xui_server_id, ok=ok, checks=checks,
    )


# ── XuiServer sync (reconcile cache ↔ panel) ──────────────────────────────


class XuiServerSyncResponse(BaseModel):
    xui_server_id: int
    added: int
    updated: int
    removed: int
    chain_skipped: int
    orphan_nodes_removed: int


class XuiPolicyResponse(BaseModel):
    xui_server_id: int
    changed: bool
    applied: Dict[str, Any]
    detail: str


@router.post(
    "/servers/{xui_server_id}/apply-policy",
    response_model=XuiPolicyResponse,
)
async def apply_xray_policy(
    xui_server_id: int,
    restart: bool = True,
    direct: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """Set the connection-lifetime timeouts in the panel's xray template.

    Xray's defaults kill an idle pooled connection after 5 minutes and
    give a half-closed stream 2–5 seconds to finish, which is what makes
    long-lived clients (agent SDKs, streaming responses) drop for no
    visible reason. Nothing set these before: chains got PiTun's template
    without timeouts, and a plain panel had no template at all and ran on
    the built-in defaults.

    We PATCH whatever the panel currently has rather than pushing a
    template of our own — the operator's outbounds, routing rules and
    stats flags are preserved, only the four timeout keys are written.
    That keeps the action safe on a panel PiTun doesn't otherwise manage.

    Idempotent: re-running reports `changed=false` and skips the restart.
    """
    from app.api.system import _load_settings_map
    from app.core.xray_policy import timeouts_from_settings
    from app.core.xui_policy import apply_policy_to_panel

    xs, srv = await _get_xs_or_404(xui_server_id, session)
    settings_map = await _load_settings_map(session)
    try:
        result = await apply_policy_to_panel(
            base_url=_api_base_url(xs, srv),
            api_token=xs.api_token,
            panel_user=xs.panel_user,
            panel_pass=xs.panel_pass,
            direct=direct,
            restart=restart,
            settings_map=settings_map,
        )
    except XuiAPIError as exc:
        raise HTTPException(
            502, detail=f"Panel policy update failed ({exc.kind}): {exc}",
        )

    return XuiPolicyResponse(
        xui_server_id=xui_server_id, changed=result.changed,
        applied=timeouts_from_settings(settings_map), detail=result.detail,
    )


@router.post(
    "/servers/{xui_server_id}/sync",
    response_model=XuiServerSyncResponse,
)
async def sync_xui_server(
    xui_server_id: int,
    direct: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """Reconcile PiTun's `XuiClient` cache with the panel's live state.

    The panel is the source of truth — humans can add / remove clients
    via its own UI without telling PiTun, and a stale cache breaks
    Node-export badges, /sync workflows, and (worst) cascade-delete
    on uninstall (a removed-by-hand client lingers as a Node row
    that can never authenticate). Sync walks every NON-chain inbound:

      * Client present on panel + in cache  → refresh the cached
        protocol / port / remark / config blob from the panel's
        current shape (the operator may have edited it).
      * Client present on panel, missing from cache → INSERT a fresh
        cache row so future PiTun-driven ops (delete-client, export)
        recognise it as managed.
      * Cache row whose client vanished from the panel → DELETE the
        cache row + cascade-delete the linked `Node` (matches what
        the explicit `/clients/<uuid>` DELETE endpoint does, since
        the credentials no longer work either way).

    Chain inbounds (`tag` starts with `chain-`) are intentionally
    skipped — their clients live under the chain orchestrator's
    bookkeeping (`ChainClient` / `ChainClientChannel`) and PiTun
    is the source of truth there, not the panel.
    """
    try:
        session.sync_session.expire_on_commit = False
    except Exception:  # noqa: BLE001
        pass

    xs, srv = await _get_xs_or_404(xui_server_id, session)
    base_url = _api_base_url(xs, srv)
    async with XuiClient(
        base_url=base_url, api_token=xs.api_token, verify_tls=False,
        direct=direct,
    ) as client:
        try:
            inbounds = await client.list_inbounds()
        except XuiAPIError as exc:
            raise HTTPException(
                502, detail=f"Panel list_inbounds failed ({exc.kind}): {exc}",
            )

    # Build the canonical "what the panel actually has" view. Tuple
    # `(inbound_remote_id, client_uuid)` is the natural key — for
    # trojan/ss/socks the panel still emits a stable `id`/`password`
    # at the same slot we treat as `client_uuid` in our cache, so a
    # single key works across protocols.
    chain_skipped = 0
    panel_view: Dict[tuple, Dict[str, Any]] = {}
    for ib in inbounds:
        if (ib.get("tag") or "").startswith("chain-"):
            chain_skipped += 1
            continue
        ib_id = int(ib.get("id") or 0)
        if not ib_id:
            continue
        # v3.1.0 returns settings as nested dict, v3.0.x as JSON-string —
        # parse_inbound_field handles both shapes.
        settings = parse_inbound_field(ib.get("settings"))
        ib_proto = (ib.get("protocol") or "").lower()
        ib_port = int(ib.get("port") or 0)
        ib_remark = ib.get("remark") or ""
        for c in (settings.get("clients") or []):
            uuid_val = (
                c.get("id")
                or c.get("password")
                or c.get("user")
                or ""
            )
            if not uuid_val:
                continue
            panel_view[(ib_id, uuid_val)] = {
                "label": c.get("email") or uuid_val[:8],
                "protocol": ib_proto,
                "port": ib_port,
                "remark": ib_remark,
                "config": c,
            }

    # Pull the existing cache for this panel in one go.
    cache_rows = list((await session.exec(
        select(XuiClientModel)
        .where(XuiClientModel.xui_server_id == xui_server_id),
    )).all())
    cache_by_key = {
        (r.inbound_remote_id, r.client_uuid): r for r in cache_rows
    }

    # Fallback index by (inbound, label). Rows written before the
    # natural-id fix stored an EMPTY `client_uuid` for trojan clients,
    # so they can never match a panel entry keyed by password — sync
    # would call a live client "vanished" and cascade-delete the Node it
    # was exported to. Matching on the label (the panel's `email`, which
    # PiTun itself generated) recovers those rows and heals them in
    # place; only rows with a genuinely missing counterpart are dropped.
    cache_by_label = {
        (r.inbound_remote_id, r.label): r
        for r in cache_rows
        if r.label and not r.client_uuid
    }
    adopted_rows: list = []

    added = 0
    updated = 0
    removed = 0
    orphan_node_ids: List[int] = []

    now = datetime.now(timezone.utc)
    for key, pv in panel_view.items():
        existing = cache_by_key.get(key)
        if existing is None:
            legacy = cache_by_label.get((key[0], pv["label"]))
            if legacy is not None:
                # Adopt the panel's natural id so the next sync matches
                # on the fast path — and keep `exported_node_id`.
                legacy.client_uuid = key[1]
                existing = legacy
                adopted_rows.append(legacy)
        if existing is None:
            session.add(XuiClientModel(
                xui_server_id=xui_server_id,
                inbound_remote_id=key[0],
                client_uuid=key[1],
                label=pv["label"],
                inbound_protocol=pv["protocol"],
                inbound_port=pv["port"],
                inbound_remark=pv["remark"],
                config_json=json.dumps(pv["config"]),
                last_synced_at=now,
            ))
            added += 1
        else:
            existing.label = pv["label"]
            existing.inbound_protocol = pv["protocol"]
            existing.inbound_port = pv["port"]
            existing.inbound_remark = pv["remark"]
            existing.config_json = json.dumps(pv["config"])
            existing.last_synced_at = now
            session.add(existing)
            updated += 1

    # Cache rows whose key disappeared from the panel — drop them
    # AND cascade-clean the linked Node row. Skip chain-tag inbounds
    # at this step too (we don't even cache chain clients here, but
    # be defensive against legacy rows from older PiTun builds).
    for key, row in cache_by_key.items():
        if key in panel_view:
            continue
        # Adopted above under the panel's real key — not an orphan.
        if row in adopted_rows:
            continue
        # Look up whether the inbound itself disappeared (the panel
        # del-inbound path would have cascaded all its clients).
        # Either way, the cache row is stale — drop it.
        if row.exported_node_id:
            orphan_node_ids.append(row.exported_node_id)
        await session.delete(row)
        removed += 1

    from app.models import Node
    orphan_nodes_removed = 0
    for nid in orphan_node_ids:
        n = await session.get(Node, nid)
        if n is not None:
            await session.delete(n)
            orphan_nodes_removed += 1

    await session.commit()
    return XuiServerSyncResponse(
        xui_server_id=xui_server_id,
        added=added, updated=updated, removed=removed,
        chain_skipped=chain_skipped,
        orphan_nodes_removed=orphan_nodes_removed,
    )


@router.get("/servers/{xui_server_id}/inbounds")
async def list_inbounds(
    xui_server_id: int, session: AsyncSession = Depends(get_session),
):
    """Live list of inbounds from the panel. Includes everything —
    PiTun-managed AND hand-added. Frontend filters by `email` prefix
    when it wants the PiTun-managed subset.

    Each inbound dict gets a `_pitun_exports` field — a map of
    `{client_uuid_or_email: node_id}` covering every client on the
    inbound that PiTun has already exported as a Node. The frontend
    renders a "→ Node #N" badge from this without a per-row round-trip.
    """
    xs, srv = await _get_xs_or_404(xui_server_id, session)
    base_url = _api_base_url(xs, srv)
    async with XuiClient(
        base_url=base_url, api_token=xs.api_token, verify_tls=False,
    ) as client:
        try:
            inbounds = await client.list_inbounds()
        except XuiAPIError as exc:
            raise HTTPException(502, detail=f"Panel error ({exc.kind}): {exc}")

    # Pull every exported-client row for this panel in a single
    # query, group by inbound id. The cache covers two paths into
    # Node rows: clients added via PiTun's add-client UI, and
    # clients exported via the new export-node endpoint (which
    # back-fills the cache row's exported_node_id).
    exports_q = (
        select(XuiClientModel)
        .where(XuiClientModel.xui_server_id == xui_server_id)
        .where(XuiClientModel.exported_node_id.is_not(None))
    )
    exports = list((await session.exec(exports_q)).all())
    by_inbound: Dict[int, Dict[str, int]] = {}
    for row in exports:
        by_inbound.setdefault(row.inbound_remote_id, {})
        if row.client_uuid:
            by_inbound[row.inbound_remote_id][row.client_uuid] = row.exported_node_id  # type: ignore[assignment]
        if row.label:
            # Also key by email/label so the frontend can match
            # protocols where client_uuid is empty (trojan / ss).
            by_inbound[row.inbound_remote_id][row.label] = row.exported_node_id  # type: ignore[assignment]

    for ib in inbounds:
        ib["_pitun_exports"] = by_inbound.get(int(ib.get("id") or 0), {})
    return inbounds


@router.post("/servers/{xui_server_id}/inbounds", status_code=201)
async def create_inbound(
    xui_server_id: int,
    body: InboundCreateBody,
    direct: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """Create an inbound from a preset + user-provided values.

    Reality keys + UUID are generated server-side via the panel's
    util endpoints (`getNewX25519Cert` / `getNewUUID`) so the
    frontend never sees the private key.
    """
    preset = get_preset(body.preset_id)
    if preset is None:
        raise HTTPException(
            400,
            detail=(
                f"Unknown preset {body.preset_id!r}. Valid: "
                f"{list(PRESETS.keys())}"
            ),
        )

    xs, srv = await _get_xs_or_404(xui_server_id, session)

    if preset.needs_domain and not xs.domain:
        raise HTTPException(
            400,
            detail=(
                f"Preset {preset.id!r} requires a TLS domain, but this "
                "panel was installed in bare mode (no nginx / no Let's "
                "Encrypt). Pick a Reality preset (vless-reality-*) or "
                "re-deploy the panel with DOMAIN set."
            ),
        )

    # Validate required fields up-front — gives the frontend a clean
    # 400 with the missing field name instead of a panel API error
    # that's harder to surface in the form.
    values = dict(body.values)
    if body.remark and "remark" not in values:
        values["remark"] = body.remark
    for f in preset.fields:
        if f.required and not values.get(f.name):
            if f.default:
                values[f.name] = f.default
            else:
                raise HTTPException(400, detail=f"Missing required field: {f.name}")

    base_url = _api_base_url(xs, srv)
    async with XuiClient(
        base_url=base_url, api_token=xs.api_token, verify_tls=False,
        direct=direct,
    ) as client:
        # Resolve server-generated values via the panel itself so
        # the frontend never handles the private key.
        resolved: Dict[str, Any] = {}
        try:
            # vless inbounds need a server-generated UUID for the
            # bootstrap client; trojan uses a password (the preset
            # generates it itself if not supplied). Passing `uuid` to
            # _build_trojan_grpc would TypeError on the kwarg.
            if preset.protocol == "vless":
                resolved["uuid"] = await client.get_new_uuid()
            if preset.supports_reality:
                priv, pub = await client.get_new_x25519_cert()
                resolved["private_key"] = priv
                resolved["public_key"] = pub
                resolved["short_id"] = secrets.token_hex(4)
            if preset.needs_domain:
                resolved["domain"] = xs.domain or ""
        except XuiAPIError as exc:
            raise HTTPException(
                502,
                detail=f"Panel util call failed ({exc.kind}): {exc}",
            )

        try:
            payload = preset.build_payload(values, **resolved)
        except KeyError as exc:
            raise HTTPException(400, detail=f"Missing required field: {exc.args[0]}")

        # The panel's settings + streamSettings + sniffing fields are
        # JSON-in-JSON: outer body holds them as STRINGS, not nested
        # dicts. Same quirk as addClient — see xui_api.py docstring.
        wire = {
            **{k: v for k, v in payload.items()
               if k not in ("settings", "streamSettings", "sniffing")},
            "settings": json.dumps(payload["settings"]),
            "streamSettings": json.dumps(payload["streamSettings"]),
            "sniffing": json.dumps(payload["sniffing"]),
        }

        try:
            created = await client.add_inbound(wire)
        except XuiAPIError as exc:
            raise HTTPException(
                502,
                detail=f"Panel rejected add-inbound ({exc.kind}): {exc}",
            )
        # Force Xray to pick up the new inbound. Both bare 3x-ui and
        # x-ui-pro persist `add_inbound` straight into their own
        # sqlite but don't reload Xray's running config — the
        # `bin/config.json` keeps the pre-create snapshot, the new
        # inbound never starts listening with the right Reality keys,
        # and Speedtest hits TCP (the inbound's TCP listener IS up,
        # owned by the panel's own watchdog process) but Reality
        # handshake silently drops. Mirror of the same gotcha we
        # already patched in `orchestrate_add_client`.
        try:
            await client.restart_xray()
        except XuiAPIError as exc:
            logger.warning(
                "create_inbound: restart_xray after add_inbound failed: %s", exc,
            )

    # Open the inbound's listen port in UFW on the VPS. Standalone
    # inbounds bind directly on the host (no nginx fronting), so a
    # default-deny firewall would silently drop SYNs. Domain-mode
    # presets use `externalProxy` over :443 — nginx terminates and
    # forwards to a random localhost port, no extra UFW rule needed
    # — but the inbound's literal port still benefits from being
    # opened (panel may also serve it directly for testing). Best-
    # effort: a failure logs but doesn't block the create response.
    inbound_port = int(payload.get("port") or 0)
    if inbound_port and not preset.needs_domain:
        try:
            await _ufw_chain_ports(srv, [inbound_port], action="allow")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "create_inbound: ufw allow %s failed on srv=%s: %s",
                inbound_port, srv.id, exc,
            )

    # The panel returns the inbound with a new `id` — re-fetch live so
    # the frontend renders identical data to a subsequent /list call.
    return created


@router.delete(
    "/servers/{xui_server_id}/inbounds/{inbound_id}",
    status_code=204,
)
async def delete_inbound(
    xui_server_id: int, inbound_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Delete an inbound on the panel + cascade-clean PiTun state.

    The cascade covers three things tied to this inbound:
      1. The panel-side inbound itself (`del_inbound`).
      2. Every cached `XuiClient` row pointing at it.
      3. Every `Node` row those cache rows exported as routable
         outbounds. The clients are gone → their UUIDs no longer
         authenticate → the Nodes can never connect; leaving them
         around just confuses /nodes and breaks health-checks.
    """
    # The endpoint commits multiple times below; the orchestrate_*
    # `_no_expire_on_commit` pattern keeps ORM reads cheap afterwards.
    try:
        session.sync_session.expire_on_commit = False
    except Exception:  # noqa: BLE001
        pass

    xs, srv = await _get_xs_or_404(xui_server_id, session)
    base_url = _api_base_url(xs, srv)
    # Snapshot the inbound's listen port BEFORE the delete so we
    # know which UFW rule to retract. Best-effort: a 404 here just
    # means the panel already lost track of the inbound.
    inbound_port_to_close: int = 0
    ib_meta: Dict[str, Any] = {}
    async with XuiClient(
        base_url=base_url, api_token=xs.api_token, verify_tls=False,
    ) as client:
        try:
            ib_meta = await client.get_inbound(inbound_id)
            inbound_port_to_close = int(ib_meta.get("port") or 0)
        except XuiAPIError:
            pass
        try:
            await client.del_inbound(inbound_id)
        except XuiAPIError as exc:
            raise HTTPException(
                502,
                detail=f"Panel rejected del-inbound ({exc.kind}): {exc}",
            )
        # Force Xray to drop the inbound from its running config.
        # Same persist-to-DB-but-not-runtime quirk as add_inbound /
        # add_client. Best-effort.
        try:
            await client.restart_xray()
        except XuiAPIError as exc:
            logger.warning(
                "delete_inbound: restart_xray failed: %s", exc,
            )

    # Mirror the create-time UFW open. Standalone inbounds bound a
    # public port via UFW allow; the matching deny goes here. Skip
    # silently for chain-managed inbounds (chain orchestrator owns
    # those rules) and for ports we couldn't read off the panel.
    if inbound_port_to_close and not (ib_meta.get("tag") or "").startswith("chain-"):
        try:
            await _ufw_chain_ports(
                srv, [inbound_port_to_close], action="delete-allow",
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "delete_inbound: ufw close %s failed on srv=%s: %s",
                inbound_port_to_close, srv.id, exc,
            )

    cached = list((await session.exec(
        select(XuiClientModel)
        .where(XuiClientModel.xui_server_id == xui_server_id)
        .where(XuiClientModel.inbound_remote_id == inbound_id),
    )).all())
    # Cascade-delete the exported Nodes that this inbound's clients
    # produced. Best-effort: if the Node was already deleted by the
    # operator on /nodes, the session.get returns None and we skip.
    for row in cached:
        if row.exported_node_id:
            node = await session.get(Node, row.exported_node_id)
            if node is not None:
                await session.delete(node)
        await session.delete(row)
    await session.commit()


@router.post(
    "/servers/{xui_server_id}/inbounds/{inbound_id}/clients",
    status_code=201,
)
async def add_client(
    xui_server_id: int, inbound_id: int,
    body: ClientCreateBody,
    direct: bool = False,
    session: AsyncSession = Depends(get_session),
):
    """Add a client to an existing inbound + cache the row in
    `xuiclient` for /sync + node-export bookkeeping. `direct=true` dials
    the panel off the active node's tunnel."""
    xs, srv = await _get_xs_or_404(xui_server_id, session)
    label = body.label or f"pi-{secrets.token_hex(4)}"

    base_url = _api_base_url(xs, srv)
    async with XuiClient(
        base_url=base_url, api_token=xs.api_token, verify_tls=False,
        direct=direct,
    ) as client:
        # Look up the inbound to know its protocol — the panel doesn't
        # mind a flow on a non-vless inbound but the resulting client
        # would be silently broken. Fail fast instead.
        try:
            inbound = await client.get_inbound(inbound_id)
        except XuiAPIError as exc:
            raise HTTPException(
                404 if exc.kind == "not_found" else 502,
                detail=f"Inbound lookup failed ({exc.kind}): {exc}",
            )
        protocol = inbound.get("protocol") or "vless"
        port = int(inbound.get("port") or 0)
        remark = inbound.get("remark") or ""

        try:
            uuid = await client.get_new_uuid()
        except XuiAPIError as exc:
            raise HTTPException(
                502,
                detail=f"getNewUUID failed ({exc.kind}): {exc}",
            )

        client_obj = _build_client(
            label=label, uuid=uuid,
            extras={"protocol": protocol, **body.extras},
        )

        try:
            await client.add_client(inbound_id, client_obj)
        except XuiAPIError as exc:
            raise HTTPException(
                502,
                detail=f"Panel rejected addClient ({exc.kind}): {exc}",
            )
        # Same gotcha as `create_inbound`: x-ui persists addClient to
        # its DB but doesn't reload Xray. Without this restart the
        # fresh UUID returns a silent Reality drop.
        try:
            await client.restart_xray()
        except XuiAPIError as exc:
            logger.warning(
                "add_client: restart_xray failed: %s", exc,
            )

    # Store the same natural id `/sync` derives from the panel payload
    # (`id` → `password` → `user`). Storing "" for trojan meant the cache
    # row never matched its panel counterpart, so the very next sync
    # declared the client "vanished" and cascade-deleted the Node it had
    # been exported to — while the client was alive on the panel.
    row = XuiClientModel(
        xui_server_id=xui_server_id,
        inbound_remote_id=inbound_id,
        client_uuid=str(
            client_obj.get("id")
            or client_obj.get("password")
            or client_obj.get("user")
            or ""
        ),
        label=label,
        inbound_protocol=protocol,
        inbound_port=port,
        inbound_remark=remark,
        config_json=json.dumps(client_obj),
        last_synced_at=datetime.now(timezone.utc),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return {
        "id": row.id,
        "xui_server_id": row.xui_server_id,
        "inbound_remote_id": row.inbound_remote_id,
        "client_uuid": row.client_uuid,
        "label": row.label,
        "inbound_protocol": row.inbound_protocol,
        "inbound_port": row.inbound_port,
        "inbound_remark": row.inbound_remark,
        "config": client_obj,
        "exported_node_id": row.exported_node_id,
    }


@router.delete(
    "/servers/{xui_server_id}/inbounds/{inbound_id}/clients/{client_uuid}",
    status_code=204,
)
async def delete_client(
    xui_server_id: int, inbound_id: int, client_uuid: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete one client on the panel + cascade-clean PiTun state.

    Mirror of `delete_inbound` but scoped to a single client:
      1. Remove the client from the panel (`del_client`).
      2. Drop the cached `XuiClient` row.
      3. Cascade-delete the exported `Node` row, if any.
      4. Bounce Xray on the panel so the running config drops the
         credentials (x-ui-pro persists del to the panel DB but
         doesn't auto-reload Xray — same gotcha we hit in beta.7
         with addClient on chain inbounds).
    """
    try:
        session.sync_session.expire_on_commit = False
    except Exception:  # noqa: BLE001
        pass

    xs, srv = await _get_xs_or_404(xui_server_id, session)
    base_url = _api_base_url(xs, srv)
    async with XuiClient(
        base_url=base_url, api_token=xs.api_token, verify_tls=False,
    ) as client:
        try:
            await client.del_client(inbound_id, client_uuid)
        except XuiAPIError as exc:
            raise HTTPException(
                502,
                detail=f"Panel rejected delClient ({exc.kind}): {exc}",
            )
        # Reload Xray so the deleted credentials stop authenticating
        # in the running config. Best-effort.
        try:
            await client.restart_xray()
        except XuiAPIError as exc:
            logger.warning("delete_client: restart_xray failed: %s", exc)

    cached = (await session.exec(
        select(XuiClientModel)
        .where(XuiClientModel.xui_server_id == xui_server_id)
        .where(XuiClientModel.inbound_remote_id == inbound_id)
        .where(XuiClientModel.client_uuid == client_uuid),
    )).first()
    if cached is not None:
        if cached.exported_node_id:
            node = await session.get(Node, cached.exported_node_id)
            if node is not None:
                await session.delete(node)
        await session.delete(cached)
        await session.commit()


class XuiClientExportNodeBody(BaseModel):
    """Optional override for the export name; defaults to
    `<inbound-remark>-<client-email or short-uuid>`."""
    name: Optional[str] = None


@router.get(
    "/servers/{xui_server_id}/inbounds/{inbound_id}/clients/{client_id}/share-uri",
)
async def get_inbound_client_share_uri(
    xui_server_id: int, inbound_id: int, client_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Build a pasteable share URI for one inbound-side client.

    Used by the X-ui Panels page to render a QR code without
    persisting a Node first. Mirrors the inbound→Node parsing logic
    from `export_inbound_client_to_node` via
    `core.uri_formatter.inbound_client_to_uri`.

    Returns `{ uri: str | null, reason: str | null }`. `reason`
    explains why the URI is unavailable (unknown protocol, client
    missing UUID/password, etc.) so the frontend can render a
    sensible tooltip instead of just hiding the QR button silently.
    """
    xs, srv = await _get_xs_or_404(xui_server_id, session)
    base_url = _api_base_url(xs, srv)
    async with XuiClient(
        base_url=base_url, api_token=xs.api_token, verify_tls=False,
    ) as client:
        try:
            inbound = await client.get_inbound(inbound_id)
        except XuiAPIError as exc:
            raise HTTPException(
                404 if exc.kind == "not_found" else 502,
                detail=f"Inbound lookup failed ({exc.kind}): {exc}",
            )

    settings = parse_inbound_field(inbound.get("settings"))

    clients = settings.get("clients") or []
    target = None
    for c in clients:
        if c.get("id") == client_id or c.get("email") == client_id:
            target = c
            break
    # SOCKS5 inbound has no per-client list — accounts live in
    # settings.accounts[0]. Treat the inbound itself as a "client"
    # so the share URI builder can pull user/pass out of accounts.
    if target is None and (inbound.get("protocol") or "").lower() == "socks":
        target = {}

    if target is None:
        raise HTTPException(
            404,
            detail=f"Client {client_id!r} not found on inbound {inbound_id}",
        )

    uri = inbound_client_to_uri(inbound, target, srv.host)
    if uri is None:
        proto = (inbound.get("protocol") or "?").lower()
        return {
            "uri": None,
            "reason": (
                f"Protocol {proto!r} doesn't have a canonical share URI "
                "(e.g. WireGuard configs need the private key + full peer "
                "block, which doesn't fit into a single line)."
            ),
        }
    return {"uri": uri, "reason": None}


@router.post(
    "/servers/{xui_server_id}/inbounds/{inbound_id}/clients/{client_id}/export-node",
)
async def export_inbound_client_to_node(
    xui_server_id: int, inbound_id: int, client_id: str,
    body: XuiClientExportNodeBody,
    session: AsyncSession = Depends(get_session),
):
    """Turn one inbound-side client into a routable PiTun Node.

    `client_id` matches either the client UUID (vless / vmess) or
    its email (any protocol). We resolve the inbound from the panel
    (live source of truth), pull the client's id / flow, then build
    a Node row using the inbound's transport / TLS / Reality fields.

    Idempotent: if a Node with the same uuid/password + address +
    port already exists, returns it without duplicating.
    """
    # Disable expire-on-commit for this session: the endpoint does
    # session.commit() and then reads `node.id` on the return line,
    # which under SQLAlchemy's default would re-load the instance
    # via the sync greenlet → MissingGreenlet. Same systemic fix
    # we applied to the orchestrate_* functions.
    try:
        session.sync_session.expire_on_commit = False
    except Exception:  # noqa: BLE001
        pass
    xs, srv = await _get_xs_or_404(xui_server_id, session)
    base_url = _api_base_url(xs, srv)
    async with XuiClient(
        base_url=base_url, api_token=xs.api_token, verify_tls=False,
    ) as client:
        try:
            inbound = await client.get_inbound(inbound_id)
        except XuiAPIError as exc:
            raise HTTPException(
                404 if exc.kind == "not_found" else 502,
                detail=f"Inbound lookup failed ({exc.kind}): {exc}",
            )

    settings = parse_inbound_field(inbound.get("settings"))
    clients = settings.get("clients") or []
    target = None
    for c in clients:
        if c.get("id") == client_id or c.get("email") == client_id:
            target = c
            break
    if target is None:
        raise HTTPException(
            404, detail=f"Client {client_id!r} not found on inbound {inbound_id}",
        )

    stream = parse_inbound_field(inbound.get("streamSettings"))

    # Default: dial the VPS IP directly on the inbound's own port.
    # This is correct for Reality / SOCKS5 / anything not fronted by
    # nginx. For xui-pro domain-mode inbounds we override below from
    # `externalProxy` — those advertise a reverse-proxied endpoint
    # (the client must hit `<domain>:443` with TLS, NOT the inbound's
    # localhost-ish port).
    address = srv.host
    port = int(inbound.get("port") or 0)
    protocol = (inbound.get("protocol") or "").lower()

    network = stream.get("network") or "tcp"
    security = stream.get("security") or "none"

    # externalProxy ↣ xui-pro reverse-proxy convention. When present,
    # `dest`/`port`/`forceTls` describe what the CLIENT should dial,
    # while the inbound itself stays on its random local port behind
    # nginx. Always take the first entry — multi-front setups (e.g.
    # both a CDN and a direct domain) would emit several, and the
    # first one wins by panel convention.
    ep_list = stream.get("externalProxy") or []
    if isinstance(ep_list, list) and ep_list:
        ep = ep_list[0] if isinstance(ep_list[0], dict) else {}
        ep_dest = ep.get("dest") or ""
        ep_port = int(ep.get("port") or 0)
        ep_force_tls = (ep.get("forceTls") or "").lower()
        if ep_dest:
            address = ep_dest
            # SNI matches the cert nginx serves on :443 — same as dest.
            sni_from_ep = ep_dest
        if ep_port:
            port = ep_port
        if ep_force_tls == "tls":
            security = "tls"
    else:
        sni_from_ep = ""
    sni = sni_from_ep
    fingerprint = "chrome"
    alpn = ""
    reality_pbk = ""
    reality_sid = ""
    reality_spx = ""
    ws_path = "/"
    ws_host = ""
    grpc_service = ""
    http_path = "/"
    http_host = ""

    tls_settings = (stream.get("tlsSettings") or {})
    if tls_settings:
        sni = tls_settings.get("serverName") or ""
        fingerprint = tls_settings.get("fingerprint") or fingerprint
        alpn_val = tls_settings.get("alpn") or []
        alpn = ",".join(alpn_val) if isinstance(alpn_val, list) else str(alpn_val)
    reality_settings = (stream.get("realitySettings") or {})
    if reality_settings:
        sni = reality_settings.get("serverName") or (
            (reality_settings.get("serverNames") or [""])[0]
        ) or sni
        fingerprint = (
            (reality_settings.get("settings") or {}).get("fingerprint")
            or fingerprint
        )
        reality_pbk = (
            (reality_settings.get("settings") or {}).get("publicKey") or ""
        )
        reality_sid = ((reality_settings.get("shortIds") or [""])[0])
        reality_spx = (
            (reality_settings.get("settings") or {}).get("spiderX") or ""
        )
        if security == "none" and reality_pbk:
            security = "reality"
    ws_settings = (stream.get("wsSettings") or {})
    if ws_settings:
        ws_path = ws_settings.get("path") or "/"
        ws_host = ws_settings.get("host") or ""
        if not ws_host:
            ws_host = ((ws_settings.get("headers") or {}).get("Host") or "")
    grpc_settings = (stream.get("grpcSettings") or {})
    grpc_authority = ""
    grpc_mode = "gun"
    if grpc_settings:
        grpc_service = grpc_settings.get("serviceName") or ""
        grpc_authority = grpc_settings.get("authority") or ""
        # Panel stores multiMode as a bool; our ORM model encodes
        # the same toggle as `grpc_mode in {"gun","multi"}`.
        if grpc_settings.get("multiMode") is True:
            grpc_mode = "multi"
    http_settings = (
        stream.get("httpSettings")
        or stream.get("xhttpSettings")
        or {}
    )
    if http_settings:
        http_path = http_settings.get("path") or "/"
        host = http_settings.get("host")
        if isinstance(host, list) and host:
            http_host = host[0]
        elif isinstance(host, str):
            http_host = host
        # Don't auto-fill for xhttp — Reality+xhttp uses serverName
        # from realitySettings for SNI cover, and a non-empty Host
        # header pointing at the wrong target breaks path matching
        # (matches the user's hand-rolled working URL where `host=`
        # is empty). For h2/http the panel already returns the right
        # host, so we honour it.

    # Build the Node row. Field names match the ORM exactly — same
    # gotcha that bit the chain exporter (silently-dropped unknown
    # kwargs).
    uuid_val = target.get("id") or ""
    password_val = target.get("password") or ""
    label = target.get("email") or (uuid_val[:8] if uuid_val else "client")
    name = body.name or f"{inbound.get('remark') or 'inbound-' + str(inbound_id)}-{label}"

    natural_key = uuid_val or password_val

    # Re-export path: if PiTun's cache already links this client to a
    # Node, UPDATE that Node in place. This keeps the Node id stable
    # (so any saved routing rules / chain_node_id refs survive) and
    # picks up changes from re-exports — e.g. the beta.7 fix moving
    # domain-mode inbounds from `IP:<random_port>` to `<domain>:443`
    # with `tls=tls` would otherwise leave a stale Node row.
    cached_for_update = (await session.exec(
        select(XuiClientModel)
        .where(XuiClientModel.xui_server_id == xui_server_id)
        .where(XuiClientModel.inbound_remote_id == inbound_id)
        .where(
            (XuiClientModel.client_uuid == client_id)
            | (XuiClientModel.label == client_id)
        ),
    )).first()
    if cached_for_update is not None and cached_for_update.exported_node_id:
        existing = await session.get(Node, cached_for_update.exported_node_id)
        if existing is not None:
            existing.protocol = protocol or "vless"
            existing.address = address
            existing.port = port
            existing.uuid = uuid_val or None
            existing.password = password_val or None
            existing.transport = network
            existing.tls = security
            existing.sni = sni or address
            existing.fingerprint = fingerprint
            existing.alpn = alpn
            existing.ws_path = ws_path
            existing.ws_host = ws_host
            existing.grpc_service = grpc_service
            existing.grpc_mode = grpc_mode
            existing.grpc_authority = grpc_authority or None
            existing.http_path = http_path
            existing.http_host = http_host
            existing.reality_pbk = reality_pbk
            existing.reality_sid = reality_sid
            existing.reality_spx = reality_spx
            existing.flow = target.get("flow") or None
            existing.server_id = srv.id
            session.add(existing)
            await session.commit()
            return {"node_id": existing.id, "reused": True}

    # Idempotency — same (uuid, address, port) tuple → reuse.
    if natural_key:
        existing = (await session.exec(
            select(Node)
            .where(Node.protocol == (protocol or "vless"))
            .where(Node.address == address)
            .where(Node.port == port)
            .where(
                (Node.uuid == natural_key) | (Node.password == natural_key)
            ),
        )).first()
        if existing is not None:
            # Cache may still be stale (e.g. Node row created via a
            # different path); ensure the link is recorded so the
            # inbound-list export badges show up.
            cached_existing = (await session.exec(
                select(XuiClientModel)
                .where(XuiClientModel.xui_server_id == xui_server_id)
                .where(XuiClientModel.inbound_remote_id == inbound_id)
                .where(
                    (XuiClientModel.client_uuid == client_id)
                    | (XuiClientModel.label == client_id)
                ),
            )).first()
            if cached_existing is None:
                cached_existing = XuiClientModel(
                    xui_server_id=xui_server_id,
                    inbound_remote_id=inbound_id,
                    client_uuid=natural_key,
                    label=(target.get("email") or natural_key[:8]),
                    inbound_protocol=protocol or "vless",
                    inbound_port=port,
                    inbound_remark=inbound.get("remark") or "",
                    config_json=json.dumps(target),
                    last_synced_at=datetime.now(timezone.utc),
                    exported_node_id=existing.id,
                )
                session.add(cached_existing)
                await session.commit()
            elif cached_existing.exported_node_id != existing.id:
                cached_existing.exported_node_id = existing.id
                session.add(cached_existing)
                await session.commit()
            return {"node_id": existing.id, "reused": True}

    node = Node(
        name=name,
        protocol=protocol or "vless",
        address=address,
        port=port,
        uuid=uuid_val or None,
        password=password_val or None,
        transport=network,
        tls=security,
        sni=sni or address,
        fingerprint=fingerprint,
        alpn=alpn,
        ws_path=ws_path,
        ws_host=ws_host,
        grpc_service=grpc_service,
        grpc_mode=grpc_mode,
        grpc_authority=grpc_authority or None,
        http_path=http_path,
        http_host=http_host,
        reality_pbk=reality_pbk,
        reality_sid=reality_sid,
        reality_spx=reality_spx,
        flow=target.get("flow") or None,
        # Wire the Node to its source Server so the "Привязанный
        # сервер" picker reflects the relationship and uninstall-time
        # cascades stay clean.
        server_id=srv.id,
    )
    session.add(node)
    await session.flush()
    # Update or seed the cached XuiClient row's exported_node_id so
    # both /sync bookkeeping and the inbound-list export badges work
    # across page reloads. The row may not exist yet if the client
    # was added directly on the panel (not via PiTun's add-client
    # endpoint) — in that case we insert a fresh row.
    cached = (await session.exec(
        select(XuiClientModel)
        .where(XuiClientModel.xui_server_id == xui_server_id)
        .where(XuiClientModel.inbound_remote_id == inbound_id)
        .where(
            (XuiClientModel.client_uuid == client_id)
            | (XuiClientModel.label == client_id)
        ),
    )).first()
    if cached is None:
        cached = XuiClientModel(
            xui_server_id=xui_server_id,
            inbound_remote_id=inbound_id,
            client_uuid=uuid_val,
            label=label,
            inbound_protocol=protocol or "vless",
            inbound_port=port,
            inbound_remark=inbound.get("remark") or "",
            config_json=json.dumps(target),
            last_synced_at=datetime.now(timezone.utc),
            exported_node_id=node.id,
        )
        session.add(cached)
    elif cached.exported_node_id is None:
        cached.exported_node_id = node.id
        session.add(cached)
    await session.commit()
    return {"node_id": node.id, "reused": False}


# ── Chains (since v1.3.0-beta.7) ────────────────────────────────────────────


class ChannelDraftBody(BaseModel):
    """One channel inside a chain-create request. Server-side defaults
    handled in xui_chain.orchestrate_create: empty ports = auto-pick,
    empty xhttp_path = `/api/v1/<name>`, etc."""

    name: str
    client_sni: str
    exit_port: int = 0
    relay_port: int = 0
    exit_xhttp_path: str = ""
    relay_remark: str = ""
    exit_remark: str = ""


class ChainCreateBody(BaseModel):
    name: str
    exit_xui_server_id: int
    relay_xui_server_id: int
    exit_sni: str = "www.google.com"
    channels: List[ChannelDraftBody]


class ChannelRead(BaseModel):
    id: int
    chain_id: int
    name: str
    order: int
    exit_inbound_remote_id: int
    relay_inbound_remote_id: int
    exit_port: int
    relay_port: int
    exit_xhttp_path: str
    client_sni: str
    relay_inbound_remark: str
    exit_inbound_remark: str
    # Reality public material — surfaced so the UI can render the
    # client VLESS URL without re-asking the panel. Private keys
    # stay backend-only.
    exit_pbk: str
    exit_sid: str
    relay_pbk: str
    relay_sid: str


class ChainRead(BaseModel):
    id: int
    name: str
    exit_xui_server_id: int
    relay_xui_server_id: int
    exit_sni: str
    status: str
    last_error: Optional[str] = None
    last_synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    channels: List[ChannelRead] = []
    # Connection hints for the UI — host the chain is reachable at.
    relay_host: str = ""
    exit_host: str = ""


def _channel_to_read(ch: ChainChannel) -> ChannelRead:
    return ChannelRead(
        id=ch.id, chain_id=ch.chain_id, name=ch.name, order=ch.order,
        exit_inbound_remote_id=ch.exit_inbound_remote_id,
        relay_inbound_remote_id=ch.relay_inbound_remote_id,
        exit_port=ch.exit_port, relay_port=ch.relay_port,
        exit_xhttp_path=ch.exit_xhttp_path,
        client_sni=ch.client_sni,
        relay_inbound_remark=ch.relay_inbound_remark,
        exit_inbound_remark=ch.exit_inbound_remark,
        exit_pbk=ch.exit_pbk, exit_sid=ch.exit_sid,
        relay_pbk=ch.relay_pbk, relay_sid=ch.relay_sid,
    )


async def _chain_to_read(
    chain: ProxyChain, session: AsyncSession,
) -> ChainRead:
    channels = (await session.exec(
        select(ChainChannel)
        .where(ChainChannel.chain_id == chain.id)
        .order_by(ChainChannel.order, ChainChannel.id),
    )).all()

    # Display the VPS IPs in the chain summary, not the panel domain.
    # The domain only acts as a Reality SNI cover; the actual
    # connection address is the IP.
    relay_host = ""
    exit_host = ""
    relay_xs = await session.get(XuiServer, chain.relay_xui_server_id)
    if relay_xs is not None:
        relay_srv = await session.get(Server, relay_xs.server_id)
        if relay_srv is not None:
            relay_host = relay_srv.host
    exit_xs = await session.get(XuiServer, chain.exit_xui_server_id)
    if exit_xs is not None:
        exit_srv = await session.get(Server, exit_xs.server_id)
        if exit_srv is not None:
            exit_host = exit_srv.host

    return ChainRead(
        id=chain.id, name=chain.name,
        exit_xui_server_id=chain.exit_xui_server_id,
        relay_xui_server_id=chain.relay_xui_server_id,
        exit_sni=chain.exit_sni,
        status=chain.status,
        last_error=chain.last_error,
        last_synced_at=chain.last_synced_at,
        created_at=chain.created_at, updated_at=chain.updated_at,
        channels=[_channel_to_read(ch) for ch in channels],
        relay_host=relay_host, exit_host=exit_host,
    )


@router.get("/chains", response_model=List[ChainRead])
async def list_chains(session: AsyncSession = Depends(get_session)):
    rows = (await session.exec(select(ProxyChain))).all()
    return [await _chain_to_read(c, session) for c in rows]


@router.post("/chains", response_model=ChainRead, status_code=201)
async def create_chain(
    body: ChainCreateBody, session: AsyncSession = Depends(get_session),
):
    if not body.channels:
        raise HTTPException(400, detail="At least one channel is required")
    if len(body.channels) > 32:
        # Soft cap — xrayTemplateConfig with 32 outbounds is already
        # the size of a small download; beyond that the relay's
        # config-validate step on /panel/server/restartXrayService
        # starts to be noticeably slow.
        raise HTTPException(
            400, detail="Too many channels (max 32 per chain)",
        )

    draft = ChainCreateDraft(
        name=body.name,
        exit_xui_server_id=body.exit_xui_server_id,
        relay_xui_server_id=body.relay_xui_server_id,
        exit_sni=body.exit_sni,
        channels=[
            ChannelDraft(
                name=ch.name,
                client_sni=ch.client_sni,
                exit_port=ch.exit_port,
                relay_port=ch.relay_port,
                exit_xhttp_path=ch.exit_xhttp_path,
                relay_remark=ch.relay_remark,
                exit_remark=ch.exit_remark,
            )
            for ch in body.channels
        ],
    )

    try:
        chain = await orchestrate_create(draft=draft, session=session)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc))
    except XuiAPIError as exc:
        raise HTTPException(
            502, detail=f"Panel error ({exc.kind}): {exc}",
        )
    return await _chain_to_read(chain, session)


@router.get("/chains/{chain_id}", response_model=ChainRead)
async def get_chain(chain_id: int, session: AsyncSession = Depends(get_session)):
    chain = await session.get(ProxyChain, chain_id)
    if chain is None:
        raise HTTPException(404, detail=f"Chain id={chain_id} not found")
    return await _chain_to_read(chain, session)


@router.delete("/chains/{chain_id}", status_code=204)
async def delete_chain(
    chain_id: int, session: AsyncSession = Depends(get_session),
):
    chain = await session.get(ProxyChain, chain_id)
    if chain is None:
        raise HTTPException(404, detail=f"Chain id={chain_id} not found")
    channels = (await session.exec(
        select(ChainChannel).where(ChainChannel.chain_id == chain_id),
    )).all()
    await orchestrate_delete(chain=chain, channels=list(channels), session=session)


@router.delete(
    "/chains/{chain_id}/channels/{channel_id}",
    status_code=204,
)
async def delete_chain_channel(
    chain_id: int,
    channel_id: int,
    session: AsyncSession = Depends(get_session),
):
    """Remove a single channel from a chain.

    Drops the channel's exit + relay inbounds on the panels, closes
    their UFW ports, re-pushes the relay's combined template
    (excluding this channel), cascade-cleans linked Nodes, then
    removes the `ChainChannel` row. If the deleted channel was the
    last one in the chain, the `ProxyChain` row is also dropped so
    the UI doesn't show a phantom empty chain.
    """
    chain = await session.get(ProxyChain, chain_id)
    if chain is None:
        raise HTTPException(404, detail=f"Chain id={chain_id} not found")
    channel = await session.get(ChainChannel, channel_id)
    if channel is None or channel.chain_id != chain_id:
        raise HTTPException(
            404,
            detail=f"Channel id={channel_id} not found on chain {chain_id}",
        )
    has_siblings = await orchestrate_delete_channel(
        chain=chain, channel=channel, session=session,
    )
    if not has_siblings:
        # Empty chain has no purpose — fold the chain row away too.
        # All panel-side state was already cleaned during the
        # channel removal (last-channel branch in
        # `orchestrate_delete_channel`).
        chain_fresh = await session.get(ProxyChain, chain_id)
        if chain_fresh is not None:
            await session.delete(chain_fresh)
            await session.commit()


# ── Chain healthcheck ────────────────────────────────────────────────


class ChainHealthCheckResult(BaseModel):
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: Optional[str] = None


class ChainHealthCheckResponse(BaseModel):
    chain_id: int
    ok: bool
    checks: List[ChainHealthCheckResult]


@router.post(
    "/chains/{chain_id}/healthcheck",
    response_model=ChainHealthCheckResponse,
)
async def healthcheck_chain(
    chain_id: int, session: AsyncSession = Depends(get_session),
):
    """Run a series of probes against both panels + the live xray
    config and return a structured pass/fail report.

    Checks (per side where applicable):
      * panel reachable + Bearer token valid
      * xray process running on the VPS
      * each chain inbound exists, is enabled, on the expected port
      * relay's running xray has the chain outbound + matching
        routing rule (otherwise traffic from clients gets blackholed)

    No PANEL state is mutated. PiTun's own `ProxyChain.status` is
    updated from the verdict (deployed ↔ degraded) so drift stays
    visible after the dialog is closed.
    """
    chain = await session.get(ProxyChain, chain_id)
    if chain is None:
        raise HTTPException(404, detail=f"Chain id={chain_id} not found")
    channels = list((await session.exec(
        select(ChainChannel).where(ChainChannel.chain_id == chain_id),
    )).all())

    exit_xs = await session.get(XuiServer, chain.exit_xui_server_id)
    relay_xs = await session.get(XuiServer, chain.relay_xui_server_id)
    exit_srv = await session.get(Server, exit_xs.server_id) if exit_xs else None
    relay_srv = await session.get(Server, relay_xs.server_id) if relay_xs else None

    checks: List[ChainHealthCheckResult] = []

    def _add(name: str, status: str, detail: Optional[str] = None) -> None:
        checks.append(ChainHealthCheckResult(name=name, status=status, detail=detail))

    async def _probe_side(
        label: str, xs: Optional[XuiServer], srv: Optional[Server],
        expected_remote_ids_ports: List[tuple[int, int, str]],
    ) -> Optional[Dict[str, Any]]:
        """Probe one panel: panel reachability, xray state, each inbound.
        Returns the running xray config dict (for relay caller to use)
        or None on early failure."""
        if xs is None or srv is None:
            _add(f"{label}: panel resolvable", "fail", "XuiServer or Server row missing")
            return None
        try:
            async with XuiClient(
                base_url=_api_base_url(xs, srv),
                api_token=xs.api_token, verify_tls=False,
            ) as c:
                # 1. Reachability + auth.
                try:
                    await c.probe()
                    _add(f"{label}: panel reachable", "ok", f"{srv.host}:{xs.panel_port}")
                except XuiAPIError as exc:
                    _add(f"{label}: panel reachable", "fail", str(exc))
                    return None

                # 2. xray state via server/status (Bearer).
                running_cfg: Optional[Dict[str, Any]] = None
                try:
                    body = await c._request("GET", "/panel/api/server/status")
                    obj = body.get("obj") or {}
                    xray_obj = obj.get("xray") or {}
                    state = str(xray_obj.get("state") or "")
                    if state == "running":
                        _add(f"{label}: xray running", "ok", xray_obj.get("version") or "")
                    else:
                        _add(
                            f"{label}: xray running", "fail",
                            f"state={state!r} errorMsg={xray_obj.get('errorMsg')!r}",
                        )
                except XuiAPIError as exc:
                    _add(f"{label}: xray running", "fail", str(exc))

                # 3. Live xray config (for outbound/routing checks below).
                try:
                    cfg_body = await c._request("GET", "/panel/api/server/getConfigJson")
                    running_cfg = cfg_body.get("obj") or {}
                except XuiAPIError as exc:
                    _add(f"{label}: running config readable", "warn", str(exc))

                # 4. Inbound checks.
                for remote_id, expected_port, expected_tag in expected_remote_ids_ports:
                    if not remote_id:
                        _add(
                            f"{label}: inbound for {expected_tag}",
                            "fail",
                            "no remote_id stored (chain create never completed?)",
                        )
                        continue
                    try:
                        ib_body = await c._request(
                            "GET", f"/panel/api/inbounds/get/{remote_id}",
                        )
                        ib = ib_body.get("obj") or {}
                        if not ib.get("enable"):
                            _add(
                                f"{label}: inbound #{remote_id}", "warn",
                                f"disabled (expected enable=true)",
                            )
                        elif int(ib.get("port") or 0) != expected_port:
                            _add(
                                f"{label}: inbound #{remote_id}", "fail",
                                f"port={ib.get('port')} expected {expected_port}",
                            )
                        else:
                            _add(
                                f"{label}: inbound #{remote_id}", "ok",
                                f"port={expected_port} tag={ib.get('tag')}",
                            )
                    except XuiAPIError as exc:
                        _add(
                            f"{label}: inbound #{remote_id}", "fail",
                            str(exc),
                        )
                return running_cfg
        except Exception as exc:  # noqa: BLE001
            _add(f"{label}: panel reachable", "fail", str(exc))
            return None

    # Exit side: just inbounds + xray-running.
    exit_targets = [
        (ch.exit_inbound_remote_id, ch.exit_port, _exit_tag(chain.id or 0, ch.name))
        for ch in channels
    ]
    await _probe_side("Exit", exit_xs, exit_srv, exit_targets)

    # Relay side: inbounds + check chain routing in running xray.
    relay_targets = [
        (ch.relay_inbound_remote_id, ch.relay_port, _relay_tag(chain.id or 0, ch.name))
        for ch in channels
    ]
    relay_cfg = await _probe_side("Relay", relay_xs, relay_srv, relay_targets)

    # The most diagnostic check: does the relay's running config
    # actually route chain inbound traffic through the chain outbound?
    # Without this, the inbound accepts TLS but xray has nowhere to
    # send the bytes → silent drop / blackhole (the bug that bit us
    # in beta.7 testing). Verify per channel.
    if relay_cfg:
        out_tags = {
            (ob.get("tag") or "") for ob in (relay_cfg.get("outbounds") or [])
        }
        rules = (relay_cfg.get("routing") or {}).get("rules") or []
        in_to_out: Dict[str, str] = {}
        for r in rules:
            for tag in (r.get("inboundTag") or []):
                in_to_out[tag] = r.get("outboundTag") or ""
        for ch in channels:
            relay_tag = _relay_tag(chain.id or 0, ch.name)
            chain_out_tag = f"chain-{chain.id or 0}-{ch.name}"
            if chain_out_tag not in out_tags:
                _add(
                    f"Relay: chain outbound for {ch.name}", "fail",
                    f"outbound {chain_out_tag!r} missing from running xray config "
                    "— template push may have silently failed",
                )
                continue
            mapped = in_to_out.get(relay_tag)
            if mapped == chain_out_tag:
                _add(
                    f"Relay: routing {ch.name}", "ok",
                    f"{relay_tag} → {chain_out_tag}",
                )
            else:
                _add(
                    f"Relay: routing {ch.name}", "fail",
                    f"inbound {relay_tag!r} routes to {mapped!r}, "
                    f"expected {chain_out_tag!r}",
                )

    # End-to-end live probe: ask the relay panel to actually dial
    # through each chain outbound and fetch the Google generate_204
    # test URL. This closes the loop — exits a "configured" but
    # actually-broken hop (Reality cert mismatch, UFW on exit closed,
    # exit xray crashed, etc.) won't pass.
    if relay_cfg and relay_xs is not None and relay_srv is not None:
        # Map running-config outbounds by tag so we send the same
        # JSON shape xray actually has.
        out_by_tag = {
            (ob.get("tag") or ""): ob
            for ob in (relay_cfg.get("outbounds") or [])
        }
        try:
            async with XuiClient(
                base_url=_api_base_url(relay_xs, relay_srv),
                api_token=relay_xs.api_token, verify_tls=False,
                panel_user=relay_xs.panel_user, panel_pass=relay_xs.panel_pass,
            ) as c:
                for ch in channels:
                    chain_out_tag = f"chain-{chain.id or 0}-{ch.name}"
                    ob = out_by_tag.get(chain_out_tag)
                    if ob is None:
                        # Already flagged above as fail; skip the live probe.
                        continue
                    try:
                        result = await c.test_outbound(ob)
                    except XuiAPIError as exc:
                        _add(
                            f"Relay → Exit live probe ({ch.name})",
                            "fail", f"panel rejected: {exc}",
                        )
                        continue
                    # The panel returns {success, delay, error?} —
                    # `success: true` + a delay in ms is a green.
                    if result.get("success") or (
                        isinstance(result.get("delay"), (int, float))
                        and result.get("delay", 0) > 0
                        and not result.get("error")
                    ):
                        delay = result.get("delay")
                        _add(
                            f"Relay → Exit live probe ({ch.name})", "ok",
                            f"delay={delay}ms" if delay is not None else None,
                        )
                    else:
                        err = result.get("error") or result.get("msg") or str(result)
                        _add(
                            f"Relay → Exit live probe ({ch.name})", "fail",
                            str(err)[:200],
                        )
        except Exception as exc:  # noqa: BLE001
            _add(
                "Relay → Exit live probe", "warn",
                f"cookie session failed: {exc}",
            )

    ok = all(c.status == "ok" for c in checks)

    # Persist the verdict. `degraded` was documented on the model and
    # rendered by the UI, but nothing ever set it — a chain whose relay
    # inbound had been hand-deleted in the panel kept showing "deployed"
    # once the healthcheck dialog was closed, and "Add client" happily
    # handed out URIs that could never connect.
    failed = [c.name for c in checks if c.status == "fail"]
    if chain.status in ("deployed", "degraded"):
        new_status = "deployed" if ok else ("degraded" if failed else chain.status)
        if new_status != chain.status:
            chain.status = new_status
            chain.last_error = (
                None if new_status == "deployed"
                else f"healthcheck failed: {', '.join(failed)[:800]}"
            )
            chain.updated_at = datetime.now(timezone.utc)
            session.add(chain)
            await session.commit()

    return ChainHealthCheckResponse(
        chain_id=chain_id, ok=ok, checks=checks,
    )


class ChainClientCreateBody(BaseModel):
    label: Optional[str] = None


class ChainClientChannelRead(BaseModel):
    id: int
    channel_id: int
    channel_name: str
    client_uuid: str
    exported_node_id: Optional[int] = None
    # Ready-to-paste VLESS URL for the user (assembled server-side
    # from the channel's relay-side Reality material).
    vless_uri: str


class ChainClientRead(BaseModel):
    id: int
    chain_id: int
    label: str
    created_at: datetime
    channels: List[ChainClientChannelRead] = []


def _vless_uri(
    *,
    uuid: str,
    host: str,
    port: int,
    pbk: str,
    sid: str,
    sni: str,
    remark: str,
) -> str:
    """Build a vless+tcp+reality URL the user pastes into a client.

    Mirrors the format `setup-relay.sh` emits: `vless://<uuid>@<host>:
    <port>?type=tcp&security=reality&fp=chrome&pbk=...&sni=...&sid=...
    &flow=xtls-rprx-vision#<remark>`.
    """
    import urllib.parse as up
    qs = up.urlencode({
        "type": "tcp",
        "security": "reality",
        "fp": "chrome",
        "pbk": pbk,
        "sni": sni,
        "sid": sid,
        "flow": "xtls-rprx-vision",
    })
    fragment = up.quote(remark, safe="")
    return f"vless://{uuid}@{host}:{port}?{qs}#{fragment}"


async def _chain_client_to_read(
    chain_client: ChainClient, session: AsyncSession,
) -> ChainClientRead:
    chain = await session.get(ProxyChain, chain_client.chain_id)
    relay_xs = await session.get(XuiServer, chain.relay_xui_server_id) if chain else None
    relay_srv = await session.get(Server, relay_xs.server_id) if relay_xs else None
    # IP, not domain — see the export-nodes endpoint for the
    # reasoning (the domain only matters as a Reality SNI cover, and
    # that lives in `client_sni`).
    relay_host = relay_srv.host if relay_srv else "127.0.0.1"

    pairs = (await session.exec(
        select(ChainClientChannel)
        .where(ChainClientChannel.chain_client_id == chain_client.id),
    )).all()
    channels = (await session.exec(
        select(ChainChannel)
        .where(ChainChannel.chain_id == chain_client.chain_id)
        .order_by(ChainChannel.order, ChainChannel.id),
    )).all()
    channels_by_id = {ch.id: ch for ch in channels}

    out_channels: List[ChainClientChannelRead] = []
    for pair in pairs:
        ch = channels_by_id.get(pair.channel_id)
        if ch is None:
            continue
        uri = _vless_uri(
            uuid=pair.client_uuid,
            host=relay_host,
            port=ch.relay_port,
            pbk=ch.relay_pbk,
            sid=ch.relay_sid,
            sni=ch.client_sni,
            remark=f"{chain.name if chain else 'chain'}-{ch.name}",
        )
        out_channels.append(ChainClientChannelRead(
            id=pair.id, channel_id=ch.id, channel_name=ch.name,
            client_uuid=pair.client_uuid,
            exported_node_id=pair.exported_node_id,
            vless_uri=uri,
        ))

    return ChainClientRead(
        id=chain_client.id, chain_id=chain_client.chain_id,
        label=chain_client.label, created_at=chain_client.created_at,
        channels=out_channels,
    )


@router.get("/chains/{chain_id}/clients", response_model=List[ChainClientRead])
async def list_chain_clients(
    chain_id: int, session: AsyncSession = Depends(get_session),
):
    chain = await session.get(ProxyChain, chain_id)
    if chain is None:
        raise HTTPException(404, detail=f"Chain id={chain_id} not found")
    rows = (await session.exec(
        select(ChainClient).where(ChainClient.chain_id == chain_id)
        .order_by(ChainClient.id),
    )).all()
    return [await _chain_client_to_read(r, session) for r in rows]


@router.post(
    "/chains/{chain_id}/clients",
    response_model=ChainClientRead, status_code=201,
)
async def create_chain_client(
    chain_id: int,
    body: ChainClientCreateBody,
    session: AsyncSession = Depends(get_session),
):
    chain = await session.get(ProxyChain, chain_id)
    if chain is None:
        raise HTTPException(404, detail=f"Chain id={chain_id} not found")
    if chain.status != "deployed":
        raise HTTPException(
            400,
            detail=(
                f"Chain status is {chain.status!r} — cannot add clients "
                "until it's 'deployed'."
            ),
        )
    channels = (await session.exec(
        select(ChainChannel).where(ChainChannel.chain_id == chain_id)
        .order_by(ChainChannel.order, ChainChannel.id),
    )).all()
    if not channels:
        raise HTTPException(400, detail="Chain has no channels")

    try:
        cc = await orchestrate_add_client(
            chain=chain, channels=list(channels), label=body.label,
            session=session,
        )
    except XuiAPIError as exc:
        raise HTTPException(
            502, detail=f"Panel error ({exc.kind}): {exc}",
        )
    return await _chain_client_to_read(cc, session)


@router.delete(
    "/chains/{chain_id}/clients/{chain_client_id}", status_code=204,
)
async def delete_chain_client(
    chain_id: int, chain_client_id: int,
    session: AsyncSession = Depends(get_session),
):
    chain = await session.get(ProxyChain, chain_id)
    chain_client = await session.get(ChainClient, chain_client_id)
    if chain is None or chain_client is None or chain_client.chain_id != chain_id:
        raise HTTPException(404, detail="Chain or chain-client not found")
    pairs = (await session.exec(
        select(ChainClientChannel)
        .where(ChainClientChannel.chain_client_id == chain_client_id),
    )).all()
    channels = (await session.exec(
        select(ChainChannel).where(ChainChannel.chain_id == chain_id),
    )).all()
    channels_by_id = {ch.id: ch for ch in channels}
    await orchestrate_delete_client(
        chain=chain, chain_client=chain_client,
        pairs=list(pairs), channels_by_id=channels_by_id,
        session=session,
    )


class ChainClientExportBody(BaseModel):
    """Pick which channels to export. Empty list = all of them."""
    channel_ids: List[int] = []
    # Optional Node-name prefix; defaults to `<chain.name>-<client.label>`.
    name_prefix: Optional[str] = None


@router.post("/chains/{chain_id}/clients/{chain_client_id}/export-nodes")
async def export_chain_client_nodes(
    chain_id: int, chain_client_id: int,
    body: ChainClientExportBody,
    session: AsyncSession = Depends(get_session),
):
    """Convert the chain-client's per-channel UUIDs into routable
    Node rows. One Node per (chain-client × channel) pair selected.

    Idempotent on re-call: existing exports (where exported_node_id
    is already set on the channel pair) are skipped rather than
    duplicated. The user can delete the Node manually to get a
    fresh export."""
    chain = await session.get(ProxyChain, chain_id)
    chain_client = await session.get(ChainClient, chain_client_id)
    if chain is None or chain_client is None or chain_client.chain_id != chain_id:
        raise HTTPException(404, detail="Chain or chain-client not found")

    pairs = (await session.exec(
        select(ChainClientChannel)
        .where(ChainClientChannel.chain_client_id == chain_client_id),
    )).all()
    channels = (await session.exec(
        select(ChainChannel).where(ChainChannel.chain_id == chain_id),
    )).all()
    channels_by_id = {ch.id: ch for ch in channels}

    selected_ids = set(body.channel_ids) if body.channel_ids else None
    name_prefix = body.name_prefix or f"{chain.name}-{chain_client.label}"

    relay_xs = await session.get(XuiServer, chain.relay_xui_server_id)
    relay_srv = await session.get(Server, relay_xs.server_id) if relay_xs else None
    # Use the VPS's IP as the dial address, not the panel domain. The
    # domain only matters as a Reality SNI cover — and that lives in
    # `client_sni` per-channel. The IP also avoids extra DNS lookups
    # on every health-check and lets us bypass DNS-level blocks.
    relay_host = (relay_srv.host if relay_srv else "127.0.0.1")

    created_node_ids: List[int] = []
    for pair in pairs:
        ch = channels_by_id.get(pair.channel_id)
        if ch is None:
            continue
        if selected_ids is not None and pair.channel_id not in selected_ids:
            continue
        if pair.exported_node_id is not None:
            # Already exported — skip silently. The UI sees the
            # node link via the chain-client read.
            continue
        # Field names must match the Node ORM exactly. SQLModel /
        # Pydantic silently drops unknown kwargs (no AttributeError),
        # which is how earlier exports landed with tls='none' and
        # blank reality_pbk / reality_sid — those kwargs were named
        # `security`, `pbk`, `sid`.
        node = Node(
            name=f"{name_prefix}-{ch.name}",
            protocol="vless",
            address=relay_host,
            port=ch.relay_port,
            uuid=pair.client_uuid,
            sni=ch.client_sni,
            reality_pbk=ch.relay_pbk,
            reality_sid=ch.relay_sid,
            flow="xtls-rprx-vision",
            transport="tcp",
            tls="reality",
            fingerprint="chrome",
        )
        session.add(node)
        await session.flush()
        pair.exported_node_id = node.id
        session.add(pair)
        created_node_ids.append(node.id)
    await session.commit()
    return {"exported_node_ids": created_node_ids}
