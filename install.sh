#!/usr/bin/env bash
#
# PiTun one-touch installer.
#
# Designed to survive flaky internet during install: every download is
# retried, written to a `.tmp` file first, and only atomically renamed
# on full success — so if the connection drops mid-way and you re-run
# the same command, completed downloads are skipped and only the
# missing/partial ones get retried.
#
# Quick start:
#
#   curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/install.sh | sudo bash
#
# Options (pass after `bash -s --`):
#
#   --version vX.Y.Z       Install a specific release tag (default: latest).
#   --dir PATH             Where to install PiTun (default: /opt/pitun).
#   --build                Force building Docker images from source.
#                          Slower (~25 min on RPi) but doesn't need a
#                          published release. Selected automatically if
#                          no GitHub Release is found.
#   --offline DIR          Use pre-downloaded artifacts from DIR instead
#                          of fetching from the network. Useful for
#                          air-gapped installs.
#   --skip-host-prep       Skip avahi disable / sysctl / modprobe / Docker
#                          install. Use only if you've already prepared
#                          the host yourself.
#   --non-interactive      Don't ask any questions; pick safe defaults.
#                          Required when piping from `curl | bash`.
#   --dry-run              Print every step without executing.
#   --help                 Show this help.
#
# Environment variable equivalents (handy when piping from curl):
#
#   PITUN_VERSION, PITUN_DIR, PITUN_BUILD, PITUN_OFFLINE, PITUN_SKIP_HOST_PREP,
#   PITUN_NON_INTERACTIVE.

set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
GITHUB_REPO="DaveBugg/PiTun"
INSTALL_DIR="${PITUN_DIR:-/opt/pitun}"
VERSION="${PITUN_VERSION:-latest}"
USE_BUILD="${PITUN_BUILD:-0}"
OFFLINE_DIR="${PITUN_OFFLINE:-}"
SKIP_HOST_PREP="${PITUN_SKIP_HOST_PREP:-0}"
NON_INTERACTIVE="${PITUN_NON_INTERACTIVE:-0}"
DRY_RUN=0

# Detect "piped from curl" — implies non-interactive (stdin is the script,
# not a terminal). Without this, any prompt would silently hang.
[[ ! -t 0 ]] && NON_INTERACTIVE=1

# ── Pretty output ────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
step()  { echo -e "${BLUE}[STEP]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*" >&2; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# Wrap a destructive command for --dry-run.
run() {
    if [[ "$DRY_RUN" == "1" ]]; then
        echo "  [dry-run] $*"
    else
        "$@"
    fi
}

# ── Argument parsing ─────────────────────────────────────────────────────────
print_help() {
    sed -n '2,/^set -e/p' "$0" | sed 's/^#\s\?//;/^set -e/d' | sed '1d'
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)         VERSION="$2"; shift 2 ;;
        --dir)             INSTALL_DIR="$2"; shift 2 ;;
        --build)           USE_BUILD=1; shift ;;
        --offline)         OFFLINE_DIR="$2"; shift 2 ;;
        --skip-host-prep)  SKIP_HOST_PREP=1; shift ;;
        --non-interactive) NON_INTERACTIVE=1; shift ;;
        --dry-run)         DRY_RUN=1; shift ;;
        --help|-h)         print_help ;;
        *) error "Unknown option: $1 (use --help)" ;;
    esac
done

# ── Pre-flight checks ────────────────────────────────────────────────────────
[[ $EUID -ne 0 ]] && error "Run as root: sudo bash $0 [...]"

ARCH_RAW="$(uname -m)"
case "$ARCH_RAW" in
    aarch64|arm64) ARCH="arm64" ;;
    x86_64)        ARCH="amd64" ;;
    armv7l)        ARCH="arm"   ;;
    *) error "Unsupported architecture: $ARCH_RAW (need arm64 / amd64 / armv7l)" ;;
esac

DISTRO_ID="unknown"
[[ -f /etc/os-release ]] && DISTRO_ID="$(. /etc/os-release && echo "${ID:-unknown}")"

KERNEL_VER="$(uname -r)"

