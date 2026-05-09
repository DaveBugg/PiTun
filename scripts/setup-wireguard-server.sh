#!/bin/bash
# ============================================================================
# PiTun WireGuard server installer (v1.3.0-beta.4 onward).
#
# Sub-command dispatcher. Replaces the interactive `wg-install.sh`
# (Nyr/angristan) loop with a small set of one-shot operations the
# PiTun backend can call over SSH. All input is via env vars; nothing
# blocks on `read`.
#
# Sub-commands (passed as $1):
#
#   install        First-time setup: install wireguard packages, generate
#                  server keys, write /etc/wireguard/wg0.conf, bring up
#                  wg-quick@wg0, then create the first peer (CLIENT_NAME).
#                  Prints `URI=wireguard://...` for the first client.
#
#                  Required env: CLIENT_NAME
#                  Optional env: SERVER_PORT (default 51820),
#                                WG_NETWORK_4 (default 10.66.66.0/24),
#                                WG_NETWORK_6 (default fd42:42:42::/64),
#                                DNS_1 (default 1.1.1.1),
#                                DNS_2 (default 1.0.0.1),
#                                ALLOWED_IPS (default "0.0.0.0/0,::/0"),
#                                SERVER_PUB_IP (autodetected if empty).
#
#   add-client     Add a new peer to an already-installed server.
#                  Allocates next free IP in WG_NETWORK_4/6, generates
#                  priv/pub/psk for the peer, appends [Peer] block to
#                  wg0.conf, hot-reloads via `wg syncconf` (no tunnel
#                  restart — existing clients stay connected), prints
#                  the new peer's URI line.
#
#                  Required env: CLIENT_NAME
#                  Optional env: DNS_1, DNS_2, ALLOWED_IPS (override
#                                per-client; defaults inherit from
#                                params).
#
#   remove-client  Remove a peer by name. Strips the [Peer] block from
#                  wg0.conf, hot-reloads via `wg syncconf`, deletes the
#                  per-client conf file. Prints `REMOVED=<name>`.
#
#                  Required env: CLIENT_NAME
#
#   list-clients   Emit the current server-side peer list as JSON for
#                  the backend's sync routine. Prints
#                  `CLIENTS=<json>` where the JSON is an array of
#                  {name, public_key, address}.
#
# Convention: each peer block in wg0.conf is preceded by a marker
# comment `# PITUN-CLIENT name=<name>` so we can identify our peers
# unambiguously. Peers added externally (by hand-editing wg0.conf,
# by another wg-install variant, etc.) without that marker are
# IGNORED by add/remove/list — sync will treat them as out-of-band
# (PiTun won't manage them).
#
# Required at runtime: bash 4+, wg, wg-quick, ip, sysctl, iptables
#                      (or nftables-equivalent), systemctl. The
#                      `install` sub-command apt-get's everything.
# ============================================================================

set -euo pipefail

# ── Pretty output ──────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
err()  { echo -e "${RED}[x]${NC} $*" >&2; exit 1; }
info() { echo -e "${BLUE}[i]${NC} $*"; }

[[ $EUID -ne 0 ]] && err "Run as root: sudo bash $0 <sub-command>"

# ── OS detection (same matrix setup-naive-server.sh supports) ──────────────
[[ -f /etc/os-release ]] || err "/etc/os-release missing — can't detect OS"
# shellcheck disable=SC1091
. /etc/os-release
OS_ID="${ID:-unknown}"
OS_VER="${VERSION_ID:-0}"
OS_MAJOR="${OS_VER%%.*}"

case "$OS_ID" in
    debian)
        if (( OS_MAJOR < 12 )); then
            err "Debian $OS_VER unsupported — needs 12 (bookworm) or newer"
        fi
        ;;
    ubuntu)
        if (( OS_MAJOR < 22 )); then
            err "Ubuntu $OS_VER unsupported — needs 22.04 or newer"
        fi
        ;;
    *)
        warn "OS '$OS_ID' is untested — proceeding anyway."
        ;;
esac

# ── Constants ──────────────────────────────────────────────────────────────
WG_IF="wg0"
WG_CONF="/etc/wireguard/${WG_IF}.conf"
WG_PARAMS="/etc/wireguard/pitun-params"
CLIENTS_DIR="/etc/wireguard/pitun-clients"

