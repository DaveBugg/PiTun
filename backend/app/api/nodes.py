"""Node CRUD, URI import, health check, speed test endpoints."""
import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import APP_VERSION
from app.database import get_session
from app.models import BalancerGroup, Node, RoutingRule
from app.schemas import (
    HealthResult,
    NaiveSidecarLogs,
    NaiveSidecarStatus,
    NodeBase,
    NodeCreate,
    NodeImportRequest,
    NodeImportResponse,
    NodePage,
    NodeRead,
    NodeUpdate,
    SpeedTestResult,
)

router = APIRouter(prefix="/nodes", tags=["nodes"])


# ── NaiveProxy sidecar helpers ────────────────────────────────────────────────
#
# These are wrappers that fire-and-forget the docker lifecycle call and never
# break the main CRUD flow. If the docker daemon is unreachable the node is
# still created/updated in the DB — the sidecar will be reconciled next time
# naive_manager.sync_all() runs (on next backend startup or manual retry).

async def _ensure_naive_port(node: Node, session: AsyncSession) -> None:
    if node.protocol != "naive" or node.internal_port:
        return
    from app.core.naive_manager import naive_manager

    node.internal_port = await naive_manager.allocate_port(session, node.id)
    session.add(node)
    await session.commit()
    await session.refresh(node)


async def _sync_naive_sidecar(node: Node, *, enabled: bool) -> None:
    """Start or stop the sidecar container to match `enabled`. Non-fatal."""
    if node.protocol != "naive":
        return
    import logging
    log = logging.getLogger(__name__)
    from app.core.naive_manager import naive_manager

    try:
        if enabled:
            await naive_manager.start_node(node)
        else:
            await naive_manager.stop_node(node.id)
    except Exception as exc:
        log.warning(
            "Naive sidecar sync failed for node %d (%s): %s — "
            "DB state saved, sidecar will be reconciled later",
            node.id, node.name, exc,
        )


async def _refresh_naive_tproxy_bypass(session: AsyncSession) -> None:
    """Re-apply nftables if xray is running so the newly-added naive
    upstream IP lands in `bypass_dst4`.

    Without this, packets from the sidecar to its upstream get marked
    for tproxy and loop back through xray → the sidecar → forever.
    `_apply_nftables` auto-collects enabled naive upstream IPs (see
    `_collect_naive_bypass_dsts`), so all we have to do is trigger a
    re-apply on the same live xray. No xray restart needed.
    """
    try:
        from app.core.xray import xray_manager
        if not xray_manager.is_running:
            return
        from app.api.system import _apply_nftables, _load_settings_map
        settings_map = await _load_settings_map(session)
        await _apply_nftables(session, settings_map)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning(
            "Failed to refresh nftables naive bypass: %s", exc,
        )


# ── CRUD ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[NodeRead])
async def list_nodes(
    enabled: Optional[bool] = Query(None),
    group: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Node).order_by(Node.order, Node.id)
    if enabled is not None:
        stmt = stmt.where(Node.enabled == enabled)
    if group is not None:
        stmt = stmt.where(Node.group == group)
    return list((await session.exec(stmt)).all())