info "PiTun installer"
info "  Arch:    $ARCH_RAW ($ARCH)"
info "  Distro:  $DISTRO_ID"
info "  Kernel:  $KERNEL_VER"
info "  Target:  $INSTALL_DIR"
info "  Version: $VERSION"
[[ "$DRY_RUN" == "1" ]] && warn "DRY-RUN mode — no changes will be made."

# Sanity warnings for older kernels (TPROXY needs >= 4.19, we suggest 5.4+).
KMAJ=$(echo "$KERNEL_VER" | cut -d. -f1)
KMIN=$(echo "$KERNEL_VER" | cut -d. -f2)
if (( KMAJ < 5 || (KMAJ == 5 && KMIN < 4) )); then
    warn "Kernel $KERNEL_VER is older than 5.4. TPROXY may behave unexpectedly."
fi

# ── Helpers ──────────────────────────────────────────────────────────────────

# Resilient HTTP GET. Retries 5×, resumes partial downloads, writes to .tmp
# then atomically renames on success. If the destination already exists and
# is non-empty, skip — assume previous run finished it.
download() {
    local url="$1" dst="$2" desc="${3:-$(basename "$dst")}"

    if [[ -s "$dst" ]]; then
        info "  ✓ $desc already downloaded (skip)"
        return 0
    fi

    step "Downloading $desc"
    info "  URL: $url"
    info "  ->  $dst"

    if [[ "$DRY_RUN" == "1" ]]; then
        echo "  [dry-run] would download $url -> $dst"
        return 0
    fi

    mkdir -p "$(dirname "$dst")"
    # `--continue-at -` resumes a partial download if the server supports it.
    # `--retry-all-errors` makes the whole retry loop catch transient HTTP 5xx
    # too, not just connection errors.
    curl -fL --progress-bar \
        --retry 5 --retry-delay 5 --retry-all-errors \
        --continue-at - \
        -o "${dst}.tmp" "$url" \
        || { rm -f "${dst}.tmp"; error "Failed to download $desc"; }
    mv "${dst}.tmp" "$dst"
}

# Find the asset URL for a given filename pattern in a release's JSON.
# Pattern is grep-style; first match wins.
asset_url() {
    local release_json="$1" name_pattern="$2"
    grep -oE '"browser_download_url":\s*"[^"]+"' "$release_json" \
        | sed 's/.*"\(http[^"]*\)"/\1/' \
        | grep -E "$name_pattern" \
        | head -n 1
}

# ── Resolve version + asset URLs ─────────────────────────────────────────────
STAGING_DIR="${TMPDIR:-/tmp}/pitun-install"
mkdir -p "$STAGING_DIR"

if [[ -n "$OFFLINE_DIR" ]]; then
    info "Offline mode: using artifacts from $OFFLINE_DIR"
    [[ -d "$OFFLINE_DIR" ]] || error "Offline dir does not exist: $OFFLINE_DIR"
elif [[ "$USE_BUILD" != "1" ]]; then
    # Online release-mode: fetch release metadata.
    if [[ "$VERSION" == "latest" ]]; then
        api_url="https://api.github.com/repos/${GITHUB_REPO}/releases/latest"
    else
        api_url="https://api.github.com/repos/${GITHUB_REPO}/releases/tags/${VERSION}"
    fi
    info "Resolving release: $api_url"
    if ! download "$api_url" "$STAGING_DIR/release.json" "release metadata"; then
        warn "No release found — falling back to build-from-source."
        USE_BUILD=1
    else
        # Pull the actual tag name out so source clone matches.
        RESOLVED_TAG=$(grep -oE '"tag_name":\s*"[^"]+"' "$STAGING_DIR/release.json" \
                        | head -n1 | sed 's/.*"\([^"]*\)"/\1/')
        info "Resolved version: $RESOLVED_TAG"
        VERSION="$RESOLVED_TAG"
    fi
fi

