"""SSH connection helper for the Servers feature.

Wraps asyncssh for the Server-tab probe endpoint, with a critical
twist: every outbound socket uses `SO_MARK = 0xFF` so the connection
bypasses the in-host TPROXY rules. Without that mark, SYN packets from
the backend container get redirected into xray's TPROXY listener,
which either:

  - drops them silently when xray isn't running (timeout)
  - succeeds locally in <1ms when xray IS running (because we connect
    to the local xray inbound, not the remote host) — making "TCP RTT"
    measurements meaningless

The SO_MARK convention (0xFF) and DNS-via-marked-UDP-to-8.8.8.8 are
borrowed from `app/core/healthcheck.py:HealthChecker._tcp_ping_sync`
and `_resolve_direct`. The nft TPROXY ruleset has an explicit
`mark eq 0xFF return` exception for these probes — see
`app/core/nftables.py`.

CAP_NET_ADMIN is required for SO_MARK; the backend container has it
via `cap_add: NET_ADMIN` in docker-compose.yml.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import struct
import time
from dataclasses import dataclass
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Bypass mark: the nft ruleset's `mark eq 0xFF return` lets these
# packets skip TPROXY interception. Same value HealthChecker uses.
_BYPASS_MARK = 0xFF

_PROBE_CMD = "uname -a; echo ---; (cat /etc/os-release 2>/dev/null | head -3) || true"

_CONNECT_TIMEOUT_S = 8.0
_EXEC_TIMEOUT_S = 6.0


@dataclass
class SSHTestResult:
    ok: bool
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    remote_info: Optional[str] = None


# ── Helpers ──────────────────────────────────────────────────────────────────

def _set_bypass_mark(sock: socket.socket) -> None:
    """Apply SO_MARK so this socket's traffic skips TPROXY. Best-effort —
    if the syscall fails (no CAP_NET_ADMIN, non-Linux, etc.) we fall
    back to a plain socket and the connect will likely time out. That
    failure mode is at least loud, unlike a silently-misrouted probe."""
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_MARK, _BYPASS_MARK)
    except (OSError, AttributeError):
        pass


async def _resolve_direct(address: str) -> str:
    """Resolve `address` to an IPv4 string via 8.8.8.8, with SO_MARK
    bypass on the UDP socket so the DNS query itself doesn't get
    intercepted by TPROXY. If `address` is already a literal IP, return
    as-is.

    Mirrors the convention in healthcheck._resolve_direct — keeping the
    pattern in two places is intentional: the SSH probe needs the
    bypass too, and pulling it into a shared util would require either
    moving the helper or importing across module boundaries that today
    are clean."""
    try:
        socket.inet_aton(address)
        return address
    except OSError:
        pass

    def _sync_resolve() -> str:
        txn_id = os.urandom(2)
        name_parts = b""
        for part in address.encode().split(b"."):
            name_parts += bytes([len(part)]) + part
        name_parts += b"\x00"
        # Standard A-record query (qtype=1, qclass=1, RD=1)
        query = txn_id + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00" + name_parts + b"\x00\x01\x00\x01"

        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(3)
        _set_bypass_mark(s)
        try:
            s.sendto(query, ("8.8.8.8", 53))
            data = s.recv(512)
        finally:
            s.close()

        # Skip 12-byte header + question section, scan the answer section
        # for the first A record (rtype=1, rdlen=4).
        pos = 12
        while pos < len(data) and data[pos] != 0:
            pos += data[pos] + 1
        pos += 5  # null terminator (1) + qtype (2) + qclass (2)
        an_count = struct.unpack("!H", data[6:8])[0]
        for _ in range(an_count):
            # Name field — either pointer (top two bits 11) or labels.
            if data[pos] & 0xC0 == 0xC0:
                pos += 2
            else:
                while pos < len(data) and data[pos] != 0:
                    pos += data[pos] + 1
                pos += 1
            rtype = struct.unpack("!H", data[pos:pos + 2])[0]
            rdlen = struct.unpack("!H", data[pos + 8:pos + 10])[0]
            pos += 10
            if rtype == 1 and rdlen == 4:
                return socket.inet_ntoa(data[pos:pos + 4])
            pos += rdlen
        raise OSError(f"could not resolve {address!r}")

    return await asyncio.get_event_loop().run_in_executor(None, _sync_resolve)


def _connect_marked(ip: str, port: int, timeout: float) -> Tuple[socket.socket, int]:
    """Sync TCP connect with SO_MARK=0xFF, returns (connected_sock, rtt_ms).

    Caller is responsible for closing the socket (or handing it off to
    asyncssh, which will close on its end). On error the socket is
    closed before re-raising."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _set_bypass_mark(sock)
    sock.settimeout(timeout)
    started = time.monotonic()
    try:
        sock.connect((ip, port))
    except Exception:
        sock.close()
        raise
    rtt_ms = int((time.monotonic() - started) * 1000)
    return sock, rtt_ms


