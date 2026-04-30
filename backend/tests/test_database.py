"""Tests for database module: engines, migrations fallback, init_default_settings."""
import os
import pytest
from unittest.mock import patch, MagicMock

from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy import Engine


class TestEngines:
    def test_async_url_conversion(self):
        from app.database import _to_async_url
        assert _to_async_url("sqlite:///data/db.sqlite") == "sqlite+aiosqlite:///data/db.sqlite"
        assert _to_async_url("postgresql://host/db") == "postgresql://host/db"

    def test_get_async_engine_returns_async_engine(self, tmp_path):
        import app.database as db_mod

        old_engine = db_mod._async_engine
        db_mod._async_engine = None
        try:
            with patch.object(db_mod.settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}"):
                engine = db_mod.get_async_engine()
                assert isinstance(engine, AsyncEngine)
                engine2 = db_mod.get_async_engine()
                assert engine is engine2
        finally:
            db_mod._async_engine = old_engine

    def test_get_sync_engine_returns_engine(self, tmp_path):
        import app.database as db_mod

        old_engine = db_mod._sync_engine
        db_mod._sync_engine = None
        try:
            with patch.object(db_mod.settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}"):
                engine = db_mod.get_sync_engine()
                assert isinstance(engine, Engine)
        finally:
            db_mod._sync_engine = old_engine


class TestMigrationsFallback:
    def test_run_migrations_fallback_on_missing_alembic(self, tmp_path):
        """When alembic dir doesn't exist, falls back to create_all."""
        import app.database as db_mod
        from sqlmodel import SQLModel

        db_url = f"sqlite:///{tmp_path / 'fallback.db'}"
        old_sync = db_mod._sync_engine
        db_mod._sync_engine = None
        try:
            with patch.object(db_mod.settings, "database_url", db_url):
                db_mod.run_migrations()
                engine = db_mod.get_sync_engine()
                from sqlalchemy import inspect
                inspector = inspect(engine)
                tables = inspector.get_table_names()
                assert "settings" in tables
        finally:
            db_mod._sync_engine = old_sync


class TestInitDefaultSettings:
    @pytest.mark.asyncio
    async def test_init_default_settings_creates_records(self, tmp_path):
        import app.database as db_mod
        from sqlmodel import SQLModel, select
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlmodel.ext.asyncio.session import AsyncSession

        db_path = tmp_path / "init_test.db"
        db_url = f"sqlite:///{db_path}"
        async_url = f"sqlite+aiosqlite:///{db_path}"

        old_async = db_mod._async_engine
        old_sync = db_mod._sync_engine
        db_mod._async_engine = None
        db_mod._sync_engine = None
        try:
            with patch.object(db_mod.settings, "database_url", db_url):
                db_mod.run_migrations()

                test_engine = create_async_engine(async_url, echo=False)
                db_mod._async_engine = test_engine

                await db_mod.init_default_settings()

                async with AsyncSession(test_engine) as session:
                    from app.models import Settings as DBSettings
                    rows = (await session.exec(select(DBSettings))).all()
                    keys = {r.key for r in rows}
                    assert "mode" in keys
                    assert "dns_port" in keys
                    assert "device_routing_mode" in keys

                await db_mod.init_default_settings()
                async with AsyncSession(test_engine) as session:
                    rows2 = (await session.exec(select(DBSettings))).all()
                    assert len(rows2) == len(rows)
        finally:
            db_mod._async_engine = old_async
            db_mod._sync_engine = old_sync