@router.get("/page", response_model=NodePage)
async def list_nodes_paginated(
    # Pagination. `limit=0` is a deliberate escape hatch for "give me
    # the whole filtered set" — used by bulk-export and tests. The UI
    # always sends a positive limit.
    limit: int = Query(50, ge=0, le=500),
    offset: int = Query(0, ge=0),
    # Filters
    subscription_id: Optional[int] = Query(
        None, description="Match nodes belonging to this subscription"
    ),
    local: Optional[bool] = Query(
        None,
        description=(
            "When True, match only nodes WITHOUT a subscription "
            "(`subscription_id IS NULL`). Mutually exclusive with "
            "`subscription_id` — if both are provided, `subscription_id` "
            "wins (more specific). Used by the UI to show 'local nodes "
            "only' separately from any registered subscription."
        ),
    ),
    protocol: Optional[str] = Query(
        None, description="Filter by protocol (vless/vmess/trojan/ss/wg/...)"
    ),
    enabled: Optional[bool] = Query(None),
    online: Optional[bool] = Query(
        None, description="Match `is_online` flag from last healthcheck"
    ),
    group: Optional[str] = Query(None),
    search: Optional[str] = Query(
        None, description="Substring match on node.name (case-insensitive)"
    ),
    direction: str = Query(
        "desc",
        pattern="^(asc|desc)$",
        description=(
            "Sort direction for the `id` tiebreaker. `desc` (default) "
            "shows newest-added nodes first — natural for subscription "
            "imports where the operator wants to see what just landed. "
            "`asc` reverses to oldest-first. `Node.order` stays the "
            "primary sort key either way so drag-to-reorder still wins."
        ),
    ),
    session: AsyncSession = Depends(get_session),
) -> NodePage:
    """Paginated + filtered Node listing (since v1.3.3).

    Needed because a single subscription can pull 1000+ nodes — the
    legacy unbounded endpoint left the UI rendering 1000 cards in one
    pass, which was unusable on a Raspberry Pi-served UI.

    Filters compose via AND. `total` is the count BEFORE pagination so
    the UI can render "Showing 51–100 of 1256". The stable sort order
    (`Node.order` then `Node.id`) is preserved so paging through a
    reorderable list works without jumping.
    """
    base_filters = []
    if subscription_id is not None:
        base_filters.append(Node.subscription_id == subscription_id)
    elif local is True:
        # Only applied when `subscription_id` wasn't passed — they
        # describe overlapping axes and the explicit subscription id
        # always wins.
        base_filters.append(Node.subscription_id.is_(None))  # type: ignore[union-attr]
    if protocol is not None:
        base_filters.append(Node.protocol == protocol)
    if enabled is not None:
        base_filters.append(Node.enabled == enabled)
    if online is not None:
        base_filters.append(Node.is_online == online)
    if group is not None:
        base_filters.append(Node.group == group)
    if search:
        # Case-insensitive substring match on `name`. SQLite's LIKE is
        # case-insensitive by default for ASCII; for the small chance
        # the operator named a node in Cyrillic we explicitly LOWER both
        # sides so the filter still matches in practice.
        pattern = f"%{search.lower()}%"
        base_filters.append(func.lower(Node.name).like(pattern))

    # Total count (unpaginated). Using a count(*) keyed on id avoids
    # pulling every Node row into memory just to size the result.
    count_stmt = select(func.count(Node.id))
    for f in base_filters:
        count_stmt = count_stmt.where(f)
    total = (await session.exec(count_stmt)).one() or 0

    # Paged result. Limit=0 = no LIMIT clause (escape hatch — see
    # docstring); otherwise the normal limit/offset combo. The `order`
    # column is always the primary sort axis so manual drag-to-reorder
    # placement wins; `id` is just the tiebreaker for rows with equal
    # `order` values (subscription imports leave that field at 0).
    id_axis = Node.id.desc() if direction == "desc" else Node.id.asc()  # type: ignore[union-attr]
    stmt = select(Node).order_by(Node.order, id_axis)
    for f in base_filters:
        stmt = stmt.where(f)
    if limit > 0:
        stmt = stmt.limit(limit).offset(offset)
    elif offset > 0:
        stmt = stmt.offset(offset)
    items = list((await session.exec(stmt)).all())

    return NodePage(items=items, total=int(total), limit=limit, offset=offset)


async def _validate_chain_target(session: AsyncSession, chain_node_id: Optional[int]) -> None:
    """Reject a chain that routes THROUGH a WireGuard node.

    xray's WireGuard outbound can't act as a dialer for another outbound:
    when something chains through it, the tunnel forwards 0 bytes (the
    config is accepted and xray starts, but traffic silently dies).
    WireGuard can only be the FINAL exit hop. So a node may chain THROUGH
    vless / trojan / vmess / ss / etc., but never through wireguard.
    (Verified live on a box: WG-over-VLESS works; *-over-WG = 0 B.)
    """
    if chain_node_id is None:
        return
    target = await session.get(Node, chain_node_id)
    if target is None:
        raise HTTPException(400, f"Chain target node {chain_node_id} not found")
    if target.protocol == "wireguard":
        raise HTTPException(
            400,
            f"Can't chain through {target.name!r}: xray can't tunnel traffic "
            "through a WireGuard node — WireGuard can only be the chain's final "
            "exit hop. Use a stream-protocol relay (VLESS / Trojan / VMess / SS), "
            "or make the WireGuard node the exit instead.",
        )


