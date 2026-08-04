#!/usr/bin/env bash
#
# PiTun auto-updater — check GitHub for a newer release and apply it.
#
# Why a separate script when `install.sh` already upgrades in place:
# install.sh is the *installer*, driven by a human who knows which
# version they want. This one is the *scheduler-facing* half — it
# decides whether an update is warranted at all, picks the network path
# that can actually reach GitHub from a box whose own traffic is
# TPROXY'd, and only then hands over to install.sh. That split keeps the
# installer free of policy and makes the unattended path auditable.
#
# Network path — the part that makes this box special:
#   PiTun's nftables OUTPUT chain marks the host's own traffic for
#   TPROXY, so a plain `curl` from here already egresses through the
#   active node. That is usually what you want (the ISP may throttle or
#   block GitHub), but it fails closed: with no active node, a dead
#   tunnel, or the kill switch armed, curl just hangs.
#
#   So we probe, in order:
#     1. xray's local SOCKS inbound (explicit, deterministic, and it
#        proves the node really carries traffic),
#     2. whatever the default route gives us,
#   and use the first that answers. `--no-proxy` forces step 2.
#
# Usage:
#   pitun-update.sh                 check and apply if newer
#   pitun-update.sh --check         report only, never touch anything
#   pitun-update.sh --force         re-apply even if versions match
#   pitun-update.sh --version vX.Y.Z  pin a specific tag
#   pitun-update.sh --prerelease    consider pre-releases too
#   pitun-update.sh --no-proxy      always go direct
#   pitun-update.sh --install-timer install the daily systemd timer
#   pitun-update.sh --remove-timer  remove it again
#
# Exit codes (stable — the timer and any monitoring rely on them):
#   0  up to date, or update applied successfully
#   10 update available (only with --check)
#   20 no network path to GitHub
#   30 update failed
#
set -uo pipefail

REPO="${PITUN_REPO:-DaveBugg/PiTun}"
INSTALL_DIR="${PITUN_DIR:-/opt/pitun}"
API_URL="${PITUN_API_URL:-http://127.0.0.1:8000}"
LOG_FILE="${PITUN_UPDATE_LOG:-/var/log/pitun-update.log}"
LOCK_FILE="/var/lock/pitun-update.lock"
# Both files live on the bind-mount the backend also sees (as /app/data),
# which is what lets a UI-driven update survive the backend restarting
# halfway through: the status is on disk, not in the dying process.
DATA_DIR="${PITUN_DATA_DIR:-${INSTALL_DIR}/data}"
STATUS_FILE="${PITUN_UPDATE_STATUS:-${DATA_DIR}/update-status.json}"
REQUEST_FILE="${PITUN_UPDATE_REQUEST:-${DATA_DIR}/update-request.json}"
CURL_TIMEOUT="${PITUN_CURL_TIMEOUT:-25}"

MODE="apply"          # apply | check
FORCE=0
WANT_VERSION=""
ALLOW_PRERELEASE=0
USE_PROXY=1

# ── Logging ──────────────────────────────────────────────────────────────────

log() {
    local line="[$(date '+%Y-%m-%d %H:%M:%S')] $*"
    echo "$line"
    # Best-effort: a read-only /var/log must not abort an update.
    [[ -n "$LOG_FILE" ]] && echo "$line" >>"$LOG_FILE" 2>/dev/null
    return 0
}
die() { log "ERROR: $*"; exit "${2:-30}"; }

# ── Version comparison ───────────────────────────────────────────────────────

# Strip a leading `v` and any pre-release suffix for numeric comparison.
version_core() {
    local v="${1#v}"
    echo "${v%%-*}"
}

