"""
Parse proxy URIs into Node-compatible dicts.
Supports: vless, vmess, trojan, ss, wireguard, socks5, hy2
Also handles base64-encoded URI lists and Clash YAML.
"""
import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import yaml

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _b64_decode(s: str) -> str:
    """Decode base64, padding-insensitive."""
    s = s.strip().replace("-", "+").replace("_", "/")
    pad = len(s) % 4
    if pad:
        s += "=" * (4 - pad)
    return base64.b64decode(s).decode(errors="replace")


def _qs(params: Dict[str, List[str]], key: str, default: str = "") -> str:
    return params.get(key, [default])[0]


def _fragment(parsed) -> str:
    return unquote(parsed.fragment) if parsed.fragment else ""


# ── Individual parsers ────────────────────────────────────────────────────────

def _parse_vless(uri: str) -> Optional[Dict[str, Any]]:
    """vless://UUID@host:port?params#name"""
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)
    name = _fragment(parsed) or f"{parsed.hostname}:{parsed.port}"

    node: Dict[str, Any] = {
        "name": name,
        "protocol": "vless",
        "address": parsed.hostname or "",
        "port": parsed.port or 443,
        "uuid": parsed.username or "",
        "transport": _qs(params, "type", "tcp"),
        "tls": _qs(params, "security", "none"),
        "sni": _qs(params, "sni") or _qs(params, "host") or parsed.hostname or "",
        "fingerprint": _qs(params, "fp", "chrome"),
        "alpn": _qs(params, "alpn"),
        "flow": _qs(params, "flow"),
        # WS
        "ws_path": _qs(params, "path", "/"),
        "ws_host": _qs(params, "host"),
        # gRPC
        "grpc_service": _qs(params, "serviceName"),
        # HTTP / xhttp
        "http_path": _qs(params, "path", "/"),
        "http_host": _qs(params, "host"),
        # Reality
        "reality_pbk": _qs(params, "pbk"),
        "reality_sid": _qs(params, "sid"),
        "reality_spx": _qs(params, "spx"),
    }
    return node


def _parse_vmess(uri: str) -> Optional[Dict[str, Any]]:
    """vmess://BASE64(json)"""
    b64 = uri[len("vmess://"):]
    try:
        obj = json.loads(_b64_decode(b64))
    except Exception as exc:
        logger.debug("vmess decode failed: %s", exc)
        return None

    net = obj.get("net", "tcp")
    tls_str = "tls" if obj.get("tls") else "none"

    node: Dict[str, Any] = {
        "name": obj.get("ps") or obj.get("add", ""),
        "protocol": "vmess",
        "address": obj.get("add", ""),
        "port": int(obj.get("port", 443)),
        "uuid": obj.get("id", ""),
        "transport": net,
        "tls": tls_str,
        "sni": obj.get("sni") or obj.get("add", ""),
        "fingerprint": obj.get("fp", "chrome"),
        "alpn": obj.get("alpn", ""),
        # WS / HTTP
        "ws_path": obj.get("path", "/"),
        "ws_host": obj.get("host", ""),
        "http_path": obj.get("path", "/"),
        "http_host": obj.get("host", ""),
        # gRPC
        "grpc_service": obj.get("path", ""),
    }
    return node


def _parse_trojan(uri: str) -> Optional[Dict[str, Any]]:
    """trojan://password@host:port?params#name"""
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)
    name = _fragment(parsed) or f"{parsed.hostname}:{parsed.port}"

    node: Dict[str, Any] = {
        "name": name,
        "protocol": "trojan",
        "address": parsed.hostname or "",
        "port": parsed.port or 443,
        "password": unquote(parsed.username or ""),
        "transport": _qs(params, "type", "tcp"),
        "tls": _qs(params, "security", "tls"),
        "sni": _qs(params, "sni") or parsed.hostname or "",
        "fingerprint": _qs(params, "fp", "chrome"),
        "alpn": _qs(params, "alpn"),
        "ws_path": _qs(params, "path", "/"),
        "ws_host": _qs(params, "host"),
        "grpc_service": _qs(params, "serviceName"),
        "http_path": _qs(params, "path", "/"),
        "http_host": _qs(params, "host"),
    }
    return node


