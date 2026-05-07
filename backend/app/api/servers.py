"""Server CRUD + connection test + naive install-script generator.

Phase 1 endpoints — see SECURITY.md "Threat model" for the credential-
storage rationale (LAN-only deployment, plain-text in SQLite is fine).

Endpoints:
  GET    /servers                 — list (no secrets, has_* booleans only)
  POST   /servers                 — create
  GET    /servers/{id}            — read single (no secrets)
  PATCH  /servers/{id}            — partial update (empty string = leave
                                     existing for secret fields)
  DELETE /servers/{id}            — delete (cascades server_id=NULL on nodes)
  POST   /servers/{id}/test       — SSH connection probe, updates status
  POST   /servers/test-all        — run probe on every server, parallel
  GET    /servers/{id}/naive-install-script
                                  — return a self-contained bash one-liner
                                     that bootstraps NaiveProxy on the
                                     server. Pure text/x-shellscript download,
                                     no SSH involved (user runs it themselves).

Phase 2 (later) will add `POST /servers/{id}/run/{script_slug}` that streams
output via SSE/WebSocket.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import shlex
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.ssh import test_ssh_connection
from app.database import get_session
from app.models import Node, Server, ServerDeployment
from app.schemas import (
    NodeRead,
    ServerCreate,
    ServerDeploymentRead,
    ServerDeploymentUpsert,
    ServerRead,
    ServerTestAllResult,
    ServerTestResult,
    ServerUpdate,
)

router = APIRouter(prefix="/servers", tags=["servers"])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _to_read(server: Server) -> ServerRead:
    """Project a DB row to the API shape — strips secret fields, replaces
    them with `has_*` booleans so the UI can show "password set" vs
    "password not set" without ever shipping the value to the browser."""
    return ServerRead(
        id=server.id,
        name=server.name,
        description=server.description,
        host=server.host,
        port=server.port,
        user=server.user,
        auth_type=server.auth_type,
        has_password=bool(server.password),
        has_private_key=bool(server.private_key),
        has_passphrase=bool(server.passphrase),
        status=server.status,
        last_check=server.last_check,
        last_check_error=server.last_check_error,
        latency_ms=server.latency_ms,
        created_at=server.created_at,
        updated_at=server.updated_at,
    )


def _apply_secret_field(current: Optional[str], incoming) -> Optional[str]:
    """Three-state PATCH semantics for secret fields. See ServerUpdate
    docstring for the full rule table. `incoming` is the raw value from
    the Pydantic model; `_UNSET` sentinels aren't possible because Pydantic
    drops absent fields when `.model_dump(exclude_unset=True)` is used.

    Called only when the field WAS present in the request body."""
    if incoming is None:
        return None  # explicit null → clear
    if incoming == "":
        return current  # empty string → leave existing
    return incoming  # otherwise replace


# ── CRUD ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[ServerRead])
async def list_servers(session: AsyncSession = Depends(get_session)):
    rows = (await session.exec(select(Server).order_by(Server.id))).all()
    return [_to_read(s) for s in rows]


@router.post("", response_model=ServerRead, status_code=201)
async def create_server(
    data: ServerCreate, session: AsyncSession = Depends(get_session)
):
    server = Server(**data.model_dump())
    session.add(server)
    await session.commit()
    await session.refresh(server)
    return _to_read(server)


@router.get("/{server_id:int}", response_model=ServerRead)
async def get_server(server_id: int, session: AsyncSession = Depends(get_session)):
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return _to_read(server)


@router.patch("/{server_id:int}", response_model=ServerRead)
async def update_server(
    server_id: int,
    data: ServerUpdate,
    session: AsyncSession = Depends(get_session),
):
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    # `exclude_unset=True` so missing fields are NOT overwritten. Then we
    # apply three-state rules to the secret fields explicitly.
    payload = data.model_dump(exclude_unset=True)

    # Plain fields — direct overwrite if present in payload.
    for field in ("name", "description", "host", "port", "user", "auth_type"):
        if field in payload:
            setattr(server, field, payload[field])

    # Secret fields — three-state.
    for field in ("password", "private_key", "passphrase"):
        if field in payload:
            setattr(server, field, _apply_secret_field(getattr(server, field), payload[field]))

    server.updated_at = datetime.now(timezone.utc)
    session.add(server)
    await session.commit()
    await session.refresh(server)
    return _to_read(server)


@router.delete("/{server_id:int}", status_code=204)
async def delete_server(
    server_id: int, session: AsyncSession = Depends(get_session)
):
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    await session.delete(server)
    await session.commit()
    # Linked nodes get server_id=NULL via the FK ON DELETE SET NULL constraint.


# ── Connection test ──────────────────────────────────────────────────────────

async def _probe_one(server: Server, session: AsyncSession) -> ServerTestResult:
    """Run the SSH probe and persist the outcome on the row.

    Note: snapshot `server.id` BEFORE `await session.commit()`. The async
    session expires all attributes on commit by default, and accessing
    them afterwards inside the same coroutine path tries to re-fetch
    via blocking IO → `MissingGreenlet`. Capturing into a local before
    commit is the standard workaround.
    """
    result = await test_ssh_connection(
        host=server.host,
        port=server.port,
        username=server.user,
        password=server.password if server.auth_type == "password" else None,
        private_key=server.private_key if server.auth_type == "key" else None,
        passphrase=server.passphrase if server.auth_type == "key" else None,
    )

    server_id = server.id  # snapshot before commit (see docstring)

    server.status = "online" if result.ok else "offline"
    server.last_check = datetime.now(timezone.utc)
    server.last_check_error = None if result.ok else (result.error or "unknown error")
    server.latency_ms = result.latency_ms if result.ok else None
    session.add(server)
    await session.commit()

    return ServerTestResult(
        server_id=server_id,
        ok=result.ok,
        latency_ms=result.latency_ms,
        error=result.error,
        remote_info=result.remote_info,
    )


@router.post("/{server_id:int}/test", response_model=ServerTestResult)
async def test_server(
    server_id: int, session: AsyncSession = Depends(get_session)
):
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return await _probe_one(server, session)


@router.post("/test-all", response_model=ServerTestAllResult)
async def test_all_servers(session: AsyncSession = Depends(get_session)):
    """Run the SSH probe against every server in parallel.

    Each probe gets its own short connect timeout (~8s) so a dead server
    doesn't drag the request past ~10s total. Probes are awaited via
    gather; we re-use the same session sequentially when persisting
    results to keep things simple (SQLite write-serialised anyway).
    """
    rows = (await session.exec(select(Server).order_by(Server.id))).all()

    async def _run(s: Server):
        # Open a fresh probe — but persist via the outer session in order,
        # below, to avoid concurrent SQLite writes.
        return s, await test_ssh_connection(
            host=s.host,
            port=s.port,
            username=s.user,
            password=s.password if s.auth_type == "password" else None,
            private_key=s.private_key if s.auth_type == "key" else None,
            passphrase=s.passphrase if s.auth_type == "key" else None,
        )

    probed = await asyncio.gather(*[_run(s) for s in rows], return_exceptions=False)

    # Build result objects up front (snapshot ids before commit expires
    # attributes — same reason as _probe_one).
    results: List[ServerTestResult] = []
    now = datetime.now(timezone.utc)
    for server, ssh in probed:
        results.append(ServerTestResult(
            server_id=server.id,
            ok=ssh.ok,
            latency_ms=ssh.latency_ms,
            error=ssh.error,
            remote_info=ssh.remote_info,
        ))
        server.status = "online" if ssh.ok else "offline"
        server.last_check = now
        server.last_check_error = None if ssh.ok else (ssh.error or "unknown error")
        server.latency_ms = ssh.latency_ms if ssh.ok else None
        session.add(server)
    await session.commit()
    return ServerTestAllResult(results=results)


# ── Naive install-script generator ───────────────────────────────────────────
#
# The user pastes their domain + email and we emit a self-contained bash
# one-liner that fetches `scripts/setup-naive-server.sh` from the public
# repo and runs it with the right env vars pre-filled. Nothing is sent
# over SSH — the user copies the script and runs it on their VPS
# themselves. This is the "I don't want to give PiTun my SSH key" path.
#
# Two endpoints share the underlying generator:
#   - `/servers/{id}/naive-install-script` — script tagged with the
#     specific server's name/id (more useful when the user has registered
#     the VPS in PiTun ahead of time).
#   - `/scripts/naive-install` (in `app/api/scripts.py`) — server-agnostic
#     version available even when no servers exist yet. Same body shape,
#     just a generic header.
#
# `build_naive_install_script` is the only place that knows the script's
# layout, so the two endpoints stay in lock-step automatically.


def build_naive_install_script(
    *,
    domain: str,
    email: str,
    naive_user: Optional[str] = None,
    naive_pass: Optional[str] = None,
    server_label: Optional[str] = None,
    suggested_filename: str = "naive-install.sh",
) -> str:
    """Render the bash bootstrap that fetches setup-naive-server.sh from
    the PiTun repo and runs it with credentials pre-filled.

    `server_label` is woven into the header comment when given (typically
    `"<server.name> (id=<id>)"`). Skipped for the manual endpoint where
    the user hasn't registered a server yet.
    """
    user = naive_user or "pitun"
    pwd = naive_pass or secrets.token_urlsafe(24)
    label_line = (
        f"# PiTun — NaiveProxy install bootstrap for server '{server_label}'\n"
        if server_label
        else "# PiTun — NaiveProxy install bootstrap (manual / unregistered server)\n"
    )

    # shlex.quote everything user-controlled so a malicious or just messy
    # value can't break out of the env-var assignment. This is defence in
    # depth — admin trust boundary already gates this endpoint, but it
    # keeps the generated script well-formed even if someone pastes weird
    # characters into the form.
    return f"""#!/usr/bin/env bash
{label_line}# Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}.
#
# Run on the target VPS as root:
#
#   sudo bash {suggested_filename}
#
# What it does:
#   1. Fetches scripts/setup-naive-server.sh from the PiTun repo
#   2. Runs it with the credentials pre-filled
#   3. Prints the naive+https:// URI to import into PiTun -> Nodes
#
# Re-run safe: setup-naive-server.sh is idempotent for the package install
# step; on a domain mismatch or port-in-use it will abort instead of
# corrupting state.

