"""Generate xray JSON configuration from DB models."""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from sqlmodel import select

from app.config import settings
from app.models import BalancerGroup, Device, DNSRule, Node, RoutingRule, RoutingSet

if TYPE_CHECKING:
    from sqlmodel.ext.asyncio.session import AsyncSession

logger = logging.getLogger(__name__)

# Guards the whole write→validate sequence in `write_config` — every
# writer goes through that one function.
#
# One lock PER EVENT LOOP, not a single module-level lock: an
# `asyncio.Lock` binds itself to the first loop that awaits it and then
# raises "bound to a different event loop" everywhere else. The app runs
# one loop, but helpers and tests call `asyncio.run()`, which makes a
# fresh one each time. Keyed weakly-ish by loop, with closed loops pruned.
_write_locks: "dict[Any, asyncio.Lock]" = {}


def _write_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _write_locks.get(loop)
    if lock is None:
        for dead in [lp for lp in _write_locks if lp.is_closed()]:
            _write_locks.pop(dead, None)
        lock = asyncio.Lock()
        _write_locks[loop] = lock
    return lock


async def collect_routing_set_context(
    session: "AsyncSession",
) -> tuple[list[RoutingSet], dict[int, list[str]]]:
    """Load RoutingSets + device → set MAC mapping from the DB.

    Shared by every callsite that regenerates xray config or applies
    nftables (`api/system.py`, `core/circle_scheduler.py`,
    `core/healthcheck.py`). Returning both pieces in one async pass
    avoids duplicating the query logic across modules and keeps the
    "snapshot of v1.4 per-set state" consistent within a regen tick.

    Returns:
        `(routing_sets, device_set_macs)`:
          * `routing_sets` — every row, ordered by (order, id)
          * `device_set_macs` — `{set_id: [lowercased MACs]}` for
            devices with a non-empty `routing_set_id`. Sets without
            any devices appear with an empty list — callers decide
            whether to skip (config_gen + nftables both do).
    """
    routing_sets = list((await session.exec(
        select(RoutingSet).order_by(RoutingSet.order, RoutingSet.id)
    )).all())
    if not routing_sets:
        return [], {}

    devices = list((await session.exec(
        select(Device).where(Device.routing_set_id.is_not(None))
    )).all())

    device_set_macs: dict[int, list[str]] = {rs.id: [] for rs in routing_sets}
    for dev in devices:
        if not dev.mac:
            continue
        # Defensive: skip devices pointing at a deleted set (SQLite FK
        # enforcement is off by default, the orphan can otherwise leak).
        if dev.routing_set_id not in device_set_macs:
            continue
        device_set_macs[dev.routing_set_id].append(dev.mac.lower())

    return routing_sets, device_set_macs


def _tls_settings(node: Node) -> Optional[Dict[str, Any]]:
    if node.tls == "none":
        return None

    if node.tls == "reality":
        # Reality has its own top-level structure — no nested "reality" key
        reality: Dict[str, Any] = {
            "serverName": node.sni or node.address,
            "fingerprint": node.fingerprint or "chrome",
            "publicKey": node.reality_pbk or "",
            "shortId": node.reality_sid or "",
        }
        if node.reality_spx:
            reality["spiderX"] = node.reality_spx
        return reality

    # Standard TLS
    tls: Dict[str, Any] = {
        "enabled": True,
        "serverName": node.sni or node.address,
        "allowInsecure": node.allow_insecure,
    }
    if node.fingerprint:
        tls["fingerprint"] = node.fingerprint
    if node.alpn:
        tls["alpn"] = [a.strip() for a in node.alpn.split(",")]

    return tls


def _stream_settings(node: Node) -> Dict[str, Any]:
    # A node imported before the schema learned Xray's "raw" alias may be
    # stored with transport="raw". It is the "tcp" transport under a newer
    # name; emit "tcp" so every bundled xray version accepts the config.
    network = "tcp" if node.transport == "raw" else node.transport
    stream: Dict[str, Any] = {"network": network}

    if node.transport == "ws":
        # xray deprecated "Host" inside "headers" — use top-level "host" field instead
        stream["wsSettings"] = {
            "path": node.ws_path or "/",
            "host": node.ws_host or node.sni or node.address,
        }

    elif node.transport == "grpc":
        grpc_settings: Dict[str, Any] = {
            "serviceName": node.grpc_service or "",
            "multiMode": node.grpc_mode == "multi",
        }
        # Only emit `authority` when set — xray defaults to the
        # outbound's destination host otherwise. The x-ui-pro
        # reverse-proxy convention requires the matching authority
        # for nginx to route correctly; standalone Reality / direct
        # gRPC setups don't need it and a stray header would just
        # confuse downstream caching.
        if node.grpc_authority:
            grpc_settings["authority"] = node.grpc_authority
        stream["grpcSettings"] = grpc_settings

    elif node.transport in ("h2", "http"):
        stream["httpSettings"] = {
            "path": node.http_path or "/",
            "host": [node.http_host or node.sni or node.address],
        }

    elif node.transport in ("xhttp", "splithttp"):
        stream["network"] = "xhttp"
        xhttp: Dict[str, Any] = {
            "path": node.http_path or "/",
            # `mode: "auto"` lets xray pick at runtime — matches the
            # reference vless-xhttp-reality URL the user shipped, and
            # is compatible with `packet-up` server-side. Hard-coding
            # `packet-up` here would break clients that emit the URL
            # without an explicit `mode=` (xray treats absent as auto).
            "mode": "auto",
        }
        # Host header: only emit when explicitly set. For Reality+xhttp
        # the SNI already lives in realitySettings.serverName, and a
        # stray Host header pointing at the dial IP confuses xray's
        # path matching. The user's hand-rolled working reference has
        # `host=` empty in the URL.
        if node.http_host:
            xhttp["host"] = node.http_host
        stream["xhttpSettings"] = xhttp

    elif node.transport == "httpupgrade":
        stream["httpupgradeSettings"] = {
            "path": node.http_path or "/",
            "host": node.http_host or node.sni or node.address,
        }

    elif node.transport == "kcp":
        stream["kcpSettings"] = {
            "header": {"type": node.kcp_header or "none"},
            "seed": node.kcp_seed or "",
        }

    elif node.transport == "quic":
        stream["quicSettings"] = {}

    # TLS
    tls = _tls_settings(node)
    if tls:
        key = "realitySettings" if node.tls == "reality" else "tlsSettings"
        stream["security"] = "reality" if node.tls == "reality" else "tls"
        stream[key] = tls
    else:
        stream["security"] = "none"

    # Mark outbound packets so nftables skips them (prevents routing loop)
    stream["sockopt"] = {"mark": 255}

    return stream


def _outbound_vless(node: Node) -> Dict[str, Any]:
    user: Dict[str, Any] = {"id": node.uuid or "", "encryption": "none"}
    if node.flow:
        user["flow"] = node.flow
    return {
        "tag": f"node-{node.id}",
        "protocol": "vless",
        "settings": {
            "vnext": [{"address": node.address, "port": node.port, "users": [user]}]
        },
        "streamSettings": _stream_settings(node),
    }


def _outbound_vmess(node: Node) -> Dict[str, Any]:
    return {
        "tag": f"node-{node.id}",
        "protocol": "vmess",
        "settings": {
            "vnext": [
                {
                    "address": node.address,
                    "port": node.port,
                    "users": [{"id": node.uuid or "", "alterId": 0, "security": "auto"}],
                }
            ]
        },
        "streamSettings": _stream_settings(node),
    }


def _outbound_trojan(node: Node) -> Dict[str, Any]:
    return {
        "tag": f"node-{node.id}",
        "protocol": "trojan",
        "settings": {
            "servers": [
                {
                    "address": node.address,
                    "port": node.port,
                    "password": node.password or "",
                }
            ]
        },
        "streamSettings": _stream_settings(node),
    }