# ── Download phase ───────────────────────────────────────────────────────────
SRC_TARBALL="$STAGING_DIR/pitun-src.tar.gz"
BACKEND_IMG="$STAGING_DIR/pitun-backend.tar.gz"
NAIVE_IMG="$STAGING_DIR/pitun-naive.tar.gz"
FRONTEND_DIST="$STAGING_DIR/pitun-frontend.tar.gz"
GEOIP_DAT="$STAGING_DIR/geoip.dat"
GEOSITE_DAT="$STAGING_DIR/geosite.dat"

if [[ -n "$OFFLINE_DIR" ]]; then
    # Map offline files into staging via symlink so the rest of the script
    # can treat them uniformly. Missing files fall through to "did you
    # download them?" errors at use time.
    for f in pitun-src.tar.gz pitun-backend.tar.gz pitun-naive.tar.gz \
             pitun-frontend.tar.gz geoip.dat geosite.dat; do
        if [[ -e "$OFFLINE_DIR/$f" ]]; then
            ln -sf "$OFFLINE_DIR/$f" "$STAGING_DIR/$f"
        fi
    done
else
    # Source tarball — always needed (we read docker-compose.yml + scripts/
    # from it). Three cases:
    #   - VERSION resolved to a real tag → archive of that tag
    #   - --build with no release at all → fall back to master HEAD
    #   - explicit --version vX.Y.Z → archive of that tag
    if [[ "$VERSION" == "latest" ]]; then
        # Got here because USE_BUILD was set explicitly and we skipped
        # release resolution — there's no resolved tag to download an
        # archive from. Use the master branch instead.
        info "No version resolved — using master branch tarball"
        src_url="https://codeload.github.com/${GITHUB_REPO}/tar.gz/refs/heads/master"
        SRC_DESC="PiTun source (master)"
    else
        src_url="https://codeload.github.com/${GITHUB_REPO}/tar.gz/refs/tags/${VERSION}"
        SRC_DESC="PiTun source ($VERSION)"
    fi
    download "$src_url" "$SRC_TARBALL" "$SRC_DESC"

    if [[ "$USE_BUILD" != "1" ]]; then
        # Pre-built images and dist from the release. Asset names follow
        # the convention enforced by .github/workflows/release.yml.
        be_url=$(asset_url "$STAGING_DIR/release.json" "pitun-backend-.*-${ARCH}\.tar\.gz$") || true
        nv_url=$(asset_url "$STAGING_DIR/release.json" "pitun-naive-.*-${ARCH}\.tar\.gz$") || true
        fe_url=$(asset_url "$STAGING_DIR/release.json" "pitun-frontend-.*\.tar\.gz$") || true

        if [[ -z "$be_url" || -z "$nv_url" || -z "$fe_url" ]]; then
            warn "Release $VERSION is missing one or more arch-specific assets ($ARCH)."
            warn "  backend:  ${be_url:-MISSING}"
            warn "  naive:    ${nv_url:-MISSING}"
            warn "  frontend: ${fe_url:-MISSING}"
            warn "Falling back to build-from-source."
            USE_BUILD=1
        else
            download "$be_url" "$BACKEND_IMG" "backend image (linux/$ARCH)"
            download "$nv_url" "$NAIVE_IMG"   "naive image (linux/$ARCH)"
            download "$fe_url" "$FRONTEND_DIST" "frontend dist"
        fi
    fi

    # GeoIP / GeoSite databases (bind-mounted into the backend container
    # for on-demand refresh from the UI). The xray binary itself is
    # bundled inside the backend image as of v1.2.0 — no separate
    # download for it here.
    download "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat" \
             "$GEOIP_DAT" "geoip.dat"
    download "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat" \
             "$GEOSITE_DAT" "geosite.dat"
fi

info "All downloads complete. Internet may go down now — install continues offline."

