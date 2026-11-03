"""Updating an x-ui panel from PiTun, to the one release PiTun has tested.

The panel's own "update" button would fetch an unpinned updater and move to
whatever is newest, and a newer panel may change the API PiTun talks to. So
PiTun does it over SSH instead: upstream's own `update.sh`, taken at the commit
of the pinned release and checked against a sha256 (the same pinning the
installer uses), told to install exactly that release tag. The release archive
is verified by that script against the checksum published next to it.

An update runs the new version's database migrations once and cannot be undone,
so PiTun copies the panel database on the VPS first and refuses to go on if the
copy was not made. Bump the constants below together with `XUI_VERSION` and
`XUI_INSTALL_SHA` in scripts/setup-xui-server.sh once a newer panel is tested.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from app.core.xui_xray import parse_version

DB_PATH = "/etc/x-ui/x-ui.db"

TARGET_VERSION = "3.11.0"
TARGET_TAG = "v3.11.0"
# MHSanaei/3x-ui @ v3.11.0 (lightweight tag -> commit), and that commit's update.sh.
UPDATE_SH_COMMIT = "c621ba9014a6aa21fd7c9ecf45655e44e24ebf13"
UPDATE_SH_SHA256 = "c3512d562ccc52091ff6480027cdfc4b62e159577dd67ba119867466bca784ac"

UPDATE_SCRIPT = r"""set -u
TMP="$(mktemp /tmp/pitun-xui-update.XXXXXX)"
trap 'rm -f "$TMP"' EXIT
curl -fsSL --retry 3 --connect-timeout 15 --max-time 60 -o "$TMP" \
  "https://raw.githubusercontent.com/MHSanaei/3x-ui/__COMMIT__/update.sh" \
  || { echo "PITUN_UPDATE_ERR=could not download the updater"; exit 4; }
echo "__SHA256__  $TMP" | sha256sum -c - >/dev/null 2>&1 \
  || { echo "PITUN_UPDATE_ERR=updater checksum mismatch, refusing to run it"; exit 5; }
set +e
XUI_UPDATE_TAG="__TAG__" bash "$TMP"
rc=$?
echo "PITUN_UPDATE_EXIT=$rc"
exit $rc
""".replace("__COMMIT__", UPDATE_SH_COMMIT).replace("__SHA256__", UPDATE_SH_SHA256).replace("__TAG__", TARGET_TAG)

BACKUP_SCRIPT = r"""set -e
DB=/etc/x-ui/x-ui.db
if [ ! -f "$DB" ]; then echo "PITUN_BACKUP_ERR=no database at $DB"; exit 2; fi
DEST="$DB.pitun-pre-update-$(date +%Y%m%d-%H%M%S)"
if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB" ".backup '$DEST'"
else
  cp -p "$DB" "$DEST"
  [ -f "$DB-wal" ] && cp -p "$DB-wal" "$DEST-wal"
fi
chmod 600 "$DEST"
[ -s "$DEST" ] || { echo "PITUN_BACKUP_ERR=copy is empty"; exit 3; }
echo "PITUN_BACKUP=$DEST"
"""


def parse_backup_path(stdout: str) -> Optional[str]:
    for line in (stdout or "").splitlines():
        if line.startswith("PITUN_BACKUP="):
            path = line.split("=", 1)[1].strip()
            return path if path.startswith(DB_PATH + ".pitun-pre-update-") else None
    return None


def describe(current: Optional[str]) -> Dict[str, Any]:
    """What the UI shows: the panel's version against the pinned target."""
    cur = (current or "").strip() or None
    c, tgt = parse_version(cur), parse_version(TARGET_VERSION)
    return {
        "supported": True,
        "channel": "pinned",
        "current": cur,
        "latest": TARGET_VERSION,
        "update_available": bool(c and tgt and tgt > c),
    }
