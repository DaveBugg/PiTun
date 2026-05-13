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
import re
import secrets
import shlex
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Tuple

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


@dataclass
class DeployResult:
    """Outcome of `exec_remote_script`. The caller (api/servers.py
    deploy endpoint) parses `stdout` for a `URI=…` contract line and
    decides whether to insert a Node row.

    On a clean run, `ok=True` + `exit_code=0`. Any non-zero exit, SSH
    error, timeout, or upload failure produces `ok=False` with `error`
    set; partial stdout/stderr capture is preserved when possible so
    the admin can debug from the response body.
    """
    ok: bool
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    duration_sec: float = 0.0
    error: Optional[str] = None
    # SSH connect latency (TCP RTT) — useful for diagnosing slow
    # provisioning runs vs slow networks.
    connect_latency_ms: Optional[int] = None


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


# ── Auto-deploy: upload + run a local script on the remote VPS ───────────────


# Output capture cap. Provisioning scripts are chatty (Caddy install,
# certbot, naive download, etc.) but a runaway loop could easily spew
# hundreds of MB into the SSH session and OOM the backend. 256 KB per
# stream is enough for the verbose `setup-*` scripts plus diagnostic
# headroom; anything beyond that gets truncated with a marker.
_OUTPUT_CAP_BYTES = 256 * 1024

# Default deploy timeout (seconds). Naive provisioning is ~3-5 min on
# a fast VPS in a good region. Slow upstream package mirrors, an
# IPv6-broken host, or a first-time Caddy build can push the whole
# install to 15-25 min, so the cap needs to be generous. Originally
# 600s — bumped to 1800s (30 min) after a v1.3.0-beta.1 smoke test
# hit the cap mid-`apt-get install` on a fresh Debian 13 test VPS.
# Override via PITUN_DEPLOY_TIMEOUT_S env var if a really slow VPS
# needs even more headroom.
_DEFAULT_DEPLOY_TIMEOUT_S = float(os.environ.get("PITUN_DEPLOY_TIMEOUT_S", "1800"))


# ANSI escape sequence pattern. Provisioning scripts colour their
# progress lines with `\033[…m` (`info` / `warn` / etc). asyncssh
# delivers those bytes verbatim, and the frontend log panel renders
# raw text → users see literal `0;34m[i]0m` in the UI. Strip the
# escapes server-side before emitting each line; the colours don't
# carry semantic meaning here, only the message does.
#
# Pattern matches CSI sequences (ESC `[ … letter`) plus simple two-byte
# escapes (`ESC ? letter`). Keeps the regex narrow so non-ANSI byte
# sequences happen to start with `\033` aren't mangled.
_ANSI_RE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|[@-_])")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences. Cheap; called per output line."""
    return _ANSI_RE.sub("", text)


def _truncate(blob: str, cap: int = _OUTPUT_CAP_BYTES) -> str:
    """Cap a captured stream; mark truncation so the admin sees it."""
    if len(blob) <= cap:
        return blob
    head = blob[: cap - 100]
    return f"{head}\n…[truncated, {len(blob) - cap + 100} more bytes]…"


def _build_remote_command(
    remote_script_path: str, env: dict[str, str]
) -> str:
    """Compose the remote shell command that runs the uploaded script
    with the env-var assignments. Each value is shlex-quoted so a
    domain or password containing spaces / special chars stays a
    single argument. Final form:

        DOMAIN='proxy.example.com' EMAIL='me@x' \\
        NAIVE_USER='pitun' NAIVE_PASS='hunter2' bash /tmp/...sh

    Sub-command dispatch: if the env dict contains
    `PITUN_WG_SUBCOMMAND`, its value is appended as the script's
    first positional argument (and the env entry itself is removed
    from the prefix — it's just our internal channel for passing
    the arg). This lets multi-mode scripts like
    setup-wireguard-server.sh dispatch on $1 (`install`,
    `add-client`, `remove-client`, …) without us having to broaden
    `exec_remote_script` to take an args list across the whole
    code-path.

    We use `bash` explicitly (not just exec'ing the script) so a
    file-system mount option like `noexec` on /tmp doesn't reject it.
    """
    sub_command = env.pop("PITUN_WG_SUBCOMMAND", "")
    env_prefix = " ".join(
        f"{k}={shlex.quote(v)}" for k, v in env.items()
    )
    cmd = f"{env_prefix} bash {shlex.quote(remote_script_path)}"
    if sub_command:
        cmd += f" {shlex.quote(sub_command)}"
    return cmd


