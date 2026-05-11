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

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.core.xui_api import XuiAPIError, XuiClient
from app.core.xui_chain import (
    ChainCreateDraft,
    ChannelDraft,
    orchestrate_add_client,
    orchestrate_create,
    orchestrate_delete,
    orchestrate_delete_client,
)
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
router = APIRouter(prefix="/xui", tags=["xui"])


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
                placeholder=f.placeholder,
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

    Bare mode: `https://<server.host>:<panel_port><basepath>` (self-signed)
    xui-pro mode: `http://<domain>:<panel_port><basepath>` (TLS terminates
                  at nginx — the panel itself runs HTTP behind it).
    The XuiClient verify_tls flag stays False in both cases — bare uses
    self-signed; xui-pro doesn't see a cert at all because the URL is
    HTTP.
    """
    if xs.mode == "xui-pro" and xs.domain:
        return f"http://{xs.domain}:{xs.panel_port}{xs.panel_basepath}"
    return f"https://{srv.host}:{xs.panel_port}{xs.panel_basepath}"


async def _get_xs_or_404(
    server_id: int, session: AsyncSession,
) -> tuple[XuiServer, Server]:
    xs = (await session.exec(
        select(XuiServer).where(XuiServer.id == server_id),
    )).scalars().first()
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
    rows = (await session.exec(select(XuiServer))).scalars().all()
    out: List[XuiServerRead] = []
    for xs in rows:
        srv = await session.get(Server, xs.server_id)
        if srv is None:
            continue
        out.append(_xui_to_read(xs, srv))
    return out


@router.post("/servers/import", response_model=XuiServerRead, status_code=201)
async def import_xui_server(
    body: XuiServerImportBody,
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
        if not (body.api_token and body.panel_port and body.panel_basepath
                and body.panel_user and body.panel_pass):
            raise HTTPException(
                400,
                detail="Provide either `uri` or all of api_token + "
                       "panel_port + panel_basepath + panel_user + panel_pass.",
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

    # Probe the token + URL before committing the row.
    base_url = (
        f"http://{domain}:{panel_port}{panel_basepath}"
        if mode == "xui-pro" and domain
        else f"https://{srv.host}:{panel_port}{panel_basepath}"
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
    )).scalars().first()
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
    )).scalars().first()
    if xs is None:
        raise HTTPException(404, detail=f"XuiServer id={xui_server_id} not found")
    await session.delete(xs)
    await session.commit()


@router.post("/servers/{xui_server_id}/probe", response_model=XuiServerRead)
async def probe_xui_server(
    xui_server_id: int, session: AsyncSession = Depends(get_session),
):
    """Re-test the Bearer token. Updates last_check / last_check_error
    on the row regardless of outcome so the UI badge reflects the
    truth."""
    xs, srv = await _get_xs_or_404(xui_server_id, session)
    base_url = _api_base_url(xs, srv)
    now = datetime.now(timezone.utc)
    async with XuiClient(
        base_url=base_url, api_token=xs.api_token, verify_tls=False,
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
    await session.refresh(xs)
    return _xui_to_read(xs, srv)


@router.get("/servers/{xui_server_id}/inbounds")
async def list_inbounds(
    xui_server_id: int, session: AsyncSession = Depends(get_session),
):
    """Live list of inbounds from the panel. Includes everything —
    PiTun-managed AND hand-added. Frontend filters by `email` prefix
    when it wants the PiTun-managed subset."""
    xs, srv = await _get_xs_or_404(xui_server_id, session)
    base_url = _api_base_url(xs, srv)
    async with XuiClient(
        base_url=base_url, api_token=xs.api_token, verify_tls=False,
    ) as client:
        try:
            inbounds = await client.list_inbounds()
        except XuiAPIError as exc:
            raise HTTPException(502, detail=f"Panel error ({exc.kind}): {exc}")
    return inbounds


@router.post("/servers/{xui_server_id}/inbounds", status_code=201)
async def create_inbound(
    xui_server_id: int,
    body: InboundCreateBody,
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
    ) as client:
        # Resolve server-generated values via the panel itself so
        # the frontend never handles the private key.
        resolved: Dict[str, Any] = {}
        try:
            if preset.protocol in ("vless", "trojan"):
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
    """Delete an inbound on the panel + drop any cached XuiClient
    rows that referenced it. Hand-added clients on a hand-added
    inbound aren't tracked — the panel just makes them disappear."""
    xs, srv = await _get_xs_or_404(xui_server_id, session)
    base_url = _api_base_url(xs, srv)
    async with XuiClient(
        base_url=base_url, api_token=xs.api_token, verify_tls=False,
    ) as client:
        try:
            await client.del_inbound(inbound_id)
        except XuiAPIError as exc:
            raise HTTPException(
                502,
                detail=f"Panel rejected del-inbound ({exc.kind}): {exc}",
            )

    # Drop our cache rows. We don't cascade-delete the exported Nodes
    # — those stand on their own; the user can clean them up via
    # /api/nodes if they want.
    cached = (await session.exec(
        select(XuiClientModel)
        .where(XuiClientModel.xui_server_id == xui_server_id)
        .where(XuiClientModel.inbound_remote_id == inbound_id),
    )).scalars().all()
    for row in cached:
        await session.delete(row)
    await session.commit()


