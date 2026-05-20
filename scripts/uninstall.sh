#!/usr/bin/env bash
# PiTun uninstaller.
#
# Removes the PiTun stack from a host without nuking unrelated state.
# Containers and install dirs go by default; host-level tweaks (nftables,
# sysctl, swap, DNS, host network config) require explicit confirmation
# or `--purge`. Designed to be safe on a re-run.
#
# Usage:
#   sudo ./uninstall.sh                  # interactive, asks before each risky step
#   sudo ./uninstall.sh -y               # skip prompts on default removals
#   sudo ./uninstall.sh --purge          # remove EVERYTHING including host config
#   sudo ./uninstall.sh --dry-run        # show what would be removed
#   sudo ./uninstall.sh --keep-data      # preserve DB + config dirs
#   sudo ./uninstall.sh --help           # full help
#
# Designed to handle every installer permutation we ship: stock install.sh,
# `--build` local builds, `--offline DIR` air-gapped installs, the
# docker-compose.dev.yml dev stack, naive sidecars (dynamic container
# names), and hot-deploy backup directories left by maintainers.

set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
DRY_RUN=false
ASSUME_YES=false
PURGE=false
KEEP_DATA=false
KEEP_NETWORK=false
KEEP_XRAY=false
KEEP_SWAP=false
PREFIX=""

# ── CLI ──────────────────────────────────────────────────────────────────────
usage() {
    cat <<'EOF'
PiTun uninstaller — removes containers, images, install files, and
(with confirmation) host-level tweaks.

USAGE
    sudo ./uninstall.sh [OPTIONS]

OPTIONS
    --dry-run            Show what would be removed, don't touch anything.
    -y, --yes            Skip prompts on standard removals (still asks
                         before host-network changes to protect SSH).
    --purge              Remove EVERYTHING: standard set + host network
                         config + sysctl tweaks + swap. Implies -y but
                         still warns + waits 5s before host network ops.
    --keep-data          Preserve install dir's data/ subdir (DB +
                         exported configs). Standard removal otherwise.
    --keep-network       Never touch host network manager files
                         (NetworkManager / netplan / ifupdown / networkd).
    --keep-xray          Leave /usr/local/bin/xray and geo data alone
                         (use when xray is shared with another tool).
    --keep-swap          Leave /swapfile alone (the 2 GB swap install.sh
                         creates may be useful independent of PiTun).
    --prefix PATH        Install prefix to scan. Default: auto-detect
                         (/opt/pitun, /opt/pitun-dev, then any dir
                         containing docker-compose.yml + pitun-backend
                         container).
    -h, --help           Show this help.

EXIT CODES
    0   Success or dry-run completed.
    1   User aborted at a prompt.
    2   Not running as root.
    3   PiTun footprint not detected (nothing to remove).
    4   Argument error.

EXAMPLES
    # Default — interactive, preserves host-level config:
    sudo ./uninstall.sh

    # Headless re-image prep — wipe everything including host tweaks:
    sudo ./uninstall.sh --purge

    # Keep DB + config for a future reinstall:
    sudo ./uninstall.sh -y --keep-data

    # See what would happen, change nothing:
    sudo ./uninstall.sh --dry-run
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run)       DRY_RUN=true; shift ;;
        -y|--yes)        ASSUME_YES=true; shift ;;
        --purge)         PURGE=true; ASSUME_YES=true; shift ;;
        --keep-data)     KEEP_DATA=true; shift ;;
        --keep-network)  KEEP_NETWORK=true; shift ;;
        --keep-xray)     KEEP_XRAY=true; shift ;;
        --keep-swap)     KEEP_SWAP=true; shift ;;
        --prefix)        PREFIX="${2:?--prefix needs a path}"; shift 2 ;;
        -h|--help)       usage; exit 0 ;;
        *)               echo "Unknown option: $1" >&2; usage >&2; exit 4 ;;
    esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────
C_RED=$'\033[31m'; C_GRN=$'\033[32m'; C_YEL=$'\033[33m'
C_BLU=$'\033[34m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
[[ -t 1 ]] || { C_RED=""; C_GRN=""; C_YEL=""; C_BLU=""; C_DIM=""; C_RST=""; }

info()  { echo "${C_BLU}==>${C_RST} $*"; }
warn()  { echo "${C_YEL}!! ${C_RST} $*" >&2; }
ok()    { echo "${C_GRN} ✓${C_RST} $*"; }
skip()  { echo "${C_DIM} - ${C_RST}$*"; }
err()   { echo "${C_RED}!! ${C_RST}$*" >&2; }

