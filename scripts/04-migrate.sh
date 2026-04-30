#!/bin/bash
# ============================================================================
# PiTun — Step 4: Database migration
# ============================================================================
# Applies Alembic migrations or resets the database from scratch.
#
# Usage:
#   bash 04-migrate.sh           # Apply pending migrations
#   bash 04-migrate.sh --fresh   # Delete DB and recreate from scratch
#   bash 04-migrate.sh --status  # Show current migration version and tables
# ============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[x]${NC} $*"; exit 1; }

PITUN_DIR="${HOME}/pitun"
cd "$PITUN_DIR" || err "PiTun directory not found at $PITUN_DIR"

CONTAINER="pitun-backend"
DB_PATH="/app/data/pitun.db"
ACTION="${1:-migrate}"

# ── Check container is running ──
if ! docker ps --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
    err "Container $CONTAINER is not running. Start with: cd ~/pitun && docker compose up -d"
fi

py_exec() {
    docker exec "$CONTAINER" python3 -c "$1"
}

show_status() {
    log "Database status:"
    echo ""

    # Alembic version
    VERSION=$(py_exec "
import sqlite3, os
db='$DB_PATH'
if not os.path.exists(db):
    print('  DB does not exist')
else:
    c = sqlite3.connect(db)
    try:
        rows = c.execute('SELECT version_num FROM alembic_version').fetchall()
        print('  Alembic version: ' + (rows[0][0] if rows else 'none'))
    except:
        print('  Alembic version: no alembic_version table')
    tables = sorted([t[0] for t in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()])
    print('  Tables (' + str(len(tables)) + '): ' + ', '.join(tables))
    c.close()
" 2>&1)
    echo "$VERSION"
    echo ""
}

case "$ACTION" in
    --status|-s)
        show_status
        ;;

    --fresh|-f)
        warn "This will DELETE the database and recreate it from scratch!"
        warn "All nodes, rules, settings, and users will be lost."
        echo ""
        # 30 s timeout so the script can't silently hang in CI/non-interactive
        # contexts. `read -t` returns non-zero on timeout, which triggers
        # `set -e` — we handle that with `|| CONFIRM=""` so the abort message
        # fires instead of a cryptic "exit 142".
        read -t 30 -rp "Type 'yes' to confirm (auto-abort in 30s): " CONFIRM || CONFIRM=""
        if [ "$CONFIRM" != "yes" ]; then
            echo "Aborted."
            exit 0
        fi

        log "Stopping backend..."
        docker compose stop backend

        log "Removing database..."
        rm -f "$PITUN_DIR/data/pitun.db" "$PITUN_DIR/data/pitun.db-wal" "$PITUN_DIR/data/pitun.db-shm"

        log "Starting backend (will run all migrations)..."
        docker compose start backend

        # Wait for backend to be ready
        for i in $(seq 1 30); do
            if curl -s http://localhost/health > /dev/null 2>&1; then
                break
            fi
            sleep 2
            [ $((i % 5)) -eq 0 ] && echo "  Waiting... ($i/30)"
        done

        echo ""
        show_status
        HEALTH=$(curl -s http://localhost/health 2>/dev/null || echo '{"status":"error"}')
        log "Health: $HEALTH"
        log "Database recreated. Default login: admin / password"
        ;;

    migrate|--migrate|-m|"")
        log "Applying pending migrations..."
        docker exec "$CONTAINER" python3 -c "
from app.database import run_migrations
run_migrations()
"
        echo ""
        show_status
        log "Migrations applied."
        ;;

    *)
        echo "Usage: bash 04-migrate.sh [--status|--fresh|--migrate]"
        echo ""
        echo "  (no args)   Apply pending Alembic migrations"
        echo "  --status    Show current DB version and tables"
        echo "  --fresh     Delete DB and recreate from scratch"
        exit 1
        ;;
esac
