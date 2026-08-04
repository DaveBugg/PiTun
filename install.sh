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
# ─── Quick start (no flags — installs the latest release) ──────────────────
#
#   curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/install.sh | sudo bash
#
# ─── Install a specific version ────────────────────────────────────────────
#
# THREE WORKING FORMS — pick whichever you find easiest. ANY of them works,
# but the FIRST is the most foolproof and the one we recommend:
#
#   # 1. Download to a temp file, then run with the flag.
#   #    Recommended — no shell-syntax pitfalls.
#   curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/install.sh \
#        -o /tmp/pitun-install.sh
#   sudo bash /tmp/pitun-install.sh --version v1.2.7
#
#   # 2. Pipe-form with `bash -s --` separator (REQUIRED to pass flags).
#   curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/install.sh \
#        | sudo bash -s -- --version v1.2.7
#
#   # 3. Environment variable (works without `-s --`).
#   curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/install.sh \
#        | sudo PITUN_VERSION=v1.2.7 bash
#
# ─── COMMON MISTAKE — DO NOT do this: ──────────────────────────────────────
#
#   curl ... | sudo bash --version v1.2.7      ❌ WRONG
#
# `--version` is interpreted by BASH ITSELF (prints bash's version + exits)
# before our installer ever runs. You'll see GNU bash copyright text and
# nothing else. Use one of the three forms above instead.
#
# ─── Options ───────────────────────────────────────────────────────────────
#
#   --version vX.Y.Z       Install a specific release tag (default: latest).
#   --dir PATH             Where to install PiTun (default: /opt/pitun).
#   --build                Force building Docker images from source.
#                          Slower (~25 min on RPi) but doesn't need a
#                          published release. Selected automatically if
#                          no GitHub Release is found.
#   --offline DIR          Use pre-downloaded artifacts from DIR. The
#                          script picks them up in HYBRID mode — any
#                          file present in DIR is used as-is, anything
#                          missing is downloaded as usual. Useful for
#                          air-gapped installs (drop all six artefacts
#                          + run with `--offline .`) and for "I already
#                          have the geo files" rerun cases.
#                          Auto-detected when install.sh is launched
#                          from a directory that already contains any
#                          of the six expected filenames — explicit
#                          `--offline` overrides the auto-detect.
#                          Expected filenames:
#                            pitun-src.tar.gz
#                            pitun-backend.tar.gz
#                            pitun-naive.tar.gz
#                            pitun-frontend.tar.gz
#                            geoip.dat
#                            geosite.dat
#   --skip-host-prep       Skip avahi disable / sysctl / modprobe / Docker
#                          install. Use only if you've already prepared
#                          the host yourself.
#   --non-interactive      Don't ask any questions; pick safe defaults.
#                          Required when piping from `curl | bash`.
#   --ipv6                 Allow IPv6 for downloads. Default is IPv4-only
#                          since v1.3.0-beta.3 — saw too many devices where
#                          IPv6 to GitHub silently hangs (Debian 13 RPi,
#                          some VPS on broken BGP routes). If you have a
#                          v6-only network, opt in with this flag.
#   --fix-blockers         Pre-flush kill-switch leftovers (`inet pitun`
#                          nftables + `ip rule fwmark 0x1 lookup 100`)
#                          before downloads. Use ONLY when:
#                            * a previous PiTun run died with kill_switch
#                              active, AND
#                            * curl from this host now hangs on first
#                              download (kernel still TPROXYing into a
#                              dead xray socket).
#                          On a HEALTHY install with kill_switch + a
#                          running backend, omit this flag — the install
#                          will work over xray's normal bypass path.
#                          The pre-flight detects + warns automatically;
#                          re-run with this flag if it suggests so.
#   --dry-run              Print every step without executing.
#   --help                 Show this help.
#
# Environment variable equivalents (handy when piping from curl):
#
#   PITUN_VERSION, PITUN_DIR, PITUN_BUILD, PITUN_OFFLINE, PITUN_SKIP_HOST_PREP,
#   PITUN_NON_INTERACTIVE, PITUN_FORCE_IPV6, PITUN_FIX_BLOCKERS.
#
# Uninstall:
#
#   To remove PiTun, run the bundled uninstaller:
#
#     sudo bash /opt/pitun/scripts/uninstall.sh           # interactive
#     sudo bash /opt/pitun/scripts/uninstall.sh --dry-run # preview
#     sudo bash /opt/pitun/scripts/uninstall.sh --purge   # nuke everything
#
#   Handles containers, images, volumes, install dirs, nftables, sysctl,
#   DNS, swap, and host-network config. Idempotent + safe by default —
#   see `scripts/README.md` (section "Uninstall") for the full flag list.

set -euo pipefail

# ── Defaults ─────────────────────────────────────────────────────────────────
GITHUB_REPO="DaveBugg/PiTun"
INSTALL_DIR="${PITUN_DIR:-/opt/pitun}"
VERSION="${PITUN_VERSION:-latest}"
USE_BUILD="${PITUN_BUILD:-0}"
OFFLINE_DIR="${PITUN_OFFLINE:-}"
SKIP_HOST_PREP="${PITUN_SKIP_HOST_PREP:-0}"
NON_INTERACTIVE="${PITUN_NON_INTERACTIVE:-0}"
# `--fix-blockers` (or PITUN_FIX_BLOCKERS=1) — pre-flush kill-switch
# leftovers (nftables `inet pitun` + `ip rule fwmark 0x1 lookup 100`)
# before any download. Default OFF: a healthy install with kill_switch
# enabled and a running backend doesn't need this — traffic flows
# through xray normally and `curl … | sudo bash` works. Only enable
# when a previous run died with kill_switch active and left the
# kernel mid-blocked (curl hangs on first download).
FIX_BLOCKERS="${PITUN_FIX_BLOCKERS:-0}"
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

# Track whether the user explicitly pinned a version (via `--version` or
# `PITUN_VERSION`). When implicit ("latest"), we refuse to install
# pre-release tags (`-beta`/`-rc`/`-alpha`) below — beta channels must
# be opt-in to keep `curl … | sudo bash` cron jobs from accidentally
# rolling production onto an in-flight beta.
USER_PINNED_VERSION=0
[[ "$VERSION" != "latest" ]] && USER_PINNED_VERSION=1

while [[ $# -gt 0 ]]; do
    case "$1" in
        --version)         VERSION="$2"; USER_PINNED_VERSION=1; shift 2 ;;
        --dir)             INSTALL_DIR="$2"; shift 2 ;;
        --build)           USE_BUILD=1; shift ;;
        --offline)         OFFLINE_DIR="$2"; shift 2 ;;
        --skip-host-prep)  SKIP_HOST_PREP=1; shift ;;
        --non-interactive) NON_INTERACTIVE=1; shift ;;
        --ipv6)            PITUN_FORCE_IPV6=1; shift ;;
        --fix-blockers)    FIX_BLOCKERS=1; shift ;;
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

