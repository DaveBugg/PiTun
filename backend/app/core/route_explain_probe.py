"""xray-probe: ground-truth routing decision (layer B of the explainer).

When the pure-Python matcher hits a `geosite:`/`geoip:` rule it can't
decide offline, this asks xray itself. It spins a throwaway xray on a
private port that mirrors the REAL routing rules + outbounds + DNS, with
the access log on, sends ONE probe connection to the target, and reads
which `outboundTag` xray chose from the access log line:

    ... accepted tcp:youtube.com:443 [socks-in >> node-1]
                                       ^^^^^^^^^^^^^^^^^^^^

This is the exact decision the running xray would make (geosite/geoip
included) because it IS xray evaluating the same rules. The throwaway
instance uses `block`-less, marked-direct outbounds so the probe never
actually tunnels real traffic through a node — we only care about the
routing verdict, then we tear it down.

Kept separate from route_explain.py so the matcher stays import-light;
this module pulls in asyncio/subprocess/the config generator.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import tempfile
from typing import Optional

from app.config import settings


# access.log line: "... accepted tcp:HOST:PORT [INBOUND >> OUTBOUND]"
_ACCESS_RE = re.compile(r"accepted\s+\w+:[^\s]+\s+\[([^\]]+)\]")


def _reserve_local_port(fallback: int = 15359) -> int:
    """Free loopback port for the throwaway probe instance.

    A fixed port meant two concurrent Explain calls (two tabs, or a second
    click while the first probe still ran) fought over it: the loser's xray
    died with "address already in use" and the UI reported the diagnostic
    itself as broken.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
    except OSError:
        return fallback


def build_probe_config(base_config: dict, *, probe_port: int, log_path: str) -> dict:
    """Transform the live xray config into a throwaway probe config.

    Pure + side-effect-free (the file write + process spawn live in the
    async runner) so it's unit-testable. Three transforms:

      1. Strip `api`/`stats`/`policy` — the `api` service references the
         `api` inbound we remove, and xray refuses to start otherwise.
      2. Replace ALL inbounds with one loopback SOCKS inbound (sniffing
         on) so a domain target is routed by domain rules exactly like
         real client traffic. Drop every inbound-scoped routing rule
         (api/dns-in/per-set inboundTag) — only destination rules
         (domain/ip/port/geosite/geoip) decide our probe.
      3. Stub real proxy outbounds as marked `freedom` (keeping their
         TAGS) so the probe's follow-through dial can't hang on a flaky
         node — we only read the routing verdict from the access log.
    """
    cfg = json.loads(json.dumps(base_config))  # deep copy
    cfg["log"] = {"loglevel": "warning", "access": log_path, "error": ""}
    cfg.pop("api", None)
    cfg.pop("stats", None)
    cfg.pop("policy", None)
    cfg["inbounds"] = [{
        "tag": "probe-in",
        "protocol": "socks",
        "listen": "127.0.0.1",
        "port": probe_port,
        "settings": {"auth": "noauth", "udp": False},
        "sniffing": {"enabled": True, "destOverride": ["http", "tls"], "routeOnly": True},
    }]
    cfg.setdefault("routing", {})["rules"] = [
        dict(r) for r in cfg.get("routing", {}).get("rules", [])
        if not r.get("inboundTag")
    ]
    new_out = []
    for o in cfg.get("outbounds", []):
        tag = o.get("tag", "")
        if tag in ("direct", "dns-out", "block"):
            new_out.append(o)
        else:
            new_out.append({
                "tag": tag, "protocol": "freedom",
                "settings": {"domainStrategy": "AsIs"},
                "streamSettings": {"sockopt": {"mark": 255}},
            })
    cfg["outbounds"] = new_out
    return cfg


