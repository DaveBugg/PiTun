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
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
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
        return _IPV4.sub(ip, s)

    def hide(self, obj: Any, key: str = "") -> Any:
        k = key.lower()
        if isinstance(obj, dict):
            return {kk: self.hide(vv, str(kk)) for kk, vv in obj.items()}
        if isinstance(obj, list):
            return [self.hide(v, key) for v in obj]
        if isinstance(obj, str):
            if not obj or _PLACEHOLDER.fullmatch(obj):
                return obj
            if k in _SECRET_KEYS and len(obj) >= 4:
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
