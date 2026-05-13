#!/usr/bin/env bash
#
# scripts/cleanup-go.sh — sweep Go SDK + build cache + workspace after
# the caller's install step is done. Idempotent + safe to source from
# any post-install context.
#
# Both setup-xui-server.sh and setup-naive-server.sh bootstrap a full
# Go toolchain (xcaddy needs it to build Caddy with klzgrad/forwardproxy;
# upstream x-ui-pro.sh's installer occasionally drops Go too — leftover
# from older revisions). On a 10 GB VPS that's ~2.5 GB of dead weight
# at runtime (~/go = 1.6 GB, ~/.cache/go-build = 600 MB,
# /usr/local/go = 250 MB), so we collapse it once the caller is sure
# the install actually succeeded.
#
# What we DON'T touch:
#   * /usr/local/bin/caddy, /usr/local/bin/xcaddy — runtime binaries
#   * /usr/local/x-ui/         — x-ui runtime + DB
#   * /root/randomfakehtml-master/ — fakesite archive (rotation reuses)
#
# Re-running the parent installer re-downloads Go on demand. The
# bootstrap is ~80 MB and adds maybe a minute to a fresh install.
#
# Usage (from another script):
#     bash "$(dirname "$0")/cleanup-go.sh"
# or, when sourced for env propagation:
#     source "$(dirname "$0")/cleanup-go.sh"

set -u

# Only run if the parent install left something we actually own.
# Skip silently otherwise — the operator may have their own Go install
# (homedirs etc.) we shouldn't be touching.
_target_exists=0
for p in /root/go /root/.cache/go-build /usr/local/go; do
    [[ -e "$p" ]] && _target_exists=1
done

if [[ "$_target_exists" -eq 1 ]]; then
    rm -rf /root/go /root/.cache/go-build /usr/local/go 2>/dev/null || true
    # `go` symlink lives in /usr/local/bin and points into the deleted
    # SDK — drop it so a future `command -v go` honestly reports
    # missing rather than dangling-symlink-but-still-found.
    [[ -L /usr/local/bin/go ]] && rm -f /usr/local/bin/go
    echo "[pitun] cleanup-go.sh: removed Go SDK / cache / workspace"
fi

# Also drop the randomfakehtml master.zip the x-ui-pro installer
# leaves behind. The extracted directory stays — fakesite rotation
# needs it. The zip is just an extraction artifact.
[[ -f /root/randomfakehtml-master.zip ]] && rm -f /root/randomfakehtml-master.zip
