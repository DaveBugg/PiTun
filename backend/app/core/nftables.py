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
