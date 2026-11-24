"""Bypass PiTun's own TPROXY for a specific outbound operation.

The backend container runs `net=host`, so its own egress (panel API over
httpx, SSH to managed servers) is intercepted by the same nftables TPROXY as
LAN traffic and routed through the active node. That's the right default — a
server op then looks like it comes from the exit — but the operator sometimes
needs the opposite: dial DIRECTLY, off the tunnel (e.g. to reach a panel while
the active node is down, or deliberately not through the VPN).

`SO_MARK = 0xFF` is the universal bypass mark PiTun already sets on xray's own
outbounds; the prerouting/output nft rules `return` early for it. Setting the
same mark on a socket makes that socket skip TPROXY and take the host's real
default route. This module builds those socket options for the two clients
that need it — httpx (`socket_options=`) and asyncssh (`sock=`).
"""
from __future__ import annotations

import asyncio
import contextvars
import socket
from typing import List, Optional, Tuple

# Request-scoped "dial direct this time" flag. A FastAPI dependency
# (`read_direct`) captures `?direct=` into it, and XuiClient / the SSH
# helper read it as their default — so ONE dependency on a router makes
# every panel/SSH op in that request honour the toggle, without threading
# the flag through a dozen signatures. Copied into each request's context
# by asyncio, so concurrent requests don't see each other's value.
_direct_ctx: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "pitun_direct", default=False,
)


def direct_active() -> bool:
    """Is the current request asking to bypass the active node?"""
    return _direct_ctx.get()


async def read_direct(direct: bool = False) -> bool:
    """FastAPI dependency: latch `?direct=` for the whole request.

    Add it to a router's `dependencies=[...]` and every XuiClient/SSH call
    made while serving that request bypasses (or not) uniformly.
    """
    _direct_ctx.set(direct)
    return direct

# PiTun's universal TPROXY-bypass mark (matches SO_MARK on xray outbounds and
# the `meta mark 0xff return` nft rules).
SO_MARK_BYPASS = 0xFF

# SO_MARK is Linux-only. On the RPi/box it resolves to 36; on a non-Linux dev
# box the constant is absent, so fall back to the well-known numeric value —
# the option is still structurally correct and unit-testable there (it is only
# ever applied to a real socket on the Linux deployment).
_SO_MARK = getattr(socket, "SO_MARK", 36)


def bypass_socket_options() -> List[Tuple[int, int, int]]:
    """httpx `socket_options` that make every socket skip TPROXY (go direct)."""
    return [(socket.SOL_SOCKET, _SO_MARK, SO_MARK_BYPASS)]


def httpx_direct_transport():
    """An httpx AsyncHTTPTransport whose sockets carry the bypass mark.

    Pass as `AsyncClient(transport=...)`. Kept as its own factory so callers
    can build a direct client without importing socket details.
    """
    import httpx

    return httpx.AsyncHTTPTransport(
        socket_options=bypass_socket_options(),
        verify=False,
    )


async def bypass_connected_socket(
    host: str, port: int, *, timeout: float = 8.0,
) -> socket.socket:
    """A connected TCP socket carrying the bypass mark, for asyncssh `sock=`.

    Resolves `host`, opens the socket with SO_MARK set BEFORE connect (the
    mark must be on the SYN to affect routing), and connects. Caller owns the
    socket and must close it if asyncssh doesn't.
    """
    loop = asyncio.get_event_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    family, socktype, proto, _canon, sockaddr = infos[0]
    sock = socket.socket(family, socktype, proto)
    try:
        sock.setsockopt(socket.SOL_SOCKET, _SO_MARK, SO_MARK_BYPASS)
        sock.setblocking(False)
        await asyncio.wait_for(loop.sock_connect(sock, sockaddr), timeout=timeout)
    except Exception:
        sock.close()
        raise
    return sock


def host_port_free(port: int) -> bool:
    """Best-effort check: is this TCP port currently free to bind on the host?

    Used when allocating listener ports (routing-set TPROXY, proxy
    endpoints) to skip a port the operator happens to have something
    else listening on. Without this guard xray would silently fail to
    start its inbound with "address already in use".

    Defensive: socket creation may fail in unusual environments (no
    AF_INET, sandboxed test runner) — treat any error as "assume free"
    so the test suite doesn't break in those envs. The backend
    container itself runs with host network, so this check sees the
    real listener landscape.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Intentionally NO SO_REUSEADDR — a bare bind is the honest
        # "is this port truly free?" test.
        try:
            s.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False
        finally:
            s.close()
    except Exception:
        return True  # fail-open — never block allocation on env quirks
