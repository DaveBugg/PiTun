"""nftables TPROXY rules management for Linux transparent proxy."""
import asyncio
import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from app.config import settings


@dataclass(frozen=True)
class RoutingSetSpec:
    """Per-RoutingSet nftables/TPROXY plumbing spec (v1.4).

    Built by `app/api/system.py:_apply_nftables` from the DB state and
    passed to `NftablesManager.apply()`. Each spec generates:
      * An `inet pitun` set named `rset_<set_id>_mac` with member MACs.
      * Two TPROXY redirect rules in `prerouting` (TCP+UDP) that fire
        BEFORE the default TPROXY rule, sending matched packets to
        `127.0.0.1:<tproxy_port>` where xray's per-set inbound listens.
    """
    set_id: int
    macs: tuple[str, ...]
    tproxy_port: int

logger = logging.getLogger(__name__)

_TABLE = "pitun"
# Router-mode NAT/forward lives in its OWN table so it has an independent
# lifecycle and cannot interfere with the TPROXY table above: applying or
# tearing down one never touches the other.
_ROUTER_TABLE = "pitun_router"

# Private IPv4 ranges to bypass
_PRIVATE_RANGES = [
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10",
    "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4",
]

_MAC_RE = re.compile(r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$")
_CIDR_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}(/\d{1,2})?$")


def _validate_mac(mac: str) -> bool:
    return bool(_MAC_RE.match(mac.strip()))


def _validate_cidr(cidr: str) -> bool:
    return bool(_CIDR_RE.match(cidr.strip()))