def _outbound_ss(node: Node) -> Dict[str, Any]:
    # password may be "method:password" encoded in the password field
    method = "chacha20-ietf-poly1305"
    pwd = node.password or ""
    if ":" in pwd:
        parts = pwd.split(":", 1)
        method, pwd = parts[0], parts[1]
    return {
        "tag": f"node-{node.id}",
        "protocol": "shadowsocks",
        "settings": {
            "servers": [
                {
                    "address": node.address,
                    "port": node.port,
                    "method": method,
                    "password": pwd,
                }
            ]
        },
        "streamSettings": _stream_settings(node),
    }


def _outbound_wireguard(node: Node) -> Dict[str, Any]:
    peers: List[Dict[str, Any]] = []
    if node.wg_public_key:
        peer: Dict[str, Any] = {
            "publicKey": node.wg_public_key,
            "endpoint": node.wg_endpoint or f"{node.address}:{node.port}",
        }
        if node.wg_preshared_key:
            peer["preSharedKey"] = node.wg_preshared_key
        peers.append(peer)

    # xray's wireguard outbound requires interface addresses to be /32 (IPv4)
    # or /128 (IPv6). Standard WG configs often use /24 or /64 — normalize.
    local_addrs: List[str] = []
    if node.wg_local_address:
        for raw in node.wg_local_address.split(","):
            a = raw.strip()
            if not a:
                continue
            ip_part = a.split("/", 1)[0]
            mask = "/128" if ":" in ip_part else "/32"
            local_addrs.append(ip_part + mask)

    reserved = [0, 0, 0]
    if node.wg_reserved:
        try:
            reserved = json.loads(node.wg_reserved)
        except Exception:
            pass

    return {
        "tag": f"node-{node.id}",
        "protocol": "wireguard",
        "settings": {
            "secretKey": node.wg_private_key or "",
            "address": local_addrs or ["10.0.0.2/32"],
            "peers": peers,
            "mtu": node.wg_mtu,
            "reserved": reserved,
        },
        "streamSettings": {"sockopt": {"mark": 255}},
    }


def _outbound_socks(node: Node) -> Dict[str, Any]:
    server: Dict[str, Any] = {"address": node.address, "port": node.port}
    if node.uuid:
        server["users"] = [{"user": node.uuid, "pass": node.password or ""}]
    elif node.password:
        server["users"] = [{"user": "", "pass": node.password}]
    return {
        "tag": f"node-{node.id}",
        "protocol": "socks",
        "settings": {"servers": [server]},
        "streamSettings": _stream_settings(node),
    }


def _outbound_naive(node: Node) -> Dict[str, Any]:
    """
    NaiveProxy outbound.

    xray-core doesn't speak naive's HTTPS-masquerade protocol directly. Instead
    the PiTun backend runs a sidecar container (see core/naive_manager.py) that
    exposes a SOCKS5 listener on 127.0.0.1:<internal_port>, and xray connects
    to it as a plain SOCKS outbound. All the actual naive-specific handshake
    happens inside the sidecar.
    """
    if not node.internal_port:
        raise ValueError(
            f"Node {node.id} ({node.name}) is naive but has no internal_port allocated. "
            "Ensure NaiveManager.allocate_port ran on create/enable."
        )
    return {
        "tag": f"node-{node.id}",
        "protocol": "socks",
        "settings": {
            "servers": [
                {"address": "127.0.0.1", "port": int(node.internal_port)}
            ]
        },
        # Loopback traffic doesn't need SO_MARK (no tproxy in the way), but we
        # keep it for consistency with every other outbound so kill-switch
        # logic and fwmark routing don't have special cases.
        "streamSettings": {"sockopt": {"mark": 255}},
    }


def _outbound_hy2(node: Node) -> Dict[str, Any]:
    settings: Dict[str, Any] = {
        "address": node.address,
        "port": node.port,
        "version": 2,
        "password": node.password or "",
    }
    if node.hy2_obfs and node.hy2_obfs_password:
        settings["obfs"] = node.hy2_obfs
        settings["obfs-password"] = node.hy2_obfs_password

    tls_cfg: Dict[str, Any] = {
        "serverName": node.sni or node.address,
        "allowInsecure": node.allow_insecure,
    }
    if node.fingerprint:
        tls_cfg["fingerprint"] = node.fingerprint
    if node.alpn:
        tls_cfg["alpn"] = [a.strip() for a in node.alpn.split(",")]

    return {
        "tag": f"node-{node.id}",
        "protocol": "hysteria",
        "settings": settings,
        "streamSettings": {
            "security": "tls",
            "tlsSettings": tls_cfg,
            "sockopt": {"mark": 255},
        },
    }


# Guards a pathological config (and keeps xray's dialerProxy nesting sane).
# Far deeper than any real topology — a 3-hop chain is already exotic.
_MAX_CHAIN_DEPTH = 8


def _apply_chain(
    node: Node,
    outbound: Dict[str, Any],
    outbounds: List[Dict],
    used_ids: set,
    all_nodes: List[Node],
    _seen: Optional[set] = None,
    _depth: int = 0,
) -> None:
    """Wire `node`'s chain into the config — RECURSIVELY, for multi-hop.

    `node.chain_node_id` is the relay this node dials through. We point
    `node`'s proxySettings at that relay's outbound tag, build the relay's
    outbound if it isn't in the config yet, then RECURSE so the relay's own
    chain is wired too. That gives a full path, e.g. exit → mid → entry
    (`WG2 → WG1 → VLESS`). Previously only the first hop was wired and any
    deeper relay silently dialed direct, collapsing a 3-node chain to 2.

    `proxySettings.transportLayer = true` is xray's documented chaining
    knob — it's converted to `sockopt.dialerProxy`, so the relay carries
    this outbound's traffic over its own transport (works for vless /
    trojan / ss / wireguard / …; the WireGuard-over-WireGuard hop is valid
    config but worth runtime-testing). Self-chains, cycles, and an
    over-deep chain are truncated with a warning instead of emitting a
    broken (or infinite) config.
    """
    if not node.chain_node_id:
        return
    if _seen is None:
        _seen = {node.id}
    if _depth >= _MAX_CHAIN_DEPTH:
        logger.warning("Chain from node %d exceeds max depth %d — truncating",
                       node.id, _MAX_CHAIN_DEPTH)
        return
    if node.chain_node_id == node.id:
        logger.warning("Node %d chains to itself — skipping chain", node.id)
        return
    if node.chain_node_id in _seen:
        logger.warning("Chain cycle at node %d → %d (already in chain) — truncating",
                       node.id, node.chain_node_id)
        return
    chain = next((n for n in all_nodes if n.id == node.chain_node_id), None)
    if not chain:
        logger.warning("Chain node %d not found for node %d — skipping chain", node.chain_node_id, node.id)
        return
    if not chain.enabled:
        logger.warning("Chain node %d is disabled for node %d — skipping chain", chain.id, node.id)
        return
    # xray can't tunnel traffic THROUGH a WireGuard outbound (it only works
    # as the exit hop; as a relay it forwards 0 bytes). The API blocks this
    # at save time, but guard here too for legacy/imported data: skip the
    # link so the node dials direct rather than into a dead WG tunnel.
    if chain.protocol == "wireguard":
        logger.warning(
            "Node %d chains THROUGH WireGuard node %d — unsupported by xray "
            "(WG can only be the exit hop); skipping chain link", node.id, chain.id)
        return

    # Reuse the relay's outbound if it's already present (it may be the
    # active node, a `node:<id>` target, or another node's relay); else
    # build it once. A node's chain is a property OF THE NODE, so sharing
    # the same outbound object keeps the relay's own chain consistent
    # everywhere it's referenced.
    chain_tag = f"node-{chain.id}"
    chain_ob = next((o for o in outbounds if o.get("tag") == chain_tag), None)
    if chain_ob is None:
        try:
            chain_ob = _build_outbound(chain)
        except Exception as exc:
            logger.warning("Chain node %d build failed: %s", chain.id, exc)
            return
        outbounds.insert(0, chain_ob)
        used_ids.add(chain.id)

    outbound["proxySettings"] = {"tag": chain_tag, "transportLayer": True}

    # Recurse so the relay's OWN chain is wired (multi-hop). `_seen` grows
    # per branch → cycles truncate; `_depth` caps runaway nesting.
    _apply_chain(chain, chain_ob, outbounds, used_ids, all_nodes,
                 _seen | {chain.id}, _depth + 1)


