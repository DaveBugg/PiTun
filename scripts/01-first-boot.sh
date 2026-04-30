#!/bin/bash
# ============================================================================
# PiTun — Step 1: First Boot Configuration
# ============================================================================
# Run on a fresh Raspberry Pi OS after first login (via monitor or SSH).
# Sets up: SSH with keys, password auth, static IP, disable desktop, IP forwarding.
#
# Usage:
#   sudo bash 01-first-boot.sh [STATIC_IP] [GATEWAY] [SSH_PUBKEY]
#
# Examples:
#   sudo bash 01-first-boot.sh 192.168.1.100 192.168.1.1 "ssh-ed25519 AAAA... user@host"
#   sudo bash 01-first-boot.sh  # uses defaults: .100, .1, no key
# ============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'
log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }

STATIC_IP="${1:-}"
GATEWAY="${2:-}"
SSH_PUBKEY="${3:-}"
CURRENT_USER="${SUDO_USER:-$(whoami)}"

# Validate IPv4 format
validate_ip() {
    local ip="$1" label="$2"
    if ! echo "$ip" | grep -qE '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; then
        echo -e "${YELLOW}[!]${NC} Invalid IP format: $ip"
        return 1
    fi
    local IFS='.'
    for octet in $ip; do
        if [ "$octet" -gt 255 ] 2>/dev/null; then
            echo -e "${YELLOW}[!]${NC} Invalid octet in $label: $octet"
            return 1
        fi
    done
    return 0
}

# Interactive prompts if not provided as arguments
if [ -z "$STATIC_IP" ]; then
    read -rp "Static IP for this RPi [192.168.1.100]: " STATIC_IP
    STATIC_IP="${STATIC_IP:-192.168.1.100}"
fi
if [ -z "$GATEWAY" ]; then
    read -rp "Gateway (router IP) [192.168.1.1]: " GATEWAY
    GATEWAY="${GATEWAY:-192.168.1.1}"
fi

validate_ip "$STATIC_IP" "STATIC_IP" || { echo "Aborting."; exit 1; }
validate_ip "$GATEWAY" "GATEWAY" || { echo "Aborting."; exit 1; }

log "PiTun First Boot Setup"
log "User: $CURRENT_USER"
log "Static IP: $STATIC_IP"
log "Gateway: $GATEWAY"

# ── 1. SSH: enable, allow password + pubkey ──
log "Configuring SSH..."
sudo systemctl enable ssh
sudo systemctl start ssh

sudo sed -i 's/#PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sudo sed -i 's/PasswordAuthentication no/PasswordAuthentication yes/' /etc/ssh/sshd_config
sudo sed -i 's/#PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config
sudo sed -i 's/PubkeyAuthentication no/PubkeyAuthentication yes/' /etc/ssh/sshd_config

# Add SSH public key if provided
if [ -n "$SSH_PUBKEY" ]; then
    USER_HOME=$(eval echo "~$CURRENT_USER")
    mkdir -p "$USER_HOME/.ssh"
    echo "$SSH_PUBKEY" >> "$USER_HOME/.ssh/authorized_keys"
    sort -u "$USER_HOME/.ssh/authorized_keys" -o "$USER_HOME/.ssh/authorized_keys"
    chmod 700 "$USER_HOME/.ssh"
    chmod 600 "$USER_HOME/.ssh/authorized_keys"
    chown -R "$CURRENT_USER:$CURRENT_USER" "$USER_HOME/.ssh"
    log "SSH key added for $CURRENT_USER"
else
    warn "No SSH key provided — use password auth or add key later"
    warn "  echo 'ssh-ed25519 AAAA...' >> ~/.ssh/authorized_keys"
fi

sudo systemctl restart ssh
log "SSH enabled (password + pubkey)"

# ── 2. Disable desktop (save ~200MB RAM) ──
if systemctl is-active --quiet gdm3 || systemctl is-active --quiet lightdm; then
    log "Disabling desktop..."
    sudo systemctl set-default multi-user.target
    log "Desktop will be disabled after reboot"
else
    log "Desktop not running — already headless"
fi

# ── 3. Set static IP ──
log "Setting static IP: $STATIC_IP..."

# Check which network manager is in use
if command -v nmcli &> /dev/null; then
    # NetworkManager (RPi OS Trixie/Bookworm)
    CON_NAME=$(nmcli -t -f NAME,DEVICE con show --active | grep eth0 | cut -d: -f1)
    if [ -z "$CON_NAME" ]; then
        CON_NAME="Wired connection 1"
    fi
    sudo nmcli con mod "$CON_NAME" \
        ipv4.addresses "$STATIC_IP/24" \
        ipv4.gateway "$GATEWAY" \
        ipv4.dns "1.1.1.1 8.8.8.8" \
        ipv4.method manual
    log "Static IP set via NetworkManager"
elif [ -f /etc/dhcpcd.conf ]; then
    # dhcpcd (older RPi OS)
    if ! grep -q "interface eth0" /etc/dhcpcd.conf; then
        cat >> /etc/dhcpcd.conf << EOF

# PiTun static IP
interface eth0
static ip_address=$STATIC_IP/24
static routers=$GATEWAY
static domain_name_servers=1.1.1.1 8.8.8.8
EOF
    fi
    log "Static IP set via dhcpcd"
else
    # Fallback: /etc/network/interfaces
    cat > /etc/network/interfaces.d/eth0 << EOF
auto eth0
iface eth0 inet static
    address $STATIC_IP/24
    gateway $GATEWAY
    dns-nameservers 1.1.1.1 8.8.8.8
EOF
    log "Static IP set via /etc/network/interfaces"
fi

# ── 4. Enable IP forwarding ──
log "Enabling IP forwarding..."
echo "net.ipv4.ip_forward = 1" | sudo tee /etc/sysctl.d/99-pitun.conf > /dev/null
sudo sysctl -p /etc/sysctl.d/99-pitun.conf > /dev/null

# ── 5. Set hostname ──
log "Setting hostname to pitun..."
sudo hostnamectl set-hostname pitun
echo "127.0.1.1 pitun" | sudo tee -a /etc/hosts > /dev/null

# ── 6. Update system ──
log "Updating system packages..."
sudo apt-get update -qq
sudo apt-get upgrade -y -qq 2>&1 | tail -3

# ── Done ──
echo ""
log "============================================"
log "  First boot setup complete!"
log "============================================"
echo ""
echo "  Hostname: pitun"
echo "  Static IP: $STATIC_IP (active after reboot)"
echo "  SSH: enabled (password + pubkey)"
echo "  IP forwarding: enabled"
echo "  Desktop: disabled"
echo ""
echo "  Next steps:"
echo "    1. Reboot: sudo reboot"
echo "    2. SSH from your PC: ssh $CURRENT_USER@$STATIC_IP"
echo "    3. Run: bash ~/pitun/scripts/02-install-stack.sh"
echo ""
