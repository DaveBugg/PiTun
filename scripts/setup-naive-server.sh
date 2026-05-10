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
#   TEMPLATE_LOCAL_ARCHIVE=<path>
#                          — extract a SFTP'd zip into /var/www/html/.
#                            Used for user-uploaded custom templates
#                            (PiTun UI ships /tmp/pitun-template.zip
#                            via the deploy SSH session). Highest
#                            priority — beats both TEMPLATE_HTML_URL
#                            and DECOY_REPO.
#   TEMPLATE_HTML_URL=<URL> — curl a single-file HTML to /var/www/html/
#                             index.html (no apt/git overhead). Used by
#                             the PiTun UI's built-in template gallery
#                             (since v1.3.0-beta.6); takes precedence
#                             over DECOY_REPO when both are set.
#   DECOY_REPO=<git URL>   — clone any static site repo into /var/www/html
#                            default: https://github.com/daleharvey/pacman
#   DECOY_REPO=none        — keep a minimal "It works" stub
#   DECOY_REPO_PINNED_COMMIT=<sha>
#                          — after cloning DECOY_REPO, `git checkout <sha>`
#                            so the served decoy is byte-deterministic
#                            and immune to the upstream repo gaining
#                            malicious commits later. Used by the UI's
#                            built-in gallery to pin known-good states
#                            of well-known projects (2048, tetris, etc.).
#                            Empty / unset = follow upstream HEAD.
#   FORCE_DECOY=yes        — replace /var/www/html on every run, even when
#                            it already contains a non-stub site. Set
#                            automatically by the script when an explicit
#                            TEMPLATE_HTML_URL is passed (which means the
#                            user picked it via the UI and wants it
#                            applied), so re-running the installer with a
#                            different template actually swaps the cover.
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
# Optional PHP support for dynamic decoys (since v1.3.0-beta.6):
#   INSTALL_PHP=yes|no           — install php-fpm with a hardened config
#                                  so `*.php` files in /var/www/html are
#                                  served via FastCGI. Used when the user
#                                  picked a decoy template that needs
#                                  server-side rendering (e.g. fake 2FA
#                                  page that shows real Network-tab POST
#                                  + 401 + Set-Cookie behaviour). Default
#                                  no — keeps the install fully static.
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

# ── PHP support (optional; off by default) ────────────────────────────────
# Off-by-default because (a) static decoys are sufficient for most threat
# models and (b) PHP is a real attack surface. When PiTun's UI picks a
# template that needs server-side rendering (fake-2fa etc.), it sets
# INSTALL_PHP=yes and we install php-fpm with a heavily-hardened config —
# disable_functions wide net, no DB drivers, no network from inside php
# (allow_url_*=Off + fsockopen disabled), open_basedir jail.
INSTALL_PHP="${INSTALL_PHP:-no}"

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

# ── 2b. PHP-FPM install + version detection (optional) ────────────────────
# Split from the full PHP setup at step 9d because the Caddyfile (step 7)
# needs to know PHP_FPM_SOCK when rendering the `php_fastcgi` directive.
# Hardened config + service start happen after Caddyfile is in place.
PHP_VER=""
PHP_FPM_SOCK=""
if [[ "${INSTALL_PHP:-no}" == "yes" ]]; then
    log "Installing php-fpm (will be hardened in step 9d)..."
    # No DB drivers (php-mysql / php-pgsql) — keeps SQL exfil
    # vectors off the table for vulnerable user-uploaded PHP.
    apt-get install -y -qq php-fpm php-cli
    PHP_VER="$(php -r 'echo PHP_MAJOR_VERSION.".".PHP_MINOR_VERSION;' 2>/dev/null \
              || dpkg-query -W -f='${Package}\n' 'php*-fpm' 2>/dev/null \
                  | head -n1 | sed -E 's/php([0-9]+\.[0-9]+).*/\1/')"
    if [[ -z "$PHP_VER" ]]; then
        err "Could not detect installed PHP version after apt install"
    fi
    PHP_FPM_SOCK="/run/php/php${PHP_VER}-fpm.sock"
    info "PHP detected: ${PHP_VER} (socket: ${PHP_FPM_SOCK})"
fi

