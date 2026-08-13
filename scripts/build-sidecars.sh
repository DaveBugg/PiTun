#!/bin/bash
# ============================================================================
# PiTun — build the router-mode sidecar images
# ============================================================================
# dnsmasq (DHCP) and hostapd (WiFi AP) run as containers, but they are NOT
# services in docker-compose.yml: the backend starts and stops them on demand,
# because they only exist while router mode is on. `docker compose up` therefore
# never builds them, and every install path has to do it explicitly.
#
# It lived inline in 03-deploy.sh, which meant boxes installed through
# install.sh — the one-liner, and what pitun-update.sh hands off to — never got
# the images at all: router mode's first enable failed on a missing image.
# One copy, called from both.
#
# Failures warn rather than abort. A box that cannot build these is still a
# perfectly good proxy in gateway mode; only router mode is unavailable, and
# the backend says so clearly when you try to enable it.
#
# Usage: bash build-sidecars.sh [PITUN_DIR] [DOCKER_CMD]
# ============================================================================
set -uo pipefail

PITUN_DIR="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
DOCKER_CMD="${2:-docker}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log()  { echo -e "${GREEN}[+]${NC} $*"; }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }

for sidecar in dnsmasq hostapd; do
    dir="$PITUN_DIR/docker/$sidecar"
    if [ ! -d "$dir" ]; then
        warn "docker/$sidecar not found — skipping (router mode will not be able to start it)"
        continue
    fi
    if $DOCKER_CMD image inspect "pitun-$sidecar:latest" >/dev/null 2>&1; then
        log "pitun-$sidecar image already present"
        continue
    fi
    log "Building pitun-$sidecar sidecar image..."
    if ! $DOCKER_CMD build -q -t "pitun-$sidecar:latest" "$dir" >/dev/null 2>&1; then
        warn "Failed to build pitun-$sidecar — router mode will not be able to start it"
    fi
done
