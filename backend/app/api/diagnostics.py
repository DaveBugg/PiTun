"""System diagnostics API — network, health checks, resources, docker logs."""
import asyncio
import logging
import re
import shlex
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query

from app.database import get_session
from app.schemas import RouteExplainRequest

router = APIRouter(prefix="/diagnostics", tags=["diagnostics"])
logger = logging.getLogger(__name__)


async def _run(cmd: str, timeout: float = 10) -> str:
    """Run a shell command and return stdout (empty on error)."""
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode(errors="replace").strip()
    except Exception as exc:
        logger.debug("Command failed: %s — %s", cmd, exc)
        return ""


# ── Health checks ────────────────────────────────────────────────────────────

async def _check_gateway() -> Dict[str, Any]:
    """Check if default gateway is reachable via ARP (not ICMP — kill switch blocks ping)."""
    gw_line = await _run("ip route show default")
    gw_match = re.search(r"default via (\S+) dev (\S+)", gw_line)
    if not gw_match:
        return {"name": "gateway", "ok": False, "detail": "No default gateway found"}
    gw = gw_match.group(1)
    dev = gw_match.group(2)
    q_gw, q_dev = shlex.quote(gw), shlex.quote(dev)

    # Try arping first (works even with kill switch — ARP is L2, not blocked by nftables)
    result = await _run(f"arping -c1 -w2 -I {q_dev} {q_gw} 2>&1")
    if "1 received" in result or "1 packets received" in result or "bytes from" in result:
        return {"name": "gateway", "ok": True, "detail": f"{gw} — reachable"}

    # Fallback: check ARP table — if gateway has a MAC, it was recently reachable
    arp_result = await _run(f"ip neigh show {q_gw}")
    if arp_result and ("REACHABLE" in arp_result or "STALE" in arp_result or "DELAY" in arp_result):
        return {"name": "gateway", "ok": True, "detail": f"{gw} — reachable (ARP)"}

    # Last fallback: ping (might fail with kill switch)
    result = await _run(f"ping -c1 -W2 {q_gw}")
    ok = "1 received" in result or "1 packets received" in result
    return {"name": "gateway", "ok": ok, "detail": f"{gw} — {'reachable' if ok else 'unreachable (kill switch may block ICMP)'}"}


async def _check_dns() -> Dict[str, Any]:
    """Check if DNS resolves."""
    # Try multiple methods
    result = await _run("getent hosts google.com 2>&1", timeout=5)
    if result and "google" in result.lower():
        return {"name": "dns", "ok": True, "detail": "resolving"}

    result = await _run("nslookup google.com 2>&1", timeout=5)
    ok = "Address" in result and "SERVFAIL" not in result and "NXDOMAIN" not in result
    detail = "resolving" if ok else "DNS resolution failed"
    return {"name": "dns", "ok": ok, "detail": detail}


async def _check_dns_udp() -> Dict[str, Any]:
    """Check if DNS over UDP works (raw socket test to 8.8.8.8:53)."""
    import socket
    import struct

    def _try_udp_dns() -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(3)
            # Minimal DNS query for google.com A record
            query = (
                b"\x12\x34"       # ID
                b"\x01\x00"       # Flags: standard query
                b"\x00\x01"       # Questions: 1
                b"\x00\x00\x00\x00\x00\x00"  # Answers/Auth/Additional: 0
                b"\x06google\x03com\x00"      # google.com
                b"\x00\x01"       # Type: A
                b"\x00\x01"       # Class: IN
            )
            sock.sendto(query, ("8.8.8.8", 53))
            resp = sock.recv(512)
            sock.close()
            return len(resp) > 12
        except Exception:
            return False

    # Run in thread to avoid blocking event loop
    loop = asyncio.get_event_loop()
    ok = await loop.run_in_executor(None, _try_udp_dns)

    if ok:
        return {"name": "dns_udp", "ok": True, "detail": "UDP:53 reachable"}
    else:
        return {
            "name": "dns_udp",
            "ok": False,
            "detail": "UDP:53 blocked — enable DNS over TCP",
        }


