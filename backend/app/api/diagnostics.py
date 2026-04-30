"""System diagnostics API — network, health checks, resources, docker logs."""
import asyncio
import logging
import re
import shlex
from typing import Any, Dict, List

from fastapi import APIRouter, Query

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


async def _check_internet() -> Dict[str, Any]:
    """Check internet connectivity via HTTP."""
    result = await _run("curl -sf --max-time 5 -o /dev/null -w '%{http_code}' https://www.google.com/generate_204")
    ok = result in ("200", "204")
    if not ok:
        result = await _run("curl -sf --max-time 5 -o /dev/null -w '%{http_code}' http://cp.cloudflare.com")
        ok = result in ("200", "204")
    return {"name": "internet", "ok": ok, "detail": "connected" if ok else "no internet access"}


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
        _check_internet(),
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