# ── Host prep ────────────────────────────────────────────────────────────────
if [[ "$SKIP_HOST_PREP" != "1" ]]; then
    step "Preparing host"

    info "Installing system packages…"
    run apt-get update -qq
    run apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg lsb-release \
        nftables iproute2 net-tools iptables \
        arp-scan dnsutils unzip jq cron

    # avahi-daemon binds UDP/5353 — same port xray uses for DNS forwarding.
    if systemctl is-active --quiet avahi-daemon 2>/dev/null; then
        info "Disabling avahi-daemon (frees UDP/5353)…"
        run systemctl stop avahi-daemon avahi-daemon.socket || true
        run systemctl disable avahi-daemon avahi-daemon.socket || true
        run systemctl mask avahi-daemon || true
    fi

    info "Configuring sysctl (IP forwarding + TPROXY loopback)…"
    if [[ "$DRY_RUN" != "1" ]]; then
        cat > /etc/sysctl.d/99-pitun.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.ipv4.conf.all.route_localnet = 1
EOF
        sysctl -p /etc/sysctl.d/99-pitun.conf >/dev/null
    fi

    info "Loading TPROXY kernel modules…"
    run modprobe nft_tproxy 2>/dev/null || true
    run modprobe xt_TPROXY  2>/dev/null || true
    if [[ "$DRY_RUN" != "1" ]]; then
        echo -e "nft_tproxy\nxt_TPROXY" > /etc/modules-load.d/pitun.conf
    fi

    if ! command -v docker &>/dev/null; then
        info "Installing Docker (via get.docker.com)…"
        # `get.docker.com` is the official auto-detecting installer.
        # Idempotent: re-running on a host with Docker already installed
        # is fine, but we skip the curl entirely above to be safe.
        run sh -c "curl -fsSL https://get.docker.com | sh"
        run systemctl enable --now docker
    else
        info "Docker already installed: $(docker --version)"
    fi

    # Docker log rotation — without this, `docker logs` will eat disk space
    # over months (xray + DNS query log are chatty).
    if [[ ! -f /etc/docker/daemon.json ]]; then
        info "Configuring Docker log rotation (10m × 3)…"
        if [[ "$DRY_RUN" != "1" ]]; then
            mkdir -p /etc/docker
            cat > /etc/docker/daemon.json <<'EOF'
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
            systemctl restart docker || true
        fi
    fi
else
    info "Skipping host prep (--skip-host-prep)"
fi

# ── Install GeoIP / GeoSite data ─────────────────────────────────────────────
# As of v1.2.0 the xray binary is bundled inside the backend image, so this
# step only places the geo databases (which stay on the host so the user
# can refresh them from the UI without rebuilding the image).
step "Installing GeoIP/GeoSite data"
mkdir -p /usr/local/share/xray
if [[ "$DRY_RUN" != "1" ]]; then
    cp "$GEOIP_DAT"   /usr/local/share/xray/geoip.dat
    cp "$GEOSITE_DAT" /usr/local/share/xray/geosite.dat
fi

# Migration cleanup: prior versions installed an xray binary at
# /usr/local/bin/xray on the host (bind-mounted into the container
# read-only). v1.2.0 ships xray inside the backend image and removes
# that bind-mount, so the host file is no longer used. Remove it on
# upgrade to keep the system tidy. Stays a no-op on fresh installs.
if [[ -f /usr/local/bin/xray && "$DRY_RUN" != "1" ]]; then
    info "Removing legacy host-side xray binary (now bundled in image)"
    rm -f /usr/local/bin/xray
fi

# ── Extract source ───────────────────────────────────────────────────────────
step "Installing PiTun source to $INSTALL_DIR"
if [[ "$DRY_RUN" != "1" ]]; then
    mkdir -p "$INSTALL_DIR"
    # Strip the top-level dir from the tarball (PiTun-x.y.z/ → /).
    tar -xzf "$SRC_TARBALL" -C "$INSTALL_DIR" --strip-components=1
fi

