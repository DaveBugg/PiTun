"""VPS macros: install a panel on a server, issue nodes from an x-ui panel.

The server itself (SSH access) is added by the user on the Servers page;
passwords and keys never go through the chat.
"""
from __future__ import annotations

import secrets
from typing import List, Optional

from sqlmodel import select

from app.assistant.tools import _score, _words

VPS_SPECS: List[dict] = [
    {
        "name": "deploy_server",
        "description": (
            "Install a proxy panel on a VPS that is already on the Servers page (SSH access saved "
            "there). panel 'xui' (default) installs 3x-ui: with a domain it is the full x-ui-pro stack "
            "(nginx + Let's Encrypt), without one a bare panel for Reality. 'naive' needs a domain and "
            "an email; 'wireguard' needs nothing. The install runs a few minutes after approval and its "
            "progress shows in the chat."),
        "parameters": {"type": "object", "properties": {
            "server": {"type": "string", "description": "server name or words from it"},
            "panel": {"type": "string", "enum": ["xui", "naive", "wireguard"]},
            "domain": {"type": "string", "description": "a domain that already points at the server"},
            "email": {"type": "string", "description": "for Let's Encrypt; default admin@<domain>"},
        }, "required": ["server"]},
    },
    {
        "name": "issue_nodes",
        "description": (
            "Issue proxy nodes from an x-ui panel PiTun manages: adds clients to an inbound and turns "
            "each into a PiTun node. Uses an existing inbound (pick one with `inbound`: words from its "
            "remark, or its port) or creates one from a preset when there is none or `preset` is given."),
        "parameters": {"type": "object", "properties": {
            "server": {"type": "string", "description": "server name or words from it"},
            "count": {"type": "integer", "description": "how many nodes, 1-10, default 1"},
            "inbound": {"type": "string", "description": "existing inbound: remark words or port"},
            "preset": {"type": "string", "enum": ["vless-reality-vision", "vless-xhttp-reality", "vless-ws",
                                                   "vless-xhttp", "trojan-grpc", "hysteria2"]},
            "name": {"type": "string", "description": "label prefix for the clients"},
        }, "required": ["server"]},
    },
]

PORT_CANDIDATES = [443, 8443, 2053, 2083, 2087, 2096, 9443]
MAX_NODES = 10


async def _find_server(session, text: str):
    from app.models import Server
    servers = (await session.exec(select(Server))).all()
    if not servers:
        return None, "There are no servers yet. Add the VPS (SSH access) on the Servers page first."
    raw = (text or "").strip().lower()
    exact = [s for s in servers if raw and raw in (s.name.lower(), s.host.lower())]
    if exact:
        return exact[0], None
    words = _words(text)
    scored = sorted(((_score(words, f"{s.name} {s.description or ''} {s.host}"), s) for s in servers),
                    key=lambda x: -x[0])
    if len(servers) == 1 and (not scored or scored[0][0] == 0) and not raw:
        return servers[0], None
    if not scored or scored[0][0] == 0:
        return None, f"No server matches '{text}'. Servers: " + ", ".join(s.name for s in servers)
    if len(scored) > 1 and scored[1][0] == scored[0][0]:
        names = ", ".join(s.name for sc, s in scored if sc == scored[0][0])
        return None, f"Several servers match '{text}': {names}. Which one?"
    return scored[0][1], None


def _ask(text: str):
    from app.assistant.macros import MacroResult
    return MacroResult(question=text)


async def deploy_server(session, args: dict, executor=None):
    from app.assistant.macros import MacroResult
    from app.models import ServerDeployment
    srv, problem = await _find_server(session, str(args.get("server") or ""))
    if srv is None:
        return _ask(problem)
    panel = str(args.get("panel") or "xui").strip().lower()
    if panel not in ("xui", "naive", "wireguard"):
        return _ask("Which panel: x-ui, naive or wireguard?")
    domain = str(args.get("domain") or "").strip().lower().removeprefix("https://").split("/")[0]
    email = str(args.get("email") or "").strip()
    if panel == "naive" and not domain:
        return _ask("Naive needs a domain that points at the server. Which domain?")

    done = (await session.exec(select(ServerDeployment).where(
        ServerDeployment.server_id == srv.id, ServerDeployment.protocol == panel,
        ServerDeployment.status == "deployed"))).first()
    if done is not None and panel == "xui":
        return _ask(f"{srv.name} already has x-ui. To reinstall or switch mode, uninstall it on the "
                    "Servers page first. To get nodes from it, use issue_nodes.")

    config: dict = {}
    if panel in ("xui", "naive"):
        config["domain"] = domain
        if email:
            config["email"] = email
    if panel == "naive" and not email:
        config["email"] = "admin@" + domain
    if panel == "wireguard":
        config["client_name"] = "client1"

    what = {"xui": ("x-ui-pro (nginx + Let's Encrypt) for " + domain) if domain else
            "bare 3x-ui (Reality, no domain)", "naive": f"NaiveProxy on {domain}", "wireguard": "WireGuard"}[panel]
    summary = (f"Install {what} on {srv.name} over SSH. It takes a few minutes; progress shows here. "
               "The panel's new logins and token are saved in PiTun.")
    if panel == "xui":
        summary += " Then nodes can be issued from it (issue_nodes)."
    if done is not None:
        summary += f" {panel} is already installed there: the install runs again."
    if domain:
        summary += " The domain must already point at the server, or the certificate fails."
    steps = [{"method": "POST", "path": f"/servers/{srv.id}/deploy", "body": {"protocol": panel, "config": config},
              "note": f"install {panel} on {srv.name}", "no_undo": True}]
    return MacroResult(title=f"Install {panel} on {srv.name}"[:120], summary=summary, steps=steps)


