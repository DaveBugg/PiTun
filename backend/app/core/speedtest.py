"""
Speed test via a dedicated temporary xray instance.

Design goals:
- INDEPENDENT of user's routing mode (rules / global / bypass / TUN).
  The main xray config and nftables rules do not affect the measurement.
- INDEPENDENT of DNS state. Both the node address and all test URL hostnames
  are pre-resolved via SO_MARK=0xff raw UDP (bypasses tproxy), so the temp
  xray instance never performs runtime DNS lookups.
- Safe under node circle rotation — each call spawns its own short-lived
  xray process bound to a specific node, with guaranteed cleanup (no zombies).
"""
import asyncio
import json
import logging
import os
import random
import socket
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.core.config_gen import _build_outbound
from app.core.healthcheck import HealthChecker
from app.database import get_async_engine
from app.models import Node

logger = logging.getLogger(__name__)

# HTTP endpoints only — we do not require TLS for speed measurement and it
# avoids SNI/cert issues when forcing --resolve. Ordered by reliability.
_TEST_URLS = [
    "http://speedtest.tele2.net/10MB.zip",
    "http://proof.ovh.net/files/10Mb.dat",
    "http://ipv4.download.thinkbroadband.com/5MB.zip",
]

_CURL_TIMEOUT = 20            # seconds per URL attempt
_XRAY_STARTUP_WAIT = 2.5      # seconds to wait for temp xray to bind SOCKS
_KILL_GRACE = 5               # seconds to wait after SIGTERM before SIGKILL
_MIN_BYTES = 65_536           # anything smaller is not a valid measurement


async def speedtest_node(node: Node) -> Dict:
    """Run a download speed test through a freshly-spawned xray for `node`."""
    proc: Optional[asyncio.subprocess.Process] = None
    tmp_path: Optional[str] = None
    try:
        # Resolve the full chain: [entry_parent, ..., node]. For a non-chained
        # node, chain == [node]. For a chained WG (WG blocked by ISP), chain
        # looks like [proxy_parent, wg] — and only the entry parent's address
        # needs to be reachable directly; everything deeper tunnels through it.
        try:
            chain = await _resolve_chain(node)
        except Exception as exc:
            return _result(node, error=f"chain resolve: {exc}")

        entry = chain[0]
        # Naive outbounds point at the LOCAL sidecar (127.0.0.1:<internal_port>),
        # not at the remote server — xray doesn't speak naive's HTTPS-masquerade
        # protocol. The sidecar handles its own DNS and remote connection, so
        # we skip the "pre-resolve entry IP + rewrite outbound address" dance
        # that other protocols need. If the entry hop is naive with a chain
        # this is a config error anyway (naive can't be tunneled through
        # xray), but for the common case of a standalone naive node it "just
        # works" — the sidecar must be running (we verify below).
        entry_ip: Optional[str] = None
        if entry.protocol != "naive":
            try:
                entry_ip = await HealthChecker._resolve_direct(entry.address)
            except Exception as exc:
                return _result(node, error=f"dns entry: {exc}")

        # Fail fast if naive sidecar isn't up — otherwise temp xray starts but
        # every connection attempt hangs until _CURL_TIMEOUT.
        if node.protocol == "naive":
            if not node.internal_port:
                return _result(node, error="naive sidecar port not allocated")
            if not await _port_open("127.0.0.1", int(node.internal_port)):
                return _result(node, error=f"naive sidecar not listening on :{node.internal_port}")

        resolved_urls = await _resolve_test_urls()
        if not resolved_urls:
            return _result(node, error="Failed to resolve any test URL")

        socks_port, proc, tmp_path = await _start_temp_xray(node, chain, entry_ip)
        if proc is None:
            return _result(node, error="Failed to start temp xray")

        return await _run_curl_speedtest(node, socks_port, resolved_urls)

    except Exception as exc:
        logger.warning("Speedtest node %d error: %s", node.id, exc)
        return _result(node, error=str(exc))
    finally:
        await _cleanup(proc, tmp_path)


# ── helpers ──────────────────────────────────────────────────────────────────


