"""Which ports are free on an x-ui VPS.

Pure helpers: the API layer feeds them the panel's inbound list plus the
raw output of one SSH round-trip (`ss` + `ufw status`) and gets back a
per-port verdict. Nothing here touches the network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

DEFAULT_CANDIDATES = (443, 8443, 2443)
MAX_CANDIDATES = 20

# One SSH round-trip; the markers split the sections for the parser.
PORT_CHECK_SCRIPT = r"""#!/usr/bin/env bash
echo "##SS"
ss -Htulnp 2>/dev/null || ss -tulnp 2>/dev/null | tail -n +2
echo "##UFW"
if command -v ufw >/dev/null 2>&1; then
  ufw status 2>/dev/null || echo "Status: unknown"
else
  echo "Status: absent"
fi
"""

_PROC_RE = re.compile(r'\("([^"]+)"')


def parse_candidates(raw: Optional[str]) -> List[int]:
    """`"443, 8443,2443"` → `[443, 8443, 2443]` (deduped, order kept).
    Raises ValueError on junk or out-of-range values."""
    if raw is None or not raw.strip():
        return list(DEFAULT_CANDIDATES)
    out: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise ValueError(f"not a port: {part!r}")
        port = int(part)
        if not 1 <= port <= 65535:
            raise ValueError(f"port out of range: {port}")
        if port not in out:
            out.append(port)
    if not out:
        return list(DEFAULT_CANDIDATES)
    if len(out) > MAX_CANDIDATES:
        raise ValueError(f"at most {MAX_CANDIDATES} ports per check")
    return out


def split_sections(stdout: str) -> Dict[str, str]:
    sections: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in stdout.splitlines():
        if line.startswith("##") and line[2:].strip().isalpha():
            current = line[2:].strip().upper()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    return {k: "\n".join(v) for k, v in sections.items()}


def parse_ss_listeners(text: str) -> Dict[int, Set[str]]:
    """`ss -Htulnp` → {port: {process names}}. A listener whose process
    isn't visible (non-root ss) is recorded as "?"."""
    listeners: Dict[int, Set[str]] = {}
    for line in text.splitlines():
        cols = line.split()
        if len(cols) < 5:
            continue
        # With -u the first column is the netid (tcp/udp); local address
        # is the first column that looks like host:port after the queues.
        local = next(
            (c for c in cols[1:] if ":" in c and c.rsplit(":", 1)[-1].isdigit()),
            None,
        )
        if local is None:
            continue
        port = int(local.rsplit(":", 1)[-1])
        names = set(_PROC_RE.findall(line)) or {"?"}
        listeners.setdefault(port, set()).update(names)
    return listeners


@dataclass
class UfwState:
    status: str  # "active" | "inactive" | "absent" | "unknown"
    allowed: List[tuple[int, int]] = field(default_factory=list)  # inclusive ranges

    def port_state(self, port: int) -> str:
        if self.status != "active":
            return self.status
        for lo, hi in self.allowed:
            if lo <= port <= hi:
                return "allow"
        return "closed"


def parse_ufw(text: str) -> UfwState:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return UfwState("unknown")
    head = lines[0].lower()
    if "inactive" in head:
        return UfwState("inactive")
    if "absent" in head:
        return UfwState("absent")
    if "active" not in head:
        return UfwState("unknown")
    allowed: List[tuple[int, int]] = []
    for ln in lines[1:]:
        # "443/tcp  ALLOW  Anywhere", "2000:3000/tcp (v6) ALLOW ...",
        # "8443 ALLOW IN Anywhere". App profiles ("OpenSSH") are skipped.
        m = re.match(r"^(\d+)(?::(\d+))?(?:/(tcp|udp))?\b.*\bALLOW\b", ln)
        if not m:
            continue
        lo = int(m.group(1))
        hi = int(m.group(2)) if m.group(2) else lo
        allowed.append((lo, hi))
    return UfwState("active", allowed)


def classify_ports(
    *,
    candidates: Iterable[int],
    inbounds: List[Dict[str, Any]],
    known_inbound_ids: Set[int],
    mode: Optional[str],
    panel_port: Optional[int],
    listeners: Optional[Dict[int, Set[str]]],
    ufw: Optional[UfwState],
) -> List[Dict[str, Any]]:
    """Per-port verdict. `listeners` / `ufw` are None when the SSH layer
    couldn't run — the verdict then rests on the panel alone."""
    by_port: Dict[int, List[Dict[str, Any]]] = {}
    for ib in inbounds:
        try:
            by_port.setdefault(int(ib.get("port") or 0), []).append(ib)
        except (TypeError, ValueError):
            continue

    out: List[Dict[str, Any]] = []
    for port in candidates:
        ibs = by_port.get(port, [])
        procs = sorted(listeners.get(port, set())) if listeners is not None else None
        row: Dict[str, Any] = {
            "port": port,
            "status": "free",
            "reason": None,
            "inbounds": [
                {
                    "id": int(ib.get("id") or 0),
                    "remark": ib.get("remark") or "",
                    "protocol": ib.get("protocol") or "",
                    "enabled": bool(ib.get("enable", True)),
                    "known_here": int(ib.get("id") or 0) in known_inbound_ids,
                }
                for ib in ibs
            ],
            "listeners": procs,
            "ufw": ufw.port_state(port) if ufw is not None else None,
        }
        if mode == "xui-pro" and port in (80, 443):
            row["status"] = "reserved"
            row["reason"] = "panel HTTPS (nginx / ACME)"
        elif panel_port and port == panel_port:
            row["status"] = "reserved"
            row["reason"] = "x-ui panel port"
        elif ibs:
            row["status"] = "taken"
            first = ibs[0]
            row["reason"] = f"inbound #{first.get('id')} {first.get('remark') or ''}".strip()
        elif procs:
            row["status"] = "taken"
            row["reason"] = "listening: " + ", ".join(procs)
        out.append(row)
    return out