async def _http_probe_direct(host_ip: str, host_header: str, port: int = 80, timeout: float = 5.0) -> int | None:
    """Open a raw TCP connection to `host_ip:port`, send a minimal
    HTTP/1.0 request, and return the response status code.

    Critically, the socket is opened with `SO_MARK=0xff` BEFORE
    connecting. PiTun's nftables `output` chain has a `meta mark
    0xff return` rule at the top (originally there so xray's own
    outbound dials don't loop back through TPROXY), which means
    packets with that mark bypass the TPROXY-to-xray hop and flow
    out via the host's normal default route. So this probe tests
    the host's underlying internet access *independently of whether
    xray's tunnel works*.

    Uses an IP literal so no DNS lookup is required — the DNS path
    has its own check, and a degraded DNS shouldn't masquerade as
    "no internet". Returns the HTTP status code, or `None` on any
    connect/send/parse failure.
    """
    import socket
    loop = asyncio.get_event_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        try:
            # SO_MARK = 36 on Linux. Requires CAP_NET_ADMIN — the
            # backend container is privileged so this works in prod;
            # tests fall through to a plain socket (no mark applied)
            # which is fine since they don't have the nftables rules.
            sock.setsockopt(socket.SOL_SOCKET, 36, 0xff)
        except (OSError, PermissionError):
            pass
        sock.setblocking(False)
        await asyncio.wait_for(loop.sock_connect(sock, (host_ip, port)), timeout=timeout)
        req = (
            f"GET / HTTP/1.0\r\nHost: {host_header}\r\n"
            f"User-Agent: pitun-diag/1.0\r\nConnection: close\r\n\r\n"
        ).encode()
        await asyncio.wait_for(loop.sock_sendall(sock, req), timeout=timeout)
        data = await asyncio.wait_for(loop.sock_recv(sock, 4096), timeout=timeout)
        first_line = data.split(b"\r\n", 1)[0]
        parts = first_line.split(b" ", 2)
        if len(parts) >= 2 and parts[1].isdigit():
            return int(parts[1])
    except Exception:
        return None
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return None


async def _check_internet_direct() -> Dict[str, Any]:
    """Internet check that BYPASSES xray/TPROXY (via SO_MARK=0xff).

    Reports whether the host has a working underlying internet
    connection regardless of VPN state. When this is green but
    `internet_via_vpn` is red, the user knows the box is fine but
    the VPN tunnel is broken (e.g. expired subscription, dead
    node) — exactly the situation we hit on 1.4.
    """
    # Cloudflare 1.1.1.1 on port 80 returns 301 redirect to https.
    # Any HTTP status response means we reached the server.
    code = await _http_probe_direct("1.1.1.1", "1.1.1.1", port=80, timeout=5.0)
    if code is None:
        # Fallback: Google DNS doubles as an HTTP server (403 on /).
        code = await _http_probe_direct("8.8.8.8", "8.8.8.8", port=80, timeout=5.0)
    ok = code is not None and 100 <= code < 600
    return {
        "name": "internet_direct",
        "ok": ok,
        "detail": f"HTTP {code} from 1.1.1.1" if ok else "host has no internet",
    }


async def _check_internet_via_vpn() -> Dict[str, Any]:
    """Internet check that goes THROUGH the xray/TPROXY pipe.

    This is the legacy `_check_internet` curl probe — packets get
    TPROXY-marked, hit xray on :7893, get routed via the user's
    rules / active node. If the VPN is healthy it succeeds; if the
    active node has a TLS handshake issue (Reality drift, expired
    creds, etc.) it times out or gets EOF mid-handshake.

    Uses `--resolve` to pin a literal IP for `cp.cloudflare.com`,
    so a degraded DNS upstream doesn't masquerade as "no internet".
    Tries the curl-default DNS path first; on failure retries with
    the explicit resolve.
    """
    result = await _run(
        "curl -sf --max-time 5 -o /dev/null -w '%{http_code}' "
        "https://www.google.com/generate_204"
    )
    ok = result in ("200", "204")
    if not ok:
        # Pin the IP so a broken xray-DNS (everything routed to a dead
        # VPN node) can't manifest as a false "no internet" — the host
        # may still have CDN reachability.
        result = await _run(
            "curl -sf --max-time 5 -o /dev/null -w '%{http_code}' "
            "--resolve cp.cloudflare.com:80:104.16.132.229 "
            "http://cp.cloudflare.com"
        )
        ok = result in ("200", "204")
    return {
        "name": "internet_via_vpn",
        "ok": ok,
        "detail": "reached via tunnel" if ok else "no internet through VPN",
    }


