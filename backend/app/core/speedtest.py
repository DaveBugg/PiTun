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
from app.core.config_gen import _build_outbound, SPEED_PROBE_PORT
from app.core.healthcheck import HealthChecker
from app.database import get_async_engine
from app.models import Node, Settings as DBSettings

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

# Streaming speed test (live progress). Targets are tried in order until one
# streams data; Cloudflare heads the list because it is anycast and reachable
# from almost any exit (the single hardcoded ISP host was the whole reason the
# legacy test returned 0B when that host was blocked). `bytes=` is large enough
# that even a gigabit link won't exhaust it before the time box; on a slow link
# we simply stop at `_STREAM_SECS` having pulled a few MB.
_STREAM_TARGETS = [
    ("Cachefly", "https://cachefly.cachefly.net/100mb.test"),
    ("OVH", "https://proof.ovh.net/files/100Mb.dat"),
    ("Hetzner", "https://speed.hetzner.de/100MB.bin"),
    ("Tele2", "https://speedtest.tele2.net/100MB.zip"),
]
# Some CDNs (Cloudflare's __down among them) 403 a request with no/blank
# User-Agent. Present as a browser.
_STREAM_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_STREAM_SECS = 14.0           # time box: measure for at most this long
_STREAM_WARMUP = 2.0          # ignore the ramp-up (TCP slow-start / TLS) in the numbers

# Reachability gate: two independent, always-on 204 endpoints so one being
# blocked/down in a region doesn't false-negative a working node. ANY 204 =
# reachable; only when all fail (across the retry round) do we call it dead
# and skip the speed targets.
_REACH_TARGETS = [
    ("google", "https://www.google.com/generate_204"),
    ("cloudflare", "https://cp.cloudflare.com/generate_204"),
]
_REACH_TIMEOUT_S = 6.0


async def _is_active_node(node_id: int) -> bool:
    """Is `node_id` the currently active node? Never raises — a lookup hiccup
    just falls back to the temp-xray path."""
    try:
        async with AsyncSession(get_async_engine()) as s:
            row = (await s.exec(
                select(DBSettings).where(DBSettings.key == "active_node_id")
            )).first()
            return bool(row and row.value and int(row.value) == node_id)
    except (TypeError, ValueError):
        return False
    except Exception as exc:  # noqa: BLE001 — never block a speed test on this
        logger.debug("active-node lookup failed: %s", exc)
        return False


async def _live_socks_ready(node_id: int) -> bool:
    """True when `node_id` is active AND the main xray's speed-probe inbound is
    listening — i.e. we can measure it through the LIVE tunnel instead of a
    second temp xray (which, for WireGuard, would fight the live session)."""
    return await _is_active_node(node_id) and await _port_open("127.0.0.1", SPEED_PROBE_PORT)


async def _gate_and_measure(node: Node, socks_port: int) -> Dict:
    """Reachability gate + download measurement over an already-open SOCKS
    port — a throwaway xray OR the live speed-probe inbound. Shared so both
    paths measure identically."""
    reachable, latency_ms, detail = await _probe_reachable(socks_port)
    if not reachable:
        return _result(node, error=f"unreachable: {detail}",
                       reachable=False, latency_ms=latency_ms)
    last_error = "no targets reachable"
    for label, url in _STREAM_TARGETS:
        try:
            measured = await _measure_download(socks_port, label, url)
        except Exception as exc:  # noqa: BLE001 — try the next target
            last_error = f"{label}: {str(exc)[:120]}"
            continue
        if measured is not None:
            avg, mx = measured
            return _result(node, download_mbps=avg, max_mbps=mx,
                           reachable=True, latency_ms=latency_ms)
    return _result(node, error=last_error, reachable=True, latency_ms=latency_ms)


