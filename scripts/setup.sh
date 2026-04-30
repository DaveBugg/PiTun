#!/usr/bin/env bash
# PiTun — Raspberry Pi 4B initial setup script
# Tested on Debian Bookworm / Ubuntu 22.04+
# Run as root: sudo bash setup.sh

set -euo pipefail

XRAY_VERSION="${XRAY_VERSION:-26.3.27}"
XRAY_INSTALL_DIR="/usr/local/bin"
XRAY_ASSET_DIR="/usr/local/share/xray"
PITUN_DIR="${PITUN_DIR:-/opt/pitun}"
ARCH="$(uname -m)"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && error "Run as root (sudo bash $0)"

# ── 1. System packages ────────────────────────────────────────────────────────
info "Installing system packages…"
apt-get update -qq
apt-get install -y --no-install-recommends \
    curl wget ca-certificates gnupg lsb-release \
    nftables iproute2 net-tools iptables \
    arp-scan dnsutils \
    unzip jq cron

# Enable IP forwarding
info "Enabling IP forwarding…"
cat > /etc/sysctl.d/99-pitun.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
# Required for TPROXY
net.ipv4.conf.all.route_localnet = 1
EOF
sysctl -p /etc/sysctl.d/99-pitun.conf

# ── 1a. Free port 5353 (used by xray's internal DNS forwarder) ───────────────
# avahi-daemon is the standard mDNS / `.local` resolver on most distros and
# binds UDP/5353 — exactly the port PiTun's xray DNS uses by default. We
# stop+mask it; if you actually use `.local` discovery on this host, change
# DNS_PORT in `.env` to a free port instead and re-enable avahi.
if systemctl is-active --quiet avahi-daemon 2>/dev/null; then
    info "Disabling avahi-daemon (frees UDP/5353 for xray DNS)…"
    systemctl stop avahi-daemon avahi-daemon.socket 2>/dev/null || true
    systemctl disable avahi-daemon avahi-daemon.socket 2>/dev/null || true
    systemctl mask avahi-daemon 2>/dev/null || true
fi

# ── 1b. Load TPROXY kernel modules ────────────────────────────────────────────
# nftables' tproxy verdict and the legacy `xt_TPROXY` matcher both need their
# kernel modules loaded. They auto-load on most modern distros when nft hits
# a tproxy rule, but pinning them makes the first deploy reliable and keeps
# them available across reboots.
info "Loading TPROXY kernel modules…"
modprobe nft_tproxy 2>/dev/null || true
modprobe xt_TPROXY 2>/dev/null || true
echo -e "nft_tproxy\nxt_TPROXY" > /etc/modules-load.d/pitun.conf

# ── 2. Docker ─────────────────────────────────────────────────────────────────
if command -v docker &>/dev/null; then
    info "Docker already installed: $(docker --version)"
else
    info "Installing Docker…"
    install -m 0755 -d /etc/apt/keyrings
    # Detect distro: debian or ubuntu
    DISTRO_ID=$(. /etc/os-release && echo "$ID")
    case "$DISTRO_ID" in
        ubuntu|debian) ;;
        *) DISTRO_ID="debian" ;;
    esac
    curl -fsSL "https://download.docker.com/linux/${DISTRO_ID}/gpg" \
        | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
        https://download.docker.com/linux/${DISTRO_ID} \
        $(lsb_release -cs) stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -qq
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    systemctl enable --now docker
    info "Docker installed: $(docker --version)"
fi

# ── 3. xray-core ──────────────────────────────────────────────────────────────
if [[ -f "${XRAY_INSTALL_DIR}/xray" ]]; then
    CURRENT_VER="$("${XRAY_INSTALL_DIR}/xray" version 2>/dev/null | head -1 | awk '{print $2}')"
    if [[ "$CURRENT_VER" == "$XRAY_VERSION" ]]; then
        info "xray ${XRAY_VERSION} already installed, skipping"
    else
        warn "xray ${CURRENT_VER} installed, upgrading to ${XRAY_VERSION}…"
        _install_xray=1
    fi
else
    _install_xray=1
fi

if [[ "${_install_xray:-0}" == "1" ]]; then
    info "Installing xray-core ${XRAY_VERSION}…"
    case "$ARCH" in
        aarch64|arm64) XRAY_ARCH="arm64-v8a" ;;
        armv7l)        XRAY_ARCH="arm32-v7a" ;;
        x86_64)        XRAY_ARCH="64" ;;
        *)             error "Unsupported architecture: $ARCH" ;;
    esac

    XRAY_URL="https://github.com/XTLS/Xray-core/releases/download/v${XRAY_VERSION}/Xray-linux-${XRAY_ARCH}.zip"
    TMP=$(mktemp -d)
    curl -fsSL "$XRAY_URL" -o "$TMP/xray.zip"
    unzip -qo "$TMP/xray.zip" -d "$TMP/xray"
    install -m 755 "$TMP/xray/xray" "${XRAY_INSTALL_DIR}/xray"
    rm -rf "$TMP"
    info "xray installed at ${XRAY_INSTALL_DIR}/xray"
fi

# ── 4. GeoData ────────────────────────────────────────────────────────────────
mkdir -p "$XRAY_ASSET_DIR"
for file in geoip.dat geosite.dat; do
    if [[ ! -f "${XRAY_ASSET_DIR}/${file}" ]]; then
        info "Downloading ${file}…"
        curl -fsSL \
            "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/${file}" \
            -o "${XRAY_ASSET_DIR}/${file}"
    else
        info "${file} already present"
    fi
done

# ── 5. nftables service ───────────────────────────────────────────────────────
info "Enabling nftables service…"
systemctl enable --now nftables || true

# ── 6. Temp directories ───────────────────────────────────────────────────────
mkdir -p /tmp/pitun
chmod 755 /tmp/pitun

# ── 7. PiTun data directory ───────────────────────────────────────────────────
mkdir -p "${PITUN_DIR}/data"
info "PiTun data directory: ${PITUN_DIR}/data"

# ── 8. Docker log rotation ─────────────────────────────────────────────────────
if [ ! -f /etc/docker/daemon.json ]; then
  info "Configuring Docker log rotation (10m × 3)…"
  mkdir -p /etc/docker
  cat > /etc/docker/daemon.json <<'DOCKER_JSON'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
DOCKER_JSON
fi

# ── 9. Cron cleanup job ───────────────────────────────────────────────────────
CLEANUP_SCRIPT="${PITUN_DIR}/scripts/cleanup.sh"
if [ -f "$CLEANUP_SCRIPT" ]; then
  chmod +x "$CLEANUP_SCRIPT"
  cat > /etc/cron.d/pitun-cleanup <<CRON_EOF
# PiTun daily cleanup — docker prune, journalctl vacuum, temp files
0 4 * * * root ${CLEANUP_SCRIPT} >> /var/log/pitun-cleanup.log 2>&1
CRON_EOF
  chmod 644 /etc/cron.d/pitun-cleanup
  info "Daily cleanup cron job installed (04:00)"
fi

# ── 10. Summary ───────────────────────────────────────────────────────────────
echo ""
info "Setup complete!"
echo -e "
${GREEN}Next steps:${NC}
  1. cd to your pitun project directory
  2. cp .env.example .env && edit .env
  3. docker compose up -d
  4. Open http://$(hostname -I | awk '{print $1}')

${YELLOW}Network setup:${NC}
  - Set RPi IP as static: 192.168.1.100 (edit /etc/dhcpcd.conf or netplan)
  - Set devices' gateway to 192.168.1.100 to route through proxy
  - OR use DHCP option 3 to push gateway to all devices
"