async def _resolve_chain(node: Node) -> List[Node]:
    """
    Walk `node.chain_node_id` upward, returning chain from entry parent to
    `node` (inclusive). Detects cycles and disabled/missing parents.
    Entry parent is chain[0] — the hop that must be reachable directly.
    """
    async with AsyncSession(get_async_engine()) as session:
        ordered: List[Node] = [node]
        seen = {node.id}
        cur = node
        while cur.chain_node_id:
            if cur.chain_node_id in seen:
                logger.warning("Chain cycle detected at node %d", cur.id)
                break
            parent = await session.get(Node, cur.chain_node_id)
            if not parent or not parent.enabled:
                logger.warning("Chain parent %s unavailable for node %d", cur.chain_node_id, cur.id)
                break
            ordered.insert(0, parent)
            seen.add(parent.id)
            cur = parent
        return ordered


async def _resolve_test_urls() -> List[Tuple[str, str, str, int]]:
    """Resolve every test URL to (url, host, ip, port) via direct DNS."""
    out: List[Tuple[str, str, str, int]] = []
    for url in _TEST_URLS:
        try:
            # naive parse — urlparse is fine but adds an import for no gain
            after_scheme = url.split("://", 1)[1]
            host_port = after_scheme.split("/", 1)[0]
            if ":" in host_port:
                host, port_s = host_port.split(":", 1)
                port = int(port_s)
            else:
                host = host_port
                port = 443 if url.startswith("https") else 80
            ip = await HealthChecker._resolve_direct(host)
            out.append((url, host, ip, port))
        except Exception as exc:
            logger.debug("Skip test URL %s (resolve failed): %s", url, exc)
            continue
    return out


