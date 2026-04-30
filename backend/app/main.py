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

    health_checker.start()
    subscription_scheduler.start()
    circle_scheduler.start()
    device_scanner.start()
    metrics_collector.start()
    geo_scheduler.start()

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

    # Apply system-level toggles (IPv6, DNS over TCP) from DB — /proc/sys resets on reboot
    from app.api.system import apply_system_toggles_on_boot
    await apply_system_toggles_on_boot()

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
    await xray_manager.stop()


app = FastAPI(
    title="PiTun API",
    version=APP_VERSION,
    description="Transparent proxy manager for Raspberry Pi",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
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
from app.api import nodes, routing, subscriptions, system, geodata, logs, dns, balancers, auth, nodecircle, devices, diagnostics, events
from app.core.auth import get_current_user

app.include_router(auth.router, prefix="/api")
app.include_router(logs.router, prefix="/api")

_auth = [Depends(get_current_user)]
app.include_router(nodes.router, prefix="/api", dependencies=_auth)
app.include_router(routing.router, prefix="/api", dependencies=_auth)
app.include_router(subscriptions.router, prefix="/api", dependencies=_auth)
app.include_router(system.router, prefix="/api", dependencies=_auth)
app.include_router(geodata.router, prefix="/api", dependencies=_auth)
app.include_router(dns.router, dependencies=_auth)
app.include_router(balancers.router, prefix="/api", dependencies=_auth)
app.include_router(nodecircle.router, prefix="/api", dependencies=_auth)
app.include_router(devices.router, prefix="/api", dependencies=_auth)
app.include_router(diagnostics.router, prefix="/api", dependencies=_auth)
app.include_router(events.router, prefix="/api", dependencies=_auth)


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