# ── Load Docker images (release mode) ────────────────────────────────────────
if [[ "$USE_BUILD" != "1" ]]; then
    step "Loading pre-built Docker images (no build needed)"
    if [[ "$DRY_RUN" != "1" ]]; then
        # Capture the loaded image's tag from `docker load` stdout
        # ("Loaded image: pitun-backend:v1.1.0") and retag to :latest.
        # The compose file references `pitun-backend:latest`; without
        # this retag, compose either uses a stale `:latest` from a
        # prior build (wrong code) or — on a fresh device with no
        # `:latest` at all — falls back to `build:` which needs
        # internet for pip + npm pulls. Both break offline install.
        be_loaded=$(docker load < "$BACKEND_IMG" | sed -n 's/^Loaded image: //p' | head -n1)
        nv_loaded=$(docker load < "$NAIVE_IMG"   | sed -n 's/^Loaded image: //p' | head -n1)
        info "  Loaded backend: ${be_loaded:-<unknown>}"
        info "  Loaded naive:   ${nv_loaded:-<unknown>}"
        if [[ -n "$be_loaded" && "$be_loaded" != "pitun-backend:latest" ]]; then
            docker tag "$be_loaded" pitun-backend:latest
            info "  Re-tagged $be_loaded → pitun-backend:latest"
        fi
        if [[ -n "$nv_loaded" && "$nv_loaded" != "pitun-naive:latest" ]]; then
            docker tag "$nv_loaded" pitun-naive:latest
            info "  Re-tagged $nv_loaded → pitun-naive:latest"
        fi

        # Frontend dist is just a tarball of static files — extract straight
        # into the bind-mount path the compose file expects.
        mkdir -p "$INSTALL_DIR/frontend/dist"
        tar -xzf "$FRONTEND_DIST" -C "$INSTALL_DIR/frontend/dist"
    fi
fi

# ── Generate .env ────────────────────────────────────────────────────────────
step "Generating .env"
if [[ "$DRY_RUN" != "1" ]]; then
    cd "$INSTALL_DIR"
    if [[ ! -f .env ]]; then
        cp .env.example .env
        # Inject a strong SECRET_KEY (idempotent: only on first generation).
        SECRET_KEY="$(openssl rand -hex 32)"
        sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$SECRET_KEY/" .env

        # Best-effort autodetect of the LAN interface — pick the first
        # non-loopback, non-docker, non-virtual interface with a default
        # route. The user is expected to verify before relying on it.
        DEFAULT_IF=$(ip -o -4 route show to default 2>/dev/null \
                      | awk '{print $5}' | head -n1)
        if [[ -n "$DEFAULT_IF" ]]; then
            sed -i "s/^INTERFACE=.*/INTERFACE=$DEFAULT_IF/" .env
            info "  Autodetected INTERFACE=$DEFAULT_IF"
        fi

        warn "Edit $INSTALL_DIR/.env before going to production:"
        warn "  - LAN_CIDR  (your home network, e.g. 192.168.1.0/24)"
        warn "  - GATEWAY_IP (your home router's IP, e.g. 192.168.1.1)"
        warn "  - INTERFACE (verify the autodetected value: $DEFAULT_IF)"
    else
        info ".env already exists, leaving it alone"
    fi
fi

# ── Bring it up ──────────────────────────────────────────────────────────────
step "Starting Docker stack"
if [[ "$DRY_RUN" != "1" ]]; then
    cd "$INSTALL_DIR"
    if [[ "$USE_BUILD" == "1" ]]; then
        # Source-build path: needs constant internet for pip + npm pulls.
        warn "Build mode — Docker will rebuild images. This needs reliable internet."
        docker compose up -d --build
    else
        docker compose up -d
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
info "PiTun is up."
echo ""
echo -e "${GREEN}Web UI:${NC}  http://${HOST_IP:-<this-host>}/"
echo -e "${GREEN}Login:${NC}   admin / admin  (change on first login)"
echo ""
echo -e "${YELLOW}Next steps:${NC}"
echo "  1. Edit $INSTALL_DIR/.env if you haven't (LAN_CIDR / GATEWAY_IP / INTERFACE)"
echo "  2. Set the host's LAN IP as static (not DHCP)"
echo "  3. Point your devices' default gateway at this host"
echo ""
echo "Logs:    docker compose -f $INSTALL_DIR/docker-compose.yml logs -f"
echo "Restart: docker compose -f $INSTALL_DIR/docker-compose.yml restart"
echo "Update:  re-run this installer with --version vX.Y.Z"
