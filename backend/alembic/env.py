"""Alembic environment configuration for PiTun."""
import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.models import (  # noqa: F401 — import all models so SQLModel.metadata is populated
    Node, RoutingRule, Subscription, DNSRule, DNSQueryLog,
    Settings, BalancerGroup, NodeCircle, User, Device,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata

# Prefer DATABASE_URL from the environment over the fallback in alembic.ini.
# This keeps migrations against the same DB the app writes to (especially
# important in Docker where compose sets DATABASE_URL=sqlite:////app/data/...
# while the ini's relative `./data/pitun.db` would resolve against whatever
# CWD the migration is invoked from).
#
# Only file-backed SQLite URLs get applied here: the test suite sets
# `DATABASE_URL=sqlite://` (in-memory) as a sentinel, and propagating that
# to alembic would migrate a throwaway connection — by the time pytest's
# actual engine opens the test DB file, the schema would be gone.
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("sqlite:///"):
    config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