def _parse_ss(uri: str) -> Optional[Dict[str, Any]]:
    """
    ss://BASE64(method:pass)@host:port#name
    or
    ss://BASE64(method:pass@host:port)#name   (old format)
    """
    fragment_start = uri.rfind("#")
    name = unquote(uri[fragment_start + 1:]) if fragment_start >= 0 else ""
    uri_body = uri[len("ss://"):fragment_start] if fragment_start >= 0 else uri[len("ss://"):]

    if "@" in uri_body:
        # SIP002 format: BASE64(method:pass)@host:port
        b64_part, host_part = uri_body.rsplit("@", 1)
        try:
            creds = _b64_decode(b64_part)
        except Exception:
            creds = unquote(b64_part)
        method, password = (creds.split(":", 1) + [""])[:2]
        # host_part can be host:port or [ipv6]:port
        if host_part.startswith("["):
            bracket_end = host_part.index("]")
            host = host_part[1:bracket_end]
            port = int(host_part[bracket_end + 2:]) if ":" in host_part[bracket_end:] else 443
        else:
            host, _, port_str = host_part.rpartition(":")
            port = int(port_str) if port_str else 443
    else:
        # Old format: entire body is base64
        try:
            decoded = _b64_decode(uri_body)
        except Exception:
            return None
        # method:pass@host:port
        at_idx = decoded.rfind("@")
        if at_idx < 0:
            return None
        creds, host_part = decoded[:at_idx], decoded[at_idx + 1:]
        method, password = (creds.split(":", 1) + [""])[:2]
        host, _, port_str = host_part.rpartition(":")
        port = int(port_str) if port_str else 443

    node: Dict[str, Any] = {
        "name": name or f"{host}:{port}",
        "protocol": "ss",
        "address": host,
        "port": port,
        "password": f"{method}:{password}",
    }
    return node


def _parse_wireguard(uri: str) -> Optional[Dict[str, Any]]:
    """wireguard://privkey@endpoint:port?publickey=...&...#name"""
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)
    name = _fragment(parsed) or f"{parsed.hostname}:{parsed.port}"

    node: Dict[str, Any] = {
        "name": name,
        "protocol": "wireguard",
        "address": parsed.hostname or "",
        "port": parsed.port or 51820,
        "wg_private_key": unquote(parsed.username or ""),
        "wg_public_key": _qs(params, "publickey"),
        "wg_preshared_key": _qs(params, "presharedkey"),
        "wg_endpoint": f"{parsed.hostname}:{parsed.port}",
        "wg_mtu": int(_qs(params, "mtu", "1420")),
        "wg_local_address": _qs(params, "address"),
        "wg_reserved": _qs(params, "reserved"),
    }
    return node


def _parse_wireguard_ini(text: str) -> Optional[Dict[str, Any]]:
    """
    Parse a standard WireGuard .conf INI file:
        [Interface]
        Address = 10.7.0.2/24, fddd:2c4:...::2/64
        DNS = 8.8.8.8, 8.8.4.4
        PrivateKey = <base64>
        MTU = 1420

        [Peer]
        PublicKey = <base64>
        PresharedKey = <base64>
        AllowedIPs = 0.0.0.0/0, ::/0
        Endpoint = host:port
        PersistentKeepalive = 25
    """
    section = None
    iface: Dict[str, str] = {}
    peer: Dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip()
        if section == "interface":
            iface[k] = v
        elif section == "peer":
            peer[k] = v

    priv = iface.get("PrivateKey")
    pub = peer.get("PublicKey")
    endpoint = peer.get("Endpoint", "")
    if not priv or not pub or not endpoint:
        return None

    # Split endpoint host:port (support IPv6 in brackets)
    host, port = endpoint, 51820
    m = re.match(r"^\[([^\]]+)\]:(\d+)$", endpoint)
    if m:
        host, port = m.group(1), int(m.group(2))
    elif ":" in endpoint:
        host, _, p = endpoint.rpartition(":")
        try:
            port = int(p)
        except ValueError:
            port = 51820

    name = iface.get("#Name") or f"wg-{host}:{port}"

    return {
        "name": name,
        "protocol": "wireguard",
        "address": host,
        "port": port,
        "wg_private_key": priv,
        "wg_public_key": pub,
        "wg_preshared_key": peer.get("PresharedKey", ""),
        "wg_endpoint": f"{host}:{port}",
        "wg_mtu": int(iface.get("MTU", "1420") or 1420),
        "wg_local_address": iface.get("Address", ""),
        "wg_reserved": "",
    }