async def exec_remote_script(
    *,
    host: str,
    port: int = 22,
    username: str = "root",
    password: Optional[str] = None,
    private_key: Optional[str] = None,
    passphrase: Optional[str] = None,
    script_content: str,
    env: Optional[dict[str, str]] = None,
    timeout: float = _DEFAULT_DEPLOY_TIMEOUT_S,
) -> DeployResult:
    """Upload `script_content` to the remote host via SFTP, exec it
    under the supplied user (typically root), capture stdout/stderr,
    and clean up.

    Mirrors `test_ssh_connection`'s SO_MARK / DNS-bypass plumbing so
    the SSH session works alongside an active xray TPROXY ruleset on
    the PiTun host.

    Failure modes covered:
      * DNS resolution fails               → ok=False, error="DNS: …"
      * TCP connect fails / times out       → ok=False, error="TCP: …"
      * SSH auth fails                      → ok=False, error="auth: …"
      * SFTP upload fails                   → ok=False, error="upload: …"
      * Script runs but exits non-zero      → ok=False, exit_code=N,
                                              stdout/stderr captured
      * Script hangs past `timeout`         → ok=False, error="timeout"
      * stdout/stderr exceeds cap           → captured prefix + marker

    Cleanup: temp file on the remote is removed in a `finally` so even
    a failure / timeout doesn't leave deploy artifacts behind.
    """
    if not host:
        return DeployResult(ok=False, error="host is required")
    if not (private_key or password):
        return DeployResult(ok=False, error="no credentials configured")
    if not script_content:
        return DeployResult(ok=False, error="empty script_content")

    env = env or {}
    started_at = time.monotonic()

    # DNS bypass — same plumbing as test_ssh_connection.
    try:
        ip = await _resolve_direct(host)
    except Exception as exc:  # noqa: BLE001
        return DeployResult(ok=False, error=f"DNS: {exc}")

    try:
        import asyncssh  # type: ignore
    except ImportError as exc:
        return DeployResult(ok=False, error=f"asyncssh not installed: {exc}")

    loop = asyncio.get_event_loop()
    try:
        sock, tcp_rtt_ms = await loop.run_in_executor(
            None, _connect_marked, ip, port, _CONNECT_TIMEOUT_S
        )
    except (OSError, socket.timeout) as exc:
        return DeployResult(ok=False, error=f"TCP: {exc}")

    connect_kwargs: dict = {
        "host": host,
        "port": port,
        "username": username,
        "known_hosts": None,
        "sock": sock,
    }
    if private_key:
        try:
            key_obj = asyncssh.import_private_key(
                private_key,
                passphrase=passphrase or None,
            )
        except Exception as exc:  # noqa: BLE001
            sock.close()
            return DeployResult(
                ok=False,
                connect_latency_ms=tcp_rtt_ms,
                error=f"invalid private key: {exc}",
            )
        connect_kwargs["client_keys"] = [key_obj]
    elif password:
        connect_kwargs["password"] = password

    # Random suffix on the remote script name so concurrent deploys to
    # the same VPS don't stomp on each other (rare but possible if the
    # admin clicks Install twice quickly). `secrets.token_hex(8)` gives
    # 16 hex chars = 64 bits of entropy — collision-free in practice.
    remote_script_path = f"/tmp/pitun-deploy-{secrets.token_hex(8)}.sh"

    try:
        # `asyncio.timeout()` wraps the whole SSH/SFTP/exec window —
        # if the connection succeeds but the script hangs forever, we
        # still bail at `timeout` seconds with a structured error.
        async with asyncio.timeout(timeout + _CONNECT_TIMEOUT_S):
            async with asyncssh.connect(**connect_kwargs) as conn:
                # Upload via SFTP. asyncssh's start_sftp_client is the
                # idiomatic way; falling back to `cat > file` over an
                # exec channel would also work but is more error-prone
                # with arbitrary content (escaping, signal handling).
                try:
                    async with conn.start_sftp_client() as sftp:
                        # Write to remote in one shot — avoids a tmpfile
                        # rename dance, our scripts are small (<100 KB).
                        async with sftp.open(remote_script_path, "wb") as rf:
                            await rf.write(script_content.encode("utf-8"))
                        # chmod isn't strictly needed since we exec via
                        # `bash <path>` not via `<path>` directly, but
                        # +x is hygiene for log/inspection later if the
                        # cleanup didn't run.
                        await sftp.chmod(remote_script_path, 0o755)
                except Exception as exc:  # noqa: BLE001 — many SFTP error subtypes
                    return DeployResult(
                        ok=False,
                        connect_latency_ms=tcp_rtt_ms,
                        error=f"upload: {exc}",
                        duration_sec=round(time.monotonic() - started_at, 3),
                    )

                # Run with env-var prefix.
                cmd = _build_remote_command(remote_script_path, env)
                try:
                    proc = await conn.run(
                        cmd,
                        timeout=timeout,
                        check=False,
                        # Don't merge — the URI parser scans stdout only.
                        # Keep stderr separate for "what went wrong" surfacing.
                        stderr=asyncssh.PIPE,  # type: ignore[attr-defined]
                    )
                except asyncio.TimeoutError:
                    return DeployResult(
                        ok=False,
                        connect_latency_ms=tcp_rtt_ms,
                        error=f"timeout: script exceeded {int(timeout)}s",
                        duration_sec=round(time.monotonic() - started_at, 3),
                    )
                finally:
                    # Cleanup — best-effort. Never raise out of cleanup;
                    # we'd rather leave a small file behind than mask
                    # the actual deploy result.
                    try:
                        await conn.run(f"rm -f {shlex.quote(remote_script_path)}", check=False)
                    except Exception as cleanup_exc:  # noqa: BLE001
                        logger.debug(
                            "Failed to clean up %s on %s: %s",
                            remote_script_path, host, cleanup_exc,
                        )

                stdout = proc.stdout if isinstance(proc.stdout, str) else ""
                stderr = proc.stderr if isinstance(proc.stderr, str) else ""
                exit_code = proc.exit_status if proc.exit_status is not None else -1

                ok = exit_code == 0
                error: Optional[str] = None
                if not ok:
                    # Pull a one-line summary from stderr's last
                    # meaningful line for the response — the admin sees
                    # the full stream too, this just gives them
                    # "what went wrong" at a glance.
                    last_err = ""
                    for line in reversed(stderr.splitlines()):
                        line = line.strip()
                        if line:
                            last_err = line
                            break
                    error = (
                        f"script exit={exit_code}"
                        + (f": {last_err}" if last_err else "")
                    )

                return DeployResult(
                    ok=ok,
                    exit_code=exit_code,
                    stdout=_truncate(stdout),
                    stderr=_truncate(stderr),
                    duration_sec=round(time.monotonic() - started_at, 3),
                    error=error,
                    connect_latency_ms=tcp_rtt_ms,
                )

    except asyncio.TimeoutError:
        return DeployResult(
            ok=False,
            connect_latency_ms=tcp_rtt_ms,
            error=f"timeout: SSH session exceeded {int(timeout + _CONNECT_TIMEOUT_S)}s",
            duration_sec=round(time.monotonic() - started_at, 3),
        )
    except Exception as exc:  # noqa: BLE001 — auth/protocol errors verbatim
        err = str(exc).strip() or exc.__class__.__name__
        return DeployResult(
            ok=False,
            connect_latency_ms=tcp_rtt_ms,
            error=err,
            duration_sec=round(time.monotonic() - started_at, 3),
        )