async def xray_probe_routing(
    *,
    base_config: dict,
    target: str,
    port: int,
    protocol: str,
) -> dict:
    """Return {"ok": bool, "outbound": str|None, "detail": str}.

    `base_config` is the live generated xray config (from generate_config).
    We clone it, swap the inbounds for a single local SOCKS inbound with
    sniffing, turn on the access log, and probe `target:port`. Routing
    rules + outbounds + dns are preserved so geosite/geoip resolve the
    same way the real instance would.
    """
    xray_bin = settings.xray_binary
    if not os.path.exists(xray_bin):
        return {"ok": False, "outbound": None,
                "detail": "xray binary not found — cannot run live probe"}

    probe_port = _reserve_local_port()
    workdir = tempfile.mkdtemp(prefix="pitun-explain-")
    cfg_path = os.path.join(workdir, "probe.json")
    log_path = os.path.join(workdir, "access.log")

    cfg = build_probe_config(base_config, probe_port=probe_port, log_path=log_path)

    with open(cfg_path, "w") as f:
        json.dump(cfg, f)

    proc = None
    try:
        env = os.environ.copy()
        env["XRAY_LOCATION_ASSET"] = str(os.path.dirname(settings.xray_geoip_path))
        proc = await asyncio.create_subprocess_exec(
            xray_bin, "run", "-config", cfg_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await asyncio.sleep(1.2)
        if proc.returncode is not None:
            err = (await proc.stderr.read()).decode(errors="replace")[:200]
            return {"ok": False, "outbound": None,
                    "detail": f"probe xray failed to start: {err}"}

        # Fire one probe through the SOCKS inbound. We don't need it to
        # succeed end-to-end — the moment xray accepts + routes it, the
        # access log records the chosen outbound.
        await _probe_once(probe_port, target, port)
        await asyncio.sleep(0.4)

        outbound = _read_chosen_outbound(log_path, target)
        if outbound:
            return {"ok": True, "outbound": outbound,
                    "detail": "decision read from live xray access log"}
        return {"ok": False, "outbound": None,
                "detail": "xray produced no routing log line for the probe"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "outbound": None, "detail": f"probe error: {exc}"}
    finally:
        if proc is not None and proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=3)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        for p in (cfg_path, log_path):
            try:
                os.remove(p)
            except OSError:
                pass
        try:
            os.rmdir(workdir)
        except OSError:
            pass


async def _probe_once(socks_port: int, target: str, port: int) -> None:
    """Open a SOCKS5 CONNECT to target:port through 127.0.0.1:socks_port.
    Best-effort — we just need xray to route it and log the decision."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", socks_port), timeout=3
        )
    except Exception:
        return
    try:
        # SOCKS5 no-auth handshake
        writer.write(b"\x05\x01\x00")
        await writer.drain()
        await asyncio.wait_for(reader.readexactly(2), timeout=3)
        # CONNECT to domain:port (ATYP=0x03 domain)
        host = target.encode()[:255]
        req = b"\x05\x01\x00\x03" + bytes([len(host)]) + host + port.to_bytes(2, "big")
        writer.write(req)
        await writer.drain()
        # Read reply (don't care about success — routing already logged)
        try:
            await asyncio.wait_for(reader.read(10), timeout=2)
        except Exception:
            pass
    finally:
        try:
            writer.close()
            await asyncio.wait_for(writer.wait_closed(), timeout=2)
        except Exception:
            pass


def _read_chosen_outbound(log_path: str, target: str) -> Optional[str]:
    """Parse the access log for the `[inbound >> outbound]` of our probe."""
    try:
        with open(log_path, errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    # Prefer a line that mentions our target; fall back to the last
    # routed line.
    chosen = None
    for line in lines:
        m = _ACCESS_RE.search(line)
        if not m:
            continue
        flow = m.group(1)  # "probe-in -> node-1" (xray 26+) or ">>" (older)
        sep = "->" if "->" in flow else ">>"
        parts = [p.strip() for p in flow.split(sep)]
        ob = parts[-1] if parts else None
        if ob:
            chosen = ob
            if target in line:
                return ob
    return chosen