def _parse_socks5(uri: str) -> Optional[Dict[str, Any]]:
    """socks5://[user:pass@]host:port#name"""
    parsed = urlparse(uri)
    name = _fragment(parsed) or f"{parsed.hostname}:{parsed.port}"
    node: Dict[str, Any] = {
        "name": name,
        "protocol": "socks",
        "address": parsed.hostname or "",
        "port": parsed.port or 1080,
        "uuid": unquote(parsed.username or ""),
        "password": unquote(parsed.password or ""),
    }
    return node


def _parse_naive(uri: str) -> Optional[Dict[str, Any]]:
    """
    naive+https://user:pass@host:port/?padding=1#name

    Auth is stored in Node.uuid (user) + Node.password (pass) — same
    convention as socks. internal_port is allocated later by NaiveManager
    (when the node is saved and enabled).
    """
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)
    name = _fragment(parsed) or f"{parsed.hostname}:{parsed.port or 443}"

    user = unquote(parsed.username or "")
    pwd = unquote(parsed.password or "")
    padding_raw = _qs(params, "padding", "1").lower()
    padding = padding_raw not in ("0", "false", "no", "off")

    node: Dict[str, Any] = {
        "name": name,
        "protocol": "naive",
        "address": parsed.hostname or "",
        "port": parsed.port or 443,
        "uuid": user,       # reused as "naive user"
        "password": pwd,    # reused as "naive pass"
        "tls": "tls",
        "sni": parsed.hostname or "",
        "naive_padding": padding,
    }
    return node


def _parse_hy2(uri: str) -> Optional[Dict[str, Any]]:
    """hy2://password@host:port?params#name"""
    parsed = urlparse(uri)
    params = parse_qs(parsed.query)
    name = _fragment(parsed) or f"{parsed.hostname}:{parsed.port}"

    node: Dict[str, Any] = {
        "name": name,
        "protocol": "hy2",
        "address": parsed.hostname or "",
        "port": parsed.port or 443,
        "password": unquote(parsed.username or ""),
        "tls": "tls",
        "sni": _qs(params, "sni") or parsed.hostname or "",
        "fingerprint": _qs(params, "fp", "chrome"),
        "alpn": _qs(params, "alpn"),
        "allow_insecure": _qs(params, "insecure", "0") == "1",
        "hy2_obfs": _qs(params, "obfs"),
        "hy2_obfs_password": _qs(params, "obfs-password"),
    }
    return node


# ── Dispatch ──────────────────────────────────────────────────────────────────

_PARSERS = {
    "vless://": _parse_vless,
    "vmess://": _parse_vmess,
    "trojan://": _parse_trojan,
    "ss://": _parse_ss,
    "wireguard://": _parse_wireguard,
    "wg://": _parse_wireguard,
    "socks5://": _parse_socks5,
    "socks://": _parse_socks5,
    "hy2://": _parse_hy2,
    "hysteria2://": _parse_hy2,
    "naive+https://": _parse_naive,
}


def parse_uri(uri: str) -> Optional[Dict[str, Any]]:
    """Parse a single proxy URI. Returns a node dict or None."""
    uri = uri.strip()
    for prefix, parser in _PARSERS.items():
        if uri.lower().startswith(prefix):
            try:
                return parser(uri)
            except Exception as exc:
                logger.debug("Failed to parse %s: %s", uri[:60], exc)
                return None
    return None


