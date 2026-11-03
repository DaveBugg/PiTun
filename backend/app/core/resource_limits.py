"""Optional resource limits for PiTun's own containers and for xray.

Off by default: nothing is limited unless the operator sets a value in
Settings. A limit protects the rest of a small box from one runaway part
(xray bursting under heavy transparent-proxy traffic, a stuck sidecar), at the
price that the limited part is killed or throttled when it hits the ceiling.

Applied to the *running* containers with Docker's update call and re-applied
when the backend starts, so it survives a restart of the stack (a recreated
container starts from docker-compose again, then gets the saved limits back).
Docker cannot clear a memory limit on a running container, so "no limit" is
written as the host's whole memory — the same thing in practice.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

import psutil
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.database import get_async_engine
from app.models import Settings as DBSettings

log = logging.getLogger("pitun.resource_limits")

KEY = "resource_limits"
LABEL_KEY = "pitun"

# target -> how to find its container(s)
TARGETS: Dict[str, Dict[str, Any]] = {
    "backend": {"names": ["pitun-backend"], "min_mb": 256,
                "label": "Backend + xray (one container)"},
    "frontend": {"names": ["pitun-frontend"], "min_mb": 32, "label": "Web UI"},
    "nginx": {"names": ["pitun-nginx"], "min_mb": 32, "label": "nginx"},
    "naive": {"label_value": "naive", "min_mb": 64, "label": "NaiveProxy sidecars (each)"},
}
XRAY_MIN_MB = 64
_PERIOD = 100_000


class LimitError(ValueError):
    pass


def docker_client() -> Any:
    import docker

    host = os.environ.get("DOCKER_HOST") or settings.docker_host
    return docker.DockerClient(base_url=host, timeout=15) if host else docker.from_env(timeout=15)


def pitun_containers(client: Any) -> List[Any]:
    """Every running container that belongs to PiTun: ours by name, plus the
    labelled sidecars."""
    seen: Dict[str, Any] = {}
    for c in client.containers.list():
        name = c.name or ""
        if name.startswith("pitun-") or (c.labels or {}).get(LABEL_KEY):
            seen[c.id] = c
    return list(seen.values())


def _containers_for(client: Any, target: str) -> List[Any]:
    spec = TARGETS[target]
    out = []
    for c in client.containers.list():
        if c.name in spec.get("names", []):
            out.append(c)
        elif spec.get("label_value") and (c.labels or {}).get(LABEL_KEY) == spec["label_value"]:
            out.append(c)
    return out


# ── validation ───────────────────────────────────────────────────────────────

def host_limits() -> Dict[str, Any]:
    return {"mem_mb": int(psutil.virtual_memory().total / 1024 / 1024), "cpus": float(psutil.cpu_count() or 1)}


def _num(raw: Any, what: str) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        raise LimitError(f"{what} must be a number")
    return v


def validate(limits: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise and check a limits dict. Raises LimitError with a plain reason."""
    host = host_limits()
    out: Dict[str, Any] = {}
    for target, spec in TARGETS.items():
        cur = (limits or {}).get(target) or {}
        mem = _num(cur.get("mem_mb"), f"{spec['label']}: memory")
        cpus = _num(cur.get("cpus"), f"{spec['label']}: CPUs")
        if mem is not None:
            if mem < spec["min_mb"]:
                raise LimitError(f"{spec['label']}: memory must be at least {spec['min_mb']} MB, or empty for no limit")
            if mem > host["mem_mb"]:
                raise LimitError(f"{spec['label']}: memory is more than this box has ({host['mem_mb']} MB)")
        if cpus is not None:
            if cpus < 0.1:
                raise LimitError(f"{spec['label']}: CPUs must be at least 0.1, or empty for no limit")
            if cpus > host["cpus"]:
                raise LimitError(f"{spec['label']}: CPUs is more than this box has ({host['cpus']:g})")
        out[target] = {"mem_mb": int(mem) if mem is not None else None,
                       "cpus": round(cpus, 2) if cpus is not None else None}
    xray = _num((limits or {}).get("xray_mem_mb"), "xray memory")
    if xray is not None:
        if xray < XRAY_MIN_MB:
            raise LimitError(f"xray memory must be at least {XRAY_MIN_MB} MB, or empty for no limit")
        if xray > host["mem_mb"]:
            raise LimitError(f"xray memory is more than this box has ({host['mem_mb']} MB)")
    out["xray_mem_mb"] = int(xray) if xray is not None else None
    return out


def empty() -> Dict[str, Any]:
    return validate({})


# ── storage ──────────────────────────────────────────────────────────────────

async def load(session: AsyncSession) -> Dict[str, Any]:
    row = (await session.exec(select(DBSettings).where(DBSettings.key == KEY))).first()
    if not row or not row.value:
        return empty()
    try:
        return validate(json.loads(row.value))
    except (ValueError, LimitError):
        return empty()


async def save(session: AsyncSession, limits: Dict[str, Any]) -> None:
    row = (await session.exec(select(DBSettings).where(DBSettings.key == KEY))).first()
    value = json.dumps(limits)
    if row is None:
        session.add(DBSettings(key=KEY, value=value))
    else:
        row.value = value
        session.add(row)
    await session.commit()