@router.post("", response_model=NodeRead, status_code=201)
async def create_node(data: NodeCreate, session: AsyncSession = Depends(get_session)):
    await _validate_chain_target(session, data.chain_node_id)
    node = Node(**data.model_dump())
    session.add(node)
    await session.commit()
    await session.refresh(node)

    # Naive nodes need a dedicated sidecar container + nftables bypass so
    # the sidecar's upstream connection isn't stolen by tproxy.
    if node.protocol == "naive":
        await _ensure_naive_port(node, session)
        await _sync_naive_sidecar(node, enabled=node.enabled)
        if node.enabled:
            await _refresh_naive_tproxy_bypass(session)

    return node


@router.get("/{node_id:int}", response_model=NodeRead)
async def get_node(node_id: int, session: AsyncSession = Depends(get_session)):
    node = await session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    return node


@router.patch("/{node_id:int}", response_model=NodeRead)
async def update_node(node_id: int, data: NodeUpdate, session: AsyncSession = Depends(get_session)):
    node = await session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    patch = data.model_dump(exclude_unset=True)
    if "chain_node_id" in patch:
        await _validate_chain_target(session, patch["chain_node_id"])

    # Track fields that change sidecar behaviour, so we know whether to restart.
    _naive_sensitive = {
        "address", "port", "uuid", "password", "naive_padding", "sni",
    }
    sidecar_needs_restart = (
        node.protocol == "naive"
        and any(k in patch for k in _naive_sensitive)
    )
    enabled_changed = "enabled" in patch and patch["enabled"] != node.enabled

    for k, v in patch.items():
        setattr(node, k, v)
    session.add(node)
    await session.commit()
    await session.refresh(node)

    if node.protocol == "naive":
        await _ensure_naive_port(node, session)
        if enabled_changed:
            await _sync_naive_sidecar(node, enabled=node.enabled)
        elif sidecar_needs_restart and node.enabled:
            await _sync_naive_sidecar(node, enabled=True)
        # Re-apply nftables if the address changed OR enabled flipped —
        # both affect whether the upstream IP is in the bypass set.
        if ("address" in patch) or enabled_changed:
            await _refresh_naive_tproxy_bypass(session)

    return node


