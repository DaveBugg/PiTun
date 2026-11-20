"""Find OpenAI-compatible model servers in the LAN.

Probes the usual ports of home inference servers on the box itself and on
the devices the scanner has seen, and asks each open port for /v1/models.
Only private addresses are probed.
"""
from __future__ import annotations

import asyncio
import ipaddress
from typing import List, Optional

import httpx
from sqlmodel import select

PORTS = {11434: "Ollama", 1234: "LM Studio", 8080: "llama.cpp", 8000: "vLLM"}
MAX_HOSTS = 128
CONNECT_TIMEOUT = 0.6


_LAN = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                           "100.64.0.0/10", "127.0.0.0/8")]


def _private(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and any(a in n for n in _LAN)


async def _hosts(session) -> List[str]:
    from app.models import Device
    hosts = ["127.0.0.1"]
    rows = (await session.exec(select(Device).where(Device.is_online == True))).all()  # noqa: E712
    for d in rows:
        if d.ip and _private(d.ip) and d.ip not in hosts:
            hosts.append(d.ip)
    return hosts[:MAX_HOSTS]


async def _open(host: str, port: int) -> bool:
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), CONNECT_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        return False
    w.close()
    try:
        await w.wait_closed()
    except OSError:
        pass
    return True


async def _models(client: httpx.AsyncClient, base: str) -> Optional[List[str]]:
    try:
        r = await client.get(f"{base}/models")
        if r.status_code != 200:
            return None
        data = r.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    items = data.get("data") or data.get("models") or []
    ids = [str(m.get("id") or m.get("name")) for m in items if isinstance(m, dict) and (m.get("id") or m.get("name"))]
    return ids


async def discover(session) -> List[dict]:
    hosts = await _hosts(session)
    sem = asyncio.Semaphore(64)

    async def check(host: str, port: int):
        async with sem:
            return (host, port) if await _open(host, port) else None

    open_ports = [x for x in await asyncio.gather(*(check(h, p) for h in hosts for p in PORTS)) if x]
    found: List[dict] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(3.0)) as client:
        for host, port in open_ports:
            base = f"http://{host}:{port}/v1"
            ids = await _models(client, base)
            if ids is None:
                continue
            found.append({"base_url": base, "kind": PORTS[port], "host": host, "port": port,
                          "models": ids[:50]})
    return found