def parse_uri_list(text: str) -> List[Dict[str, Any]]:
    """
    Parse a block of text that may be:
    - Newline-separated URI list
    - Base64-encoded URI list (single line, no spaces)
    - Clash YAML (starts with 'proxies:' or has 'proxy-groups:')
    """
    text = text.strip()

    # Try WireGuard INI (.conf)
    if re.search(r"^\s*\[Interface\]", text, re.MULTILINE) and re.search(r"^\s*\[Peer\]", text, re.MULTILINE):
        node = _parse_wireguard_ini(text)
        return [node] if node else []

    # Try xray / sing-box JSON config
    if text.startswith("[") or text.startswith("{"):
        try:
            obj = json.loads(text)
            nodes = _parse_xray_json(obj)
            if nodes:
                return nodes
        except (json.JSONDecodeError, ValueError):
            pass  # not JSON, continue to other parsers

    # Try Clash YAML
    if re.search(r"proxies\s*:", text):
        return _parse_clash_yaml(text)

    # Try base64 decode if it looks like a single base64 blob
    if "\n" not in text and len(text) > 20 and not text.startswith(tuple(_PARSERS)):
        try:
            decoded = _b64_decode(text)
            if "://" in decoded:
                text = decoded
        except Exception:
            pass

    nodes = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        node = parse_uri(line)
        if node:
            nodes.append(node)

    return nodes


