#!/usr/bin/env bash
# ============================================================================
# PiTun — x-ui server uninstaller (since v1.3.0-beta.7)
# ============================================================================
# Symmetric to `setup-xui-server.sh`. Reverses both install modes (x-ui-pro
# full stack and bare 3x-ui) — re-run safe, no-ops on absent state.
#
# What this removes:
#   * x-ui systemd unit, binary at /usr/local/x-ui, state at /etc/x-ui
#   * nginx + sites + /etc/nginx/sites-{available,enabled}/<domain>
#     (always — x-ui-pro mode always installed nginx; bare mode leaves it
#     untouched and this is a no-op there)
#   * tor + tor-geoipdb + python3-certbot-nginx + certbot (only if our
#     install dropped them — detected via x-ui-pro's marker files)
#   * /etc/letsencrypt/{live,archive,renewal}/<domain>  (only with
#     --purge-letsencrypt, default off — cert renewal data is shared
#     across services, killing it can break unrelated TLS)
#   * /var/www/html contents (decoy site / random fakesite)
#   * fail2ban (only if --remove-fail2ban / FAIL2BAN=remove)
#
# What this DOES NOT touch:
#   * SSH hardening tweaks — irreversible without state tracking. If the
#     install moved sshd port, the drop-in stays so the operator's session
#     doesn't get cut.
#   * UFW rules — we don't track which were ours vs pre-existing.
#
# Usage:
#   sudo bash uninstall-xui-server.sh
#   sudo YES=1 bash uninstall-xui-server.sh
#   sudo YES=1 FAIL2BAN=remove PURGE_LETSENCRYPT=yes bash uninstall-xui-server.sh
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
FAIL2BAN="${FAIL2BAN:-keep}"
PURGE_LETSENCRYPT="${PURGE_LETSENCRYPT:-no}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)               YES=1 ;;
        --remove-fail2ban)      FAIL2BAN=remove ;;
        --keep-fail2ban)        FAIL2BAN=keep ;;
        --purge-letsencrypt)    PURGE_LETSENCRYPT=yes ;;
        -h|--help)              sed -n '2,32p' "$0"; exit 0 ;;
        *) err "Unknown argument: $1" ;;
    esac
    shift
done

# Detect which mode was used: x-ui-pro always drops /etc/nginx with
# `cloudflare_real_ips.conf` and an /etc/nginx/sites-available entry.
INSTALLED_MODE="bare"
if [[ -f /etc/nginx/cloudflareips.sh ]] || [[ -f /etc/nginx/conf.d/cloudflare_whitelist.conf ]]; then
    INSTALLED_MODE="xui-pro"
fi

cat <<BANNER
================================================================
 PiTun — x-ui uninstaller
================================================================
 Detected install: $INSTALLED_MODE

This will remove:
  * x-ui systemd unit + /usr/local/x-ui + /etc/x-ui
  * /var/www/html contents
