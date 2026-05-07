"""Server-side auto-deploy helpers (since v1.3.0).

Companion to `core/ssh.py.exec_remote_script` — turns a per-protocol
deployment plan into:
  1. The local install script content (read from `scripts/setup-*.sh`)
  2. The set of environment variables the script expects
  3. A regex parser that pulls the resulting proxy URI out of stdout

The split keeps `core/ssh.py` protocol-agnostic (it just runs whatever
script it's handed) and isolates protocol knowledge here.

Phase 1 (v1.3.0-beta.1) ships only the `naive` protocol. xray-vless and
hysteria2 follow in beta.2 / beta.3 once the SSH execution flow is
proven on the simpler case.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)


# Protocols supported by auto-deploy. Adding a new protocol is:
#   1. Drop a `setup-<name>-server.sh` in `scripts/` that ends with
#      a `URI=<uri>` line (see scripts/setup-naive-server.sh for the
#      contract).
#   2. Add the literal here + a config-validator + URI regex below.
#   3. Add a script-name mapping in `_SCRIPT_PATH_BY_PROTOCOL`.
SupportedProtocol = Literal["naive"]
SUPPORTED_PROTOCOLS: tuple[str, ...] = ("naive",)

_SCRIPT_PATH_BY_PROTOCOL: dict[str, str] = {
    "naive": "scripts/setup-naive-server.sh",
}

# URI scheme prefix per protocol — used both for the regex anchor below
# and for response-shape sanity checks.
_URI_SCHEME_BY_PROTOCOL: dict[str, str] = {
    "naive": "naive+https://",
}


@dataclass(frozen=True)
class DeployPlan:
    """Resolved plan for a single deploy invocation. The caller hands
    this to `exec_remote_script(server, plan.script_content, plan.env)`.
    """
    protocol: str
    script_content: str
    env: dict[str, str]


def _repo_root() -> Path:
    """Locate the repo root (the dir containing both `backend/` and
    `scripts/`). We resolve relative to this file so dev-server runs
    (`uvicorn app.main:app`) and the Docker image both find it
    regardless of cwd. The Docker image bind-mounts `./backend` and
    `./scripts` at the same parent, so the layout matches.
    """
    return Path(__file__).resolve().parents[3]


def load_script(protocol: str) -> str:
    """Read the `setup-<protocol>-server.sh` script from the repo's
    `scripts/` directory. Raises `FileNotFoundError` if missing.

    The script is uploaded as-is to the remote VPS via SFTP; env vars
    in `DeployPlan.env` parameterise it (see `setup-naive-server.sh`
    for the supported names: DOMAIN, EMAIL, NAIVE_USER, NAIVE_PASS).
    """
    if protocol not in _SCRIPT_PATH_BY_PROTOCOL:
        raise ValueError(
            f"Unsupported protocol {protocol!r}; expected one of {SUPPORTED_PROTOCOLS}"
        )
    script_path = _repo_root() / _SCRIPT_PATH_BY_PROTOCOL[protocol]
    if not script_path.exists():
        raise FileNotFoundError(f"install script not found at {script_path}")
    return script_path.read_text(encoding="utf-8")


def build_naive_env(
    *,
    domain: str,
    email: str,
    naive_user: Optional[str] = None,
    naive_pass: Optional[str] = None,
) -> dict[str, str]:
    """Build the env-var dict for `setup-naive-server.sh`.

    Defaults mirror `api/servers.build_naive_install_script`:
      * `naive_user` defaults to `pitun`
      * `naive_pass` is auto-generated when absent (24-byte URL-safe)

    All values are returned as plain strings — escaping happens at the
    SFTP/exec boundary in `core/ssh.py` (we shlex-quote before injecting
    into the remote `bash -c "<env=val ...> /tmp/script.sh"` line).
    """
    if not domain:
        raise ValueError("domain is required")
    if not email:
        raise ValueError("email is required")
    return {
        "DOMAIN": domain,
        "EMAIL": email,
        "NAIVE_USER": naive_user or "pitun",
        "NAIVE_PASS": naive_pass or secrets.token_urlsafe(24),
    }


def build_plan(protocol: str, config: dict) -> DeployPlan:
    """Compose a DeployPlan from a request body. `config` is the
    protocol-specific JSON object the API client sent.

    Future protocols slot in here as elif branches.
    """
    if protocol == "naive":
        env = build_naive_env(
            domain=config.get("domain", ""),
            email=config.get("email", ""),
            naive_user=config.get("naive_user"),
            naive_pass=config.get("naive_pass"),
        )
    else:
        raise ValueError(
            f"Unsupported protocol {protocol!r}; expected one of {SUPPORTED_PROTOCOLS}"
        )

    return DeployPlan(
        protocol=protocol,
        script_content=load_script(protocol),
        env=env,
    )


# ── URI extraction ────────────────────────────────────────────────────────────


# Matches a single line `URI=<scheme>://<rest>` with no surrounding
# whitespace except the trailing newline. The `(?P<uri>...)` group
# captures the full URI for downstream consumers. Multiline + ignore
# case so future scripts can use either `URI=` or `Uri=`.
_URI_LINE_RE = re.compile(
    r"^URI=(?P<uri>\S+)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def extract_uri(stdout: str, protocol: str) -> Optional[str]:
    """Pull the proxy URI out of a successful install script's stdout.

    Contract enforced by the install scripts (see
    `scripts/setup-naive-server.sh`):
      * Last meaningful stdout line is `URI=<uri>`
      * The URI uses the protocol's expected scheme prefix
        (`naive+https://` for naive, etc.)

    Returns the URI string on success, None if no URI line was found.
    Caller decides what to do with None — typically log a warning and
    surface the deployment as `status=deployed_no_uri` so the admin
    can grab credentials manually from the captured stdout.

    Note: scans from the END of stdout backwards so a curl-output
    `URI=value` echoed mid-run (e.g. for diagnostics) won't shadow
    the canonical end-of-script line.
    """
    if not stdout:
        return None

    expected_scheme = _URI_SCHEME_BY_PROTOCOL.get(protocol)

    # Iterate matches in document order, then take the LAST one — the
    # canonical line is always the closing line of the script.
    matches = list(_URI_LINE_RE.finditer(stdout))
    if not matches:
        return None

    # Walk matches in reverse, prefer one matching the expected scheme.
    # Falls back to the last URI= line regardless of scheme so admins
    # debugging a misbehaving script still see SOMETHING in the response.
    for m in reversed(matches):
        uri = m.group("uri").strip()
        if expected_scheme and uri.lower().startswith(expected_scheme.lower()):
            return uri
    # No scheme match — return the last URI= line we saw (advisory).
    return matches[-1].group("uri").strip()