@router.delete("/{node_id:int}", status_code=204)
async def delete_node(node_id: int, session: AsyncSession = Depends(get_session)):
    import json
    import logging

    node = await session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")

    was_naive = node.protocol == "naive"
    log = logging.getLogger(__name__)

    # Tear down sidecar BEFORE committing DB changes. If the DB commit later
    # fails, `naive_manager.sync_all()` on next boot will reconcile the
    # orphaned-config-without-container state (harmless). The reverse order
    # (commit first, stop after) leaves a running container with no DB
    # backing — much harder to clean up: it gets restarted by Docker policy
    # and nobody knows to kill it.
    if was_naive:
        from app.core.naive_manager import naive_manager
        try:
            await naive_manager.stop_node(node_id)
        except Exception as exc:
            log.warning(
                "Failed to stop naive sidecar for node %d during delete: %s — "
                "proceeding with DB removal anyway; next sync_all will reconcile",
                node_id, exc,
            )

    await session.delete(node)

    # Routing rules that directly reference this node by `node:<id>` action.
    orphan_rules = (await session.exec(
        select(RoutingRule).where(RoutingRule.action == f"node:{node_id}")
    )).all()
    for r in orphan_rules:
        await session.delete(r)

    # Balancer groups: drop this id from every node_ids JSON array.
    balancers = (await session.exec(select(BalancerGroup))).all()
    for bg in balancers:
        try:
            ids = json.loads(bg.node_ids) if isinstance(bg.node_ids, str) else bg.node_ids
        except (ValueError, TypeError):
            log.warning("BalancerGroup %d has malformed node_ids; skipping cleanup", bg.id)
            continue
        if not ids:
            continue
        if node_id in ids:
            ids.remove(node_id)
            bg.node_ids = json.dumps(ids)
            session.add(bg)

    # NodeCircle: drop this id from every node_ids JSON array, and reset
    # current_index if it points past the new (shorter) list.
    from app.models import NodeCircle
    circles = (await session.exec(select(NodeCircle))).all()
    for nc in circles:
        try:
            ids = json.loads(nc.node_ids) if isinstance(nc.node_ids, str) else nc.node_ids
        except (ValueError, TypeError):
            log.warning("NodeCircle %d has malformed node_ids; skipping cleanup", nc.id)
            continue
        if not ids:
            continue
        if node_id in ids:
            ids.remove(node_id)
            nc.node_ids = json.dumps(ids)
            if nc.current_index >= len(ids):
                nc.current_index = 0
            session.add(nc)

    # Chain references: any node whose chain_node_id points at this node.
    # `chain_node_id` has no FK constraint (SQLite self-FK is painful), so we
    # NULL the refs manually — otherwise chained probes break on next health
    # check as `_resolve_probe_target` follows a dangling pointer.
    chained = (await session.exec(
        select(Node).where(Node.chain_node_id == node_id)
    )).all()
    for ch in chained:
        ch.chain_node_id = None
        session.add(ch)
        log.info("Cleared chain_node_id on node %d (was pointing at deleted %d)", ch.id, node_id)

    await session.commit()

    # Refresh nftables so the deleted naive server's IP drops out of the
    # bypass set — otherwise we'd leave a stale exception.
    if was_naive:
        await _refresh_naive_tproxy_bypass(session)


# ── Reorder ───────────────────────────────────────────────────────────────────

@router.post("/reorder", status_code=204)
async def reorder_nodes(ids: List[int], session: AsyncSession = Depends(get_session)):
    """Update node order. ids[0] gets order=0, ids[1] order=10, etc."""
    for idx, node_id in enumerate(ids):
        node = await session.get(Node, node_id)
        if node:
            node.order = idx * 10
            session.add(node)
    await session.commit()


# ── Import ────────────────────────────────────────────────────────────────────

@router.post("/import", response_model=NodeImportResponse)
async def import_nodes(
    body: NodeImportRequest,
    subscription_id: Optional[int] = Query(None),
    session: AsyncSession = Depends(get_session),
):
    from app.core.uri_parser import parse_uri_list

    parsed = parse_uri_list(body.uris)
    imported = 0
    skipped = 0
    errors: List[str] = []
    nodes: List[Node] = []

    for node_dict in parsed:
        try:
            stmt = (
                select(Node)
                .where(Node.address == node_dict.get("address"))
                .where(Node.port == node_dict.get("port"))
                .where(Node.protocol == node_dict.get("protocol"))
                .where(Node.transport == node_dict.get("transport", "tcp"))
                .where(Node.uuid == node_dict.get("uuid"))
            )
            existing = (await session.exec(stmt)).first()
            if existing:
                skipped += 1
                continue

            if subscription_id:
                node_dict["subscription_id"] = subscription_id

            node = Node(**{k: v for k, v in node_dict.items() if hasattr(Node, k)})
            session.add(node)
            await session.flush()
            nodes.append(node)
            imported += 1
        except Exception as exc:
            # Three security concerns layered here:
            #   1. CWE-209 (info exposure via raw exception text in API
            #      response) — return only the class name, not str(exc).
            #   2. CWE-532 (clear-text logging of sensitive info) —
            #      raw `exc` can stringify to include the input that
            #      triggered it (IntegrityError echoes the offending
            #      column value; Pydantic ValidationError prints input
            #      dict). Log only the class name.
            #   3. CWE-117 (log injection via user-controlled `name`)
            #      — `_NoNewlineFilter` on root logger (v1.2.2) strips
            #      \n/\r; %r additionally satisfies CodeQL's sanitiser
            #      detection.
            #
            # The pre-extracted local `row_name` (a typed `str`) detaches
            # CodeQL's taint analysis from the parent `node_dict` —
            # without this, CodeQL flags `dict.get(...)` accesses
            # generically as "tainted" because the dict has sensitive
            # sibling fields (password, private_key, uuid). See
            # alert #21 (dismissed as FP, this closes it source-side).
            row_name = node_dict.get("name", "?")
            if not isinstance(row_name, str):
                row_name = "?"
            import logging
            logging.getLogger(__name__).warning(
                "Node import row failed: name=%r err_type=%s",
                row_name, type(exc).__name__,
            )
            errors.append(
                f"{row_name}: import failed ({type(exc).__name__})"
            )

    await session.commit()
    for n in nodes:
        await session.refresh(n)

    # Spin up sidecars for any freshly-imported naive nodes
    naive_imported = False
    for n in nodes:
        if n.protocol == "naive":
            try:
                await _ensure_naive_port(n, session)
                if n.enabled:
                    await _sync_naive_sidecar(n, enabled=True)
                    naive_imported = True
            except Exception as exc:
                # See main `except Exception` above. Pre-extract name
                # to a local str to detach CodeQL taint analysis from
                # the Node ORM object's sensitive attributes.
                row_name = n.name if isinstance(n.name, str) else "?"
                import logging
                logging.getLogger(__name__).warning(
                    "Naive sidecar start after import failed: name=%r err_type=%s",
                    row_name, type(exc).__name__,
                )
                errors.append(
                    f"{row_name}: sidecar start failed ({type(exc).__name__})"
                )

    # Single nftables refresh pass covering all naive nodes imported in
    # this batch (a bulk import may bring in several).
    if naive_imported:
        await _refresh_naive_tproxy_bypass(session)

    return NodeImportResponse(
        imported=imported,
        skipped=skipped,
        nodes=[NodeRead.model_validate(n) for n in nodes],
        errors=errors,
    )


