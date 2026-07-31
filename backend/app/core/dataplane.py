"""Deferring the xray restart until after the HTTP response is sent.

`XrayManager.reload()` is a full process restart — every TCP connection
xray carries dies with it. That includes the operator's own browser
session whenever the UI is reached *through* PiTun (LAN proxy inbound, or
simply a device whose gateway is the box). Restarting inside the request
that triggered it therefore kills that request's own response: the change
lands in the DB and in the dataplane, but the browser reports
`ERR_EMPTY_RESPONSE` / "Network Error" and nginx logs a 499. Observed on
`POST /system/active-node`, and reproducible on every endpoint that
reloads.

The fix is ordering, not locking: validate and write the config while the
request is still ours (so a bad config still fails loudly with a 4xx/5xx),
then hand the process restart to a Starlette BackgroundTask, which runs
*after* the response has been flushed. The client gets its answer, and the
tunnel blips a few milliseconds later — as it must, since a restart is a
restart.

Notes for callers:

* Background tasks run AFTER FastAPI has torn down `yield` dependencies,
  so a deferred step must never touch the request's session. Everything
  here opens its own.
* Failures can no longer surface in the response, so they are logged and
  written to the events feed instead.
* `background_tasks=None` keeps the old inline behaviour — that's what
  schedulers, the watchdog and the failover path use, since they have no
  response to protect.
"""
import logging
from typing import Optional

from fastapi import BackgroundTasks
from sqlmodel.ext.asyncio.session import AsyncSession

from app.database import get_async_engine

logger = logging.getLogger(__name__)

# What to do to the xray process. The nftables layer is re-applied after
# it in every mode except `stop`, which tears it down instead.
Action = str  # "reload" | "start" | "restart" | "stop"


async def _record_failure(action: str, exc: Exception) -> None:
    from app.core.events import record_event

    await record_event(
        category="dataplane.apply_failed",
        severity="error",
        title=f"Dataplane {action} failed",
        details=(
            f"{type(exc).__name__}: {str(exc)[:400]}. The change was saved; "
            f"use Reload config or restart the proxy to apply it."
        ),
        dedup_window_sec=30,
    )


async def apply_dataplane(action: Action = "reload") -> None:
    """Apply the already-written config to the running dataplane.

    Order is load-bearing and matches `routing_sets._auto_reload_dataplane`:
    xray first so its inbounds are live, nftables second so the redirects
    point at a listening port.
    """
    from app.api.system import (
        _apply_nftables,
        _collect_vpn_server_ips,
        _load_settings_map,
    )
    from app.core.nftables import nftables_manager
    from app.core.xray import xray_manager

    async with AsyncSession(get_async_engine()) as session:
        settings_map = await _load_settings_map(session)

        if action == "stop":
            kill_switch = settings_map.get("kill_switch", "false").lower() == "true"
            if kill_switch:
                vpn_ips = await _collect_vpn_server_ips(session)
                await nftables_manager.apply_kill_switch(vpn_server_ips=vpn_ips)
            await xray_manager.stop()
            if not kill_switch:
                await nftables_manager.flush()
            return

        if action == "start":
            if xray_manager.is_running:
                await xray_manager.reload()
            else:
                await xray_manager.start()
        elif action == "restart":
            await xray_manager.restart()
        else:
            # A reload with nothing running is a no-op: `/system/start`
            # owns bringing it up, and re-applying nftables here would
            # point redirects at a port with no listener.
            if not xray_manager.is_running:
                return
            await xray_manager.reload()

        await _apply_nftables(session, settings_map)


async def _guarded(action: Action) -> None:
    try:
        await apply_dataplane(action)
    except Exception as exc:  # noqa: BLE001
        # Nobody is listening any more — the response went out first.
        logger.error("Deferred dataplane %s failed: %s", action, exc)
        try:
            await _record_failure(action, exc)
        except Exception:  # noqa: BLE001
            pass


async def dispatch_dataplane(
    background_tasks: Optional[BackgroundTasks], action: Action = "reload",
) -> None:
    """Apply now, or after the response when `background_tasks` is given."""
    if background_tasks is None:
        await apply_dataplane(action)
        return
    background_tasks.add_task(_guarded, action)