# ── Helper: source params if installed already ─────────────────────────────
load_params() {
    [[ -f "$WG_PARAMS" ]] || err "WireGuard not installed yet — run 'install' first."
    # shellcheck disable=SC1090
    . "$WG_PARAMS"
}

# ── Helper: detect public IP (autodetect path) ─────────────────────────────
detect_public_ip() {
    local ip
    # Try IPv4 from default route first — most reliable on a VPS where
    # the public IP is on the default-gateway interface.
    ip=$(ip -4 addr show 2>/dev/null \
        | grep -oP '(?<=inet\s)\d+(\.\d+){3}' \
        | grep -vE '^(10\.|172\.1[6-9]\.|172\.2[0-9]\.|172\.3[01]\.|192\.168\.|127\.|169\.254\.)' \
        | head -1)
    if [[ -z "$ip" ]]; then
        # Behind NAT — ask an external service.
        ip=$(curl -m 5 -fsS https://api.ipify.org 2>/dev/null \
             || curl -m 5 -fsS https://ip1.dynupdate.no-ip.com/ 2>/dev/null \
             || true)
    fi
    echo "$ip"
}

# ── Helper: validate client name (alphanumeric + - _ only) ─────────────────
validate_client_name() {
    local n="$1"
    [[ -z "$n" ]] && err "CLIENT_NAME is required."
    if [[ ! "$n" =~ ^[a-zA-Z0-9_-]+$ ]]; then
        err "CLIENT_NAME contains invalid characters. Use only [a-zA-Z0-9_-]."
    fi
    if (( ${#n} > 64 )); then
        err "CLIENT_NAME too long (max 64 chars)."
    fi
}

# ── Helper: emit one peer's URI on the contract format ─────────────────────
# `wireguard://privkey@host:port?publickey=...&presharedkey=...&address=...&mtu=1420#name`
# Matches `core/uri_parser._parse_wireguard()`.
emit_uri() {
    local name="$1" privkey="$2" pubkey="$3" psk="$4" \
          host="$5" port="$6" address="$7" mtu="$8"
    # Use Python for precise URL encoding. The script already pulls
    # python3 in via apt earlier (it's a stdlib build dep elsewhere).
    local enc_priv enc_pub enc_psk enc_addr enc_name
    enc_priv=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$privkey")
    enc_pub=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$pubkey")
    enc_psk=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$psk")
    enc_addr=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$address")
    enc_name=$(python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" "$name")
    echo "URI=wireguard://${enc_priv}@${host}:${port}?publickey=${enc_pub}&presharedkey=${enc_psk}&address=${enc_addr}&mtu=${mtu}#${enc_name}"
}

# ── cmd_install ────────────────────────────────────────────────────────────
cmd_install() {
    if [[ -f "$WG_CONF" ]]; then
        err "WireGuard already installed at $WG_CONF — use 'add-client' instead."
    fi

    # Required + optional inputs
    local CLIENT_NAME="${CLIENT_NAME:-}"
    validate_client_name "$CLIENT_NAME"

    local SERVER_PORT="${SERVER_PORT:-51820}"
    local WG_NETWORK_4="${WG_NETWORK_4:-10.66.66.0/24}"
    local WG_NETWORK_6="${WG_NETWORK_6:-fd42:42:42::/64}"
    local DNS_1="${DNS_1:-1.1.1.1}"
    local DNS_2="${DNS_2:-1.0.0.1}"
    local ALLOWED_IPS="${ALLOWED_IPS:-0.0.0.0/0,::/0}"
    local SERVER_PUB_IP="${SERVER_PUB_IP:-}"

    # Validate port
    if ! [[ "$SERVER_PORT" =~ ^[0-9]+$ ]] || (( SERVER_PORT < 1 || SERVER_PORT > 65535 )); then
        err "SERVER_PORT must be 1..65535 (got: $SERVER_PORT)"
    fi

    [[ -z "$SERVER_PUB_IP" ]] && SERVER_PUB_IP=$(detect_public_ip)
    [[ -z "$SERVER_PUB_IP" ]] && err "Could not auto-detect public IP. Pass SERVER_PUB_IP env var."

    # ── Install packages
    log "Installing wireguard packages..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq wireguard wireguard-tools iptables qrencode python3 ca-certificates curl

    # ── Detect default NIC for NAT
    local SERVER_NIC
    SERVER_NIC=$(ip -4 route ls | awk '/default/ {print $5; exit}')
    [[ -z "$SERVER_NIC" ]] && err "Could not detect default network interface."
    info "Public NIC for NAT: $SERVER_NIC"

    # ── Generate server keys
    mkdir -p /etc/wireguard
    chmod 700 /etc/wireguard
    local SERVER_PRIV SERVER_PUB
    SERVER_PRIV=$(wg genkey)
    SERVER_PUB=$(echo "$SERVER_PRIV" | wg pubkey)

    # First IP in the /24 (and ::/64) is the server.
    local WG_SERVER_4="${WG_NETWORK_4%.*/*}.1/24"
    # Strip last group of WG_NETWORK_6 (fd42:42:42:: / 64) → fd42:42:42::1/64
    local WG_SERVER_6="${WG_NETWORK_6%::/*}::1/64"

    # ── Write wg0.conf with PostUp/Down NAT rules
    cat > "$WG_CONF" <<EOF
# PITUN-MANAGED — do not hand-edit. setup-wireguard-server.sh manages this file.
[Interface]
Address = ${WG_SERVER_4},${WG_SERVER_6}
ListenPort = ${SERVER_PORT}
PrivateKey = ${SERVER_PRIV}
PostUp = iptables -I INPUT -p udp --dport ${SERVER_PORT} -j ACCEPT
PostUp = iptables -I FORWARD -i ${SERVER_NIC} -o ${WG_IF} -j ACCEPT
PostUp = iptables -I FORWARD -i ${WG_IF} -j ACCEPT
PostUp = iptables -t nat -A POSTROUTING -o ${SERVER_NIC} -j MASQUERADE
PostUp = ip6tables -I FORWARD -i ${WG_IF} -j ACCEPT
PostUp = ip6tables -t nat -A POSTROUTING -o ${SERVER_NIC} -j MASQUERADE
PostDown = iptables -D INPUT -p udp --dport ${SERVER_PORT} -j ACCEPT
PostDown = iptables -D FORWARD -i ${SERVER_NIC} -o ${WG_IF} -j ACCEPT
PostDown = iptables -D FORWARD -i ${WG_IF} -j ACCEPT
PostDown = iptables -t nat -D POSTROUTING -o ${SERVER_NIC} -j MASQUERADE
PostDown = ip6tables -D FORWARD -i ${WG_IF} -j ACCEPT
PostDown = ip6tables -t nat -D POSTROUTING -o ${SERVER_NIC} -j MASQUERADE
EOF
    chmod 600 "$WG_CONF"

    # ── Persist params for subsequent add-client / list-clients calls
    cat > "$WG_PARAMS" <<EOF
# Generated by setup-wireguard-server.sh — do not hand-edit.
SERVER_PUB_IP="${SERVER_PUB_IP}"
SERVER_PORT="${SERVER_PORT}"
SERVER_NIC="${SERVER_NIC}"
SERVER_PUB_KEY="${SERVER_PUB}"
WG_NETWORK_4="${WG_NETWORK_4}"
WG_NETWORK_6="${WG_NETWORK_6}"
DEFAULT_DNS_1="${DNS_1}"
DEFAULT_DNS_2="${DNS_2}"
DEFAULT_ALLOWED_IPS="${ALLOWED_IPS}"
EOF
    chmod 600 "$WG_PARAMS"

    mkdir -p "$CLIENTS_DIR"
    chmod 700 "$CLIENTS_DIR"

    # ── Enable IP forwarding
    cat > /etc/sysctl.d/99-pitun-wireguard.conf <<EOF
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
EOF
    sysctl --system >/dev/null

    # ── Bring up
    log "Starting wg-quick@${WG_IF}..."
    systemctl enable --now "wg-quick@${WG_IF}"

    # Sanity wait + check
    sleep 1
    if ! systemctl is-active --quiet "wg-quick@${WG_IF}"; then
        err "wg-quick@${WG_IF} failed to start. Check 'systemctl status wg-quick@${WG_IF}'."
    fi
    info "WireGuard server up: ${SERVER_PUB_IP}:${SERVER_PORT}"

    # ── Add the first client
    log "Adding first peer '${CLIENT_NAME}'..."
    cmd_add_client
}

# ── cmd_add_client ─────────────────────────────────────────────────────────
cmd_add_client() {
    load_params

    local CLIENT_NAME="${CLIENT_NAME:-}"
    validate_client_name "$CLIENT_NAME"

    # Reject duplicate name
    if grep -q "^# PITUN-CLIENT name=${CLIENT_NAME}\$" "$WG_CONF"; then
        err "Client '${CLIENT_NAME}' already exists. Pick another name."
    fi

    # Pull defaults from params; allow per-call override via env
    local DNS_1="${DNS_1:-$DEFAULT_DNS_1}"
    local DNS_2="${DNS_2:-$DEFAULT_DNS_2}"
    local CLIENT_ALLOWED_IPS="${ALLOWED_IPS:-$DEFAULT_ALLOWED_IPS}"

    # Allocate next free .X octet in /24 (skip .1 = server)
    local base_v4="${WG_NETWORK_4%.*/*}"
    local octet=2
    while grep -q "AllowedIPs.*${base_v4}\.${octet}/32" "$WG_CONF"; do
        ((octet++))
        (( octet > 254 )) && err "WireGuard /24 subnet full (253 peers reached)."
    done
    local CLIENT_V4="${base_v4}.${octet}"

    # Same for v6 — assemble base prefix then append the same octet
    local v6_prefix="${WG_NETWORK_6%::/*}"
    local CLIENT_V6="${v6_prefix}::${octet}"

    # Generate keys
    local CLIENT_PRIV CLIENT_PUB CLIENT_PSK
    CLIENT_PRIV=$(wg genkey)
    CLIENT_PUB=$(echo "$CLIENT_PRIV" | wg pubkey)
    CLIENT_PSK=$(wg genpsk)

    # Append [Peer] to wg0.conf with our marker
    cat >> "$WG_CONF" <<EOF

# PITUN-CLIENT name=${CLIENT_NAME}
[Peer]
PublicKey = ${CLIENT_PUB}
PresharedKey = ${CLIENT_PSK}
AllowedIPs = ${CLIENT_V4}/32,${CLIENT_V6}/128
EOF

    # Hot-reload the running tunnel without restarting it (preserves
    # all other peers' active sessions).
    wg syncconf "$WG_IF" <(wg-quick strip "$WG_IF")

    # Write the per-client conf for retrieval / QR
    local CLIENT_ADDRESS="${CLIENT_V4}/24,${CLIENT_V6}/64"
    local CLIENT_CONF="${CLIENTS_DIR}/${CLIENT_NAME}.conf"
    cat > "$CLIENT_CONF" <<EOF
[Interface]
Address = ${CLIENT_ADDRESS}
DNS = ${DNS_1},${DNS_2}
PrivateKey = ${CLIENT_PRIV}

[Peer]
PublicKey = ${SERVER_PUB_KEY}
PresharedKey = ${CLIENT_PSK}
Endpoint = ${SERVER_PUB_IP}:${SERVER_PORT}
AllowedIPs = ${CLIENT_ALLOWED_IPS}
PersistentKeepalive = 25
EOF
    chmod 600 "$CLIENT_CONF"

    info "Peer '${CLIENT_NAME}' added: ${CLIENT_V4}/${CLIENT_V6}"
    info "Conf saved to: ${CLIENT_CONF}"

    # ── Machine-readable contract for PiTun
    emit_uri "$CLIENT_NAME" \
             "$CLIENT_PRIV" "$SERVER_PUB_KEY" "$CLIENT_PSK" \
             "$SERVER_PUB_IP" "$SERVER_PORT" \
             "$CLIENT_ADDRESS" "1420"
    # Also emit the full INI for backend to stash in DeploymentClient.config_json.
    # Wrapped in BEGIN/END markers so the parser can locate it deterministically
    # in the middle of other stdout.
    echo "PITUN-CLIENT-CONF-BEGIN ${CLIENT_NAME}"
    cat "$CLIENT_CONF"
    echo "PITUN-CLIENT-CONF-END ${CLIENT_NAME}"
}

# ── cmd_remove_client ──────────────────────────────────────────────────────
cmd_remove_client() {
    load_params

    local CLIENT_NAME="${CLIENT_NAME:-}"
    validate_client_name "$CLIENT_NAME"

    if ! grep -q "^# PITUN-CLIENT name=${CLIENT_NAME}\$" "$WG_CONF"; then
        err "Client '${CLIENT_NAME}' not found."
    fi

    # Find the [Peer] block's PublicKey to drop from running interface first
    # (so existing data session terminates cleanly).
    local CLIENT_PUB
    CLIENT_PUB=$(awk -v name="$CLIENT_NAME" '
        /^# PITUN-CLIENT name=/ { capture = ($3 == "name="name); next }
        capture && /^PublicKey *= */ { print $3; exit }
    ' "$WG_CONF")

    if [[ -n "$CLIENT_PUB" ]]; then
        wg set "$WG_IF" peer "$CLIENT_PUB" remove 2>/dev/null || true
    fi

    # Strip the [Peer] block from wg0.conf (marker line + next 4 non-blank lines).
    # Use python for safety — sed cross-line ranges are touchy.
    python3 - "$WG_CONF" "$CLIENT_NAME" <<'PYEOF'
import sys
path, name = sys.argv[1], sys.argv[2]
out = []
skip = False
with open(path) as f:
    lines = f.readlines()
i = 0
while i < len(lines):
    line = lines[i]
    if line.rstrip() == f"# PITUN-CLIENT name={name}":
        # Skip marker + the 4-line [Peer] block that follows
        # (Peer header + PublicKey + PresharedKey + AllowedIPs).
        # Also eat any leading blank line we put before it.
        if out and out[-1].strip() == "":
            out.pop()
        # advance past 5 lines (marker + Peer + PublicKey + PresharedKey + AllowedIPs)
        i += 5
        continue
    out.append(line)
    i += 1
with open(path, "w") as f:
    f.writelines(out)
PYEOF

    # Hot-sync — same no-restart benefit
    wg syncconf "$WG_IF" <(wg-quick strip "$WG_IF")

    rm -f "${CLIENTS_DIR}/${CLIENT_NAME}.conf"

    echo "REMOVED=${CLIENT_NAME}"
}

# ── cmd_list_clients ───────────────────────────────────────────────────────
cmd_list_clients() {
    load_params

    # Parse marker lines + following [Peer] block. Emit JSON.
    python3 - "$WG_CONF" <<'PYEOF'
import json, re, sys
path = sys.argv[1]
clients = []
current = None
with open(path) as f:
    for raw in f:
        line = raw.strip()
        m = re.match(r"^# PITUN-CLIENT name=(.+)$", line)
        if m:
            if current:
                clients.append(current)
            current = {"name": m.group(1)}
            continue
        if not current:
            continue
        if line.startswith("PublicKey"):
            current["public_key"] = line.split("=", 1)[1].strip().split()[0]
        elif line.startswith("AllowedIPs"):
            current["address"] = line.split("=", 1)[1].strip()
        elif line.startswith("[") and current.get("name") and current.get("public_key"):
            # Hit start of next [Peer] or [Interface] — close current
            clients.append(current)
            current = None
    if current and current.get("public_key"):
        clients.append(current)
print("CLIENTS=" + json.dumps(clients))
PYEOF
}

# ── cmd_get_conf ───────────────────────────────────────────────────────────
# Re-emit a previously-generated client conf (for "Download .conf" /
# QR-code regeneration). No keys are regenerated.
cmd_get_conf() {
    load_params
    local CLIENT_NAME="${CLIENT_NAME:-}"
    validate_client_name "$CLIENT_NAME"
    local CLIENT_CONF="${CLIENTS_DIR}/${CLIENT_NAME}.conf"
    [[ -f "$CLIENT_CONF" ]] || err "Conf not found: $CLIENT_CONF"
    echo "PITUN-CLIENT-CONF-BEGIN ${CLIENT_NAME}"
    cat "$CLIENT_CONF"
    echo "PITUN-CLIENT-CONF-END ${CLIENT_NAME}"
}

# ── Sub-command dispatch ───────────────────────────────────────────────────
case "${1:-install}" in
    install)        cmd_install ;;
    add-client)     cmd_add_client ;;
    remove-client)  cmd_remove_client ;;
    list-clients)   cmd_list_clients ;;
    get-conf)       cmd_get_conf ;;
    *) err "Unknown sub-command: $1 (use install|add-client|remove-client|list-clients|get-conf)" ;;
esac