# True when $1 is strictly newer than $2. Compares MAJOR.MINOR.PATCH
# numerically (so 1.4.10 > 1.4.9, which a string compare gets wrong),
# then treats a pre-release as older than the matching stable release
# per semver: 1.5.0-beta.1 < 1.5.0.
version_gt() {
    local a b
    a="$(version_core "$1")"; b="$(version_core "$2")"
    local -a A B
    IFS=. read -r -a A <<<"$a"
    IFS=. read -r -a B <<<"$b"
    local i
    for i in 0 1 2; do
        local ai="${A[i]:-0}" bi="${B[i]:-0}"
        ai="${ai//[!0-9]/}"; bi="${bi//[!0-9]/}"
        (( 10#${ai:-0} > 10#${bi:-0} )) && return 0
        (( 10#${ai:-0} < 10#${bi:-0} )) && return 1
    done
    # Cores equal — a stable release beats a pre-release of the same core.
    local a_pre=0 b_pre=0
    [[ "$1" == *-* ]] && a_pre=1
    [[ "$2" == *-* ]] && b_pre=1
    (( a_pre == 0 && b_pre == 1 ))
}

# ── Network path ─────────────────────────────────────────────────────────────

# Read a value out of the Settings table without needing an API token.
# Falls back to the compiled-in default when the container isn't up.
setting() {
    local key="$1" default="$2" out
    out=$(docker exec pitun-backend python -c "
import sqlite3
c = sqlite3.connect('/app/data/pitun.db')
r = c.execute('select value from settings where key=?', ('$key',)).fetchone()
print(r[0] if r and r[0] else '')
" 2>/dev/null | tr -d '\r\n')
    echo "${out:-$default}"
}

CURL_NET_ARGS=()
NET_PATH="unknown"

# Pick the first network path that can actually reach GitHub.
resolve_network_path() {
    local probe="https://api.github.com/rate_limit"

    if [[ "$USE_PROXY" == "1" ]]; then
        local socks_port auth_enabled
        socks_port="$(setting socks_port 1080)"
        auth_enabled="$(setting lan_proxy_auth_enabled false)"

        # With LAN-proxy auth on we'd need credentials; the default
        # TPROXY path already goes through the same node, so just use it.
        if [[ "$auth_enabled" != "true" ]] && [[ "$socks_port" =~ ^[0-9]+$ ]]; then
            # socks5h: resolve DNS through the tunnel too, otherwise a
            # poisoned/blocked resolver defeats the point.
            if curl -fsS -m 10 --proxy "socks5h://127.0.0.1:${socks_port}" \
                    -o /dev/null "$probe" 2>/dev/null; then
                CURL_NET_ARGS=(--proxy "socks5h://127.0.0.1:${socks_port}")
                NET_PATH="active node (socks5://127.0.0.1:${socks_port})"
                log "Network path: through the active node."
                return 0
            fi
            log "SOCKS inbound on :${socks_port} did not answer — trying direct."
        fi
    fi

    if curl -fsS -m 15 -o /dev/null "$probe" 2>/dev/null; then
        CURL_NET_ARGS=()
        NET_PATH="direct"
        log "Network path: direct."
        return 0
    fi

    # Both failed. The usual cause is a kill switch armed over a dead
    # tunnel — say so instead of leaving a bare timeout in the log.
    local ks; ks="$(setting kill_switch false)"
    if [[ "$ks" == "true" ]]; then
        log "Kill switch is ARMED and the tunnel is not carrying traffic —"
        log "no path to GitHub. Fix the active node, or run install.sh"
        log "manually with --fix-blockers."
    fi
    return 1
}

gh_api() {
    curl -fsS -m "$CURL_TIMEOUT" "${CURL_NET_ARGS[@]}" \
        -H 'Accept: application/vnd.github+json' "$1"
}

# ── Version discovery ────────────────────────────────────────────────────────

# `grep -o`, not `sed 's/.*"key"...'`: GitHub returns the whole array on
# ONE line, and a greedy `.*` then anchors on the LAST match — which made
# the pre-release branch report the OLDEST tag in the list.
json_first() {
    grep -o "\"$1\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | head -1 | cut -d'"' -f4
}

running_version() {
    curl -fsS -m 5 "${API_URL}/health" 2>/dev/null | json_first version
}

latest_release() {
    if [[ "$ALLOW_PRERELEASE" == "1" ]]; then
        # /releases/latest hides pre-releases by design; take the newest
        # entry from the full list instead (GitHub returns it first).
        gh_api "https://api.github.com/repos/${REPO}/releases?per_page=10" \
            | json_first tag_name
    else
        gh_api "https://api.github.com/repos/${REPO}/releases/latest" \
            | json_first tag_name
    fi
}

# ── Status file ──────────────────────────────────────────────────────────────
#
# The UI polls this instead of holding a connection: the update restarts
# the backend, so any stream would die exactly when the user most wants
# to know what happened. A file on the shared volume is the only channel
# that outlives the process.

STATE_FROM=""
STATE_TO=""

status_write() {
    local state="$1" pct="$2" step_id="$3" message="$4"
    local ok="${5:-null}"
    [[ -d "$(dirname "$STATUS_FILE")" ]] || return 0
    local tmp="${STATUS_FILE}.tmp"
    cat >"$tmp" <<EOF
{
  "state": "$state",
  "pct": $pct,
  "step": "$step_id",
  "message": $(json_str "$message"),
  "from": $(json_str "$STATE_FROM"),
  "to": $(json_str "$STATE_TO"),
  "ok": $ok,
  "updated_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "log": $(json_str "$LOG_FILE")
}
EOF
    # Atomic swap so a poll never reads half a document.
    mv -f "$tmp" "$STATUS_FILE" 2>/dev/null || true
}

# Minimal JSON string escaper — enough for the messages we emit.
json_str() {
    local v="${1//\/\\}"
    v="${v//\"/\\\"}"
    v="${v//$'\n'/ }"
    v="${v//$'\r'/}"
    v="${v//$'\t'/ }"
    printf '"%s"' "$v"
}

# install.sh announces its phases as `[STEP] <text>`. Map the ones that
# matter onto a coarse percentage — honest about being coarse: it tracks
# phases, not bytes, because that is what the installer exposes.
step_to_pct() {
    case "$1" in
        *"Downloading"*)              echo "25|download" ;;
        *"Preparing host"*)           echo "45|host" ;;
        *"GeoIP"*|*"GeoSite"*)        echo "50|geo" ;;
        *"Installing PiTun source"*)  echo "55|source" ;;
        *"Backing up SQLite"*)        echo "60|backup" ;;
        *"Loading pre-built"*)        echo "70|images" ;;
        *"Generating .env"*)          echo "80|env" ;;
        *"Starting Docker stack"*)    echo "88|restart" ;;
        *)                            echo "" ;;
    esac
}