async def _run_exec(*args: str) -> tuple[int, str, str]:
    """Run a command without shell (safe from injection)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode or 0, stdout.decode(), stderr.decode()


async def _nft(script: str) -> bool:
    """Apply an nft script via stdin pipe (no shell, no heredoc)."""
    proc = await asyncio.create_subprocess_exec(
        "nft", "-f", "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=script.encode())
    if proc.returncode != 0:
        logger.error("nft error: %s", stderr.decode())
        return False
    return True


class NftablesManager:
    """Manage the pitun nftables table for TPROXY."""

    async def is_active(self) -> bool:
        rc, out, _ = await _run_exec("nft", "list", "table", "inet", _TABLE)
        return rc == 0 and _TABLE in out

    async def flush_rules(self) -> bool:
        """Remove the pitun nftables table entirely (alias for flush)."""
        return await self.flush()

    async def apply_rules(
        self,
        inbound_mode: str = "tproxy",
        bypass_macs: Optional[List[str]] = None,
        bypass_dst_cidrs: Optional[List[str]] = None,
        proxy_src_cidrs: Optional[List[str]] = None,
        include_macs: Optional[List[str]] = None,
        device_routing_mode: str = "all",
        tproxy_tcp: Optional[int] = None,
        tproxy_udp: Optional[int] = None,
        dns_port: Optional[int] = None,
        block_quic: bool = True,
        kill_switch: bool = False,
        routing_set_specs: Optional[Sequence[RoutingSetSpec]] = None,
    ) -> bool:
        """
        Apply nftables rules based on inbound_mode.

        inbound_mode "tun"   — flush any existing TPROXY rules, xray TUN handles routing
        inbound_mode "tproxy" or "both" — apply TPROXY rules as normal
        """
        if inbound_mode == "tun":
            if kill_switch:
                await self.apply_tun_fallback(bypass_dst_cidrs=bypass_dst_cidrs)
            else:
                await self.flush()
            logger.info("TUN mode: kill_switch=%s", kill_switch)
            return True
        return await self.apply(
            bypass_macs=bypass_macs,
            bypass_dst_cidrs=bypass_dst_cidrs,
            proxy_src_cidrs=proxy_src_cidrs,
            include_macs=include_macs,
            device_routing_mode=device_routing_mode,
            tproxy_tcp=tproxy_tcp,
            tproxy_udp=tproxy_udp,
            dns_port=dns_port,
            block_quic=block_quic,
            routing_set_specs=routing_set_specs,
        )

    async def apply(
        self,
        bypass_macs: Optional[List[str]] = None,
        bypass_dst_cidrs: Optional[List[str]] = None,
        proxy_src_cidrs: Optional[List[str]] = None,
        include_macs: Optional[List[str]] = None,
        device_routing_mode: str = "all",
        tproxy_tcp: Optional[int] = None,
        tproxy_udp: Optional[int] = None,
        dns_port: Optional[int] = None,
        block_quic: bool = True,
        routing_set_specs: Optional[Sequence[RoutingSetSpec]] = None,
    ) -> bool:
        """
        Create or replace the pitun nftables table with TPROXY rules.

        bypass_macs          – MAC addresses that bypass the proxy (routing rules + exclude_list)
        bypass_dst_cidrs     – destination CIDRs that go direct (added to private ranges)
        proxy_src_cidrs      – only these source IPs are proxied (whitelist mode, None = all)
        include_macs         – only these MACs are proxied (include_only mode)
        device_routing_mode  – "all" | "include_only" | "exclude_list"
        """
        tcp_port = tproxy_tcp or settings.tproxy_port_tcp
        udp_port = tproxy_udp or settings.tproxy_port_udp
        dns_p = dns_port or settings.dns_port

        if bypass_macs:
            bypass_macs = [m for m in bypass_macs if _validate_mac(m)]
        if bypass_dst_cidrs:
            bypass_dst_cidrs = [c for c in bypass_dst_cidrs if _validate_cidr(c)]
        if proxy_src_cidrs:
            proxy_src_cidrs = [c for c in proxy_src_cidrs if _validate_cidr(c)]
        if include_macs:
            include_macs = [m for m in include_macs if _validate_mac(m)]

        all_bypass = list(_PRIVATE_RANGES)
        if bypass_dst_cidrs:
            all_bypass.extend(bypass_dst_cidrs)
        bypass_dst_elements = ", ".join(all_bypass)

        mac_elements = ""
        mac_match = ""
        if bypass_macs:
            mac_list = ", ".join(bypass_macs)
            mac_elements = f"\n        elements = {{ {mac_list} }}"
            mac_match = "        ether saddr @bypass_mac return\n"

        # include_only mode: only proxy traffic from these MACs
        include_set = ""
        include_match = ""
        if device_routing_mode == "include_only" and include_macs:
            inc_list = ", ".join(include_macs)
            include_set = f"""
    set include_mac {{
        type ether_addr
        elements = {{ {inc_list} }}
    }}"""
            include_match = "        ether saddr != @include_mac return\n"

        proxy_src_elements = ""
        proxy_src_match = ""
        if proxy_src_cidrs:
            src_list = ", ".join(proxy_src_cidrs)
            proxy_src_elements = f"""
    set proxy_src4 {{
        type ipv4_addr
        flags interval
        elements = {{ {src_list} }}
    }}"""
            proxy_src_match = "        ip saddr != @proxy_src4 return\n"

        # Per-RoutingSet MAC sets + TPROXY redirect rules (v1.4).
        # For each non-empty RoutingSet, render:
        #   1. An nft set `rset_<id>_mac` holding the device MACs
        #   2. Two TPROXY rules in `prerouting` that match those MACs
        #      and redirect TCP/UDP to the set's xray inbound port,
        #      injected BEFORE the default TPROXY rules so first-match
        #      semantics route set members into their own xray inbound.
        #
        # Empty sets are filtered out at the call-site (see
        # `app/api/system.py:_apply_nftables`); this code defends with
        # an extra `if validated_macs` check so a stale empty list
        # can't generate an `elements = {  }` nftables syntax error.
        per_set_definitions = ""
        per_set_matches = ""
        if routing_set_specs:
            for spec in routing_set_specs:
                validated_macs = [m for m in spec.macs if _validate_mac(m)]
                if not validated_macs:
                    continue
                mac_list = ", ".join(validated_macs)
                per_set_definitions += f"""
    set rset_{spec.set_id}_mac {{
        type ether_addr
        elements = {{ {mac_list} }}
    }}"""
                # Both TCP and UDP redirect to the SAME port — xray's
                # dokodemo-door inbound bound with `network: "tcp,udp"`
                # accepts both protocols on one numeric port (TCP/UDP
                # are independent socket families in the kernel, so a
                # single port number can be bound by both at once).
                #
                # The trailing `accept` is CRITICAL. Without it, the
                # tproxy verb in nftables doesn't terminate chain
                # evaluation — the packet's TPROXY assignment and
                # fwmark get OVERWRITTEN by the default TPROXY rule
                # below in the same chain, silently routing per-set
                # device traffic into the default `tproxy-tcp` inbound.
                # nft trace confirmed this on the v1.4 dev build (1.3
                # smoke test): per-set rule fired, packet got mark=1,
                # but then default rule fired too with the SAME action
                # → last writer wins → packet landed on :7893 instead
                # of the per-set port. The default rules below don't
                # need `accept` because they're the last tproxy in the
                # chain — nothing after them rewrites the redirect.
                per_set_matches += (
                    f"        ether saddr @rset_{spec.set_id}_mac ip protocol tcp "
                    f"tproxy ip to 127.0.0.1:{spec.tproxy_port} meta mark set 1 accept\n"
                    f"        ether saddr @rset_{spec.set_id}_mac ip protocol udp "
                    f"tproxy ip to 127.0.0.1:{spec.tproxy_port} meta mark set 1 accept\n"
                )

        script = f"""
