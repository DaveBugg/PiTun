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
# the API surface (`/panel/api/inbounds/...`, Bearer auth, /panel/api/setting/
# getApiToken endpoints) stays identical regardless of which path was used.
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
#   XUI_VERSION=v3.0.0        Pinned 3x-ui release tag. Both modes use this.
#                             Override at your own risk — the Bearer API
#                             middleware exists since v3.0.0; anything older
#                             requires cookie+CSRF auth which PiTun doesn't
#                             implement.
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
XUI_VERSION="${XUI_VERSION:-v3.0.0}"
PANEL_PORT="${PANEL_PORT:-}"
PANEL_BASEPATH="${PANEL_BASEPATH:-}"
PANEL_USER="${PANEL_USER:-}"
PANEL_PASS="${PANEL_PASS:-}"
INSTALL_FAIL2BAN="${INSTALL_FAIL2BAN:-yes}"
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
    bash <(wget -qO- "https://raw.githubusercontent.com/GFW4Fun/x-ui-pro/master/x-ui-pro.sh") \
        -panel 1 \
        -xuiver "$ver_for_pro" \
        -cdn off \
        -secure no \
        -country xx \
        -subdomain "$DOMAIN" \
        || err "x-ui-pro installer exited non-zero"
else
    # ── 3b. Bare upstream 3x-ui mode ────────────────────────────────────────
    log "Running upstream 3x-ui installer ($XUI_VERSION)..."
    # `printf 'n\n'` skips the upstream installer's "use default creds?"
    # prompt — we set our own creds in step 4.
    printf 'n\n' | bash <(wget -qO- \
        "https://raw.githubusercontent.com/MHSanaei/3x-ui/${XUI_VERSION}/install.sh") \
        "$XUI_VERSION" \
        || err "3x-ui installer exited non-zero"
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
# v3.0.0 onwards exposes /panel/api/setting/{getApiToken,regenerateApiToken}.
# On a fresh install the token may exist already (populated by the panel on
# first start) OR be empty — in either case we regenerate so we know the
# value we're about to print.
log "Bootstrapping Bearer API token..."

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

# Try GET /getApiToken first — on a fresh install the panel auto-seeds
# a token at boot, so we'd rather read the existing one than rotate it
# (rotation invalidates any token we might've handed out previously,
# which matters for re-runs against an existing PiTun-managed panel).
GET_TMP="$(mktemp)"
GET_CODE=$(curl -sk --max-time 10 -b "$COOKIE" \
    -o "$GET_TMP" -w "%{http_code}" \
    "${API_BASE}/panel/setting/getApiToken" 2>/dev/null || true)
GET_RESP="$(cat "$GET_TMP" 2>/dev/null || true)"
rm -f "$GET_TMP"
API_TOKEN=$(echo "$GET_RESP" | jq -r '.obj // empty' 2>/dev/null || true)

# Regenerate path — /panel/api/* also goes through CSRFMiddleware for
# cookie-auth callers, so the token-in-header dance from /login applies
# here too. Bearer-auth would short-circuit it but we haven't got the
# token yet, that's the whole point of this call.
if [[ -z "$API_TOKEN" ]]; then
    REGEN_TMP="$(mktemp)"
    REGEN_ARGS=(-sk --max-time 10 -b "$COOKIE"
        -o "$REGEN_TMP" -w "%{http_code}"
        -X POST "${API_BASE}/panel/setting/regenerateApiToken"
        -H "Content-Type: application/json")
    [[ -n "$CSRF_TOKEN" ]] && REGEN_ARGS+=(-H "X-CSRF-Token: ${CSRF_TOKEN}")
    REGEN_CODE=$(curl "${REGEN_ARGS[@]}" 2>/dev/null || true)
    REGEN_RESP="$(cat "$REGEN_TMP" 2>/dev/null || true)"
    rm -f "$REGEN_TMP"
    API_TOKEN=$(echo "$REGEN_RESP" | jq -r '.obj // .msg // empty' 2>/dev/null \
        | grep -E '^[A-Za-z0-9_-]{20,}$' | head -n1 || true)
fi

if [[ -z "$API_TOKEN" ]]; then
    err "Failed to obtain Bearer API token. getCode=${GET_CODE:-?} getResp=${GET_RESP:-<empty>} regenCode=${REGEN_CODE:-?} regenResp=${REGEN_RESP:-<empty>}"
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

# ── 8. Emit URI for PiTun's URI parser ──────────────────────────────────────
# Format: xui://<api_token>@<host>:<panel_port><basepath>?user=<e>&pass=<e>&domain=<e>
# url-encode user/pass/domain — the script already restricts each to URL-safe
# chars at gen time, but a domain may contain dots so we encode defensively.
urlenc() {
    python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1],safe=""))' "$1"
}

HOST_FOR_URI="${DOMAIN:-$(curl -s --max-time 5 ifconfig.me || curl -s --max-time 5 api.ipify.org || echo "UNKNOWN")}"
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