# ── JSON export / import (full-fidelity backup) ──────────────────────────────
#
# Distinct from `/import` which parses VPN URIs. This pair handles the
# "back up everything to a file, restore later" use case — full Node
# row roundtrip including transport / TLS / WireGuard / Hysteria / Naive
# fields. Bundle envelope (`kind`, `version`, `exported_at`,
# `pitun_version`) lets future versions migrate or refuse incompatible
# imports. Same envelope shape is used by the Servers and (eventually)
# Routing exports — reuse the helper functions in `app/core/io_bundle.py`.

@router.get("/export-uris")
async def export_nodes_uris(session: AsyncSession = Depends(get_session)):
    """Plain-text export — one VPN URI per line.

    Counterpart to the existing `POST /api/nodes/import` (which takes
    a list of URIs). Useful for "give me my list of nodes to paste
    into another client" or for a quick offline backup that any vless
    / v2rayN-compatible reader can ingest.

    Nodes whose protocol doesn't have a URI form (currently
    WireGuard) are skipped silently; everything else round-trips
    through PiTun's own parser.
    """
    from app.core.uri_formatter import node_to_uri

    rows = (await session.exec(select(Node).order_by(Node.order, Node.id))).all()
    lines: List[str] = []
    for n in rows:
        uri = node_to_uri(n)
        if uri:
            lines.append(uri)
    body = "\n".join(lines) + ("\n" if lines else "")

    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(
        content=body,
        headers={
            "Content-Disposition": (
                f'attachment; filename="pitun-nodes-{datetime.now().strftime("%Y%m%d-%H%M%S")}.txt"'
            ),
        },
    )


@router.get("/export-json")
async def export_nodes_json(session: AsyncSession = Depends(get_session)):
    """Return all nodes as a downloadable JSON bundle."""
    rows = (await session.exec(select(Node).order_by(Node.order, Node.id))).all()
    payload = {
        "kind": "pitun-nodes-export",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pitun_version": APP_VERSION,
        "count": len(rows),
        "nodes": [NodeRead.model_validate(n).model_dump(mode="json") for n in rows],
    }
    # Plain JSON response with a Content-Disposition for browser download.
    from fastapi.responses import JSONResponse
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": (
                f'attachment; filename="pitun-nodes-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json"'
            ),
        },
    )