# ── Kill-switch leftover detection + optional cleanup (since v1.3.0-beta.3) ─
#
# When PiTun is running with kill_switch=true, the backend installs an
# `inet pitun` nftables table that TPROXYs non-bypass traffic to xray
# (and a matching `ip rule fwmark 0x1 lookup 100` policy route to deliver
# returned packets via lo). If the backend dies (crash, OOM, kernel
# panic, manual `docker compose down`) those rules stay in the kernel —
# silently dropping every outbound packet because nothing's listening
# on 127.0.0.1:7893 anymore. Even `curl -fsSL` to GitHub hangs.
#
# Default behaviour: detect, warn, and *leave alone* if backend looks
# healthy. A working PiTun gateway with kill-switch on routes its own
# traffic through xray fine — no reason to drop the protection during
# an upgrade and momentarily expose the LAN.
#
# Opt-in cleanup: pass `--fix-blockers` (or `PITUN_FIX_BLOCKERS=1` env)
# to flush the leftovers up-front. Use this when the previous run died
# mid-protect and `curl` hangs on the very first download.
#
# Heuristic for "healthy enough to leave alone": is `pitun-backend`
# container currently running? If yes → assume kill-switch is active by
# design; downloads will work via xray's bypass path. If no (or no
# Docker yet) → kill-switch artifacts are stale; warn the user.
HAS_NFT_LEFTOVER=0
HAS_IPRULE_LEFTOVER=0
if command -v nft &>/dev/null && nft list table inet pitun &>/dev/null; then
    HAS_NFT_LEFTOVER=1
fi
if ip rule show 2>/dev/null | grep -q 'fwmark 0x1 lookup 100'; then
    HAS_IPRULE_LEFTOVER=1
fi

BACKEND_RUNNING=0
if command -v docker &>/dev/null \
   && docker ps --filter 'name=^pitun-backend$' --format '{{.Status}}' 2>/dev/null \
      | grep -q '^Up '; then
    BACKEND_RUNNING=1
fi

if [[ "$FIX_BLOCKERS" == "1" ]]; then
    if (( HAS_NFT_LEFTOVER )); then
        warn "Flushing 'inet pitun' nftables (--fix-blockers requested)."
        nft delete table inet pitun 2>/dev/null || true
    fi
    while ip rule show 2>/dev/null | grep -q 'fwmark 0x1 lookup 100'; do
        ip rule del fwmark 0x1 lookup 100 2>/dev/null || break
    done
    if ip route show table 100 2>/dev/null | grep -q .; then
        ip route flush table 100 2>/dev/null || true
    fi
elif (( HAS_NFT_LEFTOVER || HAS_IPRULE_LEFTOVER )) && (( ! BACKEND_RUNNING )); then
    # Bad combo: kill-switch artefacts present BUT backend isn't up.
    # Almost certainly stale and going to block the install. Warn loudly
    # so the user can re-run with --fix-blockers if curl indeed hangs.
    warn "════════════════════════════════════════════════════════════════════"
    warn "  Detected stale kill-switch state on this host:"
    (( HAS_NFT_LEFTOVER ))    && warn "    * 'inet pitun' nftables table is present but backend is down"
    (( HAS_IPRULE_LEFTOVER )) && warn "    * 'ip rule fwmark 0x1 lookup 100' policy route is present"
    warn ""
    warn "  Without a running backend these will silently drop every"
    warn "  outbound packet — including this installer's downloads."
    warn ""
    warn "  If the next download hangs > 60 s, abort (Ctrl+C) and re-run"
    warn "  with the --fix-blockers flag, e.g.:"
    warn ""
    warn "      sudo bash /tmp/pitun-install.sh --version v1.3.0 --fix-blockers"
    warn ""
    warn "  (or set PITUN_FIX_BLOCKERS=1 in the env)"
    warn "════════════════════════════════════════════════════════════════════"
elif (( HAS_NFT_LEFTOVER || HAS_IPRULE_LEFTOVER )); then
    # Backend is running — kill-switch is intentional. Don't touch.
    info "Kill-switch is active and backend is healthy — leaving nftables alone."
fi

# IPv6 / IPv4 connectivity policy. Historically we tried to be clever
# (small IPv6 probe → if OK leave default; if broken force -4). That
# auto-detect is fragile: a tiny `/zen` request can succeed via IPv6
# while a 30 KB release-metadata request hangs (PMTU / partial-route
# / BGP issues). We've burned hours on this twice. The fix:
#   * Default behaviour now is to FORCE IPv4 (`-4`) for every curl —
#     it's the reliable path on every device we've ever installed on.
#   * Override with `--ipv6` flag or `PITUN_FORCE_IPV6=1` env var if you
#     genuinely have a v6-only network and want to opt out.
#
# CURL_FORCE_IPV4 is consumed by the `download()` function and the
# probe / API calls below.
CURL_FORCE_IPV4="-4"
if [[ "${PITUN_FORCE_IPV6:-0}" == "1" ]]; then
    CURL_FORCE_IPV4=""
    warn "PITUN_FORCE_IPV6=1 — letting curl pick IPv6 first (default behaviour)."
fi

# Detect mode: first-install vs upgrade. The signal is the existence
# of an `.env` file at the target — that file is generated once on
# the very first run and never overwritten afterwards. docker-compose.yml
# alone is too weak (we always re-extract the source tarball, so it
# would always be there during the upgrade pass).
#
# This flag is consulted later to:
#   * suppress the "set admin password on first login" hint on upgrades
#   * print a "Found existing install — upgrading" banner
#   * take a SQLite snapshot before loading new backend images
#   * skip the static-IP verification noise when the host has been
#     running PiTun fine for months (the install either has it right
#     already or the user's network really is fine with DHCP)
IS_UPDATE=0
if [[ -f "$INSTALL_DIR/.env" ]]; then
    IS_UPDATE=1
fi

# Read the running version (if any) for the upgrade banner. Best-effort
# — if the backend container isn't responding or curl is missing, we
# print "?" and move on. Both running and target are normalised to
# carry the `v` prefix so the summary reads symmetrically (`v1.2.5 →
# v1.2.6`, never `1.2.5 → v1.2.6`).
RUNNING_VERSION="?"
if [[ "$IS_UPDATE" == "1" ]] && command -v curl &>/dev/null; then
    RUNNING_VERSION=$(curl -fsS --max-time 2 http://127.0.0.1:8000/health 2>/dev/null \
        | grep -oE '"version"\s*:\s*"[^"]*"' \
        | sed 's/"version"\s*:\s*"\([^"]*\)"/\1/' || true)
    RUNNING_VERSION="${RUNNING_VERSION:-?}"
    # `/health` returns plain `1.2.5` (no `v` prefix) — add it for
    # display consistency with the GitHub-tag-style target version.
    if [[ -n "$RUNNING_VERSION" && "$RUNNING_VERSION" != "?" && "${RUNNING_VERSION#v}" == "$RUNNING_VERSION" ]]; then
        RUNNING_VERSION="v${RUNNING_VERSION}"
    fi
