"""WireGuard server-side client management (since v1.3.0-beta.4).

Routes mounted under `/api/servers/{server_id}/deployments/wireguard/clients`.
Built on the new `DeploymentClient` layer (migration 012).

  GET    /                       — list clients PiTun knows about
  POST   /                       — add a new client (SSH-runs the
                                   server's setup-wireguard-server.sh
                                   add-client subcommand, parses the
                                   resulting URI, persists DC row)
  POST   /sync                   — `list-clients` on the server,
                                   reconcile against local rows
  GET    /{name}/conf            — return the full INI conf text
                                   (for download / QR-code generation)
  POST   /{name}/export-node     — create a Node from this client
  DELETE /{name}                 — remove from server + delete DC row
                                   (Nodes exported from it are kept
                                   but get `client_orphan = true`)

Operations are SYNCHRONOUS (no JobManager / WS streaming) — each
SSH call is sub-second to a few seconds, so the request-response
shape is fine. If a client install ever gets much heavier we can
flip to the JobManager flow used by `POST /servers/{id}/deploy`.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.deploy import build_wireguard_env, load_script
from app.core.ssh import exec_remote_script
from app.core.uri_parser import parse_uri
from app.database import get_session
from app.models import DeploymentClient, Node, Server, ServerDeployment
from app.schemas import (
    DeploymentClientConf,
    DeploymentClientCreate,
    DeploymentClientList,
    DeploymentClientRead,
    DeploymentClientSyncResult,
    ExportClientToNodeRequest,
    NodeRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/servers/{server_id:int}/deployments/wireguard/clients",
    tags=["server-clients"],
)


# ── Helpers ──────────────────────────────────────────────────────────────────

# Output contract markers (match scripts/setup-wireguard-server.sh)
_CONF_BEGIN_RE = re.compile(r"^PITUN-CLIENT-CONF-BEGIN (.+)$", re.MULTILINE)
_CONF_END_RE = re.compile(r"^PITUN-CLIENT-CONF-END (.+)$", re.MULTILINE)
_CLIENTS_LINE_RE = re.compile(r"^CLIENTS=(.+)$", re.MULTILINE)
_REMOVED_LINE_RE = re.compile(r"^REMOVED=(.+)$", re.MULTILINE)


async def _resolve_deployment(
    server_id: int, session: AsyncSession
) -> tuple[Server, ServerDeployment]:
    """404 if the server or its wireguard deployment is missing.
    Returns both for the endpoint's downstream use."""
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    dep = (await session.exec(
        select(ServerDeployment)
        .where(ServerDeployment.server_id == server_id)
        .where(ServerDeployment.protocol == "wireguard")
    )).first()
    if not dep:
        raise HTTPException(
            status_code=404,
            detail="No wireguard deployment on this server (run install first)",
        )
    return server, dep


def _extract_inline_conf(stdout: str, name: str) -> Optional[str]:
    """Pull the INI conf out of the script's
    `PITUN-CLIENT-CONF-BEGIN <name>` / `…-END <name>` block. Returns
    the conf body without the markers, or None if not present.
    """
    begin = re.search(rf"^PITUN-CLIENT-CONF-BEGIN {re.escape(name)}$",
                      stdout, re.MULTILINE)
    end = re.search(rf"^PITUN-CLIENT-CONF-END {re.escape(name)}$",
                    stdout, re.MULTILINE)
    if not begin or not end or end.start() <= begin.end():
        return None
    return stdout[begin.end():end.start()].strip("\r\n")


async def _exec_wg(
    server: Server, sub_command: str, *,
    client_name: Optional[str] = None,
    extra_env: Optional[dict[str, str]] = None,
    timeout: float = 60.0,
):
    """Run setup-wireguard-server.sh <sub-command> on the target server
    via SSH. Synchronous (waits to completion), captures stdout/stderr.
    Returns the `DeployResult` from `core.ssh.exec_remote_script`.

    On `ok=False`, raises 500 with the script's stderr tail so the
    caller doesn't have to repeat that handling everywhere.
    """
    env = build_wireguard_env(
        client_name=client_name or "",
        sub_command=sub_command,  # type: ignore[arg-type]
    )
    if extra_env:
        env.update(extra_env)

    result = await exec_remote_script(
        host=server.host,
        port=server.port,
        username=server.user,
        password=server.password if server.auth_type == "password" else None,
        private_key=server.private_key if server.auth_type == "key" else None,
        passphrase=server.passphrase if server.auth_type == "key" else None,
        script_content=load_script("wireguard"),
        env=env,
        timeout=timeout,
    )
    if not result.ok:
        # Surface the last stderr line as the user-visible reason.
        last_err = ""
        for line in reversed((result.stderr or "").splitlines()):
            if line.strip():
                last_err = line.strip()
                break
        raise HTTPException(
            status_code=500,
            detail=(
                f"setup-wireguard-server.sh {sub_command} failed "
                f"(exit={result.exit_code}): {last_err or result.error or 'unknown error'}"
            ),
        )
    return result


