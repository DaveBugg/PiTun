"""Inbound presets for the x-ui panel API (since v1.3.0-beta.7).

PiTun deliberately does NOT expose the full xray inbound surface to
end-users — that's hundreds of fields with subtle interactions, and
half of them require Reality-vs-TLS-vs-WS / xtls-rprx-vision-vs-none
expertise that's noise for someone just wanting "a working VLESS
endpoint behind a fake HTTPS site".

Instead we ship 6 pre-baked preset templates. Each preset:
  * has a stable id + display label
  * declares which user-input fields are needed (`fields`)
  * builds the panel's wire-shape `add_inbound` payload from those
    fields (`build_payload`)
  * declares its capability requirements (needs_domain, supports_reality)

UI shows a 6-card picker, user fills the small subset of fields the
preset actually needs, PiTun calls the panel's
`POST /panel/api/inbounds/add` with the resulting payload.

Preset list (matches the user's beta.7 spec):
  1. vless-reality-vision   — VLESS + TCP + Reality + xtls-rprx-vision
                              No domain. SNI-masked under a third-party
                              host (google.com etc.). Top performance.
  2. vless-xhttp-reality    — VLESS + xhttp + Reality, no flow.
                              No domain. xhttp obfuscation under SNI.
  3. vless-ws               — VLESS + WS over TLS (port 443 typically).
                              Domain required (Let's Encrypt cert + nginx
                              fakesite path).
  4. vless-xhttp            — VLESS + xhttp over TLS. Domain required.
  5. trojan-grpc            — Trojan + gRPC over TLS. Domain required.
  6. socks5                 — Plain SOCKS5 with user/pass auth. No TLS,
                              no SNI — just a convenient socks endpoint.

Adding a preset = add one entry to PRESETS + a build_* function. The
schema-level enum used by the API is derived from PRESETS.keys() so
the registry is the single source of truth.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Optional


# ── Field schema for the UI ─────────────────────────────────────────────────


FieldType = Literal["string", "int", "domain", "sni", "select"]


@dataclass(frozen=True)
class PresetField:
    """One user-input field on the preset form.

    Kept structurally minimal: name + type + (optional) help text +
    (optional) default. The frontend uses `type` to pick an input
    widget — `string` / `int` are obvious, `domain` and `sni` are
    string types with extra validators, `select` requires a
    populated `choices` list.
    """

    name: str
    label: str
    type: FieldType
    required: bool = True
    default: Optional[str] = None
    help: str = ""
    choices: Optional[List[str]] = None
    placeholder: str = ""


@dataclass(frozen=True)
class InboundPreset:
    """One preset template.

    `build_payload(values, **resolved)` accepts:
      * `values` — the dict the user filled out, keyed by `PresetField.name`
      * `resolved` keyword args — server-side helpers the API layer
        injects (uuid, x25519 keys, short-id) to spare the build_*
        function from doing its own panel calls.
    Returns the panel-API payload ready for POST /panel/api/inbounds/add.
    """

    id: str
    label: str
    description: str
    needs_domain: bool
    supports_reality: bool
    protocol: str           # vless | trojan | shadowsocks | socks
    fields: List[PresetField]
    build_payload: Callable[..., Dict[str, Any]] = field(repr=False)


# ── Common building blocks ──────────────────────────────────────────────────


def _sniff() -> Dict[str, Any]:
    """The `sniffing` block we attach to every inbound.

    `routeOnly: false` means xray uses the sniffed dest for both
    routing decisions AND the actual outbound dial — required for
    Reality SNI matching to work end-to-end. `metadataOnly: false`
    keeps the body in the sniff so xtls-rprx-vision's domain-fronting
    detection can fire."""
    return {
        "enabled": True,
        "destOverride": ["http", "tls", "quic", "fakedns"],
        "metadataOnly": False,
        "routeOnly": False,
    }


def _client_email_label() -> str:
    """Return a fresh `pi-XXXXXXXX` label for the inbound's bootstrap
    client. Each inbound gets one default client created at the same
    time as the inbound (panel API requires at least one client in
    `settings.clients` for vless/trojan); subsequent clients are
    added via `addClient`. Re-uses the same `pi-` prefix as standalone
    clients so `/sync` recognises both as PiTun-managed."""
    return f"pi-{secrets.token_hex(4)}"


# ── Preset 1: VLESS + TCP + Reality + xtls-rprx-vision ──────────────────────


def _build_vless_reality_vision(
    values: Dict[str, Any],
    *,
    uuid: str,
    private_key: str,
    public_key: str,
    short_id: str,
) -> Dict[str, Any]:
    sni = values["sni"]
    port = int(values["port"])
    remark = values.get("remark") or "vless-reality-vision"
    settings = {
        "clients": [
            {
                "id": uuid,
                "flow": "xtls-rprx-vision",
                "email": _client_email_label(),
                "limitIp": 0, "totalGB": 0, "expiryTime": 0,
                "enable": True, "tgId": "", "subId": "",
            },
        ],
        "decryption": "none",
        "fallbacks": [],
    }
    stream = {
        "network": "tcp",
        "security": "reality",
        "realitySettings": {
            "show": False, "xver": 0,
            "dest": f"{sni}:443",
            "serverNames": [sni],
            "privateKey": private_key,
            "minClient": "", "maxClient": "", "maxTimediff": 0,
            "shortIds": [short_id],
            "settings": {
                "publicKey": public_key,
                "fingerprint": "chrome",
                "serverName": "",
                "spiderX": "/",
            },
        },
        "tcpSettings": {
            "acceptProxyProtocol": False,
            "header": {"type": "none"},
        },
    }
    return {
        "up": 0, "down": 0, "total": 0,
        "remark": remark, "enable": True, "expiryTime": 0,
        "listen": "", "port": port,
        "protocol": "vless",
        "settings": settings,
        "streamSettings": stream,
        "sniffing": _sniff(),
    }


# ── Preset 2: VLESS + xhttp + Reality, no flow ──────────────────────────────


def _build_vless_xhttp_reality(
    values: Dict[str, Any],
    *,
    uuid: str,
    private_key: str,
    public_key: str,
    short_id: str,
) -> Dict[str, Any]:
    sni = values["sni"]
    port = int(values["port"])
    xhttp_path = values.get("xhttp_path") or "/api/v1"
    remark = values.get("remark") or "vless-xhttp-reality"
    settings = {
        "clients": [
            {
                "id": uuid, "flow": "",
                "email": _client_email_label(),
                "limitIp": 0, "totalGB": 0, "expiryTime": 0,
                "enable": True, "tgId": "", "subId": "",
            },
        ],
        "decryption": "none", "fallbacks": [],
    }
    stream = {
        "network": "xhttp",
        "security": "reality",
        "realitySettings": {
            "show": False, "xver": 0,
            "dest": f"{sni}:443",
            "serverNames": [sni],
            "privateKey": private_key,
            "minClient": "", "maxClient": "", "maxTimediff": 0,
            "shortIds": [short_id],
            "settings": {
                "publicKey": public_key,
                "fingerprint": "chrome",
                "serverName": "", "spiderX": "/",
            },
        },
        "xhttpSettings": {
            "path": xhttp_path,
            "mode": "packet-up",
        },
    }
    return {
        "up": 0, "down": 0, "total": 0,
        "remark": remark, "enable": True, "expiryTime": 0,
        "listen": "", "port": port,
        "protocol": "vless",
        "settings": settings,
        "streamSettings": stream,
        "sniffing": _sniff(),
    }


# ── Preset 3: VLESS + WS over TLS ───────────────────────────────────────────


def _build_vless_ws(
    values: Dict[str, Any],
    *,
    uuid: str,
    domain: str,
) -> Dict[str, Any]:
    port = int(values.get("port") or 443)
    ws_path = values.get("ws_path") or f"/{secrets.token_hex(4)}"
    remark = values.get("remark") or "vless-ws"
    settings = {
        "clients": [
            {
                "id": uuid, "flow": "",
                "email": _client_email_label(),
                "limitIp": 0, "totalGB": 0, "expiryTime": 0,
                "enable": True, "tgId": "", "subId": "",
            },
        ],
        "decryption": "none", "fallbacks": [],
    }
    stream = {
        "network": "ws",
        "security": "tls",
        "tlsSettings": {
            "serverName": domain,
            "alpn": ["http/1.1"],
            "certificates": [],
            "minVersion": "1.2",
            "rejectUnknownSni": False,
        },
        "wsSettings": {
            "path": ws_path,
            "headers": {"Host": domain},
        },
    }
    return {
        "up": 0, "down": 0, "total": 0,
        "remark": remark, "enable": True, "expiryTime": 0,
        "listen": "", "port": port,
        "protocol": "vless",
        "settings": settings,
        "streamSettings": stream,
        "sniffing": _sniff(),
    }


# ── Preset 4: VLESS + xhttp over TLS ────────────────────────────────────────


def _build_vless_xhttp(
    values: Dict[str, Any],
    *,
    uuid: str,
    domain: str,
) -> Dict[str, Any]:
    port = int(values.get("port") or 443)
    xhttp_path = values.get("xhttp_path") or "/api/v1"
    remark = values.get("remark") or "vless-xhttp"
    settings = {
        "clients": [
            {
                "id": uuid, "flow": "",
                "email": _client_email_label(),
                "limitIp": 0, "totalGB": 0, "expiryTime": 0,
                "enable": True, "tgId": "", "subId": "",
            },
        ],
        "decryption": "none", "fallbacks": [],
    }
    stream = {
        "network": "xhttp",
        "security": "tls",
        "tlsSettings": {
            "serverName": domain,
            "alpn": ["h2", "http/1.1"],
            "certificates": [],
            "minVersion": "1.2",
            "rejectUnknownSni": False,
        },
        "xhttpSettings": {
            "path": xhttp_path,
            "mode": "packet-up",
            "host": domain,
        },
    }
    return {
        "up": 0, "down": 0, "total": 0,
        "remark": remark, "enable": True, "expiryTime": 0,
        "listen": "", "port": port,
        "protocol": "vless",
        "settings": settings,
        "streamSettings": stream,
        "sniffing": _sniff(),
    }


# ── Preset 5: Trojan + gRPC over TLS ────────────────────────────────────────


def _build_trojan_grpc(
    values: Dict[str, Any],
    *,
    domain: str,
) -> Dict[str, Any]:
    port = int(values.get("port") or 443)
    password = values.get("password") or secrets.token_urlsafe(20)
    service_name = values.get("service_name") or f"grpc{secrets.token_hex(3)}"
    remark = values.get("remark") or "trojan-grpc"
    settings = {
        "clients": [
            {
                "password": password,
                "email": _client_email_label(),
                "limitIp": 0, "totalGB": 0, "expiryTime": 0,
                "enable": True, "tgId": "", "subId": "",
            },
        ],
        "fallbacks": [],
    }
    stream = {
        "network": "grpc",
        "security": "tls",
        "tlsSettings": {
            "serverName": domain,
            "alpn": ["h2"],
            "certificates": [],
            "minVersion": "1.2",
            "rejectUnknownSni": False,
        },
        "grpcSettings": {
            "serviceName": service_name,
            "multiMode": False,
            "idle_timeout": 60,
        },
    }
    return {
        "up": 0, "down": 0, "total": 0,
        "remark": remark, "enable": True, "expiryTime": 0,
        "listen": "", "port": port,
        "protocol": "trojan",
        "settings": settings,
        "streamSettings": stream,
        "sniffing": _sniff(),
    }


# ── Preset 6: SOCKS5 ────────────────────────────────────────────────────────


def _build_socks5(values: Dict[str, Any], **_: Any) -> Dict[str, Any]:
    port = int(values["port"])
    user = values.get("user") or f"pi{secrets.token_hex(2)}"
    password = values.get("password") or secrets.token_urlsafe(12)
    remark = values.get("remark") or "socks5"
    # SOCKS5 is plain — no TLS, no SNI. xray's "socks" inbound takes a
    # password-list directly in `settings.accounts`.
    settings = {
        "auth": "password",
        "accounts": [
            {"user": user, "pass": password},
        ],
        "udp": True,
        "ip": "127.0.0.1",
    }
    stream = {
        "network": "tcp",
        "security": "none",
        "tcpSettings": {"header": {"type": "none"}},
    }
    return {
        "up": 0, "down": 0, "total": 0,
        "remark": remark, "enable": True, "expiryTime": 0,
        "listen": "", "port": port,
        "protocol": "socks",
        "settings": settings,
        "streamSettings": stream,
        # Sniffing is meaningless for plain socks (the protocol IS
        # the connection metadata) — don't enable.
        "sniffing": {"enabled": False, "destOverride": []},
    }


# ── Registry ─────────────────────────────────────────────────────────────────


PRESETS: Dict[str, InboundPreset] = {
    "vless-reality-vision": InboundPreset(
        id="vless-reality-vision",
        label="VLESS + TCP + Reality + xtls-rprx-vision",
        description=(
            "Top-performance Reality inbound. Masquerades under a "
            "third-party SNI (google.com / cloudflare.com / etc.). "
            "Domain not required — Reality keys do the auth and the "
            "SNI does the cover."
        ),
        needs_domain=False,
        supports_reality=True,
        protocol="vless",
        fields=[
            PresetField(
                name="port", label="Port", type="int",
                default="443",
                help="TCP port the inbound listens on. 443 is fine when "
                     "x-ui-pro's nginx fronts it; otherwise pick a high "
                     "random port to avoid collisions.",
            ),
            PresetField(
                name="sni", label="SNI to masquerade as", type="sni",
                default="www.google.com",
                placeholder="www.google.com",
                help="What the DPI/firewall sees as the connection target. "
                     "Pick something popular and reliably reachable.",
            ),
            PresetField(
                name="remark", label="Remark (optional)", type="string",
                required=False,
                placeholder="vless-reality-vision",
            ),
        ],
        build_payload=_build_vless_reality_vision,
    ),

    "vless-xhttp-reality": InboundPreset(
        id="vless-xhttp-reality",
        label="VLESS + xhttp + Reality",
        description=(
            "Reality with xhttp transport (no flow). Useful when "
            "TCP+vision is fingerprint-detected — xhttp packetises "
            "differently. Same SNI-masked threat model as preset 1."
        ),
        needs_domain=False,
        supports_reality=True,
        protocol="vless",
        fields=[
            PresetField(name="port", label="Port", type="int", default="443"),
            PresetField(
                name="sni", label="SNI to masquerade as", type="sni",
                default="www.google.com",
            ),
            PresetField(
                name="xhttp_path", label="xhttp path", type="string",
                default="/api/v1", required=False,
                help="URL path the xhttp transport tunnels under. "
                     "Pick something boring; clients must use the same.",
            ),
            PresetField(
                name="remark", label="Remark (optional)", type="string",
                required=False,
            ),
        ],
        build_payload=_build_vless_xhttp_reality,
    ),

    "vless-ws": InboundPreset(
        id="vless-ws",
        label="VLESS + WS over TLS",
        description=(
            "VLESS over WebSocket inside a TLS connection. Domain "
            "required (Let's Encrypt cert via x-ui-pro's nginx). "
            "Plays nicely behind Cloudflare CDN."
        ),
        needs_domain=True,
        supports_reality=False,
        protocol="vless",
        fields=[
            PresetField(name="port", label="Port", type="int", default="443"),
            PresetField(
                name="ws_path", label="WebSocket path", type="string",
                required=False,
                placeholder="auto-generated (e.g. /a3b9f1c4)",
                help="The URL path the WS upgrade hits. Keep secret-ish "
                     "— acts as a soft ID.",
            ),
            PresetField(
                name="remark", label="Remark (optional)", type="string",
                required=False,
            ),
        ],
        build_payload=_build_vless_ws,
    ),

    "vless-xhttp": InboundPreset(
        id="vless-xhttp",
        label="VLESS + xhttp over TLS",
        description=(
            "VLESS over xhttp inside a TLS connection (no Reality). "
            "Domain required. Behaves like a plain HTTPS endpoint to "
            "DPI; nginx in front can serve a fakesite on the same port."
        ),
        needs_domain=True,
        supports_reality=False,
        protocol="vless",
        fields=[
            PresetField(name="port", label="Port", type="int", default="443"),
            PresetField(
                name="xhttp_path", label="xhttp path", type="string",
                default="/api/v1", required=False,
            ),
            PresetField(
                name="remark", label="Remark (optional)", type="string",
                required=False,
            ),
        ],
        build_payload=_build_vless_xhttp,
    ),

    "trojan-grpc": InboundPreset(
        id="trojan-grpc",
        label="Trojan + gRPC over TLS",
        description=(
            "Trojan over gRPC (multi-stream HTTP/2). Domain required. "
            "Same fakesite-on-443 pattern as the xhttp-TLS preset, "
            "but with the password-auth Trojan threat model instead "
            "of UUID-based VLESS."
        ),
        needs_domain=True,
        supports_reality=False,
        protocol="trojan",
        fields=[
            PresetField(name="port", label="Port", type="int", default="443"),
            PresetField(
                name="password", label="Password (optional)",
                type="string", required=False,
                placeholder="auto-generated",
                help="Trojan auth secret. Empty → random 20-byte "
                     "URL-safe.",
            ),
            PresetField(
                name="service_name", label="gRPC service name",
                type="string", required=False,
                placeholder="auto-generated (e.g. grpcabc)",
                help="The gRPC service path. Keep secret-ish.",
            ),
            PresetField(
                name="remark", label="Remark (optional)",
                type="string", required=False,
            ),
        ],
        build_payload=_build_trojan_grpc,
    ),

    "socks5": InboundPreset(
        id="socks5",
        label="SOCKS5 (user/pass)",
        description=(
            "Plain SOCKS5 with password auth. NOT a censorship-bypass "
            "endpoint — just a convenient socks proxy. Use when you "
            "need a quick HTTP/SOCKS reachable on the panel host."
        ),
        needs_domain=False,
        supports_reality=False,
        protocol="socks",
        fields=[
            PresetField(name="port", label="Port", type="int", default="1080"),
            PresetField(
                name="user", label="Username (optional)", type="string",
                required=False, placeholder="auto-generated",
            ),
            PresetField(
                name="password", label="Password (optional)", type="string",
                required=False, placeholder="auto-generated",
            ),
            PresetField(
                name="remark", label="Remark (optional)", type="string",
                required=False,
            ),
        ],
        build_payload=_build_socks5,
    ),
}


def get_preset(preset_id: str) -> Optional[InboundPreset]:
    """Look up by id. Returns None on unknown id — callers convert
    to a 400 with a list of valid ids."""
    return PRESETS.get(preset_id)


def list_presets() -> List[InboundPreset]:
    """Stable order for UI rendering. Matches the user's beta.7 spec
    list (Reality variants first, then TLS-fronted, then plain socks)."""
    return list(PRESETS.values())
