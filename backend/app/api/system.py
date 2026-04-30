"""System control: start/stop/status/mode/settings."""
import json
import subprocess
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_session
from app.models import BalancerGroup, DNSRule, Node, RoutingRule, Settings as DBSettings
from app.core.device_scanner import get_device_macs_for_mode
from app.schemas import (
    ActiveNodeUpdate,
    DataVersions,
    HostVersions,
    ModeUpdate,
    PitunVersions,
    RuntimeVersions,
    SettingsRead,
    SettingsUpdate,
    SystemStatus,
    SystemVersions,
    ThirdPartyVersions,
)

router = APIRouter(prefix="/system", tags=["system"])


def _safe_int(settings_map: dict, key: str, default: int) -> int:
    """Read an integer setting with a hardened fallback.

    DB Settings are stored as strings and can be corrupted (manual edit,
    bad migration, UI glitch) — a single bad value previously crashed
    `/start` with a bare `ValueError` from `int("abc")` and left the user
    unable to bring up the proxy. This helper returns `default` on any
    parse failure instead.
    """
    try:
        raw = settings_map.get(key)
        if raw is None or raw == "":
            return default
        return int(raw)
    except (TypeError, ValueError):
        import logging
        logging.getLogger(__name__).warning(
            "Invalid int value for setting %r: %r — falling back to %d",
            key, settings_map.get(key), default,
        )
        return default


def _detect_ip(interface: str) -> str:
    """Auto-detect the first IPv4 address on `interface` via `ip -j addr`."""
    try:
        out = subprocess.run(
            ["ip", "-j", "-4", "addr", "show", interface],
            capture_output=True, text=True, timeout=3,
        )
        if out.returncode == 0 and out.stdout.strip():
            import json as _json
            data = _json.loads(out.stdout)
            for iface in data:
                for info in iface.get("addr_info", []):
                    addr = info.get("local", "")
                    if addr:
                        return addr
    except Exception:
        pass
    return ""


_HOST_PROC_SYS = "/host/proc_sys"   # bind-mounted from host /proc/sys
_HOST_RESOLV = "/host/resolv.conf"  # bind-mounted from host /etc/resolv.conf


async def apply_system_toggles_on_boot() -> None:
    """Apply disable_ipv6 and dns_over_tcp from DB to host on startup.

    /proc/sys values reset after reboot, so we re-apply saved state.
    resolv.conf survives reboot, but we ensure consistency anyway.
    """
    import logging
    _log = logging.getLogger(__name__)

    try:
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.database import get_async_engine
        from app.models import Settings as DBSettings

        async with AsyncSession(get_async_engine()) as session:
            rows = (await session.exec(select(DBSettings))).all()
            m = {r.key: r.value for r in rows}

        # IPv6
        disable_ipv6 = m.get("disable_ipv6", "false").lower() == "true"
        val = "1" if disable_ipv6 else "0"
        for path in ("net/ipv6/conf/all/disable_ipv6", "net/ipv6/conf/default/disable_ipv6"):
            try:
                with open(f"{_HOST_PROC_SYS}/{path}", "w") as f:
                    f.write(val)
            except Exception:
                pass
        _log.info("Applied disable_ipv6=%s on boot", disable_ipv6)

        # DNS over TCP
        dns_over_tcp = m.get("dns_over_tcp", "false").lower() == "true"
        try:
            with open(_HOST_RESOLV) as f:
                lines = [l for l in f.read().splitlines() if "use-vc" not in l]
            if dns_over_tcp:
                lines.append("options use-vc")
            with open(_HOST_RESOLV, "w") as f:
                f.write("\n".join(lines) + "\n")
            _log.info("Applied dns_over_tcp=%s on boot", dns_over_tcp)
        except Exception:
            pass

    except Exception as exc:
        _log.warning("Failed to apply system toggles on boot: %s", exc)


