#!/bin/bash
# ============================================================================
# PiTun — Step 2: Install Docker + xray + dependencies
# ============================================================================
# Run after 01-first-boot.sh and reboot.
# Installs: Docker, Docker Compose v2, xray-core, nftables, system deps.
#
# Usage:
#   sudo bash 02-install-stack.sh
# ============================================================================

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'
log()  { echo -e "${GREEN}[+]${NC} $*"; }
err()  { echo -e "${RED}[x]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && err "Run as root: sudo $0"
CURRENT_USER="${SUDO_USER:-$(whoami)}"

log "PiTun Stack Installation"

# ── 0. Fix DNS if UDP:53 is blocked (some routers intercept/block it) ──
log "Checking DNS..."
if ! timeout 5 getent hosts google.com > /dev/null 2>&1; then
    warn "DNS not working (UDP may be blocked). Switching to DNS over TCP..."
    printf "nameserver 8.8.8.8\nnameserver 1.1.1.1\noptions use-vc\n" > /etc/resolv.conf
    if timeout 5 getent hosts google.com > /dev/null 2>&1; then
        log "DNS over TCP works — fix applied"
    else
        warn "DNS still failing. Check network connectivity manually."
    fi
else
    log "DNS OK"
fi

# ── 1. System dependencies ──
log "Installing system packages..."
apt-get update -qq
apt-get install -y -qq \
    curl git nftables iproute2 net-tools sqlite3 unzip \
    ca-certificates gnupg lsb-release \
    arp-scan dnsutils jq cron \
    > /dev/null 2>&1
log "System packages OK"

# ── 1b. Disable avahi-daemon (occupies port 5353 needed by xray DNS) ──
if systemctl is-active --quiet avahi-daemon 2>/dev/null; then
    log "Disabling avahi-daemon (frees port 5353 for xray DNS)..."
    systemctl stop avahi-daemon 2>/dev/null || true
    systemctl stop avahi-daemon.socket 2>/dev/null || true
    systemctl disable avahi-daemon 2>/dev/null || true
    systemctl disable avahi-daemon.socket 2>/dev/null || true
    systemctl mask avahi-daemon 2>/dev/null || true
    log "avahi-daemon disabled"
fi

# ── 2. Docker ──
if command -v docker &> /dev/null; then
    log "Docker already installed: $(docker --version)"
else
    log "Installing Docker..."
    apt-get install -y -qq docker.io > /dev/null 2>&1
    systemctl enable docker
    systemctl start docker
    usermod -aG docker "$CURRENT_USER"
    log "Docker installed: $(docker --version)"
fi

# ── 3. Docker Compose v2 ──
if docker compose version &> /dev/null; then
    log "Docker Compose already installed: $(docker compose version)"
else
    log "Installing Docker Compose v2..."
    ARCH=$(uname -m)
    case "$ARCH" in
        aarch64) COMPOSE_ARCH="aarch64" ;;
        x86_64)  COMPOSE_ARCH="x86_64" ;;
        armv7l)  COMPOSE_ARCH="armv7" ;;
        *)       err "Unsupported arch: $ARCH" ;;
    esac
    mkdir -p /usr/local/lib/docker/cli-plugins
    curl -sSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
        "https://github.com/docker/compose/releases/download/v2.29.1/docker-compose-linux-${COMPOSE_ARCH}"
    chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
    log "Docker Compose installed: $(docker compose version)"
fi

# ── 3b. Docker log rotation ──
if [ ! -f /etc/docker/daemon.json ]; then
    log "Configuring Docker log rotation (10m × 3)..."
    cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
EOF
    systemctl restart docker
    log "Docker log rotation configured"
else
    log "Docker daemon.json already exists, skipping log rotation"
fi

# ── 4. xray-core ──
if [ -x /usr/local/bin/xray ]; then
    log "xray already installed: $(/usr/local/bin/xray version | head -1)"
else
    log "Installing xray-core..."
    XRAY_VERSION="26.3.27"
    ARCH=$(uname -m)
    case "$ARCH" in
        x86_64)  XRAY_ARCH="64" ;;
        aarch64) XRAY_ARCH="arm64-v8a" ;;
        armv7l)  XRAY_ARCH="arm32-v7a" ;;
        *)       err "Unsupported arch: $ARCH" ;;
    esac
    cd /tmp
    curl -sSL -o xray.zip \
        "https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip"
    # Verify SHA256 checksum if available
    DGST_URL="https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip.dgst"
    if curl -sSL -o xray.zip.dgst "$DGST_URL" 2>/dev/null && [ -s xray.zip.dgst ]; then
        EXPECTED=$(grep -i 'SHA2-256' xray.zip.dgst | head -1 | awk '{print $NF}')
        ACTUAL=$(sha256sum xray.zip | awk '{print $1}')
        if [ -n "$EXPECTED" ] && [ "$EXPECTED" != "$ACTUAL" ]; then
            err "xray checksum mismatch! Expected: $EXPECTED Got: $ACTUAL"
        fi
        log "xray checksum verified"
    fi
    rm -f xray.zip.dgst
    unzip -o xray.zip xray geoip.dat geosite.dat > /dev/null
    install -m 755 xray /usr/local/bin/xray
    mkdir -p /usr/local/share/xray
    mv -f geoip.dat geosite.dat /usr/local/share/xray/
    rm -f xray.zip LICENSE README.md
    cd -
    log "xray installed: $(/usr/local/bin/xray version | head -1)"
fi

# ── 5. Load kernel modules for TPROXY ──
log "Loading TPROXY kernel modules..."
modprobe nft_tproxy 2>/dev/null || true
modprobe xt_TPROXY 2>/dev/null || true

# Persist modules
echo "nft_tproxy" > /etc/modules-load.d/pitun.conf 2>/dev/null || true

# ── 6. Verify static IP ──
# Static IP is set in 01-first-boot.sh. Verify it's active — if PiTun uses DHCP,
# it will receive its own DHCP option 3 (gateway) and route to itself, breaking everything.
METHOD=$(nmcli -t -f ipv4.method con show "$(nmcli -t -f NAME,DEVICE con show --active | grep eth0 | cut -d: -f1)" 2>/dev/null | cut -d: -f2)
if [ "$METHOD" = "manual" ]; then
    log "Static IP verified (set in 01-first-boot.sh)"
else
    warn "WARNING: eth0 is using DHCP! Run 01-first-boot.sh first to set static IP."
    warn "Gateway mode will break without static IP (DHCP option 3 loop)."
fi

# ── Done ──
echo ""
log "============================================"
log "  Stack installation complete!"
log "============================================"
echo ""
echo "  Docker: $(docker --version)"
echo "  Compose: $(docker compose version)"
echo "  xray: $(/usr/local/bin/xray version | head -1)"
echo "  nftables: $(nft --version)"
echo ""
echo "  IMPORTANT: Log out and back in for docker group to take effect:"
echo "    exit"
echo "    ssh $CURRENT_USER@$(hostname -I | awk '{print $1}')"
echo ""
echo "  Then run: bash ~/pitun/scripts/03-deploy.sh"
echo ""