# DNS check — now that curl is available. `getent hosts` is the fallback
# path if we can't reach api.ipify.org (no internet egress for some
# reason); in that case we skip the public-IP comparison silently.
info "Checking DNS → this host..."
PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
RESOLVED_IP="$(getent hosts "$DOMAIN" | awk '{print $1}' | head -1 || true)"
if [[ -n "$PUBLIC_IP" && -n "$RESOLVED_IP" && "$PUBLIC_IP" != "$RESOLVED_IP" ]]; then
    warn "$DOMAIN resolves to $RESOLVED_IP but this host is $PUBLIC_IP"
    warn "TLS certificate issuance will fail unless the A-record is correct."
    if [[ "${PITUN_AUTO_CONTINUE:-}" == "yes" ]]; then
        warn "PITUN_AUTO_CONTINUE=yes — proceeding anyway (Let's Encrypt may still fail)."
    else
        read -r -p "Continue anyway? [y/N]: " cont
        [[ "${cont:-N}" =~ ^[yY]$ ]] || exit 1
    fi
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

# Many small VPS images (Hetzner CX11, Contabo VPS S, etc.) mount /tmp
# as tmpfs sized at ~50% of RAM. With 1 GB RAM that's ~480 MB — and
# `xcaddy build` pulls 300+ Go modules whose intermediate `*.a` /
# `importcfg` files easily exceed 1 GB. Without redirection the build
# fails halfway with `no space left on device` writing to /tmp/go-build*
# (observed during v1.3.0-beta.1 smoke testing). Use disk-backed
# /var/tmp instead — Go honours TMPDIR for its build scratch dir.
BUILD_TMP="$(mktemp -d -p /var/tmp xcaddy-build.XXXXXX)"
chmod 0700 "$BUILD_TMP"
trap 'rm -rf "$BUILD_TMP"' RETURN  # best-effort cleanup if scope returns
export TMPDIR="$BUILD_TMP"
export GOTMPDIR="$BUILD_TMP"
info "Using $BUILD_TMP for Go build scratch (avoids small-tmpfs OOM)."

cd "$BUILD_TMP"
xcaddy build \
    --with github.com/caddyserver/forwardproxy@caddy2=github.com/klzgrad/forwardproxy@naive \
    --output "$CADDY_BIN"
chmod +x "$CADDY_BIN"
info "Caddy built: $($CADDY_BIN version)"

# Done with the big scratch dir — release the disk space.
unset TMPDIR GOTMPDIR
rm -rf "$BUILD_TMP"
trap - RETURN
cd /tmp

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

# When PHP is enabled, inject a `php_fastcgi` directive into the site
# block so *.php paths get FastCGI'd to php-fpm. The hardened php-fpm
# install above (step 9d) listens on the unix socket we computed
# there; if INSTALL_PHP=no, the socket variable stays unset and the
# Caddyfile keeps its pre-PHP layout.
#
# `php_fastcgi`'s try_files matcher uses the site-level `root`
# directive, so we also emit `root * /var/www/html` when PHP is on
# (file_server already had its own inline root, which still works).
PHP_DIRECTIVE_LINE=""
SITE_ROOT_LINE=""
if [[ "${INSTALL_PHP:-no}" == "yes" ]]; then
    PHP_DIRECTIVE_LINE="    php_fastcgi unix/${PHP_FPM_SOCK}"
    SITE_ROOT_LINE="    root * /var/www/html"
fi

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
$SITE_ROOT_LINE

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

$PHP_DIRECTIVE_LINE
    # Serve a plausible-looking site for anyone else visiting the domain.
    # This makes the endpoint indistinguishable from a static site.
    # When PHP is enabled (line above), php_fastcgi takes precedence
    # for *.php paths via Caddy's standard directive ordering.
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
# Track whether the user passed an explicit decoy choice via env vs.
# fell through to our default — if they explicitly chose, force-swap
# even on re-runs so the UI's "pick a different template" flow
# actually changes anything visible. Without this guard, the
# original "don't clobber custom content" check would skip the swap
# because /var/www/html already contains the previous decoy.
EXPLICIT_DECOY=0
if [[ -n "${TEMPLATE_HTML_URL:-}" ]] \
   || [[ -n "${DECOY_REPO:-}" ]] \
   || [[ -n "${TEMPLATE_LOCAL_ARCHIVE:-}" ]]; then
    EXPLICIT_DECOY=1
