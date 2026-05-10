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
SupportedProtocol = Literal["naive", "wireguard"]
SUPPORTED_PROTOCOLS: tuple[str, ...] = ("naive", "wireguard")

_SCRIPT_PATH_BY_PROTOCOL: dict[str, str] = {
    "naive": "scripts/setup-naive-server.sh",
    "wireguard": "scripts/setup-wireguard-server.sh",
}

# Symmetric uninstall scripts — wipe the server-side state the
# install scripts above set up. Used by `POST /servers/{id}/uninstall/
# {protocol}` for the "I want to redeploy from scratch on this VPS"
# flow. The scripts are idempotent so re-running them on an already-
# clean system is a safe no-op.
_UNINSTALL_SCRIPT_PATH_BY_PROTOCOL: dict[str, str] = {
    "naive": "scripts/uninstall-naive-server.sh",
    "wireguard": "scripts/uninstall-wireguard-server.sh",
}

# URI scheme prefix per protocol — used both for the regex anchor below
# and for response-shape sanity checks.
_URI_SCHEME_BY_PROTOCOL: dict[str, str] = {
    "naive": "naive+https://",
    "wireguard": "wireguard://",
}

# Protocols where each deploy invocation produces ONE peer config that
# becomes a `DeploymentClient` row (and may later be exported to a
# Node). Protocols outside this set follow the legacy single-tunnel
# model: deploy → URI parse → Node row directly via
# `ServerDeployment.last_node_id`.
MULTI_CLIENT_PROTOCOLS: tuple[str, ...] = ("wireguard",)


@dataclass(frozen=True)
class DeployPlan:
    """Resolved plan for a single deploy invocation. The caller hands
    this to `exec_remote_script(server, plan.script_content, plan.env)`.
    """
    protocol: str
    script_content: str
    env: dict[str, str]