async def _check_xray() -> Dict[str, Any]:
    """Check if xray process is running — use xray_manager directly."""
    from app.core.xray import xray_manager
    if xray_manager.is_running:
        pid = xray_manager.pid
        return {"name": "xray", "ok": True, "detail": f"PID {pid}"}

    # Fallback: try pgrep with relaxed match
    result = await _run("pgrep -f 'xray run' 2>/dev/null || pgrep -x xray 2>/dev/null")
    if result.strip():
        return {"name": "xray", "ok": True, "detail": f"PID {result.strip().splitlines()[0]}"}

    return {"name": "xray", "ok": False, "detail": "not running"}


async def _check_nftables() -> Dict[str, Any]:
    """Check if nftables rules are loaded."""
    result = await _run("nft list tables 2>/dev/null")
    has_pitun = "pitun" in result.lower() or "tproxy" in result.lower()
    return {"name": "nftables", "ok": has_pitun, "detail": "rules loaded" if has_pitun else "no PiTun rules"}


async def _check_tun() -> Dict[str, Any]:
    """Check TUN interface — context-aware (TUN vs TPROXY mode)."""
    # Determine inbound mode from settings
    mode = "tproxy"  # default
    try:
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.database import get_async_engine
        from app.models import Settings as DBSettings

        async with AsyncSession(get_async_engine()) as session:
            row = (await session.exec(
                select(DBSettings).where(DBSettings.key == "inbound_mode")
            )).first()
            if row:
                mode = row.value
    except Exception:
        pass

    result = await _run("ip link show tun0 2>/dev/null")
    has_tun = "tun0" in result
    if not has_tun:
        result = await _run("ip link show utun 2>/dev/null")
        has_tun = "utun" in result

    if mode == "tproxy":
        # TUN not needed in TPROXY mode
        return {
            "name": "tun",
            "ok": True,
            "detail": "TPROXY mode — TUN not needed",
            "info": True,  # frontend can style this differently
        }
    elif mode == "tun":
        return {"name": "tun", "ok": has_tun, "detail": "interface up" if has_tun else "TUN interface missing!"}
    else:
        # "both" mode
        return {"name": "tun", "ok": has_tun, "detail": "interface up" if has_tun else "TUN interface not active"}


# ── Network info ─────────────────────────────────────────────────────────────

async def _get_interfaces() -> List[Dict[str, Any]]:
    """Get network interfaces with IPs."""
    raw = await _run("ip -j addr show 2>/dev/null")
    if raw:
        import json
        try:
            ifaces = json.loads(raw)
            result = []
            for iface in ifaces:
                name = iface.get("ifname", "")
                if name == "lo":
                    continue
                state = iface.get("operstate", "UNKNOWN")
                addrs = []
                for ai in iface.get("addr_info", []):
                    addrs.append(f"{ai.get('local', '')}/{ai.get('prefixlen', '')}")
                result.append({"name": name, "state": state, "addresses": addrs})
            return result
        except Exception:
            pass
    # Fallback: parse text output
    raw = await _run("ip -br addr show")
    result = []
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) < 2 or parts[0] == "lo":
            continue
        result.append({"name": parts[0], "state": parts[1], "addresses": parts[2:]})
    return result


async def _get_gateway_info() -> Dict[str, Any]:
    """Analyze gateway and subnet."""
    raw = await _run("ip route show default")
    gw_match = re.search(r"default via (\S+) dev (\S+)", raw)
    if not gw_match:
        return {"gateway": None, "device": None, "subnet": None, "recommendation": None}

    gw = gw_match.group(1)
    dev = gw_match.group(2)

    # Get device IP and subnet
    addr_raw = await _run(f"ip -4 addr show {shlex.quote(dev)}")
    ip_match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", addr_raw)
    my_ip = ip_match.group(1) if ip_match else ""
    prefix = ip_match.group(2) if ip_match else ""

    # Build subnet string
    subnet = f"{my_ip}/{prefix}" if my_ip else ""

    # Recommendation
    rec = None
    octets = gw.split(".")
    if len(octets) == 4:
        net = ".".join(octets[:3])
        if octets[3] == "1":
            rec = f"Шлюз {gw} — стандартная конфигурация. PiTun рекомендуется ставить как {net}.2"
        else:
            rec = f"Шлюз {gw} — нестандартный адрес. Убедитесь, что устройства в сети используют его"

    return {
        "gateway": gw,
        "device": dev,
        "my_ip": my_ip,
        "subnet": subnet,
        "recommendation": rec,
    }


