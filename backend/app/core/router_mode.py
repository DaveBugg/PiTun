"""Bring router mode up and down as one operation.

The pieces (NAT, the firewall, DHCP, IP forwarding) are individually harmless
and individually useless — a box that NATs but hands out no addresses, or
hands out addresses while forwarding is off, is broken in a way that looks
like "the internet stopped working" to everyone on the LAN.

So this is deliberately all-or-nothing: apply in dependency order, and if any
step fails, tear the whole thing back down rather than leave the network
half-configured. Gateway mode is the safe resting state and teardown is
always allowed to run, even from an inconsistent starting point.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core import dhcp as dhcp_mod
from app.core import nftables as nft
from app.core import wifi as wifi_mod
from app.core import wan as wan_mod
from app.core import network_config as nc
from app.models import Settings as DBSettings

logger = logging.getLogger(__name__)

_HOST_PROC_SYS = "/host/proc_sys"   # bind-mounted from the host, as in api/system


class RouterModeError(RuntimeError):
    """Router mode could not be applied. The caller is left in gateway mode."""


def set_ip_forward(enabled: bool) -> bool:
    """Toggle IPv4 forwarding on the host. Without it nothing routes."""
    try:
        with open(f"{_HOST_PROC_SYS}/net/ipv4/ip_forward", "w") as f:
            f.write("1" if enabled else "0")
        logger.info("ip_forward set to %s", int(enabled))
        return True
    except OSError as exc:
        logger.error("Could not set ip_forward: %s", exc)
        return False


async def _settings_map(session: AsyncSession) -> dict:
    rows = (await session.exec(select(DBSettings))).all()
    return {r.key: r.value for r in rows}


def _lan_addressing(lan: str, m: dict) -> tuple[str, str]:
    """(lan_address, lan_cidr) for the LAN port.

    Prefers what the interface actually has — router mode can't invent an
    address the kernel doesn't hold — and falls back to the configured
    `lan_cidr` only to describe the subnet shape.
    """
    ip, prefix = nc.read_interface_address(lan)
    if not ip or prefix is None:
        raise RouterModeError(
            f"LAN port '{lan}' has no IPv4 address. Give it a static address "
            f"before enabling router mode — it has to be the gateway the LAN "
            f"talks to."
        )
    import ipaddress
    net = ipaddress.ip_network(f"{ip}/{prefix}", strict=False)
    return ip, str(net)


async def teardown() -> dict:
    """Return the box to gateway mode. Safe to call at any time.

    Every step is best-effort and failures are recorded rather than raised:
    this is the escape hatch. If it could fail, a box that lost its Docker
    socket (or was already half-configured) would be stuck in router mode with
    no way back — and gateway is the state that keeps the LAN working.
    """
    steps: dict = {}
    try:
        steps["dhcp"] = (await dhcp_mod.stop())["running"] is False
    except Exception as exc:  # noqa: BLE001 — never block the way back
        logger.warning("Teardown: could not stop the DHCP server: %s", exc)
        steps["dhcp"] = f"error: {exc}"
    try:
        steps["wifi"] = (await wifi_mod.stop())["running"] is False
    except Exception as exc:  # noqa: BLE001
        logger.warning("Teardown: could not stop the access point: %s", exc)
        steps["wifi"] = f"error: {exc}"
    try:
        # Only PiTun-created uplink connections are removed; a plain DHCP
        # uplink was never ours to take down.
        steps["wan"] = wan_mod.teardown()["removed"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Teardown: could not remove the uplink connection: %s", exc)
        steps["wan"] = f"error: {exc}"
    try:
        steps["nat"] = await nft.remove_router_nat()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Teardown: could not remove the router firewall: %s", exc)
        steps["nat"] = f"error: {exc}"
    # IP forwarding is left ON: the TPROXY path in gateway mode needs it too,
    # and turning it off here would break proxying for everyone.
    logger.info("Router mode torn down: %s", steps)
    return {"mode": "gateway", "steps": steps}


async def apply(session: AsyncSession) -> dict:
    """Reconcile the dataplane with `operating_mode`.

    Returns a summary. Raises RouterModeError if router mode was requested but
    could not be brought up — the box is left in gateway mode in that case.
    """
    m = await _settings_map(session)
    if m.get("operating_mode", "gateway") != "router":
        return await teardown()

    wan = (m.get("wan_interface") or "").strip()
    lan = (m.get("lan_interface") or "").strip()
    if not wan or not lan:
        raise RouterModeError("Router mode needs both a WAN and a LAN port assigned.")

    present = {i["name"] for i in nc.list_interfaces()}
    for role, value in (("WAN", wan), ("LAN", lan)):
        if value not in present:
            raise RouterModeError(f"{role} port '{value}' is not present on this box.")

    lan_address, lan_cidr = _lan_addressing(lan, m)

    # DHCP config is built (and validated) BEFORE anything is applied, so a bad
    # pool fails while the network is still untouched.
    lease_hours = int(m.get("dhcp_lease_hours") or 12)
    pool_start = (m.get("dhcp_pool_start") or "").strip()
    pool_end = (m.get("dhcp_pool_end") or "").strip()
    if not pool_start or not pool_end:
        pool_start, pool_end = dhcp_mod.default_pool_for(lan_cidr, lan_address)
    # Reservations come from devices the operator explicitly pinned. Ones that
    # don't fit the current subnet are dropped by the renderer with a log line
    # rather than failing the whole apply — a stale reservation from a previous
    # LAN shouldn't stop the router coming up.
    from app.models import Device
    reserved = (await session.exec(
        select(Device).where(Device.dhcp_reserved_ip != None)  # noqa: E711
    )).all()
    leases = [
        dhcp_mod.StaticLease(
            mac=d.mac, ip=d.dhcp_reserved_ip or "",
            name=(d.name or d.hostname or "").replace(" ", "-")[:32],
        )
        for d in reserved if d.mac and d.dhcp_reserved_ip
    ]

    dhcp_cfg = dhcp_mod.DhcpConfig(
        interface=lan, lan_cidr=lan_cidr, lan_address=lan_address,
        pool_start=pool_start, pool_end=pool_end, lease_hours=lease_hours,
        static_leases=leases,
    )
    try:
        dhcp_mod.render_dnsmasq_conf(dhcp_cfg)
    except dhcp_mod.DhcpConfigError as exc:
        raise RouterModeError(f"DHCP settings are not usable: {exc}")

    # The uplink is built first and everything downstream keys off the
    # interface it actually produces: PPPoE moves traffic to ppp0 and a VLAN
    # tag to eth0.<id>. NAT on the physical port while traffic leaves
    # elsewhere is a router that forwards nothing and explains nothing.
    wan_cfg = wan_mod.WanConfig(
        interface=wan,
        mode=m.get("wan_mode", "dhcp"),
        vlan_id=int(m.get("wan_vlan_id") or 0),
        mac_clone=(m.get("wan_mac_clone") or "").strip(),
        address=(m.get("wan_static_address") or "").strip(),
        gateway=(m.get("wan_static_gateway") or "").strip(),
        dns=[d.strip() for d in (m.get("wan_static_dns") or "").split(",") if d.strip()],
        username=(m.get("wan_pppoe_user") or "").strip(),
        password=m.get("wan_pppoe_password", ""),
        service=(m.get("wan_pppoe_service") or "").strip(),
    )
    try:
        wan_mod.validate(wan_cfg)
    except wan_mod.WanConfigError as exc:
        raise RouterModeError(f"WAN settings are not usable: {exc}")
    wan_iface = wan_mod.effective_interface(wan_cfg)

    applied: list[str] = []
    try:
        # Only touch the uplink when it needs building. Plain DHCP on an
        # untagged port with no MAC clone is what the host already does, and
        # replacing that connection would drop the line for no gain.
        needs_wan_setup = (
            wan_cfg.mode != "dhcp" or wan_cfg.vlan_id or wan_cfg.mac_clone
        )
        if needs_wan_setup:
            wan_mod.apply(wan_cfg)
            applied.append("wan")

        if not set_ip_forward(True):
            raise RouterModeError("Could not enable IPv4 forwarding on the host.")
        applied.append("ip_forward")

        if not await nft.apply_router_nat(wan_iface, lan):
            raise RouterModeError("Could not apply the router firewall/NAT rules.")
        applied.append("nat")

        if (m.get("dhcp_enabled") or "true").lower() == "true":
            await dhcp_mod.start(dhcp_cfg)
            applied.append("dhcp")

        # WiFi last: it's the only step that can take the LAN interface down
        # while reconfiguring the radio, so everything else is already up if
        # it fails and the rollback has less to undo.
        if (m.get("wifi_enabled") or "false").lower() == "true":
            if not nc.is_wireless(lan):
                raise RouterModeError(
                    f"WiFi is enabled but the LAN port '{lan}' is not a wireless "
                    f"adapter. Either pick a wireless LAN port or turn WiFi off."
                )
            wifi_cfg = wifi_mod.WifiConfig(
                interface=lan,
                ssid=m.get("wifi_ssid", ""),
                passphrase=m.get("wifi_passphrase", ""),
                country=(m.get("wifi_country") or "").upper(),
                band=m.get("wifi_band", "2.4"),
                channel=int(m.get("wifi_channel") or 0),
                security=m.get("wifi_security", "wpa2"),
                hidden=(m.get("wifi_hidden") or "false").lower() == "true",
            )
            try:
                await wifi_mod.start(wifi_cfg)
            except wifi_mod.WifiConfigError as exc:
                raise RouterModeError(f"WiFi could not start: {exc}")
            applied.append("wifi")
    except Exception as exc:
        # Half-applied router mode is worse than none: the LAN would get
        # addresses pointing at a gateway that doesn't forward, or forwarding
        # with no addresses handed out. Undo everything and stay in gateway.
        logger.error("Router mode apply failed after %s — rolling back: %s", applied, exc)
        await teardown()
        if isinstance(exc, RouterModeError):
            raise
        raise RouterModeError(str(exc))

    logger.info("Router mode active: WAN=%s LAN=%s pool=%s-%s",
                wan, lan, pool_start, pool_end)
    return {
        "mode": "router", "wan": wan, "wan_interface": wan_iface,
        "wan_mode": wan_cfg.mode, "lan": lan,
        "lan_address": lan_address, "lan_cidr": lan_cidr,
        "dhcp_pool": [pool_start, pool_end], "reservations": len(leases),
        "applied": applied,
    }


async def status(session: AsyncSession) -> dict:
    """What the dataplane is actually doing, not what settings say."""
    m = await _settings_map(session)
    return {
        "configured_mode": m.get("operating_mode", "gateway"),
        "wan_interface": m.get("wan_interface", ""),
        "lan_interface": m.get("lan_interface", ""),
        "dhcp": await dhcp_mod.status(),
    }


# ── Diagnosis of the two silent uplink failures ──────────────────────────────
#
# Both look identical from a user's chair — "the internet is broken" — and
# neither logs anything, so they get diagnosed by guesswork. The counters
# attached to the WAN rules turn them into something we can just read off.

async def diagnose_wan() -> list[dict]:
    """Findings about the uplink, in plain language.

    Each finding: {level, title, detail, hint}. `level` is ok | warn | error.
    """
    findings: list[dict] = []
    counters = await nft.router_counters()
    if not counters:
        return [{
            "level": "ok",
            "title": "Router mode is not active",
            "detail": "The uplink firewall isn't applied, so there is nothing to check.",
            "hint": "",
        }]

    dhcp_in = counters.get("wan_dhcp_in", {}).get("packets", 0)
    icmp_in = counters.get("wan_icmp_in", {}).get("packets", 0)
    blocked = counters.get("wan_blocked", {}).get("packets", 0)

    # 1. Did the uplink ever receive a DHCP reply?
    #
    # If the WAN has an address, DHCP obviously worked (or it's static) and the
    # counter only matters for renewals. If it has none AND no replies were
    # ever seen, that's the diagnosis rather than a guess.
    if dhcp_in == 0:
        findings.append({
            "level": "warn",
            "title": "No DHCP replies have arrived on the WAN port",
            "detail": (
                "Nothing has come back from an upstream DHCP server since the "
                "firewall was applied. That's expected if the uplink uses a "
                "static address or PPPoE; it's the cause to look at first if "
                "the WAN has no address at all."
            ),
            "hint": "Check the cable and whether the ISP hands out addresses by DHCP.",
        })
    else:
        findings.append({
            "level": "ok",
            "title": f"DHCP replies reaching the WAN port ({dhcp_in} packets)",
            "detail": "The uplink can obtain and renew its address.",
            "hint": "",
        })

    # 2. PMTU black hole — the nastiest of the two, because most things work.
    #
    # Small requests (DNS, a ping, an SSH handshake) succeed while large
    # transfers hang forever: pages half-load, downloads stall at a few KB.
    # It happens when ICMP "fragmentation needed" can't get back to us.
    if icmp_in == 0:
        findings.append({
            "level": "warn",
            "title": "No ICMP has returned on the WAN port",
            "detail": (
                "PMTU discovery relies on ICMP 'destination unreachable — "
                "fragmentation needed' coming back from the path. With none "
                "seen, a path MTU problem would show up as large transfers "
                "hanging while small requests work — pages that half-load and "
                "downloads that stall — rather than as a clean error."
            ),
            "hint": (
                "Normal on a freshly applied ruleset or a clean path. Suspect it "
                "if browsing hangs on some sites but DNS and ping are fine."
            ),
        })
    else:
        findings.append({
            "level": "ok",
            "title": f"ICMP returning normally ({icmp_in} packets)",
            "detail": "PMTU discovery and traceroute have a working return path.",
            "hint": "",
        })

    findings.append({
        "level": "ok",
        "title": f"{blocked} unsolicited packets dropped from the internet",
        "detail": (
            "Connection attempts from the WAN that were refused. A steady count "
            "here is ordinary background scanning, not a problem."
        ),
        "hint": "",
    })
    return findings
