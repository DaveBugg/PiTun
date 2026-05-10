#!/usr/bin/env bash
# ============================================================================
# PiTun — NaiveProxy server uninstaller
# ============================================================================
# Symmetric to `setup-naive-server.sh`. Reverses everything that script
# installs, so a re-run of the installer afterwards starts from a clean
# slate. Use case: testing template / config changes by re-deploying
# from PiTun's UI without leaving stale Caddy state behind.
#
# What this removes:
#   * Caddy package (purge — drops /etc/caddy along with state files)
#   * /usr/local/bin/caddy (the xcaddy-built binary the installer drops
#     when forwardproxy@naive isn't in the apt build — `apt purge caddy`
#     misses this on Debian since cloudsmith's package doesn't include
#     forward_proxy and the installer always re-builds)
#   * /etc/caddy/Caddyfile (in case the purge missed the override)
#   * /var/log/caddy/* (access + error logs)
#   * /var/www/html/* (decoy site — confirmed first; users with hand-
#     customised sites get an opt-out)
#   * php-fpm + php-cli + /etc/php/*/fpm/conf.d/99-pitun-decoy.ini
#     ONLY if our hardening drop-in is present, which means *we*
#     installed php-fpm via INSTALL_PHP=yes. If the drop-in is missing,
#     PHP predates us and we leave it alone.
#   * fail2ban package (only if --remove-fail2ban / FAIL2BAN=remove
#     is set — by default we leave it installed since it's a generic
#     SSH-protection package, not naive-specific)
#   * /etc/systemd/system/caddy.service (custom override, if present)
#
# What this DOES NOT touch:
#   * SSH hardening (/etc/ssh/sshd_config tweaks the install script
#     applies on demand): irreversible without state tracking. If the
#     user accepted hardening, they need to revert manually.
#   * UFW rules: only opened-by-us rules would be safe to close, but
#     we don't track which were ours vs. pre-existing — leave alone.
#   * Other apt packages we co-opted (curl, gnupg, ca-certificates):
#     standard system tooling, almost certainly used by other things.
#
# Usage:
#   sudo bash uninstall-naive-server.sh                # interactive
#   sudo YES=1 bash uninstall-naive-server.sh          # non-interactive
#   sudo YES=1 FAIL2BAN=remove bash uninstall-naive-server.sh
#
# Or one-liner over SSH:
#   curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/scripts/uninstall-naive-server.sh \
#     | sudo bash
# (interactive — drop the `| sudo bash` and `| sudo YES=1 bash` for
#  non-interactive flag.)
#
# Re-run safe: every step checks state first; running on an
# already-clean system is a no-op except for "already gone" log lines.
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
FAIL2BAN="${FAIL2BAN:-keep}"   # keep | remove

# Allow simple flag form too (--yes, --remove-fail2ban) so users
# don't have to remember the env-var names. shift past consumed
# args; unknown args fail fast to surface typos.
while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes|-y)            YES=1 ;;
        --remove-fail2ban)   FAIL2BAN=remove ;;
        --keep-fail2ban)     FAIL2BAN=keep ;;
        -h|--help)
            sed -n '2,40p' "$0"
            exit 0
            ;;
        *) err "Unknown argument: $1" ;;
    esac
    shift
done

# Detect whether *we* installed PHP. The setup script's hardening
# drop-in is unique to PiTun, so its presence means we own the
# php-fpm install and can safely purge it. Absence means PHP either
# predates us or was wired up by another tool — leave it alone.
PITUN_PHP_INI="$(ls /etc/php/*/fpm/conf.d/99-pitun-decoy.ini 2>/dev/null | head -n1 || true)"
PHP_REMOVAL="kept (was not installed by PiTun)"
if [[ -n "$PITUN_PHP_INI" ]]; then
    PHP_REMOVAL="purged (php-fpm + php-cli + hardening drop-in)"
fi

cat <<BANNER
================================================================
 PiTun — NaiveProxy uninstaller