async def xray_env() -> Dict[str, str]:
    """Soft memory cap for the xray process: Go's GC works harder before the
    heap passes it. Read when xray starts."""
    try:
        async with AsyncSession(get_async_engine()) as session:
            mb = (await load(session)).get("xray_mem_mb")
    except Exception:  # noqa: BLE001 - never block xray from starting
        return {}
    return {"GOMEMLIMIT": f"{int(mb)}MiB"} if mb else {}


# ── applying ─────────────────────────────────────────────────────────────────

def _update_kwargs(mem_mb: Optional[int], cpus: Optional[float], host_mem_mb: int) -> Dict[str, Any]:
    if mem_mb:
        mem_kwargs = {"mem_limit": f"{int(mem_mb)}m", "memswap_limit": f"{int(mem_mb)}m"}
    else:
        # Docker cannot clear a memory limit on a running container, so "none"
        # is the whole of the host's memory. Raising it needs swap room above
        # it in the same call (runc refuses `swap=-1` together with a rise);
        # `_lift_swap` frees the swap afterwards.
        mem_kwargs = {"mem_limit": f"{host_mem_mb}m", "memswap_limit": f"{host_mem_mb * 2}m"}
    cpu_kwargs = (
        {"cpu_period": _PERIOD, "cpu_quota": int(cpus * _PERIOD)} if cpus
        else {"cpu_period": _PERIOD, "cpu_quota": -1}
    )
    return {**mem_kwargs, **cpu_kwargs}


def _host_config(c: Any) -> Dict[str, Any]:
    """The container's inspected HostConfig (a list entry only has a summary)."""
    try:
        c.reload()
    except Exception:  # noqa: BLE001 - fall back to whatever the listing had
        pass
    return (getattr(c, "attrs", None) or {}).get("HostConfig") or {}


def _is_limited(hc: Dict[str, Any], host_mem_bytes: int) -> bool:
    mem = int(hc.get("Memory") or 0)
    return (
        (0 < mem < host_mem_bytes * 0.98)
        or int(hc.get("CpuQuota") or 0) > 0
        or int(hc.get("NanoCpus") or 0) > 0
    )


def _apply_sync(limits: Dict[str, Any]) -> List[Dict[str, Any]]:
    client = docker_client()
    host_mem = host_limits()["mem_mb"]
    host_bytes = psutil.virtual_memory().total
    results: List[Dict[str, Any]] = []
    for target in TARGETS:
        cur = limits.get(target) or {}
        wants_none = not cur.get("mem_mb") and not cur.get("cpus")
        for c in _containers_for(client, target):
            try:
                if wants_none and not _is_limited(_host_config(c), host_bytes):
                    # nothing to remove: leave the container exactly as it is
                    results.append({"target": target, "container": c.name, "ok": True, "error": None})
                    continue
                c.update(**_update_kwargs(cur.get("mem_mb"), cur.get("cpus"), host_mem))
                if not cur.get("mem_mb"):
                    c.update(memswap_limit=-1)        # now that memory is up, free the swap
                results.append({"target": target, "container": c.name, "ok": True, "error": None})
            except Exception as exc:  # noqa: BLE001
                log.warning("limit update on %s failed: %s", c.name, exc)
                results.append({"target": target, "container": c.name, "ok": False,
                                "error": _short(exc)})
    return results


def _short(exc: Exception) -> str:
    text = str(exc)
    if "Minimum memory limit" in text:
        return "Docker refuses a memory limit that low"
    if "Maximum" in text and "limit" in text:
        return "Docker refuses that limit"
    if "403" in text or "Forbidden" in text:
        return "Docker API access does not allow changing limits (the socket proxy blocks it)"
    return "Docker refused the update"


async def apply(limits: Dict[str, Any]) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_apply_sync, limits)


def _current_sync() -> Dict[str, List[Dict[str, Any]]]:
    client = docker_client()
    host_mem = psutil.virtual_memory().total
    out: Dict[str, List[Dict[str, Any]]] = {}
    for target in TARGETS:
        rows = []
        for c in _containers_for(client, target):
            hc = _host_config(c)
            mem = int(hc.get("Memory") or 0)
            quota, period = int(hc.get("CpuQuota") or 0), int(hc.get("CpuPeriod") or 0)
            nano = int(hc.get("NanoCpus") or 0)
            cpus = nano / 1e9 if nano else (quota / period if quota > 0 and period > 0 else None)
            rows.append({
                "container": c.name,
                "mem_mb": int(mem / 1024 / 1024) if 0 < mem < host_mem * 0.98 else None,
                "cpus": round(cpus, 2) if cpus else None,
            })
        out[target] = rows
    return out


async def current() -> Dict[str, List[Dict[str, Any]]]:
    return await asyncio.to_thread(_current_sync)


async def reapply_on_boot() -> None:
    """Put the saved limits back on a freshly (re)created stack. A no-op when
    nothing was ever set."""
    try:
        async with AsyncSession(get_async_engine()) as session:
            limits = await load(session)
        if not any((limits.get(t) or {}).get("mem_mb") or (limits.get(t) or {}).get("cpus") for t in TARGETS):
            return
        await apply(limits)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not re-apply resource limits at boot: %s", exc)