def _row_to_read(
    dc: DeploymentClient, exported_node_ids: list[int]
) -> DeploymentClientRead:
    """Project a DC row to API shape — strips private key + PSK."""
    return DeploymentClientRead(
        id=dc.id,
        deployment_id=dc.deployment_id,
        name=dc.name,
        wg_public_key=dc.wg_public_key,
        wg_endpoint=dc.wg_endpoint,
        wg_local_address=dc.wg_local_address,
        wg_mtu=dc.wg_mtu,
        dns_servers=dc.dns_servers,
        allowed_ips=dc.allowed_ips,
        status=dc.status,
        exported_node_ids=exported_node_ids,
        created_at=dc.created_at,
        last_synced_at=dc.last_synced_at,
    )


async def _list_with_node_links(
    deployment_id: int, session: AsyncSession
) -> list[DeploymentClientRead]:
    """Fetch DCs + their exported Node ids in one pass."""
    dcs = (await session.exec(
        select(DeploymentClient)
        .where(DeploymentClient.deployment_id == deployment_id)
        .order_by(DeploymentClient.id)
    )).all()
    if not dcs:
        return []
    # Pull all Nodes pointing back at any of these DCs.
    dc_ids = [dc.id for dc in dcs if dc.id is not None]
    nodes_by_dc: dict[int, list[int]] = {dc_id: [] for dc_id in dc_ids}
    if dc_ids:
        nodes = (await session.exec(
            select(Node).where(Node.from_deployment_client_id.in_(dc_ids))  # type: ignore[union-attr]
        )).all()
        for n in nodes:
            if n.from_deployment_client_id is not None:
                nodes_by_dc.setdefault(n.from_deployment_client_id, []).append(n.id)
    return [
        _row_to_read(dc, nodes_by_dc.get(dc.id or -1, []))
        for dc in dcs
    ]


# ── GET / — list ─────────────────────────────────────────────────────────────


@router.get("", response_model=DeploymentClientList)
async def list_clients(
    server_id: int, session: AsyncSession = Depends(get_session)
):
    """List all WireGuard clients PiTun knows about for this server.
    No SSH call — purely local DB. Use POST /sync to refresh against
    the server-side state.
    """
    _server, dep = await _resolve_deployment(server_id, session)
    clients = await _list_with_node_links(dep.id, session)
    return DeploymentClientList(clients=clients)


# ── POST / — add client ──────────────────────────────────────────────────────