fi

# Same normalisation on the install target. `latest` stays as-is; any
# concrete version string (e.g. `1.2.6` if user passed `--version 1.2.6`)
# gets a `v` prepended for the banner.
DISPLAY_VERSION="$VERSION"
if [[ -n "$DISPLAY_VERSION" && "$DISPLAY_VERSION" != "latest" \
      && "${DISPLAY_VERSION#v}" == "$DISPLAY_VERSION" ]]; then
    DISPLAY_VERSION="v${DISPLAY_VERSION}"
fi

info "PiTun installer"
info "  Mode:    $([[ "$IS_UPDATE" == "1" ]] && echo "UPGRADE (current version: $RUNNING_VERSION)" || echo "FRESH INSTALL")"
info "  Arch:    $ARCH_RAW ($ARCH)"
info "  Distro:  $DISTRO_ID"
info "  Kernel:  $KERNEL_VER"
info "  Target:  $INSTALL_DIR"
info "  Version: $DISPLAY_VERSION"
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
    # `$CURL_FORCE_IPV4` is `-4` when initial connectivity probe found
    # broken IPv6 (Debian 13 + IPv6-broken VPS pattern); empty otherwise.
    curl ${CURL_FORCE_IPV4:-} -fL --progress-bar \
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

# Auto-discovery of pre-downloaded artefacts. When install.sh launches
# out of a directory that already has the expected filenames sitting
# next to it (the "scp these files + install.sh to /tmp on the air-
# gapped box" workflow), treat that directory as OFFLINE_DIR without
# requiring the explicit `--offline` flag. Skipped when the script is
# piped from `curl | bash` (BASH_SOURCE[0] is `-bash` or empty) or when
# the operator passed `--offline` already.
#
# Filename matching tolerates the version/arch suffix used by GitHub
# Releases (e.g. `pitun-backend-v1.3.2-amd64.tar.gz`) in addition to the
# canonical short names (`pitun-backend.tar.gz`). The actual asset → canonical
# mapping happens in the staging-symlink loop below — here we only need
# to flag the dir as offline-mode if *any* match shows up.
if [[ -z "$OFFLINE_DIR" ]]; then
    _script_path="${BASH_SOURCE[0]:-}"
    if [[ -n "$_script_path" && -f "$_script_path" ]]; then
        _script_dir="$(cd "$(dirname "$_script_path")" 2>/dev/null && pwd)"
        if [[ -n "$_script_dir" ]]; then
            shopt -s nullglob
            for _pattern in \
                "$_script_dir"/pitun-src.tar.gz       "$_script_dir"/pitun-src-*.tar.gz \
                "$_script_dir"/pitun-backend.tar.gz   "$_script_dir"/pitun-backend-*.tar.gz \
                "$_script_dir"/pitun-naive.tar.gz     "$_script_dir"/pitun-naive-*.tar.gz \
                "$_script_dir"/pitun-frontend.tar.gz  "$_script_dir"/pitun-frontend-*.tar.gz \
                "$_script_dir"/geoip.dat              "$_script_dir"/geosite.dat
            do
                if [[ -f "$_pattern" ]]; then
                    OFFLINE_DIR="$_script_dir"
                    info "Auto-detected pre-downloaded artefacts in $OFFLINE_DIR"
                    break
                fi
            done
            shopt -u nullglob
        fi
    fi
fi

# Cache invalidation (since v1.2.9). Every artifact in STAGING_DIR has a
# version-agnostic filename (release.json, pitun-src.tar.gz,
# pitun-backend.tar.gz, ...). Without this guard, re-running with a
# different `--version` re-uses the previous run's cached artifacts and
# silently "upgrades" to the cached version. Symptom in the wild
# (v1.2.2 → v1.2.6 attempt): `Resolved version: v1.2.2` because
# release.json was a stale cache from an earlier run.
#
# Strategy: stamp the cache with the requested VERSION on each run. If
# it doesn't match (or VERSION == latest, which is symbolic and may
# drift between runs), wipe the PiTun-versioned artifacts. We
# deliberately keep geoip.dat / geosite.dat — they come from a separate
# repo's "latest" and are version-independent on PiTun's side, so
# preserving them avoids re-downloading ~15 MB on every install.
STAMP_FILE="$STAGING_DIR/.cached-for"
CACHED_VERSION=""
[[ -f "$STAMP_FILE" ]] && CACHED_VERSION=$(cat "$STAMP_FILE" 2>/dev/null || true)
if [[ -z "$OFFLINE_DIR" ]] && {
    [[ "$VERSION" == "latest" ]] || [[ "$CACHED_VERSION" != "$VERSION" ]]
}; then
    if [[ -n "$CACHED_VERSION" ]]; then
        info "Staging cache was for ${CACHED_VERSION}; wiping for ${VERSION}."
    fi
    rm -f \
        "$STAGING_DIR/release.json" \
        "$STAGING_DIR/pitun-src.tar.gz" \
        "$STAGING_DIR/pitun-backend.tar.gz" \
        "$STAGING_DIR/pitun-naive.tar.gz" \
        "$STAGING_DIR/pitun-frontend.tar.gz"
fi
echo "$VERSION" > "$STAMP_FILE"

if [[ -n "$OFFLINE_DIR" ]]; then
    info "Offline mode: using artifacts from $OFFLINE_DIR"
    [[ -d "$OFFLINE_DIR" ]] || error "Offline dir does not exist: $OFFLINE_DIR"
fi
# Decide whether we need release metadata. Skip in three cases:
#   1. `--build` was explicitly requested (no release assets used).
#   2. Truly air-gapped install — OFFLINE_DIR has all the image
#      artefacts (`pitun-backend.tar.gz` + `pitun-naive.tar.gz` +
#      `pitun-frontend.tar.gz`) so we never need to look up their
#      URLs from release.json.
# Otherwise fetch — even in hybrid offline mode where some images
# are local and some aren't (the operator dropped just the source
# and geo files, say). A failing fetch falls back to build-from-
# source so a truly disconnected box still installs successfully.
_need_release_json=0
if [[ "$USE_BUILD" != "1" ]]; then
    if [[ -z "$OFFLINE_DIR" ]] \
        || [[ ! -e "$OFFLINE_DIR/pitun-backend.tar.gz" ]] \
        || [[ ! -e "$OFFLINE_DIR/pitun-naive.tar.gz" ]] \
        || [[ ! -e "$OFFLINE_DIR/pitun-frontend.tar.gz" ]]; then
        _need_release_json=1
    fi