@router.post("/import-json", response_model=NodeImportResponse)
async def import_nodes_json(
    payload: Dict[str, Any],
    replace: bool = Query(False, description="If true, delete existing nodes before import"),
    session: AsyncSession = Depends(get_session),
):
    """Restore nodes from a previously-exported JSON bundle.

    `replace=true` wipes existing nodes first (full restore). Default
    `replace=false` is additive — duplicates by (protocol, address,
    port, uuid) are skipped, new entries are inserted.
    """
    if not isinstance(payload, dict):
        raise HTTPException(400, "Invalid bundle: expected a JSON object at the top level")
    if payload.get("kind") != "pitun-nodes-export":
        raise HTTPException(400, "Not a PiTun nodes export bundle (kind mismatch)")
    if payload.get("version") != 1:
        raise HTTPException(400, f"Unsupported export version: {payload.get('version')}")
    nodes_in = payload.get("nodes")
    if not isinstance(nodes_in, list):
        raise HTTPException(400, "Bundle missing 'nodes' array")

    imported = 0
    skipped = 0
    errors: List[str] = []
    created: List[Node] = []

    if replace:
        # Wipe existing nodes. RoutingRule entries that referenced
        # `node:<id>` will dangle, but the routing layer treats unknown
        # node ids as direct anyway (graceful degradation). User who
        # opts into `replace=true` accepts that consequence.
        existing = (await session.exec(select(Node))).all()
        for n in existing:
            await session.delete(n)
        await session.flush()

    for nd in nodes_in:
        try:
            # Filter to only known NodeBase fields — silently drops
            # unknown columns (forward-compat: a newer export with
            # extra fields imports cleanly into older PiTun).
            allowed = set(NodeBase.model_fields.keys())
            clean = {k: v for k, v in nd.items() if k in allowed}
            # Validate via NodeCreate (which extends NodeBase) — surfaces
            # invalid protocol/transport/tls values as 422-style errors
            # in our `errors` list rather than blowing up the whole import.
            validated = NodeCreate(**clean)

            # Dedup unless we're in replace mode (where we just wiped).
            if not replace:
                stmt = (
                    select(Node)
                    .where(Node.protocol == validated.protocol)
                    .where(Node.address == validated.address)
                    .where(Node.port == validated.port)
                )
                if validated.uuid:
                    stmt = stmt.where(Node.uuid == validated.uuid)
                if (await session.exec(stmt)).first():
                    skipped += 1
                    continue

            node = Node(**validated.model_dump())
            session.add(node)
            await session.flush()
            created.append(node)
            imported += 1
        except Exception as exc:  # noqa: BLE001 — surface per-row errors
            # Same security envelope as `/import` (CWE-209/532/117).
            # Pre-extract name to a local str — see CodeQL FP rationale
            # in the legacy import handler above.
            row_name = nd.get("name", "?")
            if not isinstance(row_name, str):
                row_name = "?"
            import logging
            logging.getLogger(__name__).warning(
                "Node import-json row failed: name=%r err_type=%s",
                row_name, type(exc).__name__,
            )
            errors.append(
                f"{row_name}: import failed ({type(exc).__name__})"
            )

    await session.commit()
    for n in created:
        await session.refresh(n)

    # Spin up sidecars for naive nodes, like /import does.
    naive_imported = False
    for n in created:
        if n.protocol == "naive":
            try:
                await _ensure_naive_port(n, session)
                if n.enabled:
                    await _sync_naive_sidecar(n, enabled=True)
                    naive_imported = True
            except Exception as exc:
                # Same security envelope as above. Pre-extract name to local str.
                row_name = n.name if isinstance(n.name, str) else "?"
                import logging
                logging.getLogger(__name__).warning(
                    "Naive sidecar start after import-json failed: name=%r err_type=%s",
                    row_name, type(exc).__name__,
                )
                errors.append(
                    f"{row_name}: sidecar start failed ({type(exc).__name__})"
                )
    if naive_imported:
        await _refresh_naive_tproxy_bypass(session)

    return NodeImportResponse(
        imported=imported,
        skipped=skipped,
        nodes=[NodeRead.model_validate(n) for n in created],
        errors=errors,
    )