@router.post("", response_model=DeploymentClientRead, status_code=201)
async def add_client(
    server_id: int,
    body: DeploymentClientCreate,
    session: AsyncSession = Depends(get_session),
):
    """Add a new WireGuard peer to this server, persist it as a
    DeploymentClient. The server-side script generates the priv/pub/
    psk + allocates the next free /32+/128 in the WG subnet, then
    appends a [Peer] block and hot-reloads via `wg syncconf`.
    """
    server, dep = await _resolve_deployment(server_id, session)

    # Sane name validation (mirrors the script's regex; we surface a
    # 400 here rather than letting the script err out at exit 1).
    if not re.match(r"^[a-zA-Z0-9_-]+$", body.name) or len(body.name) > 64:
        raise HTTPException(
            status_code=400,
            detail="Client name must be [a-zA-Z0-9_-]+ and ≤64 chars",
        )

    # Fail fast on duplicate (the script checks too, but a 400 here
    # is clearer than a 500 with a script-side error message).
    existing = (await session.exec(
        select(DeploymentClient)
        .where(DeploymentClient.deployment_id == dep.id)
        .where(DeploymentClient.name == body.name)
    )).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"Client '{body.name}' already exists on this server",
        )

    result = await _exec_wg(server, "add-client", client_name=body.name)
    uri = _extract_uri_from_stdout(result.stdout)
    if not uri:
        raise HTTPException(
            status_code=500,
            detail="Script succeeded but did not emit URI=… (script bug?)",
        )
    parsed = parse_uri(uri)
    if not parsed or parsed.get("protocol") != "wireguard":
        raise HTTPException(
            status_code=500,
            detail=f"Could not parse WG URI from script output: {uri[:200]}",
        )

    inline_conf = _extract_inline_conf(result.stdout, body.name)

    dc = DeploymentClient(
        deployment_id=dep.id,
        name=body.name,
        wg_private_key=parsed.get("wg_private_key"),
        wg_public_key=parsed.get("wg_public_key"),
        wg_preshared_key=parsed.get("wg_preshared_key"),
        wg_endpoint=parsed.get("wg_endpoint"),
        wg_mtu=parsed.get("wg_mtu", 1420),
        wg_local_address=parsed.get("wg_local_address"),
        # DNS + AllowedIPs aren't in the URI shape — pull from script's
        # client conf INI (the verbatim block we stash in config_json
        # below has them) or leave None (UI shows "default" when null).
        dns_servers=_parse_ini_field(inline_conf, "DNS"),
        allowed_ips=_parse_ini_field(inline_conf, "AllowedIPs"),
        config_json=json.dumps({"client_conf_ini": inline_conf}) if inline_conf else None,
        status="available",
        last_synced_at=datetime.now(timezone.utc),
    )
    session.add(dc)
    await session.commit()
    await session.refresh(dc)

    return _row_to_read(dc, [])


# ── DELETE /{name} — remove client ───────────────────────────────────────────


@router.delete("/{name}", status_code=204)
async def remove_client(
    server_id: int, name: str,
    session: AsyncSession = Depends(get_session),
):
    """Remove a WireGuard peer from the server + delete the local
    DC row. Any Nodes that were exported from this client are KEPT
    but flagged `client_orphan = true` so the user sees "server-side
    client deleted" in the Nodes UI.
    """
    server, dep = await _resolve_deployment(server_id, session)

    dc = (await session.exec(
        select(DeploymentClient)
        .where(DeploymentClient.deployment_id == dep.id)
        .where(DeploymentClient.name == name)
    )).first()
    if not dc:
        raise HTTPException(status_code=404, detail=f"Client '{name}' not found")

    result = await _exec_wg(server, "remove-client", client_name=name)
    if not _REMOVED_LINE_RE.search(result.stdout):
        # Script ran ok=true (exit 0) but didn't print our marker —
        # treat as soft fail; we still proceed to local cleanup.
        logger.warning(
            "remove-client on server_id=%s name=%s did not print REMOVED= "
            "marker — script may be from an older release. Cleaning up "
            "local state anyway.",
            server_id, name,
        )

    dc_id = dc.id
    await session.delete(dc)

    # Mark any Nodes exported from this DC as orphaned.
    if dc_id is not None:
        orphan_nodes = (await session.exec(
            select(Node).where(Node.from_deployment_client_id == dc_id)
        )).all()
        for n in orphan_nodes:
            n.client_orphan = True
            session.add(n)
    await session.commit()


# ── POST /sync — reconcile local vs server state ─────────────────────────────