async def speedtest_node(node: Node) -> Dict:
    """Measure download speed through a freshly-spawned xray for `node`.

    The single, unified path shared by the manual button, "speed all" and
    the background auto-check sweep:

      1. Spin the throwaway xray.
      2. **Reachability gate** — two popular 204 endpoints (Google, CF).
         If the node can't reach the internet, return early instead of
         burning every 14s speed fallback for nothing.
      3. Measure the download exactly like the live streaming test:
         average after a warm-up + the peak steady window.

    Returns `_result(...)` with `download_mbps` (avg), `max_mbps` (peak),
    `reachable` and `latency_ms`.
    """
    proc: Optional[asyncio.subprocess.Process] = None
    tmp_path: Optional[str] = None
    try:
        # Active node: measure through the LIVE tunnel (the main xray's
        # speed-probe inbound → active outbound) instead of a throwaway xray.
        # A second instance re-opens the node's outbound; for WireGuard that's
        # a second session with the same peer key, which the server can't hold
        # twice — the temp test and the live tunnel fight, the reachability
        # gate flaps to "unreachable", and the live tunnel is briefly disrupted.
        if await _live_socks_ready(node.id):
            return await _gate_and_measure(node, SPEED_PROBE_PORT)

        # Resolve the full chain: [entry_parent, ..., node]. Only the entry
        # parent's address needs to be reachable directly; deeper hops tunnel.
        try:
            chain = await _resolve_chain(node)
        except Exception as exc:
            return _result(node, error=f"chain resolve: {exc}")

        entry = chain[0]
        # Naive outbounds point at the LOCAL sidecar, not the remote server —
        # skip the pre-resolve+rewrite dance other protocols need.
        entry_ip: Optional[str] = None
        if entry.protocol != "naive":
            try:
                entry_ip = await HealthChecker._resolve_direct(entry.address)
            except Exception as exc:
                return _result(node, error=f"dns entry: {exc}")

        # Fail fast if the naive sidecar isn't up (otherwise every connection
        # through the temp xray hangs to timeout).
        if node.protocol == "naive":
            if not node.internal_port:
                return _result(node, error="naive sidecar port not allocated")
            if not await _port_open("127.0.0.1", int(node.internal_port)):
                return _result(node, error=f"naive sidecar not listening on :{node.internal_port}")

        socks_port, proc, tmp_path, start_err = await _start_temp_xray(node, chain, entry_ip)
        if proc is None or start_err:
            return _result(node, error=start_err or "Failed to start temp xray")

        return await _gate_and_measure(node, socks_port)

    except Exception as exc:
        logger.warning("Speedtest node %d error: %s", node.id, exc)
        return _result(node, error=str(exc))
    finally:
        await _cleanup(proc, tmp_path)


async def speedtest_stream(node: Node):
    """Async generator yielding live speed-test progress events.

    Same throwaway-xray setup as `speedtest_node`, but instead of a single
    curl at the end it streams the download itself (httpx over the node's
    SOCKS) and emits a progress event ~twice a second with the host and the
    current Mbps, so the UI shows "via Cloudflare · 45.2 Mbps" ticking up
    rather than a blank spinner. Time-boxed to `_STREAM_SECS`.

    Event shapes (dicts): {phase:"start"}, {phase:"connecting",host},
    {phase:"progress",host,mbps,elapsed,bytes}, {phase:"done",host,mbps,...},
    {phase:"error",error}. On a target failure it emits {phase:"target_failed"}
    and moves to the next target.
    """
    proc: Optional[asyncio.subprocess.Process] = None
    tmp_path: Optional[str] = None
    try:
        yield {"phase": "start", "node_id": node.id}

        # Active node → measure through the LIVE tunnel (speed-probe inbound),
        # never a second temp xray (WireGuard single-session contention — see
        # speedtest_node). Otherwise spin the usual throwaway instance.
        if await _live_socks_ready(node.id):
            socks_port = SPEED_PROBE_PORT
        else:
            try:
                chain = await _resolve_chain(node)
            except Exception as exc:  # noqa: BLE001
                yield {"phase": "error", "error": f"chain resolve: {exc}"}
                return

            entry = chain[0]
            entry_ip: Optional[str] = None
            if entry.protocol != "naive":
                try:
                    entry_ip = await HealthChecker._resolve_direct(entry.address)
                except Exception as exc:  # noqa: BLE001
                    yield {"phase": "error", "error": f"dns entry: {exc}"}
                    return
            if node.protocol == "naive":
                if not node.internal_port:
                    yield {"phase": "error", "error": "naive sidecar port not allocated"}
                    return
                if not await _port_open("127.0.0.1", int(node.internal_port)):
                    yield {"phase": "error", "error": f"naive sidecar not on :{node.internal_port}"}
                    return

            yield {"phase": "connecting", "host": "starting xray"}
            socks_port, proc, tmp_path, start_err = await _start_temp_xray(node, chain, entry_ip)
            if proc is None or start_err:
                yield {"phase": "error", "error": start_err or "failed to start temp xray"}
                return

        # Reachability gate first — if the node can't reach the internet, say
        # so and stop instead of grinding every speed fallback to timeout.
        yield {"phase": "connecting", "host": "checking reachability"}
        reachable, _lat, detail = await _probe_reachable(socks_port)
        if not reachable:
            yield {"phase": "error", "error": f"unreachable: {detail}"}
            return

        last_error = "no targets reachable"
        for label, url in _STREAM_TARGETS:
            yield {"phase": "connecting", "host": label}
            got_data = False
            try:
                async for ev in _stream_download(socks_port, label, url):
                    if ev["phase"] in ("progress", "done"):
                        got_data = True
                    yield ev
                if got_data:
                    return  # first working target wins
            except Exception as exc:  # noqa: BLE001
                last_error = f"{label}: {str(exc)[:120]}"
                yield {"phase": "target_failed", "host": label, "error": last_error}
                continue
        yield {"phase": "error", "error": last_error}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Speedtest stream node %d error: %s", node.id, exc)
        yield {"phase": "error", "error": str(exc)}
    finally:
        await _cleanup(proc, tmp_path)