async def _get_routes() -> List[Dict[str, str]]:
    """Get routing table (main entries)."""
    raw = await _run("ip route show")
    routes = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        routes.append({"route": line})
    return routes[:30]  # Limit


async def _get_listeners() -> List[Dict[str, str]]:
    """Get listening TCP/UDP ports."""
    raw = await _run("ss -tlnp 2>/dev/null")
    listeners = []
    for line in raw.splitlines()[1:]:  # Skip header
        parts = line.split()
        if len(parts) >= 5:
            listeners.append({
                "proto": parts[0],
                "listen": parts[3],
                "process": parts[5] if len(parts) > 5 else "",
            })

    raw_udp = await _run("ss -ulnp 2>/dev/null")
    for line in raw_udp.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 5:
            listeners.append({
                "proto": parts[0],
                "listen": parts[3],
                "process": parts[5] if len(parts) > 5 else "",
            })
    return listeners


# ── System resources ─────────────────────────────────────────────────────────

async def _get_resources() -> Dict[str, Any]:
    """CPU, RAM, disk, temperature, uptime — uses psutil for reliability."""
    import psutil

    # CPU
    load_avg = [f"{x:.2f}" for x in psutil.getloadavg()]
    cpu_count = psutil.cpu_count() or 1

    # Memory
    vm = psutil.virtual_memory()
    mem = {
        "total_mb": vm.total // (1024 * 1024),
        "used_mb": vm.used // (1024 * 1024),
        "available_mb": vm.available // (1024 * 1024),
    }

    # Disk
    du = psutil.disk_usage("/")
    def _human(b: int) -> str:
        for u in ("B", "K", "M", "G", "T"):
            if b < 1024:
                return f"{b:.1f}{u}"
            b /= 1024
        return f"{b:.1f}P"

    disk = {
        "total": _human(du.total),
        "used": _human(du.used),
        "available": _human(du.free),
        "use_percent": f"{du.percent}%",
    }

    # Temperature (RPi thermal zone)
    temp = None
    temps = psutil.sensors_temperatures() if hasattr(psutil, "sensors_temperatures") else {}
    if temps:
        # RPi usually reports under 'cpu_thermal' or 'thermal_zone0'
        for key in ("cpu_thermal", "cpu-thermal", "thermal_zone0"):
            if key in temps and temps[key]:
                temp = round(temps[key][0].current, 1)
                break
    if temp is None:
        # Fallback: read sysfs
        temp_raw = await _run("cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null")
        if temp_raw:
            try:
                temp = round(int(temp_raw) / 1000, 1)
            except ValueError:
                pass

    # Uptime
    import time
    boot = psutil.boot_time()
    secs = int(time.time() - boot)
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{mins}m")
    uptime_raw = "up " + " ".join(parts)

    return {
        "load_avg": load_avg,
        "cpu_count": cpu_count,
        "memory": mem,
        "disk": disk,
        "temperature": temp,
        "uptime": uptime_raw,
    }


# ── Docker logs ──────────────────────────────────────────────────────────────

def _get_container_logs(lines: int = 100, filter_level: str = "") -> List[str]:
    """Get backend logs from the in-memory ring buffer."""
    from app.core.log_buffer import get_lines
    return get_lines(n=lines, level_filter=filter_level)


# ── API Endpoints ────────────────────────────────────────────────────────────

@router.get("/health-checks")
async def health_checks():
    """Run all health checks in parallel."""
    checks = await asyncio.gather(
        _check_gateway(),
        _check_dns(),
        _check_dns_udp(),
        _check_internet_direct(),
        _check_internet_via_vpn(),
        _check_xray(),
        _check_nftables(),
        _check_tun(),
    )
    return {"checks": checks}


@router.get("/network")
async def network_info():
    """Full network diagnostics."""
    interfaces, gateway, routes, listeners = await asyncio.gather(
        _get_interfaces(),
        _get_gateway_info(),
        _get_routes(),
        _get_listeners(),
    )
    return {
        "interfaces": interfaces,
        "gateway": gateway,
        "routes": routes,
        "listeners": listeners,
    }


@router.get("/resources")
async def system_resources():
    """System resource usage."""
    return await _get_resources()


@router.get("/logs")
async def docker_logs(
    lines: int = Query(100, ge=10, le=1000),
    level: str = Query("", description="Filter by log level: ERROR, WARNING, INFO"),
):
    """Get backend logs."""
    log_lines = _get_container_logs(lines=lines, filter_level=level)
    return {"lines": log_lines}


