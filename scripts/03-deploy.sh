#!/bin/bash
# ============================================================================
# PiTun — Step 3: Deploy PiTun application
# ============================================================================
# Run after 02-install-stack.sh and re-login.
# Creates .env, builds Docker containers, starts the app.
#
# Usage:
#   bash 03-deploy.sh [STATIC_IP]
#
# Examples:
#   bash 03-deploy.sh 192.168.1.100
#   bash 03-deploy.sh  # auto-detects current IP
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

VM_IP="${1:-$(hostname -I | awk '{print $1}')}"
log "PiTun Deployment — IP: $VM_IP"

# ── 0. Fix ownership of pitun directory ──
CURRENT_USER=$(id -un)
CURRENT_GROUP=$(id -gn)
log "Fixing ownership of $PITUN_DIR..."
sudo chown -R "$CURRENT_USER:$CURRENT_GROUP" "$PITUN_DIR"
chmod -R u+rwX "$PITUN_DIR"

# ── 1. Verify prerequisites ──
docker --version > /dev/null 2>&1 || err "Docker not installed — run 02-install-stack.sh first"

# Auto-detect if sudo is needed for docker
DOCKER="docker"
if ! docker info > /dev/null 2>&1; then
    if sudo docker info > /dev/null 2>&1; then
        DOCKER="sudo docker"
        warn "Docker requires sudo — consider: sudo usermod -aG docker $CURRENT_USER && re-login"
    else
        err "Cannot connect to Docker daemon"
    fi
fi
$DOCKER compose version > /dev/null 2>&1 || err "Docker Compose not installed"
[ -x /usr/local/bin/xray ] || err "xray not installed — run 02-install-stack.sh first"

# ── 1b. Disable avahi-daemon if it occupies port 5353 ──
if systemctl is-active --quiet avahi-daemon 2>/dev/null; then
    warn "avahi-daemon is running on port 5353 — disabling for xray DNS..."
    sudo systemctl stop avahi-daemon avahi-daemon.socket 2>/dev/null || true
    sudo systemctl disable avahi-daemon avahi-daemon.socket 2>/dev/null || true
    sudo systemctl mask avahi-daemon 2>/dev/null || true
    log "avahi-daemon disabled"
fi

# ── 1c. Ensure Docker Hub is reachable, add mirror if not ──
DAEMON_JSON="/etc/docker/daemon.json"
if ! curl -sI --max-time 5 https://registry-1.docker.io/v2/ | grep -q "HTTP"; then
    warn "Docker Hub is not reachable directly — configuring registry mirror..."
    if [ ! -f "$DAEMON_JSON" ] || ! grep -q "registry-mirrors" "$DAEMON_JSON" 2>/dev/null; then
        sudo tee "$DAEMON_JSON" > /dev/null <<'MIRROR'
{
  "registry-mirrors": [
    "https://mirror.gcr.io",
    "https://dockerhub.timeweb.cloud",
    "https://huecker.io"
  ]
}
MIRROR
        sudo systemctl restart docker
        sleep 2
        log "Docker registry mirrors configured and daemon restarted"
    fi
fi

# ── 2. Create .env if not exists ──
if [ ! -f .env ]; then
    log "Generating .env..."
    SECRET_KEY=$(openssl rand -hex 32)
    cat > .env << EOF
# PiTun configuration — auto-generated
SECRET_KEY=${SECRET_KEY}
BACKEND_PORT=8000
DATABASE_URL=sqlite:///./data/pitun.db

# xray
XRAY_BINARY=/usr/local/bin/xray
XRAY_CONFIG_PATH=/tmp/pitun/config.json
XRAY_GEOIP_PATH=/usr/local/share/xray/geoip.dat
XRAY_GEOSITE_PATH=/usr/local/share/xray/geosite.dat
XRAY_LOG_LEVEL=warning

# Network
TPROXY_PORT_TCP=7893
TPROXY_PORT_UDP=7894
DNS_PORT=5353
INTERFACE=eth0
LAN_CIDR=192.168.1.0/24
GATEWAY_IP=${VM_IP}

# GeoData
GEOIP_URL=https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat
GEOSITE_URL=https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat

# Frontend (relative paths — works via nginx reverse proxy regardless of IP)
VITE_API_BASE_URL=/api
VITE_WS_BASE_URL=

# CORS
CORS_ORIGINS=http://${VM_IP},http://${VM_IP}:3000,http://localhost:5173,http://localhost:3000
EOF
    log ".env created with SECRET_KEY"
else
    warn ".env already exists — keeping it"
fi

# ── 3. Create data directory ──
mkdir -p data
mkdir -p /tmp/pitun
# NaiveProxy sidecar config dir (mounted into backend)
sudo mkdir -p /etc/pitun/naive
sudo chown "$CURRENT_USER":"$CURRENT_USER" /etc/pitun/naive 2>/dev/null || true

# ── 4. Install cleanup cron job ──
if [ -f "$PITUN_DIR/scripts/cleanup.sh" ] && command -v cron &>/dev/null; then
    chmod +x "$PITUN_DIR/scripts/cleanup.sh" 2>/dev/null || true
    sudo tee /etc/cron.d/pitun-cleanup > /dev/null <<EOF
# PiTun daily cleanup — docker prune, journalctl vacuum, temp files
0 4 * * * root /bin/bash $PITUN_DIR/scripts/cleanup.sh >> /var/log/pitun-cleanup.log 2>&1
EOF
    sudo chmod 644 /etc/cron.d/pitun-cleanup
    log "Cleanup cron job installed (daily 04:00)"