def _detect_sysctl_bool(key: str, fallback: bool) -> bool:
    """Read a sysctl value from host /proc/sys (bind-mounted)."""
    try:
        proc_path = _HOST_PROC_SYS + "/" + key.replace(".", "/")
        with open(proc_path) as f:
            return f.read().strip() == "1"
    except Exception:
        return fallback


def _detect_resolv_use_vc(fallback: bool) -> bool:
    """Check if host /etc/resolv.conf contains 'options use-vc'."""
    try:
        with open(_HOST_RESOLV) as f:
            return "use-vc" in f.read()
    except Exception:
        return fallback


# ── Helper ────────────────────────────────────────────────────────────────────

async def _get_setting(session: AsyncSession, key: str) -> Optional[str]:
    row = (await session.exec(select(DBSettings).where(DBSettings.key == key))).first()
    return row.value if row else None


async def _set_setting(session: AsyncSession, key: str, value: str) -> None:
    row = (await session.exec(select(DBSettings).where(DBSettings.key == key))).first()
    if row:
        row.value = value
        session.add(row)
    else:
        session.add(DBSettings(key=key, value=value))


async def _collect_vpn_server_ips(session: AsyncSession) -> List[str]:
    nodes = (await session.exec(select(Node).where(Node.enabled == True))).all()
    return list({n.address for n in nodes if n.address})


# ── Status ────────────────────────────────────────────────────────────────────

@router.get("/status", response_model=SystemStatus)
async def get_status(session: AsyncSession = Depends(get_session)):
    from app.core.xray import xray_manager
    from app.core.nftables import nftables_manager

    mode = await _get_setting(session, "mode") or "rules"
    active_id_str = await _get_setting(session, "active_node_id") or ""
    active_node = None
    active_node_id = None
    if active_id_str:
        try:
            active_node_id = int(active_id_str)
            active_node = await session.get(Node, active_node_id)
        except ValueError:
            pass

    version = xray_manager.version or await xray_manager.get_version()
    nft_active = await nftables_manager.is_active()

    from app.config import APP_VERSION
    return SystemStatus(
        running=xray_manager.is_running,
        pid=xray_manager.pid,
        uptime_seconds=xray_manager.uptime,
        mode=mode,
        active_node_id=active_node_id,
        active_node_name=active_node.name if active_node else None,
        nftables_active=nft_active,
        version=version,
        app_version=APP_VERSION,
    )


# ── Full version snapshot ─────────────────────────────────────────────────────