# ── Image housekeeping ───────────────────────────────────────────────────────

# Old release images pile up: every update loads `pitun-backend:vX.Y.Z`
# and retags `:latest`, leaving the previous tag behind. On a 32 GB SD
# card three or four of those is a real problem, so drop everything that
# is not referenced by a running container.
prune_old_images() {
    command -v docker >/dev/null 2>&1 || return 0
    local in_use keep removed=0
    in_use="$(docker ps --format '{{.Image}}' 2>/dev/null | sort -u)"
    while read -r ref id; do
        [[ -z "$ref" || "$ref" == "<none>:<none>" ]] && continue
        case "$ref" in
            pitun-backend:*|pitun-naive:*) ;;
            *) continue ;;
        esac
        [[ "$ref" == *:latest ]] && continue
        grep -qxF "$ref" <<<"$in_use" && continue
        if docker rmi "$id" >/dev/null 2>&1; then
            log "  removed stale image $ref"
            removed=$((removed + 1))
        fi
    done < <(docker images --format '{{.Repository}}:{{.Tag}} {{.ID}}' 2>/dev/null)
    # Dangling layers left by `docker load` overwriting a tag.
    docker image prune -f >/dev/null 2>&1 || true
    log "Image cleanup: ${removed} stale PiTun image(s) removed."
}

# install.sh takes a SQLite snapshot before every upgrade and never
# removes it — deliberately, because a regression can surface a day
# later. Left alone they accumulate one per update forever.
#
# Pruned ONLY after a verified-healthy update, and never below
# KEEP_SNAPSHOTS: a failed update is precisely when the older ones are
# worth having. Newest-first by mtime, so a clock skew on the timestamp
# in the filename cannot reorder them.
KEEP_SNAPSHOTS="${KEEP_SNAPSHOTS:-3}"

