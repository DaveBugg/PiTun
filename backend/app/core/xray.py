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

# Legacy single queue. Kept for backwards compatibility with anything
# still reading it directly; the WS endpoint now uses `subscribe_logs()`.
log_queue: asyncio.Queue = asyncio.Queue(maxsize=2000)

# One queue PER log-stream subscriber.
#
# With a single shared queue, every connected viewer competed for the
# same items (`Queue.get()` hands a line to exactly ONE waiter), so two
# open Logs tabs each saw a random half of the stream — and a tab left
# open in the background (the page connects even while paused) quietly
# ate lines the foreground tab never saw. Fan-out gives each subscriber
# the full stream; a slow consumer drops its own oldest lines and can't
# stall the producer. Same shape as JobManager's per-subscriber queues.
_log_subscribers: "set[asyncio.Queue]" = set()


def subscribe_logs(maxsize: int = 2000) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
    _log_subscribers.add(q)
    return q


def unsubscribe_logs(q: asyncio.Queue) -> None:
    _log_subscribers.discard(q)


class XrayManager:
    # Default deadline for acquiring `_lock` before the API endpoint
    # returns 503. Picked to be longer than the longest legitimate
    # critical section (config write + `xray -test` + reload, ~15 s
    # worst case) but short enough that one stuck reload doesn't wedge
    # every API endpoint that touches xray forever.
    #
    # Without it, one stuck holder (e.g. a sync getaddrinfo() inside
    # _apply_nftables hanging on a broken /etc/resolv.conf) queues every
    # subsequent routing-rule POST and /system/restart indefinitely. The
    # timeout drains the queue as 503 so the operator sees an error
    # rather than a frozen UI.
    LOCK_ACQUIRE_TIMEOUT: float = 30.0

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

    class LockBusyError(RuntimeError):
        """Raised when `_lock` is held past `LOCK_ACQUIRE_TIMEOUT`.

        API handlers should catch this and surface it as 503 Service
        Unavailable so the operator sees a clear "xray is busy" error
        instead of a hung response. The earlier behavior (unbounded
        `async with self._lock`) wedged every routing/system endpoint
        when one critical section hung — see class-level docstring."""

    async def _acquire_lock_or_raise(self, op_name: str) -> None:
        """Acquire `_lock` with a deadline. Raises LockBusyError if
        the lock is still held when the timeout elapses."""
        try:
            await asyncio.wait_for(
                self._lock.acquire(),
                timeout=self.LOCK_ACQUIRE_TIMEOUT,
            )
        except asyncio.TimeoutError as exc:
            logger.error(
                "xray_manager._lock busy >%.0fs while attempting %s — "
                "previous operation may be stuck. Check `docker logs "
                "pitun-backend` for the last 'xray' line.",
                self.LOCK_ACQUIRE_TIMEOUT, op_name,
            )
            raise XrayManager.LockBusyError(
                f"xray manager is busy (lock held >{self.LOCK_ACQUIRE_TIMEOUT:.0f}s)"
            ) from exc

    async def start(self) -> None:
        await self._acquire_lock_or_raise("start")
        try:
            await self._start_unlocked()
        finally:
            self._lock.release()

    async def stop(self) -> None:
        await self._acquire_lock_or_raise("stop")
        try:
            await self._stop_unlocked()
        finally:
            self._lock.release()

    async def restart(self) -> None:
        await self._acquire_lock_or_raise("restart")
        try:
            await self._stop_unlocked()
            await self._start_unlocked()
        finally:
            self._lock.release()

    async def reload(self) -> None:
        await self._acquire_lock_or_raise("reload")
        try:
            if self.is_running:
                await self._stop_unlocked()
            await self._start_unlocked()
            logger.info("xray reloaded (restart with new config)")
        finally:
            self._lock.release()

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
            # Use the same helper as `api/system._apply_nftables`: a rule's
            # match_value may hold several comma-separated MACs, and a raw
            # list-comp passed "aa:..,bb:.." through as one token, which
            # then failed _validate_mac and was silently dropped — so
            # multi-MAC bypasses evaporated on every crash/boot restore.
            from app.api.system import _collect_bypass_macs, _safe_int
            bypass_macs = list(_collect_bypass_macs(rules))

            # Restore device routing policies (include/exclude)
            from app.core.device_scanner import get_device_macs_for_mode
            device_info = await get_device_macs_for_mode(session)
            device_mode = device_info["mode"]
            if device_mode == "exclude_list":
                bypass_macs.extend(device_info["exclude_macs"])

            # Collect bypass destinations: explicit "dst_ip + direct" rules
            # AND auto-bypass for naive sidecar upstreams. Without the
            # latter, the host-netns naive sidecar's TCP socket to its
            # remote Caddy gets caught by the tproxy mangle hook and loops
            # back through xray → sidecar → forever (the sidecar can't
            # set SO_MARK=255 the way xray's own outbounds do). This used
            # to live only in `api/system._apply_nftables` — _that_ path
            # is hit on `POST /system/start`, but the auto-restart-on-
            # crash path here (and the boot-time auto-start in main.py)
            # rebuilt the table without these entries, so a backend
            # restart silently broke naive (the sidecar started fine,
            # but every CONNECT got RST'd → speedtest "too small (0B)",
            # actual traffic also failed). Mirror the helper here.
            from app.api.system import _collect_bypass_dsts, _collect_naive_bypass_dsts
            bypass_dsts = list(_collect_bypass_dsts(rules))
            naive_dsts = await _collect_naive_bypass_dsts(session)
            if naive_dsts:
                bypass_dsts.extend(naive_dsts)

            # v1.4 RoutingSet support — collect per-set specs so nft
            # restores the MAC sets + per-set TPROXY redirects after a
            # crash/reboot. Without this, only the *xray* config
            # regenerates (which creates per-set inbounds), while nft
            # rebuilds the table with NO per-set rules → packets from
            # set members silently fall through to the default tproxy
            # → set rules never apply. Same bug class as the v1.4-dev
            # auto_reload_xray-only issue, but on the watchdog path.
            from app.core.config_gen import collect_routing_set_context
            from app.core.nftables import RoutingSetSpec
            routing_sets, device_set_macs = await collect_routing_set_context(session)
            routing_set_specs = []
            for rs in routing_sets:
                macs = device_set_macs.get(rs.id) or []
                if not macs:
                    continue
                routing_set_specs.append(RoutingSetSpec(
                    set_id=rs.id, macs=tuple(macs), tproxy_port=rs.tproxy_port,
                ))

            mode = settings_map.get("mode", "rules")
            if mode == "bypass":
                await nftables_manager.flush()
            else:
                await nftables_manager.apply_rules(
                    inbound_mode=settings_map.get("inbound_mode", "tproxy"),
                    bypass_macs=bypass_macs,
                    bypass_dst_cidrs=bypass_dsts,
                    include_macs=device_info["include_macs"] if device_mode == "include_only" else None,
                    device_routing_mode=device_mode,
                    # `_safe_int`, not raw int(): same hardening as the
                    # `/system/start` path, so a corrupted Settings value
                    # degrades to the default here too instead of only there.
                    tproxy_tcp=_safe_int(settings_map, "tproxy_port_tcp", 7893),
                    tproxy_udp=_safe_int(settings_map, "tproxy_port_udp", 7894),
                    dns_port=_safe_int(settings_map, "dns_port", 5353),
                    block_quic=settings_map.get("block_quic", "true").lower() == "true",
                    kill_switch=settings_map.get("kill_switch", "false").lower() == "true",
                    routing_set_specs=routing_set_specs,
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

    # Fan out to every live viewer. Never await a subscriber: one stalled
    # WebSocket must not back-pressure the log pump. A full queue drops
    # its own oldest line instead.
    for q in list(_log_subscribers):
        if q.full():
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            q.put_nowait(line)
        except asyncio.QueueFull:
            pass


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