@router.get("/versions", response_model=SystemVersions)
async def get_versions():
    """Gather a complete version dump for the sidebar popover / About tab.

    Each sub-section is defensive: a missing /etc/os-release or broken
    docker socket degrades to `None`, the rest of the response still
    renders. PiTun-owned versions come from `app.config.APP_VERSION`
    (single source of truth — bump once, read everywhere).
    """
    import logging
    import os
    from datetime import datetime, timezone
    import platform

    from app.config import APP_VERSION, settings
    from app.core.xray import xray_manager

    log = logging.getLogger(__name__)

    pitun = PitunVersions(backend=APP_VERSION, naive_image=APP_VERSION)

    # Runtime: xray binary, python interpreter.
    xray_ver = xray_manager.version or await xray_manager.get_version()
    runtime = RuntimeVersions(xray=xray_ver, python=platform.python_version())

    # Host: `pid: host` in compose means `os.uname()` already returns the
    # host kernel — no bind-mount needed for that. For the distro name we
    # look at `/host/os-release` (bind-mounted in compose); fall back to
    # `/etc/os-release` which — inside the backend image — is Debian
    # (python:3.11-slim), NOT the real host, so we flag it as such to
    # avoid confusing the user.
    host = HostVersions()
    try:
        uname = os.uname()
        host.kernel = uname.release
        host.arch = uname.machine
    except Exception as exc:
        log.debug("versions: uname failed: %s", exc)

    host_release_candidates = ["/host/os-release", "/host/etc/os-release"]
    os_release_src = next((p for p in host_release_candidates if os.path.exists(p)), None)
    if os_release_src:
        try:
            host.os = _parse_os_release(os_release_src)
        except Exception as exc:
            log.debug("versions: parsing %s failed: %s", os_release_src, exc)
    else:
        # Fall back to container's own /etc/os-release, but mark it so the
        # UI knows this is NOT the host distro. Better than showing nothing.
        try:
            distro = _parse_os_release("/etc/os-release")
            host.os = f"{distro} (container)" if distro else None
        except Exception as exc:
            log.debug("versions: /etc/os-release parse failed: %s", exc)

    # Docker engine version + the nginx container image tag (the nginx
    # running as reverse proxy is `pitun-nginx`). We skip `pitun-frontend`
    # entirely — it IS built on nginx:alpine but the user-facing "nginx
    # version" should reflect the actual ingress proxy. `pitun-docker-proxy`
    # only exists with COMPOSE_PROFILES=secure.
    third_party = ThirdPartyVersions()
    try:
        from app.core.naive_manager import naive_manager
        client = naive_manager._get_client()
        host.docker = client.version().get("Version")
        for ctr_name, attr in (("pitun-nginx", "nginx"), ("pitun-docker-proxy", "socket_proxy")):
            try:
                c = client.containers.get(ctr_name)
                tag = c.image.tags[0] if c.image.tags else c.image.id[:12]
                setattr(third_party, attr, _short_tag(tag))
            except Exception:
                continue
    except Exception as exc:
        log.debug("versions: docker client introspection failed: %s", exc)

    # Data: migration HEAD from alembic_version table + geo-data mtimes
    # so the user can tell how stale the xray rules are without opening
    # the GeoData page.
    data_block = DataVersions()
    try:
        from sqlmodel.ext.asyncio.session import AsyncSession
        from sqlalchemy import text as sql_text
        from app.database import get_async_engine
        async with AsyncSession(get_async_engine()) as session:
            result = await session.execute(sql_text("SELECT version_num FROM alembic_version"))
            row = result.first()
            if row:
                data_block.alembic_rev = row[0]
    except Exception as exc:
        log.debug("versions: alembic_version query failed: %s", exc)

    for attr, path in (
        ("geoip_mtime", settings.xray_geoip_path),
        ("geosite_mtime", settings.xray_geosite_path),
    ):
        try:
            if os.path.exists(path):
                ts = os.stat(path).st_mtime
                iso = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
                setattr(data_block, attr, iso)
        except Exception as exc:
            log.debug("versions: stat %s failed: %s", path, exc)

    return SystemVersions(
        pitun=pitun,
        runtime=runtime,
        third_party=third_party,
        host=host,
        data=data_block,
    )


def _parse_os_release(path: str) -> str | None:
    """Extract PRETTY_NAME from /etc/os-release-style files."""
    with open(path, "r") as f:
        entries = dict(
            line.strip().split("=", 1)
            for line in f
            if "=" in line and not line.startswith("#")
        )
    return entries.get("PRETTY_NAME", "").strip('"') or None


def _short_tag(tag: str) -> str:
    """Strip registry prefix from a docker image tag for display."""
    if ":" in tag:
        return tag.rsplit("/", 1)[-1]
    return tag


# ── Start / Stop / Restart ────────────────────────────────────────────────────

