"""Which values a routing rule of each type may hold.

Used twice: the API refuses a rule with values of the wrong kind (with a
hint, e.g. domains saved as an IP rule), and the xray config generator drops
such values from rules already in the DB (imports, older versions), so one
bad rule can never stop xray for the whole LAN.
"""
from __future__ import annotations

import ipaddress
import re
from typing import List, Optional, Tuple

_DOMAIN = re.compile(r"^(?:\*\.)?(?:[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?\.)*[a-z0-9-]{1,63}$", re.I)
_DOMAIN_PREFIXES = ("domain:", "full:", "keyword:", "regexp:", "geosite:", "ext:", "geoip:")
_IP_PREFIXES = ("geoip:", "ext:")
_TAG = re.compile(r"^!?[a-z0-9_@.!:-]+$", re.I)
_HOSTISH = re.compile(r"^[^\s/\:,;'\"<>]+$")
_MAC = re.compile(r"^[0-9a-f]{2}([:-][0-9a-f]{2}){5}$", re.I)
_PROTOCOLS = {"http", "tls", "quic", "bittorrent"}


def _is_ip(v: str) -> bool:
    try:
        ipaddress.ip_network(v.strip("[]"), strict=False)
        return True
    except ValueError:
        return False


def _looks_like_domain(v: str) -> bool:
    return "." in v and bool(_DOMAIN.match(v)) and not _is_ip(v)


def _port_ok(v: str) -> bool:
    parts = v.split("-")
    if len(parts) > 2 or not all(p.isdigit() for p in parts):
        return False
    nums = [int(p) for p in parts]
    return all(0 <= n <= 65535 for n in nums) and nums == sorted(nums)


def value_ok(rule_type: str, v: str) -> bool:
    v = v.strip()
    if not v:
        return False
    if rule_type in ("dst_ip", "src_ip"):
        return _is_ip(v) or (v.startswith(_IP_PREFIXES) and len(v) > 6)
    if rule_type == "domain":
        if v.startswith("regexp:"):
            return len(v) > 7
        if v.startswith(_DOMAIN_PREFIXES):
            v = v.split(":", 1)[1]
            return bool(v) and " " not in v and "/" not in v
        # Permissive on purpose (IDN like пример.рф): refuse only what can
        # never be a host name.
        return bool(_HOSTISH.match(v)) and not _is_ip(v)
    if rule_type == "port":
        return _port_ok(v)
    if rule_type == "protocol":
        return v.lower() in _PROTOCOLS
    if rule_type in ("geoip", "geosite"):
        return bool(_TAG.match(v))
    if rule_type == "mac":
        return bool(_MAC.match(v))
    return True


def split_values(match_value: str) -> List[str]:
    return [v.strip() for v in (match_value or "").split(",") if v.strip()]


def check(rule_type: str, match_value: str) -> Optional[str]:
    """Human-readable problem with a rule's values, or None when all fit."""
    values = split_values(match_value)
    if not values:
        return "the rule has no values"
    bad = [v for v in values if not value_ok(rule_type, v)]
    if not bad:
        return None
    shown = ", ".join(bad[:5]) + (f" and {len(bad) - 5} more" if len(bad) > 5 else "")
    if rule_type in ("dst_ip", "src_ip") and any(_looks_like_domain(v) for v in bad):
        return (f"{shown}: these are domains, but the rule type is IP. "
                "Choose the rule type 'domain' for domain names.")
    if rule_type == "domain" and any(_is_ip(v) for v in bad):
        return f"{shown}: these are IP addresses; choose the rule type 'dst_ip' for them."
    if rule_type == "domain" and any("://" in v or "/" in v for v in bad):
        return f"{shown}: enter domains only (example.com), without https:// or a path."
    hint = {
        "dst_ip": "IP addresses or subnets (203.0.113.5, 198.51.100.0/24) or geoip:XX",
        "src_ip": "LAN IP addresses or subnets",
        "domain": "domain names (example.com) or domain:/full:/keyword:/regexp:/geosite: entries",
        "port": "ports or ranges (443, 1000-2000)",
        "protocol": "one of http, tls, quic, bittorrent",
        "geoip": "geoip tags (ru, private)",
        "geosite": "geosite tags (youtube, category-ads-all)",
        "mac": "MAC addresses (aa:bb:cc:dd:ee:ff)",
    }.get(rule_type, "valid values")
    return f"not valid for a '{rule_type}' rule: {shown}. Expected {hint}."


def keep_valid(rule_type: str, values: List[str]) -> Tuple[List[str], List[str]]:
    """-> (values xray accepts, dropped values)."""
    ok = [v for v in values if value_ok(rule_type, v)]
    return ok, [v for v in values if v not in ok]
