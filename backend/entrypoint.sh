#!/bin/sh
# Entrypoint for pitun-backend.
# Runs alembic migrations on every start, then execs the real command.
# This means a fresh code deploy + `docker compose up -d` automatically picks
# up new migrations — no more manual 04-migrate.sh step.
set -eu

cd /app

# alembic.ini lives at /app/alembic.ini; the `alembic/` dir may be bind-
# mounted from the host so the migration files are always the latest. If
# the directory is missing (e.g. stale image, unmounted), skip gracefully
# rather than crash the container.
# Migration strictness:
#   MIGRATION_STRICT=1 (default) → fail container on migration error.
#   MIGRATION_STRICT=0           → log warning and continue (old behavior).
# Default is strict: a schema mismatch at boot can silently corrupt data
# or crash routes at the first write, which is far worse than a crash-loop
# that surfaces the problem immediately.
: "${MIGRATION_STRICT:=1}"

if [ -f /app/alembic.ini ] && [ -d /app/alembic/versions ]; then
    echo "[entrypoint] running alembic upgrade head..."
    if ! alembic upgrade head; then
        if [ "$MIGRATION_STRICT" = "1" ]; then
            echo "[entrypoint] FATAL: alembic upgrade failed — refusing to start (set MIGRATION_STRICT=0 to override)" >&2
            exit 1
        else
            echo "[entrypoint] WARNING: alembic upgrade failed — starting anyway (MIGRATION_STRICT=0)"
        fi
    fi
else
    echo "[entrypoint] alembic.ini or alembic/versions missing — skipping migrations"
fi

exec "$@"