# ── SFTP one-shot file upload ────────────────────────────────────────────────


async def upload_file_to_remote(
    *,
    host: str,
    port: int = 22,
    username: str = "root",
    password: Optional[str] = None,
    private_key: Optional[str] = None,
    passphrase: Optional[str] = None,
    remote_path: str,
    content: bytes,
    timeout: float = 120.0,
) -> None:
    """Push `content` bytes to `remote_path` via SFTP.

    Mirrors `exec_remote_script`'s connection plumbing so the upload
    rides the same SO_MARK-tagged socket — important on the PiTun
    host where tproxy would otherwise drop the SSH packets.

    Raises on any failure (DNS, TCP, auth, SFTP write); callers map
    to HTTPException. We deliberately do NOT chmod here — the script
    that consumes the file handles permissions in its own pass, so
    the SFTP-default 0644 is fine for the brief lifetime.
    """
    try:
        import asyncssh  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"asyncssh not installed: {exc}") from exc

    ip = await _resolve_direct(host)
    loop = asyncio.get_event_loop()
    sock, _ = await loop.run_in_executor(
        None, _connect_marked, ip, port, _CONNECT_TIMEOUT_S,
    )

    connect_kwargs: dict = {
        "host": host, "port": port, "username": username,
        "known_hosts": None, "sock": sock,
    }
    if private_key:
        key_obj = asyncssh.import_private_key(
            private_key, passphrase=passphrase or None,
        )
        connect_kwargs["client_keys"] = [key_obj]
    elif password:
        connect_kwargs["password"] = password

    async with asyncio.timeout(timeout):
        async with asyncssh.connect(**connect_kwargs) as conn:
            async with conn.start_sftp_client() as sftp:
                # Open in binary write mode; asyncssh's SFTP wraps
                # the remote file handle in a context manager.
                async with sftp.open(remote_path, "wb") as fh:
                    await fh.write(content)


