import logging
import os
from typing import AsyncGenerator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncEngine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import settings

logger = logging.getLogger(__name__)

_async_engine: AsyncEngine | None = None
_sync_engine = None


def _to_async_url(url: str) -> str:
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    return url


def _ensure_db_dir(url: str) -> None:
    if url.startswith("sqlite") and "///" in url:
        db_path = url.split("///", 1)[1]
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)


def get_async_engine() -> AsyncEngine:
    global _async_engine
    if _async_engine is None:
        _ensure_db_dir(settings.database_url)
        async_url = _to_async_url(settings.database_url)
        _async_engine = create_async_engine(async_url, echo=False)

        @event.listens_for(_async_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return _async_engine


def get_sync_engine():
    """Sync engine — used only for Alembic migrations."""
    global _sync_engine
    if _sync_engine is None:
        _ensure_db_dir(settings.database_url)
        connect_args: dict = {}
        kwargs: dict = {}
        if settings.database_url.startswith("sqlite"):
            connect_args["check_same_thread"] = False
            kwargs["poolclass"] = StaticPool

        _sync_engine = create_engine(
            settings.database_url, connect_args=connect_args, **kwargs
        )
    return _sync_engine


def run_migrations():
    """Run Alembic migrations programmatically. Falls back to create_all on error."""
    try:
        from alembic.config import Config
        from alembic import command

        alembic_cfg = Config()
        alembic_cfg.set_main_option(
            "script_location",
            os.path.join(os.path.dirname(__file__), "..", "alembic"),
        )
        alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)

        db_url = settings.database_url
        if db_url.startswith("sqlite"):
            if "///" in db_url:
                db_path = db_url.split("///", 1)[1]
                db_exists = os.path.exists(db_path) and os.path.getsize(db_path) > 0
            else:
                db_exists = False
        else:
            db_exists = True

        if db_exists:
            try:
                from alembic.migration import MigrationContext

                engine = get_sync_engine()
                with engine.connect() as conn:
                    ctx = MigrationContext.configure(conn)
                    current_rev = ctx.get_current_revision()
                if current_rev is None:
                    logger.info("Existing DB without Alembic history — stamping as 001")
                    command.stamp(alembic_cfg, "001")
            except Exception as exc:
                logger.warning("Could not check migration state: %s", exc)

        command.upgrade(alembic_cfg, "head")
        logger.info("Alembic migrations applied successfully")
    except Exception as exc:
        logger.warning("Alembic migration failed (%s), falling back to create_all", exc)
        SQLModel.metadata.create_all(get_sync_engine())


def create_db_and_tables():
    run_migrations()


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSession(get_async_engine()) as session:
        yield session


async def init_default_settings():
    """Insert default settings into DB if they don't exist."""
    from app.models import Settings as DBSettings
    from sqlmodel import select

    defaults = {
        "mode": "rules",
        "active_node_id": "",
        "failover_enabled": "false",
        # GeoScheduler — daily auto-update of geoip.dat / geosite.dat /
        # mmdb. Reloads xray after a successful update. User can disable
        # in Settings or stretch the interval. The window keys constrain
        # the run to off-peak hours (in the user's chosen `timezone`,
        # not the container's TZ env var) so the ~50 MB download doesn't
        # compete with daytime user traffic.
        "geo_auto_update": "true",
        "geo_update_interval_days": "1",
        "geo_update_window_start": "4",
        "geo_update_window_end": "6",

        # Display + scheduling timezone. Used by GeoScheduler's window
        # check (and any future "do X at HH:MM local" feature). Stored
        # as an IANA name (e.g. "Europe/Moscow", "America/New_York").
        # Default UTC because the container has no inherent locale.
        "timezone": "UTC",

        # Health full-check loop — every N seconds, probe ALL enabled
        # nodes and refresh their is_online flag. Independent of the
        # fast active-node loop (which drives failover).
        "health_full_check_interval": "300",
        "failover_node_ids": "[]",
        "tproxy_port_tcp": str(settings.tproxy_port_tcp),
        "tproxy_port_udp": str(settings.tproxy_port_udp),
        "socks_port": str(settings.socks_port),
        "http_port": str(settings.http_port),
        "dns_port": str(settings.dns_port),
        "dns_mode": "plain",
        "dns_upstream": "8.8.8.8",
        "dns_upstream_secondary": "",
        "dns_fallback": "8.8.8.8",
        "fakedns_enabled": "false",
        "fakedns_pool": "198.18.0.0/15",
        "fakedns_pool_size": "65535",
        "dns_sniffing": "true",
        "bypass_cn_dns": "false",
        "bypass_ru_dns": "false",
        "bypass_private": "true",
        "log_level": settings.xray_log_level,
        "geoip_url": settings.geoip_url,
        "geosite_url": settings.geosite_url,
        "geoip_mmdb_url": settings.geoip_mmdb_url,
        "inbound_mode": "tproxy",
        "tun_address": "10.0.0.1/30",
        "tun_address6": "fd59:7153:2388::1/126",
        "tun_mtu": "9000",
        "tun_stack": "system",
        "tun_auto_route": "true",
        "tun_strict_route": "true",
        "tun_endpoint_nat": "true",
        "tun_sniff": "true",
        "block_quic": "true",
        "kill_switch": "false",
        "auto_restart_xray": "true",
        "dns_query_log_enabled": "false",
        "dns_query_log_max": "10000",
        "device_routing_mode": "all",
        "device_scan_interval": "60",
        # Network (override from UI — fallback to env)
        "interface": settings.interface,
        "gateway_ip": settings.gateway_ip,
        "lan_cidr": settings.lan_cidr,
        "router_ip": "",
        # Health check
        "health_interval": str(settings.health_interval),
        "health_timeout": str(settings.health_timeout),
        "disable_ipv6": "false",
        "dns_over_tcp": "false",
    }

    # Race-safe upsert: INSERT OR IGNORE on (key). Avoids the TOCTOU gap
    # where two startup paths (entrypoint migrations + lifespan init) could
    # both observe `existing is None` and then both try to INSERT, violating
    # the UNIQUE constraint on `Settings.key`. Dialect-specific SQL is used
    # for SQLite (our only target); the pattern falls back to select-then-
    # insert for other dialects.
    from sqlalchemy import text

    async with AsyncSession(get_async_engine()) as session:
        dialect_name = session.bind.dialect.name if session.bind else ""
        if dialect_name == "sqlite":
            for key, value in defaults.items():
                await session.exec(
                    text("INSERT OR IGNORE INTO settings (key, value) VALUES (:k, :v)")
                    .bindparams(k=key, v=value)
                )
            await session.commit()
        else:
            for key, value in defaults.items():
                stmt = select(DBSettings).where(DBSettings.key == key)
                existing = (await session.exec(stmt)).first()
                if existing is None:
                    session.add(DBSettings(key=key, value=value))
            await session.commit()

    from app.models import User

    async with AsyncSession(get_async_engine()) as session:
        existing_user = (await session.exec(select(User))).first()
        if existing_user is None:
            from app.core.auth import hash_password

            admin = User(username="admin", password_hash=hash_password("password"))
            session.add(admin)
            await session.commit()