set -euo pipefail

export DOMAIN={shlex.quote(domain)}
export EMAIL={shlex.quote(email)}
export NAIVE_USER={shlex.quote(user)}
export NAIVE_PASS={shlex.quote(pwd)}

curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/scripts/setup-naive-server.sh \\
  | sudo -E bash
"""


@router.get("/{server_id:int}/naive-install-script", response_class=PlainTextResponse)
async def naive_install_script(
    server_id: int,
    domain: str = Query(..., description="DNS A-record pointing at the VPS"),
    email: str = Query(..., description="Let's Encrypt registration email"),
    naive_user: Optional[str] = Query(None, description="Defaults to 'pitun'"),
    naive_pass: Optional[str] = Query(None, description="Auto-generated if absent"),
    session: AsyncSession = Depends(get_session),
):
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    filename = f"naive-install-{server.id}.sh"
    script = build_naive_install_script(
        domain=domain,
        email=email,
        naive_user=naive_user,
        naive_pass=naive_pass,
        server_label=f"{server.name} (id={server.id})",
        suggested_filename=filename,
    )
    return PlainTextResponse(
        content=script,
        media_type="text/x-shellscript",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ── Deployments ──────────────────────────────────────────────────────────────
#
# A "deployment" persists the credentials the user picked when generating
# a per-protocol install script for a Server. Frontend saves before
# downloading the script so:
#   1. Re-opening the modal pre-fills with last values
#   2. Auto-generated naive password isn't lost when the modal closes
#   3. Click "Create Node" instantiates a Node row from the saved values
#
# Keyed by (server_id, protocol) — one plan per protocol per server.
# Re-saving with the same protocol updates the existing row.

def _deployment_to_read(d: ServerDeployment) -> ServerDeploymentRead:
    return ServerDeploymentRead(
        id=d.id,
        server_id=d.server_id,
        protocol=d.protocol,
        config=json.loads(d.config_json) if d.config_json else {},
        status=d.status,
        last_node_id=d.last_node_id,
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


async def _get_server_or_404(server_id: int, session: AsyncSession) -> Server:
    server = await session.get(Server, server_id)
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@router.get("/{server_id:int}/deployments", response_model=List[ServerDeploymentRead])
async def list_deployments(
    server_id: int, session: AsyncSession = Depends(get_session)
):
    await _get_server_or_404(server_id, session)
    rows = (
        await session.exec(
            select(ServerDeployment)
            .where(ServerDeployment.server_id == server_id)
            .order_by(ServerDeployment.id)
        )
    ).all()
    return [_deployment_to_read(d) for d in rows]


@router.put(
    "/{server_id:int}/deployments/{protocol}",
    response_model=ServerDeploymentRead,
)
async def upsert_deployment(
    server_id: int,
    protocol: str,
    data: ServerDeploymentUpsert,
    session: AsyncSession = Depends(get_session),
):
    """Create-or-update a deployment plan keyed by (server_id, protocol).

    The protocol in the URL path is the canonical key; the body's
    `protocol` field must match (defence in depth — keeps the URL the
    primary identifier).
    """
    if data.protocol != protocol:
        raise HTTPException(
            status_code=400,
            detail=f"path protocol={protocol!r} does not match body protocol={data.protocol!r}",
        )
    await _get_server_or_404(server_id, session)

    existing = (
        await session.exec(
            select(ServerDeployment)
            .where(ServerDeployment.server_id == server_id)
            .where(ServerDeployment.protocol == protocol)
        )
    ).first()

    config_json = json.dumps(data.config)
    now = datetime.now(timezone.utc)

    if existing:
        existing.config_json = config_json
        existing.updated_at = now
        # Re-saving while we previously created a node → mark as
        # 'configured' again. User likely re-running with new params.
        if existing.status == "deployed" and existing.last_node_id is None:
            existing.status = "configured"
        session.add(existing)
        target = existing
    else:
        target = ServerDeployment(
            server_id=server_id,
            protocol=protocol,
            config_json=config_json,
            status="configured",
            created_at=now,
            updated_at=now,
        )
        session.add(target)

    await session.commit()
    await session.refresh(target)
    return _deployment_to_read(target)


@router.delete("/{server_id:int}/deployments/{protocol}", status_code=204)
async def delete_deployment(
    server_id: int,
    protocol: str,
    session: AsyncSession = Depends(get_session),
):
    await _get_server_or_404(server_id, session)
    existing = (
        await session.exec(
            select(ServerDeployment)
            .where(ServerDeployment.server_id == server_id)
            .where(ServerDeployment.protocol == protocol)
        )
    ).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Deployment not found")
    await session.delete(existing)
    await session.commit()


@router.post(
    "/{server_id:int}/deployments/{protocol}/create-node",
    response_model=NodeRead,
    status_code=201,
)
async def create_node_from_deployment(
    server_id: int,
    protocol: str,
    session: AsyncSession = Depends(get_session),
):
    """Instantiate a Node entry from a saved deployment plan.

    Reads the config that was persisted when the user last generated the
    install script, and creates a Node pre-filled with the right
    credentials and the server's host/port. The new node is linked back
    to the Server (via `node.server_id`) and to the Deployment (via
    `deployment.last_node_id`).

    This is the "I ran the script on the VPS, now turn this into a real
    node" button. Doesn't talk to the VPS at all — purely a database
    convenience built around values the user already typed.
    """
    server = await _get_server_or_404(server_id, session)
    deployment = (
        await session.exec(
            select(ServerDeployment)
            .where(ServerDeployment.server_id == server_id)
            .where(ServerDeployment.protocol == protocol)
        )
    ).first()
    if not deployment:
        raise HTTPException(status_code=404, detail="Deployment not found")

    config = json.loads(deployment.config_json) if deployment.config_json else {}

    if protocol == "naive":
        domain = config.get("domain") or server.host
        node = Node(
            name=f"{server.name} (naive)",
            enabled=True,
            protocol="naive",
            address=domain,
            port=443,
            uuid=config.get("naive_user") or "pitun",
            password=config.get("naive_pass") or "",
            transport="tcp",
            tls="tls",
            sni=domain,
            allow_insecure=False,
            naive_padding=True,
            server_id=server.id,
            order=0,
        )
    else:
        # Defensive — schema validator should have caught this earlier.
        raise HTTPException(
            status_code=400,
            detail=f"create-node not implemented for protocol={protocol!r}",
        )

    session.add(node)
    await session.commit()
    await session.refresh(node)

    deployment.last_node_id = node.id
    deployment.status = "deployed"
    deployment.updated_at = datetime.now(timezone.utc)
    session.add(deployment)
    await session.commit()

    # NaiveProxy nodes need a sidecar container + nftables bypass to work.
    # Mirror what /api/nodes POST does — see app/api/nodes.py:create_node.
    # Failures here shouldn't roll back the node creation (DB row is the
    # source of truth; sidecar will be reconciled on next backend boot
    # via naive_manager.sync_all).
    try:
        from app.api.nodes import (
            _ensure_naive_port,
            _refresh_naive_tproxy_bypass,
            _sync_naive_sidecar,
        )

        await _ensure_naive_port(node, session)
        await _sync_naive_sidecar(node, enabled=node.enabled)
        if node.enabled:
            await _refresh_naive_tproxy_bypass(session)
    except Exception:
        import logging
        logging.getLogger(__name__).warning(
            "Naive sidecar sync after create-node failed (non-fatal); "
            "will reconcile on next boot",
            exc_info=True,
        )

    # Final refresh so FastAPI's response serialization can access all
    # node attributes without triggering a lazy DB load (which would
    # raise MissingGreenlet — async session expires attrs on every
    # commit, and we did several commits above).
    await session.refresh(node)
    return node


# ── JSON export / import (full-fidelity backup) ──────────────────────────────
#
# Same envelope shape as /api/nodes/export-json so the UI can use one
# generic file-picker. Secrets handling differs from the Nodes flow
# because servers carry SSH credentials, which are sensitive enough to
# warrant explicit opt-in: by default the export STRIPS password,
# private_key, passphrase. Setting `include_secrets=true` writes them
# in plain — the user accepts responsibility for the resulting file.
# Either way, secrets remain plain at rest in SQLite (per LAN-only
# threat model in SECURITY.md), so the export doesn't change the
# security posture, it just gives the user a portable copy.

@router.get("/export-json")
async def export_servers_json(
    include_secrets: bool = Query(False, description="Include password/private_key/passphrase"),
    session: AsyncSession = Depends(get_session),
):
    from datetime import datetime as _dt, timezone as _tz
    from app.config import APP_VERSION as _APP_VERSION
    from fastapi.responses import JSONResponse as _JSON

    rows = (await session.exec(select(Server).order_by(Server.id))).all()

    servers_out: List[dict] = []
    for s in rows:
        item = {
            "name": s.name,
            "description": s.description,
            "host": s.host,
            "port": s.port,
            "user": s.user,
            "auth_type": s.auth_type,
        }
        if include_secrets:
            item["password"] = s.password
            item["private_key"] = s.private_key
            item["passphrase"] = s.passphrase
        servers_out.append(item)

    payload = {
        "kind": "pitun-servers-export",
        "version": 1,
        "exported_at": _dt.now(_tz.utc).isoformat(timespec="seconds"),
        "pitun_version": _APP_VERSION,
        "include_secrets": include_secrets,
        "count": len(servers_out),
        "servers": servers_out,
    }
    suffix = "with-secrets" if include_secrets else "no-secrets"
    return _JSON(
        content=payload,
        headers={
            "Content-Disposition": (
                f'attachment; filename="pitun-servers-{_dt.now().strftime("%Y%m%d-%H%M%S")}-{suffix}.json"'
            ),
        },
    )


@router.post("/import-json")
async def import_servers_json(
    payload: dict,
    replace: bool = Query(False, description="If true, delete existing servers before import"),
    session: AsyncSession = Depends(get_session),
):
    """Restore servers from a previously-exported JSON bundle.

    Returns `{imported, skipped, errors, has_secrets}`. `has_secrets`
    reflects whether the bundle's `include_secrets` flag was set —
    helpful for the UI to remind the user "you'll need to re-enter
    passwords for these N servers" if the bundle was secret-stripped.
    """
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid bundle: expected a JSON object at the top level")
    if payload.get("kind") != "pitun-servers-export":
        raise HTTPException(400, "Not a PiTun servers export bundle (kind mismatch)")
    if payload.get("version") != 1:
        raise HTTPException(400, f"Unsupported export version: {payload.get('version')}")
    servers_in = payload.get("servers")
    if not isinstance(servers_in, list):
        raise HTTPException(400, "Bundle missing 'servers' array")
    has_secrets = bool(payload.get("include_secrets", False))

    if replace:
        existing = (await session.exec(select(Server))).all()
        for srv in existing:
            await session.delete(srv)
        await session.flush()

    imported = 0
    skipped = 0
    errors: List[str] = []
    for sd in servers_in:
        try:
            allowed = {"name", "description", "host", "port", "user", "auth_type",
                       "password", "private_key", "passphrase"}
            clean = {k: v for k, v in sd.items() if k in allowed}
            # Validate via the Pydantic ServerCreate schema to enforce
            # auth_type / port ranges.
            validated = ServerCreate(**clean)

            if not replace:
                # Dedup by (name, host, port). Two servers at the same
                # endpoint with the same display name collapse — a
                # "name collision but different host" still inserts.
                stmt = (
                    select(Server)
                    .where(Server.name == validated.name)
                    .where(Server.host == validated.host)
                    .where(Server.port == validated.port)
                )
                if (await session.exec(stmt)).first():
                    skipped += 1
                    continue

            server = Server(**validated.model_dump())
            session.add(server)
            imported += 1
        except Exception as exc:  # noqa: BLE001 — per-row error reporting
            # Don't leak the raw exception text into the API response —
            # could surface internal paths, SQL fragments, library
            # versions, or attribute names to API clients (CWE-209,
            # CodeQL "Information exposure through an exception"). Log
            # the full detail server-side so admins can debug from the
            # backend logs, return only the exception class name to the
            # client.
            import logging
            logging.getLogger(__name__).warning(
                "Server import row failed: name=%s err=%s",
                sd.get("name", "?"), exc,
            )
            errors.append(
                f"{sd.get('name', '?')}: import failed ({type(exc).__name__})"
            )

    await session.commit()
    return {
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
        "has_secrets": has_secrets,
    }
