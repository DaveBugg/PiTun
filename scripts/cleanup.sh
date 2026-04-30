#!/usr/bin/env bash
# PiTun maintenance cleanup — run daily via cron
# Removes dangling Docker images, old containers, build cache, and temp files.
set -euo pipefail

echo "[$(date -Is)] PiTun cleanup starting"

docker image prune -f --filter "until=24h" 2>/dev/null || true

docker container prune -f --filter "until=24h" 2>/dev/null || true

docker builder prune -f --filter "until=48h" 2>/dev/null || true

find /tmp/pitun -name "*.json" ! -name "config.json" -mtime +7 -delete 2>/dev/null || true

if command -v journalctl &>/dev/null; then
    journalctl --vacuum-size=50M 2>/dev/null || true
fi

# Restart xray to prevent memory leaks (xray grows ~500MB over time)
if pgrep -f "xray run" > /dev/null 2>&1; then
    curl -s -X POST http://localhost/api/system/reload-config \
        -H "Content-Type: application/json" 2>/dev/null || true
    echo "  xray restarted"
fi

echo "[$(date -Is)] PiTun cleanup done"
