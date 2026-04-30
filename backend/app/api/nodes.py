"""Node CRUD, URI import, health check, speed test endpoints."""
import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import BalancerGroup, Node, RoutingRule
from app.schemas import (
    HealthResult,
    NaiveSidecarLogs,
    NaiveSidecarStatus,
    NodeCreate,
    NodeImportRequest,
    NodeImportResponse,
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


@router.post("", response_model=NodeRead, status_code=201)
async def create_node(data: NodeCreate, session: AsyncSession = Depends(get_session)):
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


@router.get("/{node_id}", response_model=NodeRead)
async def get_node(node_id: int, session: AsyncSession = Depends(get_session)):
    node = await session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    return node


@router.patch("/{node_id}", response_model=NodeRead)
async def update_node(node_id: int, data: NodeUpdate, session: AsyncSession = Depends(get_session)):
    node = await session.get(Node, node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    patch = data.model_dump(exclude_unset=True)

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


@router.delete("/{node_id}", status_code=204)
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
            errors.append(f"{node_dict.get('name', '?')}: {exc}")

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
                errors.append(f"{n.name}: sidecar start failed: {exc}")

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


# ── NaiveProxy sidecar endpoints ──────────────────────────────────────────────

@router.get("/{node_id}/sidecar", response_model=NaiveSidecarStatus)
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


@router.post("/{node_id}/sidecar/restart", response_model=NaiveSidecarStatus)
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


@router.get("/{node_id}/sidecar/logs", response_model=NaiveSidecarLogs)
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

@router.post("/{node_id}/check", response_model=HealthResult)
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

@router.post("/{node_id}/speedtest", response_model=SpeedTestResult)
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