fi
if [[ "$_need_release_json" == "1" ]]; then
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

        # Pre-release safety net (since v1.2.8). Refuse to silently
        # install a pre-release tag (`-beta.N`, `-rc.N`, `-alpha.N`,
        # per semver 2.0) when the user didn't explicitly opt in.
        # release.yml already marks such tags `prerelease: true` so
        # the GitHub `/releases/latest` API skips them — this guard
        # is defense-in-depth for cases where:
        #   * release.yml hasn't published yet (race window)
        #   * a maintainer manually flipped a beta to "Latest" in UI
        #   * the resolved JSON came from a stale cache / CDN edge
        # If user passed --version or PITUN_VERSION, they get whatever
        # they asked for, no questions asked (rolling forward to or
        # back from a beta is a deliberate choice).
        if [[ "$USER_PINNED_VERSION" == "0" && "$RESOLVED_TAG" == *-* ]]; then
            echo ""
            warn "════════════════════════════════════════════════════════════════════"
            warn "  ⛔ PRE-RELEASE BLOCKED — aborting."
            warn ""
            warn "    GitHub /releases/latest resolved to: $RESOLVED_TAG"
            warn "    This is a pre-release (semver suffix '${RESOLVED_TAG#*-}')."
            warn ""
            warn "  The installer refuses to auto-install pre-release tags when no"
            warn "  --version flag was given. Production hosts running this script"
            warn "  in a cron / unattended workflow shouldn't roll onto an in-flight"
            warn "  beta on accident."
            warn ""
            warn "  If you DO want this beta, opt in explicitly:"
            warn "    sudo bash $0 --version $RESOLVED_TAG"
            warn "  Or via env var:"
            warn "    sudo PITUN_VERSION=$RESOLVED_TAG bash $0"
            warn ""
            warn "  If you expected a stable release, the most likely cause is that"
            warn "  release.yml hasn't yet published the latest stable tag. Wait a"
            warn "  few minutes and re-run."
            warn "════════════════════════════════════════════════════════════════════"
            exit 1
        fi

        VERSION="$RESOLVED_TAG"
        # Re-derive display version now that `latest` has been resolved
        # to a concrete tag — keeps the final summary's `→ vX.Y.Z`
        # symmetric with the running version.
        DISPLAY_VERSION="$VERSION"
        if [[ -n "$DISPLAY_VERSION" && "${DISPLAY_VERSION#v}" == "$DISPLAY_VERSION" ]]; then
            DISPLAY_VERSION="v${DISPLAY_VERSION}"
        fi
    fi
fi

# Downgrade detection — HARD ABORT.
#
# Scenario: user is on v1.2.6, runs `curl … | sudo bash` (default
# --version latest). GitHub's /releases/latest API returns v1.2.2
# (release.yml didn't publish newer ones, or the latest Release wasn't
# marked make_latest=true, or the user's network MITMs api.github.com,
# or unauthenticated rate-limit returned a cached old value). Without
# this guard, the script would silently DOWNGRADE the install — pulls
# old images, recreates containers, runs older alembic head. Possibly
# data-destructive if a future release migrates the schema forward.
#
# Use `sort -V` (version sort) as a cheap semver comparator. Strips a
# leading `v`, then asks whether running > resolved. If yes, abort.
# Same-version (running == resolved) handled separately below.
if [[ "$IS_UPDATE" == "1" \
      && -n "$RUNNING_VERSION" && "$RUNNING_VERSION" != "?" ]]; then
    _running_num="${RUNNING_VERSION#v}"
    _target_num="${DISPLAY_VERSION#v}"
    if [[ "$_running_num" != "$_target_num" \
          && "$(printf '%s\n%s\n' "$_running_num" "$_target_num" | sort -V | tail -1)" == "$_running_num" ]]; then
        echo ""
        warn "════════════════════════════════════════════════════════════════════"
        warn "  ⛔ DOWNGRADE DETECTED — aborting."
        warn ""
        warn "    Currently running: $RUNNING_VERSION"
        warn "    Target version:    $DISPLAY_VERSION (resolved from --version=$VERSION)"
        warn ""
        warn "  The target is OLDER than what's already installed. This usually"
        warn "  means GitHub's /releases/latest endpoint returned a stale value:"
        warn "    • newer Release object wasn't marked 'Latest' on GitHub"
        warn "    • CI release.yml hasn't published yet"
        warn "    • api.github.com is rate-limited or unreachable from this host"
        warn ""
        warn "  Refusing to silently downgrade your install. Either:"
        warn "    1. Wait for CI / GitHub UI 'Latest' flag and re-run."
        warn "    2. Pin a specific newer version explicitly:"
        warn "         sudo bash $0 --version v$_running_num"
        warn "    3. If you intentionally want to roll back, force-pin to the older:"
        warn "         sudo bash $0 --version $DISPLAY_VERSION"
        warn "════════════════════════════════════════════════════════════════════"
        exit 1
    fi
fi

