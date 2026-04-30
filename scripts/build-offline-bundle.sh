#!/bin/bash
# ============================================================================
# PiTun — Build offline deployment bundle
# ============================================================================
# Builds pitun-backend, pitun-frontend, pitun-naive images AND re-exports
# the two third-party base images (nginx, docker-socket-proxy), then saves
# all five as compressed tarballs under ./docker/offline/.
#
# Tarball naming:
#     <name>-<arch>-<version>.tar.gz
# e.g. pitun-backend-arm64-1.0.1.tar.gz, nginx-amd64-1.0.1.tar.gz.
# The `<version>` is the PiTun release version (read from
# backend/app/config.py:APP_VERSION by default). It tags the bundle as a
# unit — the upstream nginx/socket-proxy tags inside the tarballs are
# unchanged.
#
# Run this on a machine with internet access + Docker (or Docker Desktop).
# The resulting bundle can be shipped to airgapped devices via scp +
# scripts/deploy-offline.sh.
#
# Env vars:
#   ARCH      arm64 (default) or amd64
#   VERSION   PiTun bundle version. Default: read APP_VERSION from backend.
#   MIRROR    Docker Hub mirror for base images. Default: mirror.gcr.io
#   BUILDER   Buildx builder name.                 Default: pitun-builder
# ============================================================================

set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$PWD"
ARCH="${ARCH:-arm64}"
PLATFORM="linux/${ARCH}"
OFFLINE_DIR="${ROOT}/docker/offline"
BUILDER="${BUILDER:-pitun-builder}"

# Derive default version from backend/app/config.py:APP_VERSION. Keeps the
# bundle version in sync with what the running app reports. Can be
# overridden by setting VERSION= explicitly.
default_version() {
    local cfg="${ROOT}/backend/app/config.py"
    if [ -f "$cfg" ]; then
        grep -oE '^APP_VERSION\s*=\s*"[^"]+"' "$cfg" | head -1 \
            | sed -E 's/.*"([^"]+)".*/\1/'
    fi
}
VERSION="${VERSION:-$(default_version)}"
[ -n "$VERSION" ] || { echo "[x] cannot determine VERSION (APP_VERSION not found, set VERSION=...)" >&2; exit 1; }

MIRROR="${MIRROR:-mirror.gcr.io}"

G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; N='\033[0m'
log()  { echo -e "${G}[+]${N} $*"; }
warn() { echo -e "${Y}[!]${N} $*"; }
err()  { echo -e "${R}[x]${N} $*" >&2; exit 1; }

command -v docker >/dev/null || err "docker not found"
docker version >/dev/null || err "docker daemon not running"

log "Target platform: ${PLATFORM}"
log "Bundle version:  ${VERSION}"
log "Registry mirror: ${MIRROR}"

mkdir -p "${OFFLINE_DIR}"

# ── 1. Ensure buildx builder exists ────────────────────────────────────────
if ! docker buildx inspect "${BUILDER}" >/dev/null 2>&1; then
    log "Creating buildx builder '${BUILDER}'..."
    docker buildx create --name "${BUILDER}" --platform "${PLATFORM}" --use
fi
docker buildx use "${BUILDER}"

# ── 2. Build application images ────────────────────────────────────────────
# Each image gets two tags: one versioned+arched (for traceability / to
# keep multiple arch+version builds around on a dev machine) and one
# `latest` (for the compose file on the target which refers to `:latest`).
log "Building pitun-backend (${PLATFORM}) v${VERSION}..."
docker buildx build --platform "${PLATFORM}" \
    --build-arg "PYTHON_IMAGE=${MIRROR}/library/python:3.11-slim" \
    -f backend/Dockerfile --target production \
    -t "pitun-backend:${VERSION}-${ARCH}" \
    -t "pitun-backend:latest-${ARCH}" \
    --load backend/