fi
DECOY_REPO="${DECOY_REPO:-https://github.com/daleharvey/pacman}"
DECOY_REPO_PINNED_COMMIT="${DECOY_REPO_PINNED_COMMIT:-}"
TEMPLATE_HTML_URL="${TEMPLATE_HTML_URL:-}"
TEMPLATE_LOCAL_ARCHIVE="${TEMPLATE_LOCAL_ARCHIVE:-}"
FORCE_DECOY="${FORCE_DECOY:-no}"
# Auto-force when the user picked ANY decoy explicitly through the
# UI (single-file template OR a non-default git repo). Either way
# the intent is unambiguous: "apply this cover now". Without this
# auto-force, switching from Corporate → Pac-Man via the UI would
# leave the previous corporate.html in place because /var/www/html
# is already non-empty.
if [[ "$EXPLICIT_DECOY" == "1" ]]; then
    FORCE_DECOY=yes
fi

mkdir -p /var/www/html
# Replace the decoy if any of:
#   - the directory is empty (fresh install)
#   - it contains only our minimal "It works" stub (script's own
#     fallback that no real user would intentionally keep)
#   - FORCE_DECOY=yes (explicit re-run with a new template)
# Otherwise leave it alone — preserves an intentionally-customised
# site across re-runs of the install script.
DECOY_EXISTING="$(find /var/www/html -mindepth 1 -maxdepth 1 2>/dev/null | wc -l)"
if [[ "$FORCE_DECOY" == "yes" ]] || \
   [[ "$DECOY_EXISTING" -eq 0 ]] || \
   ([[ "$DECOY_EXISTING" -eq 1 ]] && [[ -f /var/www/html/index.html ]] && \
    grep -q "This is the default page" /var/www/html/index.html 2>/dev/null); then

    # Highest-priority path (since v1.3.0-beta.6): user-uploaded
    # custom template, SFTP'd to /tmp/pitun-template.zip by the
    # deploy SSH session. Beats both TEMPLATE_HTML_URL and
    # DECOY_REPO — the user explicitly picked their own archive.
    # Falls through to the cheaper alternatives if the archive is
    # missing or unzip fails.
    TEMPLATE_LOCAL_INSTALLED=0
    if [[ -n "$TEMPLATE_LOCAL_ARCHIVE" ]] && [[ -f "$TEMPLATE_LOCAL_ARCHIVE" ]]; then
        log "Extracting custom template from $TEMPLATE_LOCAL_ARCHIVE ..."
        apt-get install -y -qq unzip
        TMP_EXTRACT="$(mktemp -d)"
        if unzip -q "$TEMPLATE_LOCAL_ARCHIVE" -d "$TMP_EXTRACT"; then
            rm -rf /var/www/html/*
            # If the archive's top level contains a single dir (the
            # common "myproject/" wrapper from `git archive` etc.),
            # promote its contents up; otherwise copy as-is.
            top_count=$(find "$TMP_EXTRACT" -mindepth 1 -maxdepth 1 | wc -l)
            top_dir=$(find "$TMP_EXTRACT" -mindepth 1 -maxdepth 1 -type d | head -n1)
            if [[ "$top_count" -eq 1 ]] && [[ -n "$top_dir" ]] && [[ -f "$top_dir/index.html" ]]; then
                cp -r "$top_dir/." /var/www/html/
            else
                cp -r "$TMP_EXTRACT/." /var/www/html/
            fi
            info "Custom decoy installed (zip: $(basename "$TEMPLATE_LOCAL_ARCHIVE"))"
            TEMPLATE_LOCAL_INSTALLED=1
            rm -f "$TEMPLATE_LOCAL_ARCHIVE"  # cleanup the SFTP'd zip
        else
            warn "Failed to unzip $TEMPLATE_LOCAL_ARCHIVE — falling back"
        fi
        rm -rf "$TMP_EXTRACT"
    fi
    # Cheaper path: single-file template via curl. No apt install,
    # deterministic, ~10 KB. Used by the built-in gallery for
    # corporate / blog / docs / maintenance covers.
    if [[ "$TEMPLATE_LOCAL_INSTALLED" != "1" ]] && [[ -n "$TEMPLATE_HTML_URL" ]]; then
        log "Fetching single-file decoy template from $TEMPLATE_HTML_URL ..."
        TMP_HTML="$(mktemp)"
        if curl -fsSL --max-time 30 -o "$TMP_HTML" "$TEMPLATE_HTML_URL"; then
            # Sanity-check: must look like HTML (not a 404 page that
            # snuck past `-f`, not an empty file from a CDN race).
            if [[ -s "$TMP_HTML" ]] && head -c 64 "$TMP_HTML" | grep -qiE '<!doctype|<html'; then
                rm -rf /var/www/html/*
                install -m 0644 "$TMP_HTML" /var/www/html/index.html
                info "Decoy installed from $TEMPLATE_HTML_URL"
                rm -f "$TMP_HTML"
                # Skip the DECOY_REPO branch below — done.
                TEMPLATE_HTML_INSTALLED=1
            else
                warn "Downloaded file doesn't look like HTML — falling back to DECOY_REPO"
                rm -f "$TMP_HTML"
            fi
        else
            warn "Failed to fetch $TEMPLATE_HTML_URL — falling back to DECOY_REPO"
            rm -f "$TMP_HTML"
        fi
    fi

    if [[ "$TEMPLATE_LOCAL_INSTALLED" != "1" ]] && [[ "${TEMPLATE_HTML_INSTALLED:-0}" != "1" ]]; then
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
        # When pinning to a SHA, do a full (non-shallow) clone — git
        # can't shallow-clone an arbitrary commit unless the remote
        # has uploadpack.allowReachableSHA1InWant turned on, which
        # GitHub does NOT enable by default for repos. Full clone is
        # one-time per install and the repos in our gallery are tiny
        # (~2-5 MB), so this is fine.
        CLONE_OK=0
        if [[ -n "$DECOY_REPO_PINNED_COMMIT" ]]; then
            if git clone "$DECOY_REPO" "$TMP_DECOY" 2>&1 \
               && (cd "$TMP_DECOY" && git checkout --quiet "$DECOY_REPO_PINNED_COMMIT" 2>&1); then
                info "Pinned decoy to commit $DECOY_REPO_PINNED_COMMIT"
                CLONE_OK=1
            fi
        else
            if git clone --depth=1 "$DECOY_REPO" "$TMP_DECOY" 2>&1; then
                CLONE_OK=1
            fi
        fi
        if [[ "$CLONE_OK" == "1" ]]; then
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

# ── 9d. PHP-FPM (optional, hardened) ───────────────────────────────────────
# Installed only when the deploy explicitly requested PHP via
# INSTALL_PHP=yes (PiTun UI sets this when the user picked a template
# whose `requires_php` flag is set, e.g. fake-2fa). Heavily hardened so
# a malicious / vulnerable user-uploaded PHP file can't escape the jail:
#   * disable_functions: every shell-spawning + dynamic-eval + outbound-
#     network primitive we don't need on a static-feel decoy
#   * allow_url_fopen / allow_url_include: Off — blocks SSRF + RFI
#   * open_basedir: jails reads/writes to /var/www/html + /tmp/pitun-php
#   * No DB drivers (php-mysql / php-pgsql NOT installed) — no SQL exfil
#   * php-fpm runs as the `caddy` user (no shell, no login) — no creds
#     hop into a different user's home
#
# These together leave PHP useful for fake-auth-page state (sessions,
# random sleeps, prog. error messages) but functionally inert for
# attacker-style RCE / SSRF / data-exfil.
if [[ "${INSTALL_PHP:-no}" == "yes" ]]; then
    log "Hardening php-fpm config..."
    # PHP_VER + PHP_FPM_SOCK were computed at step 2b (so the
    # Caddyfile in step 7 could embed the socket path). Re-check
    # that detection succeeded; abort with a clear message if
    # something exotic happened (php-fpm package gone, etc.).
    if [[ -z "$PHP_VER" ]]; then
        err "PHP_VER unset — step 2b detection failed; cannot harden"
    fi

    # Hardened override that ships AFTER the distro defaults so it
    # wins. `99-` prefix puts it last in the conf.d alphabetical
    # sort that PHP-FPM loads.
    HARDEN_INI="/etc/php/${PHP_VER}/fpm/conf.d/99-pitun-decoy.ini"
    cat >"$HARDEN_INI" <<'PHPINI'
; PiTun: hardened PHP for decoy-only use. See setup-naive-server.sh
; for the rationale on each toggle. Don't bypass without thinking.

; Block every commonly-abused function. The list looks long but each
; entry has a known exploit path on a misconfigured app:
;   exec/passthru/shell_exec/system/proc_open/popen   — RCE
;   pcntl_exec                                        — RCE
;   eval/assert/create_function/show_source            — code disclosure / dynamic eval
;   curl_exec/curl_multi_exec/fsockopen/pfsockopen/
;     stream_socket_client                            — SSRF / outbound exfil
;   parse_ini_file/parse_ini_string                   — read /etc/passwd-style configs
;   ini_set/error_reporting                           — disable defenses at runtime
;   putenv/dl/phpinfo                                 — env leakage / module load / fingerprint
disable_functions = exec,passthru,shell_exec,system,proc_open,popen,proc_get_status,proc_nice,proc_terminate,pcntl_exec,show_source,eval,create_function,assert,curl_exec,curl_multi_exec,fsockopen,pfsockopen,stream_socket_client,parse_ini_file,parse_ini_string,putenv,dl,phpinfo

; Network egress from PHP — Off blocks both `include 'http://...';`
; (RFI) and `file_get_contents('http://attacker/')` (SSRF).
allow_url_fopen   = Off
allow_url_include = Off

; Filesystem jail — PHP can only read/write /var/www/html (the decoy
; site) plus /tmp/pitun-php (session save dir). /etc/passwd, /root/*,
; /var/log/* etc. are off-limits.
open_basedir = /var/www/html:/tmp/pitun-php

; Don't leak the exact PHP version in `Server:` / `X-Powered-By`.
; A determined fingerprinter can still tell PHP-FPM is there from
; behavioural cues, but no need to advertise the patch level.
expose_php = Off

; Sessions live in the jail too (default is /var/lib/php/sessions
; which is outside open_basedir). gc_probability=1/100 keeps the
; dir from filling up.
session.save_path     = /tmp/pitun-php
session.gc_probability = 1
session.gc_divisor     = 100

; Modest upload caps — decoy POST bodies are tiny.
post_max_size       = 1M
upload_max_filesize = 1M
max_execution_time  = 5
max_input_time      = 5
memory_limit        = 32M
PHPINI

    # Session save dir, owned by the caddy user that php-fpm runs as.
    mkdir -p /tmp/pitun-php
    chown caddy:caddy /tmp/pitun-php
    chmod 700 /tmp/pitun-php

    # Run php-fpm under the same `caddy` user that owns the docroot,
    # so file_get_contents on user content + session writes JustWork.
    # The default `www-data` user lives in its own /var/www but that's
    # not how this Caddy install lays out perms.
    POOL_CONF="/etc/php/${PHP_VER}/fpm/pool.d/www.conf"
    if [[ -f "$POOL_CONF" ]]; then
        sed -i 's/^user *= *.*/user = caddy/'   "$POOL_CONF"
        sed -i 's/^group *= *.*/group = caddy/' "$POOL_CONF"
        sed -i 's|^listen.owner *= *.*|listen.owner = caddy|' "$POOL_CONF"
        sed -i 's|^listen.group *= *.*|listen.group = caddy|' "$POOL_CONF"
    else
        warn "$POOL_CONF not found — php-fpm will run as default www-data"
    fi

    systemctl enable "php${PHP_VER}-fpm" >/dev/null
    systemctl restart "php${PHP_VER}-fpm"
    if systemctl is-active --quiet "php${PHP_VER}-fpm"; then
        info "php-fpm: ${PHP_VER} active (hardened, jailed to /var/www/html + /tmp/pitun-php)"
    else
        err "php${PHP_VER}-fpm failed to start — check 'journalctl -u php${PHP_VER}-fpm'"
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

# ── Machine-readable contract ────────────────────────────────────────────────
# A single deterministic line at end-of-output for PiTun's auto-deploy
# pipeline (since v1.3.0): when this script is invoked over SSH from
# `core/ssh.py.exec_remote_script()`, the backend parses stdout for
# `URI=…` and inserts a Node row from the captured value. The `>&1` is
# explicit so it survives even if the caller redirects stderr.
#
# Format: `URI=<uri>` on a line by itself, no surrounding whitespace,
# no ANSI codes (note: NAIVE_URI was rendered without color escapes).
# Keep this LAST in stdout so the parser can scan from end-of-stream.
echo "URI=${NAIVE_URI}" >&1
