#!/bin/bash
# ============================================================================
# PiTun — NaiveProxy Server Setup (Caddy + forwardproxy on a VPS)
# ============================================================================
# Deploys a NaiveProxy-compatible server: Caddy built with klzgrad's
# forwardproxy plugin, auto-issued Let's Encrypt cert, systemd unit.
#
# Requirements:
#   - Fresh Debian 12+ or Ubuntu 22.04+ VPS (root access)
#   - A DNS A-record pointing <domain> → this VPS's public IP
#   - Ports 80 and 443 reachable from the internet
#
# Usage:
#   sudo bash setup-naive-server.sh
#   # or non-interactive:
#   sudo DOMAIN=proxy.example.com EMAIL=me@example.com \
#        NAIVE_USER=myuser NAIVE_PASS=mysecret \
#        bash setup-naive-server.sh
#
# Optional decoy site override (anyone without proxy auth sees this):
#   DECOY_REPO=<git URL>   — clone any static site repo into /var/www/html
#                            default: https://github.com/daleharvey/pacman
#   DECOY_REPO=none        — keep a minimal "It works" stub
#
# Optional SSH hardening (asked interactively if none set):
#   HARDEN_SSH=yes|no            — enable/skip
#   SSH_PORT=<num>               — new SSH port (default 2222)
#   SSH_DISABLE_PASSWORD=yes|no  — disable password auth (default no — keep password login)
#   SSH_DISABLE_ROOT_PW=yes|no   — PermitRootLogin prohibit-password (default no)
#   SSH_KEEP_22=yes              — also keep listening on :22 as a safety net
#
# Optional fail2ban (asked interactively if none set, default yes):
#   INSTALL_FAIL2BAN=yes|no      — install fail2ban with sshd jail (5 fails → 1h ban)
#
# On success, prints the naive+https:// URI for import into PiTun.
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

[[ $EUID -ne 0 ]] && err "Run as root: sudo bash $0"

# ── 0. Detect OS ────────────────────────────────────────────────────────────
if ! command -v apt-get >/dev/null; then
    err "This script supports Debian/Ubuntu only (apt-get not found)."
fi

if [[ ! -f /etc/os-release ]]; then
    err "/etc/os-release missing — can't detect OS version"
fi
# shellcheck disable=SC1091
. /etc/os-release
OS_ID="${ID:-unknown}"
OS_VER="${VERSION_ID:-0}"
# Strip patch component: 22.04 → 22, 12.5 → 12
OS_MAJOR="${OS_VER%%.*}"

case "$OS_ID" in
    debian)
        if (( OS_MAJOR < 12 )); then
            err "Debian $OS_VER is unsupported — this script requires Debian 12 (bookworm) or newer"
        fi
        ;;
    ubuntu)
        # Ubuntu versioning keeps the ".04": major = year
        if (( OS_MAJOR < 22 )); then
            err "Ubuntu $OS_VER is unsupported — this script requires Ubuntu 22.04 or newer"
        fi
        ;;
    *)
        warn "OS is '$OS_ID' (not debian/ubuntu). Script is untested here — proceeding anyway."
        ;;
esac
info "Detected: $PRETTY_NAME"

# ── 1. Collect configuration ────────────────────────────────────────────────
prompt() {
    local var_name="$1" label="$2" default="${3:-}" silent="${4:-}"
    local current="${!var_name:-}"
    if [[ -n "$current" ]]; then
        info "$label: ${silent:+***}${silent:-$current}"
        return
    fi
    local value
    if [[ -n "$silent" ]]; then
        read -r -s -p "$label${default:+ [$default]}: " value; echo
    else
        read -r -p "$label${default:+ [$default]}: " value
    fi
    value="${value:-$default}"
    [[ -z "$value" ]] && err "$label is required"
    printf -v "$var_name" '%s' "$value"
}

log "NaiveProxy server setup"
echo

