"""Updating the xray core that runs inside an x-ui panel, from PiTun.

The panel already does the work (`installXray/<version>`): it downloads the
release, swaps the binary and restarts xray. What PiTun adds is the guard
rails:

* A panel older than 3.8.0 does not rewrite its stored xray templates for the
  keys xray 26.9 refuses (`proxySettings`, a freedom `domainStrategy`, …), so
  an xray at or above 26.9.9 under such a panel may not start. The panel must
  be updated first.
* The version must be one the panel offers, and is checked again after the
  install: the new core has to be running and report the requested version.
* Rolling back is the same call with the previous version.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

Version = Tuple[int, ...]

MIN_PANEL_FOR_NEW_XRAY: Version = (3, 8, 0)
NEW_XRAY_FROM: Version = (26, 9, 9)

_VERSION_RE = re.compile(r"^v?(\d{1,3})\.(\d{1,3})\.(\d{1,3})$")


def parse_version(raw: Optional[str]) -> Optional[Version]:
    """`v26.10.31` / `26.10.31` / `3.11.0` -> (26, 10, 31); None if it is not one."""
    m = _VERSION_RE.match((raw or "").strip())
    return tuple(int(g) for g in m.groups()) if m else None


def normalize_tag(raw: str) -> Optional[str]:
    v = parse_version(raw)
    return "v" + ".".join(str(n) for n in v) if v else None


def plain(raw: str) -> str:
    return (raw or "").strip().lstrip("v")


def check_target(panel_version: Optional[str], target: str) -> Optional[str]:
    """Why `target` must not be installed on this panel, or None when it may."""
    t = parse_version(target)
    if t is None:
        return "That is not a version number."
    p = parse_version(panel_version)
    if t >= NEW_XRAY_FROM and (p is None or p < MIN_PANEL_FOR_NEW_XRAY):
        shown = panel_version or "unknown"
        return (
            f"Panel {shown} does not migrate its stored xray templates for xray 26.9 and newer, "
            "so the new core may fail to start. Update the panel to 3.8.0 or newer first."
        )
    return None


def describe(status: Dict[str, Any], versions: List[str]) -> Dict[str, Any]:
    """Shape the panel's `status` + `getXrayVersion` for the UI."""
    xray = (status.get("xray") or {}) if isinstance(status, dict) else {}
    panel_version = status.get("panelVersion") if isinstance(status, dict) else None
    current = plain(str(xray.get("version") or ""))
    cur_t = parse_version(current)
    items = []
    for tag in versions:
        v = parse_version(tag)
        if v is None:
            continue
        reason = check_target(panel_version, tag)
        items.append({
            "version": ".".join(str(n) for n in v),
            "tag": "v" + ".".join(str(n) for n in v),
            "newer": bool(cur_t and v > cur_t),
            "current": bool(cur_t and v == cur_t),
            "blocked": reason,
        })
    items.sort(key=lambda i: parse_version(i["version"]) or (), reverse=True)
    return {
        "panel_version": panel_version,
        "xray_version": current or None,
        "xray_state": xray.get("state"),
        "xray_error": xray.get("errorMsg") or None,
        "versions": items,
    }