async def _start_temp_xray(
    node: Node, chain: List[Node], entry_ip: Optional[str]
) -> Tuple[int, Optional[asyncio.subprocess.Process], Optional[str]]:
    """
    Start a minimal xray instance bound to 127.0.0.1:<random>.

    `chain` is the full tunnel path [entry, ..., node]. For a 1-element chain
    we just build that single outbound. For N elements we build each as a
    separate outbound and link them via `proxySettings.tag` so that the
    entry hop dials its IP directly and each subsequent hop tunnels through
    the previous one — exactly how the main xray config handles chaining.

    Only the `entry` hop's address is pre-resolved, because every other hop
    is dialed via the previous hop and xray must resolve those hostnames
    inside the tunnel. `entry_ip` is None for naive entries (the address
    inside the outbound is already 127.0.0.1:<sidecar_port>, not the remote
    server — see comment in speedtest_node).
    """
    socks_port = random.randint(19000, 19999)

    try:
        outbounds_chain: List[Dict] = []
        for i, hop in enumerate(chain):
            ob = _build_outbound(hop)
            ob["tag"] = f"speed-hop-{i}"
            if i == 0:
                # Entry hop: pre-resolved IP, reached directly. `entry_ip`
                # is None for naive entries — they point at the local
                # sidecar, no address rewrite needed.
                if entry_ip is not None:
                    _override_outbound_address(ob, entry_ip)
            else:
                # Non-entry hop: tunnel through previous hop.
                ob["proxySettings"] = {
                    "tag": f"speed-hop-{i - 1}",
                    "transportLayer": True,
                }
            outbounds_chain.append(ob)
    except Exception as exc:
        logger.warning("Cannot build chain outbounds for node %d: %s", node.id, exc)
        return 0, None, None

    final_tag = outbounds_chain[-1]["tag"]

    # Direct outbound also gets mark=255 (in case xray ever uses it) so it
    # bypasses tproxy too.
    direct_outbound = {
        "tag": "direct",
        "protocol": "freedom",
        "settings": {"domainStrategy": "AsIs"},
        "streamSettings": {"sockopt": {"mark": 255}},
    }

    tmp_config = {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "tag": "socks-speed",
            "protocol": "socks",
            "port": socks_port,
            "listen": "127.0.0.1",
            "settings": {"auth": "noauth", "udp": False},
        }],
        "outbounds": [*outbounds_chain, direct_outbound],
        "routing": {
            "rules": [
                {"type": "field", "inboundTag": ["socks-speed"], "outboundTag": final_tag}
            ],
        },
    }

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(tmp_config, f)
        tmp_path = f.name

    env = os.environ.copy()
    env["XRAY_LOCATION_ASSET"] = str(Path(settings.xray_geoip_path).parent)

    proc = await asyncio.create_subprocess_exec(
        settings.xray_binary, "run", "-config", tmp_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    # Poll for the SOCKS port to become ready, up to _XRAY_STARTUP_WAIT.
    deadline = asyncio.get_event_loop().time() + _XRAY_STARTUP_WAIT
    while asyncio.get_event_loop().time() < deadline:
        if proc.returncode is not None:
            stdout_data, stderr_data = await proc.communicate()
            err = (stderr_data or stdout_data or b"").decode(errors="replace")[-300:]
            logger.warning("Temp xray for node %d died early: %s", node.id, err)
            _safe_unlink(tmp_path)
            return 0, None, None
        if await _port_open("127.0.0.1", socks_port):
            return socks_port, proc, tmp_path
        await asyncio.sleep(0.15)

    # Timed out waiting for port — still alive but unresponsive
    logger.warning("Temp xray for node %d: SOCKS port %d never opened", node.id, socks_port)
    return socks_port, proc, tmp_path  # caller still cleans up via finally


def _override_outbound_address(outbound: Dict, ip: str) -> None:
    """Replace the hostname in `outbound` with a pre-resolved IP."""
    proto = outbound.get("protocol")
    s = outbound.get("settings") or {}
    if proto in ("vless", "vmess"):
        v = s.get("vnext") or []
        if v:
            v[0]["address"] = ip
    elif proto in ("trojan", "shadowsocks"):
        srv = s.get("servers") or []
        if srv:
            srv[0]["address"] = ip
    elif proto == "socks":
        srv = s.get("servers") or []
        if srv:
            srv[0]["address"] = ip
    elif proto == "hysteria":
        s["address"] = ip
    elif proto == "wireguard":
        peers = s.get("peers") or []
        for p in peers:
            ep = p.get("endpoint") or ""
            if ":" in ep:
                _, port = ep.rsplit(":", 1)
                p["endpoint"] = f"{ip}:{port}"


async def _port_open(host: str, port: int) -> bool:
    try:
        fut = asyncio.open_connection(host, port)
        _, w = await asyncio.wait_for(fut, timeout=0.5)
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def _run_curl_speedtest(
    node: Node, socks_port: int, resolved_urls: List[Tuple[str, str, str, int]]
) -> Dict:
    """Try each pre-resolved URL via SOCKS until one produces a valid sample."""
    last_error = "no test url succeeded"

    for url, host, ip, port in resolved_urls:
        try:
            args = [
                "curl", "-s",
                "-x", f"socks5h://127.0.0.1:{socks_port}",
                "--resolve", f"{host}:{port}:{ip}",
                "-o", "/dev/null",
                "-w", "%{size_download} %{speed_download}",
                "--max-time", str(_CURL_TIMEOUT),
                url,
            ]
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_data, _ = await asyncio.wait_for(
                    proc.communicate(), timeout=_CURL_TIMEOUT + 5
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
                await proc.wait()
                last_error = f"timeout on {host}"
                continue

            parts = stdout_data.decode().strip().split()
            if len(parts) < 2:
                last_error = f"no output from {host}"
                continue

            downloaded = int(float(parts[0]))
            speed_bps = float(parts[1])

            if downloaded < _MIN_BYTES:
                last_error = f"too small ({downloaded}B) from {host}"
                continue

            mbps = round(speed_bps * 8 / 1_000_000, 2)
            return _result(node, download_mbps=mbps)

        except Exception as exc:
            last_error = f"{host}: {exc}"
            continue

    return _result(node, error=last_error)


async def _cleanup(proc: Optional[asyncio.subprocess.Process], tmp_path: Optional[str]) -> None:
    """Guarantee the temp xray process is reaped and the temp file removed."""
    if proc is not None and proc.returncode is None:
        try:
            proc.terminate()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=_KILL_GRACE)
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2)
            except asyncio.TimeoutError:
                logger.warning("Temp xray refused to die; possible zombie")
    _safe_unlink(tmp_path)


def _safe_unlink(path: Optional[str]) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _result(node: Node, download_mbps=None, error=None) -> Dict:
    return {
        "node_id": node.id,
        "node_name": node.name,
        "download_mbps": download_mbps,
        "error": error,
    }