prompt DOMAIN     "Domain (must point to this VPS)"
prompt EMAIL      "Email for Let's Encrypt"
prompt NAIVE_USER "Proxy username" "naive"
# generate default password if empty
DEFAULT_PASS="$(head -c 18 /dev/urandom | base64 | tr -d '+/=' | head -c 24)"
prompt NAIVE_PASS "Proxy password" "$DEFAULT_PASS" silent

# ── SSH hardening (optional) ────────────────────────────────────────────────
# Controlled by env vars OR interactive y/N prompt:
#   HARDEN_SSH=yes|no          — enable/skip (default: ask)
#   SSH_PORT=<num>             — new port (default: 2222)
#   SSH_DISABLE_PASSWORD=yes   — disallow password auth, keys only (default: yes if HARDEN_SSH=yes)
#   SSH_DISABLE_ROOT_PW=yes    — force PermitRootLogin=prohibit-password (default: yes)
if [[ -z "${HARDEN_SSH:-}" ]]; then
    echo
    read -r -p "Harden SSH now? (move port + disable password auth) [y/N]: " _h
    [[ "${_h:-N}" =~ ^[yY]$ ]] && HARDEN_SSH=yes || HARDEN_SSH=no
fi

if [[ "$HARDEN_SSH" == "yes" ]]; then
    SSH_PORT="${SSH_PORT:-2222}"
    if ! [[ "$SSH_PORT" =~ ^[0-9]+$ ]] || (( SSH_PORT < 1 || SSH_PORT > 65535 )); then
        err "SSH_PORT '$SSH_PORT' is not a valid port number"
    fi
    if (( SSH_PORT == 80 || SSH_PORT == 443 )); then
        err "SSH_PORT=$SSH_PORT collides with Caddy (80/443). Pick another."
    fi
    # Password auth stays ENABLED by default — flip to "yes" explicitly if
    # you have keys set up and want keys-only.
    SSH_DISABLE_PASSWORD="${SSH_DISABLE_PASSWORD:-no}"
    SSH_DISABLE_ROOT_PW="${SSH_DISABLE_ROOT_PW:-no}"

    # Safety check: if user explicitly asked to disable password auth, make
    # sure root has a working authorized_keys file — otherwise the next
    # login would fail and the VPS turns into a brick.
    if [[ "$SSH_DISABLE_PASSWORD" == "yes" ]] && [[ ! -s /root/.ssh/authorized_keys ]]; then
        err "SSH_DISABLE_PASSWORD=yes but /root/.ssh/authorized_keys is missing or empty.
        Add your public key first:  ssh-copy-id -p 22 root@<ip>   OR
        set SSH_DISABLE_PASSWORD=no"
    fi
    info "SSH hardening: port $SSH_PORT, password-auth=${SSH_DISABLE_PASSWORD} (disabled?), root-pw=${SSH_DISABLE_ROOT_PW} (disabled?)"
fi

# ── fail2ban (optional but recommended) ────────────────────────────────────
if [[ -z "${INSTALL_FAIL2BAN:-}" ]]; then
    read -r -p "Install fail2ban for SSH brute-force protection? [Y/n]: " _f2b
    [[ "${_f2b:-Y}" =~ ^[nN]$ ]] && INSTALL_FAIL2BAN=no || INSTALL_FAIL2BAN=yes
fi

# Validate domain (rudimentary)
if [[ ! "$DOMAIN" =~ ^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$ ]]; then
    err "Domain '$DOMAIN' doesn't look valid"
fi

# ── 2. Install dependencies ─────────────────────────────────────────────────
# Install curl + friends BEFORE any network probe — a minimal Debian 13 cloud
# image (Contabo, Hetzner Cloud, some OVH images) ships without curl.
log "Installing base packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq curl ca-certificates ufw debian-keyring debian-archive-keyring apt-transport-https gnupg lsb-release