# ── Route Explainer ───────────────────────────────────────────────────────────

def _extract_host(raw: str) -> str:
    """Reduce whatever the user typed to a bare host (domain or IP).

    Operators routinely paste a full URL (`https://cloudconvert.com/md-to-pdf`)
    into the target box; resolving that whole string as a domain yields a
    confusing NXDOMAIN. Strip scheme, userinfo, path/query/fragment, and a
    trailing :port — while leaving bare IPv6 (multiple colons) and bare
    hostnames untouched. The port comes from the dedicated port field.
    """
    s = (raw or "").strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    if "@" in s:                       # strip user:pass@
        s = s.rsplit("@", 1)[1]
    for sep in ("/", "?", "#"):        # strip path / query / fragment
        if sep in s:
            s = s.split(sep, 1)[0]
    s = s.strip()
    if s.startswith("[") and "]" in s:           # [IPv6]:port → IPv6
        s = s[1:s.index("]")]
    elif s.count(":") == 1:                       # host:port (not IPv6) → host
        s = s.split(":", 1)[0]
    return s.strip().rstrip(".")


@router.post("/explain")
async def explain_route(body: RouteExplainRequest, session=Depends(get_session)):
    """Explain where traffic to a target would go: which DNS rule + server
    resolves it, which routing rule matches + the resulting outbound, and
    (optionally) whether it actually connects.

    Layer A (python matcher) is always run. When it can't decide because a
    geosite/geoip rule blocks certainty AND `verify_routing` is set, layer
    B (live xray probe) supplies the ground-truth outbound.
    """
    import ipaddress
    import time as _time
    from sqlmodel import select
    from app.models import (
        RoutingRule, DNSRule, Node, Device, RoutingSet,
        Settings as DBSettings,
    )
    from app.core import route_explain as rex
    from app.schemas import (
        RouteExplainResult, RouteExplainDns, RouteExplainRouting,
        RouteExplainReachability,
    )

    target = _extract_host(body.target)
    port = body.port
    protocol = (body.protocol or "tcp").lower()

    # is the target a literal IP?
    is_ip = False
    try:
        ipaddress.ip_address(target)
        is_ip = True
    except ValueError:
        is_ip = False

    # Load DB state
    settings_map = {r.key: r.value for r in (await session.exec(select(DBSettings))).all()}
    routing_rules = list((await session.exec(select(RoutingRule))).all())
    dns_rules = list((await session.exec(select(DNSRule))).all())
    nodes = list((await session.exec(select(Node))).all())
    node_labels = {n.id: n.name for n in nodes}
    mode = settings_map.get("mode", "rules")
    bypass_private = settings_map.get("bypass_private", "true").lower() == "true"
    active_node_id = None
    aid = settings_map.get("active_node_id", "")
    if aid:
        try:
            active_node_id = int(aid)
        except ValueError:
            active_node_id = None

    # Optional per-device (routing set) context
    set_context = None
    if body.from_mac:
        mac = body.from_mac.strip().lower()
        dev = (await session.exec(
            select(Device).where(Device.mac == mac)
        )).first()
        if dev and dev.routing_set_id:
            rs = await session.get(RoutingSet, dev.routing_set_id)
            if rs:
                member_ids = [
                    r.id for r in routing_rules
                    if getattr(r, "routing_set_id", None) == rs.id
                ]
                set_context = (rs, member_ids)

    # ── DNS stage ──
    dns_x = rex.explain_dns(
        target, is_ip=is_ip, dns_rules=dns_rules, settings_map=settings_map,
    )
    resolved_ips: list[str] = []
    resolve_error = None
    if not is_ip:
        try:
            from app.api.dns import _resolve_plain, _resolve_doh
            srv = dns_x.server or settings_map.get("dns_upstream", "8.8.8.8")
            if (dns_x.server_type or "").lower() == "doh" or str(srv).startswith("http"):
                doh = srv if str(srv).startswith("http") else f"https://{srv}/dns-query"
                ips, _lat = await _resolve_doh(target, doh)
            else:
                # dot maps to plaintext tcp/53 in PiTun — _resolve_plain
                # uses UDP/53 which returns the same records for an
                # explain (we only need the address, not the transport).
                ips, _lat = await _resolve_plain(target, str(srv) if srv else None)
            resolved_ips = ips
        except Exception as exc:  # noqa: BLE001
            resolve_error = str(exc)
    primary_ip = (target if is_ip else (resolved_ips[0] if resolved_ips else None))

    # ── Routing stage (A: python) ──
    route_x = rex.explain_routing(
        target=target, is_ip=is_ip, resolved_ip=primary_ip,
        port=port, protocol=protocol, mode=mode,
        bypass_private=bypass_private, rules=routing_rules,
        active_node_id=active_node_id, node_labels=node_labels,
        set_context=set_context,
    )
    method = "python_matcher"
    probe_detail = None

    # ── Routing stage (B: xray probe) when uncertain + requested ──
    if body.verify_routing and not route_x.certain and not is_ip:
        try:
            from app.core.config_gen import generate_config, collect_routing_set_context
            from app.core.route_explain_probe import xray_probe_routing
            from app.models import BalancerGroup
            active_node = await session.get(Node, active_node_id) if active_node_id else None
            all_nodes = [n for n in nodes if n.enabled]
            balancers = list((await session.exec(select(BalancerGroup))).all())
            rsets, dmap = await collect_routing_set_context(session)
            cfg = generate_config(
                active_node, all_nodes,
                [r for r in routing_rules if r.enabled],
                mode, settings_map, dns_rules, balancers,
                routing_sets=rsets, device_set_macs=dmap,
            )
            probe = await xray_probe_routing(
                base_config=cfg, target=target, port=port, protocol=protocol,
            )
            probe_detail = probe.get("detail")
            if probe.get("ok") and probe.get("outbound"):
                ob = probe["outbound"]
                route_x.outbound = ob
                route_x.outbound_label = rex._label_for(ob, node_labels)
                route_x.certain = True
                method = "xray_probe"
                # xray's access log exposes only the chosen OUTBOUND, never
                # which rule matched. Layer A's matched_rule was merely the
                # geosite/geoip CANDIDATE that triggered this probe — it did
                # NOT necessarily match. Re-derive the action from the real
                # outbound and drop the stale candidate so the UI can't show
                # the contradiction "action: direct / outbound: node-2".
                blocker = route_x.blocking_rule
                route_x.action = rex.action_from_outbound(ob)
                route_x.matched_rule_id = None
                route_x.matched_rule_name = None
                route_x.matched_rule_type = None
                route_x.matched_value = None
                if blocker:
                    route_x.notes.append(
                        f"Layer A couldn't decide because rule {blocker} needs "
                        "the geosite/geoip .dat files. xray evaluated the full "
                        f"ruleset live and routed the target to {ob} "
                        "(its access log exposes only the chosen outbound, not "
                        "which rule matched)."
                    )
                route_x.notes.append("Ground-truth decision from live xray probe.")
        except Exception as exc:  # noqa: BLE001
            probe_detail = f"probe failed: {exc}"

    # ── Reachability stage ──
    reach = RouteExplainReachability(tested=False)
    if body.test_reachability and primary_ip:
        reach.tested = True
        ob = route_x.outbound or "direct"
        t0 = _time.monotonic()
        if ob == "block":
            reach.ok = False
            reach.via = "block"
            reach.detail = "Routing action is BLOCK — connection would be dropped."
        elif ob == "direct":
            code = await _http_probe_direct(primary_ip, target, port=port, timeout=6.0)
            reach.ok = code is not None
            reach.http_code = code
            reach.via = "direct"
            reach.latency_ms = int((_time.monotonic() - t0) * 1000)
            reach.detail = (f"HTTP {code} direct (SO_MARK bypass)"
                            if code is not None else "no response (direct)")
        else:
            # proxy / node-N → go through the live SOCKS inbound so it
            # follows the SAME routing rules the box runs.
            code, detail = await _probe_via_socks(settings_map, target, port)
            reach.ok = code is not None
            reach.http_code = code
            reach.via = ob
            reach.latency_ms = int((_time.monotonic() - t0) * 1000)
            reach.detail = detail

    return RouteExplainResult(
        target=target, port=port, protocol=protocol, is_ip=is_ip,
        dns=RouteExplainDns(
            is_ip=dns_x.is_ip,
            matched_rule_id=dns_x.matched_rule_id,
            matched_rule_name=dns_x.matched_rule_name,
            matched_pattern=dns_x.matched_pattern,
            server=dns_x.server,
            server_type=dns_x.server_type,
            uses_global_upstream=dns_x.uses_global_upstream,
            query_strategy=dns_x.query_strategy,
            geosite_uncertain=dns_x.geosite_uncertain,
            resolved_ips=resolved_ips,
            resolve_error=resolve_error,
            note=dns_x.note,
        ),
        routing=RouteExplainRouting(
            matched_rule_id=route_x.matched_rule_id,
            matched_rule_name=route_x.matched_rule_name,
            matched_rule_type=route_x.matched_rule_type,
            matched_value=route_x.matched_value,
            action=route_x.action,
            outbound=route_x.outbound,
            outbound_label=route_x.outbound_label,
            certain=route_x.certain,
            blocking_rule=route_x.blocking_rule,
            rules_evaluated=route_x.rules_evaluated,
            set_id=route_x.set_id,
            set_name=route_x.set_name,
            method=method,
            probe_detail=probe_detail,
            notes=route_x.notes,
        ),
        reachability=reach,
    )


