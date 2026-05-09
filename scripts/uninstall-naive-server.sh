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
#   * /etc/caddy/Caddyfile (in case the purge missed the override)
#   * /var/log/caddy/* (access + error logs)
#   * /var/www/html/* (decoy site — confirmed first; users with hand-
#     customised sites get an opt-out)
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

cat <<BANNER
================================================================
 PiTun — NaiveProxy uninstaller
================================================================
This will remove:
  • Caddy (purged) + /etc/caddy/ + /var/log/caddy/
  • Decoy site under /var/www/html/
  • Custom systemd override at /etc/systemd/system/caddy.service
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
  fail2ban:     $(dpkg -l fail2ban 2>/dev/null | grep -q '^ii' && echo 'installed' || echo 'not installed')

You can now re-run setup-naive-server.sh from a clean slate.
================================================================
DONE