# DNS check — now that curl is available. `getent hosts` is the fallback
# path if we can't reach api.ipify.org (no internet egress for some
# reason); in that case we skip the public-IP comparison silently.
info "Checking DNS → this host..."
PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
RESOLVED_IP="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
if [[ -n "$PUBLIC_IP" && -n "$RESOLVED_IP" && "$PUBLIC_IP" != "$RESOLVED_IP" ]]; then
    warn "$DOMAIN resolves to $RESOLVED_IP but this host is $PUBLIC_IP"
    warn "TLS certificate issuance will fail unless the A-record is correct."
    read -r -p "Continue anyway? [y/N]: " cont
    [[ "${cont:-N}" =~ ^[yY]$ ]] || exit 1
fi

# ── 3. Install xcaddy ──────────────────────────────────────────────────────
# Pull the prebuilt xcaddy binary straight from GitHub releases — the old
# Cloudsmith apt repo (`dl.cloudsmith.io/.../debian.deb.txt`) has a moving
# URL scheme and has returned a malformed source-list entry in at least
# one run (Debian 13 / amd64 / Apr 2026). GitHub releases are stable and
# don't need an apt source at all.
if ! command -v xcaddy >/dev/null; then
    XCADDY_VERSION="0.4.2"
    case "$(uname -m)" in
        x86_64)  XCADDY_ARCH=amd64 ;;
        aarch64) XCADDY_ARCH=arm64 ;;
        armv7l)  XCADDY_ARCH=armv7 ;;
        *) err "Unsupported arch for xcaddy: $(uname -m)" ;;
    esac
    log "Installing xcaddy v${XCADDY_VERSION} (${XCADDY_ARCH})..."
    curl -fsSL \
        "https://github.com/caddyserver/xcaddy/releases/download/v${XCADDY_VERSION}/xcaddy_${XCADDY_VERSION}_linux_${XCADDY_ARCH}.tar.gz" \
        -o /tmp/xcaddy.tar.gz
    tar -xzf /tmp/xcaddy.tar.gz -C /tmp xcaddy
    install -m 755 /tmp/xcaddy /usr/local/bin/xcaddy
    rm -f /tmp/xcaddy.tar.gz /tmp/xcaddy
fi

# ── 4. Install Go (required by xcaddy) ──────────────────────────────────────
if ! command -v go >/dev/null || ! go version | grep -qE 'go1\.(2[1-9]|[3-9][0-9])'; then
    log "Installing Go 1.22..."
    GO_VERSION="1.22.5"
    ARCH="$(dpkg --print-architecture)"
    case "$ARCH" in
        amd64) GO_ARCH="amd64" ;;
        arm64) GO_ARCH="arm64" ;;
        armhf) GO_ARCH="armv6l" ;;
        *) err "Unsupported architecture: $ARCH" ;;
    esac
    curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-${GO_ARCH}.tar.gz" -o /tmp/go.tar.gz
    rm -rf /usr/local/go
    tar -C /usr/local -xzf /tmp/go.tar.gz
    rm /tmp/go.tar.gz
    ln -sf /usr/local/go/bin/go /usr/local/bin/go
fi

# ── 5. Build Caddy with klzgrad's forwardproxy plugin ───────────────────────
CADDY_BIN=/usr/local/bin/caddy
log "Building Caddy with klzgrad/forwardproxy@naive..."
cd /tmp
xcaddy build \
    --with github.com/caddyserver/forwardproxy@caddy2=github.com/klzgrad/forwardproxy@naive \
    --output "$CADDY_BIN"
chmod +x "$CADDY_BIN"
info "Caddy built: $($CADDY_BIN version)"

# ── 6. Create caddy user + directories ──────────────────────────────────────
if ! id caddy >/dev/null 2>&1; then
    groupadd --system caddy
    useradd --system --gid caddy --create-home --home-dir /var/lib/caddy \
        --shell /usr/sbin/nologin --comment "Caddy web server" caddy
fi
mkdir -p /etc/caddy /var/log/caddy
chown -R caddy:caddy /var/log/caddy /var/lib/caddy

