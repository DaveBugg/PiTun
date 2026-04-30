#!/bin/bash
# ============================================================================
# PiTun — Change network settings (IP, gateway)
# ============================================================================
# Use when moving PiTun to a different network or changing IP/gateway.
# Sets static IP via NetworkManager, updates PiTun DB, restarts services.
#
# Usage:
#   sudo bash change-network.sh                    # interactive
#   sudo bash change-network.sh 192.168.1.10 192.168.1.1   # IP + gateway
#   sudo bash change-network.sh 192.168.0.50 192.168.0.1  # new network
# ============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'
log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[x]${NC} $*"; exit 1; }

[[ $EUID -ne 0 ]] && err "Run as root: sudo $0"

# Current values
CURRENT_IP=$(ip -4 addr show eth0 2>/dev/null | grep -oP '(?<=inet\s)\d+(\.\d+){3}' | head -1)
CURRENT_GW=$(ip route 2>/dev/null | grep 'default via' | awk '{print $3}' | head -1)
CON_NAME=$(nmcli -t -f NAME,DEVICE con show --active 2>/dev/null | grep eth0 | cut -d: -f1)

echo ""
echo "  Current IP:      ${CURRENT_IP:-unknown}"
echo "  Current gateway: ${CURRENT_GW:-unknown}"
echo "  NM connection:   ${CON_NAME:-not found}"
echo ""

# Validate IPv4
validate_ip() {
    echo "$1" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}$' || return 1
    local IFS='.'
    for octet in $1; do
        [ "$octet" -le 255 ] 2>/dev/null || return 1
    done
    return 0
}

# Get new values
NEW_IP="${1:-}"
NEW_GW="${2:-}"

if [ -z "$NEW_IP" ]; then
    read -rp "New static IP [$CURRENT_IP]: " NEW_IP
    NEW_IP="${NEW_IP:-$CURRENT_IP}"
fi
if [ -z "$NEW_GW" ]; then
    read -rp "New gateway (router IP) [$CURRENT_GW]: " NEW_GW
    NEW_GW="${NEW_GW:-$CURRENT_GW}"
fi

validate_ip "$NEW_IP" || err "Invalid IP: $NEW_IP"
validate_ip "$NEW_GW" || err "Invalid gateway: $NEW_GW"

[ "$NEW_IP" = "$NEW_GW" ] && err "IP and gateway cannot be the same! (causes routing loop)"

echo ""
log "New IP:      $NEW_IP"
log "New gateway: $NEW_GW"
echo ""
read -rp "Apply changes? [y/N]: " CONFIRM
[[ "$CONFIRM" != "y" && "$CONFIRM" != "Y" ]] && { echo "Aborted."; exit 0; }

# ── 1. Apply static IP via NetworkManager ──
if [ -z "$CON_NAME" ]; then
    CON_NAME="Wired connection 1"
    warn "No active eth0 connection found, using '$CON_NAME'"
fi

log "Setting static IP: $NEW_IP/24, gateway: $NEW_GW"
nmcli con mod "$CON_NAME" \
    ipv4.method manual \
    ipv4.addresses "$NEW_IP/24" \
    ipv4.gateway "$NEW_GW" \
    ipv4.dns "$NEW_GW"

# Apply (will briefly drop SSH if IP changed)
if [ "$NEW_IP" != "$CURRENT_IP" ]; then
    warn "IP is changing — SSH will disconnect. Reconnect to $NEW_IP"
    warn "Applying in 3 seconds..."
    sleep 3
fi

nmcli con up "$CON_NAME" > /dev/null 2>&1 || true
log "NetworkManager updated"

# ── 2. Update PiTun DB ──
DB_PATH="/home/$(logname 2>/dev/null || echo pi)/pitun/data/pitun.db"
if [ -f "$DB_PATH" ]; then
    log "Updating PiTun database..."
    sqlite3 "$DB_PATH" "UPDATE settings SET value='$NEW_IP' WHERE key='gateway_ip';" 2>/dev/null || true
    log "gateway_ip → $NEW_IP"
else
    warn "PiTun DB not found at $DB_PATH — skip DB update"
fi

# ── 3. Restart PiTun backend ──
COMPOSE_DIR="/home/$(logname 2>/dev/null || echo pi)/pitun"
if [ -f "$COMPOSE_DIR/docker-compose.yml" ]; then
    log "Restarting PiTun backend..."
    cd "$COMPOSE_DIR"
    docker compose restart backend 2>/dev/null || true
    log "Backend restarted"
fi

# ── 4. Verify ──
echo ""
log "============================================"
log "  Network settings updated!"
log "============================================"
echo ""
echo "  Static IP: $NEW_IP/24"
echo "  Gateway:   $NEW_GW"
echo "  Method:    manual (DHCP disabled)"
echo ""
echo "  If using gateway mode, update OpenWrt DHCP:"
echo "    uci delete dhcp.lan.dhcp_option"
echo "    uci add_list dhcp.lan.dhcp_option='3,$NEW_IP'"
echo "    uci add_list dhcp.lan.dhcp_option='6,$NEW_IP'"
echo "    uci commit dhcp"
echo "    /etc/init.d/dnsmasq restart"
echo ""
if [ "$NEW_IP" != "$CURRENT_IP" ]; then
    echo "  Reconnect SSH: ssh user@$NEW_IP"
fi