table inet {_TABLE} {{
    set bypass_mac {{
        type ether_addr{mac_elements}
    }}
    set bypass_dst4 {{
        type ipv4_addr
        flags interval
        elements = {{ {bypass_dst_elements} }}
    }}{include_set}{proxy_src_elements}{per_set_definitions}

    chain prerouting {{
        type filter hook prerouting priority mangle - 1; policy accept;
        # Skip already-marked traffic (our own outbound)
        meta mark 255 return
{mac_match}{include_match}{proxy_src_match}        # Skip private / bypass destinations
        ip daddr @bypass_dst4 return
        # DNS redirect to xray DNS inbound. MUST trailing-`accept` so the
        # chain terminates here — otherwise the per-set TPROXY rule below
        # (which has its own `accept`) would re-route DNS packets from
        # set members into the per-set inbound instead of `dns-in`.
        # xray's DNS rules are gated on `inboundTag: [dns-in, dns-in-53]`,
        # so DNS that lands on a per-set inbound silently bypasses the
        # whole DNS engine (no upstream override, no DoH/DoT, no
        # per-domain server) and gets proxied as raw UDP — caught
        # by an operator on 1.3 (DNS rule for *.youtube.com → DoT
        # 94.140.14.14 not applying to devices in the test set).
        ip protocol tcp tcp dport 53 tproxy ip to 127.0.0.1:{dns_p} meta mark set 1 accept
        ip protocol udp udp dport 53 tproxy ip to 127.0.0.1:{dns_p} meta mark set 1 accept
        {"# Reject QUIC (UDP/443) — browsers immediately fall back to TCP" if block_quic else "# QUIC blocking disabled"}
        {"ip protocol udp udp dport 443 reject" if block_quic else ""}
        # Per-RoutingSet TPROXY (v1.4) — BEFORE the default TPROXY so
        # set members get redirected into their own xray inbound
        # (matched there by xray router via `inboundTag`).
{per_set_matches}        # TCP TPROXY (default — all other devices)
        ip protocol tcp tproxy ip to 127.0.0.1:{tcp_port} meta mark set 1
        # UDP TPROXY (non-QUIC)
        ip protocol udp tproxy ip to 127.0.0.1:{udp_port} meta mark set 1
    }}

    chain output {{
        type route hook output priority mangle; policy accept;
        meta mark 255 return
        ip daddr @bypass_dst4 return
        ip protocol tcp meta mark set 1
        ip protocol udp meta mark set 1
    }}
}}
"""
        # Delete existing table first (idempotent)
        await _run_exec("nft", "delete", "table", "inet", _TABLE)
        ok = await _nft(script)
        if ok:
            # ip rule for TPROXY: packets with mark 1 go to local routing table 100
            await _run_exec("ip", "rule", "del", "fwmark", "1", "lookup", "100")
            await _run_exec("ip", "rule", "add", "fwmark", "1", "lookup", "100")
            await _run_exec("ip", "route", "replace", "local", "0.0.0.0/0", "dev", "lo", "table", "100")
            logger.info("nftables TPROXY rules applied")
        return ok

    async def apply_kill_switch(
        self,
        bypass_dst_cidrs: Optional[List[str]] = None,
        vpn_server_ips: Optional[List[str]] = None,
    ) -> bool:
        """
        Kill switch: DROP all non-local traffic.
        LAN stays accessible, but internet is blocked until proxy is back.
        VPN server IPs are whitelisted so xray can reconnect.
        """
        all_bypass = list(_PRIVATE_RANGES)
        if bypass_dst_cidrs:
            all_bypass.extend([c for c in bypass_dst_cidrs if _validate_cidr(c)])
        bypass_elements = ", ".join(all_bypass)

        vpn_elements = ""
        vpn_match = ""
        if vpn_server_ips:
            valid_ips = [ip for ip in vpn_server_ips if _validate_cidr(ip)]
            if valid_ips:
                vpn_elements = f"""
    set vpn_servers {{
        type ipv4_addr
        flags interval
        elements = {{ {", ".join(valid_ips)} }}
    }}"""
                vpn_match = "        ip daddr @vpn_servers accept\n"

        script = f"""