# ── 7. Write Caddyfile ──────────────────────────────────────────────────────
# klzgrad/forwardproxy@naive uses PLAINTEXT basic_auth (it does NOT support
# bcrypt/hashed passwords like Caddy's standard basicauth directive).
# The Caddyfile is chmod 640 root:caddy so it's not world-readable.

# Escape special Caddyfile characters in the password. Caddyfile tokens are
# whitespace-separated and quotes are stripped — we quote the pass and
# escape any embedded double quotes.
ESC_PASS="${NAIVE_PASS//\"/\\\"}"

cat >/etc/caddy/Caddyfile <<EOF
{
    email $EMAIL
    # Silence the admin endpoint (not needed for this deployment).
    # NB: `admin off` disables caddy-reload via API, so systemctl reload
    # will fail; the unit (step 10) uses ExecReload that falls back to
    # restart. Use \`systemctl restart caddy\` when you edit this file.
    admin off

    # Required for klzgrad/forwardproxy@naive (it uses a v2 directive
    # that isn't in Caddy's default ordering).
    order forward_proxy before file_server
}

# Two site matchers on one block:
#   :443                    — catches CONNECT / absolute-URI requests
#                              whose Host header is the TARGET (not this
#                              server's domain). Without this, Caddy's
#                              host-based routing sends those requests to
#                              the default handler and forward_proxy is
#                              never invoked — the client sees a 200 with
#                              empty body and every naive CONNECT fails
#                              with "TLS record overflow".
#   $DOMAIN                 — normal visits to the decoy site (browsers
#                              hitting https://$DOMAIN directly), plus ACME
#                              HTTP-01 challenge requests.
# The cert is still issued via the explicit domain — the \`:443\`
# matcher reuses it.
:443, $DOMAIN {
    tls $EMAIL

    forward_proxy {
        basic_auth $NAIVE_USER "$ESC_PASS"
        hide_ip
        hide_via
        # probe_resistance is REQUIRED, not optional — without it
        # forward_proxy returns 407 "Proxy Authentication Required"
        # for EVERY request lacking Proxy-Authorization, including
        # ordinary browser GETs for the decoy page (your Pacman /
        # minimal stub). With it, non-proxy requests are silently
        # passed to the next handler (file_server), so the decoy
        # site is visible to random visitors while the forward-proxy
        # is still available for authenticated naive clients.
        probe_resistance
    }

    # Serve a plausible-looking site for anyone else visiting the domain.
    # This makes the endpoint indistinguishable from a static site.
    file_server {
        root /var/www/html
    }

    log {
        output file /var/log/caddy/access.log {
            roll_size 10mb
            roll_keep 3
        }
        format json
    }
}
EOF
chown root:caddy /etc/caddy/Caddyfile
chmod 640 /etc/caddy/Caddyfile

# ── Decoy site ──────────────────────────────────────────────────────────────
# The decoy is what non-authenticated visitors see — it must look like a real
# website. By default we clone daleharvey/pacman (a pure-static HTML5 Pac-Man
# game): small (~2 MB), recognisable, diverse asset mix (html+css+js+mp3).
# Override by exporting DECOY_REPO=<git URL> before running the script, or
# set DECOY_REPO="none" to keep a minimal stub.
DECOY_REPO="${DECOY_REPO:-https://github.com/daleharvey/pacman}"

mkdir -p /var/www/html
# Only replace the decoy if the directory is empty or contains just our stub
# (avoid clobbering an intentionally customised site on re-runs).
DECOY_EXISTING="$(find /var/www/html -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)"
if [[ "$DECOY_EXISTING" -eq 0 ]] || \
   ([[ "$DECOY_EXISTING" -eq 1 ]] && [[ -f /var/www/html/index.html ]] && \
    grep -q "This is the default page" /var/www/html/index.html 2>/dev/null); then

    if [[ "$DECOY_REPO" == "none" ]]; then
        log "Writing minimal decoy stub (DECOY_REPO=none)..."
        cat >/var/www/html/index.html <<'HTML'
