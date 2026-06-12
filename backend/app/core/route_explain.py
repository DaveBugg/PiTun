"""Route Explainer — "why does traffic to X go where it goes?"

Two layers, mirroring how PiTun actually builds the xray config:

  1. A pure-Python matcher (`explain_dns` / `explain_routing`) that
     replays the SAME rule ordering + match semantics as
     `config_gen._build_dns_section` and `_routing_rule_to_xray`.
     Fast, no xray needed, 100% accurate for LITERAL matchers
     (domain:/full:/keyword:/regexp:, dst_ip CIDR, port, mac, src_ip).

  2. For CATEGORY matchers (`geosite:X`, `geoip:X`) the Python layer
     cannot resolve membership offline — that lives inside the .dat
     files which only xray reads. When the walk hits such a rule
     BEFORE a definite literal match, the result is flagged
     `certain=False` with the blocking rule named, and the caller can
     fall back to `xray_probe_routing` (in route_explain_probe.py) for
     ground truth.

This module is matcher-only and import-light (no xray, no network) so
it stays unit-testable and cheap.
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import List, Optional

from app.models import DNSRule, RoutingRule, RoutingSet


# ── Domain / IP / port match primitives ─────────────────────────────────────

# Mirror config_gen's _DOMAIN_PREFIXES. A bare entry is treated as
# `domain:` (suffix match) — same auto-prefix the config generator does.
_KNOWN_PREFIXES = ("domain:", "full:", "keyword:", "regexp:", "geosite:")

# Sentinel returned by match helpers when the pattern is a category
# (geosite:/geoip:) whose membership can't be decided without xray.
NEEDS_XRAY = "needs_xray"


def _domain_pattern_matches(target: str, pattern: str) -> "bool | str":
    """Does `target` (a bare hostname, lowercased) match one xray domain
    pattern? Returns True/False, or NEEDS_XRAY for geosite categories.

    Semantics match xray-core:
      * domain:X  → X itself OR any subdomain (suffix on a label boundary)
      * full:X    → exact equality
      * keyword:X → substring anywhere
      * regexp:X  → regex search
      * geosite:X → category membership (NEEDS_XRAY)
      * bare X    → treated as domain:X (config_gen auto-prefixes)
    """
    t = target.lower().strip(".")
    p = pattern.strip()
    if p.startswith("geosite:"):
        return NEEDS_XRAY
    if p.startswith("full:"):
        return t == p[len("full:"):].lower().strip(".")
    if p.startswith("keyword:"):
        return p[len("keyword:"):].lower() in t
    if p.startswith("regexp:"):
        try:
            return re.search(p[len("regexp:"):], t) is not None
        except re.error:
            return False
    if p.startswith("domain:"):
        d = p[len("domain:"):].lower().strip(".")
    else:
        d = p.lower().strip(".")  # bare → domain:
    return t == d or t.endswith("." + d)


def _ip_pattern_matches(target_ip: str, pattern: str) -> "bool | str":
    """Does an IP match a dst_ip/geoip pattern? geoip:X → NEEDS_XRAY."""
    p = pattern.strip()
    if p.startswith("geoip:"):
        return NEEDS_XRAY
    if p.startswith("geosite:"):
        return False  # geosite never matches an IP
    try:
        ip = ipaddress.ip_address(target_ip)
    except ValueError:
        return False
    cidr = p
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        return ip in net
    except ValueError:
        # bare IP equality
        try:
            return ip == ipaddress.ip_address(cidr)
        except ValueError:
            return False


def _port_matches(port: int, spec: str) -> bool:
    """xray port spec: comma-separated ranges/singletons, e.g. '53,80-443'."""
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            try:
                lo, hi = part.split("-", 1)
                if int(lo) <= port <= int(hi):
                    return True
            except ValueError:
                continue
        else:
            try:
                if int(part) == port:
                    return True
            except ValueError:
                continue
    return False


# ── DNS explanation ─────────────────────────────────────────────────────────

@dataclass
class DnsExplain:
    is_ip: bool
    matched_rule_id: Optional[int] = None
    matched_rule_name: Optional[str] = None
    matched_pattern: Optional[str] = None
    server: Optional[str] = None          # the upstream that will resolve it
    server_type: Optional[str] = None     # plain | dot | doh (rule.dns_type)
    uses_global_upstream: bool = False
    query_strategy: str = "UseIPv4"
    geosite_uncertain: bool = False       # a geosite DNS rule could also match
    note: Optional[str] = None


def explain_dns(
    target: str,
    *,
    is_ip: bool,
    dns_rules: List[DNSRule],
    settings_map: dict,
) -> DnsExplain:
    """Which DNS server will resolve `target`? Mirrors config_gen's
    per-rule server grouping + the global-upstream fallback."""
    qstrat = settings_map.get("dns_query_strategy", "UseIPv4")
    if is_ip:
        return DnsExplain(is_ip=True, query_strategy=qstrat,
                          note="Target is a literal IP — no DNS resolution needed.")

    geosite_seen = False
    for rule in sorted([r for r in dns_rules if r.enabled], key=lambda r: r.order):
        for pat in [d.strip() for d in rule.domain_match.split(",") if d.strip()]:
            m = _domain_pattern_matches(target, pat)
            if m is NEEDS_XRAY:
                geosite_seen = True
                continue
            if m:
                return DnsExplain(
                    is_ip=False,
                    matched_rule_id=rule.id,
                    matched_rule_name=rule.name or None,
                    matched_pattern=pat,
                    server=rule.dns_server,
                    server_type=(rule.dns_type or "plain"),
                    query_strategy=qstrat,
                    geosite_uncertain=geosite_seen,
                )

    # No per-rule match → global upstream (Primary).
    return DnsExplain(
        is_ip=False,
        server=settings_map.get("dns_upstream", "8.8.8.8"),
        server_type=settings_map.get("dns_mode", "plain"),
        uses_global_upstream=True,
        query_strategy=qstrat,
        geosite_uncertain=geosite_seen,
        note=("A geosite DNS rule exists and might match this domain — "
              "the literal matcher can't tell; the global upstream is shown."
              if geosite_seen else None),
    )


# ── Routing explanation ─────────────────────────────────────────────────────

@dataclass
class RoutingExplain:
    matched_rule_id: Optional[int] = None
    matched_rule_name: Optional[str] = None
    matched_rule_type: Optional[str] = None
    matched_value: Optional[str] = None
    action: Optional[str] = None          # proxy | direct | block | node:X | balancer:X
    outbound: Optional[str] = None        # resolved tag: node-N | direct | block
    outbound_label: Optional[str] = None  # human node name if known
    certain: bool = True                  # False when a geosite/geoip rule blocks certainty
    blocking_rule: Optional[str] = None   # the category rule that made us uncertain
    rules_evaluated: int = 0
    set_id: Optional[int] = None
    set_name: Optional[str] = None
    notes: List[str] = field(default_factory=list)


def action_from_outbound(outbound: str) -> str:
    """Inverse of `_resolve_outbound`: given the OUTBOUND tag xray actually
    chose (read from its access log), name the action class.

    Used when the live xray probe overrides the Python best-guess: the
    access log exposes only the chosen outbound, never which rule matched,
    so we re-derive the action from the verdict instead of keeping the
    stale guess from the geosite/geoip candidate rule.
    """
    if outbound == "direct":
        return "direct"
    if outbound == "block":
        return "block"
    if outbound.startswith("node-"):
        return "proxy"
    if outbound.startswith("balancer-"):
        return "balancer"
    return outbound


def _resolve_outbound(action: str, active_node_id: Optional[int]) -> str:
    if action == "proxy":
        return f"node-{active_node_id}" if active_node_id else "direct"
    if action in ("direct", "block"):
        return action
    if action.startswith("node:"):
        try:
            return f"node-{int(action.split(':', 1)[1])}"
        except (ValueError, IndexError):
            return "direct"
    if action.startswith("balancer:"):
        try:
            return f"balancer-{int(action.split(':', 1)[1])}"
        except (ValueError, IndexError):
            return "direct"
    return "direct"


def explain_routing(
    *,
    target: str,
    is_ip: bool,
    resolved_ip: Optional[str],
    port: int,
    protocol: str,
    mode: str,
    bypass_private: bool,
    rules: List[RoutingRule],
    active_node_id: Optional[int],
    node_labels: Optional[dict] = None,
    set_context: Optional[tuple] = None,   # (RoutingSet, [member RoutingRule ids]) or None
) -> RoutingExplain:
    """Walk the routing rules the way xray would and report the first
    match. `resolved_ip` is the DNS-resolved address (used for dst_ip /
    private-bypass checks); falls back to `target` if it's already an IP.

    Honours the system rules PiTun always injects first: RFC1918 bypass
    (when bypass_private) and the port-53 → direct DNS guard.
    """
    node_labels = node_labels or {}
    res = RoutingExplain()
    ip_for_match = target if is_ip else resolved_ip

    if mode == "bypass":
        res.action = "direct"
        res.outbound = "direct"
        res.notes.append("Proxy mode is 'bypass' — ALL traffic goes direct.")
        return res
    if mode == "global":
        # Everything proxied except private (if bypass_private).
        if bypass_private and ip_for_match and _is_private(ip_for_match):
            res.action = "direct"
            res.outbound = "direct"
            res.notes.append("Global mode + private destination → direct.")
            return res
        res.action = "proxy"
        res.outbound = f"node-{active_node_id}" if active_node_id else "direct"
        res.outbound_label = node_labels.get(active_node_id)
        res.notes.append("Global mode — everything routes through the active node.")
        return res

    # ── rules mode ──
    # System rule 1: RFC1918 bypass
    if bypass_private and ip_for_match and _is_private(ip_for_match):
        res.action = "direct"
        res.outbound = "direct"
        res.matched_rule_name = "(system) bypass private networks"
        res.notes.append("Destination is a private/LAN IP — bypassed before user rules.")
        return res
    # System rule 2: DNS port 53 → direct
    if port == 53:
        res.action = "direct"
        res.outbound = "direct"
        res.matched_rule_name = "(system) port 53 → direct"
        res.notes.append("DNS dial (port 53) is force-directed before user rules.")
        return res

    # Per-set context: a device in a routing set evaluates that set's
    # rules first (they carry inboundTag in the real config), THEN the
    # global rules. We model that by ordering set rules ahead.
    set_rule_ids = set()
    if set_context is not None:
        rs, member_ids = set_context
        res.set_id = rs.id
        res.set_name = rs.name
        set_rule_ids = set(member_ids)

    enabled = [r for r in rules if r.enabled]
    # Per-set rules first (sorted by order), then global (NULL set) rules.
    if set_rule_ids:
        ordered = (
            sorted([r for r in enabled if r.id in set_rule_ids], key=lambda r: r.order)
            + sorted([r for r in enabled if getattr(r, "routing_set_id", None) is None],
                     key=lambda r: r.order)
        )
    else:
        ordered = sorted(
            [r for r in enabled if getattr(r, "routing_set_id", None) is None],
            key=lambda r: r.order,
        )

    for rule in ordered:
        res.rules_evaluated += 1
        hit = _rule_matches(rule, target=target, is_ip=is_ip,
                            ip_for_match=ip_for_match, port=port, protocol=protocol)
        if hit is NEEDS_XRAY:
            # First category rule that could match → we lose certainty.
            res.certain = False
            res.blocking_rule = f"[{rule.rule_type}] {rule.name or rule.match_value[:40]}"
            res.notes.append(
                f"Rule '{res.blocking_rule}' is a geosite/geoip category — "
                "membership can't be decided offline. Use 'Verify with live xray' "
                "for the exact decision."
            )
            # Stop: anything after this is uncertain too.
            res.action = rule.action  # best-guess: assume it matches
            res.outbound = _resolve_outbound(rule.action, active_node_id)
            res.outbound_label = _label_for(res.outbound, node_labels)
            res.matched_rule_id = rule.id
            res.matched_rule_name = rule.name or None
            res.matched_rule_type = rule.rule_type
            res.matched_value = rule.match_value
            return res
        if hit:
            res.matched_rule_id = rule.id
            res.matched_rule_name = rule.name or None
            res.matched_rule_type = rule.rule_type
            res.matched_value = rule.match_value
            res.action = rule.action
            res.outbound = _resolve_outbound(rule.action, active_node_id)
            res.outbound_label = _label_for(res.outbound, node_labels)
            return res

    # Catch-all → direct (rules mode default)
    res.action = "direct"
    res.outbound = "direct"
    res.matched_rule_name = "(default catch-all) → direct"
    res.notes.append("No user rule matched — falls through to the default direct route.")
    return res


def _rule_matches(rule: RoutingRule, *, target: str, is_ip: bool,
                  ip_for_match: Optional[str], port: int, protocol: str) -> "bool | str":
    values = [v.strip() for v in rule.match_value.split(",") if v.strip()]
    rt = rule.rule_type
    if rt == "domain":
        if is_ip:
            return False
        saw_geo = False
        for v in values:
            m = _domain_pattern_matches(target, v)
            if m is NEEDS_XRAY:
                saw_geo = True
                continue
            if m:
                return True
        return NEEDS_XRAY if saw_geo else False
    if rt == "geosite":
        return NEEDS_XRAY if not is_ip else False
    if rt == "dst_ip":
        if not ip_for_match:
            return False
        return any(_ip_pattern_matches(ip_for_match, v) is True for v in values)
    if rt == "geoip":
        return NEEDS_XRAY if ip_for_match else False
    if rt == "port":
        return any(_port_matches(port, v) for v in values)
    if rt == "protocol":
        return protocol.lower() in [v.lower() for v in values]
    # mac / src_ip are source-side — not matched by a destination probe here.
    return False


def _is_private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        return a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
    except ValueError:
        return False


def _label_for(outbound: str, node_labels: dict) -> Optional[str]:
    if outbound and outbound.startswith("node-"):
        try:
            return node_labels.get(int(outbound.split("-", 1)[1]))
        except (ValueError, IndexError):
            return None
    return None
