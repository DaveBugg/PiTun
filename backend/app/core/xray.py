"""xray process lifecycle management."""
import asyncio
import logging
import os
import signal
import time
from pathlib import Path
from typing import Optional

from app.config import settings

_tun_active: bool = False

logger = logging.getLogger(__name__)

log_queue: asyncio.Queue = asyncio.Queue(maxsize=2000)


class XrayManager:
    def __init__(self) -> None:
        self._process: Optional[asyncio.subprocess.Process] = None
        self._start_time: Optional[float] = None
        self._version: Optional[str] = None
        self._log_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

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

    @property
    def version(self) -> Optional[str]:
        return self._version

    async def get_version(self) -> Optional[str]:
        if not Path(settings.xray_binary).exists():
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                settings.xray_binary, "version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            line = stdout.decode().splitlines()[0] if stdout else ""
            parts = line.split()
            return parts[1] if len(parts) > 1 else line
        except Exception as exc:
            logger.warning("Cannot get xray version: %s", exc)
            return None

    async def start(self) -> None:
        async with self._lock:
            await self._start_unlocked()

    async def stop(self) -> None:
        async with self._lock:
            await self._stop_unlocked()

    async def restart(self) -> None:
        async with self._lock:
            await self._stop_unlocked()
            await self._start_unlocked()

    async def reload(self) -> None:
        async with self._lock:
            if self.is_running:
                await self._stop_unlocked()
            await self._start_unlocked()
            logger.info("xray reloaded (restart with new config)")

    async def _start_unlocked(self) -> None:
        if self.is_running:
            logger.warning("xray already running (pid=%d)", self.pid)
            return

        config_path = Path(settings.xray_config_path)
        if not config_path.exists():
            raise FileNotFoundError(f"xray config not found at {config_path}")

        xray_bin = settings.xray_binary
        if not Path(xray_bin).exists():
            raise FileNotFoundError(f"xray binary not found at {xray_bin}")

        os.makedirs(config_path.parent, exist_ok=True)

        env = os.environ.copy()
        env["XRAY_LOCATION_ASSET"] = str(Path(settings.xray_geoip_path).parent)

        self._process = await asyncio.create_subprocess_exec(
            xray_bin, "run", "-config", str(config_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        self._start_time = time.time()
        logger.info("xray started (pid=%d)", self._process.pid)

        self._log_task = asyncio.create_task(self._read_logs())

    async def _stop_unlocked(self) -> None:
        global _tun_active
        if not self.is_running:
            return

        logger.info("Stopping xray (pid=%d)", self._process.pid)
        try:
            self._process.send_signal(signal.SIGTERM)
            await asyncio.wait_for(self._process.wait(), timeout=10)
        except (asyncio.TimeoutError, ProcessLookupError):
            logger.warning("xray did not stop in time, killing")
            try:
                self._process.kill()
            except ProcessLookupError:
                pass
        finally:
            self._process = None
            self._start_time = None
            if self._log_task:
                self._log_task.cancel()
                self._log_task = None

        if _tun_active:
            try:
                from app.core.tun import teardown_tun, tun_exists
                if await tun_exists():
                    await teardown_tun()
                    logger.info("tun0 interface removed")
            except Exception as exc:
                logger.warning("Failed to teardown tun0: %s", exc)
            _tun_active = False

    async def _read_logs(self) -> None:
        if self._process is None or self._process.stdout is None:
            return
        try:
            async for line in self._process.stdout:
                text = line.decode(errors="replace").rstrip()
                await asyncio.gather(
                    _push_log(text),
                    _maybe_process_dns(text),
                )
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.debug("Log reader error: %s", exc)

        if self._process and self._process.returncode not in (None, 0):
            logger.error("xray process died unexpectedly (rc=%s)", self._process.returncode)
            await _apply_kill_switch_if_enabled()
            async with self._lock:
                await _auto_restart_if_enabled()


async def _auto_restart_if_enabled(*, from_boot: bool = False) -> None:
    """Bring xray up if `auto_restart_xray=true` and a node is active.

    `from_boot=True` is set by the lifespan startup hook — that's a
    normal container start, not a watchdog recovery, so we suppress
    the `xray.auto_restarted` event in that case (otherwise every
    deploy/reboot pollutes the Recent Events feed with a fake "xray
    auto-restarted" warning, which is what we want to avoid surfacing).
    The crash-monitor (`XrayManager._monitor`) calls without args, so
    real unexpected exits still emit the event.
    """
    try:
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.database import get_async_engine
        from app.models import Settings as DBSettings, Node

        async with AsyncSession(get_async_engine()) as session:
            row = (await session.exec(
                select(DBSettings).where(DBSettings.key == "auto_restart_xray")
            )).first()
            enabled = row and row.value.lower() == "true"
            if not enabled:
                return

            node_count = len((await session.exec(select(Node).where(Node.enabled == True))).all())
            if node_count == 0:
                logger.warning("Auto-restart skipped: no enabled nodes configured")
                return

        logger.info("Auto-restarting xray after crash (waiting 3s)...")
        await asyncio.sleep(3)

        from app.api.system import _regenerate_and_write
        from app.core.nftables import nftables_manager

        async with AsyncSession(get_async_engine()) as session:
            await _regenerate_and_write(session)

        config_path = settings.xray_config_path
        if Path(config_path).exists():
            proc = await asyncio.create_subprocess_exec(
                settings.xray_binary, "run", "-test", "-config", config_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                logger.error("Auto-restart aborted: config verification failed:\n%s",
                             stderr.decode(errors="replace")[-500:])
                return

        async with AsyncSession(get_async_engine()) as session:
            settings_map = {r.key: r.value for r in (await session.exec(select(DBSettings))).all()}
            from app.models import RoutingRule
            rules = list((await session.exec(select(RoutingRule).where(RoutingRule.enabled == True))).all())
            bypass_macs = [r.match_value for r in rules if r.rule_type == "mac" and r.action == "direct"]

            # Restore device routing policies (include/exclude)
            from app.core.device_scanner import get_device_macs_for_mode
            device_info = await get_device_macs_for_mode(session)
            device_mode = device_info["mode"]
            if device_mode == "exclude_list":
                bypass_macs.extend(device_info["exclude_macs"])

            mode = settings_map.get("mode", "rules")
            if mode == "bypass":
                await nftables_manager.flush()
            else:
                await nftables_manager.apply_rules(
                    inbound_mode=settings_map.get("inbound_mode", "tproxy"),
                    bypass_macs=bypass_macs,
                    include_macs=device_info["include_macs"] if device_mode == "include_only" else None,
                    device_routing_mode=device_mode,
                    tproxy_tcp=int(settings_map.get("tproxy_port_tcp", "7893")),
                    tproxy_udp=int(settings_map.get("tproxy_port_udp", "7894")),
                    dns_port=int(settings_map.get("dns_port", "5353")),
                    block_quic=settings_map.get("block_quic", "true").lower() == "true",
                    kill_switch=settings_map.get("kill_switch", "false").lower() == "true",
                )

        await xray_manager._start_unlocked()
        logger.info("xray auto-restarted successfully")
        if not from_boot:
            from app.core.events import record_event
            await record_event(
                category="xray.auto_restarted",
                severity="warning",
                title="xray auto-restarted",
                details="xray exited unexpectedly and was relaunched by the watchdog",
                # 60s dedup so a restart loop doesn't bury other events.
                dedup_window_sec=60,
            )

    except Exception as exc:
        logger.error("Auto-restart failed: %s", exc)
        if not from_boot:
            from app.core.events import record_event
            await record_event(
                category="xray.auto_restart_failed",
                severity="error",
                title="xray auto-restart failed",
                details=str(exc),
                dedup_window_sec=60,
            )


async def _apply_kill_switch_if_enabled() -> None:
    try:
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.database import get_async_engine
        from app.models import Settings as DBSettings, Node
        from app.core.nftables import nftables_manager

        async with AsyncSession(get_async_engine()) as session:
            row = (await session.exec(
                select(DBSettings).where(DBSettings.key == "kill_switch")
            )).first()
            enabled = row and row.value.lower() == "true"
            if not enabled:
                return
            nodes = (await session.exec(select(Node).where(Node.enabled == True))).all()
            vpn_ips = list({n.address for n in nodes if n.address})

        await nftables_manager.apply_kill_switch(vpn_server_ips=vpn_ips)
    except Exception as exc:
        logger.error("Failed to apply kill switch on crash: %s", exc)


async def _push_log(line: str) -> None:
    if log_queue.full():
        try:
            log_queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
    await log_queue.put(line)


async def _maybe_process_dns(line: str) -> None:
    global _dns_log_enabled, _dns_log_checked

    if "app/dns:" not in line:
        return
    try:
        now = time.time()
        if now - _dns_log_checked > _DNS_LOG_CACHE_TTL:
            from sqlmodel import select
            from sqlmodel.ext.asyncio.session import AsyncSession
            from app.database import get_async_engine
            from app.models import Settings as DBSettings
            async with AsyncSession(get_async_engine()) as session:
                row = (await session.exec(
                    select(DBSettings).where(DBSettings.key == "dns_query_log_enabled")
                )).first()
                _dns_log_enabled = bool(row and row.value.lower() == "true")
            _dns_log_checked = now

        if _dns_log_enabled:
            from app.core.dns_logger import process_log_line
            await process_log_line(line)
    except Exception:
        pass


_dns_log_enabled: bool = False
_dns_log_checked: float = 0.0
_DNS_LOG_CACHE_TTL = 60.0  # seconds

xray_manager = XrayManager()
