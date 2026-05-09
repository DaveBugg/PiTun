#!/usr/bin/env bash
# ============================================================================
# PiTun — WireGuard server uninstaller
# ============================================================================
# Symmetric to `setup-wireguard-server.sh install`. Reverses the
# server-side state the install sub-command sets up so a re-install
# starts from a clean slate. Use case: testing different network
# configs, switching server keys, or just cleaning up after a
# burner test VPS.
#
# What this removes:
#   * wg-quick@<iface> (stop + disable). PostDown hooks in wg0.conf
#     auto-clear the iptables NAT/INPUT/FORWARD rules they installed,
#     so the firewall returns to its previous state.
#   * /etc/wireguard/                — wg0.conf, server priv key,
#                                       pitun-params, pitun-clients/.
#   * /etc/sysctl.d/99-pitun-wireguard.conf — IP-forwarding override.
#   * sysctl reapply so the live kernel state catches up.
#   * wireguard / wireguard-tools / qrencode packages (purge — only if
#     `--remove-packages` is passed; default keeps them since they're
#     small and might be used by other tunnels).
#
# What this DOES NOT touch:
#   * iptables / nftables rules NOT created by wg0's PostUp hooks
#     (PostDown reverses only what PostUp added).
#   * Other WireGuard interfaces — only the configured iface (default
#     wg0, override via WG_IF env).
#
# Usage:
#   sudo bash uninstall-wireguard-server.sh                # interactive
#   sudo YES=1 bash uninstall-wireguard-server.sh          # non-interactive
#   sudo YES=1 REMOVE_PACKAGES=1 bash uninstall-wireguard-server.sh
#   sudo WG_IF=wg1 bash uninstall-wireguard-server.sh      # alt interface
#
# Or one-liner over SSH:
#   curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/scripts/uninstall-wireguard-server.sh \
#     | sudo bash
#
# Re-run safe: every step checks state first.
# ============================================================================

set -euo pipefail

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[x]${NC} $*" >&2; exit 1; }
info() { echo -e "${BLUE}[i]${NC} $*"; }

[[ "$(id -u)" -eq 0 ]] || err "Run as root: sudo bash $0"

YES="${YES:-0}"
REMOVE_PACKAGES="${REMOVE_PACKAGES:-0}"
WG_IF="${WG_IF:-wg0}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)             YES=1 ;;
        --remove-packages)    REMOVE_PACKAGES=1 ;;
        --keep-packages)      REMOVE_PACKAGES=0 ;;
        --interface)          shift; WG_IF="${1:?missing arg}" ;;
        -h|--help)
            sed -n '2,40p' "$0"
            exit 0
            ;;
        *) err "Unknown argument: $1" ;;
    esac
    shift
done

WG_CONF="/etc/wireguard/${WG_IF}.conf"
WG_PARAMS="/etc/wireguard/pitun-params"
CLIENTS_DIR="/etc/wireguard/pitun-clients"
SYSCTL_CONF="/etc/sysctl.d/99-pitun-wireguard.conf"

cat <<BANNER
================================================================
 PiTun — WireGuard uninstaller
================================================================
This will:
  • Stop + disable wg-quick@${WG_IF} (PostDown clears its iptables rules)
  • Remove ${WG_CONF}, ${WG_PARAMS}, ${CLIENTS_DIR}/
  • Remove ${SYSCTL_CONF} and reload sysctl
  • $([[ "$REMOVE_PACKAGES" == "1" ]] && echo 'Purge wireguard / wireguard-tools / qrencode packages' || echo 'Keep packages installed (pass --remove-packages to also purge)')

NOT touched:
  • Other WireGuard interfaces (only ${WG_IF})
  • Manual iptables / nftables rules outside wg0.conf's PostUp set

================================================================
BANNER

if [[ "$YES" != "1" ]]; then
    read -r -p "Proceed? [y/N]: " _ans
    [[ "${_ans:-N}" =~ ^[yY]$ ]] || { info "Aborted."; exit 0; }
fi