table inet {_TABLE} {{
    set bypass_dst4 {{
        type ipv4_addr
        flags interval
        elements = {{ {bypass_elements} }}
    }}{vpn_elements}

    chain prerouting {{
        type filter hook prerouting priority mangle - 1; policy accept;
        # Kill switch: allow local, drop everything else
        ip daddr @bypass_dst4 accept
{vpn_match}        # DROP all other traffic — kill switch active
        drop
    }}

    chain output {{
        type route hook output priority mangle; policy accept;
        ip daddr @bypass_dst4 accept
{vpn_match}        drop
    }}
}}
"""
        await _run_exec("nft", "delete", "table", "inet", _TABLE)
        ok = await _nft(script)
        if ok:
            logger.warning("KILL SWITCH ACTIVE — all non-local traffic blocked")
        return ok

    async def apply_tun_fallback(
        self,
        bypass_dst_cidrs: Optional[List[str]] = None,
    ) -> bool:
        """
        TUN mode kill switch fallback.
        When xray TUN is running, autoRoute handles everything.
        But if xray crashes and tun0 disappears, this rule catches traffic
        and DROPs it instead of leaking direct through the default route.

        Much simpler than full TPROXY rules — just a safety net.
        """
        all_bypass = list(_PRIVATE_RANGES)
        if bypass_dst_cidrs:
            all_bypass.extend([c for c in bypass_dst_cidrs if _validate_cidr(c)])
        bypass_elements = ", ".join(all_bypass)

        script = f"""
table inet {_TABLE} {{
    set bypass_dst4 {{
        type ipv4_addr
        flags interval
        elements = {{ {bypass_elements} }}
    }}

    chain forward {{
        type filter hook forward priority filter; policy accept;
        # Allow LAN / private destinations
        ip daddr @bypass_dst4 accept
        # If tun0 exists, xray handles routing — let it through
        iifname "xray0" accept
        oifname "xray0" accept
        # Otherwise DROP — tun0 is down, don't leak
        drop
    }}
}}
"""
        await _run_exec("nft", "delete", "table", "inet", _TABLE)
        ok = await _nft(script)
        if ok:
            logger.info("TUN fallback kill switch applied (forward chain)")
        return ok

    async def flush(self) -> bool:
        """Remove the pitun nftables table entirely."""
        rc, _, err = await _run_exec("nft", "delete", "table", "inet", _TABLE)
        await _run_exec("ip", "rule", "del", "fwmark", "1", "lookup", "100")
        await _run_exec("ip", "route", "del", "local", "0.0.0.0/0", "dev", "lo", "table", "100")
        await _run_exec("nft", "delete", "table", "ip", "pitun_dns")  # legacy cleanup
        if rc != 0 and "No such" not in err:
            logger.warning("nft flush error: %s", err)
            return False
        logger.info("nftables rules flushed")
        return True

    async def update_bypass_macs(self, macs: List[str]) -> bool:
        """Update the bypass_mac set without full rule reload."""
        if not macs:
            script = f"flush set inet {_TABLE} bypass_mac"
        else:
            macs = [m for m in macs if _validate_mac(m)]
            if not macs:
                script = f"flush set inet {_TABLE} bypass_mac"
                return await _nft(script)
            elements = ", ".join(macs)
            script = f"""
table inet {_TABLE} {{
    set bypass_mac {{
        type ether_addr
        elements = {{ {elements} }}
    }}
}}
"""
        return await _nft(script)

    async def update_bypass_dsts(self, cidrs: List[str]) -> bool:
        """Replace the bypass_dst4 set."""
        cidrs = [c for c in cidrs if _validate_cidr(c)]
        all_cidrs = list(_PRIVATE_RANGES) + cidrs
        elements = ", ".join(all_cidrs)
        script = f"""