# Same-version detection. We've seen this in the wild: a user on
# v1.2.2 runs `curl … | sudo bash` (default `--version latest`), the
# GitHub `/releases/latest` API returns v1.2.2 because newer tags
# don't have published Release objects yet (release.yml may still be
# building or the tag was never released formally), and the script
# happily proceeds to "upgrade" 1.2.2 → 1.2.2. Wastes time and
# misleads the admin into thinking nothing happened despite their
# explicit upgrade intent.
#
# Guard: warn loudly and pause for 5 seconds so the user can Ctrl+C.
# We don't hard-abort because re-installing the same version IS
# sometimes useful (recover from corrupt files, reset .env, etc.).
if [[ "$IS_UPDATE" == "1" \
      && -n "$RUNNING_VERSION" && "$RUNNING_VERSION" != "?" \
      && "$RUNNING_VERSION" == "$DISPLAY_VERSION" ]]; then
    echo ""
    warn "════════════════════════════════════════════════════════════════════"
    warn "  Already running ${RUNNING_VERSION}. The installer will re-pull the"
    warn "  same images, re-extract source, and recreate containers."
    warn ""
    warn "  If you wanted a different version, abort now (Ctrl+C) and use:"
    warn ""
    warn "    # Foolproof — download, then run with the flag:"
    warn "    curl -fsSL https://raw.githubusercontent.com/${GITHUB_REPO}/master/install.sh \\"
    warn "         -o /tmp/pitun-install.sh"
    warn "    sudo bash /tmp/pitun-install.sh --version vX.Y.Z"
    warn ""
    warn "    # Or pipe-form — note 'bash -s --' is REQUIRED to pass flags:"
    warn "    curl -fsSL https://raw.githubusercontent.com/${GITHUB_REPO}/master/install.sh \\"
    warn "         | sudo bash -s -- --version vX.Y.Z"
    warn "════════════════════════════════════════════════════════════════════"
    echo ""
    if [[ "$NON_INTERACTIVE" != "1" && "$DRY_RUN" != "1" ]]; then
        info "Continuing in 5s — press Ctrl+C to abort..."
        sleep 5
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
    # Hybrid mode — symlink in any file the operator pre-staged, then
    # fall through to download for the rest. Previously a partial
    # offline-dir silently broke the install at use-time when a
    # missing file was first referenced; the message used to come
    # out as "cannot extract" or "docker load: no such file" three
    # phases later, which made the underlying "you forgot one of
    # the six artefacts" cause hard to spot.
    info "Offline-dir mode: $OFFLINE_DIR (will download anything not present)"
    # `_pick_offline` resolves a canonical name (e.g. `pitun-backend.tar.gz`)
    # to whatever actually sits in OFFLINE_DIR — either the canonical
    # filename itself, or a Release-style suffixed one such as
    # `pitun-backend-v1.3.2-amd64.tar.gz`. For backend/naive we prefer
    # an arch-matched suffix when present, falling back to any version
    # suffix; for frontend/src the suffix is version-only.
    #
    # Outputs nothing on stdout — sets `_pick_offline_result` instead.
    # Empty result = no local file, downstream will download as usual.
    _pick_offline() {
        local canonical="$1" base ext glob_arch glob_any
        _pick_offline_result=""
        # Exact-name match wins (cheapest, no glob)
        if [[ -e "$OFFLINE_DIR/$canonical" ]]; then
            _pick_offline_result="$OFFLINE_DIR/$canonical"
            return 0
        fi
        # Suffix-tolerant fallback. Geo files are intentionally NOT
        # suffix-tolerant — Loyalsoldier ships them flat, no version,
        # so a "geoip-something.dat" file is operator error.
        case "$canonical" in
            *.tar.gz)
                base="${canonical%.tar.gz}"; ext=".tar.gz" ;;
            *)
                return 0 ;;
        esac
        shopt -s nullglob
        # Prefer arch-matched suffix for arch-specific assets.
        local matches=()
        case "$canonical" in
            pitun-backend.tar.gz|pitun-naive.tar.gz)
                matches=("$OFFLINE_DIR"/"${base}-"*-"${ARCH}${ext}")
                # Fall back to any-suffix match (older release naming, or operator
                # manually built without arch suffix).
                (( ${#matches[@]} > 0 )) || matches=("$OFFLINE_DIR"/"${base}-"*"${ext}")
                ;;
            *)
                matches=("$OFFLINE_DIR"/"${base}-"*"${ext}")
                ;;
        esac
        shopt -u nullglob
        if (( ${#matches[@]} > 0 )); then
            if (( ${#matches[@]} > 1 )); then
                warn "  multiple ${base}-* candidates in OFFLINE_DIR — using $(basename "${matches[0]}")"
            fi
            _pick_offline_result="${matches[0]}"
        fi
    }
    for f in pitun-src.tar.gz pitun-backend.tar.gz pitun-naive.tar.gz \
             pitun-frontend.tar.gz geoip.dat geosite.dat; do
        _pick_offline "$f"
        if [[ -n "$_pick_offline_result" ]]; then
            ln -sf "$_pick_offline_result" "$STAGING_DIR/$f"
            local_basename="$(basename "$_pick_offline_result")"
            if [[ "$local_basename" == "$f" ]]; then
                info "  using local: $f"
            else
                info "  using local: $local_basename → $f"
            fi
        fi
    done
fi

if [[ -z "$OFFLINE_DIR" ]] || [[ ! -e "$SRC_TARBALL" ]] || [[ "$USE_BUILD" != "1" && ( ! -e "$BACKEND_IMG" || ! -e "$NAIVE_IMG" || ! -e "$FRONTEND_DIST" ) ]] || [[ ! -e "$GEOIP_DAT" ]] || [[ ! -e "$GEOSITE_DAT" ]]; then
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
    # Per-file guard: each `download` call skipped when the artefact
    # is already on disk (offline-dir symlink or a previous run
    # leftover). Keeps the staging dir as the single source of truth
    # — `$SRC_TARBALL` etc. resolve identically regardless of how
    # they got there.
    [[ -e "$SRC_TARBALL" ]] \
        || download "$src_url" "$SRC_TARBALL" "$SRC_DESC"

    if [[ "$USE_BUILD" != "1" ]]; then
        if [[ -e "$BACKEND_IMG" && -e "$NAIVE_IMG" && -e "$FRONTEND_DIST" ]]; then
            info "All Docker image / frontend artefacts present locally — skipping release-asset lookup."
        else
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
                [[ -e "$BACKEND_IMG"  ]] || download "$be_url" "$BACKEND_IMG" "backend image (linux/$ARCH)"
                [[ -e "$NAIVE_IMG"    ]] || download "$nv_url" "$NAIVE_IMG"   "naive image (linux/$ARCH)"
                [[ -e "$FRONTEND_DIST" ]] || download "$fe_url" "$FRONTEND_DIST" "frontend dist"
            fi
        fi
    fi

    # GeoIP / GeoSite databases (bind-mounted into the backend container
    # for on-demand refresh from the UI). The xray binary itself is
    # bundled inside the backend image as of v1.2.0 — no separate
    # download for it here.
    [[ -e "$GEOIP_DAT" ]] \
        || download "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat" \
                    "$GEOIP_DAT" "geoip.dat"
    [[ -e "$GEOSITE_DAT" ]] \
        || download "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat" \
                    "$GEOSITE_DAT" "geosite.dat"
fi

info "All downloads complete. Internet may go down now — install continues offline."

# ── Host prep ────────────────────────────────────────────────────────────────
if [[ "$SKIP_HOST_PREP" != "1" ]]; then
    step "Preparing host"

    # DNS-over-TCP fallback. Some networks (corporate firewalls, certain
    # ISPs, hotel APs) silently drop UDP:53. Without this, `apt-get update`
    # below dies with a cryptic "Temporary failure resolving" error and
    # the user has to figure out it's DNS. The `options use-vc` line tells
    # libc to use TCP for resolves. Idempotent — already-tcp configs stay
    # the same; networks where UDP works skip this branch entirely.
    if ! timeout 5 getent hosts deb.debian.org >/dev/null 2>&1 \
       && ! timeout 5 getent hosts archive.ubuntu.com >/dev/null 2>&1; then
        warn "DNS resolution failing — likely UDP:53 blocked. Switching to DNS over TCP…"
        if [[ "$DRY_RUN" != "1" ]]; then
            printf "nameserver 8.8.8.8\nnameserver 1.1.1.1\noptions use-vc\n" \
                > /etc/resolv.conf
        fi
        if timeout 5 getent hosts deb.debian.org >/dev/null 2>&1; then
            info "DNS-over-TCP fallback is working"
        else
            warn "DNS still failing — you may need to fix /etc/resolv.conf manually."
        fi
    fi

    info "Installing system packages…"
    run apt-get update -qq
    run apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg lsb-release \
        nftables iproute2 net-tools iptables \
        arp-scan dnsutils unzip jq cron \
        sqlite3 git

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

    # Persistent systemd journal. Default Debian/Ubuntu/RaspiOS configs leave
    # journald in "auto" mode, which means logs live in /run/log/journal and
    # vanish on every reboot. That makes postmortem debugging of an
    # unexpected reboot (kernel OOM, undervoltage, watchdog reset) impossible
    # — by the time the operator SSHes in, the evidence is already gone.
    # Caps tuned for the documented 64 GB minimum disk in README: 200 MB
    # max journal size, never eat into the last 1 GB of free space, 20 MB
    # per file so rotation is responsive. Idempotent: skip if user already
    # set Storage= in the main conf, or our drop-in already exists.
    info "Configuring persistent systemd journal (postmortem reboot evidence)…"
    if [[ "$DRY_RUN" != "1" ]]; then
        if grep -qE '^\s*Storage=' /etc/systemd/journald.conf 2>/dev/null \
           || [[ -f /etc/systemd/journald.conf.d/pitun.conf ]]; then
            info "  journal storage already configured — leaving alone"
        else
            # Pre-create /var/log/journal/<machine-id>/ so journald picks
            # persistent mode on next restart. systemd-journal group +
            # setgid bit are required by journald spec.
            mid=$(cat /etc/machine-id 2>/dev/null || true)
            if [[ -n "$mid" ]]; then
                install -d -o root -g systemd-journal -m 2755 \
                    /var/log/journal \
                    "/var/log/journal/${mid}"
            fi
            mkdir -p /etc/systemd/journald.conf.d
            cat > /etc/systemd/journald.conf.d/pitun.conf <<'EOF'
[Journal]
Storage=persistent
SystemMaxUse=200M
SystemMaxFileSize=20M
SystemKeepFree=1G
EOF
            # Restart attempts to flush /run → /var/log, but the flush is
            # idempotent-per-boot: if journald already decided "runtime" at
            # the current boot it won't migrate until next boot. Still
            # worth restarting so new entries land in /var/log immediately
            # on systems where the directory wasn't pre-existing.
            systemctl restart systemd-journald \
                || warn "journald restart failed (config still armed — kicks in on next reboot)"
            info "  persistent journal configured (full effect after next reboot)"
        fi
    fi

    if ! command -v docker &>/dev/null; then
        info "Installing Docker (via get.docker.com)…"
        # `get.docker.com` is the official auto-detecting installer.
        # Idempotent: re-running on a host with Docker already installed
        # is fine, but we skip the curl entirely above to be safe.
        # Inject `$CURL_FORCE_IPV4` if our probe found IPv6 broken —
        # otherwise the upstream get.docker.com curl can hang the same
        # 30-75s on the IPv6 attempt before falling through.
        run sh -c "curl ${CURL_FORCE_IPV4:-} -fsSL https://get.docker.com | sh"
        run systemctl enable --now docker
    else
        info "Docker already installed: $(docker --version)"
    fi

    # Docker Compose v2. `get.docker.com` ships the plugin in fresh
    # installs, but on hosts where Docker was put down via Debian's
    # `docker.io` package without the plugin, `docker compose` is
    # missing and `docker compose up -d` later in this script fails
    # cryptically. Detect + install the plugin binary as a fallback.
    if ! docker compose version &>/dev/null; then
        info "Installing Docker Compose v2 plugin…"
        # Top-level script — no `local`, but using a `_compose_arch`
        # name to make it obvious the var is private to this block.
        _compose_arch=$(uname -m)
        case "$_compose_arch" in
            aarch64) _compose_arch="aarch64" ;;
            x86_64)  _compose_arch="x86_64"  ;;
            armv7l)  _compose_arch="armv7"   ;;
            *) error "Unsupported arch for Docker Compose: $_compose_arch" ;;
        esac
        if [[ "$DRY_RUN" != "1" ]]; then
            mkdir -p /usr/local/lib/docker/cli-plugins
            curl ${CURL_FORCE_IPV4:-} -fsSL -o /usr/local/lib/docker/cli-plugins/docker-compose \
                "https://github.com/docker/compose/releases/download/v2.29.1/docker-compose-linux-${_compose_arch}"
            chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
        fi
        info "Docker Compose installed: $(docker compose version 2>&1 | head -1)"
    else
        info "Docker Compose already available: $(docker compose version 2>&1 | head -1)"
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

    # Add the invoking user to the `docker` group so they can run
    # `docker` and `docker compose` without sudo after a logout/login
    # cycle. Without this, every `docker exec pitun-backend …` from a
    # plain user shell hits "permission denied connecting to docker.sock"
    # — exactly what bit one of our installs in the wild on v1.2.2.
    #
    # We pick `$SUDO_USER` (the original user that invoked sudo) when
    # available; falls back to skipping if install.sh was run by root
    # directly (no SUDO_USER) or by the docker user in a container.
    TARGET_USER="${SUDO_USER:-}"
    if [[ -n "$TARGET_USER" && "$TARGET_USER" != "root" ]]; then
        if id -nG "$TARGET_USER" 2>/dev/null | tr ' ' '\n' | grep -qx docker; then
            info "User '$TARGET_USER' already in 'docker' group"
        else
            info "Adding user '$TARGET_USER' to 'docker' group…"
            run usermod -aG docker "$TARGET_USER"
            warn "$TARGET_USER must log out and back in (or run 'newgrp docker') for the group to take effect."
        fi
    else
        warn "Couldn't detect a non-root invoking user — skipping docker-group setup."
        warn "If you'll run docker commands as a non-root user later: usermod -aG docker <username>"
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

# ── Backup SQLite before upgrade ─────────────────────────────────────────────
# Pre-upgrade snapshot so a botched migration / new-version regression has
# a one-line rollback. We use sqlite's online backup API via Python (the
# container always has it) instead of a raw `cp` so concurrent backend
# writes don't tear the file.
if [[ "$IS_UPDATE" == "1" && "$DRY_RUN" != "1" ]]; then
    step "Backing up SQLite (pre-upgrade snapshot)"
    BACKUP_PATH="$INSTALL_DIR/data-backup-pre-${VERSION}-$(date +%s).db"
    if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx 'pitun-backend'; then
        if docker exec pitun-backend python -c "
import sqlite3
src = sqlite3.connect('/app/data/pitun.db')
dst = sqlite3.connect('/tmp/pitun-pre-upgrade.bak')
src.backup(dst)
dst.close(); src.close()
" 2>/dev/null; then
            docker cp pitun-backend:/tmp/pitun-pre-upgrade.bak "$BACKUP_PATH" 2>/dev/null && \
                info "  Snapshot saved to $BACKUP_PATH" || \
                warn "  Could not docker-cp snapshot — continuing without backup"
        else
            warn "  pitun-backend not responding — skipping pre-upgrade backup"
        fi
    else
        warn "  pitun-backend container not running — skipping pre-upgrade backup"
    fi
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
        # Per-arch sha256 of the unpacked xray binary inside the backend
        # image. Pinned so we can detect post-`docker load` corruption
        # (observed on a 1.3 user device during v1.3.0-beta.4 install:
        # ext4 htree corruption silently flipped bytes in overlay2
        # layers, producing a binary that segfaulted on `xray version`
        # while the release tarball itself was bit-perfect — see the
        # post-mortem in notes.md). The Dockerfile also pins these at
        # build-time; this is the runtime check on the target device.
        XRAY_SHA_AMD64="${PITUN_XRAY_SHA_AMD64:-64d46afb80adea1bf97a0d467e83f4a9ac1ebd0995891e84bca3f1a1d1affb1d}"
        XRAY_SHA_ARM64="${PITUN_XRAY_SHA_ARM64:-4b8af237444801bf17b3dc10a1c5c24581fbe3d433eba3d78c6c3a0da1df56fc}"
        XRAY_SHA_ARM="${PITUN_XRAY_SHA_ARM:-63a2fb09b928d1bde3a0df9b663d1936f13b53cd0db40c8ce85e4accdc779860}"

        # Wrap docker load + xray-sha verification in a retry loop. Each
        # attempt: nuke any stale tag, load fresh, run sha256sum on the
        # bundled xray binary, compare to the pinned expectation. On
        # mismatch, remove the loaded image + retry up to 3 times.
        # If all attempts fail, surface a loud error pointing at the
        # most likely cause (storage corruption — fsck the device).
        host_arch=$(uname -m 2>/dev/null || echo unknown)
        case "$host_arch" in
            x86_64|amd64)        EXPECTED_XRAY_SHA="$XRAY_SHA_AMD64" ;;
            aarch64|arm64)       EXPECTED_XRAY_SHA="$XRAY_SHA_ARM64" ;;
            armv7l|armhf|arm)    EXPECTED_XRAY_SHA="$XRAY_SHA_ARM"   ;;
            *)
                warn "  Unknown host arch '$host_arch' — skipping xray sha verification"
                EXPECTED_XRAY_SHA=""
                ;;
        esac

        # Helper: load image tarball + verify it (for backend; sha
        # check applies because xray lives in this image only).
        load_backend_with_verify() {
            local attempt
            for attempt in 1 2 3; do
                # Clean any remnants of a prior failed attempt so we get
                # a fresh layer materialisation each retry.
                docker rmi -f pitun-backend:latest >/dev/null 2>&1 || true
                be_loaded=$(docker load < "$BACKEND_IMG" \
                            | sed -n 's/^Loaded image: //p' | head -n1)
                if [[ -z "$be_loaded" ]]; then
                    warn "  Backend load (attempt $attempt) returned no tag — retrying"
                    continue
                fi
                if [[ -z "$EXPECTED_XRAY_SHA" ]]; then
                    # No expected sha for this arch — accept and move on.
                    return 0
                fi
                got_sha=$(docker run --rm --entrypoint sha256sum \
                            "$be_loaded" /usr/local/bin/xray 2>/dev/null \
                          | awk '{print $1}')
                if [[ "$got_sha" == "$EXPECTED_XRAY_SHA" ]]; then
                    info "  xray sha verified (attempt $attempt) ✓"
                    return 0
                fi
                warn "  xray sha MISMATCH on attempt $attempt — got '$got_sha'"
                warn "  expected '$EXPECTED_XRAY_SHA' — retrying load…"
                docker rmi -f "$be_loaded" >/dev/null 2>&1 || true
            done
            return 1
        }

        if ! load_backend_with_verify; then
            error "Backend image's xray binary is corrupted after $((attempt:-3)) load attempts.

