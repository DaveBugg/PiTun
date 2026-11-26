"""Extra SOCKS5 proxy endpoints — validation and port allocation.

Each endpoint is its own xray process (see `core/endpoint_manager.py`)
with one SOCKS5 inbound. Hard cap `MAX_PROXY_ENDPOINTS`: every endpoint
costs a process, so a LAN box stays the main flow plus at most 9 extras.

Uniqueness rule: a node can be assigned to ONE flow — the main flow
(active node) or a single endpoint. Balancers are exempt by design.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import BalancerGroup, Node, ProxyEndpoint, RoutingSet, Settings

MAX_PROXY_ENDPOINTS = 9

# Default allocation range for endpoint SOCKS5 ports: right after the
# main flow's 1080, far from DNS (53/5353), TPROXY (7893/7894) and the
# routing-set TPROXY range (65500+).
ENDPOINT_PORT_FIRST = 1081
ENDPOINT_PORT_LAST = 1999

# Ports that must never be handed to an endpoint even if free.
RESERVED_PORTS = frozenset({53, 1080, 5353, 7893, 7894, 8080, 10085})


class EndpointValidationError(ValueError):
    """Raised for operator-fixable endpoint config errors (→ HTTP 400)."""


def validate_socks_port(port: int) -> None:
    if not 1 <= port <= 65535:
        raise EndpointValidationError(f"socks_port must be 1..65535 (got: {port})")
    if port in RESERVED_PORTS:
        raise EndpointValidationError(f"port {port} is reserved for the main flow")
    if 65500 <= port <= 65535:
        raise EndpointValidationError(f"port {port} is in the routing-set TPROXY range")


async def next_free_socks_port(session: AsyncSession) -> int:
    """Lowest free port in the endpoint range: not reserved, not taken
    by another endpoint, and bind-probe-clean on the host."""
    from app.core.net import host_port_free

    taken = set((
        await session.exec(select(ProxyEndpoint.socks_port))
    ).all())
    for port in range(ENDPOINT_PORT_FIRST, ENDPOINT_PORT_LAST + 1):
        if port in RESERVED_PORTS or port in taken:
            continue
        if host_port_free(port):
            return port
    raise EndpointValidationError("no free port left in the endpoint range")


def endpoint_assigned_node_ids_subquery():
    """Subquery returning every ``Node.id`` currently assigned to a
    ``ProxyEndpoint``. Use as ``Node.id.notin_(...)`` to exclude nodes
    that would violate the one-node-per-flow invariant."""
    return select(ProxyEndpoint.node_id).where(ProxyEndpoint.node_id.isnot(None))  # type: ignore[union-attr]


async def endpoint_assigned_node_ids(session: AsyncSession) -> set[int]:
    """Set of node IDs currently assigned to any ``ProxyEndpoint``."""
    rows = (await session.exec(endpoint_assigned_node_ids_subquery())).all()
    return {row for row in rows if row is not None}


async def validate_assignment(
    session: AsyncSession,
    *,
    node_id: Optional[int],
    balancer_id: Optional[int],
    mode: str,
    routing_set_id: Optional[int],
    excluding_endpoint_id: Optional[int] = None,
) -> None:
    """Cross-field + flow-uniqueness validation for create/update."""
    if (node_id is None) == (balancer_id is None):
        raise EndpointValidationError(
            "exactly one of node_id / balancer_id is required"
        )
    if mode not in ("global", "routing_set"):
        raise EndpointValidationError(f"unknown mode: {mode!r}")
    if mode == "routing_set":
        if routing_set_id is None:
            raise EndpointValidationError("routing_set mode requires routing_set_id")
        if await session.get(RoutingSet, routing_set_id) is None:
            raise EndpointValidationError(f"routing set {routing_set_id} not found")
    elif routing_set_id is not None:
        raise EndpointValidationError("routing_set_id needs mode=routing_set")

    if balancer_id is not None:
        if await session.get(BalancerGroup, balancer_id) is None:
            raise EndpointValidationError(f"balancer group {balancer_id} not found")
        return  # balancers are exempt from flow uniqueness

    node = await session.get(Node, node_id)
    if node is None:
        raise EndpointValidationError(f"node {node_id} not found")

    # Main flow: the active node is taken.
    row = (await session.exec(
        select(Settings).where(Settings.key == "active_node_id")
    )).first()
    if row and row.value.isdigit() and int(row.value) == node_id:
        raise EndpointValidationError(
            f"node {node.name!r} is the active node of the main flow"
        )

    q = select(ProxyEndpoint).where(ProxyEndpoint.node_id == node_id)
    if excluding_endpoint_id is not None:
        q = q.where(ProxyEndpoint.id != excluding_endpoint_id)
    other = (await session.exec(q)).first()
    if other is not None:
        raise EndpointValidationError(
            f"node {node.name!r} is already assigned to endpoint {other.name!r}"
        )


async def flow_for_node(session: AsyncSession, node_id: int) -> Optional[str]:
    """Name of the flow a node is assigned to ("main flow" or the
    endpoint's name), or None. Used by delete guards."""
    row = (await session.exec(
        select(Settings).where(Settings.key == "active_node_id")
    )).first()
    if row and row.value.isdigit() and int(row.value) == node_id:
        return "main flow"
    ep = (await session.exec(
        select(ProxyEndpoint).where(ProxyEndpoint.node_id == node_id)
    )).first()
    return ep.name if ep else None
