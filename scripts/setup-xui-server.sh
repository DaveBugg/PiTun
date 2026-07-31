#!/bin/bash
# ============================================================================
# PiTun — x-ui-pro / 3x-ui server installer (since v1.3.0-beta.7)
# ============================================================================
# Deploys an Xray-core panel with the PiTun-compatible API surface, in one
# of two modes determined by whether DOMAIN is set:
#
#   * DOMAIN set    → full x-ui-pro stack (nginx + Let's Encrypt + 170-template
#                     random fakesite + Cloudflare-IPs + Tor/WARP outbounds).
#                     The panel itself runs on a random high port behind nginx;
#                     visitors to the apex domain see the fakesite.
#   * DOMAIN empty  → bare upstream MHSanaei/3x-ui at a pinned tag. No nginx,
#                     no Let's Encrypt; panel served directly on its random
#                     port with self-signed/HTTP. Use this for Reality-only
#                     setups where the SNI masquerade is the only cover and
#                     a real domain pointed at the VPS isn't needed.
#
# Both modes pin the SAME upstream 3x-ui release tag (XUI_VERSION below) so
# the API surface (`/panel/api/inbounds/...`, `/panel/api/clients/...`, Bearer
# auth, apiTokens endpoints) stays identical regardless of which path was used.
# Pinning matters because 3x-ui's `master` branch is a moving target — a
# major-version drift between PiTun's API client and a freshly-installed
# panel would silently break inbound CRUD.
#
# Requirements:
#   * Fresh Debian 12+ / Ubuntu 22.04+ VPS, root
#   * For DOMAIN mode: DNS A-record pointing <domain> → this VPS; ports 80/443
#     reachable from the internet (Let's Encrypt HTTP-01 challenge)
#
# Required env:
#   (nothing — DOMAIN is optional)
#
# Optional env:
#   DOMAIN=<sub.domain.tld>   See above. Empty triggers bare-3x-ui mode.
#   EMAIL=<addr>              Let's Encrypt registration email (DOMAIN mode
#                             only). Defaults to admin@<DOMAIN> if missing.
#   XUI_VERSION=v3.6.0        Pinned 3x-ui release tag. Both modes use this.
#                             The API surface PiTun uses is identical across
#                             v3.1.0–v3.6.0 (verified additive-only), so PiTun
#                             manages panels on any tag in that range. Panels
#                             older than v3.1.0 are NOT manageable (per-client
#                             endpoints live in /panel/api/clients/* since
#                             v3.1.0).
#
# ── Why upstream scripts are pinned by commit SHA, not tag ──────────────────
# Both upstream installer URLs below resolve to a **commit SHA**, not a
# branch/tag. GitHub permits tag force-pushes and `master` is a moving
# target by definition — if either upstream silently rewrites their
# installer (intentionally or via repo takeover), our pinned URL still
# fetches the byte-identical script we audited against XUI_VERSION
# v3.1.0. Bump XUI_VERSION + the two `*_SHA` constants together when
# updating to a new 3x-ui release.
#
# To bump:
#   1. Set XUI_VERSION=v3.X.Y below.
#   2. Update XUI_INSTALL_SHA to the commit at tag v3.X.Y:
#        curl -sf https://api.github.com/repos/MHSanaei/3x-ui/git/refs/tags/v3.X.Y
#      → take .object.sha, then dereference annotated tag:
#        curl -sf https://api.github.com/repos/MHSanaei/3x-ui/git/tags/<above>
#      → take .object.sha (commit). That's XUI_INSTALL_SHA.
#   3. Update XUI_PRO_SHA to current x-ui-pro master HEAD:
#        curl -sf https://api.github.com/repos/GFW4Fun/x-ui-pro/branches/master
#      → take .commit.sha.
#   4. Verify content didn't drift unexpectedly:
#        curl -sf https://raw.githubusercontent.com/MHSanaei/3x-ui/v3.X.Y/install.sh | sha256sum
#        curl -sf https://raw.githubusercontent.com/MHSanaei/3x-ui/<XUI_INSTALL_SHA>/install.sh | sha256sum
#      → must match.
#   5. Recompute XUI_INSTALL_SHA256 / XUI_PRO_SHA256 from the fetched
#      scripts (`sha256sum`) — fetch_pinned refuses to run on mismatch.
#   PANEL_PORT=<num>          Force the panel port instead of generating one.
#                             Empty (default) → random 30000-60000.
#   PANEL_BASEPATH=<str>      Force the panel base path. Empty → random
#                             16-char slug. Leading/trailing slashes are
#                             normalised by the script.
#   PANEL_USER=<str>          Panel admin username (random 10-char if empty).
#   PANEL_PASS=<str>          Panel admin password (random 18-char if empty).
#   SSH_PORT=<num>            Move SSH listener to this port (1-65535, empty
#                             or 22 = no-op). Mirrors setup-naive-server.sh.
#   SSH_KEEP_22=yes           Keep :22 as a safety fallback during cutover.
#   INSTALL_FAIL2BAN=yes|no   Install fail2ban with sshd jail. Default yes.
#   OPTIMIZE=yes|no           Apply VPS tuning for high-conn-count proxy
#                             workloads (BBR + sysctl TCP buffers + swap +
#                             ulimits + logrotate). Default yes — x-ui is
#                             always a proxy, not a static-content host,
#                             so the defaults matter. Set `no` to skip if
#                             the VPS is already tuned by your provisioner.
#   PITUN_AUTO_CONTINUE=yes   Skip the soft-warning interactive prompts;
#                             non-interactive deploys (PiTun UI) set this.
#
# On success, prints `URI=xui://<api_token>@<host>:<port><basepath>?...` for
# import into PiTun → Servers. The URI carries:
#   * api_token — Bearer token for `/panel/api/*` calls
#   * user / pass — for human-visible panel login (URL-encoded)
#   * domain — empty in bare mode, set in x-ui-pro mode
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