# ── NaiveProxy sidecar endpoints ──────────────────────────────────────────────

@router.get("/{node_id:int}/sidecar", response_model=NaiveSidecarStatus)
async def naive_sidecar_status(node_id: int, session: AsyncSession = Depends(get_session)):
    node = await session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    if node.protocol != "naive":
        raise HTTPException(400, "Node is not of protocol 'naive'")
    from app.core.naive_manager import naive_manager

    st = await naive_manager.get_status(node_id)
    return NaiveSidecarStatus(
        exists=st["exists"],
        running=st["running"],
        status=st["status"],
        started_at=st.get("started_at"),
        restart_count=st.get("restart_count", 0),
        internal_port=node.internal_port,
    )


@router.post("/{node_id:int}/sidecar/restart", response_model=NaiveSidecarStatus)
async def naive_sidecar_restart(node_id: int, session: AsyncSession = Depends(get_session)):
    node = await session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    if node.protocol != "naive":
        raise HTTPException(400, "Node is not of protocol 'naive'")
    if not node.enabled:
        raise HTTPException(400, "Node is disabled — enable it first")
    from app.core.naive_manager import naive_manager, NaiveManagerError

    await _ensure_naive_port(node, session)
    try:
        await naive_manager.restart_node(node)
    except NaiveManagerError as exc:
        raise HTTPException(500, str(exc))

    st = await naive_manager.get_status(node_id)
    return NaiveSidecarStatus(
        exists=st["exists"],
        running=st["running"],
        status=st["status"],
        started_at=st.get("started_at"),
        restart_count=st.get("restart_count", 0),
        internal_port=node.internal_port,
    )


@router.get("/{node_id:int}/sidecar/logs", response_model=NaiveSidecarLogs)
async def naive_sidecar_logs(
    node_id: int,
    tail: int = Query(200, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
):
    node = await session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    if node.protocol != "naive":
        raise HTTPException(400, "Node is not of protocol 'naive'")
    from app.core.naive_manager import naive_manager

    logs = await naive_manager.get_logs(node_id, tail=tail)
    return NaiveSidecarLogs(node_id=node_id, logs=logs)


# ── Health checks ─────────────────────────────────────────────────────────────

@router.post("/{node_id:int}/check", response_model=HealthResult)
async def check_node_health(node_id: int, session: AsyncSession = Depends(get_session)):
    from app.core.healthcheck import health_checker

    node = await session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")

    result = await health_checker.check_node(node)

    node_id_val = node.id
    node_name_val = node.name

    node.is_online = result["is_online"]
    node.latency_ms = result["latency_ms"]
    from datetime import datetime, timezone
    node.last_check = datetime.now(tz=timezone.utc)
    session.add(node)
    await session.commit()

    return HealthResult(
        node_id=node_id_val,
        node_name=node_name_val,
        is_online=result["is_online"],
        latency_ms=result["latency_ms"],
        error=result.get("error"),
    )


@router.post("/check-all", response_model=List[HealthResult])
async def check_all_nodes():
    from app.core.healthcheck import health_checker

    results = await health_checker.check_all_nodes()
    return [HealthResult(**r) for r in results]


# ── Speed test ────────────────────────────────────────────────────────────────

@router.post("/{node_id:int}/speedtest", response_model=SpeedTestResult)
async def speedtest_node(node_id: int, session: AsyncSession = Depends(get_session)):
    from app.core.speedtest import speedtest_node as _speedtest

    node = await session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")

    result = await _speedtest(node)
    return SpeedTestResult(**result)


@router.post("/speedtest-all", response_model=List[SpeedTestResult])
async def speedtest_all_nodes(session: AsyncSession = Depends(get_session)):
    """Run speed test on all enabled nodes sequentially (each spawns its own xray)."""
    from app.core.speedtest import speedtest_node as _speedtest

    nodes = list((await session.exec(select(Node).where(Node.enabled == True))).all())
    results = []
    for node in nodes:
        result = await _speedtest(node)
        results.append(SpeedTestResult(**result))
    return results
