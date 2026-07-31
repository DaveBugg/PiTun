"""Release checks and UI-driven update requests.

Split of responsibility — deliberate, and the reason this module is small:

* **Checking** is safe to do in-process. It is one HTTPS GET, and the
  answer is only advisory.
* **Applying** is not. The update replaces this very container: whatever
  process runs it dies partway through, taking any HTTP response or
  WebSocket with it. So the backend only *requests* an update by dropping
  a file on the shared volume; a systemd path unit on the host
  (`scripts/pitun-update.sh --agent`) picks it up and does the work.

Progress therefore travels through a file too (`update-status.json`,
written by the agent). That is what lets the UI show what happened across
the backend restart — a stream could not survive it.

Network path mirrors the agent's: PiTun TPROXYs the host's own traffic,
so a plain request already egresses through the active node. We try
xray's local SOCKS inbound first anyway, because it *proves* the node
carries traffic, and fall back to the default route when it doesn't — an
update must never be blocked by the tunnel it might be fixing.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app.config import APP_VERSION, settings

logger = logging.getLogger(__name__)

GITHUB_REPO = os.getenv("PITUN_REPO", "DaveBugg/PiTun")
DATA_DIR = Path(os.getenv("PITUN_DATA_DIR", "/app/data"))
STATUS_FILE = DATA_DIR / "update-status.json"
REQUEST_FILE = DATA_DIR / "update-request.json"

_TIMEOUT = httpx.Timeout(15.0, connect=8.0)


# ── Version comparison ───────────────────────────────────────────────────────

def _core(version: str) -> tuple:
    """(major, minor, patch) with the `v` prefix and pre-release suffix off."""
    raw = (version or "").lstrip("vV").split("-", 1)[0]
    parts = []
    for chunk in raw.split(".")[:3]:
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    """True when `candidate` supersedes `current`.

    Numeric per component, so 1.4.10 beats 1.4.9 — a string compare gets
    that backwards. On equal cores a stable release beats its own
    pre-release (semver), which is what makes `1.5.0` an upgrade over
    `1.5.0-beta.1` but not the other way round.
    """
    if not candidate or not current:
        return False
    c, cur = _core(candidate), _core(current)
    if c != cur:
        return c > cur
    return ("-" in candidate) < ("-" in current)


# The in-UI updater (this module, its endpoints and Settings → Updates)
# ships in 1.4.8. Installing anything older REMOVES it — the box stays
# updatable, but only from a shell on it. A downgrade is a legitimate
# repair move, so this is a warning, not a block; being stranded without
# knowing why is the part worth preventing.
UPDATE_UI_SINCE = "1.4.8"


def lacks_update_ui(version: Optional[str]) -> bool:
    """True when `version` predates the in-UI updater.

    Compares CORES only, so `1.4.8-beta.1` still counts as having it —
    the feature is there, pre-release or not.
    """
    if not version:
        return False
    return _core(version) < _core(UPDATE_UI_SINCE)


# ── GitHub ───────────────────────────────────────────────────────────────────

async def _try_get(url: str, *, proxy: Optional[str]) -> Optional[httpx.Response]:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, proxy=proxy) as client:
            resp = await client.get(
                url, headers={"Accept": "application/vnd.github+json"},
            )
            resp.raise_for_status()
            return resp
    except Exception as exc:  # noqa: BLE001 — every failure means "try the next path"
        logger.debug("release check via %s failed: %s", proxy or "direct", exc)
        return None


async def _fetch(url: str, session_settings: Dict[str, str]) -> tuple[Optional[Any], str]:
    """GET `url`, preferring the active node. Returns (json, path_used)."""
    auth_on = (
        session_settings.get("lan_proxy_auth_enabled", "false").lower() == "true"
    )
    if not auth_on:
        # With LAN-proxy auth we have no credentials here; the default
        # route already goes through the same node, so skip to it.
        try:
            port = int(session_settings.get("socks_port") or settings.socks_port)
        except (TypeError, ValueError):
            port = settings.socks_port
        resp = await _try_get(url, proxy=f"socks5://127.0.0.1:{port}")
        if resp is not None:
            return resp.json(), "active node"

    resp = await _try_get(url, proxy=None)
    if resp is not None:
        return resp.json(), "direct"
    return None, "unreachable"


async def check_for_update(
    settings_map: Dict[str, str], *, prerelease: bool = False,
) -> Dict[str, Any]:
    """Ask GitHub what the newest release is and compare with ours."""
    if prerelease:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases?per_page=10"
    else:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

    payload, path = await _fetch(url, settings_map)
    if payload is None:
        return {
            "current": APP_VERSION,
            "latest": None,
            "update_available": False,
            "network_path": path,
            "target_lacks_update_ui": False,
            "update_ui_since": UPDATE_UI_SINCE,
            "error": (
                "GitHub is unreachable. If the kill switch is armed over a "
                "dead tunnel, fix the active node first."
            ),
        }

    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    latest = (payload or {}).get("tag_name")
    notes = (payload or {}).get("body") or None

    return {
        "current": APP_VERSION,
        "latest": latest,
        "update_available": bool(latest) and is_newer(latest, APP_VERSION),
        "network_path": path,
        # Only a real step DOWN loses something: if we already lack the
        # UI, installing another old build takes nothing away.
        "target_lacks_update_ui": (
            lacks_update_ui(latest) and not lacks_update_ui(APP_VERSION)
        ),
        "update_ui_since": UPDATE_UI_SINCE,
        "published_at": (payload or {}).get("published_at"),
        "notes": (notes[:4000] if isinstance(notes, str) else None),
        "error": None,
    }


# ── Status / request files ───────────────────────────────────────────────────

def read_status() -> Dict[str, Any]:
    """Last known state of the agent. Survives our own restart, which is
    the whole point — the update kills this process mid-flight."""
    try:
        raw = STATUS_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {"state": "idle", "pct": 0, "step": "", "message": "", "ok": None}
    except OSError as exc:
        logger.warning("update status unreadable: %s", exc)
        return {"state": "unknown", "pct": 0, "step": "", "message": str(exc), "ok": None}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # The agent writes atomically, so this means someone else
        # clobbered it — report rather than crash the endpoint.
        return {
            "state": "unknown", "pct": 0, "step": "",
            "message": "status file is not valid JSON", "ok": None,
        }


def request_pending() -> bool:
    return REQUEST_FILE.exists()


def write_request(
    *, version: Optional[str] = None, force: bool = False, prerelease: bool = False,
) -> Dict[str, Any]:
    """Ask the host agent to run an update.

    Written atomically: the agent is triggered by the file APPEARING, so a
    half-written request would be read and consumed as garbage.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    body = {
        "version": version or "",
        "force": bool(force),
        "prerelease": bool(prerelease),
        "requested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    fd, tmp = tempfile.mkstemp(dir=str(DATA_DIR), prefix=".update-request.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(body, fh)
        os.replace(tmp, REQUEST_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    # Seed a status so the UI has something to render immediately —
    # the agent may take a second or two to be scheduled.
    try:
        STATUS_FILE.write_text(json.dumps({
            "state": "queued", "pct": 0, "step": "queued",
            "message": "Waiting for the update agent to pick this up",
            "from": APP_VERSION, "to": version or "latest", "ok": None,
            "updated_at": body["requested_at"],
        }), encoding="utf-8")
    except OSError:
        pass
    return body