@router.post("/sync", response_model=DeploymentClientSyncResult)
async def sync_clients(
    server_id: int, session: AsyncSession = Depends(get_session)
):
    """Re-list peers on the server and reconcile against local DB.

    Three buckets in the result:
      * `added`     — server-only peers, just imported into our DB.
                      Note: synced-in clients have wg_private_key=NULL
                      since the server only has the public half. The
                      Node-export action will fail for these with
                      a clear 400; admins can `Re-key` (remove +
                      add fresh) if they want a Node from them.
      * `unchanged` — peers we already knew about; status untouched
                      (still 'available' / 'exported').
      * `orphaned`  — PiTun-side peers no longer present on the server
                      (status flipped to 'orphan'). Any Node exported
                      from them is also flagged `client_orphan = true`.
    """
    server, dep = await _resolve_deployment(server_id, session)

    result = await _exec_wg(server, "list-clients")
    m = _CLIENTS_LINE_RE.search(result.stdout)
    if not m:
        raise HTTPException(
            status_code=500,
            detail="Script did not emit CLIENTS=… line (script bug?)",
        )
    try:
        server_clients = json.loads(m.group(1))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=500, detail=f"Could not parse CLIENTS JSON: {exc}",
        )
    if not isinstance(server_clients, list):
        raise HTTPException(status_code=500, detail="CLIENTS payload not a list")

    server_names = {c.get("name") for c in server_clients if c.get("name")}

    # Pull current local rows (DC) + nodes-by-DC for orphan flagging.
    local = {
        dc.name: dc
        for dc in (await session.exec(
            select(DeploymentClient)
            .where(DeploymentClient.deployment_id == dep.id)
        )).all()
    }

    added: list[str] = []
    unchanged: list[str] = []
    orphaned: list[str] = []

    now = datetime.now(timezone.utc)

    for c in server_clients:
        nm = c.get("name")
        if not nm:
            continue
        if nm in local:
            # Refresh sync timestamp + un-orphan if it had been flagged
            row = local[nm]
            row.last_synced_at = now
            if row.status == "orphan":
                row.status = "available"
            session.add(row)
            unchanged.append(nm)
        else:
            # New on server side — import sans private key
            allowed_ips_field = c.get("address", "")  # script names it `address`
            new = DeploymentClient(
                deployment_id=dep.id,
                name=nm,
                wg_private_key=None,  # we don't have it; can't export to Node
                wg_public_key=c.get("public_key"),
                wg_preshared_key=None,
                wg_endpoint=f"{server.host}:{_server_port_from_dep(dep)}",
                wg_local_address=allowed_ips_field,
                status="available",
                last_synced_at=now,
            )
            session.add(new)
            added.append(nm)

    # Anything local-but-not-on-server flips to orphan + flags exported Nodes.
    for nm, row in local.items():
        if nm not in server_names:
            if row.status != "orphan":
                row.status = "orphan"
                session.add(row)
                # Flag exported Nodes
                if row.id is not None:
                    n_rows = (await session.exec(
                        select(Node).where(
                            Node.from_deployment_client_id == row.id
                        )
                    )).all()
                    for n in n_rows:
                        n.client_orphan = True
                        session.add(n)
            orphaned.append(nm)

    await session.commit()
    return DeploymentClientSyncResult(
        added=added, unchanged=unchanged, orphaned=orphaned,
    )


# ── GET /{name}/conf — download client conf ──────────────────────────────────


@router.get("/{name}/conf", response_model=DeploymentClientConf)
async def get_client_conf(
    server_id: int, name: str,
    session: AsyncSession = Depends(get_session),
):
    """Return the full WireGuard `.conf` INI for this peer. Includes
    the private key — used for downloading the file or rendering a
    QR code in the UI. Same threat model as Node creds (LAN-only,
    plain SQLite). Use sparingly.
    """
    server, dep = await _resolve_deployment(server_id, session)
    dc = (await session.exec(
        select(DeploymentClient)
        .where(DeploymentClient.deployment_id == dep.id)
        .where(DeploymentClient.name == name)
    )).first()
    if not dc:
        raise HTTPException(status_code=404, detail=f"Client '{name}' not found")

    # Prefer the cached INI from when we added the client. If absent
    # (e.g. row came in via sync, no priv key locally), fall back to
    # asking the server's get-conf sub-command — but if priv key is
    # gone, the conf there will be missing the [Interface] PrivateKey
    # too (the server only stores public side). Surface that.
    cached_ini: Optional[str] = None
    if dc.config_json:
        try:
            cached_ini = json.loads(dc.config_json).get("client_conf_ini")
        except Exception:  # noqa: BLE001
            cached_ini = None
    if cached_ini:
        return DeploymentClientConf(name=name, wg_conf=cached_ini)

    # Fallback: ask server. If the conf file is gone, surface as 404.
    result = await _exec_wg(server, "get-conf", client_name=name, timeout=30.0)
    inline = _extract_inline_conf(result.stdout, name)
    if not inline:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Server has no stored conf for '{name}' (likely a peer "
                "added externally — PiTun doesn't have its private key. "
                "Remove + re-add the client to regenerate."
            ),
        )
    return DeploymentClientConf(name=name, wg_conf=inline)


