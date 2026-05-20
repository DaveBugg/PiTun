#!/usr/bin/env bash
# scripts/make-offline-bundle.sh
#
# Bundle every file the installer needs for an offline / air-gapped PiTun
# install into a single directory you can scp to the target box.
#
# The output dir is the "drop next to install.sh" workflow target — the
# installer's auto-detect (install.sh ~line 400) picks up canonical
# filenames AND Release-asset-style suffixed names (since v1.3.3).
#
# What lands in the bundle:
#   * pitun-backend-<ver>-<arch>.tar.gz   ← gh release asset
#   * pitun-naive-<ver>-<arch>.tar.gz     ← gh release asset
#   * pitun-frontend-<ver>.tar.gz         ← gh release asset (arch-less)
#   * pitun-src.tar.gz                    ← codeload (auto-generated)
#   * geoip.dat                           ← Loyalsoldier latest
#   * geosite.dat                         ← Loyalsoldier latest
#   * install.sh                          ← raw.githubusercontent.com (or local)
#
# Usage:
#   scripts/make-offline-bundle.sh [--version vX.Y.Z] [--arch amd64|arm64]
#                                  [--output DIR] [--tar] [--no-geo]
#                                  [--local-install-sh PATH]
#
# Defaults: latest stable release, host arch (via uname -m), ./pitun-offline-<ver>-<arch>.
#
# Examples:
#   # Build a bundle for the current host architecture, latest release
#   scripts/make-offline-bundle.sh
#
#   # Cross-arch bundle for an arm64 Raspberry Pi, pinned version, tarball'd
#   scripts/make-offline-bundle.sh --version v1.3.2 --arch arm64 --tar
#
#   # Use a local install.sh (handy when iterating on the installer itself)
#   scripts/make-offline-bundle.sh --local-install-sh ./install.sh
#
# Dependencies: curl, jq, tar. No gh CLI required — we hit the GitHub API directly.

set -euo pipefail

# ── Config ─────────────────────────────────────────────────────────────────
GITHUB_REPO="DaveBugg/PiTun"
GEO_REPO_URL="https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download"

VERSION=""
ARCH=""
OUTPUT_DIR=""
MAKE_TAR=0
SKIP_GEO=0
LOCAL_INSTALL_SH=""

# ── Helpers ────────────────────────────────────────────────────────────────
info()  { printf '\033[1;36m[INFO]\033[0m  %s\n' "$*"; }
warn()  { printf '\033[1;33m[WARN]\033[0m  %s\n' "$*" >&2; }
error() { printf '\033[1;31m[ERR]\033[0m   %s\n' "$*" >&2; exit 1; }
step()  { printf '\033[1;32m[STEP]\033[0m  %s\n' "$*"; }

usage() {
    sed -n '2,/^set -euo/p' "$0" | sed 's/^# \{0,1\}//;/^set/d'
    exit "${1:-0}"
}

# ── Args ───────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)             VERSION="$2"; shift 2 ;;
        --arch)                ARCH="$2"; shift 2 ;;
        --output)              OUTPUT_DIR="$2"; shift 2 ;;
        --tar)                 MAKE_TAR=1; shift ;;
        --no-geo)              SKIP_GEO=1; shift ;;
        --local-install-sh)    LOCAL_INSTALL_SH="$2"; shift 2 ;;
        -h|--help)             usage 0 ;;
        *)                     error "Unknown flag: $1 (see --help)" ;;
    esac
done

# Tool check — fail early with a clear message.
for tool in curl jq tar; do
    command -v "$tool" >/dev/null 2>&1 \
        || error "'$tool' is required but not installed."
done

# ── Resolve VERSION ────────────────────────────────────────────────────────
if [[ -z "$VERSION" ]]; then
    step "Resolving latest stable release from GitHub"
    VERSION=$(curl -fsSL "https://api.github.com/repos/${GITHUB_REPO}/releases/latest" \
        | jq -r '.tag_name')
    [[ -n "$VERSION" && "$VERSION" != "null" ]] \
        || error "Failed to resolve latest version from GitHub API"
    info "  latest: $VERSION"
fi

# ── Resolve ARCH ───────────────────────────────────────────────────────────
if [[ -z "$ARCH" ]]; then
    case "$(uname -m)" in
        x86_64|amd64)   ARCH=amd64 ;;
        aarch64|arm64)  ARCH=arm64 ;;
        *)              error "Unsupported host architecture: $(uname -m). Pass --arch amd64|arm64 explicitly." ;;
    esac
fi
case "$ARCH" in
    amd64|arm64) ;;
    *)           error "Invalid --arch: $ARCH (use amd64 or arm64)" ;;
esac

# ── Output dir ─────────────────────────────────────────────────────────────
if [[ -z "$OUTPUT_DIR" ]]; then
    OUTPUT_DIR="./pitun-offline-${VERSION}-${ARCH}"
fi
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"   # absolute path for nicer messages

info "Bundle target: $OUTPUT_DIR"
info "  version: $VERSION"
info "  arch:    $ARCH"

# ── Download release assets ────────────────────────────────────────────────
# Use the API to find direct asset URLs — avoids hardcoding the download
# host (api.github.com → release.assets[].browser_download_url) and lets
# users self-host or mirror without code changes.
step "Fetching release metadata for $VERSION"
RELEASE_JSON=$(curl -fsSL \
    "https://api.github.com/repos/${GITHUB_REPO}/releases/tags/${VERSION}")