# ── Defaults ────────────────────────────────────────────────────────────────
DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-}"
XUI_VERSION="${XUI_VERSION:-v3.6.0}"
# Commit SHAs of the upstream installer scripts at our pinned version.
# These freeze the downloaded scripts byte-for-byte regardless of any
# future tag rewrite or master-branch drift. See the bump procedure in
# the header comment above.
XUI_INSTALL_SHA="${XUI_INSTALL_SHA:-c377dca27c23549cdf84e0ffd2d287a16bee577c}"  # MHSanaei/3x-ui@v3.6.0 install.sh
XUI_PRO_SHA="${XUI_PRO_SHA:-2d8f916526a092358b504903e01c1b3c5329293a}"        # GFW4Fun/x-ui-pro master @ 2026-05-26, re-verified 2026-07-30
# Content hashes of those two scripts. The commit-SHA URL already freezes
# the ref; the sha256 additionally catches tampering between the raw host
# and us. fetch_pinned() enforces these.
XUI_INSTALL_SHA256="${XUI_INSTALL_SHA256:-7bb41e811f2107a3182da9090f24893d3612b5b6310194a7dd1f9965ff29e0c8}"
XUI_PRO_SHA256="${XUI_PRO_SHA256:-a88f6e4f3ceb29307ca3196fe13aaf09a2b5c4dac78fa84976179945992ec710}"
PANEL_PORT="${PANEL_PORT:-}"
PANEL_BASEPATH="${PANEL_BASEPATH:-}"
PANEL_USER="${PANEL_USER:-}"
PANEL_PASS="${PANEL_PASS:-}"
INSTALL_FAIL2BAN="${INSTALL_FAIL2BAN:-yes}"
OPTIMIZE="${OPTIMIZE:-yes}"
PITUN_AUTO_CONTINUE="${PITUN_AUTO_CONTINUE:-no}"

# Random URL-safe string. `openssl rand -hex` is the simplest portable
# generator that doesn't pipe — `tr -dc | head -c` under `set -o
# pipefail` is broken: head's early exit gives tr a SIGPIPE → 141,
# pipefail propagates, the original `||` fallback fired, and we got
# TWICE the requested chars concatenated (16 → 32, breaking
# basepath length). hex is URL-safe by default.
rand_str() {
    local n="${1:-10}"
    # Each hex byte is 2 chars, so request ceil(n/2) bytes then trim.
    local bytes=$(( (n + 1) / 2 ))
    local s
    s=$(openssl rand -hex "$bytes")
    printf '%s' "${s:0:n}"
}

# Download a pinned upstream script and verify its content hash before
# anything executes it.
fetch_pinned() {  # <url> <sha256> <outfile>
    wget -qO "$3" "$1" || err "download failed: $1"
    echo "$2  $3" | sha256sum -c - >/dev/null 2>&1 \
        || err "sha256 mismatch for $1 — upstream content drifted, refusing to run it"
}

# Default randomisation matches x-ui-pro's own gen_str ranges (8-12 chars
# user/pass, 16 chars basepath). PORT range 30000-60000 stays inside
# what most VPS UFW rule defaults allow without extra tweaking.
[[ -z "$PANEL_USER" ]]     && PANEL_USER="$(rand_str 10)"
[[ -z "$PANEL_PASS" ]]     && PANEL_PASS="$(rand_str 18)"
[[ -z "$PANEL_BASEPATH" ]] && PANEL_BASEPATH="$(rand_str 16)"
if [[ -z "$PANEL_PORT" ]]; then
    # Avoid clashing with anything that's currently listening.
    while true; do
        PANEL_PORT=$(( RANDOM % 30000 + 30000 ))
        ss -tlnp 2>/dev/null | awk '{print $4}' | grep -q ":$PANEL_PORT\$" || break
    done
fi

# Normalise basepath: must start and end with `/`.
case "$PANEL_BASEPATH" in
    /*) ;;
    *)  PANEL_BASEPATH="/$PANEL_BASEPATH" ;;
esac
case "$PANEL_BASEPATH" in
    */) ;;
    *)  PANEL_BASEPATH="$PANEL_BASEPATH/" ;;
esac

INSTALL_MODE="bare"
[[ -n "$DOMAIN" ]] && INSTALL_MODE="xui-pro"

# ── Banner ──────────────────────────────────────────────────────────────────
echo
info "PiTun — x-ui server install"
info "  Mode:     $INSTALL_MODE"
info "  Pinned:   3x-ui $XUI_VERSION"
[[ -n "$DOMAIN" ]] && info "  Domain:   $DOMAIN"
info "  Panel:    :$PANEL_PORT${PANEL_BASEPATH}"
echo

# ── 1. 443-slot pre-flight (for both modes since both bind a web server) ────
# Bare 3x-ui doesn't bind 443 by default, but x-ui-pro does. We refuse to
# install when 443 is already taken by something other than nginx because
# the install will silently land non-functional otherwise. Naive's Caddy is
# the most likely conflict.
if [[ "$INSTALL_MODE" == "xui-pro" ]]; then
    if ss -tlnp 2>/dev/null | awk '$4 ~ /:443$/' | grep -q .; then
        # Distinguish a stale-our-own-nginx from a real conflict.
        owner="$(ss -tlnp 2>/dev/null | awk '$4 ~ /:443$/ {print $NF}' | head -n1)"
        if echo "$owner" | grep -qiE 'nginx|x-ui'; then
            warn ":443 is held by '$owner' — assuming a previous run, will stop it"
            systemctl stop nginx 2>/dev/null || true
            systemctl stop x-ui 2>/dev/null || true
        else
            err ":443 is already bound by '$owner'. Uninstall the other web server first (this is the naive/xui mutual-exclusion guard)."
        fi
    fi
fi

# ── 2. Base packages (shared between modes) ─────────────────────────────────
log "Installing base packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    curl wget jq sqlite3 openssl apache2-utils ca-certificates ufw unzip
# `unzip` is needed by x-ui-pro.sh's random-fakesite step (it downloads
# a ~268 MB master.zip from GFW4Fun/randomfakehtml and unzips it). The
# upstream script doesn't apt-install unzip itself, so without this line
# x-ui-pro mode dies at "unzip: command not found" half-way through.