def _build_outbound(node: Node) -> Dict[str, Any]:
    builders = {
        "vless": _outbound_vless,
        "vmess": _outbound_vmess,
        "trojan": _outbound_trojan,
        "ss": _outbound_ss,
        "wireguard": _outbound_wireguard,
        "socks": _outbound_socks,
        "hy2": _outbound_hy2,
        "naive": _outbound_naive,
    }
    builder = builders.get(node.protocol)
    if builder is None:
        raise ValueError(f"Unsupported protocol: {node.protocol}")
    return builder(node)


# ── Routing rules → xray routing ─────────────────────────────────────────────

_PRIVATE_CIDRS = [
    "0.0.0.0/8", "10.0.0.0/8", "100.64.0.0/10",
    "127.0.0.0/8", "169.254.0.0/16", "172.16.0.0/12",
    "192.168.0.0/16", "224.0.0.0/4", "240.0.0.0/4",
    "255.255.255.255/32", "::1/128", "fc00::/7", "fe80::/10",
]


def _routing_rule_to_xray(
    rule: RoutingRule,
    active_node_id: Optional[int],
    active_balancer_tag: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Convert a RoutingRule DB row to an xray routing rule dict.

    When `active_balancer_tag` is set (the active node belongs to a running
    NodeCircle), `action == "proxy"` targets that balancer instead of a
    single `node-<id>` outbound — so circle rotation can hot-swap the
    selected node via the gRPC balancerOverride API without a restart.
    """
    values = [v.strip() for v in rule.match_value.split(",") if v.strip()]

    xray_rule: Dict[str, Any] = {"type": "field"}

    if rule.action == "proxy":
        if active_balancer_tag:
            xray_rule["balancerTag"] = active_balancer_tag
        else:
            xray_rule["outboundTag"] = f"node-{active_node_id}" if active_node_id else "direct"
    elif rule.action == "direct":
        xray_rule["outboundTag"] = "direct"
    elif rule.action == "block":
        xray_rule["outboundTag"] = "block"
    elif rule.action.startswith("node:"):
        # Malformed "node:" (no id) or "node:abc" (non-integer) → skip the
        # rule; the rest of the ruleset still applies.
        try:
            nid = int(rule.action.split(":", 1)[1])
            xray_rule["outboundTag"] = f"node-{nid}"
        except (ValueError, IndexError):
            return None
    elif rule.action.startswith("balancer:"):
        try:
            bid = int(rule.action.split(":", 1)[1])
            xray_rule["balancerTag"] = f"balancer-{bid}"
        except (ValueError, IndexError):
            return None
    else:
        xray_rule["outboundTag"] = "direct"

    if rule.rule_type == "dst_ip":
        xray_rule["ip"] = values
    elif rule.rule_type == "src_ip":
        xray_rule["sourceIP"] = values
    elif rule.rule_type == "domain":
        # xray treats a bare entry in `domain` as a SUBSTRING match —
        # `youtube.com` would match `mynot-youtube.com.fake.tld`. That's
        # almost never what users want. Auto-prefix bare entries with
        # `domain:` (suffix match) unless they already carry a known
        # matcher prefix. This keeps backward compatibility with any
        # explicit `full:` / `keyword:` / `regexp:` / `geosite:` entries
        # users might have hand-written.
        _DOMAIN_PREFIXES = ("domain:", "full:", "keyword:", "regexp:", "geosite:")
        xray_rule["domain"] = [
            v if v.startswith(_DOMAIN_PREFIXES) else f"domain:{v}"
            for v in values
        ]
    elif rule.rule_type == "port":
        xray_rule["port"] = ",".join(values)
    elif rule.rule_type == "protocol":
        xray_rule["protocol"] = values
    elif rule.rule_type == "geoip":
        xray_rule["ip"] = [f"geoip:{v}" for v in values]
    elif rule.rule_type == "geosite":
        xray_rule["domain"] = [f"geosite:{v}" for v in values]
    elif rule.rule_type == "mac":
        # MAC is handled by nftables, not xray routing
        return None
    else:
        return None

    return xray_rule


def _build_dns_section(
    settings_map: Dict[str, str],
    dns_rules: Optional[List[DNSRule]] = None,
) -> Dict[str, Any]:
    """Build the xray DNS configuration section from settings and DNS rules."""
    dns_mode = settings_map.get("dns_mode", "plain")
    dns_upstream = settings_map.get("dns_upstream", "8.8.8.8")
    dns_upstream_secondary = settings_map.get("dns_upstream_secondary", "").strip()
    dns_fallback = settings_map.get("dns_fallback", "8.8.8.8").strip()
    fakedns_enabled = settings_map.get("fakedns_enabled", "false").lower() == "true"
    fakedns_pool = settings_map.get("fakedns_pool", "198.18.0.0/15")
    fakedns_pool_size = int(settings_map.get("fakedns_pool_size", "65535"))
    bypass_cn_dns = settings_map.get("bypass_cn_dns", "false").lower() == "true"
    bypass_ru_dns = settings_map.get("bypass_ru_dns", "false").lower() == "true"
    disable_fallback = settings_map.get("dns_disable_fallback", "true").lower() == "true"

    # queryStrategy — defaults to UseIPv4 to prevent the IPv6 leak.
    #
    # PiTun's TPROXY pipeline is IPv4-only (nftables has no ip6 TPROXY
    # rules). If the DNS engine hands a LAN client an AAAA record, the
    # client connects over IPv6 — and that traffic uses the client's
    # OWN IPv6 default route (handed out by the LAN router via RA/
    # DHCPv6), which goes straight to the internet BYPASSING PiTun
    # entirely. The proxy/routing rules never see it. Returning only
    # A records forces clients onto IPv4, which IS intercepted by
    # TPROXY. This is the standard anti-leak technique for IPv4-only
    # transparent proxies.
    #
    # Overridable for the rare operator who genuinely handles IPv6
    # elsewhere: set `dns_query_strategy` = UseIP | UseIPv4 | UseIPv6.
    # Validated against xray's accepted values; anything else falls
    # back to the safe UseIPv4 default.
    query_strategy = settings_map.get("dns_query_strategy", "UseIPv4").strip()
    if query_strategy not in ("UseIP", "UseIPv4", "UseIPv6"):
        query_strategy = "UseIPv4"

    # Format primary upstream based on mode
    if dns_mode == "doh":
        primary_addr = f"https://{dns_upstream}/dns-query" if not dns_upstream.startswith("http") else dns_upstream
    elif dns_mode == "dot":
        # NB: xray-core does NOT support native DoT (see issue #786, PR #2042
        # closed as draft). We fall back to plaintext DNS-over-TCP on port 53
        # — not encrypted, but at least functional. UI labels this mode as
        # "DNS over TCP (not encrypted)".
        primary_addr = f"tcp://{dns_upstream}:53"
    else:
        primary_addr = dns_upstream

    dns_servers: List[Any] = []

    # FakeDNS first if enabled
    if fakedns_enabled:
        dns_servers.append("fakedns")

    # CN bypass: use 114.114.114.114 for CN domains
    if bypass_cn_dns:
        dns_servers.append({
            "address": "114.114.114.114",
            "domains": ["geosite:cn"],
            "tag": "cn-dns",
        })

    # RU bypass: use Yandex DNS (77.88.8.8) for RU domains
    if bypass_ru_dns:
        dns_servers.append({
            "address": "77.88.8.8",
            "domains": ["domain:ru", "domain:su", "domain:xn--p1ai"],
            "tag": "ru-dns",
        })

    # Add per-rule DNS servers from DNSRule table (grouped by formatted address)
    #
    # Each DNSRule has both `dns_server` (host/ip) and `dns_type`
    # (plain/doh/dot). We format the address the same way as the global
    # upstream — so a rule with type=doh → `https://...`, type=dot →
    # `tcp://...:53` (xray-core has no native DoT; same caveat as upstream).
    # If the user already typed a scheme (https://, tcp://, quic+local://),
    # we pass it through unchanged.
    def _format_rule_addr(host: str, rtype: str) -> str:
        h = host.strip()
        # Already a scheme URL? pass through
        if "://" in h or h.lower() in ("localhost", "fakedns"):
            return h
        if rtype == "doh":
            return f"https://{h}/dns-query"
        if rtype == "dot":
            # xray-core does not support native DoT — fall back to plaintext
            # DNS-over-TCP on port 53. See config_gen's primary-upstream note.
            return f"tcp://{h}:53"
        return h  # plain

    if dns_rules:
        server_to_domains: Dict[str, List[str]] = {}
        for rule in sorted([r for r in dns_rules if r.enabled], key=lambda r: r.order):
            key = _format_rule_addr(rule.dns_server, (rule.dns_type or "plain"))
            if key not in server_to_domains:
                server_to_domains[key] = []
            for domain in rule.domain_match.split(","):
                domain = domain.strip()
                if domain:
                    server_to_domains[key].append(domain)

        for server_addr, domains in server_to_domains.items():
            entry: Dict[str, Any] = {"address": server_addr, "domains": domains}
            dns_servers.append(entry)

    # Primary upstream
    dns_servers.append(primary_addr)

    # Secondary upstream (if set and different from primary)
    if dns_upstream_secondary and dns_upstream_secondary != dns_upstream:
        if dns_mode == "doh":
            sec_addr = f"https://{dns_upstream_secondary}/dns-query" if not dns_upstream_secondary.startswith("http") else dns_upstream_secondary
        elif dns_mode == "dot":
            sec_addr = f"tcp://{dns_upstream_secondary}:53"
        else:
            sec_addr = dns_upstream_secondary
        dns_servers.append(sec_addr)

    # Fallback DNS (if set and different from primary/secondary)
    if dns_fallback and dns_fallback not in (dns_upstream, dns_upstream_secondary):
        dns_servers.append(dns_fallback)

    # Pin DNS server IPs in hosts to prevent xray from trying to resolve
    # "tls://8.8.8.8" as a domain through its own DNS (recursive loop)
    dns_hosts: Dict[str, Any] = {}
    import re as _re
    for srv in dns_servers:
        addr = srv if isinstance(srv, str) else srv.get("address", "")
        # Extract IP from tls://IP, https://IP/path, etc.
        m = _re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", addr)
        if m:
            ip = m.group(1)
            dns_hosts[ip] = ip  # identity mapping — skip resolution

    # Pin every DNS server entry to the `direct` outbound (since v1.3.5).
    #
    # xray-core treats DNS-upstream connections as regular outbound dials
    # — meaning they pass through the user's routing rules. That's a
    # footgun: a perfectly legitimate "everything-via-proxy" routing
    # rule (e.g. `port: 0-65535 → proxy` or `geoip:!ru → proxy`) ALSO
    # captures the DNS resolver's own connection to 8.8.8.8:53,
    # tunnelling it through the active node. If the node is flaky or
    # under handshake-failure, the whole DNS layer dies — and with
    # broken DNS, nothing else resolves either. A catch-all proxy rule
    # plus one transient handshake failure is enough to lock up both
    # the API and the proxy.
    #
    # The `outboundTag` field on a DNS server config is exactly the
    # escape hatch — xray short-circuits routing for THAT server's
    # upstream connections and dials via the named outbound. Pinning
    # to `direct` means DNS always resolves over the host's normal
    # network path, regardless of what the user's routing rules say.
    # Privacy-wise this is the standard pattern: DNS-over-TCP/53 is
    # not encrypted anyway, and forcing it through the proxy adds a
    # circular dependency (the proxy node's address itself needs to
    # be resolved before the proxy can be used).
    pinned_servers: List[Any] = []
    for srv in dns_servers:
        if srv == "fakedns":
            pinned_servers.append(srv)  # sentinel — no outbound
            continue
        if isinstance(srv, str):
            pinned_servers.append({"address": srv, "outboundTag": "direct"})
        elif isinstance(srv, dict):
            # Preserve any existing outboundTag (currently none, but
            # defensive against future per-server overrides).
            if "outboundTag" not in srv:
                srv = {**srv, "outboundTag": "direct"}
            pinned_servers.append(srv)
        else:
            pinned_servers.append(srv)  # unknown shape — leave alone

    dns_section: Dict[str, Any] = {
        "hosts": dns_hosts,
        "servers": pinned_servers,
        "disableFallback": disable_fallback,
        # See query_strategy note above — UseIPv4 by default closes the
        # IPv6 bypass leak on this IPv4-only TPROXY box.
        "queryStrategy": query_strategy,
    }

    # FakeDNS pool config
    if fakedns_enabled:
        dns_section["fakedns"] = [{"ipPool": fakedns_pool, "poolSize": fakedns_pool_size}]

    return dns_section


def _build_tun_inbound(settings_map: Dict[str, str]) -> Dict[str, Any]:
    """Build xray TUN inbound configuration."""
    address = settings_map.get("tun_address", "10.0.0.1/30")
    mtu = int(settings_map.get("tun_mtu", "9000"))
    stack = settings_map.get("tun_stack", "system")
    auto_route = settings_map.get("tun_auto_route", "true").lower() == "true"
    strict_route = settings_map.get("tun_strict_route", "true").lower() == "true"
    endpoint_nat = settings_map.get("tun_endpoint_nat", "true").lower() == "true"
    sniff = settings_map.get("tun_sniff", "true").lower() == "true"

    inbound: Dict[str, Any] = {
        "tag": "tun-in",
        "protocol": "tun",
        "settings": {
            "name": "xray0",
            "inet4Address": address,
            "mtu": mtu,
            "autoRoute": auto_route,
            "strictRoute": strict_route,
            "endpointIndependentNat": endpoint_nat,
            "stack": stack,
        },
    }
    if sniff:
        inbound["sniffing"] = {
            "enabled": True,
            "destOverride": ["http", "tls", "quic"],
            "routeOnly": False,
        }
    return inbound


def resolve_active_circle(circles, active_node_id):
    """Return (circle_id, member_node_ids) of the enabled NodeCircle that
    contains `active_node_id`, else (None, None).

    Callers pass the active circle into `generate_config` so the proxy
    traffic routes via a per-circle balancer over all members — that's what
    lets rotation hot-swap the selected node through the gRPC balancerOverride
    API without restarting xray (live connections survive).
    """
    if not active_node_id:
        return None, None
    for c in circles or []:
        if not getattr(c, "enabled", False):
            continue
        try:
            ids = json.loads(c.node_ids) if isinstance(c.node_ids, str) else (c.node_ids or [])
        except Exception:
            ids = []
        if active_node_id in ids:
            return c.id, list(ids)
    return None, None


def generate_config(
    active_node: Optional[Node],
    all_nodes: List[Node],
    rules: List[RoutingRule],
    mode: str,
    settings_map: Dict[str, str],
    dns_rules: Optional[List[DNSRule]] = None,
    balancer_groups: Optional[List[BalancerGroup]] = None,
    routing_sets: Optional[List[RoutingSet]] = None,
    device_set_macs: Optional[Dict[int, List[str]]] = None,
    active_circle_id: Optional[int] = None,
    active_circle_node_ids: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Build full xray JSON configuration.

    Per-set routing (v1.4): when `routing_sets` is non-empty, the config
    grows N additional TPROXY inbounds (one per RoutingSet that has at
    least one assigned device, identified by `device_set_macs[set_id]`).
    Each per-set inbound listens on `127.0.0.1:<set.tproxy_port>` and
    its tag is `tproxy-set-<set_id>`. Per-set rules carry an
    `inboundTag: ["tproxy-set-<set_id>"]` matcher so they only apply to
    traffic that nftables already redirected into that inbound (MAC-
    based, DHCP-immune).

    Empty sets (no `device_set_macs` entry, or empty list) are silently
    skipped — both their inbound AND their rules. This means a rule
    "block ads in Kids" with zero Kids devices is a no-op at config-
    generation time; it materialises the moment a device is assigned.

    `routing_set_id IS NULL` rules are GLOBAL — emitted without
    `inboundTag` so they apply to default `tproxy-tcp/udp` AND every
    per-set inbound (catch-all fallback semantics).
    """
    log_level = settings_map.get("log_level", "warning")
    dns_port = int(settings_map.get("dns_port", settings.dns_port))
    bypass_private = settings_map.get("bypass_private", "true").lower() == "true"
    tproxy_tcp = int(settings_map.get("tproxy_port_tcp", settings.tproxy_port_tcp))
    tproxy_udp = int(settings_map.get("tproxy_port_udp", settings.tproxy_port_udp))
    fakedns_enabled = settings_map.get("fakedns_enabled", "false").lower() == "true"
    dns_sniffing = settings_map.get("dns_sniffing", "true").lower() == "true"
    inbound_mode = settings_map.get("inbound_mode", "tproxy")
    socks_port = int(settings_map.get("socks_port", settings.socks_port))
    http_port = int(settings_map.get("http_port", settings.http_port))

    # LAN proxy authentication (since v1.3.0-beta.6). Single (user, pass)
    # pair applies to BOTH the explicit SOCKS and HTTP inbounds when
    # enabled — TPROXY stays passwordless because it's transparent +
    # nftables-gated. Plaintext storage is intentional: the threat
    # model is an opportunistic LAN scanner, not at-rest encryption,
    # and the user must be able to copy creds back into a client app.
    lan_auth_enabled = (
        settings_map.get("lan_proxy_auth_enabled", "false").lower() == "true"
    )
    lan_auth_user = settings_map.get("lan_proxy_auth_user", "").strip()
    lan_auth_pass = settings_map.get("lan_proxy_auth_pass", "")
    # Empty creds with auth enabled = config error. Surface it loudly
    # so xray doesn't silently accept arbitrary connections (or worse,
    # fail to start with an opaque error). The API enforces the same
    # rule on PUT /settings — this is the defence-in-depth fallback.
    if lan_auth_enabled and (not lan_auth_user or not lan_auth_pass):
        raise ValueError(
            "lan_proxy_auth_enabled=true but lan_proxy_auth_user / "
            "lan_proxy_auth_pass is empty. Set both in Settings or "
            "disable LAN proxy auth before starting xray."
        )

    # DNS section (full, with rules)
    dns_section = _build_dns_section(settings_map, dns_rules)

    # Sniffing destOverride: include fakedns if enabled
    sniff_dest = ["http", "tls"]
    if fakedns_enabled:
        sniff_dest.append("fakedns")

    # Stats API inbound (always present, local only)
    api_port = settings.xray_api_port
    inbounds: List[Dict[str, Any]] = [
        {
            "tag": "api",
            "protocol": "dokodemo-door",
            "listen": "127.0.0.1",
            "port": api_port,
            "settings": {"address": "127.0.0.1"},
        },
        {
            "tag": "dns-in",
            "protocol": "dokodemo-door",
            "port": dns_port,
            "listen": "0.0.0.0",
            "settings": {"address": "1.1.1.1", "port": 53, "network": "tcp,udp"},
        },
        {
            "tag": "dns-in-53",
            "protocol": "dokodemo-door",
            "port": 53,
            "listen": "0.0.0.0",
            "settings": {"address": "1.1.1.1", "port": 53, "network": "tcp,udp"},
        },
        {
            "tag": "socks-in",
            "protocol": "socks",
            "port": socks_port,
            "listen": "0.0.0.0",
            # SOCKS settings: when LAN auth enabled, switch from
            # `auth: noauth` to `auth: password` and inject the user
            # account. `udp: True` stays — auth doesn't change the
            # UDP-relay capability, only the gateway handshake.
            "settings": (
                {
                    "auth": "password",
                    "udp": True,
                    "accounts": [{"user": lan_auth_user, "pass": lan_auth_pass}],
                }
                if lan_auth_enabled
                else {"auth": "noauth", "udp": True}
            ),
            "sniffing": {"enabled": dns_sniffing, "destOverride": sniff_dest, "routeOnly": True},
        },
        {
            "tag": "http-in",
            "protocol": "http",
            "port": http_port,
            "listen": "0.0.0.0",
            # HTTP inbound: empty `settings` = passwordless. With LAN
            # auth enabled, inject the same single (user, pass) pair —
            # xray's HTTP inbound treats `accounts` as Basic-auth
            # required, returning 407 on missing/wrong creds.
            "settings": (
                {"accounts": [{"user": lan_auth_user, "pass": lan_auth_pass}]}
                if lan_auth_enabled
                else {}
            ),
            "sniffing": {"enabled": dns_sniffing, "destOverride": sniff_dest, "routeOnly": True},
        },
    ]

    # TPROXY inbounds
    if inbound_mode in ("tproxy", "both"):
        # Per-set TPROXY inbounds first (v1.4). One inbound per RoutingSet
        # that has at least one device assigned. nftables redirects MAC-
        # matched traffic into the set's port; this inbound receives it
        # and xray router matches `inboundTag` to apply per-set rules.
        #
        # We use `inbounds.insert(0, ...)` later for the default TPROXY,
        # so to keep per-set inbounds AFTER api/dns/socks/http (which
        # are static and appended above) but BEFORE the defaults, we
        # build them in a temporary list and insert-0 in reverse order
        # at the end. Simpler — just append-here, the relative order
        # within `inbounds` doesn't affect xray routing (rules use tags,
        # not array positions).
        if routing_sets and device_set_macs:
            for rs in sorted(routing_sets, key=lambda s: (s.order, s.id)):
                # Skip empty sets — zero member MACs means no traffic
                # will ever hit this port. Listening would waste a
                # socket and clutter `netstat`.
                macs = device_set_macs.get(rs.id) or []
                if not macs:
                    continue
                inbounds.append({
                    "tag": f"tproxy-set-{rs.id}",
                    "protocol": "dokodemo-door",
                    "port": rs.tproxy_port,
                    # listen=0.0.0.0 is REQUIRED for TPROXY (see also
                    # tproxy-tcp/tproxy-udp below). TPROXY does NOT
                    # rewrite destination — packets arrive with the
                    # ORIGINAL dst (e.g. 142.250.190.78:443 for google).
                    # A socket bound to 127.0.0.1 with IP_TRANSPARENT
                    # silently drops those packets — the kernel only
                    # delivers them to a wildcard-bound listener.
                    # Initial v1.4 dev shipped 127.0.0.1 for "loopback
                    # isolation"; it caused per-set redirect to mark
                    # 7796/18650 device packets but xray accepted 0 →
                    # rules looked configured but nothing was actually
                    # blocked. Found on live 1.3 smoke test.
                    #
                    # Safety: TPROXY sockets only accept packets with
                    # the matching fwmark, so 0.0.0.0 here isn't a LAN
                    # exposure — direct connects from LAN devices to
                    # RPi:<port> hit the standard accept path which
                    # has nothing to deliver (no listener semantics
                    # without TPROXY mark).
                    "listen": "0.0.0.0",
                    "settings": {"network": "tcp,udp", "followRedirect": True},
                    "streamSettings": {"sockopt": {"tproxy": "tproxy", "mark": 255}},
                    "sniffing": {
                        "enabled": dns_sniffing,
                        "destOverride": sniff_dest,
                        "routeOnly": True,
                    },
                })

        inbounds.insert(0, {
            "tag": "tproxy-udp",
            "protocol": "dokodemo-door",
            "port": tproxy_udp,
            "listen": "0.0.0.0",
            "settings": {"network": "udp", "followRedirect": True},
            "streamSettings": {"sockopt": {"tproxy": "tproxy", "mark": 255}},
        })
        inbounds.insert(0, {
            "tag": "tproxy-tcp",
            "protocol": "dokodemo-door",
            "port": tproxy_tcp,
            "listen": "0.0.0.0",
            "settings": {"network": "tcp", "followRedirect": True},
            "streamSettings": {"sockopt": {"tproxy": "tproxy", "mark": 255}},
            "sniffing": {"enabled": dns_sniffing, "destOverride": sniff_dest, "routeOnly": True},
        })

    # TUN inbound
    if inbound_mode in ("tun", "both"):
        inbounds.insert(0, _build_tun_inbound(settings_map))

    # Outbounds
    outbounds: List[Dict[str, Any]] = []

    used_ids: set = {active_node.id} if active_node else set()

    if active_node:
        try:
            active_outbound = _build_outbound(active_node)
            _apply_chain(active_node, active_outbound, outbounds, used_ids, all_nodes)
            outbounds.append(active_outbound)
        except Exception as exc:
            logger.error("Failed to build outbound for node %d: %s", active_node.id, exc)

    # NodeCircle members: when the active node belongs to a running circle,
    # preload EVERY member's outbound (+ its chain) and route proxy traffic at
    # a balancer over them (built below). Rotation then hot-swaps the selected
    # member via the gRPC balancerOverride API — no xray restart, so live
    # connections survive (xray picks the outbound per-connection at dispatch).
    circle_member_tags: List[str] = []
    circle_active = bool(active_circle_id and active_circle_node_ids and active_node)
    if circle_active:
        for nid in active_circle_node_ids:
            tag = f"node-{nid}"
            if not any(o.get("tag") == tag for o in outbounds):
                node = next((n for n in all_nodes if n.id == nid), None)
                if node and node.enabled:
                    try:
                        ob = _build_outbound(node)
                        _apply_chain(node, ob, outbounds, used_ids, all_nodes)
                        outbounds.append(ob)
                        used_ids.add(nid)
                    except Exception as exc:
                        logger.warning("Circle member node %d skip: %s", nid, exc)
            if any(o.get("tag") == tag for o in outbounds):
                circle_member_tags.append(tag)
    active_balancer_tag = (
        f"circle-{active_circle_id}" if (circle_active and circle_member_tags) else None
    )

    # Additional nodes for "node:<id>" routing rules
    for node in all_nodes:
        if node.id not in used_ids and node.enabled:
            # Check if any rule references this node
            for rule in rules:
                if rule.action == f"node:{node.id}":
                    try:
                        node_outbound = _build_outbound(node)
                        _apply_chain(node, node_outbound, outbounds, used_ids, all_nodes)
                        outbounds.append(node_outbound)
                        used_ids.add(node.id)
                    except Exception as exc:
                        logger.warning("Skip node %d: %s", node.id, exc)
                    break

    # Additional nodes referenced by balancer groups
    if balancer_groups:
        for bg in balancer_groups:
            if not bg.enabled:
                continue
            ids = json.loads(bg.node_ids) if isinstance(bg.node_ids, str) else bg.node_ids
            for nid in ids:
                if nid not in used_ids:
                    node = next((n for n in all_nodes if n.id == nid), None)
                    if node and node.enabled:
                        try:
                            ob = _build_outbound(node)
                            _apply_chain(node, ob, outbounds, used_ids, all_nodes)
                            outbounds.append(ob)
                            used_ids.add(nid)
                        except Exception as exc:
                            logger.warning("Balancer node %d skip: %s", nid, exc)

    outbounds += [
        {"tag": "direct", "protocol": "freedom", "settings": {"domainStrategy": "UseIP"}, "streamSettings": {"sockopt": {"mark": 255}}},
        {"tag": "block", "protocol": "blackhole"},
        # `proxySettings.tag: direct` forces every DNS-upstream dial
        # (the connection to 8.8.8.8:53 / 1.1.1.1:53 / etc) through
        # the `direct` outbound, bypassing the user's routing rules
        # entirely. Without this, a "catch-all → proxy" rule (e.g.
        # `port: 0-65535 → proxy`, `geoip:!ru → proxy`) intercepts
        # the DNS dial too and tunnels it through the active VPN
        # node — if that node has any handshake issue, DNS dies and
        # nothing else can resolve. Pair with the
        # `outboundTag: direct` per-DNS-server pin in `_build_dns_section`
        # — that one wins for any internal xray query that goes
        # through the routing engine BEFORE hitting the outbound.
        {
            "tag": "dns-out",
            "protocol": "dns",
            "streamSettings": {"sockopt": {"mark": 255}},
            "proxySettings": {"tag": "direct"},
        },
    ]

    # Build balancers list for xray routing
    xray_balancers = []
    if balancer_groups:
        for bg in balancer_groups:
            if not bg.enabled:
                continue
            ids = json.loads(bg.node_ids) if isinstance(bg.node_ids, str) else bg.node_ids
            selector = [f"node-{nid}" for nid in ids]
            if selector:
                xray_balancers.append({
                    "tag": f"balancer-{bg.id}",
                    "selector": selector,
                    "strategy": {"type": bg.strategy},
                })

    # Active-NodeCircle balancer. Selector lists every preloaded member; the
    # actually-selected member is pinned at runtime via balancerOverride (the
    # apply path overrides to active_node on (re)load, the scheduler overrides
    # to the next member on rotation). `random` is just a valid cold-start
    # default before the first override lands.
    if active_balancer_tag and circle_member_tags:
        xray_balancers.append({
            "tag": active_balancer_tag,
            "selector": circle_member_tags,
            "strategy": {"type": "random"},
        })

    # Routing
    routing_rules: List[Dict[str, Any]] = [
        # Stats API: route api inbound to api outbound (internal)
        {"type": "field", "inboundTag": ["api"], "outboundTag": "api"},
    ]

    # TUN, SOCKS5, HTTP inbounds all share the same routing rules as TPROXY
    # No special routing needed — they fall through to the same rule set

    # DNS redirect
    routing_rules.append({
        "type": "field",
        "inboundTag": ["dns-in", "dns-in-53"],
        "outboundTag": "dns-out",
    })

    # System-level DNS-upstream protection (since v1.3.5). Forces any
    # destination port 53 to the `direct` outbound BEFORE user rules
    # get a chance to match it. Necessary because xray's `dns` outbound
    # uses the normal routing engine to dial the DNS server itself —
    # so a user `port: 0-65535 → proxy` (or `geoip:!ru → proxy`) rule
    # would otherwise capture the DNS dial and tunnel it through the
    # active VPN node, breaking the resolver when that node has any
    # handshake issue: add the catch-all rule → kill DNS → kill
    # everything.
    #
    # Why we don't pin via dns-out's `proxySettings.tag`: that field
    # chains outbounds (dns-out's resolved data flows through direct)
    # — it doesn't override routing rules for the dial destination.
    # Why we don't strip dangerous user rules: surgical user-rule
    # rewriting is brittle and user-surprising. A system rule that
    # always wins is robust and stays explicit in the generated config.
    routing_rules.append({
        "type": "field",
        "port": "53",
        "outboundTag": "direct",
    })

    if mode == "bypass":
        routing_rules.append({"type": "field", "ip": ["0.0.0.0/0", "::/0"], "outboundTag": "direct"})
    elif mode == "global":
        if bypass_private:
            routing_rules.append({"type": "field", "ip": _PRIVATE_CIDRS, "outboundTag": "direct"})
        _global_target = (
            {"balancerTag": active_balancer_tag} if active_balancer_tag
            else {"outboundTag": f"node-{active_node.id}" if active_node else "direct"}
        )
        routing_rules.append({
            "type": "field",
            "ip": ["0.0.0.0/0", "::/0"],
            **_global_target,
        })
    else:
        # rules mode
        if bypass_private:
            routing_rules.append({"type": "field", "ip": _PRIVATE_CIDRS, "outboundTag": "direct"})

        sorted_rules = sorted([r for r in rules if r.enabled], key=lambda r: r.order)
        active_node_id = active_node.id if active_node else None

        # Per-set rules first (v1.4). For each RoutingSet that has at
        # least one device assigned, emit its rules with `inboundTag`
        # filter so they only apply to that set's TPROXY inbound. Sort
        # named sets by their `order` then `id` to make ordering
        # predictable across config regenerations.
        #
        # Empty sets (no member devices) are silently skipped — their
        # rules would be dead anyway since the corresponding inbound
        # isn't generated either. When a device is later assigned to
        # the set, config regenerates and the rules materialise.
        known_sets = {rs.id: rs for rs in (routing_sets or [])}
        active_set_ids: set[int] = set()
        if device_set_macs:
            for sid, macs in device_set_macs.items():
                if macs and sid in known_sets:
                    active_set_ids.add(sid)

        for sid in sorted(
            active_set_ids,
            key=lambda s: (known_sets[s].order, s),
        ):
            tag = f"tproxy-set-{sid}"
            for rule in sorted_rules:
                if rule.routing_set_id != sid:
                    continue
                xray_rule = _routing_rule_to_xray(rule, active_node_id, active_balancer_tag)
                if xray_rule:
                    xray_rule["inboundTag"] = [tag]
                    routing_rules.append(xray_rule)

        # Global rules (routing_set_id IS NULL) — apply to ALL inbounds
        # including per-set ones. No `inboundTag` filter → catch-all
        # fallback semantics: device in Kids hits Kids rules first
        # (matched by inboundTag), then falls through to globals.
        for rule in sorted_rules:
            if rule.routing_set_id is None:
                xray_rule = _routing_rule_to_xray(rule, active_node_id, active_balancer_tag)
                if xray_rule:
                    routing_rules.append(xray_rule)

        # Rules whose `routing_set_id` points at an empty or non-
        # existent RoutingSet are silently dropped — their inbound
        # doesn't exist so the rule could never match anyway. Keeping
        # them out of the config also prevents accidental "leakage"
        # into the default inbound via the no-inboundTag fallback path.

        # Default: direct
        routing_rules.append({"type": "field", "ip": ["0.0.0.0/0", "::/0"], "outboundTag": "direct"})

    config: Dict[str, Any] = {
        "log": {
            "loglevel": log_level,
            "access": "",
            "error": "",
        },
        "stats": {},
        "api": {
            "tag": "api",
            "services": ["StatsService", "HandlerService", "RoutingService"],
        },
        "policy": {
            "system": {
                "statsOutboundUplink": True,
                "statsOutboundDownlink": True,
            },
        },
        "dns": dns_section,
        "inbounds": inbounds,
        "outbounds": outbounds,
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "domainMatcher": "hybrid",
            "rules": routing_rules,
            **({"balancers": xray_balancers} if xray_balancers else {}),
        },
    }

    return config


