"""Format Node ORM rows back into pasteable proxy URIs.

Symmetric to `uri_parser.py`. Plain-text export of nodes (since
v1.3.0-beta.7) emits a `vless://`-style file per line so the operator
can paste the list into another tool, share with a colleague, etc.

Since v1.3.1 we also build URIs straight from an x-ui inbound + client
dict (without persisting a Node) so the X-ui Panels page can show a
QR code for any panel-side client. See `inbound_client_to_uri`.

Coverage matches the parser:
  * vless     → `vless://<uuid>@<host>:<port>?…#<remark>`
  * vmess     → `vmess://<base64-json>` (legacy quirk: vmess URIs are
                a base64-encoded JSON blob, not a query-string)
  * trojan    → `trojan://<password>@<host>:<port>?…#<remark>`
  * ss        → `ss://<base64(method:password)>@<host>:<port>#<remark>`
  * hy2       → `hysteria2://<password>@<host>:<port>?…#<remark>`
  * socks     → `socks://<base64(user:pass)>@<host>:<port>#<remark>`
                (matches the v2rayN / NekoBox convention)
  * wireguard → not emitted — WG configs need the private key and
                full peer/AllowedIPs block which doesn't round-trip
                cleanly as a URI. Operator can copy WG config from
                the existing per-node QR endpoint instead.

Returns `None` for unsupported protocols so callers can skip them
without raising.
"""
from __future__ import annotations

import base64
import json
from typing import Optional
from urllib.parse import quote, urlencode

from app.models import Node


def _frag(name: str) -> str:
    """URL-encode the URI fragment (the `#<remark>` tail) so panel
    UIs that round-trip through unquote don't mangle non-ASCII /
    spaces in the node name."""
    return quote(name or "", safe="")


def _vless_query(node: Node) -> str:
    """Build the query-string for a vless URI.

    Mirrors what `xui_chain._vless_uri` emits for chain clients, but
    generalises across the transport / TLS / Reality cases the parser
    accepts. Any field that's empty / default is omitted to keep the
    URI compact (the parser fills in defaults on its end).
    """
    params: list[tuple[str, str]] = [
        ("type", node.transport or "tcp"),
    ]
    if node.tls and node.tls != "none":
        params.append(("security", node.tls))
    if node.fingerprint:
        params.append(("fp", node.fingerprint))
    if node.sni:
        params.append(("sni", node.sni))
    if node.alpn:
        params.append(("alpn", node.alpn))
    if node.allow_insecure:
        params.append(("allowInsecure", "1"))

    # Reality params (only when tls == 'reality')
    if node.tls == "reality":
        if node.reality_pbk:
            params.append(("pbk", node.reality_pbk))
        if node.reality_sid:
            params.append(("sid", node.reality_sid))
        if node.reality_spx:
            params.append(("spx", node.reality_spx))

    # Transport-specific params — keep names matching parser
    # expectations (ws→path/host, grpc→serviceName, etc.).
    if node.transport == "ws":
        if node.ws_path:
            params.append(("path", node.ws_path))
        if node.ws_host:
            params.append(("host", node.ws_host))
    elif node.transport == "grpc":
        if node.grpc_service:
            params.append(("serviceName", node.grpc_service))
        if (node.grpc_mode or "").lower() == "multi":
            params.append(("mode", "multi"))
    elif node.transport in ("h2", "http", "xhttp", "splithttp"):
        if node.http_path:
            params.append(("path", node.http_path))
        if node.http_host:
            params.append(("host", node.http_host))
    elif node.transport == "kcp":
        if node.kcp_header:
            params.append(("headerType", node.kcp_header))
        if node.kcp_seed:
            params.append(("seed", node.kcp_seed))

    # Flow is vless-specific (xtls-rprx-vision for TCP+Reality)
    if node.flow:
        params.append(("flow", node.flow))

    return urlencode(params)


def _format_vless(node: Node) -> Optional[str]:
    if not node.uuid:
        return None
    qs = _vless_query(node)
    return f"vless://{node.uuid}@{node.address}:{node.port}?{qs}#{_frag(node.name)}"