fi

# ── 4b. Offline image fallback ──
# If docker/offline/*.tar.gz bundles are present, load them before attempting
# a network build. Makes 03-deploy.sh work on airgapped devices too.
OFFLINE_DIR="$PITUN_DIR/docker/offline"
if [ -d "$OFFLINE_DIR" ] && ls "$OFFLINE_DIR"/*.tar.gz >/dev/null 2>&1; then
    log "Offline bundle detected in docker/offline/ — loading images..."
    for t in "$OFFLINE_DIR"/*.tar.gz; do
        log "  loading $(basename "$t")"
        gunzip -c "$t" | $DOCKER load >/dev/null || warn "failed to load $t"
    done
    # Retag arch-suffixed images to the tags docker-compose expects.
    for arch in arm64 amd64; do
        $DOCKER tag "pitun-backend:latest-${arch}"         pitun-backend:latest   2>/dev/null || true
        $DOCKER tag "pitun-frontend:latest-${arch}"        pitun-frontend:latest  2>/dev/null || true
        $DOCKER tag "pitun-naive:latest-${arch}"           pitun-naive:latest     2>/dev/null || true
        $DOCKER tag "nginx-${arch}:1.25-alpine"            nginx:1.25-alpine      2>/dev/null || true
        $DOCKER tag "docker-socket-proxy-${arch}:0.3"      tecnativa/docker-socket-proxy:0.3 2>/dev/null || true
    done
fi

# ── 5. Build and start ──
#
# Safe rebuild: if tproxy is currently active, Docker's outbound traffic to
# registries gets intercepted by xray and may fail (depending on current mode
# and routing rules). We temporarily flush the pitun nftables table so that
# the build/pull traffic goes direct via the default gateway, then rely on
# the backend's own startup path to re-apply the rules after the rebuild.
REBUILD_FLUSHED=0
if sudo nft list table inet pitun > /dev/null 2>&1; then
    warn "Active tproxy detected — flushing nftables for the duration of the rebuild"
    sudo nft delete table inet pitun 2>/dev/null || true
    sudo ip rule del fwmark 1 lookup 100 2>/dev/null || true
    sudo ip route del local 0.0.0.0/0 dev lo table 100 2>/dev/null || true
    REBUILD_FLUSHED=1
fi

log "Building Docker containers (this may take 5-10 minutes on first run)..."
$DOCKER compose up -d --build 2>&1

# Build NaiveProxy sidecar image (used by backend to spawn naive nodes).
# Cheap to rebuild — binary is cached inside the image layer.
if [ -f "$PITUN_DIR/docker/naive/Dockerfile" ]; then
    log "Building pitun-naive sidecar image..."
    if ! $DOCKER build -t pitun-naive:latest "$PITUN_DIR/docker/naive" 2>&1; then
        warn "Failed to build pitun-naive image — NaiveProxy nodes will not work until this is fixed"
    fi
fi

# Re-apply nftables rules via backend if we had flushed them
if [ "$REBUILD_FLUSHED" = "1" ]; then
    log "Waiting for backend to come back up to re-apply nftables rules..."
    for i in $(seq 1 30); do
        if curl -s http://localhost/health > /dev/null 2>&1; then
            break
        fi
        sleep 2
    done
    $DOCKER exec pitun-backend python3 - <<'PYEOF' 2>/dev/null || warn "Could not re-apply nftables automatically; reload from UI (Settings → Apply)"
import asyncio
from sqlmodel.ext.asyncio.session import AsyncSession
from app.database import get_async_engine
from app.api.system import _apply_nftables, _load_settings_map
from app.core.xray import xray_manager

async def main():
    async with AsyncSession(get_async_engine()) as s:
        sm = await _load_settings_map(s)
        if xray_manager.is_running:
            await _apply_nftables(s, sm)
            print("nftables rules re-applied")
        else:
            print("xray not running — rules will be applied on next /system/start")

asyncio.run(main())
PYEOF
fi

# ── 6. Wait for backend ──
log "Waiting for backend to start..."
for i in $(seq 1 60); do
    if curl -s http://localhost/health > /dev/null 2>&1; then
        break
    fi
    sleep 2
    [ $((i % 10)) -eq 0 ] && echo "  Still waiting... ($i/60)"
done

# ── 7. Verify ──
HEALTH=$(curl -s http://localhost/health 2>/dev/null || echo '{"status":"error"}')

echo ""
log "============================================"
log "  PiTun deployed!"
log "============================================"
echo ""
echo "  Health: $HEALTH"
echo ""
echo "  Web UI:     http://${VM_IP}"
echo "  API docs:   http://${VM_IP}/api/docs"
echo "  Login:      admin / password"
echo ""
echo "  Proxy endpoints (after adding nodes and starting xray):"
echo "    TPROXY:   set device gateway to ${VM_IP}"
echo "    SOCKS5:   ${VM_IP}:1080"
echo "    HTTP:     ${VM_IP}:8080"
echo ""
echo "  Management:"
echo "    Logs:     docker compose logs -f"
echo "    Stop:     docker compose down"
echo "    Update:   git pull && docker compose up -d --build"
echo "    Tests:    docker exec pitun-backend python -m pytest tests/ -v"
echo "    Reset pw: docker exec pitun-backend bash /app/scripts/reset-password.sh newpassword"
echo ""
echo "  Run e2e tests:"
echo "    bash scripts/e2e-test.sh"
echo ""