$([[ "$INSTALLED_MODE" == "xui-pro" ]] && cat <<EOF
  * nginx (purged) + /etc/nginx site files
  * tor + tor-geoipdb (purged — only used by x-ui-pro's WARP/Tor outbounds)
  * python3-certbot-nginx + certbot (purged)
$([[ "$PURGE_LETSENCRYPT" == "yes" ]] && echo '  * /etc/letsencrypt/{live,archive,renewal}' || echo '  * /etc/letsencrypt kept (pass --purge-letsencrypt to also remove)')
EOF
)
  * $([[ "$FAIL2BAN" == "remove" ]] && echo 'fail2ban (purged)' || echo 'fail2ban (kept — pass --remove-fail2ban to also remove)')

NOT removed:
  * SSH hardening (sshd_config.d drop-in)
  * UFW rules
================================================================
BANNER

if [[ "$YES" != "1" ]]; then
    read -r -p "Proceed? [y/N]: " ans
    [[ "${ans:-N}" =~ ^[yY]$ ]] || { info "Aborted."; exit 0; }
fi

# ── 1. Stop x-ui ────────────────────────────────────────────────────────────
if systemctl list-unit-files 2>/dev/null | grep -q '^x-ui\.service'; then
    log "Stopping x-ui.service..."
    systemctl stop x-ui 2>/dev/null || true
    systemctl disable x-ui 2>/dev/null || true
fi

# ── 2. Remove x-ui binary + state ───────────────────────────────────────────
# Try upstream's own uninstaller first — it cleans up auxiliary state like
# the `x-ui` PATH symlink that we'd otherwise have to chase. Fall back to
# manual removal if the CLI is gone.
if [[ -x /usr/local/x-ui/x-ui ]]; then
    log "Running upstream x-ui uninstall (binary side)..."
    /usr/local/x-ui/x-ui uninstall >/dev/null 2>&1 || true
fi
rm -rf /usr/local/x-ui /etc/x-ui
rm -f /etc/systemd/system/x-ui.service
systemctl daemon-reload 2>/dev/null || true

# Also strip the PATH wrapper that 3x-ui's installer drops at /usr/bin/x-ui
# — leftover symlinks confuse re-install detection.
rm -f /usr/bin/x-ui /usr/local/bin/x-ui

# ── 3. /var/www/html cleanup ────────────────────────────────────────────────
if [[ -d /var/www/html ]] && [[ -n "$(ls -A /var/www/html 2>/dev/null)" ]]; then
    log "Clearing /var/www/html/..."
    rm -rf /var/www/html/* /var/www/html/.[!.]* /var/www/html/..?* 2>/dev/null || true
fi

# ── 4. x-ui-pro-specific cleanup ────────────────────────────────────────────
if [[ "$INSTALLED_MODE" == "xui-pro" ]]; then
    # nginx — purge + drop our site configs. x-ui-pro stomps all over /etc/nginx
    # with its own nginx.conf + sites-{available,enabled} + cloudflare conf,
    # so a purge here doesn't risk eating an unrelated nginx install (we'd
    # have detected that as a 443-conflict at install time and refused).
    if dpkg -l nginx 2>/dev/null | grep -q '^ii' \
        || dpkg -l nginx-full 2>/dev/null | grep -q '^ii'; then
        log "Purging nginx..."
        systemctl stop nginx 2>/dev/null || true
        DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq nginx nginx-full nginx-common \
            || warn "apt purge nginx returned non-zero"
    fi
    rm -rf /etc/nginx /var/log/nginx
    rm -f /etc/logrotate.d/nginx

    # certbot + the nginx plugin (always paired by x-ui-pro)
    if dpkg -l python3-certbot-nginx 2>/dev/null | grep -q '^ii' \
        || dpkg -l certbot 2>/dev/null | grep -q '^ii'; then
        log "Purging certbot..."
        DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq \
            python3-certbot-nginx certbot \
            || warn "apt purge certbot returned non-zero"
    fi

    # Let's Encrypt state — opt-in because the data is shared across
    # services and removing it forces fresh cert issuance (rate limits).
    if [[ "$PURGE_LETSENCRYPT" == "yes" ]] && [[ -d /etc/letsencrypt ]]; then
        log "Removing /etc/letsencrypt/..."
        rm -rf /etc/letsencrypt
    fi

    # tor + geoipdb (x-ui-pro installs these for WARP/Psiphon support).
    # The user can still keep tor by setting `TOR=keep` env, but default
    # purge is right because no other PiTun feature uses tor.
    if dpkg -l tor 2>/dev/null | grep -q '^ii'; then
        log "Purging tor + tor-geoipdb..."
        systemctl stop tor 2>/dev/null || true
        DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq tor tor-geoipdb \
            || warn "apt purge tor returned non-zero"
        rm -rf /etc/tor
    fi

    # warp-plus binary if x-ui-pro added it
    rm -f /etc/systemd/system/warp-plus.service
    rm -rf /etc/warp-plus
    systemctl daemon-reload 2>/dev/null || true
fi

# ── 5. fail2ban (shared with naive/wg uninstalls) ───────────────────────────
if [[ "$FAIL2BAN" == "remove" ]]; then
    if dpkg -l fail2ban 2>/dev/null | grep -q '^ii'; then
        log "Purging fail2ban..."
        DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq fail2ban \
            || warn "apt purge fail2ban returned non-zero"
        rm -rf /etc/fail2ban
    fi
else
    rm -f /etc/fail2ban/jail.d/pitun-sshd.local 2>/dev/null || true
    info "fail2ban kept (use --remove-fail2ban to also remove it)"
fi

# ── 6. apt autoremove ──────────────────────────────────────────────────────
log "Running apt autoremove..."
DEBIAN_FRONTEND=noninteractive apt-get autoremove -y -qq >/dev/null 2>&1 || true

cat <<DONE

================================================================
 x-ui uninstall complete.
================================================================

  x-ui binary:    $([[ -e /usr/local/x-ui/x-ui ]] && echo 'STILL PRESENT (?)' || echo 'removed')
  x-ui unit:      $(systemctl list-unit-files 2>/dev/null | grep -q '^x-ui\.service' && echo 'present' || echo 'removed')
  /etc/x-ui:      $([[ -d /etc/x-ui ]] && echo 'present' || echo 'removed')
  /var/www/html:  $(find /var/www/html -mindepth 1 -maxdepth 1 2>/dev/null | wc -l) file(s)
$([[ "$INSTALLED_MODE" == "xui-pro" ]] && cat <<EOF
  nginx:          $(dpkg -l nginx 2>/dev/null | grep -q '^ii' && echo 'still installed' || echo 'not installed')
  certbot:        $(dpkg -l certbot 2>/dev/null | grep -q '^ii' && echo 'still installed' || echo 'not installed')
  tor:            $(dpkg -l tor 2>/dev/null | grep -q '^ii' && echo 'still installed' || echo 'not installed')
EOF
)
  fail2ban:       $(dpkg -l fail2ban 2>/dev/null | grep -q '^ii' && echo 'installed' || echo 'not installed')

You can now re-run setup-xui-server.sh from a clean slate.
================================================================
DONE