def _repo_root() -> Path:
    """Locate the directory that contains the `scripts/` folder we need
    to read install scripts from. Walks up from this module's path and
    returns the first ancestor with a `scripts` subdir.

    Why a walk-up instead of a fixed `parents[N]`:
      * Dev / CI host: this file is at `<repo>/backend/app/core/deploy.py`
        and `scripts/` lives at `<repo>/scripts/` — `parents[3]` worked.
      * Production Docker container: bind-mount layout is
        `/app/app/core/deploy.py` (3 dirs above = `/app`) and
        `/app/scripts/` (sibling). `parents[3]` would resolve to `/`
        which doesn't have `scripts/` — historic bug surfaced during
        v1.3.0-beta.1 smoke testing on a VM.
      * The walk-up is robust to either layout without hard-coding the
        path-component count.

    Falls back to `parents[3]` if no ancestor has `scripts/` so the
    error message at `load_script()` still points at a sensible-looking
    path (instead of `/`) for debugging.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "scripts").is_dir():
            return parent
    return here.parents[3]


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


def load_uninstall_script(protocol: str) -> str:
    """Read the `uninstall-<protocol>-server.sh` script. Symmetric
    to `load_script` but for the wipe path. Same SFTP-upload + env-
    parametrise flow; the env we ship is just `YES=1` to skip the
    interactive confirm prompt."""
    if protocol not in _UNINSTALL_SCRIPT_PATH_BY_PROTOCOL:
        raise ValueError(
            f"Unsupported protocol {protocol!r}; expected one of "
            f"{tuple(_UNINSTALL_SCRIPT_PATH_BY_PROTOCOL.keys())}"
        )
    script_path = _repo_root() / _UNINSTALL_SCRIPT_PATH_BY_PROTOCOL[protocol]
    if not script_path.exists():
        raise FileNotFoundError(f"uninstall script not found at {script_path}")
    return script_path.read_text(encoding="utf-8")


def build_naive_env(
    *,
    domain: str,
    email: str,
    naive_user: Optional[str] = None,
    naive_pass: Optional[str] = None,
    template_id: Optional[str] = None,
    install_php: bool = False,
    ssh_port: Optional[int] = None,
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

    # Resolve the optional decoy-site template id to its env-var
    # representation. Unknown / unset ids fall back to the script's
    # built-in default (DECOY_REPO=daleharvey/pacman). See
    # `app.core.templates.resolve_to_env` for the full mapping.
    from app.core.templates import resolve_to_env as _tpl_env, get_template
    template_env = _tpl_env(template_id)

    # If the picked template requires PHP (e.g. fake-2fa), force the
    # PHP install on. The user can also enable it manually via the
    # checkbox; this guard ensures a php-needing template never gets
    # deployed against a static-only Caddy by accident.
    php_required_by_template = False
    if template_id:
        tpl = get_template(template_id)
        if tpl is not None and getattr(tpl, "requires_php", False):
            php_required_by_template = True

    env_install_php = "yes" if (install_php or php_required_by_template) else "no"

    # SSH port — explicit opt-in via UI. Empty / 22 / out-of-range is
    # treated as no-op by the script (validated server-side too).
    env_ssh_port = ""
    if ssh_port is not None:
        try:
            n = int(ssh_port)
            if 1 <= n <= 65535:
                env_ssh_port = str(n)
        except (TypeError, ValueError):
            pass

    return {
        "DOMAIN": domain,
        "EMAIL": email,
        "NAIVE_USER": naive_user or "pitun",
        "NAIVE_PASS": naive_pass or secrets.token_urlsafe(24),
        "INSTALL_PHP": env_install_php,
        # `SSH_PORT` empty string in the env-dict still becomes an
        # `export SSH_PORT=` in the remote bash prelude (shlex-quoted
        # empty), and setup-naive-server.sh's section 9b treats empty
        # as no-op. Same handling for the wireguard script.
        "SSH_PORT": env_ssh_port,
        # Template overrides (mutually exclusive at the script level —
        # `TEMPLATE_HTML_URL` wins when both are set).
        **template_env,
        # Skip the script's interactive read prompts. setup-naive-server.sh
        # uses bash `read -r -p` for SSH-hardening / fail2ban / "continue
        # anyway?" questions, which block forever when the script runs
        # under our auto-deploy PTY (see v1.3.0-beta.1 smoke test —
        # the script slept on `read` waiting for input the user can't
        # provide through the WS log panel). Each of these read calls
        # has an env-var fallback; setting them up-front makes the
        # script run end-to-end non-interactively.
        #
        # Defaults chosen for least-surprise on a remote auto-deploy:
        #   HARDEN_SSH=no            — never touch the SSH config the
        #                              admin used to register the
        #                              Server in PiTun; locking
        #                              ourselves out is the worst
        #                              possible failure mode.
        #   INSTALL_FAIL2BAN=yes     — recommended baseline brute-force
        #                              protection, low risk to add.
        #   PITUN_AUTO_CONTINUE=yes  — accept the script's late "continue
        #                              anyway?" prompt (DNS warning,
        #                              etc.) so a soft-warning doesn't
        #                              wedge us. The script still hard-
        #                              `err`s on actual blockers.
        "HARDEN_SSH": "no",
        "INSTALL_FAIL2BAN": "yes",
        "PITUN_AUTO_CONTINUE": "yes",
    }


def build_wireguard_env(
    *,
    client_name: str,
    server_port: Optional[int] = None,
    wg_network_4: Optional[str] = None,
    wg_network_6: Optional[str] = None,
    dns_1: Optional[str] = None,
    dns_2: Optional[str] = None,
    allowed_ips: Optional[str] = None,
    server_pub_ip: Optional[str] = None,
    ssh_port: Optional[int] = None,
    sub_command: Literal["install", "add-client", "remove-client", "list-clients", "get-conf"] = "install",
) -> dict[str, str]:
    """Build the env-var dict for `setup-wireguard-server.sh`.

    The script's behaviour is sub-command driven (the first CLI arg).
    `core/ssh.py.exec_remote_script_streaming` doesn't pass extra args
    by default, so we encode the sub-command as the first command-line
    arg via the `_build_remote_command` env injector — see
    PITUN_WG_SUBCOMMAND below.

    `client_name` is required for everything except `list-clients`;
    we still validate non-empty here to surface API errors early.

    Defaults mirror `setup-wireguard-server.sh`:
      * server_port: 51820
      * wg_network_4: 10.66.66.0/24
      * wg_network_6: fd42:42:42::/64
      * dns_1 / dns_2: 1.1.1.1 / 1.0.0.1
      * allowed_ips: 0.0.0.0/0,::/0
      * server_pub_ip: autodetected on the VPS
    """
    if sub_command != "list-clients" and not client_name:
        raise ValueError("client_name is required for sub_command=" + sub_command)

    env: dict[str, str] = {
        # The sub-command is read by `_build_remote_command` to set $1.
        # See `core/ssh.py` — when a SCRIPT_ARGS env var is set, the
        # remote shell appends its tokens after the script path.
        "PITUN_WG_SUBCOMMAND": sub_command,
    }
    if client_name:
        env["CLIENT_NAME"] = client_name
    if server_port is not None:
        env["SERVER_PORT"] = str(server_port)
    if wg_network_4:
        env["WG_NETWORK_4"] = wg_network_4
    if wg_network_6:
        env["WG_NETWORK_6"] = wg_network_6
    if dns_1:
        env["DNS_1"] = dns_1
    if dns_2:
        env["DNS_2"] = dns_2
    if allowed_ips:
        env["ALLOWED_IPS"] = allowed_ips
    if server_pub_ip:
        env["SERVER_PUB_IP"] = server_pub_ip
    # SSH port — only meaningful on `install` (subsequent add-client etc.
    # never touch sshd). Validated to 1-65535 to keep the script's bash
    # parser from blowing up; out-of-range becomes "" → script no-op.
    if ssh_port is not None and sub_command == "install":
        try:
            n = int(ssh_port)
            if 1 <= n <= 65535:
                env["SSH_PORT"] = str(n)
        except (TypeError, ValueError):
            pass
    return env


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
            template_id=config.get("template_id"),
            install_php=bool(config.get("install_php", False)),
            ssh_port=config.get("ssh_port"),
        )
    elif protocol == "wireguard":
        # WG `install` sub-command bootstraps the server AND adds the
        # first client in one go. Subsequent add-client/remove/list/
        # get-conf invocations are issued via dedicated API endpoints
        # that build their own env (see api/server_clients.py).
        # Frontend lets `client_name` stay blank — default to "client1"
        # so install always has a peer to create (mirrors naive_user
        # auto-default behaviour).
        env = build_wireguard_env(
            client_name=(config.get("client_name") or "client1"),
            server_port=config.get("server_port"),
            wg_network_4=config.get("wg_network_4"),
            wg_network_6=config.get("wg_network_6"),
            dns_1=config.get("dns_1"),
            dns_2=config.get("dns_2"),
            allowed_ips=config.get("allowed_ips"),
            server_pub_ip=config.get("server_pub_ip"),
            ssh_port=config.get("ssh_port"),
            sub_command="install",
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