<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>It works</title>
<style>body{font-family:system-ui;margin:4em auto;max-width:40em;padding:0 1em;color:#333}</style>
</head>
<body><h1>It works!</h1><p>This is the default page.</p></body>
</html>
HTML
    else
        log "Cloning decoy site from $DECOY_REPO ..."
        apt-get install -y -qq git
        TMP_DECOY="$(mktemp -d)"
        if git clone --depth=1 "$DECOY_REPO" "$TMP_DECOY" 2>&1; then
            rm -rf /var/www/html/*
            # Copy everything except the .git directory
            find "$TMP_DECOY" -mindepth 1 -maxdepth 1 ! -name '.git' \
                -exec cp -r {} /var/www/html/ \;
            info "Decoy installed from $DECOY_REPO"
        else
            warn "Failed to clone decoy repo — falling back to stub"
            cat >/var/www/html/index.html <<'HTML'
<!DOCTYPE html><html><head><meta charset="utf-8"><title>It works</title></head>
<body><h1>It works!</h1></body></html>
HTML
        fi
        rm -rf "$TMP_DECOY"
    fi

    # robots.txt — sanitiser-friendly; real sites have it
    if [[ ! -f /var/www/html/robots.txt ]]; then
        cat >/var/www/html/robots.txt <<'ROBOTS'
User-agent: *
Allow: /
ROBOTS
    fi

    # Minimal favicon — 1x1 transparent PNG wrapped as ICO. Scanners very
    # often request /favicon.ico; a 200 here is more plausible than 404.
    if [[ ! -f /var/www/html/favicon.ico ]]; then
        base64 -d > /var/www/html/favicon.ico <<'FAVICON'
AAABAAEAEBAAAAEAIABoBAAAFgAAACgAAAAQAAAAIAAAAAEAIAAAAAAAQAQAAAAAAAAAAAAAAAAAAAAA
AAD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP///wD///8A////AP//
/wA=
FAVICON
    fi

    chown -R caddy:caddy /var/www/html
else
    info "Keeping existing /var/www/html contents ($DECOY_EXISTING entries)"
fi

# ── 8. systemd unit ─────────────────────────────────────────────────────────
cat >/etc/systemd/system/caddy.service <<'EOF'
[Unit]
Description=Caddy (naive forward-proxy)
Documentation=https://caddyserver.com/docs/
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
User=caddy
Group=caddy
ExecStart=/usr/local/bin/caddy run --environ --config /etc/caddy/Caddyfile
ExecReload=/usr/local/bin/caddy reload --config /etc/caddy/Caddyfile --force
TimeoutStopSec=5s
LimitNOFILE=1048576
PrivateTmp=true
ProtectSystem=full
AmbientCapabilities=CAP_NET_BIND_SERVICE

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

# ── 9. Firewall ─────────────────────────────────────────────────────────────
if command -v ufw >/dev/null; then
    log "Configuring firewall (ufw)..."
    ufw allow 22/tcp   >/dev/null || true
    ufw allow 80/tcp   >/dev/null || true
    ufw allow 443/tcp  >/dev/null || true
    # Pre-open the new SSH port BEFORE we restart sshd so we can't lock
    # ourselves out.
    if [[ "${HARDEN_SSH:-no}" == "yes" ]] && [[ "$SSH_PORT" != "22" ]]; then
        ufw allow "${SSH_PORT}/tcp" >/dev/null || true
    fi
    ufw --force enable >/dev/null || true
fi

# ── 9b. SSH hardening (optional) ───────────────────────────────────────────
# Apply AFTER ufw is up (so the new port is reachable) and BEFORE Caddy is
# started — if hardening breaks sshd, we haven't yet mutated production state.
if [[ "${HARDEN_SSH:-no}" == "yes" ]]; then
    log "Hardening SSH (port $SSH_PORT, password=$SSH_DISABLE_PASSWORD)..."
    SSHD_DROPIN=/etc/ssh/sshd_config.d/99-pitun-naive.conf
    mkdir -p /etc/ssh/sshd_config.d
    {
        echo "# Written by setup-naive-server.sh — $(date -Iseconds)"
        echo "Port $SSH_PORT"
        # Keep :22 listening as a fallback during the switch if user asked to
        # keep the old port open. They can remove it manually after confirming
        # the new port works.
        if [[ "${SSH_KEEP_22:-no}" == "yes" ]]; then
            echo "Port 22"
        fi
        if [[ "$SSH_DISABLE_PASSWORD" == "yes" ]]; then
            echo "PasswordAuthentication no"
            echo "KbdInteractiveAuthentication no"
            echo "ChallengeResponseAuthentication no"
            echo "UsePAM yes"
            echo "PubkeyAuthentication yes"
        fi
        if [[ "$SSH_DISABLE_ROOT_PW" == "yes" ]]; then
            echo "PermitRootLogin prohibit-password"
        fi
    } > "$SSHD_DROPIN"
    chmod 644 "$SSHD_DROPIN"

    # Validate config before reloading. If this fails, revert and abort so we
    # don't leave sshd in a broken state after reload.
    if ! sshd -t 2>/dev/null; then
        warn "sshd config validation failed — reverting SSH changes"
        rm -f "$SSHD_DROPIN"
        sshd -t || true
    else
        # Two sshd modes coexist on modern Debian/Ubuntu:
        #   1) socket-activated: ssh.socket listens on port, spawns ssh@.service
        #      per connection. In this mode `Port` in sshd_config is IGNORED;
        #      port is set via the socket unit's ListenStream=.
        #   2) standalone ssh.service: sshd binds directly. `Port` is authoritative.
        # Pick the right mechanism based on which unit is currently active.
        # Also: `systemctl reload ssh` does NOT re-bind the listening socket
        # (SIGHUP only re-reads config) — we need `restart` for port to change.
        if systemctl is-active --quiet ssh.socket; then
            info "sshd is socket-activated — patching ssh.socket.d/override.conf"
            mkdir -p /etc/systemd/system/ssh.socket.d
            {
                echo "# Written by setup-naive-server.sh — $(date -Iseconds)"
                echo "[Socket]"
                echo "ListenStream="               # reset default :22
                if [[ "${SSH_KEEP_22:-no}" == "yes" ]]; then
                    echo "ListenStream=22"
                fi
                echo "ListenStream=$SSH_PORT"
            } > /etc/systemd/system/ssh.socket.d/override.conf
            systemctl daemon-reload
            systemctl restart ssh.socket || warn "ssh.socket restart failed"
        else
            # Standalone ssh.service — `restart`, not `reload`, so the
            # listening socket actually picks up the new Port directive
            # from sshd_config.d.
            info "sshd is standalone — restarting ssh.service"
            # Clean up any leftover socket override from a previous run on
            # the same host (e.g. user switched mode, or we wrote one
            # earlier and it's now interfering).
            rm -f /etc/systemd/system/ssh.socket.d/override.conf
            systemctl daemon-reload
            systemctl restart ssh.service 2>/dev/null \
                || systemctl restart sshd.service 2>/dev/null \
                || warn "Could not restart sshd — check manually: systemctl status ssh"
        fi
        info "SSH now listening on port $SSH_PORT"
        warn "BEFORE you close this session: open a NEW terminal and verify:"
        warn "    ssh -p $SSH_PORT root@<ip>"
        warn "If that works, this session is safe to close. Port 22 will remain"
        warn "open in ufw until you run: ufw delete allow 22/tcp"
    fi
fi

# ── 9c. fail2ban (optional) ────────────────────────────────────────────────
if [[ "${INSTALL_FAIL2BAN:-no}" == "yes" ]]; then
    log "Installing fail2ban..."
    apt-get install -y -qq fail2ban

    # sshd jail — use the (possibly new) SSH port. backend=systemd reads from
    # journalctl which works regardless of whether /var/log/auth.log exists
    # (Debian 12 / Ubuntu 22.04+ ship with journal-only logging).
    F2B_SSH_PORT="${SSH_PORT:-22}"

    cat >/etc/fail2ban/jail.d/pitun-sshd.local <<EOF
[DEFAULT]
# Ban for 1 hour after 5 failures within 10 minutes.
bantime  = 1h
findtime = 10m
maxretry = 5
backend  = systemd

[sshd]
enabled  = true
port     = ${F2B_SSH_PORT}$( [[ "${SSH_KEEP_22:-no}" == "yes" && "$F2B_SSH_PORT" != "22" ]] && printf ",22" )
EOF

    systemctl enable fail2ban >/dev/null
    systemctl restart fail2ban
    sleep 1
    if systemctl is-active --quiet fail2ban; then
        info "fail2ban: sshd jail active (port ${F2B_SSH_PORT}, 5 failures → 1h ban)"
    else
        warn "fail2ban did not start — check 'journalctl -u fail2ban'"
    fi
fi

# ── 10. Start service ───────────────────────────────────────────────────────
log "Starting caddy service..."
systemctl enable caddy >/dev/null
systemctl restart caddy

# Wait for TLS cert (up to 60s)
log "Waiting for TLS certificate..."
SUCCESS=0
for i in {1..30}; do
    sleep 2
    if curl -fsS --max-time 5 -o /dev/null "https://$DOMAIN/" 2>/dev/null; then
        SUCCESS=1
        break
    fi
done

echo
if [[ $SUCCESS -eq 1 ]]; then
    log "TLS handshake OK — server is reachable at https://$DOMAIN/"
else
    warn "Could not verify HTTPS in 60s. Check 'journalctl -u caddy -n 100' for details."
fi

# ── 11. Print import URI ────────────────────────────────────────────────────
# URL-encode user and pass for the URI
urlencode() {
    python3 -c "import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1], safe=''))" "$1" 2>/dev/null || \
    printf '%s' "$1" | sed -e 's/%/%25/g' -e 's/ /%20/g' -e 's/@/%40/g' -e 's/:/%3A/g' -e 's|/|%2F|g' -e 's/?/%3F/g' -e 's/#/%23/g' -e 's/&/%26/g'
}
ENC_USER="$(urlencode "$NAIVE_USER")"
ENC_PASS="$(urlencode "$NAIVE_PASS")"
NAIVE_URI="naive+https://${ENC_USER}:${ENC_PASS}@${DOMAIN}:443/?padding=1#${DOMAIN}"

echo
echo "════════════════════════════════════════════════════════════════════"
echo -e "${GREEN}  NaiveProxy server is ready${NC}"
echo "════════════════════════════════════════════════════════════════════"
echo
echo -e "  Domain:    ${BLUE}$DOMAIN${NC}"
echo -e "  User:      ${BLUE}$NAIVE_USER${NC}"
echo -e "  Password:  ${BLUE}$NAIVE_PASS${NC}"
if [[ "${HARDEN_SSH:-no}" == "yes" ]]; then
    echo -e "  SSH port:  ${BLUE}${SSH_PORT}${NC}  ${YELLOW}(verify in a NEW session before closing this one!)${NC}"
fi
echo
echo "  Import URI (paste into PiTun → Nodes → Import):"
echo
echo -e "    ${YELLOW}$NAIVE_URI${NC}"
echo
echo "  Useful commands:"
echo "    systemctl status caddy"
echo "    journalctl -u caddy -f"
echo "    tail -f /var/log/caddy/access.log"
if [[ "${INSTALL_FAIL2BAN:-no}" == "yes" ]]; then
    echo "    fail2ban-client status sshd    # view bans"
    echo "    fail2ban-client unban <ip>     # unban an IP"
fi
echo "════════════════════════════════════════════════════════════════════"