async def _probe_via_socks(settings_map: dict, target: str, port: int) -> tuple:
    """Connect to target:port through the live xray SOCKS inbound (1080)
    so reachability follows the same routing the box runs. Returns
    (http_code|None, detail). Uses LAN proxy creds if auth is on."""
    socks_port = int(settings_map.get("socks_port", "1080"))
    user = settings_map.get("lan_proxy_auth_user", "")
    pw = settings_map.get("lan_proxy_auth_pass", "")
    auth_on = settings_map.get("lan_proxy_auth_enabled", "false").lower() == "true"
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", socks_port), timeout=4
        )
    except Exception as exc:  # noqa: BLE001
        return None, f"cannot reach local SOCKS inbound: {exc}"
    try:
        if auth_on and user:
            writer.write(b"\x05\x01\x02")  # offer user/pass
            await writer.drain()
            ver = await asyncio.wait_for(reader.readexactly(2), timeout=4)
            if ver[1] == 0x02:
                u = user.encode()[:255]; p = pw.encode()[:255]
                writer.write(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
                await writer.drain()
                st = await asyncio.wait_for(reader.readexactly(2), timeout=4)
                if st[1] != 0x00:
                    return None, "SOCKS auth rejected"
        else:
            writer.write(b"\x05\x01\x00")
            await writer.drain()
            await asyncio.wait_for(reader.readexactly(2), timeout=4)
        host = target.encode()[:255]
        writer.write(b"\x05\x01\x00\x03" + bytes([len(host)]) + host + port.to_bytes(2, "big"))
        await writer.drain()
        rep = await asyncio.wait_for(reader.readexactly(10), timeout=6)
        if rep[1] != 0x00:
            return None, f"SOCKS connect failed (code {rep[1]})"
        # connected through the proxy — send a tiny HTTP probe for a status
        writer.write(f"GET / HTTP/1.0\r\nHost: {target}\r\nConnection: close\r\n\r\n".encode())
        await writer.drain()
        data = await asyncio.wait_for(reader.read(256), timeout=6)
        first = data.split(b"\r\n", 1)[0]
        parts = first.split(b" ", 2)
        if len(parts) >= 2 and parts[1].isdigit():
            return int(parts[1]), f"HTTP {parts[1].decode()} via proxy"
        return 0, "connected via proxy (no HTTP status parsed)"
    except Exception as exc:  # noqa: BLE001
        return None, f"proxy probe error: {exc}"
    finally:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=2)
        except Exception:
            pass


@router.post("/sni-scan")
async def sni_scan_endpoint(body: dict, session=Depends(get_session)):
    """Check whether a domain is a viable REALITY dest (TLS 1.3 + ALPN h2).

    Probes THROUGH the active node when one is set — so the verdict reflects
    what the exit sees (censorship/geo differ per exit), not the box's own
    network — and falls back to a direct probe otherwise. Body: {"domain": ...}.
    """
    from app.core.speedtest import sni_scan
    from app.models import Node, Settings as DBSettings
    from sqlmodel import select

    domain = (body or {}).get("domain") or ""

    active: Node | None = None
    row = (await session.exec(
        select(DBSettings).where(DBSettings.key == "active_node_id")
    )).first()
    if row and row.value:
        try:
            active = await session.get(Node, int(row.value))
        except (TypeError, ValueError):
            active = None
    # Only route through it if it's actually usable as an exit.
    if active is not None and not active.enabled:
        active = None

    return await sni_scan(domain, active)
