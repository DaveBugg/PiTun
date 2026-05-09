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
from app.database import get_async_engine, get_session
from app.models import Node, Server, ServerDeployment
from app.schemas import (
    DeployJobAccepted,
    NodeRead,
    ServerCreate,
    ServerDeployRequest,
    ServerDeploymentRead,
    ServerDeploymentUpsert,
    ServerRead,
    ServerTestAllResult,
    ServerTestResult,
    ServerUpdate,
)

import logging

logger = logging.getLogger(__name__)

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


@router.post(
    "/{server_id:int}/deploy",
    response_model=DeployJobAccepted,
    status_code=202,
)
async def deploy_to_server(
    server_id: int,
    body: ServerDeployRequest,
    session: AsyncSession = Depends(get_session),
):
    """Auto-deploy a proxy install script over SSH (since v1.3.0).

    **Phase 2.2 (v1.3.0-beta.1)**: this endpoint now spawns a background
    `Job` via `core.jobs.JobManager.start_deploy` and returns 202 +
    `{job_id}` immediately. Synchronous Phase 1 (which blocked the
    HTTP request for ~5 min) starved a single uvicorn worker and made
    cancel impossible. Clients now follow progress via:
      * `GET    /api/server-tasks/{job_id}` — poll for status / result
      * `POST   /api/server-tasks/{job_id}/cancel` — stop a running deploy
      * `WS     /api/server-tasks/{job_id}/stream` — live stdout/stderr

    Pre-flight (synchronous, fails before spawning a job):
      1. Resolve server (404 if missing)
      2. Validate protocol against SUPPORTED_PROTOCOLS
      3. Build deploy plan (env + script content) via core.deploy —
         ValueError → 400 (e.g. missing domain/email for naive)

    Conflict semantics:
      * Same `(server_id, protocol)` already running → 409 Conflict
        (per-pair slot lock; different servers / different protocols
        on the same server may run in parallel).

    The job's runner (closure built below) wraps:
      * `core.ssh.exec_remote_script_streaming` — pumps each line
        into the JobManager buffer + WS subscribers
      * URI parse (`core.deploy.extract_uri` + `core.uri_parser.parse_uri`)
      * Node row creation
      * ServerDeployment upsert

    On runner exit, JobManager.persists final status:
      * `succeeded` — script exit 0, URI parsed, Node created
      * `succeeded` w/ `result.status="deployed_no_uri"` — script exit 0,
        no URI line in stdout (admin must add Node manually)
      * `failed` — non-zero exit / SSH error / timeout
      * `cancelled` — operator-issued cancel (remote script keeps
        running on VPS; we just stop pumping its output locally)
    """
    from app.core.deploy import build_plan, SUPPORTED_PROTOCOLS
    from app.core.jobs import job_manager, SlotBusy

    server = await _get_server_or_404(server_id, session)

    # Snapshot server fields BEFORE we hand off to the background runner.
    # The session passed via Depends(get_session) is request-scoped —
    # it'll be closed by FastAPI before the runner finishes. The runner
    # opens its OWN session via AsyncSession(get_async_engine()).
    # Snapshotting also avoids the post-commit MissingGreenlet trap (see
    # v1.2.7 self-heal fix).
    srv_id = server.id
    srv_name = server.name
    srv_host = server.host
    srv_port = server.port
    srv_user = server.user
    srv_auth_type = server.auth_type
    srv_password = server.password
    srv_private_key = server.private_key
    srv_passphrase = server.passphrase

    if body.protocol not in SUPPORTED_PROTOCOLS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported protocol {body.protocol!r}; "
                f"expected one of {SUPPORTED_PROTOCOLS}"
            ),
        )

    # Pre-flight: build the plan now so a malformed config (missing
    # domain/email, etc.) returns 400 synchronously instead of dying
    # inside an async job 200ms later.
    try:
        plan = build_plan(body.protocol, body.config)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        # The setup-*.sh script went missing from the install — server
        # bug, not user input.
        raise HTTPException(status_code=500, detail=f"Install script missing: {exc}")

    # Closures over plan + creds. The runner is the sole place that
    # touches SSH from now on; JobManager doesn't know about ssh.py.
    async def runner(job_id: str, on_line):
        from app.core.ssh import exec_remote_script_streaming
        from app.core.uri_parser import parse_uri
        from app.core.deploy import extract_uri, MULTI_CLIENT_PROTOCOLS

        result = await exec_remote_script_streaming(
            host=srv_host,
            port=srv_port,
            username=srv_user,
            password=srv_password if srv_auth_type == "password" else None,
            private_key=srv_private_key if srv_auth_type == "key" else None,
            passphrase=srv_passphrase if srv_auth_type == "key" else None,
            script_content=plan.script_content,
            env=plan.env,
            on_line=on_line,
        )

        parsed_uri: Optional[str] = None
        new_node_id: Optional[int] = None
        new_client_id: Optional[int] = None
        final_status = "failed"

        is_multi_client = body.protocol in MULTI_CLIENT_PROTOCOLS

        if result.ok:
            parsed_uri = extract_uri(result.stdout, body.protocol)
            if parsed_uri:
                node_dict = parse_uri(parsed_uri)
                if node_dict:
                    # ── Single-client path (naive, …) ───────────────────
                    # Result of deploy = one Node directly. Same flow
                    # as v1.3.0-beta.3 and earlier.
                    if not is_multi_client:
                        async with AsyncSession(get_async_engine()) as s:
                            node = Node(**{
                                k: v for k, v in node_dict.items() if hasattr(Node, k)
                            })
                            node.server_id = srv_id  # link to source Server
                            s.add(node)
                            await s.flush()
                            new_node_id = node.id
                            await s.commit()
                        final_status = "deployed"
                    else:
                        # ── Multi-client path (wireguard) ───────────────
                        # Result of deploy = one DeploymentClient row,
                        # NOT a Node. Admin clicks "Export to Node" later
                        # to actually route traffic through this peer.
                        # The rest of the WG client lifecycle (add more,
                        # remove, sync) goes through
                        # /servers/{id}/deployments/wireguard/clients
                        # endpoints (see api/server_clients.py).
                        from app.models import DeploymentClient
                        client_name = plan.env.get("CLIENT_NAME") or "client"
                        # Pull the inline INI conf from script stdout
                        # so future "Download conf" works without
                        # re-asking the server.
                        from app.api.server_clients import (
                            _extract_inline_conf, _parse_ini_field,
                        )
                        inline_conf = _extract_inline_conf(
                            result.stdout, client_name
                        )
                        async with AsyncSession(get_async_engine()) as s:
                            # Need the deployment row first — create it
                            # if missing, then attach the client.
                            existing_dep = (await s.exec(
                                select(ServerDeployment)
                                .where(ServerDeployment.server_id == srv_id)
                                .where(ServerDeployment.protocol == body.protocol)
                            )).first()
                            if not existing_dep:
                                # WG-specific config: server-level state
                                # (port, network, DNS) — peers go to
                                # DeploymentClient rows below.
                                wg_dep_config = {
                                    "server_port": int(plan.env.get("SERVER_PORT", "51820")),
                                    "wg_network_4": plan.env.get("WG_NETWORK_4", "10.66.66.0/24"),
                                    "wg_network_6": plan.env.get("WG_NETWORK_6", "fd42:42:42::/64"),
                                    "dns_1": plan.env.get("DNS_1", "1.1.1.1"),
                                    "dns_2": plan.env.get("DNS_2", "1.0.0.1"),
                                    "allowed_ips": plan.env.get("ALLOWED_IPS", "0.0.0.0/0,::/0"),
                                }
                                existing_dep = ServerDeployment(
                                    server_id=srv_id,
                                    protocol=body.protocol,
                                    config_json=json.dumps(wg_dep_config),
                                    status="deployed",
                                )
                                s.add(existing_dep)
                                await s.flush()
                            client = DeploymentClient(
                                deployment_id=existing_dep.id,
                                name=client_name,
                                wg_private_key=node_dict.get("wg_private_key"),
                                wg_public_key=node_dict.get("wg_public_key"),
                                wg_preshared_key=node_dict.get("wg_preshared_key"),
                                wg_endpoint=node_dict.get("wg_endpoint"),
                                wg_mtu=node_dict.get("wg_mtu", 1420),
                                wg_local_address=node_dict.get("wg_local_address"),
                                dns_servers=_parse_ini_field(inline_conf, "DNS"),
                                allowed_ips=_parse_ini_field(inline_conf, "AllowedIPs"),
                                config_json=(
                                    json.dumps({"client_conf_ini": inline_conf})
                                    if inline_conf else None
                                ),
                                status="available",
                                last_synced_at=datetime.now(timezone.utc),
                            )
                            s.add(client)
                            await s.flush()
                            new_client_id = client.id
                            await s.commit()
                        final_status = "deployed"  # client landed
                else:
                    final_status = "deployed_no_uri"
            else:
                final_status = "deployed_no_uri"

        # Upsert ServerDeployment in a fresh session — for naive only;
        # WG already created/looked-up the row above.
        now = datetime.now(timezone.utc)
        if not is_multi_client:
            deployment_config = {
                "domain": plan.env.get("DOMAIN"),
                "email": plan.env.get("EMAIL"),
                "naive_user": plan.env.get("NAIVE_USER"),
                # naive_pass intentionally omitted (CWE-312 — it's already
                # on the Node URI; no need to duplicate plain-text)
            }
            deployment_config_json = json.dumps(deployment_config)

            async with AsyncSession(get_async_engine()) as s:
                existing_dep = (await s.exec(
                    select(ServerDeployment)
                    .where(ServerDeployment.server_id == srv_id)
                    .where(ServerDeployment.protocol == body.protocol)
                )).first()

                if existing_dep:
                    existing_dep.config_json = deployment_config_json
                    existing_dep.status = final_status
                    existing_dep.updated_at = now
                    if new_node_id is not None:
                        existing_dep.last_node_id = new_node_id
                    s.add(existing_dep)
                    await s.flush()
                    deployment_id = existing_dep.id
                else:
                    new_dep = ServerDeployment(
                        server_id=srv_id,
                        protocol=body.protocol,
                        config_json=deployment_config_json,
                        status=final_status,
                        last_node_id=new_node_id,
                    )
                    s.add(new_dep)
                    await s.flush()
                    deployment_id = new_dep.id
                await s.commit()
        else:
            # WG: deployment row already created above. Re-fetch its id.
            async with AsyncSession(get_async_engine()) as s:
                existing_dep = (await s.exec(
                    select(ServerDeployment)
                    .where(ServerDeployment.server_id == srv_id)
                    .where(ServerDeployment.protocol == body.protocol)
                )).first()
                deployment_id = existing_dep.id if existing_dep else None
                # On failed runs we still want a deployment row to
                # surface the failure in the UI badge, even if no
                # client landed.
                if existing_dep is None:
                    new_dep = ServerDeployment(
                        server_id=srv_id,
                        protocol=body.protocol,
                        config_json="{}",
                        status=final_status,
                        last_node_id=None,
                    )
                    s.add(new_dep)
                    await s.flush()
                    deployment_id = new_dep.id
                    await s.commit()
                elif final_status != "deployed":
                    existing_dep.status = final_status
                    existing_dep.updated_at = now
                    s.add(existing_dep)
                    await s.commit()

        if not result.ok:
            # Surface the runner-level failure so the operator can grep
            # logs by server_id without parsing job rows. CWE-209/532
            # sanitised: stdout/stderr are already capped + the secret
            # never goes through error.
            logger.warning(
                "Auto-deploy failed: server_id=%d protocol=%s exit=%d error=%r",
                srv_id, body.protocol, result.exit_code, result.error,
            )
            # Returning a non-empty result dict (rather than raising)
            # keeps the Job in "succeeded" status with the failure
            # captured inside `result.status`. Choice: failed-deploy
            # is part of the deploy lifecycle, not a JobManager-level
            # crash. Frontend reads `result.status` to render the
            # actual outcome. SSH errors / runner crashes still
            # propagate as exceptions → JobManager flags `failed`.

        # Result returned to JobManager — written to Job.result_json
        # and surfaced via JobRead.result on the API. Frontend uses
        # `result.node_id` (single-client) or `result.client_id`
        # (multi-client) for the post-deploy affordance; `result.status`
        # distinguishes deployed vs deployed_no_uri vs failed.
        return {
            "deployment_id": deployment_id,
            "node_id": new_node_id,           # single-client only
            "client_id": new_client_id,       # multi-client (WG) only
            "status": final_status,
            "exit_code": result.exit_code,
            "duration_sec": result.duration_sec,
            "parsed_uri": parsed_uri,
            "error": result.error,
            "connect_latency_ms": result.connect_latency_ms,
        }

    try:
        job_id = await job_manager.start_deploy(
            server_id=srv_id,
            server_name=srv_name,
            protocol=body.protocol,
            config=body.config,
            runner=runner,
        )
    except SlotBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    return DeployJobAccepted(
        job_id=job_id,
        server_id=srv_id,
        protocol=body.protocol,
    )


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
)
async def create_node_from_deployment(
    server_id: int,
    protocol: str,
    force: bool = False,
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

    **Idempotent by default** — if `deployment.last_node_id` already
    points at a live Node row, that one is returned instead of
    creating a duplicate. Pass `?force=true` to bypass the check
    (intentional re-creation, e.g. after the user manually deleted
    the prior Node and wants the link rebuilt).
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

    # Idempotency — if we've already created a Node from this deployment
    # and that Node is still around, just return it. Avoids the previous
    # bug where double-clicking the "Create node" button silently
    # produced duplicate Naive sidecars + duplicate Node rows.
    if not force and deployment.last_node_id is not None:
        existing = await session.get(Node, deployment.last_node_id)
        if existing is not None:
            return existing

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
    """Bundle envelope `version: 2` (since v1.2.5) — includes nested
    `deployments` per server. v1 envelope (without deployments) is still
    accepted by the import endpoint for backward compatibility, so an
    older PiTun reading our export gracefully falls back to "servers
    only".

    Why deployments are nested under their server: a deployment row is
    keyed by `(server_id, protocol)`; the only stable identity across
    instances is the parent server, not the autoincrement id. Nesting
    avoids a separate id-mapping table on the import side.
    """
    from datetime import datetime as _dt, timezone as _tz
    from app.config import APP_VERSION as _APP_VERSION
    from fastapi.responses import JSONResponse as _JSON

    rows = (await session.exec(select(Server).order_by(Server.id))).all()

    # Pull all deployments in one query, then bucket by server_id —
    # avoids N+1 round-trips when the user has many servers.
    deployments = (await session.exec(
        select(ServerDeployment).order_by(ServerDeployment.id)
    )).all()
    by_server: dict[int, list[ServerDeployment]] = {}
    for d in deployments:
        by_server.setdefault(d.server_id, []).append(d)

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

        # Nested deployments. We DON'T export `last_node_id` — node ids
        # don't survive instance migration, and the linkage will be
        # re-established on the next "Create Node" click. We DO export
        # `status` so a "deployed" deployment stays "deployed" after
        # restore, otherwise the user would see the install script
        # button again on a server where they already ran the script.
        deps = by_server.get(s.id, [])
        item["deployments"] = [
            {
                "protocol": d.protocol,
                "config": json.loads(d.config_json) if d.config_json else {},
                "status": d.status,
                "created_at": d.created_at.isoformat() if d.created_at else None,
                "updated_at": d.updated_at.isoformat() if d.updated_at else None,
            }
            for d in deps
        ]
        servers_out.append(item)

    payload = {
        "kind": "pitun-servers-export",
        "version": 2,
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
    bundle_version = payload.get("version")
    # Accept v1 (servers only — pre-1.2.5) and v2 (servers + nested
    # deployments — since 1.2.5). v1 imports skip deployments restoration
    # entirely. Anything else is rejected so we don't silently lose data
    # if a future v3 introduces structural changes.
    if bundle_version not in (1, 2):
        raise HTTPException(400, f"Unsupported export version: {bundle_version}")
    servers_in = payload.get("servers")
    if not isinstance(servers_in, list):
        raise HTTPException(400, "Bundle missing 'servers' array")
    has_secrets = bool(payload.get("include_secrets", False))

    if replace:
        # Cascade: deleting Server rows takes their ServerDeployment rows
        # with them via FK. last_node_id on deployments is set to NULL
        # by the Node FK's ON DELETE SET NULL — but Node rows aren't
        # touched here, so node linkage of NEW deployments stays clean
        # if we re-create the same servers.
        existing = (await session.exec(select(Server))).all()
        for srv in existing:
            # Manually clear deployments first to avoid relying on cascade
            # at the SQLite level (we don't set up cascade in the schema
            # — the ServerDeployment FK is a plain FK without ON DELETE).
            existing_deps = (await session.exec(
                select(ServerDeployment).where(ServerDeployment.server_id == srv.id)
            )).all()
            for d in existing_deps:
                await session.delete(d)
            await session.delete(srv)
        await session.flush()

    imported = 0
    skipped = 0
    deployments_restored = 0
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
            # Need the autoincrement id before we can FK deployments
            # to it. flush() pushes the INSERT but stays inside the
            # outer transaction — the whole import is still one commit.
            await session.flush()
            await session.refresh(server)
            imported += 1

            # v2 envelope: nested deployments. Quietly tolerate v1
            # bundles that don't have the key or have it as None.
            deps_in = sd.get("deployments") or []
            if isinstance(deps_in, list):
                for dep in deps_in:
                    if not isinstance(dep, dict) or "protocol" not in dep:
                        continue
                    cfg = dep.get("config") or {}
                    if not isinstance(cfg, dict):
                        cfg = {}
                    new_dep = ServerDeployment(
                        server_id=server.id,
                        protocol=str(dep["protocol"]),
                        config_json=json.dumps(cfg),
                        status=str(dep.get("status") or "configured"),
                        # last_node_id intentionally not restored —
                        # Node ids don't survive instance migration.
                        # User clicks "Create Node" again to re-link.
                    )
                    session.add(new_dep)
                    deployments_restored += 1
        except Exception as exc:  # noqa: BLE001 — per-row error reporting
            # Three colliding concerns (CWE-209/532/117) — see
            # api/nodes.py for the full layered defence rationale.
            # Pre-extract `row_name` as a typed `str` so CodeQL's
            # taint analysis stops flagging dict.get() accesses as
            # tainted just because the parent dict has sensitive
            # sibling fields (password, private_key, passphrase).
            row_name = sd.get("name", "?")
            if not isinstance(row_name, str):
                row_name = "?"
            import logging
            logging.getLogger(__name__).warning(
                "Server import row failed: name=%r err_type=%s",
                row_name, type(exc).__name__,
            )
            errors.append(
                f"{row_name}: import failed ({type(exc).__name__})"
            )

    await session.commit()
    return {
        "imported": imported,
        "skipped": skipped,
        "deployments_restored": deployments_restored,
        "errors": errors,
        "has_secrets": has_secrets,
    }
