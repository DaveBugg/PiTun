"""Keep secrets and operator infrastructure away from the model.

Before anything reaches the model, credentials (uuids, passwords, keys,
tokens, subscription URLs) and public server addresses are swapped for
stable placeholders like ‹s3›. The model copies a placeholder into a plan
when it needs the value; the executor swaps the real value back. The map is
per conversation, so the same value always gets the same placeholder.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Any, Dict

# Keys whose string values are always secret.
_SECRET_KEYS = {
    "uuid", "password", "passwd", "pass", "private_key", "privatekey", "secret",
    "token", "api_key", "apikey", "short_id", "short_ids", "sid", "hwid", "psk",
    "preshared_key", "pre_shared_key", "obfs_password", "hy2_obfs_password",
    "wg_private_key", "ssh_key", "private_key_pem", "access_token", "auth",
    "url", "sub_url", "subscription_url", "panel_password", "naive_pass",
    "password_hash", "secret_key", "web_base_path",
}
# Keys holding a server address: hidden unless it is a LAN/private address.
_ADDRESS_KEYS = {
    "address", "host", "hostname_public", "server", "server_address", "endpoint",
    "wg_endpoint", "sni", "server_name", "fqdn", "panel_url", "ssh_host", "domain_name",
    "exit_ip", "public_ip", "ip_public",
}
_ADDRESS_KEYS |= {"ws_host", "http_host", "grpc_authority", "authority", "host_header", "relay_host",
                  "exit_host", "panel_host", "server_host"}
# Secret by the shape of the key name (reality_sid, kcp_seed, client_uuid,
# exit_sid, wifi_passphrase, api_token, panel_pass...).
_SECRET_SUFFIX = re.compile(
    r"(pass|passwd|password|passphrase|secret|token|uuid|_sid|sid|seed|psk|preshared_key|private_key|privatekey|"
    r"api_key|apikey|_key_pem|sub_url|subscription_url)$")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_IPV6 = re.compile(r"(?<![\w:])(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{0,4}(?::[0-9a-fA-F]{1,4})*(?![\w:])")
# Credentials inside free text.
_PROXY_URI = re.compile(r"\b(?:vless|vmess|trojan|ss|ssr|hysteria2?|hy2|tuic|wireguard|wg|socks5?|naive\+https|"
                        r"happ|xui|anytls|juicity)://\S+", re.I)
_URL_CRED = re.compile(r"\bhttps?://[^\s/@]+:[^\s/@]+@\S+", re.I)
_URL_SENSITIVE_PARAM = re.compile(
    r"\bhttps?://\S*?(?:[?&](?:"
    r"access_token|api_key|apikey|password|passwd|pass|subscription_url|subscription|sub_url|"
    r"secret|token|auth|key"
    r")=[^\s&]+)\S*",
    re.I,
)
_URL_TOKEN = re.compile(r"\bhttps?://\S*?[A-Za-z0-9_\-]{20,}\S*", re.I)
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
_WG_KEY = re.compile(r"(?i)((?:PrivateKey|PresharedKey)\s*=\s*)(\S+)")
_PLACEHOLDER = re.compile(r"‹s\d+›")
_LAN_NETS = [ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16",
    "100.64.0.0/10", "0.0.0.0/8", "224.0.0.0/4", "255.255.255.255/32",
    "::1/128", "fc00::/7", "fe80::/10", "::/128")]


def _is_lan_ip(v: str) -> bool:
    """LAN / special addresses stay visible; everything else counts as a
    server address (documentation ranges included)."""
    ip = ipaddress.ip_address(v)
    return any(ip in n for n in _LAN_NETS)


def _is_private_host(v: str) -> bool:
    host = v.strip().strip("[]")
    if host.rsplit(":", 1)[-1].isdigit() and host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    if host in ("localhost",) or host.endswith((".local", ".lan", ".internal", ".home.arpa")):
        return True
    try:
        return _is_lan_ip(host)
    except ValueError:
        return False


def _looks_like_token(url: str) -> bool:
    """A random-looking run (letters and digits mixed), not an article slug."""
    for run in re.findall(r"[A-Za-z0-9_\-]{20,}", url):
        if sum(c.isdigit() for c in run) >= 3 and sum(c.isalpha() for c in run) >= 3 and run.count("-") <= 2:
            return True
    return False


def _secret_key(k: str) -> bool:
    k = (k or "").lower()
    return k in _SECRET_KEYS or bool(_SECRET_SUFFIX.search(k))


class Redactor:
    def __init__(self, mapping: Dict[str, str] | None = None):
        self.map: Dict[str, str] = dict(mapping or {})  # placeholder -> value
        self._rev = {v: k for k, v in self.map.items()}
        self.changed = False

    def _ph(self, value: str) -> str:
        if value in self._rev:
            return self._rev[value]
        ph = f"‹s{len(self.map) + 1}›"
        self.map[ph] = value
        self._rev[value] = ph
        self.changed = True
        return ph

    def _scrub_text(self, s: str) -> str:
        def ip(m: re.Match) -> str:
            v = m.group(0)
            try:
                if not _is_lan_ip(v):
                    return self._ph(v)
            except ValueError:
                pass
            return v

        def whole(m: re.Match) -> str:
            return self._ph(m.group(0))

        s = _PROXY_URI.sub(whole, s)
        s = _URL_CRED.sub(whole, s)
        s = _URL_SENSITIVE_PARAM.sub(whole, s)
        s = _URL_TOKEN.sub(lambda m: self._ph(m.group(0)) if _looks_like_token(m.group(0)) else m.group(0), s)
        s = _UUID.sub(whole, s)
        s = _WG_KEY.sub(lambda m: m.group(1) + self._ph(m.group(2)), s)
        s = _IPV4.sub(ip, s)
        s = _IPV6.sub(ip, s)
        # Any value already known as secret, wherever it shows up again
        # (e.g. echoed back inside an error or a question).
        for value, ph in sorted(self._rev.items(), key=lambda kv: -len(kv[0])):
            if len(value) >= 6 and value in s:
                s = s.replace(value, ph)
        return s

    def hide(self, obj: Any, key: str = "") -> Any:
        k = key.lower()
        if isinstance(obj, dict):
            # {"key": "wifi_passphrase", "value": "..."} rows
            if isinstance(obj.get("key"), str) and "value" in obj and _secret_key(obj["key"]):
                return {kk: (self.hide(vv, "password") if kk == "value" else self.hide(vv, str(kk)))
                        for kk, vv in obj.items()}
            return {kk: self.hide(vv, str(kk)) for kk, vv in obj.items()}
        if isinstance(obj, list):
            return [self.hide(v, key) for v in obj]
        if isinstance(obj, str):
            if not obj or _PLACEHOLDER.fullmatch(obj):
                return obj
            if _secret_key(k) and len(obj) >= 4:
                return self._ph(obj)
            if k in _ADDRESS_KEYS and len(obj) >= 4 and not _is_private_host(obj):
                return self._ph(obj)
            return self._scrub_text(obj)
        return obj

    def reveal(self, obj: Any) -> Any:
        if isinstance(obj, dict):
            return {k: self.reveal(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.reveal(v) for v in obj]
        if isinstance(obj, str) and "‹s" in obj:
            return _PLACEHOLDER.sub(lambda m: self.map.get(m.group(0), m.group(0)), obj)
        return obj
