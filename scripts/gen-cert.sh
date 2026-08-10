#!/bin/bash
# ============================================================================
# PiTun HTTPS certificate generator
# ============================================================================
# Creates a per-install local CA and a leaf certificate for the panel so the
# reverse-proxy nginx can serve the UI over TLS (443) alongside plain HTTP (80).
#
#   - The root CA (pitun-ca.crt) is created ONCE and reused. nginx serves it
#     for download at http(s)://<box>/pitun-ca.crt; installing it in your
#     OS/browser trust store makes the panel cert trusted (no warning).
#   - The leaf cert (pitun.crt) is (re)issued when missing or when the box's
#     LAN IP changed — browsers match the SAN, not the CN, so the IP must be
#     in the SAN. Unique key material per install.
#
# Idempotent: safe to run on every deploy. MUST run BEFORE nginx starts —
# nginx with `listen 443 ssl` aborts if the cert files are absent.
#
# Usage:  gen-cert.sh [LAN_IP]
#   LAN_IP defaults to GATEWAY_IP from /opt/pitun/.env, else the src IP of the
#   default route, else 127.0.0.1.
# ============================================================================
set -euo pipefail

CERT_DIR="${PITUN_CERT_DIR:-/etc/pitun/certs}"
CA_KEY="$CERT_DIR/pitun-ca.key"
CA_CRT="$CERT_DIR/pitun-ca.crt"
SRV_KEY="$CERT_DIR/pitun.key"
SRV_CRT="$CERT_DIR/pitun.crt"
ENV_FILE="${PITUN_ENV_FILE:-/opt/pitun/.env}"

# ── Resolve the panel's LAN IP for the certificate SAN ──
LAN_IP="${1:-}"
if [ -z "$LAN_IP" ] && [ -f "$ENV_FILE" ]; then
    LAN_IP=$(grep -E '^GATEWAY_IP=' "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2 | tr -d '"'"'"' \r\n' || true)
fi
if [ -z "$LAN_IP" ]; then
    LAN_IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)
fi
[ -z "$LAN_IP" ] && LAN_IP="127.0.0.1"

mkdir -p "$CERT_DIR"
chmod 755 "$CERT_DIR"

# ── 1. Root CA — created once, reused forever ──
if [ ! -s "$CA_CRT" ] || [ ! -s "$CA_KEY" ]; then
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
        -keyout "$CA_KEY" -out "$CA_CRT" \
        -subj "/O=PiTun/CN=PiTun Local CA" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" >/dev/null 2>&1
    echo "[gen-cert] created PiTun root CA"
fi

# ── 2. Leaf cert — (re)issue when missing or IP not covered ──
need_leaf=0
if [ ! -s "$SRV_CRT" ] || [ ! -s "$SRV_KEY" ]; then
    need_leaf=1
elif ! openssl x509 -in "$SRV_CRT" -noout -text 2>/dev/null \
        | grep -oE 'IP Address:[0-9.]+' | grep -Fxq "IP Address:$LAN_IP"; then
    need_leaf=1
    echo "[gen-cert] panel IP changed to $LAN_IP — re-issuing cert"
fi

if [ "$need_leaf" = 1 ]; then
    san="DNS:pitun,DNS:pitun.local,DNS:localhost,IP:127.0.0.1"
    [ "$LAN_IP" != "127.0.0.1" ] && san="$san,IP:$LAN_IP"
    csr="$CERT_DIR/pitun.csr"
    openssl req -newkey rsa:2048 -nodes -keyout "$SRV_KEY" -out "$csr" \
        -subj "/O=PiTun/CN=PiTun Panel" >/dev/null 2>&1
    openssl x509 -req -in "$csr" -CA "$CA_CRT" -CAkey "$CA_KEY" -CAcreateserial \
        -days 3650 -out "$SRV_CRT" \
        -extfile <(printf 'subjectAltName=%s\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n' "$san") \
        >/dev/null 2>&1
    rm -f "$csr"
    echo "[gen-cert] issued panel cert (SAN: $san)"
fi

# Keys private, certs world-readable (nginx serves the CA cert for download).
chmod 600 "$CA_KEY" "$SRV_KEY" 2>/dev/null || true
chmod 644 "$CA_CRT" "$SRV_CRT" 2>/dev/null || true
