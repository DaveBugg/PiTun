#!/bin/bash
# ============================================================================
# PiTun — Offline deploy to an airgapped RPi
# ============================================================================
# Ships the prebuilt image bundle (docker/offline/*.tar.gz) + source tree to
# a target host, loads the images into its Docker daemon, retags them for
# docker-compose, runs DB migrations and brings the stack up.
#
# Prerequisites on the builder machine:
#   - scripts/build-offline-bundle.sh has been run successfully
#   - SSH key auth works to the target
#
# Prerequisites on the target RPi:
#   - Docker + Docker Compose installed (scripts/02-install-stack.sh)
#   - xray binary + geo data installed (scripts/02-install-stack.sh)
#   - User in docker group, or passwordless sudo
#
# Usage:
#   bash scripts/deploy-offline.sh user@pitun.local
#   bash scripts/deploy-offline.sh user@pitun.local ~/.ssh/id_ed25519
#   ARCH=amd64 bash scripts/deploy-offline.sh user@host
# ============================================================================

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
ARCH="${ARCH:-arm64}"
OFFLINE_DIR="${ROOT}/docker/offline"

TARGET="${1:-}"
SSH_KEY="${2:-${SSH_KEY:-}}"
REMOTE_DIR="${REMOTE_DIR:-pitun}"

G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
log()  { echo -e "${G}[+]${N} $*"; }
warn() { echo -e "${Y}[!]${N} $*"; }
err()  { echo -e "${R}[x]${N} $*" >&2; exit 1; }

[ -n "$TARGET" ] || err "Usage: $0 user@host [ssh_key]"

SSH_OPTS=()
SCP_OPTS=()
if [ -n "$SSH_KEY" ]; then
    SSH_OPTS+=("-i" "$SSH_KEY")
    SCP_OPTS+=("-i" "$SSH_KEY")
fi

ssh_run()  { ssh "${SSH_OPTS[@]}" "$TARGET" "$@"; }
scp_to()   { scp "${SCP_OPTS[@]}" "$@" "$TARGET:"; }

# ── 0. Verify bundle present ───────────────────────────────────────────────
TARBALLS=(
    "pitun-backend-${ARCH}.tar.gz"
    "pitun-frontend-${ARCH}.tar.gz"
    "pitun-naive-${ARCH}.tar.gz"
    "nginx-${ARCH}.tar.gz"
    "docker-socket-proxy-${ARCH}.tar.gz"
)
for t in "${TARBALLS[@]}"; do
    [ -f "${OFFLINE_DIR}/${t}" ] || err "Missing ${OFFLINE_DIR}/${t} — run scripts/build-offline-bundle.sh first"
done

log "Target: ${TARGET}  arch=${ARCH}"
log "Testing SSH connectivity..."
ssh_run "echo ok" >/dev/null || err "SSH to ${TARGET} failed"

# ── 1. Ensure remote dir exists ────────────────────────────────────────────
log "Preparing remote directory ~/${REMOTE_DIR}..."
ssh_run "mkdir -p ~/${REMOTE_DIR}/docker/offline ~/${REMOTE_DIR}/data"

# ── 2. Ship source tree (compose, backend code, migrations, frontend dist, naive Dockerfile, scripts) ─
log "Syncing source tree..."
SRC_PATHS=(docker-compose.yml backend docker/naive scripts)
if [ -d frontend/dist ]; then
    SRC_PATHS+=(frontend/dist)
else
    warn "frontend/dist missing — run 'npm run build' first if you want UI served"
fi

TMP_TAR=$(mktemp --suffix=.tar.gz)
tar --exclude='**/__pycache__' \
    --exclude='**/node_modules' \
    --exclude='**/.pytest_cache' \
    --exclude='**/*.pyc' \
    --exclude='docker/offline/*.tar.gz' \
    -czf "$TMP_TAR" \
    "${SRC_PATHS[@]}"

scp_to "$TMP_TAR"
REMOTE_TAR=$(basename "$TMP_TAR")
ssh_run "cd ~/${REMOTE_DIR} && tar -xzf ~/${REMOTE_TAR} && rm -f ~/${REMOTE_TAR}"
rm -f "$TMP_TAR"

