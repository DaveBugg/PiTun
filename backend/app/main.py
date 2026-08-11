"""FastAPI application entry point."""
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings, APP_VERSION
from app.database import create_db_and_tables, init_default_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
)


# Scrub CR/LF from every log message to defend against log-injection
# (CWE-117). Many places legitimately format user-supplied strings into
# log lines — node.name, subscription URIs, exception messages, etc.
# An attacker who controls one of those values could inject a fake
# log entry by inserting `\n[FAKE]`, fooling SIEM / grep-based audits.
# Replacing line terminators with the unicode "return symbol" keeps
# the original content visible (so debugging still works) without
# letting it span lines. Cheaper than patching every `logger.info(…)`
# call site, and catches future ones automatically.
class _NoNewlineFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if "\r" in msg or "\n" in msg:
            record.msg = msg.replace("\r", "␍").replace("\n", "␊")
            record.args = ()  # already-formatted into record.msg
        return True


logging.getLogger().addFilter(_NoNewlineFilter())
logger = logging.getLogger(__name__)

# Attach in-memory ring buffer so diagnostics can read recent logs
from app.core.log_buffer import install as _install_log_buffer
_install_log_buffer()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    import asyncio

    logger.info("PiTun backend starting up")
    await asyncio.to_thread(create_db_and_tables)
    await init_default_settings()

    from app.core.xray import xray_manager
    from app.core.healthcheck import health_checker
    from app.core.sub_scheduler import subscription_scheduler
    from app.core.circle_scheduler import circle_scheduler
    from app.core.device_scanner import device_scanner
    from app.core.metrics_collector import metrics_collector

    xray_manager._version = await xray_manager.get_version()
    logger.info("xray version: %s", xray_manager.version or "not found")

    from app.core.geo_scheduler import geo_scheduler

    from app.core.autocheck_scheduler import autocheck_scheduler

    health_checker.start()
    subscription_scheduler.start()
    circle_scheduler.start()
    device_scanner.start()
    metrics_collector.start()
    geo_scheduler.start()
    autocheck_scheduler.start()

    # Supervise naive sidecars: react to docker `die` events within ms,
    # rather than waiting for the 30 s HealthChecker tick.
    try:
        from app.core.naive_supervisor import naive_supervisor
        naive_supervisor.start()
    except Exception as exc:
        logger.warning("NaiveSupervisor failed to start: %s", exc)

    # Background DNS query log cleanup (replaces per-insert trim).
    try:
        from app.core.dns_logger import start_trim_task as _dns_start
        _dns_start()
    except Exception as exc:
        logger.warning("DNS log trim task failed to start: %s", exc)

    # Recent Events trim task — keeps the Event table bounded
    # (7 days OR 1000 rows). See app/core/events.py.
    try:
        from app.core.events import start_trim_task as _events_start
        _events_start()
    except Exception as exc:
        logger.warning("Events trim task failed to start: %s", exc)

    # Server-tasks (Job) subsystem — heals stale `running` rows from a
    # previous backend that died mid-deploy, then kicks off the hourly
    # trim loop. v1.3.0-beta.1.
    try:
        from app.core.jobs import job_manager
        await job_manager.start()
    except Exception as exc:
        logger.warning("JobManager failed to start: %s", exc)

    # Apply system-level toggles (IPv6, DNS over TCP) from DB — /proc/sys resets on reboot
    from app.api.system import apply_system_toggles_on_boot
    await apply_system_toggles_on_boot()

    # Parse geosite.dat / geoip.dat tag lists into the in-memory cache
    # used by routing rule pre-flight validation + the /api/geo/categories
    # autocomplete endpoint (both new in v1.2.7). Best-effort — a missing
    # or unreadable .dat just leaves the cache empty (rule validation
    # then fails open: don't reject what we can't validate).
    try:
        from app.core.geo import refresh_tag_cache
        await asyncio.to_thread(refresh_tag_cache)
    except Exception as exc:
        logger.warning("Geo tag cache initial load failed: %s", exc)

    # Reconcile NaiveProxy sidecar containers with DB state. Non-fatal if
    # docker-proxy is unreachable or the pitun-naive image is missing —
    # the backend still starts and individual naive nodes will show as
    # offline in the UI until the user investigates.
    try:
        from app.core.naive_manager import naive_manager
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.database import get_async_engine

        async with AsyncSession(get_async_engine()) as s:
            await naive_manager.sync_all(s)
    except Exception as exc:
        logger.warning("Naive sidecar sync on boot skipped: %s", exc)

    # Heal `active_node_id` if it points to a node that no longer exists or
    # is disabled — observed in the wild on a v1.2.2 install whose
    # subscription rotation deleted the previous active node, leaving the
    # setting pointing at id=64 with no row backing it. The next config
    # generation then produced a config without any proxy outbound (only
    # `direct`/`block`/`dns-out`), and traffic silently fell through to
    # direct routing. A clear log line + auto-pick of the first enabled
    # node lets the admin spot it on the next health check.
    try:
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.database import get_async_engine
        from app.models import Settings as DBSettings, Node

        async with AsyncSession(get_async_engine()) as session:
            # Disable expire-on-commit so attribute reads after
            # `session.commit()` (e.g. `enabled_nodes[0].name` on the
            # log line below) don't trigger a sync lazy-load and the
            # `MissingGreenlet: greenlet_spawn has not been called`
            # error. Same systemic fix we applied to the orchestrate_*
            # functions in 1.3.0-beta — kept the check working when
            # SQLAlchemy upgraded its expire semantics. Without this,
            # the integrity check was silently skipping every boot
            # (logged as "integrity check skipped: greenlet_spawn..."
            # — best-effort fallback).
            try:
                session.sync_session.expire_on_commit = False
            except Exception:  # noqa: BLE001
                pass

            row = (await session.exec(
                select(DBSettings).where(DBSettings.key == "active_node_id")
            )).first()
            stale = False
            if row and row.value:
                try:
                    target_id = int(row.value)
                except (TypeError, ValueError):
                    target_id = None
                    stale = True
                else:
                    node = await session.get(Node, target_id)
                    if node is None or not node.enabled:
                        stale = True

                if stale:
                    enabled_nodes = (await session.exec(
                        select(Node).where(Node.enabled == True).order_by(Node.id)
                    )).all()
                    # Snapshot fields we need AFTER commit before
                    # invoking the commit — even with expire_on_commit
                    # off, this is a belt-and-braces guarantee against
                    # future SQLAlchemy default-behaviour drift.
                    new_id = enabled_nodes[0].id if enabled_nodes else None
                    new_name = enabled_nodes[0].name if enabled_nodes else None
                    old_value = row.value
                    if new_id is not None:
                        row.value = str(new_id)
                        session.add(row)
                        await session.commit()
                        logger.warning(
                            "active_node_id healed on boot: %r -> %d (%r) "
                            "— previous value referenced a missing or disabled node",
                            old_value, new_id, new_name,
                        )
                    else:
                        # No enabled nodes at all — clear the setting so
                        # config_gen / auto-start skip cleanly. Admin will
                        # add a node and pick it manually.
                        row.value = ""
                        session.add(row)
                        await session.commit()
                        logger.warning(
                            "active_node_id cleared on boot: previous %r referenced "
                            "a missing/disabled node, and there are no other enabled "
                            "nodes to fall back to. Add a node and set it active.",
                            old_value,
                        )
    except Exception as exc:
        # Healing is best-effort — never block backend startup on it.
        logger.warning("active_node_id integrity check skipped: %s", exc)

    # Auto-start xray on container boot if auto_restart is enabled and nodes exist
    try:
        from sqlmodel import select
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.database import get_async_engine
        from app.models import Settings as DBSettings, Node

        async with AsyncSession(get_async_engine()) as session:
            settings_map = {r.key: r.value for r in (await session.exec(select(DBSettings))).all()}
            auto_start = settings_map.get("auto_restart_xray", "true").lower() == "true"
            has_nodes = len((await session.exec(select(Node).where(Node.enabled == True))).all()) > 0
            active_id = settings_map.get("active_node_id", "")

        if auto_start and has_nodes and active_id:
            logger.info("Auto-starting xray on boot...")
            from app.core.xray import _auto_restart_if_enabled
            # from_boot=True suppresses the `xray.auto_restarted` Event row —
            # bringing xray up on container start is normal, not a crash
            # recovery, so don't pollute the Recent Events feed with it.
            await _auto_restart_if_enabled(from_boot=True)
    except Exception as exc:
        logger.warning("Auto-start on boot failed: %s", exc)

    yield

    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info("PiTun backend shutting down")
    from app.core.geo_scheduler import geo_scheduler

    health_checker.stop()
    subscription_scheduler.stop()
    circle_scheduler.stop()
    device_scanner.stop()
    metrics_collector.stop()
    geo_scheduler.stop()
    autocheck_scheduler.stop()
    try:
        from app.core.naive_supervisor import naive_supervisor
        naive_supervisor.stop()
    except Exception:
        pass
    try:
        from app.core.dns_logger import stop_trim_task as _dns_stop
        _dns_stop()
    except Exception:
        pass
    try:
        from app.core.events import stop_trim_task as _events_stop
        _events_stop()
    except Exception:
        pass
    try:
        from app.core.jobs import job_manager
        await job_manager.stop()
    except Exception:
        pass
    await xray_manager.stop()