def _inbound_line(ib: dict) -> str:
    return f"#{ib.get('id')} {ib.get('remark') or '?'} ({ib.get('protocol')}, port {ib.get('port')})"


async def issue_nodes(session, args: dict, executor=None):
    from app.assistant.macros import MacroResult
    from app.core.xui_presets import get_preset
    from app.models import XuiServer
    srv, problem = await _find_server(session, str(args.get("server") or ""))
    if srv is None:
        return _ask(problem)
    xs = (await session.exec(select(XuiServer).where(XuiServer.server_id == srv.id))).first()
    if xs is None:
        return _ask(f"{srv.name} has no x-ui panel in PiTun. Install one first (deploy_server), "
                    "or import its xui:// link on the Servers page.")
    try:
        count = int(args.get("count") or 1)
    except (TypeError, ValueError):
        count = 1
    count = max(1, min(count, MAX_NODES))
    prefix = "".join(ch for ch in str(args.get("name") or "").lower() if ch.isalnum() or ch == "-")[:20]

    res = await executor.call("GET", f"/xui/servers/{xs.id}/inbounds")
    if not res["ok"]:
        detail = res["body"].get("detail") if isinstance(res["body"], dict) else res["body"]
        return _ask(f"Could not read the inbounds of {srv.name}: {detail}. Check the panel on the Servers page.")
    inbounds = [ib for ib in (res["body"] or []) if isinstance(ib, dict)]

    want = str(args.get("inbound") or "").strip()
    preset_id = str(args.get("preset") or "").strip()
    chosen: Optional[dict] = None
    if want:
        if want.isdigit():
            chosen = next((ib for ib in inbounds if str(ib.get("port")) == want or str(ib.get("id")) == want), None)
        if chosen is None:
            words = _words(want)
            scored = sorted(((_score(words, f"{ib.get('remark') or ''} {ib.get('protocol') or ''}"), ib)
                             for ib in inbounds), key=lambda x: -x[0])
            if scored and scored[0][0] > 0:
                if len(scored) > 1 and scored[1][0] == scored[0][0]:
                    lines = "\n".join("- " + _inbound_line(ib) for sc, ib in scored if sc == scored[0][0])
                    return _ask(f"Several inbounds match '{want}':\n{lines}\nWhich one?")
                chosen = scored[0][1]
        if chosen is None:
            lines = "\n".join("- " + _inbound_line(ib) for ib in inbounds) or "(none)"
            return _ask(f"No inbound matches '{want}'. Inbounds on {srv.name}:\n{lines}")
    elif not preset_id:
        usable = [ib for ib in inbounds if ib.get("enable", True)
                  and ib.get("protocol") in ("vless", "trojan", "hysteria", "hysteria2", "vmess")]
        if len(usable) == 1:
            chosen = usable[0]
        elif len(usable) > 1:
            lines = "\n".join("- " + _inbound_line(ib) for ib in usable)
            return _ask(f"{srv.name} has several inbounds:\n{lines}\nWhich one should the nodes use "
                        "(or should I create a new one from a preset)?")

    steps: List[dict] = []
    if chosen is not None:
        ib_ref = str(chosen["id"])
        where = _inbound_line(chosen)
    else:
        preset_id = preset_id or ("vless-ws" if xs.domain else "vless-reality-vision")
        preset = get_preset(preset_id)
        if preset is None:
            return _ask(f"Unknown preset '{preset_id}'.")
        if preset.needs_domain and not xs.domain:
            return _ask(f"Preset {preset_id} needs a domain, but the panel on {srv.name} has none. "
                        "Use vless-reality-vision, vless-xhttp-reality or hysteria2.")
        values: dict = {"remark": (prefix or "pitun") + "-" + preset_id}
        if any(f.name == "port" for f in preset.fields):
            port = await _free_port(executor, xs.id, used={int(ib.get("port") or 0) for ib in inbounds})
            if port is None:
                return _ask(f"None of the usual ports ({', '.join(map(str, PORT_CANDIDATES))}) is free on "
                            f"{srv.name}. Which port should the new inbound use?")
            values["port"] = port
        steps.append({"method": "POST", "path": f"/xui/servers/{xs.id}/inbounds",
                      "body": {"preset_id": preset_id, "values": values},
                      "note": f"new inbound {preset_id}" + (f" on port {values['port']}" if "port" in values else ""),
                      "no_undo": True})
        ib_ref = "{{step1.id}}"
        where = f"a new {preset_id} inbound"

    labels = [f"pi-{prefix + '-' if prefix else ''}{secrets.token_hex(3)}" for _ in range(count)]
    for lb in labels:
        steps.append({"method": "POST", "path": f"/xui/servers/{xs.id}/inbounds/{ib_ref}/clients",
                      "body": {"label": lb}, "note": f"client {lb}", "no_undo": True})
        steps.append({"method": "POST",
                      "path": f"/xui/servers/{xs.id}/inbounds/{ib_ref}/clients/{lb}/export-node",
                      "note": f"node from {lb}", "no_undo": True})
    summary = (f"{count} node(s) from {srv.name}, {where}: clients {', '.join(labels)}, each exported "
               "as a PiTun node. Undo does not apply here; remove clients on the x-ui page.")
    return MacroResult(title=f"{count} node(s) from {srv.name}"[:120], summary=summary, steps=steps)


async def _free_port(executor, xs_id: int, used: set) -> Optional[int]:
    res = await executor.call("GET", f"/xui/servers/{xs_id}/ports",
                              {"candidates": ",".join(map(str, PORT_CANDIDATES))})
    if res["ok"] and isinstance(res["body"], dict):
        for row in res["body"].get("ports") or []:
            if row.get("status") == "free" and row.get("port") not in used:
                return int(row["port"])
        return None
    return next((p for p in PORT_CANDIDATES if p not in used), None)