prune_old_snapshots() {
    local dir="${INSTALL_DIR:-/opt/pitun}"
    local -a snaps=()
    while IFS= read -r f; do
        [[ -n "$f" ]] && snaps+=("$f")
    done < <(ls -1t "$dir"/data-backup-pre-*.db 2>/dev/null)

    local total="${#snaps[@]}"
    (( total > KEEP_SNAPSHOTS )) || {
        log "DB snapshots: ${total} kept (limit ${KEEP_SNAPSHOTS})."
        return 0
    }

    local removed=0 i
    for (( i = KEEP_SNAPSHOTS; i < total; i++ )); do
        if rm -f "${snaps[i]}" 2>/dev/null; then
            log "  removed old snapshot $(basename "${snaps[i]}")"
            removed=$((removed + 1))
        fi
    done
    log "DB snapshots: kept ${KEEP_SNAPSHOTS}, removed ${removed}."
}

# ── Apply ────────────────────────────────────────────────────────────────────

# The in-UI updater (Settings → Updates, and the API behind it) ships in
# 1.4.8. Installing anything older REMOVES it: the box can still be
# updated, but only from a shell on the box itself. Downgrades are a
# legitimate repair move, so this warns rather than refuses — being
# stranded without knowing why is the part worth preventing.
UPDATE_UI_SINCE="1.4.8"

lacks_update_ui() {
    local v="$1"
    [[ -z "$v" ]] && return 1
    version_gt "$UPDATE_UI_SINCE" "$v"
}

warn_if_losing_update_ui() {
    local target="$1" current="$2"
    lacks_update_ui "$target" || return 0
    # Only a real step DOWN loses something we currently have.
    [[ -n "$current" ]] && lacks_update_ui "$current" && return 0
    log "WARNING: ${target} predates ${UPDATE_UI_SINCE} and has no in-UI updater."
    log "         After this, Settings → Updates disappears. To come back, run"
    log "         on the box:  /opt/pitun/scripts/pitun-update.sh --force"
    return 0
}

apply_update() {
    local tag="$1"
    local tmp; tmp="$(mktemp -d /tmp/pitun-update.XXXXXX)"
    # `${tmp:-}` guard: this RETURN trap can re-fire on a later function's
    # return (after `tmp` has gone out of scope), and `set -u` would then
    # abort with "tmp: unbound variable" — right after a successful update,
    # flipping the systemd unit to failed even though the update applied.
    trap 'rm -rf "${tmp:-}"' RETURN

    STATE_TO="$tag"
    status_write running 5 fetch-installer "Fetching installer for ${tag}"
    log "Downloading installer for ${tag}…"
    # Pinned to the tag being installed, not to master: the installer and
    # the release it lays down should always be the same generation.
    if ! curl -fsSL -m "$CURL_TIMEOUT" "${CURL_NET_ARGS[@]}" \
            -o "$tmp/install.sh" \
            "https://raw.githubusercontent.com/${REPO}/${tag}/install.sh"; then
        status_write failed 5 fetch-installer "Could not download the installer" false
        log "Installer download failed."
        return 1
    fi
    if ! bash -n "$tmp/install.sh"; then
        status_write failed 5 fetch-installer "Downloaded installer is not valid bash" false
        return 1
    fi

    status_write running 15 install "Running installer for ${tag}"
    log "Running installer (--version ${tag})…"

    # install.sh owns the risky part — release download, image load,
    # DB snapshot, migrations, container restart, rollback. We only
    # translate its phase announcements into progress for the UI.
    #
    # It restarts the backend near the end, so this process must not be
    # a child of it: the agent runs from systemd on the host, not from
    # the container.
    set -o pipefail
    PITUN_NON_INTERACTIVE=1 bash "$tmp/install.sh" --version "$tag" 2>&1 \
        | while IFS= read -r line; do
            echo "$line" >>"$LOG_FILE" 2>/dev/null
            local mapped pct step_id
            mapped="$(step_to_pct "$line")"
            if [[ -n "$mapped" ]]; then
                pct="${mapped%%|*}"; step_id="${mapped##*|}"
                status_write running "$pct" "$step_id" "${line#*] }"
            fi
        done
    local rc=${PIPESTATUS[0]}

    if [[ "$rc" != "0" ]]; then
        status_write failed 90 install "Installer exited with code ${rc}" false
        log "Installer exited non-zero (rc=$rc) — see $LOG_FILE."
        return 1
    fi

    status_write running 92 cleanup "Removing superseded images"
    prune_old_images

    # Wait for the freshly-restarted backend to answer before calling it
    # a success — "installer exited 0" is not the same as "it came back".
    status_write running 96 health "Waiting for the backend to come back"
    local now="" i
    for i in $(seq 1 40); do
        now="$(running_version)"
        [[ -n "$now" ]] && break
        sleep 3
    done

    if [[ -z "$now" ]]; then
        status_write failed 96 health "Backend did not report healthy after the update" false
        log "Backend never came back healthy — check 'docker compose ps'."
        return 1
    fi

    # Healthy — only now is it safe to drop the older snapshots.
    prune_old_snapshots

    STATE_TO="$now"
    status_write done 100 done "Updated to ${now}" true
    log "Update applied. Backend reports version ${now}."
    return 0
}