log "Building pitun-naive (${PLATFORM}) v${VERSION}..."
docker buildx build --platform "${PLATFORM}" \
    --build-arg "ALPINE_IMAGE=${MIRROR}/library/alpine:3.20" \
    -f docker/naive/Dockerfile \
    -t "pitun-naive:${VERSION}-${ARCH}" \
    -t "pitun-naive:latest-${ARCH}" \
    --load docker/naive/

log "Building pitun-frontend (${PLATFORM}) v${VERSION}..."
docker buildx build --platform "${PLATFORM}" \
    --build-arg "NODE_IMAGE=${MIRROR}/library/node:20-alpine" \
    --build-arg "NGINX_IMAGE=${MIRROR}/library/nginx:1.25-alpine" \
    -f frontend/Dockerfile \
    -t "pitun-frontend:${VERSION}-${ARCH}" \
    -t "pitun-frontend:latest-${ARCH}" \
    --load frontend/

# ── 3. Re-export base images with correct architecture ────────────────────
# Direct `docker pull --platform` on Docker Desktop often returns the host's
# native arch from cache. We use a one-line Dockerfile + buildx to force the
# correct manifest selection.
reexport() {
    local src="$1" dst="$2"
    # Skip if we already have the destination tag cached locally — saves
    # a round-trip to the mirror, and keeps the build working when the
    # mirror is flaky (huecker.io has intermittent 500s). We trust that
    # if `dst` is present its platform is already correct (it was created
    # by a prior successful run of this script).
    if docker image inspect "${dst}" >/dev/null 2>&1; then
        log "  (using cached ${dst})"
        return 0
    fi
    local tmp
    tmp="$(mktemp -d)"
    echo "FROM ${src}" > "${tmp}/Dockerfile"
    docker buildx build --platform "${PLATFORM}" -f "${tmp}/Dockerfile" \
        -t "${dst}" --load "${tmp}"
    rm -rf "${tmp}"
}

log "Exporting nginx:1.25-alpine (${PLATFORM})..."
reexport "${MIRROR}/library/nginx:1.25-alpine" "nginx-${ARCH}:1.25-alpine"

log "Exporting tecnativa/docker-socket-proxy:0.3 (${PLATFORM})..."
# docker-socket-proxy is NOT on mirror.gcr.io (only library/* is mirrored).
# Fall back to huecker.io which mirrors the entire Docker Hub.
reexport "huecker.io/tecnativa/docker-socket-proxy:0.3" "docker-socket-proxy-${ARCH}:0.3"

# ── 4. Save tarballs ──────────────────────────────────────────────────────
# Versioned filenames let us keep multiple releases side-by-side in
# docker/offline/ and tell at a glance what PiTun release produced each
# image set. scripts/03-deploy.sh globs `*.tar.gz` so the new names are
# picked up automatically.
log "Saving image tarballs to ${OFFLINE_DIR}/ ..."
docker save "pitun-backend:${VERSION}-${ARCH}"   | gzip > "${OFFLINE_DIR}/pitun-backend-${ARCH}-${VERSION}.tar.gz"
docker save "pitun-naive:${VERSION}-${ARCH}"     | gzip > "${OFFLINE_DIR}/pitun-naive-${ARCH}-${VERSION}.tar.gz"
docker save "pitun-frontend:${VERSION}-${ARCH}"  | gzip > "${OFFLINE_DIR}/pitun-frontend-${ARCH}-${VERSION}.tar.gz"
docker save "nginx-${ARCH}:1.25-alpine"          | gzip > "${OFFLINE_DIR}/nginx-${ARCH}-${VERSION}.tar.gz"
docker save "docker-socket-proxy-${ARCH}:0.3"    | gzip > "${OFFLINE_DIR}/docker-socket-proxy-${ARCH}-${VERSION}.tar.gz"

echo
log "Bundle ready (version ${VERSION}, arch ${ARCH}):"
ls -lh "${OFFLINE_DIR}/"*"-${ARCH}-${VERSION}.tar.gz" | awk '{print "    "$0}'
echo
log "Next step: scripts/deploy-offline.sh <user@rpi>"