app = FastAPI(
    title="PiTun API",
    version=APP_VERSION,
    description="Transparent proxy manager for Raspberry Pi",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# ── Exception handlers ───────────────────────────────────────────────────────
# Lock-busy on the xray manager → 503 Service Unavailable.
# Without this, a stuck reload (e.g. broken /etc/resolv.conf wedging
# a sync getaddrinfo while holding `xray_manager._lock`) used to
# surface as a hung connection from the operator's perspective.
# Returning a clean 503 lets the UI show "service busy, retry"
# instead of a frozen spinner. The fix in 1.3.3+ also addresses the
# underlying hang (de-blocked sync DNS) — this is the second line
# of defense.
from app.core.xray import XrayManager  # noqa: E402 — needs `app` defined first


@app.exception_handler(XrayManager.LockBusyError)
async def _xray_lock_busy_handler(request, exc):  # noqa: ARG001
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=503,
        content={
            "detail": str(exc),
            "hint": (
                "Another xray operation is still running. "
                "Wait a few seconds and retry. If this persists, "
                "check the backend logs for the last xray-related "
                "error line."
            ),
        },
    )


# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
from app.api import nodes, routing, routing_sets, subscriptions, system, geodata, logs, dns, balancers, auth, nodecircle, devices, diagnostics, events, servers, scripts, server_tasks, server_clients, templates, user_agents, xui, network, autocheck, backup
from app.core.auth import get_current_user