================================================================
This will remove:
  • Caddy (purged) + /etc/caddy/ + /var/log/caddy/
  • /usr/local/bin/caddy (xcaddy build, if present)
  • Decoy site under /var/www/html/
  • Custom systemd override at /etc/systemd/system/caddy.service
  • PHP: $PHP_REMOVAL
  • $([[ "$FAIL2BAN" == "remove" ]] && echo 'fail2ban (purged)' || echo 'fail2ban (kept — pass --remove-fail2ban to also remove)')

NOT removed (manual revert needed if applied):
  • SSH hardening tweaks in /etc/ssh/sshd_config
  • UFW firewall rules

================================================================
BANNER

if [[ "$YES" != "1" ]]; then
    read -r -p "Proceed? [y/N]: " _ans
    [[ "${_ans:-N}" =~ ^[yY]$ ]] || { info "Aborted."; exit 0; }
fi

# ── 1. Stop & disable Caddy ────────────────────────────────────────────────
if systemctl list-unit-files 2>/dev/null | grep -q '^caddy\.service'; then
    log "Stopping caddy.service…"
    systemctl stop caddy 2>/dev/null || true
    systemctl disable caddy 2>/dev/null || true
else
    info "caddy.service not registered — skipping stop/disable"
fi

# Custom systemd override the installer drops at /etc/systemd/system/
# (overrides the package's own unit so reload picks ours up first).
if [[ -f /etc/systemd/system/caddy.service ]]; then
    log "Removing /etc/systemd/system/caddy.service override…"
    rm -f /etc/systemd/system/caddy.service
    systemctl daemon-reload || true
fi

# ── 2. Purge Caddy package ─────────────────────────────────────────────────
if dpkg -l caddy 2>/dev/null | grep -q '^ii'; then
    log "Purging caddy package…"
    DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq caddy || \
        warn "apt purge caddy returned non-zero; continuing."
    # Defensive: the apt repo for cloudsmith was added by the installer.
    # Leaving the .list file behind doesn't hurt (apt won't fail) but
    # makes the repo resurface on `apt update`. Users can manually
    # delete /etc/apt/sources.list.d/caddy-stable.list — we don't
    # touch it because some distributions have other Caddy variants
    # installed via the same repo.
else
    info "caddy package not installed — skipping purge"
fi

# ── 2b. xcaddy-built binary at /usr/local/bin/caddy ────────────────────────
# The setup script always rebuilds Caddy from source via xcaddy because
# the cloudsmith packages don't ship klzgrad/forwardproxy@naive. The
# resulting binary lands in /usr/local/bin and is NOT covered by
# `apt purge caddy` above. Without this step, `command -v caddy` keeps
# resolving and the final summary reads "STILL PRESENT (?)".
if [[ -f /usr/local/bin/caddy ]]; then
    log "Removing /usr/local/bin/caddy (xcaddy build)…"
    rm -f /usr/local/bin/caddy
fi

# ── 3. Drop config + log directories ───────────────────────────────────────
# Purge above usually clears /etc/caddy on Debian/Ubuntu, but some
# layouts keep the dir around for "rc-state" reasons. Belt-and-braces:
for d in /etc/caddy /var/log/caddy /var/lib/caddy; do
    if [[ -d "$d" ]]; then
        log "Removing $d/"
        rm -rf "$d"
    fi
done