This usually means the host's filesystem or storage hardware is
flipping bytes during write — the release tarball itself is fine
(verified by build-time digest pinning).

Recommended actions:
  1. Reboot the device and run fsck on the root filesystem:
       sudo touch /forcefsck && sudo reboot
     (RPi: ext4 may show 'state: clean with errors' — see
     'sudo dumpe2fs -h /dev/<rootdev> | grep error')
  2. Check storage health:
       sudo smartctl -a /dev/<rootdev>     # for SSD/NVMe
       dmesg | grep -i 'mmc\\|sd\\|ata\\|ext4'  # for SD card
  3. If fsck finds errors repeatedly, the device's storage is
     dying — back up /opt/pitun/data + .env to another machine
     and replace the SD card / SSD before re-installing.

The pre-upgrade snapshot is safe in /opt/pitun/data-backup-pre-*.db"
        fi

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
        # interface with a default route. The user is expected to verify
        # before relying on it.
        DEFAULT_IF=$(ip -o -4 route show to default 2>/dev/null \
                      | awk '{print $5}' | head -n1)
        if [[ -n "$DEFAULT_IF" ]]; then
            sed -i "s/^INTERFACE=.*/INTERFACE=$DEFAULT_IF/" .env
            info "  Autodetected INTERFACE=$DEFAULT_IF"

            # Derive PiTun host IP and the LAN subnet from the chosen
            # interface. Without this, the .env stays at the example
            # defaults (192.168.1.0/24 + 192.168.1.100) and users on
            # other subnets (e.g. 192.168.88.0/24) end up editing four
            # places by hand. Tradeoff: we use python3 for CIDR math
            # because every supported distro already ships it and there
            # are three incompatible `ipcalc` flavours in the wild; a
            # pure-bash fallback runs only if python3 is missing.
            HOST_CIDR=$(ip -o -4 addr show dev "$DEFAULT_IF" 2>/dev/null \
                        | awk '{print $4}' | head -n1)
            ROUTER_IP=$(ip -o -4 route show to default 2>/dev/null \
                        | awk '{print $3}' | head -n1)

            if [[ -n "$HOST_CIDR" ]]; then
                HOST_IP="${HOST_CIDR%/*}"
                # Compute the network address (e.g. 192.168.88.50/24 -> 192.168.88.0/24).
                LAN_CIDR=$(python3 -c "import ipaddress; print(ipaddress.ip_network('$HOST_CIDR', strict=False))" 2>/dev/null || true)
                if [[ -z "$LAN_CIDR" ]]; then
                    # Pure-bash fallback: bitwise AND of host IP and netmask.
                    _ip=${HOST_CIDR%/*}; _prefix=${HOST_CIDR#*/}
                    IFS=. read -r _a _b _c _d <<<"$_ip"
                    _mask=$(( 0xFFFFFFFF << (32 - _prefix) & 0xFFFFFFFF ))
                    _net=$(( ((_a<<24)|(_b<<16)|(_c<<8)|_d) & _mask ))
                    LAN_CIDR=$(printf "%d.%d.%d.%d/%d" \
                        $((_net>>24 & 0xFF)) $((_net>>16 & 0xFF)) \
                        $((_net>>8  & 0xFF)) $((_net     & 0xFF)) "$_prefix")
                fi

                # GATEWAY_IP in our .env actually means "PiTun host IP"
                # — the address devices on the LAN should set as their
                # default gateway. Misnomer kept for backward-compat.
                sed -i "s|^LAN_CIDR=.*|LAN_CIDR=$LAN_CIDR|"                                    .env
                sed -i "s|^GATEWAY_IP=.*|GATEWAY_IP=$HOST_IP|"                                 .env
                sed -i "s|^VITE_API_BASE_URL=.*|VITE_API_BASE_URL=http://${HOST_IP}/api|"      .env
                sed -i "s|^VITE_WS_BASE_URL=.*|VITE_WS_BASE_URL=ws://${HOST_IP}/api|"          .env
                sed -i "s|^CORS_ORIGINS=.*|CORS_ORIGINS=http://localhost:5173,http://${HOST_IP}|" .env
                info "  Autodetected LAN_CIDR=$LAN_CIDR, host IP=$HOST_IP${ROUTER_IP:+, router=$ROUTER_IP}"
            else
                warn "Could not read IP/netmask from $DEFAULT_IF — leaving LAN_CIDR/GATEWAY_IP at example defaults; edit .env manually."
            fi
        fi

        warn "Verify $INSTALL_DIR/.env before going to production:"
        warn "  - INTERFACE  (autodetected: ${DEFAULT_IF:-<none>})"
        warn "  - LAN_CIDR   (autodetected: ${LAN_CIDR:-192.168.1.0/24 default})"
        warn "  - GATEWAY_IP (this PiTun host's LAN IP — autodetected: ${HOST_IP:-192.168.1.100 default})"
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