async def reachability_check(node: Node) -> Dict:
    """Quick "does the internet work through this node" probe.

    Spins the same throwaway xray, then fetches a tiny always-on endpoint
    (Google's generate_204) through it — a real proxied HTTP round trip,
    unlike the health check's bare TCP connect to the server. Returns
    {ok, latency_ms, detail}.
    """
    proc: Optional[asyncio.subprocess.Process] = None
    tmp_path: Optional[str] = None
    try:
        try:
            chain = await _resolve_chain(node)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "latency_ms": None, "detail": f"chain: {exc}"}
        entry = chain[0]
        entry_ip: Optional[str] = None
        if entry.protocol != "naive":
            try:
                entry_ip = await HealthChecker._resolve_direct(entry.address)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "latency_ms": None, "detail": f"dns: {exc}"}
        if node.protocol == "naive" and (
            not node.internal_port
            or not await _port_open("127.0.0.1", int(node.internal_port))
        ):
            return {"ok": False, "latency_ms": None, "detail": "naive sidecar down"}

        socks_port, proc, tmp_path, start_err = await _start_temp_xray(node, chain, entry_ip)
        if proc is None or start_err:
            return {"ok": False, "latency_ms": None, "detail": start_err or "xray failed"}

        # Same two-endpoint gate the speed test uses.
        ok, latency, detail = await _probe_reachable(socks_port)
        return {"ok": ok, "latency_ms": latency, "detail": detail}
    finally:
        await _cleanup(proc, tmp_path)