# ── Agent (UI-driven updates) ────────────────────────────────────────────────
#
# The backend cannot apply an update itself: it would have to restart the
# very container the request is running in. So the UI drops a request
# file on the shared volume and this agent — a systemd path unit on the
# host — picks it up.

agent_once() {
    [[ -f "$REQUEST_FILE" ]] || { log "No update request pending."; return 0; }

    local req; req="$(cat "$REQUEST_FILE" 2>/dev/null)"
    # Consume the request FIRST: a request that crashes the agent must
    # not be retried forever by the path unit.
    rm -f "$REQUEST_FILE"

    local want force pre
    want="$(printf '%s' "$req" | json_first version)"
    force="$(printf '%s' "$req" | grep -o '"force"[[:space:]]*:[[:space:]]*true' || true)"
    pre="$(printf '%s' "$req" | grep -o '"prerelease"[[:space:]]*:[[:space:]]*true' || true)"
    [[ -n "$force" ]] && FORCE=1
    [[ -n "$pre" ]] && ALLOW_PRERELEASE=1
    [[ -n "$want" ]] && WANT_VERSION="$want"

    log "Update requested via UI (version=${want:-latest} force=${FORCE})."
    run_update
}

install_agent() {
    local self; self="$(readlink -f "$0")"
    mkdir -p "$DATA_DIR"
    cat >"/etc/systemd/system/pitun-update-agent.service" <<EOF
[Unit]
Description=PiTun update agent (applies a UI-requested update)
After=network-online.target docker.service

[Service]
Type=oneshot
# Invoke via bash, not a direct exec: the repo ships scripts non-executable
# (100644) and a source re-extraction on update drops any +x bit, which would
# otherwise brick the agent with status=203/EXEC on the next update.
ExecStart=/bin/bash ${self} --agent
EOF
    cat >"/etc/systemd/system/pitun-update-agent.path" <<EOF
[Unit]
Description=Watch for a PiTun update request from the UI

[Path]
PathExists=${REQUEST_FILE}
Unit=pitun-update-agent.service

[Install]
WantedBy=paths.target
EOF
    systemctl daemon-reload
    systemctl enable --now pitun-update-agent.path
    log "Update agent installed and watching ${REQUEST_FILE}."
    log "The UI's Update button will now work."
}

remove_agent() {
    systemctl disable --now pitun-update-agent.path 2>/dev/null
    rm -f /etc/systemd/system/pitun-update-agent.path \
          /etc/systemd/system/pitun-update-agent.service
    systemctl daemon-reload
    log "Update agent removed."
}

# ── systemd timer ────────────────────────────────────────────────────────────

TIMER_NAME="pitun-update"

install_timer() {
    local self; self="$(readlink -f "$0")"
    cat >"/etc/systemd/system/${TIMER_NAME}.service" <<EOF
[Unit]
Description=PiTun auto-update
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=${self} ${AUTO_APPLY_FLAG}
EOF
    cat >"/etc/systemd/system/${TIMER_NAME}.timer" <<EOF
[Unit]
Description=PiTun auto-update (daily)

[Timer]
OnCalendar=daily
# Spread load across installs and avoid the top of the hour, when the
# box is also running its own scheduled jobs.
RandomizedDelaySec=4h
Persistent=true

[Install]
WantedBy=timers.target
EOF
    systemctl daemon-reload
    systemctl enable --now "${TIMER_NAME}.timer"
    log "Timer installed: $(systemctl is-active "${TIMER_NAME}.timer")."
    log "Mode: ${AUTO_APPLY_FLAG:-apply updates automatically}"
    systemctl list-timers "${TIMER_NAME}.timer" --no-pager | tail -2
}