async def _apply_nftables(session: AsyncSession, settings_map: dict) -> str:
    """Apply nftables rules with device filtering. Returns inbound_mode."""
    from app.core.nftables import nftables_manager

    mode = settings_map.get("mode", "rules")
    inbound_mode = settings_map.get("inbound_mode", "tproxy")

    # Bypass mode: no tproxy needed — flush nftables and let traffic go direct
    if mode == "bypass":
        await nftables_manager.flush()
        return inbound_mode

    rules = list((await session.exec(select(RoutingRule).where(RoutingRule.enabled == True))).all())
    bypass_macs = _collect_bypass_macs(rules)
    bypass_dsts = _collect_bypass_dsts(rules)

    # Auto-bypass all enabled naive upstream server IPs. The sidecar runs in
    # host netns and opens a plain TCP socket to its upstream — without this
    # the output mangle hook would mark that packet for tproxy and loop it
    # back through xray → the naive sidecar → forever. Xray's own outbounds
    # set SO_MARK=255 to break the loop; naive can't, so we bypass by dest.
    naive_dsts = await _collect_naive_bypass_dsts(session)
    if naive_dsts:
        bypass_dsts = list(bypass_dsts) + naive_dsts

    device_info = await get_device_macs_for_mode(session)
    device_mode = device_info["mode"]

    if device_mode == "exclude_list":
        bypass_macs.extend(device_info["exclude_macs"])

    await nftables_manager.apply_rules(
        inbound_mode=inbound_mode,
        bypass_macs=bypass_macs,
        bypass_dst_cidrs=bypass_dsts,
        include_macs=device_info["include_macs"] if device_mode == "include_only" else None,
        device_routing_mode=device_mode,
        tproxy_tcp=_safe_int(settings_map, "tproxy_port_tcp", 7893),
        tproxy_udp=_safe_int(settings_map, "tproxy_port_udp", 7894),
        dns_port=_safe_int(settings_map, "dns_port", 5353),
        block_quic=settings_map.get("block_quic", "true").lower() == "true",
        kill_switch=settings_map.get("kill_switch", "false").lower() == "true",
    )
    return inbound_mode


@router.post("/start", status_code=204)
async def start_proxy(session: AsyncSession = Depends(get_session)):
    from app.core.xray import xray_manager

    await _regenerate_and_write(session)
    settings_map = await _load_settings_map(session)
    inbound_mode = await _apply_nftables(session, settings_map)

    if xray_manager.is_running:
        await xray_manager.reload()
    else:
        await xray_manager.start()

    if inbound_mode in ("tun", "both"):
        auto_route = settings_map.get("tun_auto_route", "true").lower() == "true"
        if not auto_route:
            from app.core.tun import setup_tun
            import app.core.xray as xray_mod
            await setup_tun(
                address=settings_map.get("tun_address", "10.0.0.1/30"),
                mtu=_safe_int(settings_map, "tun_mtu", 9000),
            )
            xray_mod._tun_active = True
        else:
            import app.core.xray as xray_mod
            xray_mod._tun_active = True


@router.post("/stop", status_code=204)
async def stop_proxy(session: AsyncSession = Depends(get_session)):
    from app.core.xray import xray_manager
    from app.core.nftables import nftables_manager

    settings_map = await _load_settings_map(session)
    kill_switch = settings_map.get("kill_switch", "false").lower() == "true"
    if kill_switch:
        vpn_ips = await _collect_vpn_server_ips(session)
        await nftables_manager.apply_kill_switch(vpn_server_ips=vpn_ips)

    await xray_manager.stop()

    if not kill_switch:
        await nftables_manager.flush()


@router.post("/restart", status_code=204)
async def restart_proxy(session: AsyncSession = Depends(get_session)):
    from app.core.xray import xray_manager

    await _regenerate_and_write(session)
    settings_map = await _load_settings_map(session)
    inbound_mode = await _apply_nftables(session, settings_map)

    await xray_manager.restart()

    if inbound_mode in ("tun", "both"):
        auto_route = settings_map.get("tun_auto_route", "true").lower() == "true"
        if not auto_route:
            from app.core.tun import setup_tun
            await setup_tun(
                address=settings_map.get("tun_address", "10.0.0.1/30"),
                mtu=_safe_int(settings_map, "tun_mtu", 9000),
            )
        import app.core.xray as xray_mod
        xray_mod._tun_active = True


@router.post("/reload-config", status_code=204)
async def reload_config(session: AsyncSession = Depends(get_session)):
    """Regenerate xray config, re-apply nftables, and hot-reload."""
    from app.core.xray import xray_manager

    await _regenerate_and_write(session)
    settings_map = await _load_settings_map(session)
    await _apply_nftables(session, settings_map)

    if xray_manager.is_running:
        await xray_manager.reload()


# ── Mode ──────────────────────────────────────────────────────────────────────

