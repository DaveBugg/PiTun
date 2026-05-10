"""Parser for the `xui://` URI format emitted by `setup-xui-server.sh`.

Format
------
    xui://<api_token>@<host>:<port><basepath>?user=<u>&pass=<p>&domain=<d>&mode=<m>

Fields:
  * `api_token`      Bearer token (URL-encoded if it contains `+/=`, but
                     `setup-xui-server.sh` chooses URL-safe charset so
                     decoding is a no-op in practice).
  * `host`           IPv4/v6/DNS — for x-ui-pro mode this matches the
                     domain that nginx serves the fakesite on; for bare
                     mode it's the raw VPS IP.
  * `port`           Random panel port (30000-60000 by default).
  * `basepath`       URL path prefix the 3x-ui panel sits behind. Starts
                     with `/`, may or may not end with `/` — we
                     normalise to no-trailing-slash for downstream API
                     calls (the panel routes `/foo` → 200 and `/foo/`
                     → 200 identically, but our base-URL builder
                     concatenates `/login` etc. and a trailing slash
                     would create `//login`).
  * `?user=<u>`      Panel admin login (URL-encoded). Stored so PiTun's
                     UI can deep-link the user into the panel; the API
                     itself uses `api_token` not user/pass.
  * `?pass=<p>`      Panel admin password (URL-encoded).
  * `?domain=<d>`    Empty in bare mode, set to the cert domain in
                     x-ui-pro mode. UI uses this to render a "Open
                     panel" button against the proper hostname.
  * `?mode=<m>`      `bare` | `xui-pro` — drives feature gating in the
                     PiTun UI (e.g. fakesite picker only available in
                     x-ui-pro mode).

Why a separate parser
---------------------
`uri_parser.py` is for VPN-client URIs (vless://, wg://, etc.) → each
yields a `Node` config. An `xui://` URI is panel credentials → a
`XuiServer` row, not a Node. Keeping them in different modules avoids
the registry of node parsers having to special-case a non-node entry.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional
from urllib.parse import unquote, urlparse


@dataclass(frozen=True)
class XuiServerConfig:
    """Decoded `xui://` URI. Plain Python dataclass — DB row mapping
    happens at the call site (the `XuiServer` SQLModel lives in
    `app/models.py` once it lands)."""

    api_token: str
    host: str
    port: int
    basepath: str  # normalised: starts with `/`, no trailing `/`
    panel_user: str
    panel_pass: str
    domain: Optional[str]
    mode: Literal["bare", "xui-pro"]


def parse_xui_uri(uri: str) -> Optional[XuiServerConfig]:
    """Parse an `xui://<token>@<host>:<port><path>?...` URI.

    Returns None on any malformed input rather than raising — call
    sites typically validate at API boundaries (`/servers` create
    endpoint) and want a clean 400 with a single error path.
    """
    if not uri or not uri.startswith("xui://"):
        return None

    # urlparse handles auth + host + port + path + query in one shot.
    # The token is the "userinfo" component (no `:password`), so we
    # read it from `parsed.username`.
    try:
        parsed = urlparse(uri)
    except ValueError:
        return None

    if parsed.scheme != "xui":
        return None
    if not parsed.username or not parsed.hostname or not parsed.port:
        return None

    api_token = unquote(parsed.username)
    host = parsed.hostname
    port = parsed.port

    # Normalise basepath: must start with `/`, no trailing `/`. The
    # panel itself doesn't care, but the downstream URL builder does
    # (`f"{base}/login"` with trailing slash on base → `//login`).
    raw_path = parsed.path or "/"
    if not raw_path.startswith("/"):
        raw_path = "/" + raw_path
    if raw_path != "/" and raw_path.endswith("/"):
        raw_path = raw_path[:-1]
    basepath = raw_path

    # Query is `user=<u>&pass=<p>&domain=<d>&mode=<m>`. We hand-parse
    # so a non-URL-safe `&` inside a password (unlikely but possible)
    # is reported as a parse failure rather than silently truncated by
    # urllib.parse_qs's collapse-on-collision semantics.
    query = parsed.query
    fields: dict[str, str] = {}
    for chunk in query.split("&") if query else []:
        if "=" not in chunk:
            continue
        k, v = chunk.split("=", 1)
        fields[unquote(k)] = unquote(v)

    panel_user = fields.get("user", "")
    panel_pass = fields.get("pass", "")
    domain_raw = fields.get("domain", "")
    domain: Optional[str] = domain_raw if domain_raw else None

    mode_raw = fields.get("mode", "bare")
    if mode_raw not in ("bare", "xui-pro"):
        return None

    if not panel_user or not panel_pass:
        # The script always emits both — missing is a sign of
        # truncation or hand-edit. Refuse rather than store half-state.
        return None

    return XuiServerConfig(
        api_token=api_token,
        host=host,
        port=port,
        basepath=basepath,
        panel_user=panel_user,
        panel_pass=panel_pass,
        domain=domain,
        mode=mode_raw,  # type: ignore[arg-type]
    )