def _format_vmess(node: Node) -> Optional[str]:
    """vmess URIs are base64-encoded JSON — v2rayN format.

    The schema is well-established (`v`, `ps`, `add`, `port`, `id`,
    `aid`, `net`, `type`, `host`, `path`, `tls`, `sni`, `alpn`,
    `fp`). Pad and base64 *without* the trailing `=` (v2rayN strips
    them) — both forms decode fine in practice but matching the
    common shape avoids one more "import doesn't work" report.
    """
    if not node.uuid:
        return None
    body = {
        "v": "2",
        "ps": node.name or "",
        "add": node.address or "",
        "port": str(node.port or 443),
        "id": node.uuid,
        "aid": "0",
        "net": node.transport or "tcp",
        "type": (node.kcp_header or "none") if node.transport == "kcp" else "none",
        "host": (node.ws_host or node.http_host or node.sni or ""),
        "path": (node.ws_path or node.http_path or ""),
        "tls": "tls" if node.tls in ("tls", "reality") else "",
        "sni": node.sni or "",
        "alpn": node.alpn or "",
        "fp": node.fingerprint or "",
    }
    raw = json.dumps(body, separators=(",", ":"))
    b64 = base64.b64encode(raw.encode()).rstrip(b"=").decode()
    return f"vmess://{b64}"


def _format_trojan(node: Node) -> Optional[str]:
    if not node.password:
        return None
    # Trojan reuses the vless query layout (type/security/sni/host
    # /path/etc. are all transport-level concerns) so the same builder
    # is fine here too. Drop the `flow` field — Trojan has no xtls.
    qs = _vless_query(node)
    return f"trojan://{quote(node.password, safe='')}@{node.address}:{node.port}?{qs}#{_frag(node.name)}"


def _format_ss(node: Node) -> Optional[str]:
    """Shadowsocks SIP002: `ss://<base64(method:password)>@<host>:<port>#<remark>`.

    PiTun stores `password` as `<method>:<password>` per the parser
    convention (see `_parse_shadowsocks`). Re-encode that pair into
    the canonical form.
    """
    if not node.password or ":" not in node.password:
        return None
    creds = base64.urlsafe_b64encode(node.password.encode()).rstrip(b"=").decode()
    return f"ss://{creds}@{node.address}:{node.port}#{_frag(node.name)}"


def _format_hy2(node: Node) -> Optional[str]:
    if not node.password:
        return None
    params: list[tuple[str, str]] = []
    if node.sni:
        params.append(("sni", node.sni))
    if node.allow_insecure:
        params.append(("insecure", "1"))
    if node.hy2_obfs:
        params.append(("obfs", node.hy2_obfs))
    if node.hy2_obfs_password:
        params.append(("obfs-password", node.hy2_obfs_password))
    qs = ("?" + urlencode(params)) if params else ""
    return f"hysteria2://{quote(node.password, safe='')}@{node.address}:{node.port}{qs}#{_frag(node.name)}"


def _format_socks(node: Node) -> Optional[str]:
    # PiTun's parser stores socks user in `uuid` and password in
    # `password` — mirror that here. Empty user OR empty password
    # means no auth, in which case we emit the bare host:port form.
    user = node.uuid or ""
    pw = node.password or ""
    if user or pw:
        creds = base64.urlsafe_b64encode(f"{user}:{pw}".encode()).rstrip(b"=").decode()
        return f"socks://{creds}@{node.address}:{node.port}#{_frag(node.name)}"
    return f"socks://{node.address}:{node.port}#{_frag(node.name)}"


_FORMATTERS = {
    "vless":     _format_vless,
    "vmess":     _format_vmess,
    "trojan":    _format_trojan,
    "ss":        _format_ss,
    "hy2":       _format_hy2,
    "socks":     _format_socks,
}


def node_to_uri(node: Node) -> Optional[str]:
    """Render a Node back into a pasteable proxy URI.

    Returns `None` for protocols that don't map to a single URI
    (WireGuard primarily — see module docstring). Callers should
    filter `None`s out of the per-line export stream.
    """
    fn = _FORMATTERS.get(node.protocol)
    if fn is None:
        return None
    return fn(node)