run() {
    # Execute a destructive command — or just print it in dry-run mode.
    # First arg is a human label, rest is the command. Failures are
    # downgraded to warnings so a single missing artefact doesn't abort
    # the whole uninstall.
    local label="$1"; shift
    if $DRY_RUN; then
        echo "  ${C_DIM}[dry-run]${C_RST} $label: $*"
        return 0
    fi
    if "$@" 2>/dev/null; then
        ok "$label"
    else
        skip "$label (already gone or no-op)"
    fi
}

confirm() {
    # Yes/no prompt unless --yes / --purge bypasses. Returns 0 = yes.
    # `--purge` doesn't bypass HOST-NETWORK prompts — those are checked
    # via $2 ("network" flag).
    local msg="$1" kind="${2:-}"
    if $ASSUME_YES && [[ "$kind" != "network" ]]; then
        return 0
    fi
    if [[ "$kind" == "network" ]] && $PURGE; then
        warn "About to modify host network config (--purge). Waiting 5s — Ctrl-C to abort..."
        sleep 5
        return 0
    fi
    read -r -p "$msg [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]]
}

# Pre-flight: must be root for nftables / sysctl / fs writes.
if [[ "${EUID:-$(id -u)}" -ne 0 ]] && ! $DRY_RUN; then
    err "Run as root (or with sudo). Use --dry-run for a non-root preview."
    exit 2
fi

# ── Phase 1 — Discovery ──────────────────────────────────────────────────────
echo
info "Phase 1: discovery"

# Resolve install prefix. Order: explicit --prefix, then standard paths,
# then a docker-compose project rooted in a directory containing
# docker-compose.yml AND a pitun-backend container.
INSTALL_DIR=""
if [[ -n "$PREFIX" ]]; then
    [[ -d "$PREFIX" ]] && INSTALL_DIR="$PREFIX"
fi
if [[ -z "$INSTALL_DIR" ]]; then
    for cand in /opt/pitun /opt/pitun-dev /root/pitun /home/*/pitun; do
        [[ -d "$cand" && -f "$cand/docker-compose.yml" ]] && INSTALL_DIR="$cand" && break
    done
fi
if [[ -z "$INSTALL_DIR" ]] && command -v docker &>/dev/null; then
    # Last-ditch: ask docker which dir it knows the project from.
    INSTALL_DIR=$(docker inspect pitun-backend 2>/dev/null \
        | grep -oE '"com.docker.compose.project.working_dir": *"[^"]+"' \
        | head -1 | cut -d'"' -f4 || true)
fi
if [[ -n "$INSTALL_DIR" ]]; then
    ok "Install dir: $INSTALL_DIR"
else
    skip "Install dir: not found"
fi

# Containers (compose-managed + naive sidecars + docker-socket-proxy)
CONTAINERS=()
if command -v docker &>/dev/null; then
    while IFS= read -r name; do
        [[ -n "$name" ]] && CONTAINERS+=("$name")
    done < <(docker ps -a --format '{{.Names}}' 2>/dev/null \
        | grep -E '^(pitun-|docker-socket-proxy$)' || true)
fi
if (( ${#CONTAINERS[@]} > 0 )); then
    ok "Containers: ${#CONTAINERS[@]} found (${CONTAINERS[*]})"
else
    skip "Containers: none"
fi

# Images (built locally OR pulled — match by repo prefix `pitun`)
IMAGES=()
if command -v docker &>/dev/null; then
    while IFS= read -r img; do
        [[ -n "$img" && "$img" != "<none>:<none>" ]] && IMAGES+=("$img")
    done < <(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
        | grep -iE '^(.*/)?pitun-(backend|frontend|naive)' || true)