# Patterns that map an xray validation stderr to a human-readable hint.
# Adding new entries is the cheap way to upgrade error legibility — the
# regex extracts the offending identifier and `hint` formats a sentence
# that points at the cause + suggested fix. Order doesn't matter; the
# first matching entry wins. CodeQL-friendly: no user input flows into
# regex compilation.
_VALIDATION_HINTS: list[tuple[str, str]] = [
    (
        r"code not found in geosite\.dat:\s*(\S+)",
        "Routing rule references geosite tag '{0}' which is NOT present in your "
        "currently loaded geosite.dat. Switch the geo profile (UI → GeoData) to "
        "one that includes this category, or remove/disable the offending rule.",
    ),
    (
        r"code not found in geoip\.dat:\s*(\S+)",
        "Routing rule references geoip tag '{0}' which is NOT present in your "
        "currently loaded geoip.dat. Switch the geo profile (UI → GeoData) or "
        "remove the rule.",
    ),
    (
        r"failed to parse address:\s*(\S+)",
        "An outbound or inbound has an unparsable address '{0}'. Likely a Node "
        "row with a malformed `address` field — open Nodes in the UI and verify.",
    ),
    (
        r"unknown protocol\s+(\S+)",
        "Configuration references unknown protocol '{0}'. Likely a Node row "
        "with a typo in `protocol` — verify in the Nodes table.",
    ),
    # TUN inbound is a sing-box feature — vanilla XTLS/Xray rejects it.
    # Multiple stderr shapes observed depending on xray version: some
    # print "no protocol named tun" on stdout (caught by line above for
    # the "unknown protocol" path), others bail before logging anything
    # at all (empty stderr — exactly the symptom the user hit during
    # the 1.3.2 burn-in). The empty-stderr case is handled below in
    # `_diagnose_empty_stderr` rather than via pattern match.
    (
        r"(?:tun.*not.*supported|protocol\s+tun|inbound\s+tun.*unknown)",
        "TUN inbound is not supported by this xray build. PiTun ships "
        "vanilla XTLS/Xray-core which doesn't implement `protocol: tun` "
        "(that's a sing-box feature). Switch Inbound Mode back to "
        "TPROXY in the Dashboard.",
    ),
]


def _diagnose_empty_stderr(
    *, xray_version: str | None, inbound_mode: str | None,
    config: Dict[str, Any] | None,
) -> str:
    """Build a fallback diagnostic when xray bails with no stderr at all.

    The "(empty stderr)" case is silent-failure hell — `xray run -test`
    returns non-zero exit code but writes nothing, leaving the operator
    with no clue what's wrong. We assemble what we can from PiTun's own
    knowledge: which xray version we have, which inbound mode was just
    selected (TUN is the known footgun), and the inbound protocols in
    the generated config so a reader can spot a non-vanilla one.
    """
    bits: list[str] = ["xray returned non-zero exit code with empty stderr."]
    if inbound_mode and inbound_mode != "tproxy":
        bits.append(
            f"Inbound Mode is '{inbound_mode}'. "
            f"Note: TUN/Both modes require sing-box core and silently "
            f"fail on vanilla XTLS/Xray (this build). "
            f"Revert to TPROXY in Dashboard → Network Mode."
        )
    if xray_version:
        bits.append(f"xray version: {xray_version}")
    if config:
        try:
            inbound_protos = [
                ib.get("protocol", "?") for ib in (config.get("inbounds") or [])
            ]
            if inbound_protos:
                bits.append(f"Generated inbound protocols: {inbound_protos}")
        except Exception:  # noqa: BLE001 — best-effort diagnostic
            pass
    return " | ".join(bits)