app.include_router(auth.router, prefix="/api")
app.include_router(logs.router, prefix="/api")

_auth = [Depends(get_current_user)]
app.include_router(nodes.router, prefix="/api", dependencies=_auth)
app.include_router(routing.router, prefix="/api", dependencies=_auth)
app.include_router(routing_sets.router, prefix="/api", dependencies=_auth)
app.include_router(subscriptions.router, prefix="/api", dependencies=_auth)
# User-Agent templates (since v1.4.7) — the editable catalogue behind the
# subscription UA dropdown. Managed from the Subscriptions page.
app.include_router(user_agents.router, prefix="/api", dependencies=_auth)
app.include_router(system.router, prefix="/api", dependencies=_auth)
app.include_router(geodata.router, prefix="/api", dependencies=_auth)
app.include_router(dns.router, dependencies=_auth)
app.include_router(balancers.router, prefix="/api", dependencies=_auth)
app.include_router(nodecircle.router, prefix="/api", dependencies=_auth)
app.include_router(autocheck.router, prefix="/api", dependencies=_auth)
app.include_router(backup.router, prefix="/api", dependencies=_auth)
app.include_router(devices.router, prefix="/api", dependencies=_auth)
app.include_router(diagnostics.router, prefix="/api", dependencies=_auth)
app.include_router(events.router, prefix="/api", dependencies=_auth)
app.include_router(servers.router, prefix="/api", dependencies=_auth)
app.include_router(scripts.router, prefix="/api", dependencies=_auth)
app.include_router(templates.router, prefix="/api", dependencies=_auth)
# server_tasks: REST router is auth-gated globally; ws_router carries
# the WS endpoint and skips the Bearer dependency (browsers can't set
# Authorization headers on WebSockets), validating ?token=<jwt> itself
# — same pattern as logs.router.
app.include_router(server_tasks.router, prefix="/api", dependencies=_auth)
app.include_router(server_tasks.ws_router, prefix="/api")
# WireGuard server-side client management (since v1.3.0-beta.4) — auth-gated.
app.include_router(server_clients.router, prefix="/api", dependencies=_auth)
# x-ui-pro / 3x-ui panel management (since v1.3.0-beta.7) — auth-gated.
app.include_router(xui.router, prefix="/api", dependencies=_auth)
# Host network configuration UI (since v1.3.3) — auth-gated.
app.include_router(network.router, prefix="/api", dependencies=_auth)


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["meta"])
async def health():
    """Liveness/readiness probe.

    Returns 200 when the backend is responsive. Returns 503 when xray is
    expected to be running (has nodes + auto_restart enabled) but isn't —
    so Docker healthcheck + external monitoring can detect a silently-dead
    proxy instead of showing "healthy" on a broken system.

    If xray was never expected to run (no active node, or auto_restart off),
    we report 200 with `xray_running: false` — that's a valid operational
    state, not an error.
    """
    from fastapi.responses import JSONResponse
    from app.core.xray import xray_manager
    from sqlmodel import select
    from sqlmodel.ext.asyncio.session import AsyncSession
    from app.database import get_async_engine
    from app.models import Settings as DBSettings

    xray_running = xray_manager.is_running
    xray_expected = False
    try:
        async with AsyncSession(get_async_engine()) as session:
            rows = (await session.exec(select(DBSettings))).all()
            settings_map = {r.key: r.value for r in rows}
            auto_restart = settings_map.get("auto_restart_xray", "true").lower() == "true"
            active_id = settings_map.get("active_node_id", "").strip()
            xray_expected = auto_restart and bool(active_id)
    except Exception:
        # DB down or not yet initialized — degrade gracefully (200 with
        # unknown expectation rather than 503 that would block the
        # container from ever reaching "healthy" on first boot).
        xray_expected = False

    body = {
        "status": "ok" if (xray_running or not xray_expected) else "degraded",
        "xray_running": xray_running,
        "xray_expected": xray_expected,
        "version": APP_VERSION,
    }
    status_code = 200 if (xray_running or not xray_expected) else 503
    return JSONResponse(status_code=status_code, content=body)