@router.post(
    "/servers/{xui_server_id}/inbounds/{inbound_id}/clients",
    status_code=201,
)
async def add_client(
    xui_server_id: int, inbound_id: int,
    body: ClientCreateBody,
    session: AsyncSession = Depends(get_session),
):
    """Add a client to an existing inbound + cache the row in
    `xuiclient` for /sync + node-export bookkeeping."""
    xs, srv = await _get_xs_or_404(xui_server_id, session)
    label = body.label or f"pi-{secrets.token_hex(4)}"

    base_url = _api_base_url(xs, srv)
    async with XuiClient(
        base_url=base_url, api_token=xs.api_token, verify_tls=False,
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

    row = XuiClientModel(
        xui_server_id=xui_server_id,
        inbound_remote_id=inbound_id,
        client_uuid=uuid if protocol != "trojan" else "",
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
    cached = (await session.exec(
        select(XuiClientModel)
        .where(XuiClientModel.xui_server_id == xui_server_id)
        .where(XuiClientModel.inbound_remote_id == inbound_id)
        .where(XuiClientModel.client_uuid == client_uuid),
    )).scalars().first()
    if cached is not None:
        await session.delete(cached)
        await session.commit()


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
    )).scalars().all()

    relay_host = ""
    exit_host = ""
    relay_xs = await session.get(XuiServer, chain.relay_xui_server_id)
    if relay_xs is not None:
        relay_srv = await session.get(Server, relay_xs.server_id)
        if relay_srv is not None:
            relay_host = relay_xs.domain or relay_srv.host
    exit_xs = await session.get(XuiServer, chain.exit_xui_server_id)
    if exit_xs is not None:
        exit_srv = await session.get(Server, exit_xs.server_id)
        if exit_srv is not None:
            exit_host = exit_xs.domain or exit_srv.host

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
    rows = (await session.exec(select(ProxyChain))).scalars().all()
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
    )).scalars().all()
    await orchestrate_delete(chain=chain, channels=list(channels), session=session)


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
    relay_host = (
        (relay_xs.domain or relay_srv.host)
        if relay_xs and relay_srv
        else "127.0.0.1"
    )

    pairs = (await session.exec(
        select(ChainClientChannel)
        .where(ChainClientChannel.chain_client_id == chain_client.id),
    )).scalars().all()
    channels = (await session.exec(
        select(ChainChannel)
        .where(ChainChannel.chain_id == chain_client.chain_id)
        .order_by(ChainChannel.order, ChainChannel.id),
    )).scalars().all()
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
    )).scalars().all()
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
    )).scalars().all()
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
    )).scalars().all()
    channels = (await session.exec(
        select(ChainChannel).where(ChainChannel.chain_id == chain_id),
    )).scalars().all()
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
    )).scalars().all()
    channels = (await session.exec(
        select(ChainChannel).where(ChainChannel.chain_id == chain_id),
    )).scalars().all()
    channels_by_id = {ch.id: ch for ch in channels}

    selected_ids = set(body.channel_ids) if body.channel_ids else None
    name_prefix = body.name_prefix or f"{chain.name}-{chain_client.label}"

    relay_xs = await session.get(XuiServer, chain.relay_xui_server_id)
    relay_srv = await session.get(Server, relay_xs.server_id) if relay_xs else None
    relay_host = (relay_xs.domain or relay_srv.host) if relay_xs and relay_srv else "127.0.0.1"

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
        node = Node(
            name=f"{name_prefix}-{ch.name}",
            protocol="vless",
            address=relay_host,
            port=ch.relay_port,
            uuid=pair.client_uuid,
            sni=ch.client_sni,
            pbk=ch.relay_pbk,
            sid=ch.relay_sid,
            flow="xtls-rprx-vision",
            transport="tcp",
            security="reality",
            fingerprint="chrome",
        )
        session.add(node)
        await session.flush()
        pair.exported_node_id = node.id
        session.add(pair)
        created_node_ids.append(node.id)
    await session.commit()
    return {"exported_node_ids": created_node_ids}
