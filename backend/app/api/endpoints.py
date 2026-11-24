"""Extra SOCKS5 proxy endpoints (each its own xray process).

  GET    /endpoints            — list with runtime status
  POST   /endpoints            — create (validates limit/ports/assignment,
                                 starts the process when enabled)
  GET    /endpoints/{id}       — one
  PATCH  /endpoints/{id}       — update; enabled toggle starts/stops
  DELETE /endpoints/{id}       — stop, delete, drop config files
  POST   /endpoints/{id}/restart
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.endpoints import (
    MAX_PROXY_ENDPOINTS,
    EndpointValidationError,
    next_free_socks_port,
    validate_assignment,
    validate_socks_port,
)
from app.core.endpoint_manager import (
    build_endpoint_config,
    endpoint_config_path,
    endpoint_manager,
    write_endpoint_config,
)
from app.database import get_session
from app.models import BalancerGroup, Node, ProxyEndpoint, RoutingSet
from app.schemas import (
    ProxyEndpointCreate,
    ProxyEndpointRead,
    ProxyEndpointUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/endpoints", tags=["endpoints"])


def _to_read(ep: ProxyEndpoint, names: dict) -> ProxyEndpointRead:
    st = endpoint_manager.status(ep.id)
    return ProxyEndpointRead(
        id=ep.id,
        name=ep.name,
        enabled=ep.enabled,
        socks_port=ep.socks_port,
        mode=ep.mode,
        node_id=ep.node_id,
        balancer_id=ep.balancer_id,
        routing_set_id=ep.routing_set_id,
        order=ep.order,
        created_at=ep.created_at,
        running=st["running"],
        pid=st["pid"],
        uptime=st["uptime"],
        last_error=st["last_error"],
        node_name=names.get(("node", ep.node_id)),
        balancer_name=names.get(("balancer", ep.balancer_id)),
        routing_set_name=names.get(("set", ep.routing_set_id)),
    )


async def _names(session: AsyncSession, eps: List[ProxyEndpoint]) -> dict:
    out = {}
    for ep in eps:
        if ep.node_id is not None:
            n = await session.get(Node, ep.node_id)
            if n:
                out[("node", ep.node_id)] = n.name
        if ep.balancer_id is not None:
            b = await session.get(BalancerGroup, ep.balancer_id)
            if b:
                out[("balancer", ep.balancer_id)] = b.name
        if ep.routing_set_id is not None:
            s = await session.get(RoutingSet, ep.routing_set_id)
            if s:
                out[("set", ep.routing_set_id)] = s.name
    return out


async def _get_or_404(endpoint_id: int, session: AsyncSession) -> ProxyEndpoint:
    ep = await session.get(ProxyEndpoint, endpoint_id)
    if ep is None:
        raise HTTPException(status_code=404, detail="Endpoint not found")
    return ep


def _400(exc: EndpointValidationError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


async def _check_port_free(
    session: AsyncSession, port: int, excluding_id: Optional[int],
) -> None:
    from app.core.net import host_port_free

    clash = (await session.exec(
        select(ProxyEndpoint).where(ProxyEndpoint.socks_port == port)
    )).first()
    if clash is not None and clash.id != excluding_id:
        raise EndpointValidationError(
            f"port {port} is already used by endpoint {clash.name!r}"
        )
    current = (
        endpoint_manager.status(excluding_id)["running"]
        if excluding_id is not None else False
    )
    # The currently-running endpoint legitimately holds its own port.
    if current and clash is not None and clash.id == excluding_id:
        return
    if not host_port_free(port):
        raise EndpointValidationError(f"port {port} is already in use on the host")


async def _apply_or_500(session: AsyncSession, ep: ProxyEndpoint) -> None:
    try:
        await endpoint_manager.apply(session, ep)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500,
            detail=f"endpoint apply failed: {str(exc)[:300]}",
        ) from exc


@router.get("", response_model=List[ProxyEndpointRead])
async def list_endpoints(session: AsyncSession = Depends(get_session)):
    eps = list((await session.exec(
        select(ProxyEndpoint).order_by(ProxyEndpoint.order, ProxyEndpoint.id)
    )).all())
    names = await _names(session, eps)
    return [_to_read(ep, names) for ep in eps]


@router.post("", response_model=ProxyEndpointRead, status_code=201)
async def create_endpoint(
    body: ProxyEndpointCreate,
    session: AsyncSession = Depends(get_session),
):
    count = len(list((await session.exec(select(ProxyEndpoint))).all()))
    if count >= MAX_PROXY_ENDPOINTS:
        raise HTTPException(
            status_code=400,
            detail=f"endpoint limit reached ({MAX_PROXY_ENDPOINTS})",
        )
    dup = (await session.exec(
        select(ProxyEndpoint).where(ProxyEndpoint.name == body.name)
    )).first()
    if dup is not None:
        raise HTTPException(status_code=400, detail=f"name {body.name!r} is taken")

    try:
        port = body.socks_port or await next_free_socks_port(session)
        validate_socks_port(port)
        await _check_port_free(session, port, excluding_id=None)
        await validate_assignment(
            session,
            node_id=body.node_id,
            balancer_id=body.balancer_id,
            mode=body.mode,
            routing_set_id=body.routing_set_id,
        )
    except EndpointValidationError as exc:
        raise _400(exc) from exc

    now = datetime.now(timezone.utc)
    ep = ProxyEndpoint(
        name=body.name,
        enabled=body.enabled,
        socks_port=port,
        mode=body.mode,
        node_id=body.node_id,
        balancer_id=body.balancer_id,
        routing_set_id=body.routing_set_id,
        order=count + 1,
        created_at=now,
        updated_at=now,
    )
    session.add(ep)
    await session.commit()
    await session.refresh(ep)

    try:
        await endpoint_manager.apply(session, ep)
    except Exception as exc:  # noqa: BLE001
        # Create is all-or-nothing: a config that can't start leaves no
        # broken half-enabled row behind.
        await session.delete(ep)
        await session.commit()
        endpoint_config_path(ep.id).unlink(missing_ok=True)
        raise HTTPException(
            status_code=500,
            detail=f"endpoint apply failed: {str(exc)[:300]}",
        ) from exc
    names = await _names(session, [ep])
    return _to_read(ep, names)


@router.get("/{endpoint_id:int}", response_model=ProxyEndpointRead)
async def get_endpoint(endpoint_id: int, session: AsyncSession = Depends(get_session)):
    ep = await _get_or_404(endpoint_id, session)
    names = await _names(session, [ep])
    return _to_read(ep, names)


@router.patch("/{endpoint_id:int}", response_model=ProxyEndpointRead)
async def update_endpoint(
    endpoint_id: int,
    body: ProxyEndpointUpdate,
    session: AsyncSession = Depends(get_session),
):
    ep = await _get_or_404(endpoint_id, session)
    data = body.model_dump(exclude_unset=True)

    if "name" in data and data["name"] != ep.name:
        dup = (await session.exec(
            select(ProxyEndpoint)
            .where(ProxyEndpoint.name == data["name"])
            .where(ProxyEndpoint.id != ep.id)
        )).first()
        if dup is not None:
            raise HTTPException(status_code=400, detail=f"name {data['name']!r} is taken")

    new_port = data.get("socks_port") or ep.socks_port
    new_mode = data.get("mode", ep.mode)
    new_node = data.get("node_id", ep.node_id)
    new_balancer = data.get("balancer_id", ep.balancer_id)
    new_set = data.get("routing_set_id", ep.routing_set_id)
    # Switching to a node clears the balancer (and vice versa) — exactly
    # one egress kind per endpoint.
    if data.get("node_id") is not None:
        new_balancer = None
    if data.get("balancer_id") is not None:
        new_node = None

    try:
        if new_port != ep.socks_port:
            validate_socks_port(new_port)
            await _check_port_free(session, new_port, excluding_id=ep.id)
        await validate_assignment(
            session,
            node_id=new_node,
            balancer_id=new_balancer,
            mode=new_mode,
            routing_set_id=new_set,
            excluding_endpoint_id=ep.id,
        )
    except EndpointValidationError as exc:
        raise _400(exc) from exc

    for key, value in (
        ("name", data.get("name")),
        ("enabled", data.get("enabled")),
    ):
        if value is not None:
            setattr(ep, key, value)
    ep.socks_port = new_port
    ep.mode = new_mode
    ep.node_id = new_node
    ep.balancer_id = new_balancer
    ep.routing_set_id = new_set
    ep.updated_at = datetime.now(timezone.utc)
    session.add(ep)
    await session.commit()
    await session.refresh(ep)

    await _apply_or_500(session, ep)
    names = await _names(session, [ep])
    return _to_read(ep, names)


@router.delete("/{endpoint_id:int}", status_code=204)
async def delete_endpoint(endpoint_id: int, session: AsyncSession = Depends(get_session)):
    ep = await _get_or_404(endpoint_id, session)
    await endpoint_manager.stop(ep.id)
    for name in (
        f"{ep.id}.json", f"{ep.id}.json.prev", f"{ep.id}.tmp.json",
    ):
        (endpoint_config_path(ep.id).parent / name).unlink(missing_ok=True)
    await session.delete(ep)
    await session.commit()


@router.post("/{endpoint_id:int}/restart", response_model=ProxyEndpointRead)
async def restart_endpoint(endpoint_id: int, session: AsyncSession = Depends(get_session)):
    ep = await _get_or_404(endpoint_id, session)
    if not ep.enabled:
        raise HTTPException(status_code=400, detail="endpoint is disabled")
    try:
        config = await build_endpoint_config(ep, session)
        await write_endpoint_config(ep.id, config)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=500, detail=f"config rebuild failed: {str(exc)[:300]}",
        ) from exc
    proc = endpoint_manager._procs.get(ep.id)
    if proc is not None and proc.is_running:
        await proc.restart()
    else:
        ep_proc = endpoint_manager._get(ep)
        ep_proc.wanted = True
        await ep_proc.start()
    names = await _names(session, [ep])
    return _to_read(ep, names)