async def sni_scan(domain: str, node: Optional[Node] = None) -> Dict:
    """Is `domain` a viable REALITY dest? Checks TLS 1.3 + ALPN h2 + that it
    actually answers. Routed THROUGH the active node when one is given (so the
    verdict reflects what the exit sees, not the box's own network), else
    probed directly. Returns {ok, tls13, http2, status, via, detail}."""
    import re

    import ipaddress

    domain = (domain or "").strip()
    for pfx in ("https://", "http://"):
        if domain.lower().startswith(pfx):
            domain = domain[len(pfx):]
    domain = domain.split("/", 1)[0].split(":", 1)[0].strip()
    if not domain:
        return {"ok": False, "detail": "empty domain", "via": "direct"}

    # A bare IP (or any host whose cert won't validate) still needs probing
    # so we can read the cert it presents — that's the "scan the IP and see
    # what turns up" case. `-k` below lets the handshake finish on a mismatch
    # so curl still prints the server-certificate block.
    try:
        ipaddress.ip_address(domain)
        is_ip = True
    except ValueError:
        is_ip = False

    proc: Optional[asyncio.subprocess.Process] = None
    tmp_path: Optional[str] = None
    socks_port: Optional[int] = None
    via = "direct"
    try:
        if node is not None:
            try:
                chain = await _resolve_chain(node)
            except Exception as exc:  # noqa: BLE001
                return {"ok": False, "detail": f"chain: {exc}", "via": via}
            entry = chain[0]
            entry_ip: Optional[str] = None
            if entry.protocol != "naive":
                try:
                    entry_ip = await HealthChecker._resolve_direct(entry.address)
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "detail": f"dns: {exc}", "via": via}
            socks_port, proc, tmp_path, start_err = await _start_temp_xray(
                node, chain, entry_ip,
            )
            if proc is None or start_err:
                return {"ok": False, "detail": start_err or "xray failed", "via": via}
            via = "active node"

        cmd = ["curl", "-sS", "-v", "-o", "/dev/null", "--max-time", "10",
               "--http2", "-A", _STREAM_UA]
        if is_ip:
            cmd.append("-k")
        if socks_port:
            cmd += ["-x", f"socks5h://127.0.0.1:{socks_port}"]
        cmd.append(f"https://{domain}")
        p = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(p.communicate(), timeout=14)
        txt = stderr.decode(errors="replace")

        tls13 = "TLSv1.3" in txt
        # curl prints e.g. "* ALPN: server accepted h2" (or "...to use h2").
        m_alpn = re.search(r"ALPN[^\n]*accepted[^\n]*\bh2\b", txt)
        http2 = bool(m_alpn) or "using HTTP/2" in txt
        m_status = re.search(r"< HTTP/[\d.]+ (\d{3})", txt)
        status = int(m_status.group(1)) if m_status else None
        reachable = status is not None

        # Pull the certificate the endpoint actually presents, so scanning a
        # bare IP still surfaces the domain(s) behind it (what the operator
        # can use as the REALITY serverName). curl prints "subject: CN=…" and,
        # on a name match, the cert's own name on the subjectAltName line.
        m_subj = re.search(r"subject:\s*([^\r\n]+)", txt)
        cert_subject = m_subj.group(1).strip() if m_subj else None
        m_san = re.search(r"subjectAltName:[^\r\n]*?cert's \"([^\"]+)\"", txt)
        cert_name = m_san.group(1) if m_san else None

        ok = bool(tls13 and http2 and reachable and status < 400)
        if ok:
            detail = "good REALITY dest"
        else:
            reasons = []
            if not reachable:
                reasons.append("no response")
            else:
                if not tls13:
                    reasons.append("no TLS 1.3")
                if not http2:
                    reasons.append("no HTTP/2 (ALPN h2)")
                if status and status >= 400:
                    reasons.append(f"HTTP {status}")
            detail = ", ".join(reasons) or "unsuitable"
        return {
            "ok": ok, "tls13": tls13, "http2": http2,
            "status": status, "via": via, "detail": detail,
            "cert_subject": cert_subject, "cert_name": cert_name,
        }
    except asyncio.TimeoutError:
        return {"ok": False, "detail": "timed out", "via": via}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)[:120], "via": via}
    finally:
        await _cleanup(proc, tmp_path)


async def _probe_reachable(socks_port: int, rounds: int = 2) -> Tuple[bool, Optional[int], str]:
    """Is the internet actually reachable through this node?

    Tries the popular 204 endpoints (Google, Cloudflare) in order; the first
    one that answers 204/200 wins → reachable. If all fail, retry the whole
    set once more (`rounds`). Returns (ok, latency_ms, detail). This is the
    cheap gate run BEFORE the speed targets, so a dead node fails in ~1s
    instead of grinding every 14s speed fallback for nothing."""
    import httpx

    loop = asyncio.get_event_loop()
    proxy = f"socks5://127.0.0.1:{socks_port}"
    detail = "no response"
    for r in range(max(1, rounds)):
        for label, url in _REACH_TARGETS:
            t0 = loop.time()
            try:
                async with httpx.AsyncClient(
                    proxy=proxy,
                    timeout=httpx.Timeout(_REACH_TIMEOUT_S, connect=_REACH_TIMEOUT_S),
                    headers={"User-Agent": _STREAM_UA},
                ) as client:
                    resp = await client.get(url)
                if resp.status_code in (204, 200):
                    return True, int((loop.time() - t0) * 1000), label
                detail = f"{label}: HTTP {resp.status_code}"
            except Exception as exc:  # noqa: BLE001 — any transport error = not reachable
                detail = f"{label}: {str(exc)[:80]}"
        if r < max(1, rounds) - 1:
            await asyncio.sleep(0.4)
    return False, None, detail


async def _measure_download(socks_port: int, label: str, url: str) -> Optional[Tuple[float, float]]:
    """Drain the streaming download to its final numbers for the non-streaming
    callers. Returns (avg_mbps, max_mbps) or None if the target sent no data.
    Reuses `_stream_download` so the manual, background and live paths all
    compute speed the exact same way (avg after warm-up + peak steady window)."""
    avg: Optional[float] = None
    mx: Optional[float] = None
    async for ev in _stream_download(socks_port, label, url):
        if ev["phase"] == "done":
            avg = ev.get("mbps")
            mx = ev.get("mbps_max")
    if avg is None:
        return None
    return avg, (mx if mx is not None else avg)


