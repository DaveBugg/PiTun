"""What PiTun itself costs the box: xray, the backend, and each PiTun container.

The host-wide gauges say "the Pi is busy"; this says by whom. xray and the
backend are read with psutil; containers (the web UI, nginx, the naive
sidecars, ...) come from Docker's own accounting. CPU is "% of one core", the
way `top` and `docker stats` show it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Dict, List, Optional

import psutil

log = logging.getLogger("pitun.proc_stats")

_CACHE_TTL = 4.0
_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_procs: Dict[int, psutil.Process] = {}


def _mb(n: float) -> float:
    return round(n / (1024 * 1024), 1)


def _proc_row(name: str, kind: str, pid: Optional[int], note: str = "") -> Optional[Dict[str, Any]]:
    if not pid:
        return None
    try:
        p = _procs.get(pid)
        if p is None or not p.is_running():
            p = _procs[pid] = psutil.Process(pid)
            p.cpu_percent(None)                  # prime: the first reading is always 0
        with p.oneshot():
            rss = p.memory_info().rss
            cpu = p.cpu_percent(None)
            threads = p.num_threads()
        return {"name": name, "kind": kind, "cpu_percent": round(cpu, 1), "mem_mb": _mb(rss),
                "mem_limit_mb": None, "note": note or f"{threads} threads"}
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        _procs.pop(pid, None)
        return None


def host_totals() -> Dict[str, Any]:
    return {"mem_total_mb": _mb(psutil.virtual_memory().total), "cpu_count": psutil.cpu_count() or 1}


def container_numbers(stats: Dict[str, Any], host_mem_bytes: int) -> Dict[str, Any]:
    """CPU %, working-set memory and the limit out of one `docker stats` sample,
    the way `docker stats` computes them."""
    cpu = stats.get("cpu_stats") or {}
    pre = stats.get("precpu_stats") or {}
    cpu_delta = (cpu.get("cpu_usage") or {}).get("total_usage", 0) - (pre.get("cpu_usage") or {}).get("total_usage", 0)
    sys_delta = cpu.get("system_cpu_usage", 0) - pre.get("system_cpu_usage", 0)
    online = cpu.get("online_cpus") or len((cpu.get("cpu_usage") or {}).get("percpu_usage") or []) or 1
    pct = (cpu_delta / sys_delta * online * 100.0) if sys_delta > 0 and cpu_delta >= 0 else 0.0

    mem = stats.get("memory_stats") or {}
    inner = mem.get("stats") or {}
    cache = inner.get("inactive_file", inner.get("total_inactive_file", inner.get("cache", 0)))
    used = max(int(mem.get("usage", 0)) - int(cache or 0), 0)
    limit = int(mem.get("limit", 0) or 0)
    limited = bool(limit) and limit < host_mem_bytes * 0.98
    return {
        "cpu_percent": round(pct, 1),
        "mem_mb": _mb(used),
        "mem_limit_mb": _mb(limit) if limited else None,
    }


def _docker_rows() -> List[Dict[str, Any]]:
    """One thread: list PiTun's containers and sample each (a sample takes
    about a second, so they run side by side)."""
    from concurrent.futures import ThreadPoolExecutor

    from app.core.resource_limits import docker_client, pitun_containers

    client = docker_client()
    host_mem = psutil.virtual_memory().total
    containers = pitun_containers(client)

    def sample(c):
        try:
            nums = container_numbers(c.stats(stream=False), host_mem)
            return {"name": c.name, "kind": "container", **nums,
                    "note": (c.labels or {}).get("pitun") or c.status}
        except Exception as exc:  # noqa: BLE001 - one container must not hide the rest
            log.debug("stats for %s failed: %s", c.name, exc)
            return None

    if not containers:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(containers))) as pool:
        return [r for r in pool.map(sample, containers) if r]


async def collect(force: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["ts"] < _CACHE_TTL:
        return _cache["data"]

    rows: List[Dict[str, Any]] = []
    from app.core.xray import xray_manager

    xray = _proc_row("xray", "process", xray_manager.pid)
    if xray:
        rows.append(xray)
    me = _proc_row("pitun backend", "process", os.getpid())
    if me:
        rows.append(me)

    docker_error: Optional[str] = None
    try:
        rows.extend(await asyncio.wait_for(asyncio.to_thread(_docker_rows), timeout=25))
    except Exception as exc:  # noqa: BLE001
        docker_error = "Docker is not reachable from the backend" if "Connection" in type(exc).__name__ or "docker" in str(exc).lower() else type(exc).__name__
        log.debug("container stats unavailable: %s", exc)

    rows.sort(key=lambda r: r["mem_mb"], reverse=True)
    data = {"processes": rows, "host": host_totals(), "docker_error": docker_error}
    _cache.update(ts=now, data=data)
    return data