remove_timer() {
    systemctl disable --now "${TIMER_NAME}.timer" 2>/dev/null
    rm -f "/etc/systemd/system/${TIMER_NAME}.timer" \
          "/etc/systemd/system/${TIMER_NAME}.service"
    systemctl daemon-reload
    log "Timer removed."
}

# ── Main ─────────────────────────────────────────────────────────────────────

# Resolve versions, decide, and (unless --check) apply. Shared by the
# manual path and the agent so both behave identically.
run_update() {
    local current latest
    current="$(running_version)"
    STATE_FROM="$current"
    [[ -n "$current" ]] || log "WARNING: backend did not report a version (is it running?)"

    if ! resolve_network_path; then
        status_write failed 0 network "No network path to GitHub" false
        return 20
    fi

    if [[ -n "$WANT_VERSION" ]]; then
        latest="$WANT_VERSION"
    else
        status_write running 2 check "Checking GitHub for a newer release"
        latest="$(latest_release)"
    fi
    if [[ -z "$latest" ]]; then
        status_write failed 2 check "Could not determine the latest release" false
        log "Could not determine the latest release."
        return 20
    fi

    STATE_TO="$latest"
    log "Installed: ${current:-unknown} · latest: ${latest} · path: ${NET_PATH}"

    if [[ "$FORCE" != "1" && -n "$current" ]] && ! version_gt "$latest" "$current"; then
        log "Already up to date."
        status_write idle 100 up-to-date "Already on ${current}" true
        return 0
    fi

    if [[ "$MODE" == "check" ]]; then
        log "Update available: ${current:-unknown} → ${latest}"
        warn_if_losing_update_ui "$latest" "$current"
        status_write available 0 available "Update available: ${current:-unknown} → ${latest}"
        return 10
    fi

    warn_if_losing_update_ui "$latest" "$current"
    apply_update "$latest" || return 30
    return 0
}

main() {
    # Unattended application is OPT-IN: this box is the LAN's gateway, so
    # a silent container restart is a decision, not a default. The timer
    # reports by default; `--install-timer --apply` makes it act.
    local do_timer="" do_agent="" AUTO_APPLY_FLAG="--check"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --check)          MODE="check"; shift ;;
            --force)          FORCE=1; shift ;;
            --version)        WANT_VERSION="$2"; shift 2 ;;
            --prerelease)     ALLOW_PRERELEASE=1; shift ;;
            --no-proxy)       USE_PROXY=0; shift ;;
            --agent)          do_agent=1; shift ;;
            --install-timer)  do_timer="install"; shift ;;
            --apply)          AUTO_APPLY_FLAG=""; shift ;;
            --remove-timer)   do_timer="remove"; shift ;;
            --install-agent)  do_agent="install"; shift ;;
            --remove-agent)   do_agent="remove"; shift ;;
            -h|--help)        sed -n '2,45p' "$0"; exit 0 ;;
            *)                die "Unknown option: $1" 30 ;;
        esac
    done

    [[ "$do_timer" == "install" ]] && { install_timer; exit 0; }
    [[ "$do_timer" == "remove"  ]] && { remove_timer;  exit 0; }
    [[ "$do_agent" == "install" ]] && { install_agent; exit 0; }
    [[ "$do_agent" == "remove"  ]] && { remove_agent;  exit 0; }

    [[ "$(id -u)" == "0" ]] || die "Must run as root." 30

    # One updater at a time. Two concurrent installer runs would fight
    # over the same containers and DB snapshot.
    exec 9>"$LOCK_FILE"
    flock -n 9 || die "Another update is already running." 30

    local rc=0
    if [[ "$do_agent" == "1" ]]; then
        agent_once || rc=$?
    else
        run_update || rc=$?
    fi
    exit "$rc"
}

# Sourcing the script (tests do) must not run anything.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