# ── Public API ───────────────────────────────────────────────────────────────

async def test_ssh_connection(
    *,
    host: str,
    port: int = 22,
    username: str = "root",
    password: Optional[str] = None,
    private_key: Optional[str] = None,
    passphrase: Optional[str] = None,
) -> SSHTestResult:
    """Connect to `host:port` over SSH with TPROXY bypass, run a cheap
    `uname -a` probe, and return latency + remote info.

    Two-step structure:
      1. Resolve `host` to an IP via DNS bypass
      2. Open a TCP socket with SO_MARK=0xFF and connect to (ip, port).
         The connect time IS the displayed latency — pure TCP RTT, not
         influenced by SSH crypto cost.
      3. Hand the connected, marked socket to asyncssh via `sock=`.
         asyncssh continues the SSH protocol exchange on this socket;
         all subsequent packets inherit the socket's mark and keep
         bypassing TPROXY.

    Auth: `private_key` takes precedence over `password`. At least one
    must be set; both empty returns a structured failure rather than
    raising.
    """
    if not host:
        return SSHTestResult(ok=False, error="host is required")
    if not (private_key or password):
        return SSHTestResult(ok=False, error="no credentials configured")

    # Resolve hostname (or pass IP through). DNS goes via marked UDP so
    # an in-host TPROXY rule for :53 doesn't catch us.
    try:
        ip = await _resolve_direct(host)
    except Exception as exc:  # noqa: BLE001 — surface DNS errors verbatim
        return SSHTestResult(ok=False, error=f"DNS: {exc}")

    # Lazy-import asyncssh so a missing wheel doesn't break the rest of
    # the API surface.
    try:
        import asyncssh  # type: ignore
    except ImportError as exc:
        return SSHTestResult(ok=False, error=f"asyncssh not installed: {exc}")

    # TCP connect with SO_MARK — measure latency here, pass marked sock
    # to asyncssh below so the SSH session continues to bypass TPROXY.
    loop = asyncio.get_event_loop()
    try:
        sock, tcp_rtt_ms = await loop.run_in_executor(
            None, _connect_marked, ip, port, _CONNECT_TIMEOUT_S
        )
    except (OSError, socket.timeout) as exc:
        return SSHTestResult(ok=False, error=f"TCP: {exc}")

    # Build asyncssh kwargs. The `sock=` parameter tells asyncssh to use
    # an already-connected socket instead of opening a new one — this
    # is the trick that preserves SO_MARK across the SSH session.
    connect_kwargs: dict = {
        "host": host,            # used for SSH host-key bookkeeping only
        "port": port,
        "username": username,
        "known_hosts": None,     # admin trust boundary, see SECURITY.md
        "sock": sock,
    }
    if private_key:
        try:
            key_obj = asyncssh.import_private_key(
                private_key,
                passphrase=passphrase or None,
            )
        except Exception as exc:  # noqa: BLE001 — asyncssh raises various subtypes
            sock.close()
            return SSHTestResult(
                ok=False,
                latency_ms=tcp_rtt_ms,
                error=f"invalid private key: {exc}",
            )
        connect_kwargs["client_keys"] = [key_obj]
    elif password:
        connect_kwargs["password"] = password

    try:
        async with asyncio.timeout(_EXEC_TIMEOUT_S + 5):
            async with asyncssh.connect(**connect_kwargs) as conn:
                proc = await conn.run(_PROBE_CMD, timeout=_EXEC_TIMEOUT_S, check=False)
                stdout = (proc.stdout or "").strip() if isinstance(proc.stdout, str) else ""
                if len(stdout) > 800:
                    stdout = stdout[:800] + "…"
                return SSHTestResult(
                    ok=True,
                    latency_ms=tcp_rtt_ms,
                    remote_info=stdout or None,
                )
    except asyncio.TimeoutError:
        return SSHTestResult(
            ok=False,
            latency_ms=tcp_rtt_ms,
            error="SSH session timed out",
        )
    except Exception as exc:  # noqa: BLE001 — surface auth/protocol errors verbatim
        err = str(exc).strip() or exc.__class__.__name__
        return SSHTestResult(ok=False, latency_ms=tcp_rtt_ms, error=err)