# Pre-open the CURRENT SSH port BEFORE running any installer that might
# enable UFW with restrictive defaults (we've seen lock-outs when the
# upstream 3x-ui v3.0.0 installer + previous naive UFW state interact
# poorly — by the time the user's ssh session drops they're stuck on
# console-only access). Detect the live SSH port from sshd_config.d
# overrides; fall back to 22 if nothing is set.
#
# `|| true` everywhere because under `set -euo pipefail`:
#  * `cat /etc/ssh/sshd_config.d/*.conf` fails non-zero when the glob
#    doesn't expand (no .conf files), even with stderr suppressed;
#  * pipefail then propagates that 141/2 to the whole pipeline and
#    kills the script. Defending each step keeps the detection
#    best-effort with a safe fallback.
CURR_SSH_PORT=22
if compgen -G "/etc/ssh/sshd_config.d/*.conf" >/dev/null 2>&1; then
    p=$(awk '/^[[:space:]]*Port[[:space:]]+[0-9]+/ {print $2; exit}' \
            /etc/ssh/sshd_config.d/*.conf 2>/dev/null || true)
    [[ -n "$p" ]] && CURR_SSH_PORT="$p"
fi
if [[ "$CURR_SSH_PORT" == "22" ]] && [[ -f /etc/ssh/sshd_config ]]; then
    p=$(awk '/^[[:space:]]*Port[[:space:]]+[0-9]+/ {print $2; exit}' \
            /etc/ssh/sshd_config 2>/dev/null || true)
    [[ -n "$p" ]] && CURR_SSH_PORT="$p"
fi
ufw allow "${CURR_SSH_PORT}/tcp" >/dev/null 2>&1 || true
info "Pre-opened SSH port $CURR_SSH_PORT in UFW (lock-out guard)."

# ── 3. Branch: x-ui-pro vs bare 3x-ui ───────────────────────────────────────
XUIDB="/etc/x-ui/x-ui.db"

if [[ "$INSTALL_MODE" == "xui-pro" ]]; then
    # ── 3a. x-ui-pro mode ───────────────────────────────────────────────────
    # We feed XUI_VERSION as `-xuiver`. The upstream wrapper strips the `v`
    # and re-applies it for 3x-ui's release URL.
    #
    # NOTE: `-RandomTemplate y` is intentionally NOT passed here. That
    # flag is a *post-install* CLI mode in x-ui-pro.sh — it downloads
    # ~268 MB of fakesite templates, picks one, swaps it in, and then
    # explicitly `exit 1`s. Mixing it into the fresh-install args
    # therefore aborts the install right after extraction. PiTun can
    # call setup-xui-server.sh again with RANDOMIZE_FAKESITE=yes (TODO)
    # to invoke this mode separately.
    log "Running x-ui-pro installer (this takes 3-8 minutes)..."
    ver_for_pro="${XUI_VERSION#v}"
    # Pinned by commit SHA (XUI_PRO_SHA) rather than `/master/` so a
    # force-push or compromised commit on master can't silently
    # replace the wrapper. See header comment for the bump procedure.
    PRO_SH="$(mktemp)"
    fetch_pinned \
        "https://raw.githubusercontent.com/GFW4Fun/x-ui-pro/${XUI_PRO_SHA}/x-ui-pro.sh" \
        "$XUI_PRO_SHA256" "$PRO_SH"
    # The wrapper's inner 3x-ui install.sh call inherits these. v3.6.0+
    # install.sh is env-driven when stdin isn't a tty: keep the panel
    # plain-HTTP (we wire the LE cert into it ourselves in section 4b)
    # and sqlite-backed.
    export XUI_NONINTERACTIVE=1 XUI_SSL_MODE=none XUI_DB_TYPE=sqlite
    bash "$PRO_SH" \
        -panel 1 \
        -xuiver "$ver_for_pro" \
        -cdn off \
        -secure no \
        -country xx \
        -subdomain "$DOMAIN" \
        || err "x-ui-pro installer exited non-zero"
    rm -f "$PRO_SH"
else
    # ── 3b. Bare upstream 3x-ui mode ────────────────────────────────────────
    log "Running upstream 3x-ui installer ($XUI_VERSION)..."
    # v3.6.0+ install.sh is env-driven in non-interactive mode:
    # XUI_SSL_MODE=none keeps the panel plain-HTTP (bare mode's
    # contract; step 4 below still clears any stray cert paths).
    # The version arg ("$XUI_VERSION") is passed positionally because
    # that's how install.sh composes the binary tarball download URL
    # (release-download/<tag>/x-ui-linux-<arch>.tar.gz). The SHA-pinned
    # URL + sha256 protect the SCRIPT; the positional arg pins the BINARY.
    INSTALL_SH="$(mktemp)"
    fetch_pinned \
        "https://raw.githubusercontent.com/MHSanaei/3x-ui/${XUI_INSTALL_SHA}/install.sh" \
        "$XUI_INSTALL_SHA256" "$INSTALL_SH"
    XUI_NONINTERACTIVE=1 XUI_SSL_MODE=none XUI_DB_TYPE=sqlite \
        bash "$INSTALL_SH" "$XUI_VERSION" \
        || err "3x-ui installer exited non-zero"
    rm -f "$INSTALL_SH"
fi

# Sanity-check the install landed.
[[ -x /usr/local/x-ui/x-ui ]] \
    || err "/usr/local/x-ui/x-ui not found after install — check installer output above"
[[ -f "$XUIDB" ]] \
    || err "$XUIDB not found after install — panel state didn't materialise"

# ── 4. Pin our random port/basepath/creds ───────────────────────────────────
log "Configuring panel credentials..."
systemctl stop x-ui >/dev/null 2>&1 || true
sleep 1

# `x-ui setting -username -password -port` is the supported way to reset
# auth + port; it writes via the panel's own code paths so we don't have
# to care about bcrypt-cost or DB-schema specifics. v3.0.0+ exposes
# `-port`, older releases don't and would need a fallback (see below).
/usr/local/x-ui/x-ui setting \
    -username "$PANEL_USER" \
    -password "$PANEL_PASS" \
    -port "$PANEL_PORT" \
    >/dev/null

# `webBasePath` isn't exposed by the CLI — only sqlite. The settings
# table on a fresh install has NO unique constraint on `key`, so
# INSERT OR REPLACE silently appends a second row instead of updating;
# x-ui then reads the FIRST one at startup and our value is ignored.
# Wipe-then-insert is the only safe pattern here. The same applies if
# someone re-runs setup with a different basepath.
sqlite3 "$XUIDB" <<SQL
DELETE FROM settings WHERE key = 'webBasePath';
INSERT INTO settings (key, value) VALUES ('webBasePath', '${PANEL_BASEPATH}');
SQL

# Safety net: if the upstream installer also inserted a duplicate webPort
# (it does in v3.0.0 because that's where the IP-cert init happens), the
# CLI's `-port` writes a second row in the same way. Collapse to one.
sqlite3 "$XUIDB" <<SQL
DELETE FROM settings WHERE key = 'webPort';
INSERT INTO settings (key, value) VALUES ('webPort', '${PANEL_PORT}');
SQL

# ── 4b. Wire the Let's Encrypt cert into the panel for HTTPS ────────────────
# x-ui-pro provisions a LE cert for the apex domain via certbot, but the
# panel itself (on its random high port) keeps serving plain HTTP unless
# we plumb the cert paths into x-ui's own settings. v3.0.1's `x-ui setting
# -webCert / -webCertKey` writes those settings so the panel reads them on
# startup.
#
# Why this matters for PiTun:
#  * The Bearer api_token goes in Authorization headers — HTTP exposes
#    it to anyone sniffing the LAN/internet path between PiTun and the
#    panel. HTTPS-with-real-cert closes that.
#  * 3x-ui's settings page nags about "Panel is served over plain HTTP"
#    when there's no cert configured. Annoying + correct.
#
# Renewal: certbot's deploy hook restarts x-ui so the panel picks up
# the new cert (x-ui caches paths, not contents, but restart is the
# clean signal).
if [[ "$INSTALL_MODE" == "xui-pro" ]]; then
    # Find the lineage directory under /etc/letsencrypt/live/ that holds a
    # cert covering $DOMAIN. certbot's lineage naming isn't always the
    # FQDN — when there's a pre-existing folder for any ancestor (or a
    # prior cert for the same subject), certbot will reuse that name.
    # E.g. requesting cert for `panel.example.com` may land in
    # `/etc/letsencrypt/live/example.com/`. We scan all lineages and
    # pick the one whose CN or SAN actually matches $DOMAIN.
    LE_CERT=""
    LE_KEY=""
    if [[ -d /etc/letsencrypt/live ]]; then
        for d in /etc/letsencrypt/live/*/; do
            [[ -f "${d}fullchain.pem" ]] || continue
            # Check both CN and SAN — certbot may store the subject under
            # CN= or via X509v3 SAN: DNS:...
            if openssl x509 -in "${d}fullchain.pem" -noout -ext subjectAltName 2>/dev/null \
                | grep -qiE "DNS:[[:space:]]*${DOMAIN}([[:space:]]|,|$)"; then
                LE_CERT="${d}fullchain.pem"
                LE_KEY="${d}privkey.pem"
                break
            fi
        done
    fi

    if [[ -n "$LE_CERT" ]] && [[ -f "$LE_CERT" ]] && [[ -f "$LE_KEY" ]]; then
        log "Wiring Let's Encrypt cert into x-ui panel..."
        # Note: this is the `cert` subcommand, NOT `setting`. Earlier
        # versions of this script used `setting -webCert` which silently
        # no-ops — the setting subcommand's flag list includes -webCert /
        # -webCertKey but the handler doesn't call updateCert. Only the
        # `cert` subcommand actually writes webCertFile / webKeyFile.
        /usr/local/x-ui/x-ui cert \
            -webCert "$LE_CERT" \
            -webCertKey "$LE_KEY" \
            >/dev/null
        # LE renewal hook so the panel picks up rotated certs.
        mkdir -p /etc/letsencrypt/renewal-hooks/deploy
        cat >/etc/letsencrypt/renewal-hooks/deploy/x-ui-reload.sh <<'XEOF'
#!/bin/bash
# Auto-installed by PiTun's setup-xui-server.sh — restarts the panel
# when certbot rotates the LE cert so x-ui re-reads the new bytes.
systemctl restart x-ui
XEOF
        chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/x-ui-reload.sh
        info "Panel HTTPS enabled (cert: $LE_CERT)"
    else
        warn "No LE cert covering '$DOMAIN' found under /etc/letsencrypt/live/."
        warn "Run 'certbot --nginx -d $DOMAIN' manually then re-deploy."
    fi
else
    # Bare mode → plain HTTP for the panel. Upstream 3x-ui v3.0.1
    # auto-generates a self-signed cert at install time + populates
    # webCertFile/webCertKey, so the panel comes up on https://. The
    # api_token is the actual auth, the panel listens on a high
    # random port that nothing else hits, and the user explicitly
    # opted into bare mode = "no nginx, no LE, keep it simple".
    # Clear the cert settings via sqlite (the `-webCert ""` CLI flag
    # is treated as "no change", not "clear"), so the panel falls
    # back to plain HTTP on next start.
    log "Bare mode → clearing self-signed cert settings (HTTP-only panel)..."
    sqlite3 "$XUIDB" <<SQL
DELETE FROM settings WHERE key IN ('webCertFile', 'webKeyFile');
INSERT INTO settings (key, value) VALUES ('webCertFile', '');
INSERT INTO settings (key, value) VALUES ('webKeyFile', '');
SQL
fi

systemctl start x-ui

# Two-stage readiness probe: (1) port bound, (2) HTTP layer responds.
# Stage 1 catches the moment Go's net.Listen returns, stage 2 catches the
# moment gin's router actually accepts requests — the gap between them
# can be 1-3 seconds on first start because the panel lazy-initialises
# self-signed cert + xray template + DB migrations.
for i in $(seq 1 40); do
    ss -tlnp 2>/dev/null | grep -q ":${PANEL_PORT}\\b" && break
    sleep 0.5
done
ss -tlnp 2>/dev/null | grep -q ":${PANEL_PORT}\\b" \
    || err "Panel didn't bind :$PANEL_PORT after 20s — check 'journalctl -u x-ui'"

# ── 5. Bootstrap the Bearer API token ───────────────────────────────────────
# Named-tokens model (since v3.1.0):
#   GET  <setting-base>/apiTokens          — list rows
#   POST <setting-base>/apiTokens/create   — body {name: "<str>"} → {token: ...}
# The setting controller's mount MOVED in v3.6.0: /panel/api/setting/*
# (Bearer-capable zone); v3.1.x serves it at /panel/setting/* only, and
# v3.6.0's SPA shell answers 200+HTML on the old path. We probe
# new-first and keep whichever returns JSON. Name is `uniqueIndex` on
# the ApiToken row, so re-running the script for the same panel finds
# the existing "pitun" row instead of creating a duplicate.
log "Bootstrapping Bearer API token..."

# Stable name we use to find our token across re-runs. Anything matching
# this name in the panel is assumed to be PiTun's; a 2nd PiTun install
# against the same panel would clobber it.
PITUN_TOKEN_NAME="pitun"

# Stage 2: wait for the HTTP layer + scheme detection. Bare 3x-ui defaults
# to HTTPS with self-signed; x-ui-pro's panel is HTTP (TLS terminates at
# nginx). Probe both, prefer https, accept any non-000 response code as
# proof of life.
PANEL_SCHEME=""
for i in $(seq 1 30); do
    for try in https http; do
        code=$(curl -sk --max-time 3 -o /dev/null -w "%{http_code}" \
            "${try}://127.0.0.1:${PANEL_PORT}${PANEL_BASEPATH}" 2>/dev/null || true)
        if [[ "$code" =~ ^(200|302|301|401|403|404)$ ]]; then
            PANEL_SCHEME="$try"
            break 2
        fi
    done
    sleep 0.5
done
[[ -n "$PANEL_SCHEME" ]] \
    || err "Panel didn't accept HTTP on :$PANEL_PORT after 15s — check 'journalctl -u x-ui'"

API_BASE="${PANEL_SCHEME}://127.0.0.1:${PANEL_PORT}${PANEL_BASEPATH%/}"
COOKIE="$(mktemp)"
trap 'rm -f "$COOKIE"' EXIT
info "Panel ready on ${PANEL_SCHEME}://127.0.0.1:${PANEL_PORT}${PANEL_BASEPATH}"

# CSRF dance — v3.0.0 added middleware on /login (and every other unsafe
# method) that rejects with 403 Forbidden + empty body unless an
# X-CSRF-Token header (or `_csrf` form field) matching the session-stored
# token is present. Bearer-token callers are exempt, but we don't HAVE
# a Bearer token until we've logged in once. So:
#   1. GET /csrf-token → seeds the session cookie + returns the token
#   2. POST /login with that token + the user/pass we just configured
# Earlier 3x-ui releases (pre-v3.0.0) didn't have CSRF on /login; if
# someone pins XUI_VERSION older the GET returns 404 and we fall back
# to the legacy header-less POST.
CSRF_TMP="$(mktemp)"
CSRF_CODE=$(curl -sk --max-time 10 -c "$COOKIE" -b "$COOKIE" \
    -o "$CSRF_TMP" -w "%{http_code}" \
    "${API_BASE}/csrf-token" 2>/dev/null || true)
CSRF_RESP="$(cat "$CSRF_TMP" 2>/dev/null || true)"
rm -f "$CSRF_TMP"
CSRF_TOKEN=""
if [[ "$CSRF_CODE" == "200" ]]; then
    CSRF_TOKEN=$(echo "$CSRF_RESP" | jq -r '.obj // empty' 2>/dev/null || true)
fi

LOGIN_TMP="$(mktemp)"
LOGIN_CURL_ARGS=(-sk --max-time 10 -c "$COOKIE" -b "$COOKIE"
    -o "$LOGIN_TMP" -w "%{http_code}"
    -X POST "${API_BASE}/login"
    -H "Content-Type: application/x-www-form-urlencoded"
    --data-urlencode "username=${PANEL_USER}"
    --data-urlencode "password=${PANEL_PASS}")
[[ -n "$CSRF_TOKEN" ]] && LOGIN_CURL_ARGS+=(-H "X-CSRF-Token: ${CSRF_TOKEN}")

LOGIN_CODE=$(curl "${LOGIN_CURL_ARGS[@]}" 2>/dev/null || true)
LOGIN_RESP="$(cat "$LOGIN_TMP" 2>/dev/null || true)"
rm -f "$LOGIN_TMP"
if ! echo "$LOGIN_RESP" | jq -e '.success' >/dev/null 2>&1; then
    err "Login failed (HTTP $LOGIN_CODE, csrf=$CSRF_CODE). Response: ${LOGIN_RESP:-<empty>}. URL=${API_BASE}/login user=${PANEL_USER}"
fi

# Step 1: locate the setting mount, then list existing named tokens.
SETTING_BASE=""
LIST_CODE=""
LIST_RESP=""
for sb in "${API_BASE}/panel/api/setting" "${API_BASE}/panel/setting"; do
    LIST_TMP="$(mktemp)"
    LIST_CODE=$(curl -sk --max-time 10 -b "$COOKIE" \
        -H "X-Requested-With: XMLHttpRequest" \
        -o "$LIST_TMP" -w "%{http_code}" \
        "${sb}/apiTokens" 2>/dev/null || true)
    LIST_RESP="$(cat "$LIST_TMP" 2>/dev/null || true)"
    rm -f "$LIST_TMP"
    # JSON with success=true = the real controller; the SPA shell or a
    # 404 page fails this test and we fall through to the next mount.
    if [[ "$LIST_CODE" == "200" ]] \
        && echo "$LIST_RESP" | jq -e '.success == true' >/dev/null 2>&1; then
        SETTING_BASE="$sb"
        break
    fi
done
[[ -n "$SETTING_BASE" ]] \
    || err "apiTokens endpoint not found on either mount (last code=$LIST_CODE resp=${LIST_RESP:0:200})"

# If "pitun" already exists (re-run case), reuse its token so any
# handed-out URI stays valid.
API_TOKEN=$(echo "$LIST_RESP" \
    | jq -r --arg n "$PITUN_TOKEN_NAME" \
        '.obj[]? | select(.name == $n) | .token' \
        2>/dev/null | head -n1 || true)

# Step 2: nothing existing → create a fresh one. Cookie-auth mutations
# go through CSRFMiddleware; Bearer would short-circuit it but we don't
# have a Bearer token yet (that's the whole point of this call).
# `apiTokens/create` expects a JSON body with the required `name`
# field; response shape is `{obj: {id, name, token, enabled, createdAt}}`.
if [[ -z "$API_TOKEN" ]]; then
    CREATE_TMP="$(mktemp)"
    CREATE_ARGS=(-sk --max-time 10 -b "$COOKIE"
        -o "$CREATE_TMP" -w "%{http_code}"
        -X POST "${SETTING_BASE}/apiTokens/create"
        -H "Content-Type: application/json"
        --data-binary "{\"name\":\"${PITUN_TOKEN_NAME}\"}")
    [[ -n "$CSRF_TOKEN" ]] && CREATE_ARGS+=(-H "X-CSRF-Token: ${CSRF_TOKEN}")
    CREATE_CODE=$(curl "${CREATE_ARGS[@]}" 2>/dev/null || true)
    CREATE_RESP="$(cat "$CREATE_TMP" 2>/dev/null || true)"
    rm -f "$CREATE_TMP"
    API_TOKEN=$(echo "$CREATE_RESP" | jq -r '.obj.token // empty' 2>/dev/null || true)
fi

if [[ -z "$API_TOKEN" ]]; then
    err "Failed to obtain Bearer API token. listCode=${LIST_CODE:-?} listResp=${LIST_RESP:-<empty>} createCode=${CREATE_CODE:-?} createResp=${CREATE_RESP:-<empty>}"
fi

# Verify the token actually authenticates against the inbounds API.
PROBE_CODE=$(curl -sk --max-time 5 -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $API_TOKEN" \
    "${API_BASE}/panel/api/inbounds/list")
[[ "$PROBE_CODE" == "200" ]] \
    || err "Bearer token rejected by API (code $PROBE_CODE). Aborting before emitting bad URI."

log "API token verified."

# ── 6. SSH port + fail2ban (shared with naive/wg flow) ─────────────────────
SSH_PORT="${SSH_PORT:-}"
if [[ -n "$SSH_PORT" ]] && [[ "$SSH_PORT" != "22" ]]; then
    if ! [[ "$SSH_PORT" =~ ^[0-9]+$ ]] || (( SSH_PORT < 1 || SSH_PORT > 65535 )); then
        warn "SSH_PORT='$SSH_PORT' is not a valid 1-65535 number — ignoring"
    else
        log "Moving SSH to port $SSH_PORT..."
        DROPIN=/etc/ssh/sshd_config.d/99-pitun-xui.conf
        mkdir -p /etc/ssh/sshd_config.d
        {
            echo "# Written by setup-xui-server.sh — $(date -Iseconds)"
            echo "Port $SSH_PORT"
            [[ "${SSH_KEEP_22:-no}" == "yes" ]] && echo "Port 22"
        } > "$DROPIN"
        chmod 644 "$DROPIN"
        if ! sshd -t 2>/dev/null; then
            warn "sshd config validation failed — reverting"
            rm -f "$DROPIN"
        else
            # Pre-open in UFW before the restart.
            ufw allow "${SSH_PORT}/tcp" >/dev/null 2>&1 || true
            if systemctl is-active --quiet ssh.socket; then
                mkdir -p /etc/systemd/system/ssh.socket.d
                {
                    echo "[Socket]"
                    echo "ListenStream="
                    [[ "${SSH_KEEP_22:-no}" == "yes" ]] && echo "ListenStream=22"
                    echo "ListenStream=$SSH_PORT"
                } > /etc/systemd/system/ssh.socket.d/override.conf
                systemctl daemon-reload
                systemctl restart ssh.socket || warn "ssh.socket restart failed"
            else
                rm -f /etc/systemd/system/ssh.socket.d/override.conf
                systemctl daemon-reload
                systemctl restart ssh.service 2>/dev/null \
                    || systemctl restart sshd.service 2>/dev/null \
                    || warn "Could not restart sshd"
            fi
            info "SSH now on port $SSH_PORT"
        fi
    fi
fi

if [[ "${INSTALL_FAIL2BAN:-yes}" == "yes" ]]; then
    log "Installing fail2ban..."
    apt-get install -y -qq fail2ban
    F2B_SSH_PORT="${SSH_PORT:-22}"
    cat >/etc/fail2ban/jail.d/pitun-sshd.local <<EOF
[sshd]
enabled  = true
port     = $F2B_SSH_PORT
backend  = systemd
maxretry = 5
findtime = 1h
bantime  = 1h
EOF
    systemctl enable --now fail2ban >/dev/null 2>&1 || true
fi

# ── 7. Firewall ─────────────────────────────────────────────────────────────
log "Configuring firewall (ufw)..."
ufw allow 22/tcp   >/dev/null 2>&1 || true
ufw allow "${PANEL_PORT}/tcp" >/dev/null 2>&1 || true
if [[ "$INSTALL_MODE" == "xui-pro" ]]; then
    ufw allow 80/tcp  >/dev/null 2>&1 || true
    ufw allow 443/tcp >/dev/null 2>&1 || true
fi
ufw --force enable >/dev/null 2>&1 || true

# ── 7b. VPS optimization for proxy workloads (opt-out via OPTIMIZE=no) ──────
# x-ui hosts an xray-core process which fans out into many concurrent TCP
# sessions — a default Debian/Ubuntu install leaves enough headroom for a
# webserver but throttles hard at a few hundred conn / sec because the TCP
# socket buffers + somaxconn + file-max ceilings weren't tuned for that
# pattern. Apply a focused profile derived from the user's reference
# `optimize-server.sh` (BBR + sysctl buffers + swap + ulimits + logrotate)
# — same set of knobs, no menu (we always apply the full profile for
# x-ui boxes; toggle with OPTIMIZE=no to skip entirely).
if [[ "${OPTIMIZE:-yes}" == "yes" ]]; then
    log "Applying VPS optimization profile for proxy workloads..."

    # RAM-aware tuning. Tight RAM (<=1 GB) → smaller TCP buffers + smaller
    # min_free_kbytes to avoid OOM under burst. Generous RAM (>2 GB) →
    # 64 MB max socket buffers, 2 MB tcp_mem mid tier.
    RAM_MB=$(free -m | awk '/Mem:/{print $2}')
    if [[ "$RAM_MB" -le 1024 ]]; then
        MIN_FREE_KB=16384
        RMEM_MAX=16777216; WMEM_MAX=16777216
        TCP_RMEM="8192 524288 16777216"
        TCP_WMEM="8192 524288 16777216"
        TCP_MEM="32768 524288 16777216"
        UDP_MEM="32768 524288 16777216"
        SWAP_SIZE="2G"
    elif [[ "$RAM_MB" -le 2048 ]]; then
        MIN_FREE_KB=32768
        RMEM_MAX=33554432; WMEM_MAX=33554432
        TCP_RMEM="16384 1048576 33554432"
        TCP_WMEM="16384 1048576 33554432"
        TCP_MEM="65536 1048576 33554432"
        UDP_MEM="65536 1048576 33554432"
        SWAP_SIZE="2G"
    else
        MIN_FREE_KB=65536
        RMEM_MAX=67108864; WMEM_MAX=67108864
        TCP_RMEM="16384 1048576 67108864"
        TCP_WMEM="16384 1048576 67108864"
        TCP_MEM="65536 2097152 67108864"
        UDP_MEM="65536 2097152 67108864"
        SWAP_SIZE="4G"
    fi

    # ── BBR ──────────────────────────────────────────────────────────────
    # BBR is the modern TCP congestion control; on transit links with
    # any packet loss it consistently beats CUBIC (Debian/Ubuntu default).
    # Module is loaded out of the box on Debian 12+ kernels, but we make
    # the activation persistent.
    if ! lsmod | grep -q tcp_bbr; then
        modprobe tcp_bbr 2>/dev/null || true
    fi
    if ! grep -q "tcp_bbr" /etc/modules-load.d/*.conf 2>/dev/null; then
        echo "tcp_bbr" > /etc/modules-load.d/pitun-bbr.conf
    fi

    # ── sysctl drop-in ───────────────────────────────────────────────────
    # Use a drop-in under /etc/sysctl.d/ so we don't disturb the user's
    # own /etc/sysctl.conf. `sysctl --system` picks up the drop-in on
    # reboot; we also `sysctl -p` it now for the running kernel.
    cat >/etc/sysctl.d/99-pitun-xui-optimize.conf <<SYSEOF
# Written by setup-xui-server.sh — proxy workload tuning. Remove this
# drop-in (and reboot OR \`sysctl --system\`) to revert.

# ── File system ──
fs.file-max = 67108864

# ── Queueing & Congestion ──
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr

# ── Socket buffers ──
net.core.netdev_max_backlog = 32768
net.core.optmem_max = 262144
net.core.somaxconn = 65536
net.core.rmem_max = ${RMEM_MAX}
net.core.wmem_max = ${WMEM_MAX}
net.core.rmem_default = 1048576
net.core.wmem_default = 1048576

# ── TCP tuning ──
net.ipv4.tcp_rmem = ${TCP_RMEM}
net.ipv4.tcp_wmem = ${TCP_WMEM}
net.ipv4.tcp_mem = ${TCP_MEM}
net.ipv4.tcp_fin_timeout = 20
net.ipv4.tcp_keepalive_time = 600
net.ipv4.tcp_keepalive_probes = 5
net.ipv4.tcp_keepalive_intvl = 15
net.ipv4.tcp_max_orphans = 819200
net.ipv4.tcp_max_syn_backlog = 20480
net.ipv4.tcp_max_tw_buckets = 1440000
net.ipv4.tcp_mtu_probing = 1
net.ipv4.tcp_notsent_lowat = 16384
net.ipv4.tcp_retries2 = 8
net.ipv4.tcp_sack = 1
net.ipv4.tcp_dsack = 1
net.ipv4.tcp_slow_start_after_idle = 0
net.ipv4.tcp_window_scaling = 1
net.ipv4.tcp_syncookies = 1
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fastopen = 3
net.ipv4.ip_local_port_range = 1024 65535

# ── UDP ──
net.ipv4.udp_mem = ${UDP_MEM}

# ── UNIX sockets ──
net.unix.max_dgram_qlen = 256

# ── VM ──
vm.min_free_kbytes = ${MIN_FREE_KB}
vm.swappiness = 10
vm.vfs_cache_pressure = 120
vm.dirty_ratio = 20

# ── Security ──
net.ipv4.conf.default.rp_filter = 2
net.ipv4.conf.all.rp_filter = 2
net.ipv4.conf.all.accept_source_route = 0
net.ipv4.conf.default.accept_source_route = 0

# ── Kernel ──
kernel.panic = 1
SYSEOF
    sysctl --system >/dev/null 2>&1 || sysctl -p /etc/sysctl.d/99-pitun-xui-optimize.conf >/dev/null 2>&1 || true
    info "sysctl applied (BBR + TCP tuning for RAM=${RAM_MB}MB)"

    # ── swap ─────────────────────────────────────────────────────────────
    # Most VPS images ship without swap. Adding 2-4 GB keeps the OOM
    # killer from culling xray under a memory burst (common when many
    # clients pull big files through at once).
    CURRENT_SWAP=$(swapon --show --bytes --noheadings 2>/dev/null | awk '{sum+=$3} END{print int(sum/1024/1024)}')
    CURRENT_SWAP=${CURRENT_SWAP:-0}
    if [[ "$CURRENT_SWAP" -lt 1024 ]]; then
        log "Creating ${SWAP_SIZE} swapfile..."
        if [[ -f /swapfile ]]; then
            swapoff /swapfile 2>/dev/null || true
            rm -f /swapfile
        fi
        # fallocate works on every modern Linux FS (ext4/xfs/btrfs).
        # If it fails (some VFAT VPS images), fall back to dd. We
        # compute MiB count by stripping the trailing G and multiplying.
        if ! fallocate -l "$SWAP_SIZE" /swapfile 2>/dev/null; then
            swap_gb="${SWAP_SIZE%G}"
            dd if=/dev/zero of=/swapfile bs=1M count=$(( swap_gb * 1024 )) \
                status=none 2>/dev/null \
                || warn "swap allocation failed — skipping"
        fi
        if [[ -f /swapfile ]] && [[ -s /swapfile ]]; then
            chmod 600 /swapfile
            mkswap /swapfile >/dev/null 2>&1
            swapon /swapfile 2>/dev/null || true
            if ! grep -q "/swapfile" /etc/fstab; then
                echo "/swapfile   none    swap    sw    0   0" >> /etc/fstab
            fi
            info "swap=${SWAP_SIZE} mounted + persisted"
        fi
    else
        info "swap already configured (${CURRENT_SWAP}MB) — skipping"
    fi

    # ── ulimits ──────────────────────────────────────────────────────────
    # Default `nofile=1024` is a death sentence for an xray box. Bump
    # to 1M for both soft + hard, mirror via systemd defaults (so
    # x-ui.service inherits the higher cap on next start).
    for PATTERN in "soft nofile" "hard nofile" "soft nproc" "hard nproc"; do
        sed -i "/\*.*${PATTERN}/d" /etc/security/limits.conf 2>/dev/null || true
    done
    cat >>/etc/security/limits.conf <<'LIMEOF'
*               soft    nofile          1048576
*               hard    nofile          1048576
*               soft    nproc           65535
*               hard    nproc           65535
LIMEOF
    sed -i '/ulimit -[nu]/d' /etc/profile 2>/dev/null || true
    echo "ulimit -n 1048576" >> /etc/profile
    echo "ulimit -u 65535"   >> /etc/profile
    mkdir -p /etc/systemd/system.conf.d
    cat >/etc/systemd/system.conf.d/pitun-limits.conf <<'SDLIM'
[Manager]
DefaultLimitNOFILE=1048576
DefaultLimitNPROC=65535
SDLIM
    systemctl daemon-reload 2>/dev/null || true
    info "ulimits: nofile=1048576, nproc=65535"

    # ── logrotate ─────────────────────────────────────────────────────────
    # Without rotation x-ui's stdout + nginx's access log eat the disk
    # over weeks. journald gets a hard cap too.
    cat >/etc/logrotate.d/pitun-x-ui <<'LRXUI'
/var/log/x-ui/*.log {
    daily
    missingok
    rotate 3
    maxsize 50M
    compress
    delaycompress
    notifempty
    copytruncate
}
LRXUI
    if [[ -d /etc/nginx ]] || command -v nginx >/dev/null 2>&1; then
        cat >/etc/logrotate.d/pitun-nginx <<'LRNG'
/var/log/nginx/*.log {
    daily
    missingok
    rotate 3
    maxsize 50M
    compress
    delaycompress
    notifempty
    create 0640 www-data adm
    sharedscripts
    prerotate
        if [ -d /etc/logrotate.d/httpd-prerotate ]; then
            run-parts /etc/logrotate.d/httpd-prerotate;
        fi
    endscript
    postrotate
        invoke-rc.d nginx rotate >/dev/null 2>&1 || true
    endscript
}
LRNG
    fi
    mkdir -p /etc/systemd/journald.conf.d
    cat >/etc/systemd/journald.conf.d/pitun-size-limit.conf <<'JDLOG'
[Journal]
SystemMaxUse=100M
SystemKeepFree=200M
MaxFileSec=1day
JDLOG
    systemctl restart systemd-journald 2>/dev/null || true
    info "logrotate + journald caps configured"

    info "VPS optimization complete (BBR / sysctl / swap / ulimits / logs)"
else
    info "OPTIMIZE=no — skipping VPS tuning profile."
fi

# ── 7.5 Post-install Go cleanup ─────────────────────────────────────────────
# x-ui-pro.sh / older revisions bootstrap a Go toolchain (~2.5 GB:
# ~/go ≈ 1.6 GB, ~/.cache/go-build ≈ 600 MB, /usr/local/go ≈ 250 MB)
# that's only needed at build time. Scrub once x-ui is confirmed
# running; re-installing the panel re-downloads Go on demand.
#
# Inlined (was sourced from `scripts/cleanup-go.sh` in an earlier
# revision, but PiTun deploys setup-*.sh via single-file SFTP — the
# sibling helper never made it to the VPS and `set -e` aborted the
# whole script before the final URL summary printed).
if systemctl is-active --quiet x-ui 2>/dev/null; then
    if [ -e /root/go ] || [ -e /root/.cache/go-build ] || [ -e /usr/local/go ]; then
        log "Freeing ~2.5 GB by removing Go SDK / build cache (runtime not affected)..."
        rm -rf /root/go /root/.cache/go-build /usr/local/go 2>/dev/null || true
        # /usr/local/bin/go points into the deleted SDK — drop the
        # dangling symlink so `command -v go` honestly reports missing.
        [ -L /usr/local/bin/go ] && rm -f /usr/local/bin/go
        info "Go tooling removed (re-installs on next reinstall if needed)."
    fi
    # Drop the master.zip the x-ui-pro installer leaves around. The
    # extracted /root/randomfakehtml-master folder stays — fakesite
    # rotation needs it.
    [ -f /root/randomfakehtml-master.zip ] && rm -f /root/randomfakehtml-master.zip
fi


# ── 8. Emit URI for PiTun's URI parser ──────────────────────────────────────
# Format: xui://<api_token>@<host>:<panel_port><basepath>?user=<e>&pass=<e>&domain=<e>
# url-encode user/pass/domain — the script already restricts each to URL-safe
# chars at gen time, but a domain may contain dots so we encode defensively.
urlenc() {
    python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$1"
}

# Public-IP detection. Force IPv4 (`-4`) — without it ifconfig.me /
# ipify happily return the dual-stacked host's IPv6 (e.g.
# `2a00:bd80:a176:10:47c::1`) which then breaks the URI we emit:
# vless / xui-style URLs need bracket-wrapped IPv6 (`[…]:port`) and
# PiTun's URI parser sees the trailing `:port` as part of the IPv6
# address. Pinning to v4 is also what every existing client URL
# the operator has assumes; if they really want v6 they can supply
# DOMAIN explicitly. Falls through ifconfig.me → ipify (v4) → ifconfig.co.
HOST_FOR_URI="${DOMAIN:-}"
if [[ -z "$HOST_FOR_URI" ]]; then
    HOST_FOR_URI="$(
        curl -4s --max-time 5 ifconfig.me \
        || curl -4s --max-time 5 api.ipify.org \
        || curl -4s --max-time 5 ifconfig.co \
        || echo "UNKNOWN"
    )"
fi
ENC_USER="$(urlenc "$PANEL_USER")"
ENC_PASS="$(urlenc "$PANEL_PASS")"
ENC_DOMAIN="$(urlenc "${DOMAIN:-}")"

echo
info "════════════════════════════════════════════════════════════════"
info "  x-ui panel installed"
info "════════════════════════════════════════════════════════════════"
echo
echo "  Mode:       $INSTALL_MODE"
echo "  Pinned:     3x-ui $XUI_VERSION"
echo "  Panel URL:  ${PANEL_SCHEME}://${HOST_FOR_URI}:${PANEL_PORT}${PANEL_BASEPATH}"
echo "  Username:   $PANEL_USER"
echo "  Password:   $PANEL_PASS"
echo "  API token:  ${API_TOKEN:0:8}…${API_TOKEN: -4}    (full token in URI line below)"
echo
info "Import URI (paste into PiTun → Servers → Add x-ui):"
echo
echo "URI=xui://${API_TOKEN}@${HOST_FOR_URI}:${PANEL_PORT}${PANEL_BASEPATH%/}?user=${ENC_USER}&pass=${ENC_PASS}&domain=${ENC_DOMAIN}&mode=${INSTALL_MODE}"
echo
info "════════════════════════════════════════════════════════════════"