fi
if (( ${#IMAGES[@]} > 0 )); then
    ok "Images: ${#IMAGES[@]} found"
else
    skip "Images: none"
fi

# Volumes + networks (compose-namespaced — names start with `pitun`)
VOLUMES=()
NETWORKS=()
if command -v docker &>/dev/null; then
    while IFS= read -r v; do
        [[ -n "$v" ]] && VOLUMES+=("$v")
    done < <(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -iE '^pitun' || true)
    while IFS= read -r n; do
        [[ -n "$n" && "$n" != "bridge" && "$n" != "host" && "$n" != "none" ]] && NETWORKS+=("$n")
    done < <(docker network ls --format '{{.Name}}' 2>/dev/null | grep -iE '^pitun' || true)
fi
(( ${#VOLUMES[@]} > 0 )) && ok "Volumes: ${#VOLUMES[@]}" || skip "Volumes: none"
(( ${#NETWORKS[@]} > 0 )) && ok "Networks: ${#NETWORKS[@]}" || skip "Networks: none"

# nftables tables (PiTun creates `pitun` in `inet` family, sometimes also `ip`)
NFT_TABLES=()
if command -v nft &>/dev/null; then
    while IFS= read -r line; do
        # Lines look like: `table inet pitun {`
        [[ "$line" =~ table\ ([a-z]+)\ pitun ]] && NFT_TABLES+=("${BASH_REMATCH[1]} pitun")
    done < <(nft list tables 2>/dev/null | grep -E ' pitun( |$)' || true)
fi
(( ${#NFT_TABLES[@]} > 0 )) && ok "nft tables: ${NFT_TABLES[*]}" || skip "nft tables: none"

# Host binaries (install.sh copies xray + geo data here unless already present)
HAS_XRAY_BIN=false
[[ -f /usr/local/bin/xray ]] && HAS_XRAY_BIN=true
HAS_XRAY_SHARE=false
[[ -d /usr/local/share/xray ]] && HAS_XRAY_SHARE=true
$HAS_XRAY_BIN   && ok "xray binary: /usr/local/bin/xray"      || skip "xray binary: not present"
$HAS_XRAY_SHARE && ok "xray geo data: /usr/local/share/xray"  || skip "xray geo data: not present"

# Host config touchpoints (sysctl, DNS, swap, journald)
SYSCTL_FILES=()
while IFS= read -r f; do
    [[ -n "$f" ]] && SYSCTL_FILES+=("$f")
done < <(find /etc/sysctl.d -maxdepth 1 -name '*pitun*' -type f 2>/dev/null || true)
(( ${#SYSCTL_FILES[@]} > 0 )) && ok "sysctl drop-ins: ${#SYSCTL_FILES[@]}" || skip "sysctl drop-ins: none"

HAS_USEVC=false
grep -q '^options.*use-vc' /etc/resolv.conf 2>/dev/null && HAS_USEVC=true
$HAS_USEVC && ok "resolv.conf: 'options use-vc' present (PiTun DNS-over-TCP)" || skip "resolv.conf: clean"

HAS_SWAP=false
[[ -f /swapfile ]] && HAS_SWAP=true
$HAS_SWAP && ok "/swapfile present (install.sh creates one)" || skip "/swapfile: not present"

JOURNAL_FILES=()
while IFS= read -r f; do
    [[ -n "$f" ]] && JOURNAL_FILES+=("$f")
done < <(find /etc/systemd/journald.conf.d -maxdepth 1 -name '*pitun*' -type f 2>/dev/null || true)
(( ${#JOURNAL_FILES[@]} > 0 )) && ok "journald drop-ins: ${#JOURNAL_FILES[@]}" || skip "journald drop-ins: none"

LOGROTATE_FILES=()
while IFS= read -r f; do
    [[ -n "$f" ]] && LOGROTATE_FILES+=("$f")
done < <(find /etc/logrotate.d -maxdepth 1 -name '*pitun*' -type f 2>/dev/null || true)
(( ${#LOGROTATE_FILES[@]} > 0 )) && ok "logrotate snippets: ${#LOGROTATE_FILES[@]}" || skip "logrotate: none"

# Host network manager touched by PiTun 1.3.3+ (UI-driven static IP).
# We DON'T list these as PiTun-owned unless filenames are explicitly
# prefixed — operator-managed network config must stay.
NET_FILES=()
while IFS= read -r f; do
    [[ -n "$f" ]] && NET_FILES+=("$f")
done < <( {
    find /etc/netplan -maxdepth 1 -name '*pitun*.yaml' -type f 2>/dev/null
    find /etc/systemd/network -maxdepth 1 -name '*pitun*.network' -type f 2>/dev/null
    find /etc/network/interfaces.d -maxdepth 1 -name '*pitun*' -type f 2>/dev/null
} || true)
(( ${#NET_FILES[@]} > 0 )) && warn "PiTun-prefixed network files: ${#NET_FILES[@]} — review before removing"

# Any *.bak.* backup dirs left by hot-deploys
BACKUP_DIRS=()
if [[ -n "$INSTALL_DIR" ]]; then
    while IFS= read -r d; do
        [[ -n "$d" ]] && BACKUP_DIRS+=("$d")
    done < <(find "$INSTALL_DIR" -maxdepth 3 -name '*.bak.*' -type d 2>/dev/null || true)
fi
(( ${#BACKUP_DIRS[@]} > 0 )) && ok "Hot-deploy backups: ${#BACKUP_DIRS[@]}" || true

# Nothing to do?
if [[ -z "$INSTALL_DIR" ]] && (( ${#CONTAINERS[@]} == 0 )) && (( ${#IMAGES[@]} == 0 )) \
   && (( ${#NFT_TABLES[@]} == 0 )) && ! $HAS_XRAY_BIN && ! $HAS_XRAY_SHARE; then
    echo
    info "No PiTun footprint detected. Nothing to do."
    exit 3
fi

# ── Confirm before destroying ────────────────────────────────────────────────
echo
if $DRY_RUN; then
    info "Dry-run mode — would proceed with removal."
elif ! $ASSUME_YES; then
    echo
    warn "About to remove containers, images, volumes, install dir."
    confirm "Continue?" || { echo "Aborted."; exit 1; }
fi

# ── Phase 2 — Remove docker artefacts ────────────────────────────────────────
echo
info "Phase 2: docker cleanup"

# `docker compose down` if we have a known install dir — cleanest path,
# it removes containers + networks + (with -v) volumes declared in the
# compose file. Falls through to manual rm for stragglers.
if [[ -n "$INSTALL_DIR" ]] && [[ -f "$INSTALL_DIR/docker-compose.yml" ]] && command -v docker &>/dev/null; then
    if $DRY_RUN; then
        echo "  ${C_DIM}[dry-run]${C_RST} docker compose down -v --remove-orphans (in $INSTALL_DIR)"
    else
        (cd "$INSTALL_DIR" && docker compose down -v --remove-orphans 2>&1 | sed 's/^/    /') || true
        ok "docker compose down"
    fi
    # Dev variant too if it exists
    if [[ -f "$INSTALL_DIR/docker-compose.dev.yml" ]]; then
        if $DRY_RUN; then
            echo "  ${C_DIM}[dry-run]${C_RST} docker compose -f docker-compose.dev.yml down -v"
        else
            (cd "$INSTALL_DIR" && docker compose -f docker-compose.dev.yml down -v 2>/dev/null) || true
            ok "docker compose (dev) down"
        fi
    fi
fi

# Stragglers — naive sidecars that aren't in the compose file (dynamic)
# plus docker-socket-proxy and any container that survived `down`.
for c in "${CONTAINERS[@]:-}"; do
    [[ -z "$c" ]] && continue
    # Re-check existence — `compose down` may have already taken them.
    if docker inspect "$c" &>/dev/null; then
        run "rm container $c" docker rm -f "$c"
    fi
done

# Images — both registry-tagged and locally-built variants
for img in "${IMAGES[@]:-}"; do
    [[ -z "$img" ]] && continue
    run "rmi $img" docker rmi -f "$img"
done

# Stray volumes (compose `down -v` should have got them, but local-build
# volumes sometimes survive — match by name prefix)
for v in "${VOLUMES[@]:-}"; do
    [[ -z "$v" ]] && continue
    run "volume rm $v" docker volume rm "$v"
done

# Stray networks
for n in "${NETWORKS[@]:-}"; do
    [[ -z "$n" ]] && continue
    run "network rm $n" docker network rm "$n"
done

# Reclaim disk space (only after our explicit removals so prune doesn't
# nuke unrelated dangling artefacts)
if command -v docker &>/dev/null; then
    run "docker builder prune" docker builder prune -f
fi

# ── Phase 3 — Filesystem ─────────────────────────────────────────────────────
echo
info "Phase 3: filesystem"

# Hot-deploy backups first (always safe to remove)
for d in "${BACKUP_DIRS[@]:-}"; do
    [[ -z "$d" ]] && continue
    run "rm -rf $d" rm -rf "$d"
done

# Data dir handling — `--keep-data` preserves the DB + exported configs
# so the operator can reinstall and pick up where they left off.
if [[ -n "$INSTALL_DIR" ]]; then
    if $KEEP_DATA && [[ -d "$INSTALL_DIR/data" ]]; then
        # Move data aside before nuking the install dir, then put it back.
        # Using /tmp is fine here — operators expect a re-mount on
        # reinstall, and /tmp survives the script run.
        if ! $DRY_RUN; then
            mv "$INSTALL_DIR/data" "/tmp/pitun-data.$$"
        fi
        run "rm -rf $INSTALL_DIR" rm -rf "$INSTALL_DIR"
        if ! $DRY_RUN; then
            mkdir -p "$INSTALL_DIR"
            mv "/tmp/pitun-data.$$" "$INSTALL_DIR/data"
            ok "Preserved data dir at $INSTALL_DIR/data (--keep-data)"
        fi
    else
        run "rm -rf $INSTALL_DIR" rm -rf "$INSTALL_DIR"
    fi
fi

# Other ancillary dirs
for d in /etc/pitun /var/lib/pitun /tmp/pitun; do
    [[ -e "$d" ]] || continue
    if $KEEP_DATA && [[ "$d" == "/var/lib/pitun" ]]; then
        skip "$d (preserved by --keep-data)"
        continue
    fi
    run "rm -rf $d" rm -rf "$d"
done

# xray binary + geo data (host-level). Default: remove. `--keep-xray`
# skips when xray is shared with another tool on the host.
if ! $KEEP_XRAY; then
    $HAS_XRAY_BIN   && run "rm /usr/local/bin/xray"      rm -f /usr/local/bin/xray
    $HAS_XRAY_SHARE && run "rm -rf /usr/local/share/xray" rm -rf /usr/local/share/xray
else
    $HAS_XRAY_BIN   && skip "/usr/local/bin/xray (kept by --keep-xray)"
    $HAS_XRAY_SHARE && skip "/usr/local/share/xray (kept by --keep-xray)"
fi

# ── Phase 4 — nftables ───────────────────────────────────────────────────────
echo
info "Phase 4: nftables"

for tbl in "${NFT_TABLES[@]:-}"; do
    [[ -z "$tbl" ]] && continue
    # `tbl` is "family name", e.g. "inet pitun" or "ip pitun"
    run "nft delete table $tbl" nft delete table $tbl
done

# Persistent config files — strip pitun blocks
if [[ -f /etc/nftables.conf ]] && grep -q pitun /etc/nftables.conf 2>/dev/null; then
    if $DRY_RUN; then
        echo "  ${C_DIM}[dry-run]${C_RST} strip pitun blocks from /etc/nftables.conf"
    else
        # Backup before edit
        cp /etc/nftables.conf /etc/nftables.conf.pre-pitun-uninstall
        # Remove any include line referencing pitun-*.nft and any pitun table block
        sed -i.bak \
            -e '/include.*pitun.*\.nft/d' \
            -e '/^table [a-z]* pitun/,/^}/d' \
            /etc/nftables.conf
        ok "stripped pitun blocks from /etc/nftables.conf (backup: .pre-pitun-uninstall)"
    fi
fi
# Drop-in directory
if [[ -d /etc/nftables.d ]]; then
    while IFS= read -r f; do
        [[ -n "$f" ]] && run "rm $f" rm -f "$f"
    done < <(find /etc/nftables.d -maxdepth 1 -name '*pitun*' -type f 2>/dev/null || true)
fi

# ── Phase 5 — host config tweaks (asks for each) ─────────────────────────────
echo
info "Phase 5: host config (sysctl / DNS / journald / logrotate)"

# sysctl drop-ins
if (( ${#SYSCTL_FILES[@]} > 0 )); then
    if $PURGE || confirm "Remove PiTun sysctl drop-ins (${#SYSCTL_FILES[@]} file(s))? Reverts ip_forward + IPv6 toggles."; then
        for f in "${SYSCTL_FILES[@]}"; do
            run "rm $f" rm -f "$f"
        done
        run "sysctl --system reload" sysctl --system
    else
        skip "Sysctl drop-ins kept by user choice"
    fi
fi

# DNS over TCP toggle in /etc/resolv.conf
if $HAS_USEVC; then
    if $PURGE || confirm "Strip 'options use-vc' from /etc/resolv.conf? (PiTun's DNS-over-TCP workaround)"; then
        if $DRY_RUN; then
            echo "  ${C_DIM}[dry-run]${C_RST} sed -i '/^options.*use-vc/d' /etc/resolv.conf"
        else
            sed -i.pre-pitun-uninstall '/^options.*use-vc/d' /etc/resolv.conf
            ok "removed use-vc from /etc/resolv.conf"
        fi
    else
        skip "resolv.conf kept by user choice"
    fi
fi

# journald drop-ins
for f in "${JOURNAL_FILES[@]:-}"; do
    [[ -z "$f" ]] && continue
    if $PURGE || confirm "Remove journald drop-in $f? Reverts log-retention cap."; then
        run "rm $f" rm -f "$f"
        ! $DRY_RUN && systemctl restart systemd-journald 2>/dev/null || true
    else
        skip "$f kept"
    fi
done

# logrotate
for f in "${LOGROTATE_FILES[@]:-}"; do
    [[ -z "$f" ]] && continue
    if $PURGE || confirm "Remove logrotate snippet $f?"; then
        run "rm $f" rm -f "$f"
    else
        skip "$f kept"
    fi
done

# ── Phase 6 — swap ───────────────────────────────────────────────────────────
if $HAS_SWAP && ! $KEEP_SWAP; then
    echo
    info "Phase 6: swap"
    if $PURGE || confirm "Remove /swapfile (2 GB)? It's independent of PiTun — install.sh creates it but other tools may benefit."; then
        run "swapoff /swapfile" swapoff /swapfile
        if ! $DRY_RUN && grep -q '^/swapfile' /etc/fstab; then
            sed -i.pre-pitun-uninstall '/^\/swapfile/d' /etc/fstab
            ok "removed /swapfile fstab entry"
        fi
        run "rm /swapfile" rm -f /swapfile
    else
        skip "/swapfile kept"
    fi
fi

# ── Phase 7 — host network config (HIGHEST RISK) ─────────────────────────────
if ! $KEEP_NETWORK && (( ${#NET_FILES[@]} > 0 )); then
    echo
    info "Phase 7: host network config (HIGH RISK — may break SSH)"
    warn "About to touch host network manager files:"
    for f in "${NET_FILES[@]}"; do
        echo "    $f"
    done
    warn "Before confirming: open a SECOND ssh session and confirm it stays up after the change."
    warn "If you only have ONE shell, abort here and remove these files manually after a reboot."
    if confirm "Proceed with removal?" network; then
        for f in "${NET_FILES[@]}"; do
            run "rm $f" rm -f "$f"
        done
        # Reload the manager so the operator sees the effect right away
        if systemctl is-active --quiet NetworkManager 2>/dev/null; then
            ! $DRY_RUN && systemctl reload NetworkManager 2>/dev/null || true
        elif systemctl is-active --quiet systemd-networkd 2>/dev/null; then
            ! $DRY_RUN && networkctl reload 2>/dev/null || true
        fi
    else
        skip "Host network config kept (re-run with --keep-network=false later if needed)"
    fi
fi

# ── Phase 8 — verification ───────────────────────────────────────────────────
echo
info "Phase 8: verification"

LEFTOVERS=0
check() {
    local label="$1" cmd_output="$2"
    if [[ -n "$cmd_output" ]]; then
        warn "Leftover — $label:"
        echo "$cmd_output" | sed 's/^/    /'
        LEFTOVERS=$((LEFTOVERS + 1))
    fi
}

if command -v docker &>/dev/null; then
    check "containers" "$(docker ps -a --format '{{.Names}}' 2>/dev/null | grep -E '^(pitun-|docker-socket-proxy$)' || true)"
    check "images"     "$(docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | grep -iE '^(.*/)?pitun-' || true)"
    check "volumes"    "$(docker volume ls --format '{{.Name}}' 2>/dev/null | grep -iE '^pitun' || true)"
fi
check "install dirs"  "$(ls -d /opt/pitun /etc/pitun /var/lib/pitun /tmp/pitun 2>/dev/null || true)"
check "xray binary"   "$([[ -e /usr/local/bin/xray ]] && echo /usr/local/bin/xray || true)"

if command -v nft &>/dev/null; then
    check "nft tables" "$(nft list tables 2>/dev/null | grep ' pitun' || true)"
fi

echo
if [[ $LEFTOVERS -eq 0 ]]; then
    ok "Clean."
else
    warn "$LEFTOVERS category(ies) have leftovers — review above."
fi

if $DRY_RUN; then
    echo
    info "Dry-run complete. Re-run without --dry-run to actually remove anything."
fi