# ── POST /{name}/export-node — create Node from this client ──────────────────


@router.post("/{name}/export-node", response_model=NodeRead, status_code=201)
async def export_to_node(
    server_id: int, name: str,
    body: ExportClientToNodeRequest,
    session: AsyncSession = Depends(get_session),
):
    """Create a `Node` row from this client's conf so PiTun starts
    routing through it. The Node is linked back via
    `from_deployment_client_id` so the Nodes UI can show the source
    server, and so we can flag `client_orphan` if the underlying
    DeploymentClient ever gets deleted on the server.

    Multiple Nodes from the same client are allowed (the same conf
    is re-imported into a fresh PiTun instance, etc.) — duplicate
    detection at the (protocol+address+port+uuid) level happens in
    the import-json endpoint, not here.
    """
    _server, dep = await _resolve_deployment(server_id, session)

    dc = (await session.exec(
        select(DeploymentClient)
        .where(DeploymentClient.deployment_id == dep.id)
        .where(DeploymentClient.name == name)
    )).first()
    if not dc:
        raise HTTPException(status_code=404, detail=f"Client '{name}' not found")

    if not dc.wg_private_key:
        raise HTTPException(
            status_code=400,
            detail=(
                "Client has no private key locally (synced from server, "
                "but server only stores the public half). Remove + re-add "
                "the client to regenerate, then export."
            ),
        )

    node_name = body.node_name or dc.name
    address, port = (dc.wg_endpoint or ":").split(":", 1)
    try:
        port_int = int(port)
    except ValueError:
        port_int = 51820

    node = Node(
        name=node_name,
        enabled=body.enabled,
        protocol="wireguard",
        address=address,
        port=port_int,
        wg_private_key=dc.wg_private_key,
        wg_public_key=dc.wg_public_key,
        wg_preshared_key=dc.wg_preshared_key,
        wg_endpoint=dc.wg_endpoint,
        wg_mtu=dc.wg_mtu,
        wg_local_address=dc.wg_local_address,
        # Deployment-side context — copied so Node config_gen has
        # everything it needs (allowed_ips on the OUTBOUND side
        # for the xray-wireguard outbound).
        # (Note: `wg_reserved` left null; angristan-style WG doesn't
        #  use the Cloudflare-WARP-style reserved bytes. If we ever
        #  expose that knob, we'll surface it on DC too.)
        server_id=dep.server_id,
        from_deployment_client_id=dc.id,
        client_orphan=False,
    )
    session.add(node)
    await session.flush()
    new_node_id = node.id

    # Flip the DC status to 'exported' (idempotent — repeated exports
    # don't downgrade it). last_synced_at unchanged.
    if dc.status == "available":
        dc.status = "exported"
        session.add(dc)

    await session.commit()
    await session.refresh(node)
    return node


# ── Helpers (private) ────────────────────────────────────────────────────────


def _extract_uri_from_stdout(stdout: str) -> Optional[str]:
    """Pull the `URI=…` contract line from script stdout (last match
    wins, in case the script echoed it twice for diagnostics)."""
    matches = list(
        re.finditer(r"^URI=(\S+)\s*$", stdout, re.MULTILINE | re.IGNORECASE)
    )
    return matches[-1].group(1).strip() if matches else None


def _parse_ini_field(ini_text: Optional[str], key: str) -> Optional[str]:
    """Pull a value out of an INI block by its key (e.g. 'DNS').
    Returns None if not present or `ini_text` is None.
    """
    if not ini_text:
        return None
    for line in ini_text.splitlines():
        line = line.strip()
        if "=" in line and line.split("=", 1)[0].strip() == key:
            return line.split("=", 1)[1].strip()
    return None


def _server_port_from_dep(dep: ServerDeployment) -> int:
    """Pull the WG ListenPort from the deployment's config blob.
    Falls back to 51820 if not stored / parsable.
    """
    try:
        cfg = json.loads(dep.config_json or "{}")
    except (ValueError, TypeError):
        return 51820
    p = cfg.get("server_port")
    if isinstance(p, int):
        return p
    if isinstance(p, str) and p.isdigit():
        return int(p)
    return 51820
