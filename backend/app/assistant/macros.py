"""Macros: whole jobs with plain-text arguments. The server resolves names to
ids and builds the plan itself, so a small model only has to pick the macro
and fill a few words. Every macro ends in a plan the user approves; when a
name is ambiguous it returns a question instead.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import List, Optional

from sqlmodel import select

from app.assistant.tools import _score, _words, find_devices, find_nodes

MACRO_SPECS: List[dict] = [
    {
        "name": "route_device",
        "description": (
            "Send ALL traffic of one LAN device through one proxy node. Give the device and the node "
            "in plain words (names, 'the TV', 'germany fastest'). The server finds both, reuses an "
            "existing rule for the device, fixes the device policy if needed and shows a plan."),
        "parameters": {"type": "object", "properties": {
            "device": {"type": "string", "description": "device name, hostname, IP or MAC"},
            "node": {"type": "string", "description": "words from the node's name; may be empty when country is set"},
            "country": {"type": "string", "description": "ISO code of the node's country, e.g. DE"},
            "prefer": {"type": "string", "enum": ["relevance", "speed", "latency"]},
        }, "required": ["device"]},
    },
    {
        "name": "build_circle",
        "description": (
            "Create or update a node circle (automatic rotation of the active node) from a "
            "subscription's nodes, optionally without some countries. Other enabled circles are "
            "switched off in the same plan unless keep_others is true."),
        "parameters": {"type": "object", "properties": {
            "subscription": {"type": "string", "description": "subscription name"},
            "exclude_countries": {"type": "array", "items": {"type": "string"},
                                  "description": "ISO codes, e.g. ['RU', 'BY']"},
            "mode": {"type": "string", "enum": ["best", "random", "sequential"]},
            "name": {"type": "string", "description": "circle name; default '<subscription> circle'"},
            "min_speed_mbps": {"type": "number"},
            "max_latency_ms": {"type": "integer"},
            "keep_others": {"type": "boolean"},
        }, "required": ["subscription"]},
    },
    {
        "name": "add_site_rule",
        "description": (
            "Route a website or service: direct, through the proxy, through one node, or block it. "
            "Put the service's domains you know in `domains` (main site, CDN, API, media); the server "
            "adds domains the LAN really looked up and reuses an existing rule with the same name."),
        "parameters": {"type": "object", "properties": {
            "site": {"type": "string", "description": "service name or main domain, e.g. 'example.com'"},
            "action": {"type": "string", "enum": ["direct", "proxy", "block", "node"]},
            "node": {"type": "string", "description": "for action 'node': words from the node's name"},
            "domains": {"type": "array", "items": {"type": "string"}},
        }, "required": ["site", "action"]},
    },
]
from app.assistant.diagnose import DIAGNOSE_SPEC  # noqa: E402
from app.assistant.macros_vps import VPS_SPECS  # noqa: E402

MACRO_SPECS += VPS_SPECS + [DIAGNOSE_SPEC]
MACRO_NAMES = {m["name"] for m in MACRO_SPECS}
# Macros that only read and return a report instead of a plan.
REPORT_MACROS = {"diagnose"}

_DOMAIN = re.compile(r"^(?:(?:full|domain|keyword|regexp):)?[a-z0-9*-]+(?:\.[a-z0-9-]+)+$|^geosite:[a-z0-9@!_-]+$")
_SLD = {"co", "com", "net", "org", "gov", "edu", "ac", "or", "ne"}


@dataclass
class MacroResult:
    question: Optional[str] = None      # ask the user this instead of a plan
    title: str = ""
    summary: str = ""
    steps: List[dict] = field(default_factory=list)


def _ask(text: str) -> MacroResult:
    return MacroResult(question=text)


def _node_line(n: dict) -> str:
    bits = [f"#{n['id']} {n['name']}"]
    if n.get("country"):
        bits.append(n["country"])
    if n.get("latency_ms"):
        bits.append(f"{n['latency_ms']} ms")
    if n.get("speed_mbps"):
        bits.append(f"{n['speed_mbps']:.0f} Mbit/s")
    if not n.get("online"):
        bits.append("offline")
    return ", ".join(bits)


async def _pick_node(session, text: str, country: str = "", prefer: str = "") -> tuple[Optional[dict], List[dict]]:
    """Best node for a description + runners-up. Nodes are not asked back on
    ties: the best online one wins and the others are listed in the plan."""
    args = {"text": text or "", "k": 6, "sort": prefer or ("speed" if not text else "relevance")}
    if country:
        args["countries"] = [country]
    if not text and not country:
        return None, []
    found = await find_nodes(session, {**args, "online_only": True}, keep_score=True)
    if not found:
        found = await find_nodes(session, args, keep_score=True)
    if not found:
        return None, []
    return found[0], found[1:4]


async def _setting(session, key: str, default: str = "") -> str:
    from app.models import Settings as DBSettings
    row = (await session.exec(select(DBSettings).where(DBSettings.key == key))).first()
    return row.value if row and row.value is not None else default


async def route_device(session, args: dict) -> MacroResult:
    from app.models import Device, RoutingRule
    dtext = str(args.get("device") or "").strip()
    if not dtext:
        return _ask("Which device? Give its name, IP or MAC.")
    devs = await find_devices(session, {"text": dtext, "k": 5}, keep_score=True)
    if not devs:
        return _ask(f"No device matches '{dtext}'. Check the name on the Devices page, or run a scan.")
    if len(devs) > 1 and devs[0]["_s"] == devs[1]["_s"]:
        lines = "\n".join(f"- #{d['id']} {d['name'] or d['hostname'] or '?'} ({d['ip'] or 'no IP'})"
                          for d in devs if d["_s"] == devs[0]["_s"])
        return _ask(f"Several devices match '{dtext}':\n{lines}\nWhich one?")
    dev = devs[0]
    dname = dev["name"] or dev["hostname"] or dev["mac"]
    if not dev["ip"]:
        return _ask(f"{dname} has no known IP yet. Connect it to the network (or run a scan) and try again.")

    node, others = await _pick_node(session, str(args.get("node") or "").strip(),
                                    str(args.get("country") or "").strip().upper(),
                                    str(args.get("prefer") or ""))
    if node is None:
        return _ask("Which node? Give words from its name or a country.")

    steps: List[dict] = []
    rule_name = f"{dname} via {node['name']}"[:120]
    rules = (await session.exec(select(RoutingRule).where(RoutingRule.rule_type == "src_ip"))).all()
    def ips(r) -> List[str]:
        return [v.strip() for v in (r.match_value or "").split(",") if v.strip()]
    # Reuse only a rule for this device alone: a rule shared with other
    # devices would move them too.
    existing = next((r for r in rules if r.routing_set_id is None and ips(r) == [dev["ip"]]), None)
    shared = next((r for r in rules if r.routing_set_id is None and dev["ip"] in ips(r) and len(ips(r)) > 1), None)
    if existing:
        steps.append({"method": "PATCH", "path": f"/routing/rules/{existing.id}",
                      "body": {"name": rule_name, "action": f"node:{node['id']}", "enabled": True},
                      "note": f"update the existing rule '{existing.name}'"})
    else:
        order = max(0, min(10, (shared.order or 10) - 1)) if shared else 10
        steps.append({"method": "POST", "path": "/routing/rules",
                      "body": {"name": rule_name, "rule_type": "src_ip", "match_value": dev["ip"],
                               "action": f"node:{node['id']}", "order": order, "enabled": True},
                      "note": f"all traffic of {dname} ({dev['ip']}) through {node['name']}"
                              + (f"; placed before the shared rule '{shared.name}'" if shared else "")})

    mode = await _setting(session, "device_routing_mode", "all")
    device = await session.get(Device, dev["id"])
    policy = device.routing_policy if device else "default"
    if mode == "include_only" and policy != "include":
        steps.append({"method": "PATCH", "path": f"/devices/{dev['id']}", "body": {"routing_policy": "include"},
                      "note": "the gateway proxies only included devices: include this one"})
    elif mode == "exclude_list" and policy == "exclude":
        steps.append({"method": "PATCH", "path": f"/devices/{dev['id']}", "body": {"routing_policy": "default"},
                      "note": "this device was excluded from the proxy: put it back"})

    summary = f"{dname} ({dev['ip']}) -> {_node_line(node)}."
    if others:
        summary += " Other candidates: " + "; ".join(_node_line(o) for o in others) + "."
    if not dev.get("dhcp_reserved_ip"):
        summary += " The rule follows the IP; the device has no DHCP reservation, so its IP may change."
    return MacroResult(title=f"{dname} through {node['name']}"[:120], summary=summary, steps=steps)


async def build_circle(session, args: dict) -> MacroResult:
    from app.core.circle_filters import node_country, normalize_countries
    from app.models import Node, NodeCircle, Subscription
    stext = str(args.get("subscription") or "").strip()
    subs = (await session.exec(select(Subscription))).all()
    if not subs:
        return _ask("There are no subscriptions yet. Add one on the Subscriptions page first.")
    words = _words(stext)
    scored = sorted(((_score(words, s.name), s) for s in subs), key=lambda x: -x[0])
    exact = [s for s in subs if s.name.lower() == stext.lower()]
    if exact:
        sub = exact[0]
    elif not scored or scored[0][0] == 0:
        return _ask(f"No subscription matches '{stext}'. Subscriptions: " + ", ".join(s.name for s in subs))
    elif len(scored) > 1 and scored[1][0] == scored[0][0]:
        names = ", ".join(s.name for sc, s in scored if sc == scored[0][0])
        return _ask(f"Several subscriptions match '{stext}': {names}. Which one?")
    else:
        sub = scored[0][1]

    excluded = normalize_countries(args.get("exclude_countries") or [])
    nodes = (await session.exec(select(Node).where(Node.subscription_id == sub.id))).all()
    members, dropped = [], {}
    for n in nodes:
        if not n.enabled:
            continue
        cc = node_country(getattr(n, "country", None), n.name)
        if cc and cc in excluded:
            dropped[cc] = dropped.get(cc, 0) + 1
            continue
        members.append(n.id)
    if len(members) < 2:
        return _ask(f"Only {len(members)} usable node(s) left in '{sub.name}' after the filters; "
                    "a circle needs at least 2.")

    mode = args.get("mode") if args.get("mode") in ("best", "random", "sequential") else "best"
    name = str(args.get("name") or f"{sub.name} circle").strip()[:80]
    body = {
        "name": name, "enabled": True, "mode": mode, "node_ids": members,
        "interval_min": 10, "interval_max": 25,
        "max_latency_ms": int(args.get("max_latency_ms") or 1500),
        "min_speed_mbps": float(args.get("min_speed_mbps") or 0),
        "subscription_ids": [sub.id], "subscription_id": sub.id,
        "excluded_countries": excluded, "rotate_when_healthy": False,
    }
    circles = (await session.exec(select(NodeCircle))).all()
    same = next((c for c in circles if c.name.lower() == name.lower()), None) or \
        next((c for c in circles if c.subscription_id == sub.id), None)
    steps: List[dict] = []
    if same:
        steps.append({"method": "PATCH", "path": f"/nodecircle/{same.id}", "body": body,
                      "note": f"update the existing circle '{same.name}'"})
    else:
        steps.append({"method": "POST", "path": "/nodecircle", "body": body,
                      "note": f"new circle with {len(members)} nodes"})
    if not args.get("keep_others"):
        for c in circles:
            if c.enabled and (same is None or c.id != same.id):
                steps.append({"method": "PATCH", "path": f"/nodecircle/{c.id}", "body": {"enabled": False},
                              "note": f"switch off circle '{c.name}' (only one circle should drive the active node)"})
    summary = f"Circle '{name}' over {len(members)} nodes of '{sub.name}', mode {mode}."
    if dropped:
        summary += " Left out: " + ", ".join(f"{cc} ({k})" for cc, k in sorted(dropped.items())) + "."
    if excluded:
        summary += " The excluded countries stay out on every subscription refresh."
    return MacroResult(title=f"Circle {name}"[:120], summary=summary, steps=steps)


def _base_domain(d: str) -> str:
    parts = d.lower().strip(".").split(".")
    if len(parts) > 2 and parts[-2] in _SLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


async def add_site_rule(session, args: dict) -> MacroResult:
    from app.models import DomainStat, RoutingRule
    site = str(args.get("site") or "").strip()
    if not site:
        return _ask("Which site or service?")
    action = str(args.get("action") or "").strip().lower()
    if action not in ("direct", "proxy", "block", "node"):
        return _ask("Should the site go direct, through the proxy, through a specific node, or be blocked?")

    domains: List[str] = []

    def add(d: str) -> None:
        d = d.strip().lower().removeprefix("https://").removeprefix("http://").split("/")[0]
        if d and _DOMAIN.match(d) and d not in domains:
            domains.append(d)

    for d in args.get("domains") or []:
        if isinstance(d, str):
            add(d)
    if "." in site:
        add(site)
    keyword = (site.split(".")[0] if "." in site else _words(site)[0] if _words(site) else "").lower()
    seen_in_lan: List[str] = []
    if len(keyword) >= 3:
        stats = (await session.exec(select(DomainStat).where(DomainStat.domain.contains(keyword))
                                    .order_by(DomainStat.hits.desc()).limit(40))).all()
        for st in stats:
            base = _base_domain(st.domain)
            if keyword in base and base not in domains and base not in seen_in_lan:
                seen_in_lan.append(base)
        for d in seen_in_lan[:10]:
            add(d)
    # Drop entries already covered by a shorter suffix in the list.
    domains = [d for d in domains if not any(d != o and d.endswith("." + o) for o in domains)]
    if not domains:
        return _ask(f"I don't know the domains of '{site}'. Name its main domain (e.g. example.com).")

    act = action
    node_txt = ""
    if action == "node":
        node, _ = await _pick_node(session, str(args.get("node") or "").strip())
        if node is None:
            return _ask("Which node should the site go through?")
        act = f"node:{node['id']}"
        node_txt = f" through {_node_line(node)}"

    rules = (await session.exec(select(RoutingRule).where(RoutingRule.rule_type == "domain"))).all()
    same = next((r for r in rules if r.routing_set_id is None and r.name.lower() == site.lower()), None)
    steps: List[dict] = []
    if same:
        merged = [v.strip() for v in (same.match_value or "").split(",") if v.strip()]
        merged += [d for d in domains if d not in merged]
        steps.append({"method": "PATCH", "path": f"/routing/rules/{same.id}",
                      "body": {"match_value": ",".join(merged), "action": act, "enabled": True},
                      "note": f"update the existing rule '{same.name}'"})
    else:
        steps.append({"method": "POST", "path": "/routing/rules",
                      "body": {"name": site[:120], "rule_type": "domain", "match_value": ",".join(domains),
                               "action": act, "order": 50, "enabled": True},
                      "note": f"{len(domains)} domain(s)"})
    verb = {"direct": "direct (no proxy)", "proxy": "through the proxy", "block": "blocked"}.get(action, "")
    summary = f"{site}: {verb or node_txt.strip()}. Domains: {', '.join(domains)}."
    if seen_in_lan:
        summary += " Some of them come from what the LAN really looked up."
    return MacroResult(title=f"{site}: {action}"[:120], summary=summary, steps=steps)


async def run_macro(session, name: str, args: dict, executor=None) -> MacroResult:
    from app.assistant.macros_vps import deploy_server, issue_nodes
    if name in ("deploy_server", "issue_nodes"):
        fn = {"deploy_server": deploy_server, "issue_nodes": issue_nodes}[name]
        return await fn(session, args, executor)
    fn = {"route_device": route_device, "build_circle": build_circle, "add_site_rule": add_site_rule}[name]
    return await fn(session, args)


def describe(result: MacroResult) -> str:
    return json.dumps({"title": result.title, "summary": result.summary, "steps": result.steps},
                      ensure_ascii=False)