@router.post("/mode", status_code=204)
async def set_mode(body: ModeUpdate, session: AsyncSession = Depends(get_session)):
    await _set_setting(session, "mode", body.mode)
    await session.commit()


@router.post("/active-node", status_code=204)
async def set_active_node(body: ActiveNodeUpdate, session: AsyncSession = Depends(get_session)):
    node = await session.get(Node, body.node_id)
    if not node:
        raise HTTPException(404, "Node not found")
    await _set_setting(session, "active_node_id", str(body.node_id))
    await session.commit()


# ── Settings ──────────────────────────────────────────────────────────────────

@router.get("/settings", response_model=SettingsRead)
async def get_settings(session: AsyncSession = Depends(get_session)):
    m = await _load_settings_map(session)
    active_id: Optional[int] = None
    if m.get("active_node_id"):
        try:
            active_id = int(m["active_node_id"])
        except ValueError:
            pass

    failover_ids: List[int] = []
    if m.get("failover_node_ids"):
        try:
            failover_ids = json.loads(m["failover_node_ids"])
        except (ValueError, json.JSONDecodeError):
            pass

    from app.config import settings as env_settings

    def _int(key: str, default: int) -> int:
        try:
            return int(m.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    # Auto-detect PiTun IP from interface; sync to DB so device_scanner sees it
    iface = m.get("interface") or env_settings.interface
    detected_ip = _detect_ip(iface)
    effective_ip = detected_ip or m.get("gateway_ip") or env_settings.gateway_ip
    if detected_ip and m.get("gateway_ip") != detected_ip:
        row = await session.exec(select(DBSettings).where(DBSettings.key == "gateway_ip"))
        s = row.first()
        if s:
            s.value = detected_ip
        else:
            session.add(DBSettings(key="gateway_ip", value=detected_ip))
        await session.commit()

    return SettingsRead(
        mode=m.get("mode", "rules"),
        active_node_id=active_id,
        failover_enabled=m.get("failover_enabled", "false").lower() == "true",
        failover_node_ids=failover_ids,
        # Network — auto-detected gateway_ip synced to DB
        interface=iface,
        gateway_ip=effective_ip,
        lan_cidr=m.get("lan_cidr") or env_settings.lan_cidr,
        router_ip=m.get("router_ip", ""),
        # Ports
        tproxy_port_tcp=_int("tproxy_port_tcp", 7893),
        tproxy_port_udp=_int("tproxy_port_udp", 7894),
        socks_port=_int("socks_port", 1080),
        http_port=_int("http_port", 8080),
        dns_port=_int("dns_port", 5353),
        dns_mode=m.get("dns_mode", "plain"),
        dns_upstream=m.get("dns_upstream", "8.8.8.8"),
        dns_upstream_secondary=m.get("dns_upstream_secondary") or None,
        dns_fallback=m.get("dns_fallback") or None,
        fakedns_enabled=m.get("fakedns_enabled", "false").lower() == "true",
        fakedns_pool=m.get("fakedns_pool", "198.18.0.0/15"),
        fakedns_pool_size=_int("fakedns_pool_size", 65535),
        dns_sniffing=m.get("dns_sniffing", "true").lower() == "true",
        bypass_cn_dns=m.get("bypass_cn_dns", "false").lower() == "true",
        bypass_ru_dns=m.get("bypass_ru_dns", "false").lower() == "true",
        bypass_private=m.get("bypass_private", "true").lower() == "true",
        log_level=m.get("log_level", "warning"),
        geoip_url=m.get("geoip_url", ""),
        geosite_url=m.get("geosite_url", ""),
        geoip_mmdb_url=m.get("geoip_mmdb_url") or None,
        inbound_mode=m.get("inbound_mode", "tproxy"),
        tun_address=m.get("tun_address", "10.0.0.1/30"),
        tun_mtu=_int("tun_mtu", 9000),
        tun_stack=m.get("tun_stack", "system"),
        tun_auto_route=m.get("tun_auto_route", "true").lower() == "true",
        tun_strict_route=m.get("tun_strict_route", "true").lower() == "true",
        block_quic=m.get("block_quic", "true").lower() == "true",
        kill_switch=m.get("kill_switch", "false").lower() == "true",
        auto_restart_xray=m.get("auto_restart_xray", "true").lower() == "true",
        dns_query_log_enabled=m.get("dns_query_log_enabled", "false").lower() == "true",
        device_routing_mode=m.get("device_routing_mode", "all"),
        disable_ipv6=_detect_sysctl_bool("net.ipv6.conf.all.disable_ipv6", m.get("disable_ipv6", "false").lower() == "true"),
        dns_over_tcp=_detect_resolv_use_vc(m.get("dns_over_tcp", "false").lower() == "true"),
        health_interval=_int("health_interval", env_settings.health_interval),
        health_timeout=_int("health_timeout", env_settings.health_timeout),
        health_full_check_interval=_int("health_full_check_interval", 300),
        # GeoScheduler knobs — read from DB (init_default_settings seeds them).
        # Pydantic defaults would mask whatever the user actually picked,
        # so the fall-through value matches the seed defaults.
        geo_auto_update=m.get("geo_auto_update", "true").lower() == "true",
        geo_update_interval_days=_int("geo_update_interval_days", 1),
        geo_update_window_start=_int("geo_update_window_start", 4),
        geo_update_window_end=_int("geo_update_window_end", 6),
        timezone=m.get("timezone", "UTC"),
    )


@router.get("/stats")
async def get_traffic_stats():
    from app.core.stats import get_outbound_stats
    return await get_outbound_stats()


_metrics_cache: dict = {"period": "", "ts": 0.0, "data": []}
_METRICS_CACHE_TTL = 30.0  # seconds


@router.get("/metrics")
async def get_system_metrics(
    period: str = "1h",
    session: AsyncSession = Depends(get_session),
):
    """System metrics for dashboard charts.

    period: 15m, 1h, 3h, 6h, 12h, 1d, 3d
    """
    import time
    from datetime import datetime, timedelta, timezone
    from app.models import SystemMetric

    # Simple in-memory cache — avoids repeated DB queries from multiple clients
    now = time.monotonic()
    if _metrics_cache["period"] == period and (now - _metrics_cache["ts"]) < _METRICS_CACHE_TTL:
        return _metrics_cache["data"]

    multipliers = {
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "3h": timedelta(hours=3),
        "6h": timedelta(hours=6),
        "12h": timedelta(hours=12),
        "1d": timedelta(days=1),
        "3d": timedelta(days=3),
    }
    delta = multipliers.get(period, timedelta(hours=1))
    since = datetime.now(timezone.utc) - delta

    rows = (await session.exec(
        select(SystemMetric)
        .where(SystemMetric.ts >= since)
        .order_by(SystemMetric.ts)
    )).all()

    # Downsample to at most ~300 points — SVG charts choke on thousands of
    # path points (Firefox STATUS_BREAKPOINT on re-render).
    _MAX_POINTS = 300
    if len(rows) > _MAX_POINTS:
        step = len(rows) // _MAX_POINTS + 1
        rows = rows[::step]

    result = [
        {
            "ts": r.ts.strftime("%Y-%m-%dT%H:%M:%SZ") if r.ts else None,
            "cpu": r.cpu_percent,
            "ram_used": r.ram_used_mb,
            "ram_total": r.ram_total_mb,
            "disk_used": r.disk_used_gb,
            "disk_total": r.disk_total_gb,
            "net_sent": r.net_sent_bytes,
            "net_recv": r.net_recv_bytes,
        }
        for r in rows
    ]

    _metrics_cache["period"] = period
    _metrics_cache["ts"] = now
    _metrics_cache["data"] = result
    return result


@router.patch("/settings", status_code=204)
async def update_settings(body: SettingsUpdate, session: AsyncSession = Depends(get_session)):
    patches = body.model_dump(exclude_unset=True)
    for key, value in patches.items():
        if value is None:
            continue
        if key == "failover_node_ids":
            await _set_setting(session, key, json.dumps(value))
        elif isinstance(value, bool):
            await _set_setting(session, key, str(value).lower())
        elif isinstance(value, int):
            await _set_setting(session, key, str(value))
        else:
            await _set_setting(session, key, str(value))
    await session.commit()

    # Apply IPv6 toggle via bind-mounted host /proc/sys
    if "disable_ipv6" in patches:
        val = "1" if patches["disable_ipv6"] else "0"
        for path in ("net/ipv6/conf/all/disable_ipv6", "net/ipv6/conf/default/disable_ipv6"):
            try:
                with open(f"{_HOST_PROC_SYS}/{path}", "w") as f:
                    f.write(val)
            except Exception:
                pass

    # Apply DNS over TCP toggle via bind-mounted host /etc/resolv.conf
    if "dns_over_tcp" in patches:
        try:
            with open(_HOST_RESOLV) as f:
                lines = [l for l in f.read().splitlines() if "use-vc" not in l]
            if patches["dns_over_tcp"]:
                lines.append("options use-vc")
            with open(_HOST_RESOLV, "w") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:
            pass


# ── Internal helpers ──────────────────────────────────────────────────────────

async def _regenerate_and_write(session: AsyncSession) -> None:
    from app.core.config_gen import generate_config, write_config

    settings_map = await _load_settings_map(session)
    mode = settings_map.get("mode", "rules")
    active_id_str = settings_map.get("active_node_id", "")
    active_node = None
    if active_id_str:
        try:
            active_node = await session.get(Node, int(active_id_str))
        except ValueError:
            pass

    all_nodes = list((await session.exec(select(Node).where(Node.enabled == True))).all())
    rules = list((await session.exec(select(RoutingRule).where(RoutingRule.enabled == True))).all())
    dns_rules = list((await session.exec(select(DNSRule).where(DNSRule.enabled == True))).all())
    balancer_groups = list((await session.exec(select(BalancerGroup))).all())
    config = generate_config(active_node, all_nodes, rules, mode, settings_map, dns_rules, balancer_groups)
    await write_config(config)


async def _load_settings_map(session: AsyncSession) -> dict:
    rows = (await session.exec(select(DBSettings))).all()
    return {row.key: row.value for row in rows}


def _collect_bypass_macs(rules: List[RoutingRule]) -> List[str]:
    macs = []
    for rule in rules:
        if rule.rule_type == "mac" and rule.action == "direct":
            macs.extend(v.strip() for v in rule.match_value.split(","))
    return macs


def _collect_bypass_dsts(rules: List[RoutingRule]) -> List[str]:
    cidrs = []
    for rule in rules:
        if rule.rule_type == "dst_ip" and rule.action == "direct":
            cidrs.extend(v.strip() for v in rule.match_value.split(","))
    return cidrs


async def _collect_naive_bypass_dsts(session: AsyncSession) -> List[str]:
    """
    Resolve IPs of all enabled naive upstream servers → /32 CIDRs.
    Used to prevent the tproxy output-chain loop for the sidecar's socket.
    Domain names are resolved via socket.getaddrinfo (A records only).
    """
    import socket as _sock
    nodes = list((await session.exec(
        select(Node).where(Node.protocol == "naive", Node.enabled == True)  # noqa: E712
    )).all())
    out: List[str] = []
    for n in nodes:
        host = (n.address or "").strip()
        if not host:
            continue
        try:
            infos = _sock.getaddrinfo(host, None, _sock.AF_INET, _sock.SOCK_STREAM)
            for fam, _, _, _, sa in infos:
                if fam == _sock.AF_INET and sa and sa[0]:
                    out.append(f"{sa[0]}/32")
        except Exception:
            # If resolution fails (e.g. node offline / DNS broken), skip it.
            # Worst case the loop prevention doesn't apply until resolvable.
            continue
    # Dedup while preserving order
    seen = set()
    uniq = []
    for c in out:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq
