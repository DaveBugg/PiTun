"""Generate xray JSON configuration from DB models."""
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import settings
from app.models import BalancerGroup, DNSRule, Node, RoutingRule

logger = logging.getLogger(__name__)


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
    stream: Dict[str, Any] = {"network": node.transport}

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


def _apply_chain(node: Node, outbound: Dict[str, Any], outbounds: List[Dict], used_ids: set, all_nodes: List[Node]) -> None:
    """If node has chain_node_id, add chain node outbound and proxySettings."""
    if not node.chain_node_id:
        return
    if node.chain_node_id == node.id:
        logger.warning("Node %d chains to itself — skipping chain", node.id)
        return
    chain = next((n for n in all_nodes if n.id == node.chain_node_id), None)
    if not chain:
        logger.warning("Chain node %d not found for node %d — skipping chain", node.chain_node_id, node.id)
        return
    if not chain.enabled:
        logger.warning("Chain node %d is disabled for node %d — skipping chain", chain.id, node.id)
        return
    if chain.id not in used_ids:
        try:
            outbounds.insert(0, _build_outbound(chain))
            used_ids.add(chain.id)
        except Exception as exc:
            logger.warning("Chain node %d build failed: %s", chain.id, exc)
            return
    outbound["proxySettings"] = {
        "tag": f"node-{chain.id}",
        "transportLayer": True,
    }


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


def _routing_rule_to_xray(rule: RoutingRule, active_node_id: Optional[int]) -> Optional[Dict[str, Any]]:
    """Convert a RoutingRule DB row to an xray routing rule dict."""
    values = [v.strip() for v in rule.match_value.split(",") if v.strip()]

    xray_rule: Dict[str, Any] = {"type": "field"}

    if rule.action == "proxy":
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

    dns_section: Dict[str, Any] = {
        "hosts": dns_hosts,
        "servers": dns_servers,
        "disableFallback": disable_fallback,
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


def generate_config(
    active_node: Optional[Node],
    all_nodes: List[Node],
    rules: List[RoutingRule],
    mode: str,
    settings_map: Dict[str, str],
    dns_rules: Optional[List[DNSRule]] = None,
    balancer_groups: Optional[List[BalancerGroup]] = None,
) -> Dict[str, Any]:
    """Build full xray JSON configuration."""
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
        {"tag": "dns-out", "protocol": "dns", "streamSettings": {"sockopt": {"mark": 255}}},
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

    if mode == "bypass":
        routing_rules.append({"type": "field", "ip": ["0.0.0.0/0", "::/0"], "outboundTag": "direct"})
    elif mode == "global":
        if bypass_private:
            routing_rules.append({"type": "field", "ip": _PRIVATE_CIDRS, "outboundTag": "direct"})
        routing_rules.append({
            "type": "field",
            "ip": ["0.0.0.0/0", "::/0"],
            "outboundTag": f"node-{active_node.id}" if active_node else "direct",
        })
    else:
        # rules mode
        if bypass_private:
            routing_rules.append({"type": "field", "ip": _PRIVATE_CIDRS, "outboundTag": "direct"})

        sorted_rules = sorted([r for r in rules if r.enabled], key=lambda r: r.order)
        for rule in sorted_rules:
            xray_rule = _routing_rule_to_xray(rule, active_node.id if active_node else None)
            if xray_rule:
                routing_rules.append(xray_rule)

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
]


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
    config: Dict[str, Any], *, validate: bool = True
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
        with open(path, "w") as f:
            json.dump(config, f, indent=2)

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