def _parse_clash_yaml(text: str) -> List[Dict[str, Any]]:
    """Parse Clash YAML format into node dicts."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.warning("Clash YAML parse error: %s", exc)
        return []

    if not isinstance(doc, dict):
        return []

    proxies = doc.get("proxies", [])
    nodes = []
    for p in proxies:
        if not isinstance(p, dict):
            continue
        node = _clash_proxy_to_node(p)
        if node:
            nodes.append(node)
    return nodes


def _clash_proxy_to_node(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ptype = p.get("type", "").lower()
    name = p.get("name", "")
    server = p.get("server", "")
    port = int(p.get("port", 443))

    base: Dict[str, Any] = {
        "name": name,
        "address": server,
        "port": port,
        "enabled": True,
    }

    if ptype == "vless":
        base.update({
            "protocol": "vless",
            "uuid": p.get("uuid", ""),
            "transport": p.get("network", "tcp"),
            "tls": "reality" if p.get("reality-opts") else ("tls" if p.get("tls") else "none"),
            "flow": p.get("flow", ""),
            "sni": p.get("servername", "") or p.get("sni", ""),
            "fingerprint": p.get("client-fingerprint", "chrome"),
            "ws_path": (p.get("ws-opts") or {}).get("path", "/"),
            "ws_host": str((p.get("ws-opts") or {}).get("headers", {}).get("Host", "")),
            "grpc_service": (p.get("grpc-opts") or {}).get("grpc-service-name", ""),
            "reality_pbk": (p.get("reality-opts") or {}).get("public-key", ""),
            "reality_sid": (p.get("reality-opts") or {}).get("short-id", ""),
        })
    elif ptype == "vmess":
        base.update({
            "protocol": "vmess",
            "uuid": p.get("uuid", ""),
            "transport": p.get("network", "tcp"),
            "tls": "tls" if p.get("tls") else "none",
            "sni": p.get("servername", ""),
            "fingerprint": p.get("client-fingerprint", "chrome"),
            "ws_path": (p.get("ws-opts") or {}).get("path", "/"),
        })
    elif ptype == "trojan":
        base.update({
            "protocol": "trojan",
            "password": p.get("password", ""),
            "transport": p.get("network", "tcp"),
            "tls": "tls",
            "sni": p.get("sni", "") or server,
            "fingerprint": p.get("client-fingerprint", "chrome"),
            "ws_path": (p.get("ws-opts") or {}).get("path", "/"),
            "grpc_service": (p.get("grpc-opts") or {}).get("grpc-service-name", ""),
        })
    elif ptype == "ss":
        base.update({
            "protocol": "ss",
            "password": f"{p.get('cipher', 'chacha20-ietf-poly1305')}:{p.get('password', '')}",
            "transport": "tcp",
            "tls": "none",
        })
    elif ptype in ("socks5", "socks"):
        base.update({
            "protocol": "socks",
            "uuid": p.get("username", ""),
            "password": p.get("password", ""),
            "tls": "tls" if p.get("tls") else "none",
        })
    elif ptype == "hysteria2":
        base.update({
            "protocol": "hy2",
            "password": p.get("password", ""),
            "tls": "tls",
            "sni": p.get("sni", "") or server,
            "hy2_obfs": (p.get("obfs") or ""),
            "hy2_obfs_password": (p.get("obfs-password") or ""),
        })
    elif ptype == "wireguard":
        base.update({
            "protocol": "wireguard",
            "wg_private_key": p.get("private-key", ""),
            "wg_public_key": p.get("public-key", ""),
            "wg_mtu": p.get("mtu", 1420),
            "wg_local_address": ", ".join(p.get("ip", [])) if isinstance(p.get("ip"), list) else p.get("ip", ""),
        })
    else:
        return None

    return base


# ── Xray / sing-box JSON parser ─────────────────────────────────────────────

def _parse_xray_json(obj: Any) -> List[Dict[str, Any]]:
    """
    Parse xray-core / sing-box JSON config format.
    Supports:
    - Array of configs: [{"remarks": ..., "outbounds": [...]}, ...]
    - Single config:    {"outbounds": [...]}
    - Bare outbounds:   [{"tag": ..., "protocol": ..., "settings": ...}, ...]
    """
    configs: list = []
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict):
            if "outbounds" in obj[0] or "remarks" in obj[0]:
                configs = obj
            elif "protocol" in obj[0] or "settings" in obj[0]:
                configs = [{"outbounds": obj}]
            else:
                configs = obj
    elif isinstance(obj, dict):
        configs = [obj]
    else:
        return []

    nodes: List[Dict[str, Any]] = []
    for cfg in configs:
        if not isinstance(cfg, dict):
            continue
        outbounds = cfg.get("outbounds", [])
        if not isinstance(outbounds, list):
            continue
        for ob in outbounds:
            node = _xray_outbound_to_node(ob)
            if node:
                nodes.append(node)
    return nodes


def _xray_outbound_to_node(ob: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert a single xray outbound JSON object to a node dict."""
    if not isinstance(ob, dict):
        return None

    protocol = ob.get("protocol", "").lower()
    tag = ob.get("tag", "")
    settings = ob.get("settings", {}) or {}
    stream = ob.get("streamSettings", {}) or {}

    # Skip non-proxy outbounds (freedom, blackhole, dns, etc.)
    if protocol not in ("vless", "vmess", "trojan", "shadowsocks", "socks", "wireguard", "hysteria2"):
        return None

    address = ""
    port = 443
    uuid_val = ""
    password = ""
    flow = ""

    if protocol in ("vless", "vmess"):
        vnext = settings.get("vnext", [])
        if vnext and isinstance(vnext, list) and isinstance(vnext[0], dict):
            srv = vnext[0]
            address = srv.get("address", "")
            port = int(srv.get("port", 443))
            users = srv.get("users", [])
            if users and isinstance(users, list) and isinstance(users[0], dict):
                user = users[0]
                uuid_val = user.get("id", "")
                flow = user.get("flow", "")

    elif protocol == "trojan":
        servers = settings.get("servers", [])
        if servers and isinstance(servers, list) and isinstance(servers[0], dict):
            srv = servers[0]
            address = srv.get("address", "")
            port = int(srv.get("port", 443))
            password = srv.get("password", "")
            flow = srv.get("flow", "")

    elif protocol == "shadowsocks":
        servers = settings.get("servers", [])
        if servers and isinstance(servers, list) and isinstance(servers[0], dict):
            srv = servers[0]
            address = srv.get("address", "")
            port = int(srv.get("port", 443))
            method = srv.get("method", "chacha20-ietf-poly1305")
            password = f"{method}:{srv.get('password', '')}"

    elif protocol == "socks":
        servers = settings.get("servers", [])
        if servers and isinstance(servers, list) and isinstance(servers[0], dict):
            srv = servers[0]
            address = srv.get("address", "")
            port = int(srv.get("port", 1080))
            users = srv.get("users", [])
            if users and isinstance(users, list) and isinstance(users[0], dict):
                uuid_val = users[0].get("user", "")
                password = users[0].get("pass", "")

    elif protocol == "wireguard":
        peers = settings.get("peers", [])
        pub_key = ""
        if peers and isinstance(peers, list) and isinstance(peers[0], dict):
            pub_key = peers[0].get("publicKey", "")
            endpoint = peers[0].get("endpoint", "")
            if endpoint:
                ep_host, _, ep_port = endpoint.rpartition(":")
                address = ep_host or settings.get("address", "")
                try:
                    port = int(ep_port)
                except ValueError:
                    port = 51820
        if not address:
            address = settings.get("address", "")
        return {
            "name": tag or f"wg-{address}:{port}",
            "protocol": "wireguard",
            "address": address,
            "port": port,
            "wg_private_key": settings.get("secretKey", ""),
            "wg_public_key": pub_key,
            "wg_mtu": settings.get("mtu", 1420),
            "wg_local_address": ", ".join(settings.get("address", [])) if isinstance(settings.get("address"), list) else str(settings.get("address", "")),
            "enabled": True,
        }

    elif protocol == "hysteria2":
        servers = settings.get("servers", []) or settings.get("server", [])
        if isinstance(servers, str):
            address = servers
        elif servers and isinstance(servers, list) and isinstance(servers[0], dict):
            srv = servers[0]
            address = srv.get("address", "")
            port = int(srv.get("port", 443))
            password = srv.get("password", "")

    if not address:
        return None

    # ── Extract transport / TLS / Reality from streamSettings ────────────
    network = stream.get("network", "tcp")
    security = stream.get("security", "none")

    sni = ""
    fingerprint = "chrome"
    alpn = ""
    reality_pbk = ""
    reality_sid = ""
    reality_spx = ""
    ws_path = "/"
    ws_host = ""
    grpc_service = ""
    http_path = "/"
    http_host = ""

    tls_settings = stream.get("tlsSettings", {}) or {}
    if tls_settings:
        sni = tls_settings.get("serverName", "")
        fingerprint = tls_settings.get("fingerprint", "chrome")
        alpn_val = tls_settings.get("alpn", [])
        alpn = ",".join(alpn_val) if isinstance(alpn_val, list) else str(alpn_val)

    reality_settings = stream.get("realitySettings", {}) or {}
    if reality_settings:
        sni = reality_settings.get("serverName", "") or sni
        fingerprint = reality_settings.get("fingerprint", "chrome") or fingerprint
        reality_pbk = reality_settings.get("publicKey", "")
        reality_sid = reality_settings.get("shortId", "")
        reality_spx = reality_settings.get("spiderX", "")
        if security == "none" and reality_pbk:
            security = "reality"

    ws_settings = stream.get("wsSettings", {}) or {}
    if ws_settings:
        ws_path = ws_settings.get("path", "/")
        headers = ws_settings.get("headers", {}) or {}
        ws_host = headers.get("Host", "")

    grpc_settings = stream.get("grpcSettings", {}) or {}
    if grpc_settings:
        grpc_service = grpc_settings.get("serviceName", "")

    http_settings = stream.get("httpSettings", {}) or {}
    if http_settings:
        http_path = http_settings.get("path", "/")
        host_list = http_settings.get("host", [])
        http_host = host_list[0] if host_list else ""

    name = tag or f"{address}:{port}"
    proto_map = {"shadowsocks": "ss", "hysteria2": "hy2", "socks": "socks"}
    norm_protocol = proto_map.get(protocol, protocol)

    node: Dict[str, Any] = {
        "name": name,
        "protocol": norm_protocol,
        "address": address,
        "port": port,
        "transport": network,
        "tls": security,
        "sni": sni or address,
        "fingerprint": fingerprint,
        "alpn": alpn,
        "enabled": True,
    }

    if norm_protocol in ("vless", "vmess"):
        node["uuid"] = uuid_val
        node["flow"] = flow
    elif norm_protocol in ("trojan", "ss", "hy2"):
        node["password"] = password
    elif norm_protocol == "socks":
        node["uuid"] = uuid_val
        node["password"] = password

    node["ws_path"] = ws_path
    node["ws_host"] = ws_host
    node["grpc_service"] = grpc_service
    node["http_path"] = http_path
    node["http_host"] = http_host
    node["reality_pbk"] = reality_pbk
    node["reality_sid"] = reality_sid
    node["reality_spx"] = reality_spx

    return node
