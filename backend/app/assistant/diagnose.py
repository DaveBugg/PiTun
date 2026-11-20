"""diagnose: read-only checks for "why does X not work". Collects the box's
health, the active node, the device's routing state and where traffic to a
site goes, and hands the findings to the model to explain."""
from __future__ import annotations

from typing import List

from app.assistant.tools import find_devices

DIAGNOSE_SPEC = {
    "name": "diagnose",
    "description": (
        "Read-only checks for 'X does not work': box health (gateway, DNS, internet direct and via "
        "the proxy, xray, nftables), the active node, the device's routing (policy, rules on its IP) "
        "and, for a site, which DNS and routing rule match and whether it connects. Changes nothing. "
        "Explain the findings to the user and propose a fix (as a plan) if one is clear."),
    "parameters": {"type": "object", "properties": {
        "device": {"type": "string", "description": "device name, hostname, IP or MAC (optional)"},
        "site": {"type": "string", "description": "domain or IP that does not work (optional)"},
    }},
}


def _brief_check(c: dict) -> dict:
    return {k: c.get(k) for k in ("name", "status", "ok", "message", "detail", "latency_ms") if c.get(k) is not None}


async def diagnose(session, args: dict, executor) -> dict:
    from app.models import Device, Node, RoutingRule
    from sqlmodel import select
    out: dict = {"findings": []}
    problems: List[str] = out["findings"]

    hc = await executor.call("GET", "/diagnostics/health-checks")
    if hc["ok"] and isinstance(hc["body"], dict):
        checks = [_brief_check(c) for c in hc["body"].get("checks") or [] if isinstance(c, dict)]
        out["health"] = checks
        for c in checks:
            if c.get("ok") is False or str(c.get("status") or "").lower() in ("fail", "error", "failed", "down"):
                problems.append(f"health check '{c.get('name')}' fails: {c.get('message') or c.get('detail') or ''}".strip())

    st = await executor.call("GET", "/system/status")
    if st["ok"] and isinstance(st["body"], dict):
        b = st["body"]
        out["status"] = {k: b.get(k) for k in ("running", "mode", "active_node_id", "active_node_name",
                                               "nftables_active", "last_xray_validation_error") if k in b}
        if not b.get("running"):
            problems.append("xray is not running")
        if b.get("last_xray_validation_error"):
            problems.append("the last xray config failed validation")
        if b.get("active_node_id"):
            node = await session.get(Node, int(b["active_node_id"]))
            if node is not None:
                out["active_node"] = {"id": node.id, "name": node.name, "online": node.is_online,
                                      "latency_ms": node.latency_ms, "enabled": node.enabled}
                if not node.is_online:
                    problems.append(f"the active node '{node.name}' is offline")
        elif b.get("mode") != "direct":
            problems.append("no active node is set")

    mac = None
    dtext = str(args.get("device") or "").strip()
    if dtext:
        devs = await find_devices(session, {"text": dtext, "k": 3})
        if not devs:
            problems.append(f"no device matches '{dtext}'")
        else:
            d = devs[0]
            mac = d.get("mac")
            info = {k: d.get(k) for k in ("id", "name", "hostname", "ip", "mac", "online", "routing_policy",
                                           "routing_set_id")}
            if len(devs) > 1:
                info["other_matches"] = [x.get("name") or x.get("hostname") for x in devs[1:]]
            from app.assistant.macros import _setting
            mode = await _setting(session, "device_routing_mode", "all")
            info["device_routing_mode"] = mode
            if mode == "include_only" and d.get("routing_policy") != "include":
                problems.append(f"{d.get('name') or d.get('ip')} is not included: the gateway proxies only "
                                "included devices, so this one goes direct")
            if mode == "exclude_list" and d.get("routing_policy") == "exclude":
                problems.append(f"{d.get('name') or d.get('ip')} is excluded from the proxy")
            if not d.get("online"):
                problems.append(f"{d.get('name') or d.get('ip')} is offline (not seen on the network)")
            if d.get("ip"):
                rules = (await session.exec(select(RoutingRule).where(RoutingRule.rule_type == "src_ip"))).all()
                info["rules_on_ip"] = [{"id": r.id, "name": r.name, "action": r.action, "enabled": r.enabled}
                                       for r in rules if d["ip"] in [v.strip() for v in (r.match_value or "").split(",")]]
            dev = await session.get(Device, d["id"])
            if dev is not None and not dev.dhcp_reserved_ip:
                info["note"] = "no DHCP reservation: rules on its IP break when the IP changes"
            out["device"] = info

    site = str(args.get("site") or "").strip()
    if site:
        body = {"target": site, "test_reachability": True, "verify_routing": True}
        if mac:
            body["from_mac"] = mac
        ex = await executor.call("POST", "/diagnostics/explain", None, body)
        if ex["ok"]:
            out["site"] = ex["body"]
            reach = (ex["body"] or {}).get("reachability") if isinstance(ex["body"], dict) else None
            if isinstance(reach, dict) and reach.get("ok") is False:
                problems.append(f"{site} does not connect through the chosen path: {reach.get('detail') or ''}".strip())
        else:
            out["site_error"] = ex["body"]

    errs = await executor.call("GET", "/diagnostics/errors", {"lines": 20})
    if errs["ok"] and isinstance(errs["body"], dict):
        lines = [str(x)[:300] for x in (errs["body"].get("lines") or [])][-10:]
        if lines:
            out["recent_errors"] = lines
    if not problems:
        problems.append("no obvious problem found by these checks")
    return out