[[ -n "$RELEASE_JSON" ]] || error "GitHub API returned empty for $VERSION"

# Asset lookup. Pattern follows the release.yml workflow's naming:
#   pitun-backend-<ver>-<arch>.tar.gz
#   pitun-naive-<ver>-<arch>.tar.gz
#   pitun-frontend-<ver>.tar.gz   (no arch)
asset_url() {
    local pattern="$1"
    echo "$RELEASE_JSON" \
        | jq -r --arg p "$pattern" \
            '.assets[] | select(.name | test($p)) | .browser_download_url' \
        | head -n1
}

# Each download is "skip if already on disk" — re-running the script
# (e.g. after a network blip) doesn't re-fetch the 95 MB backend image.
fetch() {
    local url="$1" out="$2" label="$3"
    if [[ -e "$out" ]]; then
        info "  $label already present, skipping download"
        return 0
    fi
    [[ -n "$url" ]] || error "$label URL is empty — release $VERSION may be missing this asset"
    info "  fetching $label"
    info "    $url"
    curl -fL --progress-bar -o "${out}.tmp" "$url"
    mv "${out}.tmp" "$out"
}

step "Downloading release assets"
BE_URL=$(asset_url "^pitun-backend-.*-${ARCH}\\.tar\\.gz$")
NV_URL=$(asset_url "^pitun-naive-.*-${ARCH}\\.tar\\.gz$")
FE_URL=$(asset_url "^pitun-frontend-.*\\.tar\\.gz$")

fetch "$BE_URL" "$OUTPUT_DIR/pitun-backend-${VERSION}-${ARCH}.tar.gz" "backend image"
fetch "$NV_URL" "$OUTPUT_DIR/pitun-naive-${VERSION}-${ARCH}.tar.gz"   "naive image"
fetch "$FE_URL" "$OUTPUT_DIR/pitun-frontend-${VERSION}.tar.gz"        "frontend dist"

# ── Source tarball ─────────────────────────────────────────────────────────
# Not a release asset — comes from codeload (GitHub's auto-generated
# source archive for the tag). Same URL install.sh uses when downloading
# online, so keeping this dependency keeps the bundle identical to a
# regular install.
step "Downloading source tarball"
SRC_URL="https://codeload.github.com/${GITHUB_REPO}/tar.gz/refs/tags/${VERSION}"
fetch "$SRC_URL" "$OUTPUT_DIR/pitun-src.tar.gz" "source ($VERSION)"

# ── Geo data ───────────────────────────────────────────────────────────────
# Loyalsoldier ships flat-named files. They're version-agnostic on the
# PiTun side — updates are daily, so we always pull the freshest at
# bundle-build time. The user can refresh again later from the UI.
if (( SKIP_GEO == 0 )); then
    step "Downloading geo data (Loyalsoldier latest)"
    fetch "${GEO_REPO_URL}/geoip.dat"   "$OUTPUT_DIR/geoip.dat"   "geoip.dat"
    fetch "${GEO_REPO_URL}/geosite.dat" "$OUTPUT_DIR/geosite.dat" "geosite.dat"
else
    info "Skipping geo data (--no-geo). Bundle will be HYBRID — installer downloads geoip/geosite at run time."
fi

# ── install.sh ─────────────────────────────────────────────────────────────
# Either copy a local file (for installer development) or pull the
# version-pinned copy from raw.githubusercontent.com so the bundle
# is reproducible.
step "Adding install.sh"
if [[ -n "$LOCAL_INSTALL_SH" ]]; then
    [[ -f "$LOCAL_INSTALL_SH" ]] || error "Local install.sh not found: $LOCAL_INSTALL_SH"
    cp "$LOCAL_INSTALL_SH" "$OUTPUT_DIR/install.sh"
    info "  used local: $LOCAL_INSTALL_SH"
else
    # Pin to the tag — gives a reproducible bundle. If the user wants the
    # bleeding-edge installer, they can pass --local-install-sh.
    INSTALL_SH_URL="https://raw.githubusercontent.com/${GITHUB_REPO}/${VERSION}/install.sh"
    curl -fL --progress-bar -o "$OUTPUT_DIR/install.sh.tmp" "$INSTALL_SH_URL"
    mv "$OUTPUT_DIR/install.sh.tmp" "$OUTPUT_DIR/install.sh"
    info "  fetched: $INSTALL_SH_URL"
fi
chmod +x "$OUTPUT_DIR/install.sh"

# ── Optional tarball ───────────────────────────────────────────────────────
if (( MAKE_TAR == 1 )); then
    step "Packing bundle into a single tarball"
    TARBALL="${OUTPUT_DIR%/}.tar.gz"
    tar -czf "$TARBALL" -C "$(dirname "$OUTPUT_DIR")" "$(basename "$OUTPUT_DIR")"
    info "  $(ls -lh "$TARBALL" | awk '{print $5}')  $TARBALL"
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo
info "Bundle complete:"
ls -lh "$OUTPUT_DIR" | awk 'NR>1 {printf "    %8s  %s\n", $5, $NF}'

cat <<EOF

Next steps:
  1. Copy the directory to your target box, e.g.:
       scp -r "$OUTPUT_DIR" root@<host>:/root/
  2. Run the installer there:
       ssh root@<host> "cd /root/$(basename "$OUTPUT_DIR") && ./install.sh"
     (auto-detect picks up the local artefacts — no network needed unless
      a file is missing.)

EOF