async def _xray_version() -> str | None:
    """Best-effort `xray version` capture for error context. Returns
    None if the binary is missing or hangs."""
    import asyncio

    try:
        if not Path(settings.xray_binary).exists():
            return None
        proc = await asyncio.create_subprocess_exec(
            settings.xray_binary, "version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        # Output typically starts with `Xray 26.3.27 (...) (go1.x linux/...)`
        # — take just the first line to keep error messages compact.
        line = stdout.decode(errors="replace").strip().splitlines()
        return line[0] if line else None
    except Exception:  # noqa: BLE001
        return None


async def _current_inbound_mode() -> str | None:
    """Read the most recent `inbound_mode` setting from DB. Used only
    by the empty-stderr diagnostic — best-effort, swallows all errors."""
    try:
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.database import get_async_engine
        from app.models import Settings as DBSettings

        async with AsyncSession(get_async_engine()) as s:
            row = (await s.exec(
                select(DBSettings).where(DBSettings.key == "inbound_mode")
            )).first()
            return row.value if row else None
    except Exception:  # noqa: BLE001
        return None


def _explain_xray_stderr(stderr: str) -> str | None:
    """Convert xray validation stderr into a one-line human hint when we
    recognise a known failure mode. Returns None for unrecognised errors
    (caller will still log the raw stderr tail).
    """
    import re
    for pattern, template in _VALIDATION_HINTS:
        m = re.search(pattern, stderr)
        if m:
            return template.format(*m.groups())
    return None


async def _persist_validation_error(message: str | None) -> None:
    """Save (or clear) the last xray validation error in the Settings
    table so the UI can surface it on the Diagnostics / Dashboard page.
    Best-effort — never raises into the caller.
    """
    try:
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.database import get_async_engine
        from app.models import Settings as DBSettings

        async with AsyncSession(get_async_engine()) as s:
            row = (await s.exec(
                select(DBSettings).where(DBSettings.key == "last_xray_validation_error")
            )).first()
            if row:
                row.value = message or ""
                s.add(row)
            elif message:
                s.add(DBSettings(key="last_xray_validation_error", value=message))
            else:
                # No row + no error to record — nothing to do.
                return
            await s.commit()
    except Exception as exc:
        logger.debug("Could not persist last_xray_validation_error: %s", exc)


def _parse_geo_heal_target(stderr: str) -> Optional[tuple[str, str]]:
    """If the xray validation stderr reports a missing geosite / geoip
    tag, return `(kind, tag)` so the caller can self-heal by disabling
    referencing routing rules. Otherwise None.

    Recognised patterns (case-insensitive on the tag, since xray
    sometimes upper-cases it):
        "code not found in geosite.dat: CATEGORY-TELEMETRY"
        "code not found in geoip.dat: XX"
    """
    import re
    m = re.search(r"code not found in geosite\.dat:\s*(\S+)", stderr, re.IGNORECASE)
    if m:
        return ("geosite", m.group(1).strip().lower())
    m = re.search(r"code not found in geoip\.dat:\s*(\S+)", stderr, re.IGNORECASE)
    if m:
        return ("geoip", m.group(1).strip().lower())
    return None


async def write_config(
    config: Dict[str, Any], *, validate: bool = True,
    settings_map: Optional[Dict[str, str]] = None,
) -> Optional[tuple[str, str]]:
    """Serialize + validate the xray config, serialized against itself.

    Several independent writers exist (API reload, circle rotation,
    failover, crash watchdog). Overlapping runs used to truncate the same
    file in place and validate each other's half-written JSON. The lock
    covers write AND `xray run -test`, since the test re-reads the path.
    """
    async with _write_lock():
        return await _write_config_unlocked(
            config, validate=validate, settings_map=settings_map,
        )


async def _write_config_unlocked(
    config: Dict[str, Any], *, validate: bool = True,
    settings_map: Optional[Dict[str, str]] = None,
) -> Optional[tuple[str, str]]:
    """Serialize config to JSON, write to disk, then (when `validate`)
    run a pre-flight `xray run -test` to catch obviously-invalid configs
    (typically a routing rule referencing a geosite/geoip tag absent
    from the loaded .dat).

    Validation failure does NOT block the write — semantics match
    pre-1.2.5 behaviour where xray itself was the only validator. But
    we log a human hint and persist it so the UI can surface it.

    Returns:
        None if validation passed (or was skipped).
        (kind, tag) like ("geosite", "category-telemetry") if validation
        failed with a recognisable missing-geo-tag error — caller can
        self-heal by disabling the referencing routing rules and
        re-writing the config (`validate=False` on the retry to avoid
        loops).
    """
    import asyncio

    def _write() -> None:
        path = Path(settings.xray_config_path)
        os.makedirs(path.parent, exist_ok=True)
        # Write to a sibling temp file and rename over the target.
        # `open(path, "w")` truncates in place, so a concurrent writer
        # (scheduled circle rotation vs. an operator hitting "Reload
        # config", failover vs. the watchdog) could interleave into a
        # torn JSON file — which the `-test` below then validated, or
        # worse, which xray was asked to load. os.replace is atomic on
        # the same filesystem, so every reader sees one whole config.
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w") as f:
            json.dump(config, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    await asyncio.to_thread(_write)
    logger.info("xray config written to %s", settings.xray_config_path)

    if not validate:
        return None

    # Pre-flight validation. Skipped silently if the xray binary isn't
    # present (test environments / CI use FastAPI in-process without
    # the binary).
    try:
        if not Path(settings.xray_binary).exists():
            return None

        proc = await asyncio.create_subprocess_exec(
            settings.xray_binary, "run", "-test", "-config", settings.xray_config_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning(
                "xray config pre-flight validation timed out after 10s — "
                "binary may be hung or geo data unusually large"
            )
            return None

        if proc.returncode == 0:
            # Clear any previously-stored error so the UI stops showing it.
            await _persist_validation_error(None)
            return None

        stderr_text = stderr.decode(errors="replace")
        hint = _explain_xray_stderr(stderr_text)
        tail = stderr_text.strip().splitlines()[-1] if stderr_text.strip() else "(empty stderr)"

        if hint:
            logger.error(
                "xray config validation FAILED — %s | xray says: %s", hint, tail,
            )
            await _persist_validation_error(hint)
        elif not stderr_text.strip():
            # Empty-stderr case — xray bailed without any message.
            # Assemble a fallback diagnostic from what we know on our
            # side (xray version + inbound_mode + generated inbound
            # protocols). This is the path the TUN footgun hit before
            # we added explicit pattern matching — leaving the operator
            # with "(empty stderr)" was actively misleading.
            xray_ver = await _xray_version()
            mode = (
                (settings_map or {}).get("inbound_mode")
                if settings_map
                else await _current_inbound_mode()
            )
            diagnostic = _diagnose_empty_stderr(
                xray_version=xray_ver, inbound_mode=mode, config=config,
            )
            logger.error("xray config validation FAILED: %s", diagnostic)
            await _persist_validation_error(diagnostic)
        else:
            # Unrecognised failure mode — log the last line of stderr so the
            # admin has something concrete to grep for.
            logger.error(
                "xray config validation FAILED (unrecognised reason): %s",
                stderr_text[-500:],
            )
            await _persist_validation_error(f"xray validation failed: {tail}")

        return _parse_geo_heal_target(stderr_text)
    except Exception as exc:
        # Pre-flight is advisory — never block the write on a probe error.
        logger.debug("xray config pre-flight validation skipped: %s", exc)
        return None
