"""
NaiveProxy sidecar lifecycle manager.

Each NaiveProxy node gets its own dedicated Docker container that runs
the `naive` client and exposes a SOCKS5 listener on 127.0.0.1:<internal_port>.
xray-core (running on the host) connects to that loopback port as a regular
SOCKS outbound — so from xray's perspective a naive node looks identical
to any other socks node.

The manager talks to the Docker daemon via tecnativa/docker-socket-proxy
(http://127.0.0.1:2375). It never opens /var/run/docker.sock directly.

Container naming:
    pitun-naive-<node_id>
Config file on host (bind-mounted into container as /etc/naive/config.json):
    {NAIVE_CONFIG_DIR}/<node_id>.json
Labels:
    pitun=naive
    pitun_node_id=<id>

All CRUD on naive nodes goes through this manager — never talk to Docker
directly from the API layer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import stat
from typing import Any, Dict, List, Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings
from app.models import Node

logger = logging.getLogger(__name__)


_CONTAINER_LABEL_KEY = "pitun"
_CONTAINER_LABEL_VAL = "naive"
_NODE_ID_LABEL = "pitun_node_id"


class NaiveManagerError(RuntimeError):
    """Raised when a sidecar lifecycle operation fails."""


class NaiveManager:
    """Async-friendly wrapper around the docker-py SDK.

    docker-py is synchronous, so every SDK call is wrapped in
    `asyncio.to_thread()`. Per-node locks serialize start/stop/restart on
    the same container so rapid CRUD requests never race.
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._locks: Dict[int, asyncio.Lock] = {}

    # ── Docker client ────────────────────────────────────────────────────────

    def _get_client(self) -> Any:
        """Lazy-init the docker client. Uses DOCKER_HOST env var."""
        if self._client is None:
            import docker  # imported lazily so tests can mock without SDK installed

            # docker.from_env() respects DOCKER_HOST. Our compose sets it to
            # tcp://127.0.0.1:2375 (docker-socket-proxy). Fall back to the
            # config default if env is not set (e.g. local dev without compose).
            host = os.environ.get("DOCKER_HOST") or settings.docker_host
            if host:
                self._client = docker.DockerClient(base_url=host, timeout=15)
            else:
                self._client = docker.from_env(timeout=15)
        return self._client

    def _lock_for(self, node_id: int) -> asyncio.Lock:
        lock = self._locks.get(node_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[node_id] = lock
        return lock

    # ── Paths / names ────────────────────────────────────────────────────────

    @staticmethod
    def container_name(node_id: int) -> str:
        return f"pitun-naive-{node_id}"

    @staticmethod
    def config_path(node_id: int) -> str:
        return os.path.join(settings.naive_config_dir, f"{node_id}.json")

    # ── Port allocation ──────────────────────────────────────────────────────

    async def allocate_port(self, session: AsyncSession, node_id: int) -> int:
        """Find a free port in [NAIVE_PORT_RANGE_START, NAIVE_PORT_RANGE_END]
        that is not already claimed by another naive node in the DB.
        Raises NaiveManagerError if exhausted.
        """
        stmt = select(Node).where(
            Node.protocol == "naive", Node.id != node_id
        )
        rows = (await session.exec(stmt)).all()
        used = {n.internal_port for n in rows if n.internal_port is not None}

        lo = int(settings.naive_port_range_start)
        hi = int(settings.naive_port_range_end)
        for p in range(lo, hi + 1):
            if p not in used:
                return p
        raise NaiveManagerError(
            f"No free naive ports in range {lo}-{hi} (all {len(used)} used)"
        )

    # ── Config file ──────────────────────────────────────────────────────────

    @staticmethod
    def _build_config(node: Node) -> Dict[str, Any]:
        if not node.internal_port:
            raise NaiveManagerError(
                f"Node {node.id} has no internal_port allocated"
            )
        user = node.uuid or ""
        pwd = node.password or ""
        auth = f"{user}:{pwd}@" if (user or pwd) else ""
        cfg: Dict[str, Any] = {
            # listen only on loopback — never exposed to LAN
            "listen": f"socks://127.0.0.1:{node.internal_port}",
            "proxy": f"https://{auth}{node.address}:{node.port}",
        }
        if node.naive_padding:
            cfg["padding"] = True
        if node.sni and node.sni != node.address:
            # naive supports SNI override via "host-resolver-rules" mechanism
            # but for now we rely on the domain itself. Future: expose host-resolver.
            pass
        return cfg

    @classmethod
    def _write_config_sync(cls, node: Node) -> None:
        path = cls.config_path(node.id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = json.dumps(cls._build_config(node), indent=2)
        # Write atomically: tmp file + rename
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        os.replace(tmp, path)

    @classmethod
    def _delete_config_sync(cls, node_id: int) -> None:
        path = cls.config_path(node_id)
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Failed to delete naive config %s: %s", path, exc)

    # ── Container lifecycle (sync helpers) ───────────────────────────────────

    def _remove_container_sync(self, name: str) -> None:
        import docker

        client = self._get_client()
        try:
            c = client.containers.get(name)
        except docker.errors.NotFound:
            return
        try:
            c.remove(force=True)
        except docker.errors.APIError as exc:
            raise NaiveManagerError(f"Failed to remove {name}: {exc}") from exc

    def _run_container_sync(self, node: Node) -> None:
        import docker

        client = self._get_client()
        name = self.container_name(node.id)
        cfg_host = self.config_path(node.id)

        # Ensure config exists on host before starting (bind mount requires it)
        if not os.path.exists(cfg_host):
            raise NaiveManagerError(
                f"Config file missing: {cfg_host}"
            )

        try:
            client.containers.run(
                image=settings.naive_image,
                name=name,
                detach=True,
                # Naive must talk to 127.0.0.1:<internal_port>; xray runs on
                # the host. Simplest: naive in host netns, listens on loopback.
                network_mode="host",
                restart_policy={"Name": "unless-stopped"},
                volumes={
                    cfg_host: {
                        "bind": "/etc/naive/config.json",
                        "mode": "ro",
                    },
                },
                labels={
                    _CONTAINER_LABEL_KEY: _CONTAINER_LABEL_VAL,
                    _NODE_ID_LABEL: str(node.id),
                },
                log_config=docker.types.LogConfig(
                    type=docker.types.LogConfig.types.JSON,
                    config={"max-size": "10m", "max-file": "3"},
                ),
                mem_limit="64m",
                # Security hardening — naive needs only outbound TCP
                read_only=True,
                # Small tmpfs so naive can write transient state (QUIC session
                # cache, crash dumps) without losing read-only protection on
                # everything else.
                tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
            )
        except docker.errors.ImageNotFound as exc:
            raise NaiveManagerError(
                f"Image {settings.naive_image} not found — "
                f"run `docker build -t {settings.naive_image} docker/naive/` first"
            ) from exc
        except docker.errors.APIError as exc:
            raise NaiveManagerError(
                f"Failed to start {name}: {exc}"
            ) from exc

    # ── Public async API ─────────────────────────────────────────────────────

    async def start_node(self, node: Node) -> None:
        """Create config + start/restart the sidecar container (idempotent).

        Safe to call on an already-running container: it will be recreated
        with the current config.
        """
        if node.protocol != "naive":
            return
        if not node.internal_port:
            raise NaiveManagerError(
                f"Node {node.id} has no internal_port — call allocate_port first"
            )

        lock = self._lock_for(node.id)
        async with lock:
            await asyncio.to_thread(self._write_config_sync, node)
            name = self.container_name(node.id)
            # Remove any existing container (stale config) then re-create
            await asyncio.to_thread(self._remove_container_sync, name)
            await asyncio.to_thread(self._run_container_sync, node)
            logger.info("Started naive sidecar for node %d (%s) on 127.0.0.1:%d",
                        node.id, node.name, node.internal_port)

    async def stop_node(self, node_id: int) -> None:
        """Stop + remove sidecar container and delete its config file.

        Safe to call on a missing container / missing file.
        """
        lock = self._lock_for(node_id)
        async with lock:
            name = self.container_name(node_id)
            await asyncio.to_thread(self._remove_container_sync, name)
            await asyncio.to_thread(self._delete_config_sync, node_id)
            logger.info("Stopped naive sidecar for node %d", node_id)

    async def restart_node(self, node: Node) -> None:
        """Explicit restart (= start_node)."""
        await self.start_node(node)

    async def get_status(self, node_id: int) -> Dict[str, Any]:
        """Return container status: exists/running/status/started_at."""
        def _inner() -> Dict[str, Any]:
            import docker

            client = self._get_client()
            try:
                c = client.containers.get(self.container_name(node_id))
            except docker.errors.NotFound:
                return {
                    "exists": False,
                    "running": False,
                    "status": "missing",
                    "started_at": None,
                }
            c.reload()
            state = (c.attrs.get("State") or {})
            return {
                "exists": True,
                "running": state.get("Running", False),
                "status": state.get("Status", c.status),
                "started_at": state.get("StartedAt"),
                "restart_count": c.attrs.get("RestartCount", 0),
            }

        return await asyncio.to_thread(_inner)

    async def get_logs(self, node_id: int, tail: int = 200) -> str:
        """Return last `tail` lines of the sidecar container's stdout/stderr."""
        def _inner() -> str:
            import docker

            client = self._get_client()
            try:
                c = client.containers.get(self.container_name(node_id))
            except docker.errors.NotFound:
                return ""
            data = c.logs(tail=tail, timestamps=True, stdout=True, stderr=True)
            if isinstance(data, bytes):
                return data.decode("utf-8", errors="replace")
            return str(data)

        return await asyncio.to_thread(_inner)

    async def list_all_sidecars(self) -> List[Dict[str, Any]]:
        """Return all pitun-naive-* containers visible to the daemon."""
        def _inner() -> List[Dict[str, Any]]:
            client = self._get_client()
            containers = client.containers.list(
                all=True,
                filters={"label": f"{_CONTAINER_LABEL_KEY}={_CONTAINER_LABEL_VAL}"},
            )
            return [
                {
                    "name": c.name,
                    "node_id": int(c.labels.get(_NODE_ID_LABEL, "0") or 0),
                    "status": c.status,
                }
                for c in containers
            ]

        return await asyncio.to_thread(_inner)

    async def sync_all(self, session: AsyncSession) -> None:
        """Reconcile running sidecars with DB state.

        Called on backend startup and whenever bulk changes might have
        occurred. Ensures:
          - every enabled naive node has a running container with fresh config
          - disabled / deleted nodes have no container
          - orphan containers (no matching DB row) are removed
        """
        nodes = list(
            (await session.exec(select(Node).where(Node.protocol == "naive"))).all()
        )
        enabled_ids = {n.id for n in nodes if n.enabled and n.internal_port}

        # Running containers labelled as ours
        try:
            existing = await self.list_all_sidecars()
        except Exception as exc:  # daemon unreachable / socket-proxy down
            logger.warning("Cannot list sidecars (docker unreachable?): %s", exc)
            return

        existing_node_ids = {c["node_id"] for c in existing if c["node_id"]}

        # Stop orphans (container exists but no enabled node)
        for nid in existing_node_ids - enabled_ids:
            try:
                await self.stop_node(nid)
            except Exception as exc:
                logger.warning("Failed to stop orphan sidecar %d: %s", nid, exc)

        # Start / restart each enabled naive node
        for node in nodes:
            if not node.enabled:
                continue
            if not node.internal_port:
                # Should not happen — port is allocated at node creation.
                # Skip silently; user will see it as offline in the UI.
                logger.warning(
                    "Naive node %d has no internal_port — skipping sync", node.id
                )
                continue
            try:
                await self.start_node(node)
            except Exception as exc:
                logger.warning(
                    "Failed to (re)start sidecar for node %d: %s", node.id, exc
                )


# Singleton used by the API layer
naive_manager = NaiveManager()
