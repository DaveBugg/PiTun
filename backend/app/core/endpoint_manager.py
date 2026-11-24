"""Lifecycle for extra SOCKS5 proxy endpoints — one xray process each.

Mirrors `XrayManager`'s discipline (config validated with `xray run
-test` before it replaces the working one, crash-restart with backoff,
log pump into the shared stream) but keyed by endpoint id. The manager
never touches the main flow's xray.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.models import (
    BalancerGroup,
    Node,
    ProxyEndpoint,
    RoutingRule,
    Settings,
)

logger = logging.getLogger(__name__)


def endpoints_dir() -> Path:
    """`<data>/endpoints/` — derived from the DB path (no dedicated
    data_dir setting exists; the DB is the anchor)."""
    db = settings.database_url
    prefix = "sqlite:///"
    base = Path(db[len(prefix):]).parent if db.startswith(prefix) else Path("./data")
    return base / "endpoints"


def endpoint_config_path(endpoint_id: int) -> Path:
    return endpoints_dir() / f"{endpoint_id}.json"


async def build_endpoint_config(
    endpoint, session: AsyncSession,
) -> Dict[str, Any]:
    """Assemble the xray config dict for one endpoint from the DB."""
    from app.core.config_gen import generate_endpoint_config

    node = (
        await session.get(Node, endpoint.node_id)
        if endpoint.node_id is not None else None
    )
    balancer = (
        await session.get(BalancerGroup, endpoint.balancer_id)
        if endpoint.balancer_id is not None else None
    )
    if node is None and balancer is None:
        raise ValueError("endpoint has neither a node nor a balancer")

    all_nodes = list((await session.exec(select(Node))).all())

    set_rules: List[RoutingRule] = []
    if endpoint.mode == "routing_set" and endpoint.routing_set_id is not None:
        set_rules = list((await session.exec(
            select(RoutingRule).where(
                RoutingRule.routing_set_id == endpoint.routing_set_id
            )
        )).all())

    # Balancer groups the config needs: the assigned one plus every
    # `balancer:X` action in the set's rules.
    bg_ids = {balancer.id} if balancer is not None else set()
    for rule in set_rules:
        if rule.enabled and rule.action.startswith("balancer:"):
            try:
                bg_ids.add(int(rule.action.split(":", 1)[1]))
            except (ValueError, IndexError):
                continue
    balancer_groups = []
    if bg_ids:
        balancer_groups = [
            bg for bg in
            (await session.exec(
                select(BalancerGroup).where(BalancerGroup.id.in_(bg_ids))  # type: ignore[union-attr]
            )).all()
        ]

    settings_map = {
        row.key: row.value
        for row in (await session.exec(select(Settings))).all()
    }
    return generate_endpoint_config(
        socks_port=endpoint.socks_port,
        node=node,
        balancer=balancer,
        set_rules=set_rules,
        all_nodes=all_nodes,
        balancer_groups=balancer_groups,
        settings_map=settings_map,
    )


async def write_endpoint_config(endpoint_id: int, config: Dict[str, Any]) -> None:
    """Validate with `xray run -test`, then atomically replace the file.

    Keeps `<id>.json.prev` so a bad config never kills a running
    endpoint (same discipline as the main flow's write_config).
    """
    path = endpoint_config_path(endpoint_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # xray picks the config format from the EXTENSION — the temp name
    # must still end in .json.
    tmp = path.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(config, indent=2), encoding="utf-8")

    proc = await asyncio.create_subprocess_exec(
        settings.xray_binary, "run", "-test", "-config", str(tmp),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env={**os.environ, "XRAY_LOCATION_ASSET": str(Path(settings.xray_geoip_path).parent)},
    )
    out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    if proc.returncode != 0:
        tmp.unlink(missing_ok=True)
        tail = (out or b"").decode(errors="replace").strip().splitlines()
        raise ValueError(
            "endpoint config failed xray validation: "
            + (tail[-1][:300] if tail else "unknown error")
        )

    prev = path.with_suffix(".json.prev")
    if path.exists():
        prev.write_bytes(path.read_bytes())
    tmp.replace(path)


class EndpointProcess:
    """One endpoint's xray: process handle, log pump, crash-restart."""

    def __init__(self, endpoint_id: int, name: str) -> None:
        self.endpoint_id = endpoint_id
        self.name = name
        self._process: Optional[asyncio.subprocess.Process] = None
        self._start_time: Optional[float] = None
        self._watch_task: Optional[asyncio.Task] = None
        self._log_task: Optional[asyncio.Task] = None
        # Set by start, cleared only by an explicit stop — a crash keeps
        # it, so the watcher can bring the endpoint back.
        self.wanted = False
        self.last_error: Optional[str] = None
        from app.core.xray import _RestartBackoff
        self._backoff = _RestartBackoff()

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self.is_running else None

    @property
    def uptime(self) -> Optional[float]:
        if self._start_time and self.is_running:
            return time.time() - self._start_time
        return None

    async def start(self) -> None:
        if self.is_running:
            return
        config_path = endpoint_config_path(self.endpoint_id)
        if not config_path.exists():
            raise FileNotFoundError(
                f"endpoint config not found at {config_path}"
            )
        env = os.environ.copy()
        env["XRAY_LOCATION_ASSET"] = str(Path(settings.xray_geoip_path).parent)
        from app.core.resource_limits import xray_env
        env.update(await xray_env())

        self._process = await asyncio.create_subprocess_exec(
            settings.xray_binary, "run", "-config", str(config_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        self._start_time = time.time()
        self.wanted = True
        self.last_error = None
        self._log_task = asyncio.create_task(self._read_logs())
        self._watch_task = asyncio.create_task(self._watch())
        logger.info("endpoint %r started (pid=%d)", self.name, self._process.pid)

    async def stop(self) -> None:
        self.wanted = False
        if self._watch_task:
            self._watch_task.cancel()
            self._watch_task = None
        if self._log_task:
            self._log_task.cancel()
            self._log_task = None
        if self.is_running and self._process is not None:
            try:
                self._process.send_signal(signal.SIGTERM)
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except (asyncio.TimeoutError, ProcessLookupError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
        self._process = None
        self._start_time = None

    async def restart(self) -> None:
        await self.stop()
        self.wanted = True
        await self.start()

    async def _read_logs(self) -> None:
        from app.core.xray import _push_log
        if self._process is None or self._process.stdout is None:
            return
        try:
            async for line in self._process.stdout:
                text = line.decode(errors="replace").rstrip()
                if text:
                    await _push_log(f"[ep:{self.name}] {text}")
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("endpoint %r log pump died: %s", self.name, exc)

    async def _watch(self) -> None:
        """Crash-restart with backoff while `wanted` stays set."""
        assert self._process is not None
        try:
            rc = await self._process.wait()
        except asyncio.CancelledError:
            return
        if not self.wanted:
            return
        self.last_error = f"xray exited with code {rc}"
        logger.warning(
            "endpoint %r xray exited (rc=%s) — restarting with backoff",
            self.name, rc,
        )
        from app.core.events import record_event
        try:
            await record_event(
                category="endpoint.crash", severity="warning",
                title=f"Endpoint {self.name}: xray exited (rc={rc}), restarting",
            )
        except Exception:  # noqa: BLE001
            pass
        delay = self._backoff.next_delay()
        await asyncio.sleep(delay)
        if not self.wanted:
            return
        try:
            self._process = None
            await self.start()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)[:300]
            logger.error("endpoint %r restart failed: %s", self.name, exc)
            # Watch again from the top — the loop is what `wanted` means.
            self._watch_task = asyncio.create_task(self._rewatch())

    async def _rewatch(self) -> None:
        """Restart path after a failed revive: wait, then try again."""
        delay = self._backoff.next_delay()
        await asyncio.sleep(delay)
        if not self.wanted:
            return
        try:
            await self.start()
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)[:300]
            self._watch_task = asyncio.create_task(self._rewatch())


class EndpointManager:
    def __init__(self) -> None:
        self._procs: Dict[int, EndpointProcess] = {}
        self._lock = asyncio.Lock()

    def status(self, endpoint_id: int) -> Dict[str, Any]:
        proc = self._procs.get(endpoint_id)
        if proc is None:
            return {"running": False, "pid": None, "uptime": None, "last_error": None}
        return {
            "running": proc.is_running,
            "pid": proc.pid,
            "uptime": proc.uptime,
            "last_error": proc.last_error,
        }

    def _get(self, endpoint: ProxyEndpoint) -> EndpointProcess:
        proc = self._procs.get(endpoint.id)
        if proc is None or proc.name != endpoint.name:
            proc = EndpointProcess(endpoint.id, endpoint.name)
            self._procs[endpoint.id] = proc
        return proc

    async def apply(self, session: AsyncSession, endpoint: ProxyEndpoint) -> None:
        """(Re)generate config and start/restart the process to match
        `enabled`. Raises on validation failure (nothing is disturbed)."""
        async with self._lock:
            if endpoint.enabled:
                config = await build_endpoint_config(endpoint, session)
                await write_endpoint_config(endpoint.id, config)
                proc = self._get(endpoint)
                if proc.is_running:
                    await proc.restart()
                else:
                    proc.wanted = True
                    await proc.start()
            else:
                await self._stop_unlocked(endpoint.id)

    async def _stop_unlocked(self, endpoint_id: int) -> None:
        proc = self._procs.pop(endpoint_id, None)
        if proc is not None:
            await proc.stop()

    async def stop(self, endpoint_id: int) -> None:
        async with self._lock:
            await self._stop_unlocked(endpoint_id)

    async def sync_from_db(self, session: AsyncSession) -> None:
        """Lifespan hook: make processes match the enabled DB rows."""
        rows = list((await session.exec(select(ProxyEndpoint))).all())
        wanted = {ep.id for ep in rows if ep.enabled}
        for ep_id in list(self._procs):
            if ep_id not in wanted:
                await self.stop(ep_id)
        for ep in rows:
            if not ep.enabled:
                continue
            proc = self._get(ep)
            if proc.is_running:
                continue
            try:
                config = await build_endpoint_config(ep, session)
                await write_endpoint_config(ep.id, config)
                proc.wanted = True
                await proc.start()
            except Exception as exc:  # noqa: BLE001
                proc.last_error = str(exc)[:300]
                logger.error("endpoint %r failed to start on boot: %s", ep.name, exc)

    async def shutdown(self) -> None:
        for ep_id in list(self._procs):
            await self.stop(ep_id)


endpoint_manager = EndpointManager()