table inet {_TABLE} {{
    set bypass_dst4 {{
        type ipv4_addr
        flags interval
        elements = {{ {elements} }}
    }}
}}
"""
        return await _nft(script)


nftables_manager = NftablesManager()


# ── Router mode: NAT + forward filter ────────────────────────────────────────
#
# Only used when `operating_mode = router`. In the default gateway mode the
# upstream router does this and these chains are absent entirely.
#
# Deliberately a separate table (`inet pitun_router`): the TPROXY table is
# load-bearing for the proxy and is rewritten on every rule/DNS/settings edit,
# so entangling the router dataplane with it would mean one bad router apply
# could take out proxying too.

_IFACE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,15}$")


def _validate_iface(name: str) -> bool:
    """Interface names go into an nft script, so constrain them tightly.
    Linux caps names at IFNAMSIZ-1 = 15 chars."""
    return bool(name and _IFACE_RE.match(name))


async def apply_router_nat(
    wan: str,
    lan: str,
    *,
    wan_allow_tcp: Sequence[int] = (),
    wan_allow_udp: Sequence[int] = (),
) -> bool:
    """Masquerade LAN→WAN and filter forwarding, statefully.

    The forward chain defaults to DROP: a router that forwards everything by
    default is not a router, it's a hole. Return traffic is allowed by
    conntrack state rather than by an open policy, so the WAN side can't
    initiate into the LAN.

    The input chain gives the uplink a clean posture: **nothing new arrives
    from the internet**. Only replies to connections the box itself opened are
    accepted, so every local service — the panel, SSH, and xray's DNS / SOCKS /
    HTTP inbounds, all of which bind 0.0.0.0 — is reachable over the LAN and
    invisible from the WAN. That matters because gateway mode had no
    internet-facing interface at all, so binding everything to 0.0.0.0 was
    harmless; an uplink turns the same listeners into an open resolver (abused
    for DNS amplification) and open proxies.

    It's a blanket `ct state new drop` on the WAN rather than a list of ports
    to close, because a list has to be maintained: add a service later, forget
    its port, and it's exposed. The blanket rule covers whatever we add next.

    Two exceptions are required for the uplink itself to work: DHCP-client
    replies (they arrive as NEW, not RELATED, so conntrack won't admit them),
    and the ICMP types PMTU discovery and traceroute rely on — dropping those
    produces connections that hang rather than fail. `wan_allow_tcp` /
    `wan_allow_udp` open specific ports when something is deliberately
    published.

    The chain's policy stays ACCEPT and every drop is scoped to `iifname wan`:
    the LAN path is untouched, so tightening the uplink cannot lock the
    operator out of a box they administer over the LAN.
    """
    if not (_validate_iface(wan) and _validate_iface(lan)):
        logger.error("Router NAT: invalid interface names (wan=%r lan=%r)", wan, lan)
        return False
    if wan == lan:
        logger.error("Router NAT: wan and lan must differ (both %r)", wan)
        return False

    tcp_ports = sorted({int(p) for p in wan_allow_tcp if 0 < int(p) < 65536})
    udp_ports = sorted({int(p) for p in wan_allow_udp if 0 < int(p) < 65536})
    rules = [
        "        # The uplink brings its own address up over DHCP, and those",
        "        # replies arrive as NEW — conntrack alone would not admit them.",
        f'        iifname "{wan}" udp sport 67 udp dport 68 counter name "wan_dhcp_in" accept',
        "        # PMTU discovery and traceroute fail by hanging rather than",
        "        # erroring if these are dropped, which is miserable to debug.",
        f'        iifname "{wan}" icmp type {{ echo-request, destination-unreachable, time-exceeded, parameter-problem }} counter name "wan_icmp_in" accept',
    ]
    if tcp_ports:
        rules.append(
            f'        iifname "{wan}" tcp dport {{ {", ".join(map(str, tcp_ports))} }} accept'
        )
    if udp_ports:
        rules.append(
            f'        iifname "{wan}" udp dport {{ {", ".join(map(str, udp_ports))} }} accept'
        )
    rules += [
        "        # Everything else arriving from the internet is refused. Every",
        "        # local service — panel, SSH, xray's DNS/SOCKS/HTTP — stays",
        "        # reachable over the LAN and invisible from the WAN.",
        f'        iifname "{wan}" ct state new counter name "wan_blocked" drop',
    ]
    wan_service_rules = "\n".join(rules)

    script = f"""
table inet {_ROUTER_TABLE} {{
    # Named counters so the two silent failure modes are observable rather
    # than guessed at: a WAN that never gets an address (no DHCP replies) and
    # a PMTU black hole (no ICMP getting back in) look identical from the
    # outside — "the internet is broken" — without these.
    counter wan_dhcp_in {{}}
    counter wan_icmp_in {{}}
    counter wan_blocked {{}}

    chain postrouting {{
        type nat hook postrouting priority srcnat; policy accept;
        # Everything leaving the uplink is rewritten to the box's WAN address.
        oifname "{wan}" masquerade
    }}

    chain input {{
        type filter hook input priority filter; policy accept;
        ct state established,related accept
        ct state invalid drop
{wan_service_rules}
    }}

    chain forward {{
        type filter hook forward priority filter; policy drop;
        # Replies and related flows for connections the LAN opened.
        ct state established,related accept
        ct state invalid drop
        # LAN out to the internet, and LAN talking to itself.
        iifname "{lan}" oifname "{wan}" accept
        iifname "{lan}" oifname "{lan}" accept
        # The panel itself. PiTun's web UI is an nginx container with published
        # ports, so a LAN request to this box's own address is DNAT'd to the
        # container and then traverses FORWARD — not INPUT. Without this the
        # first apply cuts every LAN device off the panel, which also means
        # nobody can press the confirm button and the watchdog reverts a
        # working router purely because we blocked the way to its own UI.
        iifname "{lan}" ct status dnat accept
        # Container-to-container (the reverse proxy reaching the SPA) once
        # bridge-nf-call-iptables puts bridged traffic through this hook.
        iifname "br-*" oifname "br-*" accept
        iifname "docker0" oifname "docker0" accept
        # Containers reaching the internet (image pulls, panel healthchecks).
        #
        # These four rules are qualified on purpose. As bare `iifname docker0
        # accept` / `oifname docker0 accept` they quietly undid the input
        # chain: a packet arriving on the WAN and DNAT'd to a published port
        # traverses FORWARD, not INPUT, so it matched `oifname docker0` and was
        # accepted. xray binds its DNS, SOCKS and HTTP inbounds to 0.0.0.0, so
        # that was an open resolver and an open proxy — and `wan_blocked` read
        # zero the whole time, because nothing ever reached the drop.
        iifname "docker0" oifname "{wan}" accept
        iifname "br-*" oifname "{wan}" accept
        # xray's tunnel interface, when the proxy is carrying the traffic.
        iifname "xray0" accept
        oifname "xray0" accept
        # Anything else — notably WAN-initiated into the LAN — hits policy drop.
    }}
}}
"""
    # Replace wholesale so a re-apply can't stack duplicate rules.
    await _nft(f"delete table inet {_ROUTER_TABLE}")
    ok = await _nft(script)
    if ok:
        logger.info("Router NAT applied: %s (LAN) -> %s (WAN)", lan, wan)
    else:
        logger.error("Router NAT apply FAILED for %s -> %s", lan, wan)
    return ok


async def remove_router_nat() -> bool:
    """Drop the router table. Idempotent — an absent table is success.

    Returns whether the table is now gone. The previous unconditional `True`
    told teardown the firewall had come down even when nft refused, which is
    the one thing teardown must not lie about. The absent-table case is
    checked first so a gateway-mode box — where this runs on every boot and
    the table never exists — stops logging an nft error for normal operation.
    """
    rc, _out, _err = await _run_exec("nft", "list", "table", "inet", _ROUTER_TABLE)
    if rc != 0:
        return True  # nothing to remove
    ok = await _nft(f"delete table inet {_ROUTER_TABLE}")
    if ok:
        logger.info("Router NAT removed")
    else:
        logger.error("Router NAT table could not be removed — it is still active")
    return ok


async def router_counters() -> dict:
    """Read the WAN-side counters, or {} when router mode isn't applied."""
    rc, out, _ = await _run_exec("nft", "-j", "list", "table", "inet", _ROUTER_TABLE)
    if rc != 0:
        return {}
    import json as _json
    try:
        data = _json.loads(out)
    except ValueError:
        return {}
    counters = {}
    for item in data.get("nftables", []):
        c = item.get("counter")
        if isinstance(c, dict) and "name" in c:
            counters[c["name"]] = {
                "packets": c.get("packets", 0),
                "bytes": c.get("bytes", 0),
            }
    return counters