def inbound_client_to_uri(
    inbound: dict,
    client: dict,
    server_host: str,
) -> Optional[str]:
    """Build a pasteable share URI from an x-ui panel inbound + client.

    Mirrors the inbound/stream parsing logic in `export_inbound_client
    _to_node` (api/xui.py) but skips DB persistence — produces a
    transient `Node` and feeds it through `node_to_uri`. Keep the two
    in sync if the panel ever introduces a new transport/security
    knob.

    Returns `None` if the protocol isn't shareable (e.g. unknown
    proto, or vless/trojan client missing its UUID/password).
    """
    # Lazy import to keep `Node` model loading out of import-time —
    # uri_formatter is imported by uri_parser, which loads at startup
    # before SQLModel is fully wired in some test contexts.
    from app.models import Node as _Node

    # v3.0.x panels emit settings/streamSettings as JSON-strings,
    # v3.1.0 as nested objects — parse_inbound_field handles both.
    from app.core.xui_api import parse_inbound_field
    settings = parse_inbound_field(inbound.get("settings"))
    stream = parse_inbound_field(inbound.get("streamSettings"))

    address = server_host
    port = int(inbound.get("port") or 0)
    protocol = (inbound.get("protocol") or "").lower()

    network = stream.get("network") or "tcp"
    security = stream.get("security") or "none"

    # externalProxy ↣ xui-pro reverse-proxy convention (same as the
    # export-to-node endpoint).
    sni_from_ep = ""
    ep_list = stream.get("externalProxy") or []
    if isinstance(ep_list, list) and ep_list:
        ep = ep_list[0] if isinstance(ep_list[0], dict) else {}
        ep_dest = ep.get("dest") or ""
        ep_port = int(ep.get("port") or 0)
        ep_force_tls = (ep.get("forceTls") or "").lower()
        if ep_dest:
            address = ep_dest
            sni_from_ep = ep_dest
        if ep_port:
            port = ep_port
        if ep_force_tls == "tls":
            security = "tls"

    sni = sni_from_ep
    fingerprint = "chrome"
    alpn = ""
    reality_pbk = ""
    reality_sid = ""
    reality_spx = ""
    ws_path = "/"
    ws_host = ""
    grpc_service = ""
    grpc_mode = "gun"
    http_path = "/"
    http_host = ""

    tls_settings = stream.get("tlsSettings") or {}
    if tls_settings:
        sni = tls_settings.get("serverName") or sni
        fingerprint = tls_settings.get("fingerprint") or fingerprint
        alpn_val = tls_settings.get("alpn") or []
        alpn = ",".join(alpn_val) if isinstance(alpn_val, list) else str(alpn_val)

    reality_settings = stream.get("realitySettings") or {}
    if reality_settings:
        sni = reality_settings.get("serverName") or (
            (reality_settings.get("serverNames") or [""])[0]
        ) or sni
        fingerprint = (
            (reality_settings.get("settings") or {}).get("fingerprint")
            or fingerprint
        )
        reality_pbk = (
            (reality_settings.get("settings") or {}).get("publicKey") or ""
        )
        reality_sid = (reality_settings.get("shortIds") or [""])[0]
        reality_spx = (
            (reality_settings.get("settings") or {}).get("spiderX") or ""
        )
        if security == "none" and reality_pbk:
            security = "reality"

    ws_settings = stream.get("wsSettings") or {}
    if ws_settings:
        ws_path = ws_settings.get("path") or "/"
        ws_host = ws_settings.get("host") or ""
        if not ws_host:
            ws_host = (ws_settings.get("headers") or {}).get("Host") or ""

    grpc_settings = stream.get("grpcSettings") or {}
    if grpc_settings:
        grpc_service = grpc_settings.get("serviceName") or ""
        if grpc_settings.get("multiMode") is True:
            grpc_mode = "multi"

    http_settings = (
        stream.get("httpSettings")
        or stream.get("xhttpSettings")
        or {}
    )
    if http_settings:
        http_path = http_settings.get("path") or "/"
        host = http_settings.get("host")
        if isinstance(host, list) and host:
            http_host = host[0]
        elif isinstance(host, str):
            http_host = host

    uuid_val = client.get("id") or ""
    password_val = client.get("password") or ""
    flow_val = client.get("flow") or None
    label = client.get("email") or (uuid_val[:8] if uuid_val else "client")
    name = f"{inbound.get('remark') or 'inbound'}-{label}"

    # SOCKS auth lives in inbound.settings.accounts[0] for the panel,
    # not on the per-client object — special-case it here. The plain
    # x-ui SOCKS5 inbound has one account regardless of "client" list.
    if protocol == "socks":
        accounts = (settings or {}).get("accounts") or []
        if accounts and isinstance(accounts[0], dict):
            uuid_val = accounts[0].get("user") or uuid_val
            password_val = accounts[0].get("pass") or password_val

    transient = _Node(
        name=name,
        protocol=protocol or "vless",
        address=address,
        port=port,
        uuid=uuid_val or None,
        password=password_val or None,
        transport=network,
        tls=security,
        sni=sni or address,
        fingerprint=fingerprint,
        alpn=alpn,
        ws_path=ws_path,
        ws_host=ws_host,
        grpc_service=grpc_service,
        grpc_mode=grpc_mode,
        http_path=http_path,
        http_host=http_host,
        reality_pbk=reality_pbk,
        reality_sid=reality_sid,
        reality_spx=reality_spx,
        flow=flow_val,
    )
    return node_to_uri(transient)