# ── Streaming variant for the v1.3.0 server-tasks subsystem ──────────────────


async def exec_remote_script_streaming(
    *,
    host: str,
    port: int = 22,
    username: str = "root",
    password: Optional[str] = None,
    private_key: Optional[str] = None,
    passphrase: Optional[str] = None,
    script_content: str,
    env: Optional[dict[str, str]] = None,
    timeout: float = _DEFAULT_DEPLOY_TIMEOUT_S,
    on_line: Callable[[str, str], "asyncio.Future[Any] | Any"],
    extra_files: Optional[dict[str, bytes]] = None,
) -> DeployResult:
    """Same contract as `exec_remote_script` but invokes
    `on_line(kind, line)` on every full output line as it arrives.

    Used by `core.jobs.JobManager` to fan out lines to the live log
    buffer + WS subscribers in real time, instead of buffering for
    minutes and emitting everything at the end.

    Implementation differs in one place: `conn.create_process()` +
    parallel readers on stdout/stderr instead of `conn.run()` (which
    waits for completion and returns whole streams). Everything
    else — DNS bypass, SO_MARK, SFTP upload, env-prefix command,
    cleanup — is identical.

    `on_line` may be sync or async. Sync callables are awaited via
    `asyncio.iscoroutine` check on the return value (no-op if it
    returned None). This lets tests pass plain `lambda` mocks
    alongside production `async def` JobManager hooks.
    """
    if not host:
        return DeployResult(ok=False, error="host is required")
    if not (private_key or password):
        return DeployResult(ok=False, error="no credentials configured")
    if not script_content:
        return DeployResult(ok=False, error="empty script_content")

    env = env or {}
    started_at = time.monotonic()

    try:
        ip = await _resolve_direct(host)
    except Exception as exc:  # noqa: BLE001
        return DeployResult(ok=False, error=f"DNS: {exc}")

    try:
        import asyncssh  # type: ignore
    except ImportError as exc:
        return DeployResult(ok=False, error=f"asyncssh not installed: {exc}")

    loop = asyncio.get_event_loop()
    try:
        sock, tcp_rtt_ms = await loop.run_in_executor(
            None, _connect_marked, ip, port, _CONNECT_TIMEOUT_S
        )
    except (OSError, socket.timeout) as exc:
        return DeployResult(ok=False, error=f"TCP: {exc}")

    connect_kwargs: dict = {
        "host": host,
        "port": port,
        "username": username,
        "known_hosts": None,
        "sock": sock,
    }
    if private_key:
        try:
            key_obj = asyncssh.import_private_key(
                private_key,
                passphrase=passphrase or None,
            )
        except Exception as exc:  # noqa: BLE001
            sock.close()
            return DeployResult(
                ok=False,
                connect_latency_ms=tcp_rtt_ms,
                error=f"invalid private key: {exc}",
            )
        connect_kwargs["client_keys"] = [key_obj]
    elif password:
        connect_kwargs["password"] = password

    remote_script_path = f"/tmp/pitun-deploy-{secrets.token_hex(8)}.sh"

    # Buffers we'll return at the end (same as sync variant — caller
    # gets the FULL stdout/stderr as well as having received per-line
    # callbacks). 256 KB cap each, marker on overflow.
    full_stdout_parts: list[str] = []
    full_stderr_parts: list[str] = []
    stdout_size = 0
    stderr_size = 0

    async def _safe_call(kind: str, line: str) -> None:
        """Invoke on_line, tolerate sync vs async callables, swallow
        callback errors (a buggy subscriber must NOT abort the deploy).
        """
        try:
            ret = on_line(kind, line)
            if asyncio.iscoroutine(ret):
                await ret
        except Exception as exc:  # noqa: BLE001 — log + continue
            logger.debug("on_line callback raised (non-fatal): %s", exc)

    async def _drain(stream, kind: str):
        """Read a stream line-by-line, push to on_line + accumulate
        in the full-output buffer. Stops on EOF or stream close."""
        nonlocal stdout_size, stderr_size
        try:
            async for raw in stream:
                # asyncssh streams yield strings already (decoded with
                # the connection's encoding setting, default utf-8).
                # Each yield is a chunk that may contain partial lines —
                # we explicitly split on \n and keep a remainder.
                # However, in practice asyncssh's process streams iterate
                # by-line if `bufsize` was lined; we still defensively
                # split.
                text = raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
                for line in text.splitlines():
                    if line == "" and not text:
                        continue
                    # Strip ANSI escape sequences once on the server
                    # side so subscribers (WS log panel + log_tail in
                    # the DB) get clean text. setup-*-server.sh scripts
                    # colourise progress lines with `\033[0;34m` etc;
                    # without this strip the UI shows literal escape
                    # bytes (`0;34m[i]0m Detected: …`).
                    line = _strip_ansi(line)
                    await _safe_call(kind, line)
                    if kind == "stdout":
                        if stdout_size < _OUTPUT_CAP_BYTES:
                            full_stdout_parts.append(line + "\n")
                            stdout_size += len(line) + 1
                    else:
                        if stderr_size < _OUTPUT_CAP_BYTES:
                            full_stderr_parts.append(line + "\n")
                            stderr_size += len(line) + 1
        except Exception as exc:  # noqa: BLE001
            logger.debug("Stream %s drain ended: %s", kind, exc)

    try:
        async with asyncio.timeout(timeout + _CONNECT_TIMEOUT_S):
            async with asyncssh.connect(**connect_kwargs) as conn:
                # Upload script via SFTP — same as sync variant.
                # `extra_files` (since v1.3.0-beta.6) ships
                # companion artifacts alongside the script. Keys
                # are absolute remote paths (e.g.
                # `/tmp/pitun-template.zip`); values are file
                # bytes. Used by the custom-template path — the
                # zip lands at the well-known location the script
                # extracts from.
                try:
                    async with conn.start_sftp_client() as sftp:
                        async with sftp.open(remote_script_path, "wb") as rf:
                            await rf.write(script_content.encode("utf-8"))
                        await sftp.chmod(remote_script_path, 0o755)
                        if extra_files:
                            for remote_path, data in extra_files.items():
                                # Defensive: reject anything that
                                # isn't an explicit /tmp/pitun-* path
                                # so a misconfigured caller can't
                                # ever overwrite an arbitrary file.
                                if not remote_path.startswith("/tmp/pitun-"):
                                    return DeployResult(
                                        ok=False,
                                        connect_latency_ms=tcp_rtt_ms,
                                        error=(
                                            f"unsafe extra_file path: {remote_path!r} "
                                            "(must start with /tmp/pitun-)"
                                        ),
                                        duration_sec=round(time.monotonic() - started_at, 3),
                                    )
                                if ".." in remote_path:
                                    return DeployResult(
                                        ok=False,
                                        connect_latency_ms=tcp_rtt_ms,
                                        error=f"path traversal in: {remote_path!r}",
                                        duration_sec=round(time.monotonic() - started_at, 3),
                                    )
                                async with sftp.open(remote_path, "wb") as ef:
                                    await ef.write(data)
                                await sftp.chmod(remote_path, 0o644)
                except Exception as exc:  # noqa: BLE001
                    return DeployResult(
                        ok=False,
                        connect_latency_ms=tcp_rtt_ms,
                        error=f"upload: {exc}",
                        duration_sec=round(time.monotonic() - started_at, 3),
                    )

                cmd = _build_remote_command(remote_script_path, env)

                # Use create_process for true streaming. wait() blocks
                # until exit; we run drain coroutines in parallel so
                # output flows to subscribers in real time.
                #
                # Allocate a PTY (`term_type="dumb"`, no echo) so the
                # remote bash and its child commands (apt, caddy, …) see
                # an interactive terminal and switch to LINE-BUFFERED
                # stdout. Without a PTY they default to BLOCK-buffered
                # 4 KB chunks, which makes the live log appear to "hang"
                # for minutes between the first banner and the eventual
                # apt-get / Caddy build progress lines (observed in the
                # wild during v1.3.0-beta.1 smoke testing). `dumb`
                # avoids any colour-capability negotiation that would
                # otherwise emit terminfo-specific escapes; we still
                # strip whatever ANSI the script emits explicitly via
                # `strip_ansi()` later.
                try:
                    proc = await conn.create_process(
                        cmd,
                        term_type="dumb",
                        # 80x24 is plenty for our progress lines
                        term_size=(80, 24),
                    )
                except Exception as exc:  # noqa: BLE001
                    return DeployResult(
                        ok=False,
                        connect_latency_ms=tcp_rtt_ms,
                        error=f"exec: {exc}",
                        duration_sec=round(time.monotonic() - started_at, 3),
                    )

                try:
                    # Run drain tasks alongside the wait — gather
                    # ensures we collect ALL output even after exit.
                    await asyncio.wait_for(
                        asyncio.gather(
                            _drain(proc.stdout, "stdout"),
                            _drain(proc.stderr, "stderr"),
                            proc.wait(),
                        ),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    try:
                        proc.terminate()
                    except Exception:  # noqa: BLE001
                        pass
                    return DeployResult(
                        ok=False,
                        connect_latency_ms=tcp_rtt_ms,
                        stdout=_truncate("".join(full_stdout_parts)),
                        stderr=_truncate("".join(full_stderr_parts)),
                        error=f"timeout: script exceeded {int(timeout)}s",
                        duration_sec=round(time.monotonic() - started_at, 3),
                    )
                finally:
                    # Cleanup — best-effort
                    try:
                        await conn.run(f"rm -f {shlex.quote(remote_script_path)}", check=False)
                    except Exception as cleanup_exc:  # noqa: BLE001
                        logger.debug(
                            "Failed to clean up %s on %s: %s",
                            remote_script_path, host, cleanup_exc,
                        )

                exit_code = proc.exit_status if proc.exit_status is not None else -1
                stdout = _truncate("".join(full_stdout_parts))
                stderr = _truncate("".join(full_stderr_parts))

                ok = exit_code == 0
                error: Optional[str] = None
                if not ok:
                    last_err = ""
                    for line in reversed(stderr.splitlines()):
                        line_s = line.strip()
                        if line_s:
                            last_err = line_s
                            break
                    error = (
                        f"script exit={exit_code}"
                        + (f": {last_err}" if last_err else "")
                    )

                return DeployResult(
                    ok=ok,
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    duration_sec=round(time.monotonic() - started_at, 3),
                    error=error,
                    connect_latency_ms=tcp_rtt_ms,
                )

    except asyncio.TimeoutError:
        return DeployResult(
            ok=False,
            connect_latency_ms=tcp_rtt_ms,
            error=f"timeout: SSH session exceeded {int(timeout + _CONNECT_TIMEOUT_S)}s",
            duration_sec=round(time.monotonic() - started_at, 3),
        )
    except Exception as exc:  # noqa: BLE001
        err = str(exc).strip() or exc.__class__.__name__
        return DeployResult(
            ok=False,
            connect_latency_ms=tcp_rtt_ms,
            error=err,
            duration_sec=round(time.monotonic() - started_at, 3),
        )
