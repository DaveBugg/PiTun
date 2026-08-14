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


def _lan_members(m: dict) -> list[str]:
    """Every port on the LAN side, primary first.

    `lan_interface` is the one that carries the address and stays the primary;
    `lan_extra_interfaces` adds the rest. Order matters: the primary is where
    the address goes back to when the bridge is torn down.
    """
    primary = (m.get("lan_interface") or "").strip()
    extras = [
        p for p in (m.get("lan_extra_interfaces") or "").replace(",", " ").split()
        if p and p != primary
    ]
    seen, out = set(), []
    for p in ([primary] if primary else []) + extras:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _lan_addressing(lan: str, m: dict) -> tuple[str, int]:
    """(address, prefix length) for the LAN side.

    Prefers what the interface actually has — router mode can't invent an
    address the kernel doesn't hold. The address belongs to the LAN rather
    than to one port of it, so the search widens from the nominated primary:
    to the bridge while one is up, and then to the other members. Tearing a
    bridge down leaves the address on a WIRED member (a radio goes down with
    hostapd and could not keep it), which is not necessarily the port named
    as primary — and demanding the operator move it back by hand before the
    next apply would be a rake laid by our own teardown.
    """
    candidates = [lan]
    try:
        if wifi_mod.bridge_exists():
            candidates.append(wifi_mod.BRIDGE_NAME)
    except Exception:  # noqa: BLE001 — a probe must not be what fails
        pass
    candidates += [p for p in _lan_members(m) if p != lan]

    ip = prefix = None
    for cand in candidates:
        ip, prefix = nc.read_interface_address(cand)
        if ip and prefix is not None:
            if cand != lan:
                logger.info(
                    "LAN address %s/%s found on %s rather than the primary %s",
                    ip, prefix, cand, lan,
                )
            break
    if not ip or prefix is None:
        raise RouterModeError(
            f"LAN port '{lan}' has no IPv4 address. Give it a static address "
            f"before enabling router mode — it has to be the gateway the LAN "
            f"talks to."
        )
    return ip, int(prefix)


# Ports nginx publishes for the panel. Opening the uplink for "the admin
# interface" has to mean something specific, and this is it.
_PANEL_PORTS = (80, 443)
_SSH_PORT = 22


def _parse_ports(raw: str) -> list[int]:
    """Comma/space separated port numbers, ignoring anything that isn't one."""
    out: set[int] = set()
    for chunk in (raw or "").replace(",", " ").split():
        try:
            port = int(chunk)
        except ValueError:
            continue
        if 0 < port < 65536:
            out.add(port)
    return sorted(out)


def _wan_allowed_ports(m: dict) -> tuple[list[int], list[int]]:
    """(tcp, udp) ports to accept on the uplink.

    Both toggles are off by default and both are ordinary settings, so they can
    be turned on BEFORE the switch to router mode — which is the only moment
    they are useful. Deciding you want SSH on the uplink after the uplink has
    stopped accepting SSH means finding a cable.
    """
    tcp = set(_parse_ports(m.get("wan_allow_tcp", "")))
    if (m.get("wan_admin_access") or "false").lower() == "true":
        tcp |= set(_PANEL_PORTS)
    if (m.get("wan_ssh_access") or "false").lower() == "true":
        tcp.add(_SSH_PORT)
    return sorted(tcp), _parse_ports(m.get("wan_allow_udp", ""))


def _refuse_public_wan_exposure(wan: str, tcp: list[int], udp: list[int]) -> None:
    """Stop the uplink being opened when it faces the actual internet.

    Publishing the panel on the WAN is a reasonable thing to want when PiTun
    sits behind another router — that "WAN" is the home network, and it is
    where the operator already is. On a public address the same switch puts
    the login page, and anything else listed, in front of the whole internet.

    The distinction is the address, not the operator's intent, so it is
    enforced rather than warned about. An address we cannot read yet (DHCP
    still in flight) is not proof of anything and doesn't block the apply.
    """
    if not (tcp or udp):
        return
    ip, _ = nc.read_interface_address(wan)
    if not ip:
        logger.warning(
            "WAN ports %s/%s opened before %s has an address — cannot check "
            "whether the uplink is public", tcp, udp, wan,
        )
        return
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return
    # `is_global` is the question being asked — can the internet reach this —
    # rather than a list of private ranges to keep in sync. It also gets
    # carrier-grade NAT right: 100.64/10 is not globally reachable, and a box
    # behind the ISP's NAT is in the same position as one behind a router.
    if not addr.is_global:
        return
    raise RouterModeError(
        f"The uplink address {ip} is a public one, so opening ports on it "
        f"would expose them to the internet — not just to your own network. "
        f"Refusing to open TCP {tcp or '-'} / UDP {udp or '-'}. This setting "
        f"is meant for a PiTun that sits behind another router; reach the "
        f"panel from the LAN side instead."
    )


