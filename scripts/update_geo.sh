#!/usr/bin/env bash
# Update geoip.dat and geosite.dat from Loyalsoldier releases
# Can be run standalone or via cron

set -euo pipefail

ASSET_DIR="${XRAY_ASSET_DIR:-/usr/local/share/xray}"
GEOIP_URL="${GEOIP_URL:-https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat}"
GEOSITE_URL="${GEOSITE_URL:-https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat}"

mkdir -p "$ASSET_DIR"

for spec in "geoip.dat:$GEOIP_URL" "geosite.dat:$GEOSITE_URL"; do
    file="${spec%%:*}"
    url="${spec#*:}"
    echo "Downloading ${file} from ${url}…"
    curl -fsSL "$url" -o "${ASSET_DIR}/${file}.tmp"
    mv "${ASSET_DIR}/${file}.tmp" "${ASSET_DIR}/${file}"
    echo "  -> ${ASSET_DIR}/${file} ($(du -sh "${ASSET_DIR}/${file}" | cut -f1))"
done

echo "GeoData updated successfully."

# Optionally reload xray via PiTun API
if [[ -n "${PITUN_API:-}" ]]; then
    curl -s -X POST "${PITUN_API}/api/system/reload-config" && echo "xray config reloaded."
fi