async def _stream_download(socks_port: int, label: str, url: str):
    """Stream `url` through the SOCKS proxy, yielding live-speed events.

    Speed is measured over a rolling half-second window for the live number;
    the final figure is total bytes over the measured window with a short
    warm-up discarded (TCP slow-start + TLS would otherwise drag it down).
    """
    import httpx

    loop = asyncio.get_event_loop()
    proxy = f"socks5://127.0.0.1:{socks_port}"
    started = loop.time()
    total = 0
    warmup_bytes = 0
    win_bytes = 0
    win_start = started
    last_emit = started
    peak_mbps = 0.0  # highest sustained (post-warmup) window seen

    timeout = httpx.Timeout(_STREAM_SECS + 5, connect=8.0)
    async with httpx.AsyncClient(
        proxy=proxy, timeout=timeout, verify=False,
        headers={"User-Agent": _STREAM_UA},
    ) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes(65536):
                now = loop.time()
                n = len(chunk)
                total += n
                win_bytes += n
                if now - started < _STREAM_WARMUP:
                    warmup_bytes += n
                if now - last_emit >= 0.5:
                    win = now - win_start
                    mbps = (win_bytes * 8 / win / 1e6) if win > 0 else 0.0
                    # Peak ignores the warm-up windows so a spurious ramp
                    # blip doesn't win — it's the best STEADY window.
                    if now - started >= _STREAM_WARMUP:
                        peak_mbps = max(peak_mbps, mbps)
                    yield {
                        "phase": "progress", "host": label,
                        "mbps": round(mbps, 1),
                        "elapsed": round(now - started, 1),
                        "bytes": total,
                    }
                    last_emit = now
                    win_bytes = 0
                    win_start = now
                if now - started >= _STREAM_SECS:
                    break

    elapsed = loop.time() - started
    measured = max(elapsed - _STREAM_WARMUP, 0.001)
    if total < _MIN_BYTES:
        raise RuntimeError(f"too little data ({total}B)")
    # Average over the post-warmup window; peak is the best steady window.
    avg = (total - warmup_bytes) * 8 / measured / 1e6
    yield {
        "phase": "done", "host": label,
        "mbps": round(avg, 1),
        "mbps_max": round(max(peak_mbps, avg), 1),
        "elapsed": round(elapsed, 1),
        "bytes": total,
    }


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
) -> Tuple[int, Optional[asyncio.subprocess.Process], Optional[str], Optional[str]]:
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
    socks_port = _reserve_local_port()

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
        return 0, None, None, f"chain build error: {exc}"

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
            # Surface the real reason (last segment of xray's error chain) to
            # the UI instead of a generic "failed to start" — e.g. an invalid
            # reality shortId or empty publicKey is otherwise invisible.
            reason = err.strip().rsplit(">", 1)[-1].strip() or "xray exited early"
            return 0, None, None, f"xray: {reason}"
        if await _port_open("127.0.0.1", socks_port):
            return socks_port, proc, tmp_path, None
        await asyncio.sleep(0.15)

    # Timed out waiting for port — process is alive but unresponsive.
    # This MUST stay a 4-tuple: returning three items made the caller's
    # unpack raise before `proc`/`tmp_path` were bound, so the `finally`
    # cleanup got None/None and the xray process leaked forever, holding
    # a temp config that contains the node's credentials.
    logger.warning("Temp xray for node %d: SOCKS port %d never opened", node.id, socks_port)
    return socks_port, proc, tmp_path, "xray: SOCKS port never opened"


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


def _reserve_local_port() -> int:
    """Ask the OS for a free loopback port instead of guessing one.

    The old `random.randint(19000, 19999)` could collide with a concurrent
    speedtest (two nodes tested back to back) or with a leaked temp xray.
    On collision our own xray died on bind while `_port_open` cheerfully
    saw the OTHER instance listening — so the measurement ran through a
    different node's tunnel and was attributed to this one.

    Falls back to the legacy random range if the bind probe fails.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
    except OSError:
        return random.randint(19000, 19999)


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


def _result(node: Node, download_mbps=None, error=None,
            max_mbps=None, reachable=None, latency_ms=None) -> Dict:
    return {
        "node_id": node.id,
        "node_name": node.name,
        "download_mbps": download_mbps,   # average after warm-up
        "max_mbps": max_mbps,             # best steady window (peak)
        "reachable": reachable,           # generate_204 succeeded through the node
        "latency_ms": latency_ms,         # reachability round-trip
        "error": error,
    }