def effective_wan(m: dict) -> str:
    """The interface the uplink's traffic actually leaves by.

    PPPoE moves it to a ppp link and a VLAN tag to `eth0.<id>`, so the port in
    the settings is not the one the firewall, the counters or the route are
    attached to. Prefers the ppp link the kernel reports over the name we
    would have guessed — a session that came up as ppp1 is still the uplink.
    """
    wan = (m.get("wan_interface") or "").strip()
    if not wan:
        return ""
    mode = (m.get("wan_mode") or "dhcp").strip()
    if mode == "pppoe":
        try:
            return wan_mod.live_pppoe_interface() or "ppp0"
        except Exception:  # noqa: BLE001 — fall back to the configured name
            return "ppp0"
    try:
        vlan = int(m.get("wan_vlan_id") or 0)
    except (TypeError, ValueError):
        vlan = 0
    return f"{wan}.{vlan}" if vlan else wan


def _safe_lease_name(raw: str) -> str:
    """A dhcp-host name dnsmasq will accept, or empty."""
    import re
    return re.sub(r"[^A-Za-z0-9-]", "-", raw).strip("-")[:32]


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
    try:
        # After hostapd, so the radio has already left the bridge, and after
        # the firewall, which matched on the bridge name. Leaving the bridge
        # behind would strand the LAN address on an interface nothing routes.
        steps["lan_bridge"] = wifi_mod.dissolve_lan_bridge()["removed"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("Teardown: could not dissolve the LAN bridge: %s", exc)
        steps["lan_bridge"] = f"error: {exc}"
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

    lan_members = _lan_members(m)
    present = {i["name"] for i in nc.list_interfaces()}
    for role, value in [("WAN", wan)] + [("LAN", p) for p in lan_members]:
        if value not in present:
            raise RouterModeError(f"{role} port '{value}' is not present on this box.")
    if wan in lan_members:
        raise RouterModeError(
            f"'{wan}' is assigned to both the uplink and the LAN. One port "
            f"cannot be both sides of the router."
        )

    lan_address, lan_prefix = _lan_addressing(lan_members[0], m)
    lan_cidr = str(__import__("ipaddress").ip_network(
        f"{lan_address}/{lan_prefix}", strict=False))

    # More than one LAN port means they have to be one segment: same subnet,
    # one DHCP scope, clients able to see each other. That is a bridge, and
    # everything downstream — the firewall, dnsmasq, the address itself —
    # must name the bridge rather than any single port. A wireless member is
    # NOT enslaved here; hostapd joins it via `bridge=`, and doing it from
    # both sides races.
    wired_lan = [p for p in lan_members if not nc.is_wireless(p)]
    bridged = len(lan_members) > 1
    if bridged and not wired_lan:
        raise RouterModeError(
            "A LAN of several ports needs at least one wired one to build the "
            "bridge on."
        )
    lan_link = wifi_mod.BRIDGE_NAME if bridged else lan_members[0]

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
            # dnsmasq's dhcp-host is a comma-separated line-oriented field
            # and the name is attacker-influenced — it comes from the DHCP
            # hostname or mDNS. A comma adds a field, a '#' comments the rest
            # of the line, a newline injects a directive; all three stop
            # dnsmasq from starting, which takes DHCP down for the whole LAN
            # as leases expire.
            name=_safe_lease_name(d.name or d.hostname or ""),
        )
        for d in reserved if d.mac and d.dhcp_reserved_ip
    ]

    dhcp_cfg = dhcp_mod.DhcpConfig(
        interface=lan_link, lan_cidr=lan_cidr, lan_address=lan_address,
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
    # Provisional: replaced by what apply() actually produced when the
    # uplink has to be built (PPPoE in particular).
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
            # Take the interface apply() reports rather than the pre-computed
            # guess: for PPPoE the link only exists once the session is up, and
            # the firewall matches on the NAME, so binding to the wrong one
            # loads cleanly and protects nothing.
            wan_iface = wan_mod.apply(wan_cfg).get("interface") or wan_iface
            applied.append("wan")

        if not set_ip_forward(True):
            raise RouterModeError("Could not enable IPv4 forwarding on the host.")
        applied.append("ip_forward")

        # Checked against the interface we actually ended up on: with PPPoE or
        # a VLAN the address lives somewhere other than the port in the config,
        # and the public/private question is about the address that faces out.
        # Before NAT and before anything binds: the firewall matches on the
        # interface NAME, and dnsmasq and hostapd both have to be handed the
        # bridge rather than a port that is about to become a bridge member.
        if bridged:
            wifi_mod.create_lan_bridge(
                wired_lan, f"{lan_address}/{lan_prefix}",
                # The radio keeps no address of its own: hostapd will put it
                # in the bridge, and until then the gateway address must live
                # in exactly one place.
                also_flush=[p for p in lan_members if p not in wired_lan],
            )
            applied.append("lan_bridge")

        wan_tcp, wan_udp = _wan_allowed_ports(m)
        _refuse_public_wan_exposure(wan_iface, wan_tcp, wan_udp)

        if not await nft.apply_router_nat(
            wan_iface, lan_link, wan_allow_tcp=wan_tcp, wan_allow_udp=wan_udp,
        ):
            raise RouterModeError("Could not apply the router firewall/NAT rules.")
        applied.append("nat")

        # Both branches matter. Without the else, turning a subsystem off in
        # the panel returned 200 while the daemon kept running — so "disable
        # WiFi" left hostapd broadcasting, which is exactly what an operator
        # does when rotating a leaked passphrase.
        if (m.get("dhcp_enabled") or "true").lower() == "true":
            await dhcp_mod.start(dhcp_cfg)
            applied.append("dhcp")
        else:
            await dhcp_mod.stop()

        # WiFi last: it's the only step that can take the LAN interface down
        # while reconfiguring the radio, so everything else is already up if
        # it fails and the rollback has less to undo.
        if (m.get("wifi_enabled") or "false").lower() == "true":
            radio = next((p for p in lan_members if nc.is_wireless(p)), "")
            if not radio:
                raise RouterModeError(
                    f"WiFi is enabled but no LAN port is a wireless adapter "
                    f"({', '.join(lan_members)}). Add the radio to the LAN, or "
                    f"turn WiFi off."
                )
            wifi_cfg = wifi_mod.WifiConfig(
                interface=radio,
                ssid=m.get("wifi_ssid", ""),
                passphrase=m.get("wifi_passphrase", ""),
                country=(m.get("wifi_country") or "").upper(),
                band=m.get("wifi_band", "2.4"),
                channel=int(m.get("wifi_channel") or 0),
                security=m.get("wifi_security", "wpa2"),
                hidden=(m.get("wifi_hidden") or "false").lower() == "true",
                # hostapd puts the AP into the bridge itself. Enslaving it from
                # our side as well races with hostapd bringing the interface up,
                # and the loser silently leaves wireless clients on their own
                # segment — same SSID, no route to the wired half.
                bridge=wifi_mod.BRIDGE_NAME if bridged else "",
            )
            try:
                await wifi_mod.start(wifi_cfg)
            except wifi_mod.WifiConfigError as exc:
                raise RouterModeError(f"WiFi could not start: {exc}")
            applied.append("wifi")
        else:
            await wifi_mod.stop()
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

async def diagnose_wan(wan: str = "") -> list[dict]:
    """Findings about the uplink, in plain language.

    Each finding: {level, title, detail, hint}. `level` is ok | warn | error.

    `wan` lets the findings account for what the port actually holds. Without
    it every check can only report packet counters, and counters alone can't
    tell a broken uplink from a working one that simply hasn't renewed its
    lease yet — which produced a permanent scary warning on a healthy link.
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
    wan_ip = ""
    if wan:
        try:
            wan_ip = (nc.read_interface_address(wan)[0] or "")
        except Exception:  # noqa: BLE001 — a diagnosis must not fail to render
            wan_ip = ""

    if dhcp_in == 0 and wan_ip:
        # The counter starts at zero when the ruleset is applied, and a lease
        # obtained before that is renewed only at half its lifetime — often
        # hours away. Reporting "no DHCP replies" for a port that visibly holds
        # an address sends people to check a cable that is plainly fine.
        findings.append({
            "level": "ok",
            "title": f"The uplink has an address ({wan_ip})",
            "detail": (
                "No DHCP reply has been counted yet, which is expected: the "
                "counter starts when the firewall is applied, and an existing "
                "lease is not renewed until roughly halfway through its life. "
                "A static or PPPoE uplink never counts one at all."
            ),
            "hint": "",
        })
    elif dhcp_in == 0:
        findings.append({
            "level": "warn",
            "title": "No DHCP replies have arrived on the WAN port",
            "detail": (
                "The port has no IPv4 address and nothing has come back from an "
                "upstream DHCP server since the firewall was applied. If the "
                "uplink is meant to get its address by DHCP, this is the cause "
                "to look at first."
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
    if icmp_in == 0 and wan_ip:
        # Zero is the normal reading on a healthy link: this counts ICMP
        # arriving from the uplink, and a path that never needs to fragment
        # produces none. It only becomes evidence alongside the symptom.
        findings.append({
            "level": "ok",
            "title": "No ICMP has arrived on the WAN port yet",
            "detail": (
                "Nothing to report — a path that never has to fragment sends "
                "no ICMP back. This becomes worth looking at only if large "
                "transfers hang while small requests work: pages that "
                "half-load and downloads that stall, rather than a clean "
                "error. That is a path-MTU black hole, and it shows up here "
                "as this staying at zero while the symptom is present."
            ),
            "hint": "",
        })
    elif icmp_in == 0:
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

    # 3. Is the uplink actually carrying anything?
    #
    # A default route that leaves by some other port means the ISP link is up,
    # NAT and the firewall are attached to it, and not one packet uses it —
    # traffic goes out whatever else still has a gateway. Everything above
    # reads healthy while the router does nothing, which is the hardest kind
    # of fault to spot from the inside. Seen on the PPPoE rig, where the
    # physical port kept a lower-metric route than the tunnel.
    live_iface, _ = nc.read_default_route()
    if wan and live_iface and live_iface != wan:
        findings.append({
            "level": "warn",
            "title": f"Traffic is leaving by {live_iface}, not the uplink",
            "detail": (
                f"Router mode has the firewall and NAT on '{wan}', but the "
                f"default route points out of '{live_iface}'. Nothing is using "
                f"the uplink, so its rules and counters describe a path no "
                f"packet takes — and traffic is leaving by a port that is not "
                f"meant to be routing."
            ),
            "hint": (
                "Usually a leftover gateway on another port: the LAN side "
                "should have an address but no default route."
            ),
        })

    # Where the uplink points changes what these drops mean. On a public
    # address they're background scanning and can be ignored; behind another
    # router they're usually something on that network trying to reach PiTun —
    # including the operator's own SSH or panel, which is worth saying plainly
    # rather than leaving them to wonder why the box stopped answering.
    private_wan = False
    if wan_ip:
        try:
            import ipaddress
            private_wan = ipaddress.ip_address(wan_ip).is_private
        except ValueError:
            private_wan = False

    findings.append({
        "level": "ok",
        "title": f"{blocked} unsolicited packets dropped on the WAN port",
        "detail": (
            "Connection attempts refused on the uplink. This port sits on a "
            "private network, so these are most likely devices on that network "
            "trying to reach PiTun — an SSH session or the panel from the old "
            "address — rather than scanning. Reach the box from the LAN side "
            "instead."
            if private_wan else
            "Connection attempts from the WAN that were refused. A steady count "
            "here is ordinary background scanning, not a problem."
        ),
        "hint": "",
    })
    return findings