# ── 4. Decoy site ──────────────────────────────────────────────────────────
if [[ -d /var/www/html ]] && [[ -n "$(ls -A /var/www/html 2>/dev/null)" ]]; then
    log "Clearing decoy site under /var/www/html/"
    rm -rf /var/www/html/*  /var/www/html/.[!.]* /var/www/html/..?* 2>/dev/null || true
    # Don't rmdir /var/www/html itself — other webserver setups may
    # rely on the directory existing (apache2 default vhost, etc.).
fi

# ── 4b. PHP (only if we installed it) ──────────────────────────────────────
# `$PITUN_PHP_INI` was probed in the banner section. If it's set, we
# know PiTun was the one to install php-fpm via INSTALL_PHP=yes — purge
# the packages and the hardening drop-in. If unset, PHP predates us
# (or is wired up by another tool) and we leave the system alone.
if [[ -n "$PITUN_PHP_INI" ]]; then
    log "Removing PiTun's php-fpm hardening drop-in: $PITUN_PHP_INI"
    rm -f "$PITUN_PHP_INI"

    # Purge the php-fpm + php-cli packages we installed. The wildcard
    # form `php*-fpm` covers whatever PHP_VER apt picked (8.2 / 8.3 /
    # 8.4 etc.) without us having to re-detect here. Idempotent: apt
    # treats absent packages as success in this mode.
    log "Purging php-fpm + php-cli packages…"
    DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq \
        'php*-fpm' 'php*-cli' php-fpm php-cli || \
        warn "apt purge php-fpm returned non-zero; continuing."

    # The php-fpm pool conf and our /tmp/pitun-php scratch dir aren't
    # needed once the package is gone.
    rm -rf /tmp/pitun-php

    # Remove now-empty /etc/php tree if package purge left it behind.
    [[ -d /etc/php ]] && rmdir --ignore-fail-on-non-empty -p /etc/php 2>/dev/null || true
else
    info "php-fpm not installed by PiTun — leaving alone"
fi

# ── 5. fail2ban (optional) ─────────────────────────────────────────────────
if [[ "$FAIL2BAN" == "remove" ]]; then
    if dpkg -l fail2ban 2>/dev/null | grep -q '^ii'; then
        log "Purging fail2ban package…"
        DEBIAN_FRONTEND=noninteractive apt-get purge -y -qq fail2ban || \
            warn "apt purge fail2ban returned non-zero; continuing."
        rm -rf /etc/fail2ban
    else
        info "fail2ban not installed — skipping"
    fi
else
    info "fail2ban kept (use --remove-fail2ban to also remove it)"
fi

# ── 6. caddy user/group cleanup ────────────────────────────────────────────
# The package's postrm should handle this on purge, but a stale
# `caddy` user occasionally lingers if the package was force-removed
# at some point. Clean up so a future re-install doesn't trip on
# UID conflicts.
if getent passwd caddy >/dev/null 2>&1; then
    log "Removing leftover 'caddy' system user…"
    deluser --system caddy 2>/dev/null || userdel caddy 2>/dev/null || true
fi
if getent group caddy >/dev/null 2>&1; then
    delgroup --only-if-empty caddy 2>/dev/null || groupdel caddy 2>/dev/null || true
fi

# ── 7. apt cleanup ─────────────────────────────────────────────────────────
log "Running apt autoremove…"
DEBIAN_FRONTEND=noninteractive apt-get autoremove -y -qq >/dev/null 2>&1 || true

cat <<DONE

================================================================
 NaiveProxy server uninstall complete.
================================================================

  Caddy:        $(command -v caddy >/dev/null && echo 'STILL PRESENT (?)' || echo 'removed')
  Caddy unit:   $(systemctl list-unit-files 2>/dev/null | grep -q '^caddy\.service' && echo 'present' || echo 'removed')
  /etc/caddy:   $([[ -d /etc/caddy ]] && echo 'present' || echo 'removed')
  /var/www/html contents: $(find /var/www/html -mindepth 1 -maxdepth 1 2>/dev/null | wc -l) file(s)
  php-fpm:      $(command -v php-fpm8.4 >/dev/null 2>&1 || command -v php-fpm8.3 >/dev/null 2>&1 || command -v php-fpm8.2 >/dev/null 2>&1 || command -v php-fpm >/dev/null 2>&1 && echo 'still installed' || echo 'not installed')
  fail2ban:     $(dpkg -l fail2ban 2>/dev/null | grep -q '^ii' && echo 'installed' || echo 'not installed')

You can now re-run setup-naive-server.sh from a clean slate.
================================================================
DONE