# ── Static IP / DHCP sanity check (both modes) ───────────────────────────────
# PiTun is a default gateway for LAN devices. If its own LAN IP changes
# (DHCP lease rolls), every device pointing at the old IP loses internet
# until they re-DHCP. We don't try to fix DHCP for the user — too many
# distros/tools (NetworkManager / dhcpcd / netplan / systemd-networkd) —
# but we DO surface the warning every install and every upgrade.
DEFAULT_IF=$(ip -o -4 route show to default 2>/dev/null | awk '{print $5}' | head -n1)
if [[ -n "$DEFAULT_IF" ]]; then
    IS_STATIC=0
    # NetworkManager
    if command -v nmcli &>/dev/null; then
        NM_CON=$(nmcli -t -f NAME,DEVICE con show --active 2>/dev/null \
                 | awk -F: -v ifc="$DEFAULT_IF" '$2==ifc{print $1; exit}')
        if [[ -n "$NM_CON" ]]; then
            METHOD=$(nmcli -t -f ipv4.method con show "$NM_CON" 2>/dev/null | cut -d: -f2)
            [[ "$METHOD" == "manual" ]] && IS_STATIC=1
        fi
    fi
    # dhcpcd-style: /etc/dhcpcd.conf with static config block
    if [[ "$IS_STATIC" == "0" && -f /etc/dhcpcd.conf ]]; then
        if grep -qE "^\s*interface\s+$DEFAULT_IF" /etc/dhcpcd.conf 2>/dev/null \
           && grep -qE "^\s*static\s+ip_address" /etc/dhcpcd.conf 2>/dev/null; then
            IS_STATIC=1
        fi
    fi
    # systemd-networkd: any *.network file declaring a static Address=
    if [[ "$IS_STATIC" == "0" && -d /etc/systemd/network ]]; then
        if grep -lE "^\s*Address=" /etc/systemd/network/*.network 2>/dev/null | head -1 >/dev/null; then
            IS_STATIC=1
        fi
    fi
    if [[ "$IS_STATIC" == "0" ]]; then
        warn "Network: $DEFAULT_IF appears to use DHCP. PiTun should run on a static IP —"
        warn "         if the host's IP changes after a router reboot, every LAN device"
        warn "         pointing at it as gateway will lose internet until they re-DHCP."
        warn "         Configure a static lease (in your router) or static IP (NetworkManager /"
        warn "         dhcpcd.conf / netplan) before relying on PiTun for production traffic."
    fi
fi

# ── Summary ──────────────────────────────────────────────────────────────────
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo ""
if [[ "$IS_UPDATE" == "1" ]]; then
    info "PiTun upgraded: $RUNNING_VERSION → $DISPLAY_VERSION"
else
    info "PiTun is up."
fi
echo ""
echo -e "${GREEN}Web UI:${NC}  http://${HOST_IP:-<this-host>}/"
if [[ "$IS_UPDATE" == "1" ]]; then
    echo -e "${GREEN}Backup:${NC}  ${BACKUP_PATH:-(no pre-upgrade snapshot — see warnings above)}"
else
    echo -e "${GREEN}Login:${NC}   admin / password  (change on first login)"
fi
echo ""
if [[ "$IS_UPDATE" == "1" ]]; then
    echo -e "${YELLOW}Post-upgrade:${NC}"
    echo "  • Verify the UI loads cleanly and your nodes/rules are intact"
    echo "  • Check the Recent Events feed for any startup warnings"
    echo "  • If anything broke: ${BACKUP_PATH:-pre-upgrade snapshot under $INSTALL_DIR/data-backup-pre-*.db}"
    echo "    can be restored: stop backend, replace data/pitun.db, start backend"
else
    echo -e "${YELLOW}Next steps:${NC}"
    echo "  1. Edit $INSTALL_DIR/.env if you haven't (LAN_CIDR / GATEWAY_IP / INTERFACE)"
    echo "  2. Set the host's LAN IP as static (not DHCP)"
    echo "  3. Point your devices' default gateway at this host"
fi
echo ""
echo "Logs:    docker compose -f $INSTALL_DIR/docker-compose.yml logs -f"
echo "Restart: docker compose -f $INSTALL_DIR/docker-compose.yml restart"
echo "Update:  re-run this installer with --version vX.Y.Z"