# ── 3. Ship image tarballs ─────────────────────────────────────────────────
log "Uploading image tarballs (~150 MB)..."
for t in "${TARBALLS[@]}"; do
    log "  → ${t}"
    scp "${SCP_OPTS[@]}" "${OFFLINE_DIR}/${t}" "$TARGET:${REMOTE_DIR}/docker/offline/"
done

# ── 4. Load and tag images ────────────────────────────────────────────────
log "Loading images into remote Docker daemon..."
ssh_run "bash -s" <<REMOTE_EOF
set -euo pipefail
cd ~/${REMOTE_DIR}

DOCKER=docker
if ! docker info >/dev/null 2>&1; then
    if sudo docker info >/dev/null 2>&1; then DOCKER="sudo docker"; else echo "docker not usable" >&2; exit 1; fi
fi

for t in docker/offline/*.tar.gz; do
    echo "  loading \$t"
    gunzip -c "\$t" | \$DOCKER load
done

# Retag arch-specific images to the names docker-compose expects
\$DOCKER tag pitun-backend:latest-${ARCH}         pitun-backend:latest   || true
\$DOCKER tag pitun-frontend:latest-${ARCH}        pitun-frontend:latest  || true
\$DOCKER tag pitun-naive:latest-${ARCH}           pitun-naive:latest     || true
\$DOCKER tag nginx-${ARCH}:1.25-alpine            nginx:1.25-alpine      || true
\$DOCKER tag docker-socket-proxy-${ARCH}:0.3      tecnativa/docker-socket-proxy:0.3 || true

echo "Loaded images:"
\$DOCKER images | grep -E 'pitun-|nginx|docker-socket-proxy' || true
REMOTE_EOF

# ── 5. Create .env if missing, run migrations, bring stack up ─────────────
log "Finalising deployment on remote..."
ssh_run "bash -s" <<REMOTE_EOF
set -euo pipefail
cd ~/${REMOTE_DIR}

DOCKER=docker
if ! docker info >/dev/null 2>&1; then DOCKER="sudo docker"; fi

# Minimal .env if one isn't there yet
if [ ! -f .env ]; then
    echo "[+] Generating .env"
    SECRET_KEY=\$(openssl rand -hex 32 2>/dev/null || echo changeme-\$(date +%s))
    VM_IP=\$(hostname -I | awk '{print \$1}')
    cat > .env <<ENV
SECRET_KEY=\${SECRET_KEY}
BACKEND_PORT=8000
DATABASE_URL=sqlite:///./data/pitun.db
XRAY_BINARY=/usr/local/bin/xray
XRAY_CONFIG_PATH=/tmp/pitun/config.json
XRAY_GEOIP_PATH=/usr/local/share/xray/geoip.dat
XRAY_GEOSITE_PATH=/usr/local/share/xray/geosite.dat
XRAY_LOG_LEVEL=warning
TPROXY_PORT_TCP=7893
TPROXY_PORT_UDP=7894
DNS_PORT=5353
INTERFACE=eth0
LAN_CIDR=192.168.1.0/24
GATEWAY_IP=\${VM_IP}
VITE_API_BASE_URL=/api
VITE_WS_BASE_URL=
CORS_ORIGINS=http://\${VM_IP},http://\${VM_IP}:3000,http://localhost
ENV
fi

sudo mkdir -p /etc/pitun/naive /tmp/pitun
sudo chown "\$(id -un)":"\$(id -gn)" /etc/pitun/naive 2>/dev/null || true
mkdir -p data

# Bring stack up (without 'secure' profile — docker-proxy is optional)
\$DOCKER compose up -d

echo "[+] Waiting for backend to be reachable..."
for i in \$(seq 1 60); do
    if curl -sf http://localhost/health >/dev/null 2>&1; then break; fi
    sleep 2
done

# Alembic migrate (idempotent)
\$DOCKER exec pitun-backend alembic upgrade head || echo "[!] alembic upgrade failed — run manually"

curl -s http://localhost/health && echo
REMOTE_EOF

echo
log "============================================"
log "  Offline deploy complete"
log "============================================"
echo "  Web UI:  http://\$(ssh ${SSH_OPTS[*]} $TARGET hostname -I | awk '{print \$1}')"
echo "  Login:   admin / password"