# ── 1. Stop + disable the systemd unit ─────────────────────────────────────
# wg-quick@<if>.service is auto-generated from wg-quick@.service —
# it exists as a unit file as soon as wg-quick is installed. Stopping
# it triggers PostDown which removes the firewall rules.
if systemctl is-active --quiet "wg-quick@${WG_IF}"; then
    log "Stopping wg-quick@${WG_IF} (PostDown will clean up iptables)…"
    systemctl stop "wg-quick@${WG_IF}" || \
        warn "systemctl stop returned non-zero; check 'wg show' / 'iptables -L' for stragglers."
else
    info "wg-quick@${WG_IF} not running — skipping stop"
fi

if systemctl is-enabled --quiet "wg-quick@${WG_IF}" 2>/dev/null; then
    log "Disabling wg-quick@${WG_IF}…"
    systemctl disable "wg-quick@${WG_IF}" 2>/dev/null || true
fi

# Defensive: if PostDown was somehow skipped (e.g. the service was
# already in a failed state when we stopped it), make sure the
# interface is gone before we delete its conf. `ip link show` is
# cheap and lets us avoid leaving a dangling kernel interface.
if ip link show "${WG_IF}" >/dev/null 2>&1; then
    warn "Interface ${WG_IF} still up after stop — bringing it down via wg-quick down…"
    wg-quick down "${WG_IF}" 2>/dev/null || \
        ip link delete "${WG_IF}" 2>/dev/null || \
        warn "Could not remove ${WG_IF} interface — check 'ip link' manually"
fi

# ── 2. Remove config + state ───────────────────────────────────────────────
for f in "$WG_CONF" "$WG_PARAMS"; do
    if [[ -f "$f" ]]; then
        log "Removing $f"
        rm -f "$f"
    fi
done

if [[ -d "$CLIENTS_DIR" ]]; then
    log "Removing $CLIENTS_DIR/ (peer .conf cache)"
    rm -rf "$CLIENTS_DIR"
fi

# Don't `rmdir /etc/wireguard` — it's a system-wide dir, other
# tunnels (or future installs) may use it. Stays around empty.

# ── 3. Sysctl override ─────────────────────────────────────────────────────
if [[ -f "$SYSCTL_CONF" ]]; then
    log "Removing $SYSCTL_CONF + reloading sysctl…"
    rm -f "$SYSCTL_CONF"
    # Reapply remaining sysctl files so the running kernel reflects
    # the post-removal state. `--system` walks all /etc/sysctl.d/
    # plus /usr/lib/sysctl.d/ etc., which is what we want — the
    # original distro defaults take over once our override is gone.
    sysctl --system >/dev/null 2>&1 || \
        warn "sysctl --system returned non-zero; ip_forward live state may be stale until reboot"
fi

# ── 4. Optionally purge packages ───────────────────────────────────────────
if [[ "$REMOVE_PACKAGES" == "1" ]]; then
    log "Purging wireguard / wireguard-tools / qrencode packages…"
    DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq \
        wireguard wireguard-tools qrencode 2>/dev/null || \
        warn "apt purge returned non-zero; some packages may not have been installed."
    DEBIAN_FRONTEND=noninteractive apt-get autoremove -y -qq >/dev/null 2>&1 || true
else
    info "Packages kept (use --remove-packages to also remove wireguard-tools etc.)"
fi

cat <<DONE

================================================================
 WireGuard server uninstall complete.
================================================================

  ${WG_IF} interface:    $(ip link show "${WG_IF}" >/dev/null 2>&1 && echo 'STILL PRESENT (?)' || echo 'gone')
  ${WG_CONF}:    $([[ -f "$WG_CONF" ]] && echo 'present' || echo 'removed')
  ${SYSCTL_CONF}: $([[ -f "$SYSCTL_CONF" ]] && echo 'present' || echo 'removed')
  net.ipv4.ip_forward live: $(cat /proc/sys/net/ipv4/ip_forward 2>/dev/null || echo '?')
  Packages:    $(dpkg -l wireguard-tools 2>/dev/null | grep -q '^ii' && echo 'installed' || echo 'not installed')

You can now re-run setup-wireguard-server.sh install from a clean slate.
================================================================
DONE
