"""Bearer-authenticated client for the 3x-ui v3.0.0+ panel API.

PiTun talks to deployed x-ui panels strictly over HTTPS-or-HTTP +
`Authorization: Bearer <api_token>`. Cookie/CSRF auth is reserved
for the install-time bootstrap inside `setup-xui-server.sh` — once
that script emits the URI, every subsequent panel interaction runs
through this module via a token.

Why Bearer-only at the API layer
--------------------------------
v3.0.0's `web/middleware/security.go::CSRFMiddleware` short-circuits
when `c.GetBool("api_authed")` is true (set by `APIController.checkAPIAuth`
on a valid Bearer match). That means Bearer callers don't need:
  * a session cookie (no `/login` POST)
  * a CSRF token (no `GET /csrf-token` dance)
  * to track expiring sessions
This module relies on that contract. If the panel is rolled back to a
pre-v3.0.0 release the Bearer token won't be honoured at all (the
endpoint just doesn't exist) — `setup-xui-server.sh` pins
`XUI_VERSION=v3.0.0` to keep the contract stable.

Endpoint surface (subset — what PiTun needs in beta.7)
------------------------------------------------------
  GET    /panel/api/inbounds/list                  → list all
  GET    /panel/api/inbounds/get/<id>              → one inbound
  POST   /panel/api/inbounds/add                   → create
  POST   /panel/api/inbounds/del/<id>              → delete
  POST   /panel/api/inbounds/update/<id>           → update
  POST   /panel/api/inbounds/addClient             → add client to inbound
  POST   /panel/api/inbounds/<id>/delClient/<uuid> → delete client
  POST   /panel/api/inbounds/updateClient/<uuid>   → update client
  GET    /panel/api/inbounds/getClientTraffics/<email>
  GET    /panel/api/server/getNewUUID              → util
  GET    /panel/api/server/getNewX25519Cert        → util (Reality keypair)

The setting endpoints (`getApiToken` / `regenerateApiToken`) live
under `/panel/setting/*` (XUIController, cookie-only) — NOT here.
Those are install-time only; runtime token rotation goes through
the deploy-runner re-running `setup-xui-server.sh` if needed.

TLS verification
----------------
Bare-mode panels use a self-signed cert generated at install time. We
default `verify=False` so PiTun-to-panel traffic doesn't 502 on the
TOFU step. The threat model is fine: PiTun is on a LAN, the panel is
identified by its API token (which fronts as Authorization: Bearer),
and a MITM that has the token already has full panel access. Users
who put their own cert chain in front of the panel can override.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# Panel API timeouts — per-call, not aggregated. The panel is on the
# LAN-or-internet but typically responds in 30-200 ms; 10 s is
# defensive padding for slow handshakes / certbot cron renewals
# briefly hogging the box. POSTs with large config payloads (chain
# templates) get a longer ceiling.
_DEFAULT_TIMEOUT = 10.0
_LARGE_PAYLOAD_TIMEOUT = 30.0


class XuiAPIError(Exception):
    """Raised when an x-ui API call fails.

    Two failure modes are bundled together because the caller almost
    always wants to surface "the panel said no" with a single error
    path:
      * HTTP-level — connection refused, 4xx/5xx, timeout
      * Application-level — `success: false` in the response body
        with a `msg` describing what went wrong (e.g. "port already
        in use", "invalid uuid")
    The `kind` attribute lets advanced callers branch when needed.
    """

    def __init__(self, message: str, *, kind: str = "api", status: Optional[int] = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


@dataclass
class XuiClient:
    """Stateless wrapper around a single panel's API.

    Re-creating the underlying httpx.AsyncClient on each method call
    would be wasteful — the connection pool is the whole point of
    httpx — so we keep one alive across the lifetime of the
    XuiClient instance. Call sites should `async with` it (or call
    `.aclose()` explicitly) to release the pool. For one-shot use
    `async with XuiClient(...) as c: await c.list_inbounds()`.
    """

    base_url: str
    """E.g. `http://194.154.29.69:55975/abc123`. NO trailing slash —
    `xui_uri.parse_xui_uri` already normalises this. The panel routes
    `/foo` and `/foo/` identically, but our concatenation of `/login`
    etc. would produce `//login` with a trailing slash on the base."""

    api_token: str
    verify_tls: bool = False
    timeout: float = _DEFAULT_TIMEOUT
    _http: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "XuiClient":
        self._ensure_client()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ── HTTP plumbing ──────────────────────────────────────────────────
    def _ensure_client(self) -> httpx.AsyncClient:
        if self._http is None:
            # Don't pass base_url to httpx — its urljoin semantics drop
            # the path component when the request path starts with `/`
            # (RFC 3986: absolute path replaces base path). For our
            # panels that means `https://h:port/<basepath>` + `/panel/
            # api/inbounds/list` collapses to `https://h:port/panel/...`,
            # missing the basepath and getting a 307 redirect back from
            # the panel. Building full URLs in `_request` instead.
            self._http = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    # The panel responds JSON either way, but being
                    # explicit avoids any future content-negotiation
                    # surprises if upstream adds an HTML fallback.
                    "Accept": "application/json",
                },
                verify=self.verify_tls,
                timeout=self.timeout,
                # Follow 307/308 redirects belt-and-suspenders — if a
                # future panel version changes its routing, we don't
                # want a hard 307 break to look like an auth failure.
                follow_redirects=True,
                # The panel issues self-signed cert and HTTP/1.1; no
                # http/2 needed and the negotiation overhead per call
                # is wasted on a one-shot RPC client.
                http2=False,
            )
        return self._http

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Issue a panel API call and unwrap the standard envelope.

        Every 3x-ui response that comes through `/panel/api/*` is JSON
        of shape `{success: bool, msg: str, obj: <payload>}`. We
        check `success` and raise XuiAPIError on false; on true we
        return the full envelope so the caller can read `obj`,
        `msg`, etc. depending on the endpoint.
        """
        client = self._ensure_client()
        # Concatenate ourselves. `self.base_url` ends without a trailing
        # slash (normalised in xui_uri.parse_xui_uri); `path` starts
        # with `/` so the join produces a single `/` between basepath
        # and the API path.
        url = f"{self.base_url}{path}"
        try:
            resp = await client.request(
                method, url, json=json,
                timeout=timeout if timeout is not None else self.timeout,
            )
        except httpx.TimeoutException as exc:
            raise XuiAPIError(
                f"timeout after {timeout or self.timeout}s on {method} {path}",
                kind="timeout",
            ) from exc
        except httpx.HTTPError as exc:
            raise XuiAPIError(
                f"transport error on {method} {path}: {exc}",
                kind="transport",
            ) from exc

        if resp.status_code == 401 or resp.status_code == 403:
            # 401 = bad token; 403 = CSRF-needed → contract drift,
            # the panel is on a release that flipped Bearer's CSRF
            # exemption off. Surface both with the same kind so the
            # admin gets a "re-deploy the panel to refresh the token"
            # nudge regardless of which one tripped.
            raise XuiAPIError(
                f"auth rejected (HTTP {resp.status_code}) on {method} {path}",
                kind="auth",
                status=resp.status_code,
            )
        if resp.status_code == 404:
            # 404 on `/panel/api/*` from an authenticated request
            # means the endpoint doesn't exist on this panel version
            # (or our base_url is wrong). Both are operator-fixable
            # but neither is retryable.
            raise XuiAPIError(
                f"endpoint not found ({method} {path}) — panel version mismatch?",
                kind="not_found",
                status=404,
            )
        if not resp.is_success:
            raise XuiAPIError(
                f"HTTP {resp.status_code} on {method} {path}: {resp.text[:300]}",
                kind="http",
                status=resp.status_code,
            )

        try:
            body = resp.json()
        except ValueError as exc:
            raise XuiAPIError(
                f"non-JSON response on {method} {path}: {resp.text[:200]}",
                kind="format",
                status=resp.status_code,
            ) from exc

        if not isinstance(body, dict) or not body.get("success"):
            msg = body.get("msg") if isinstance(body, dict) else None
            raise XuiAPIError(
                f"panel rejected {method} {path}: {msg or body!r}",
                kind="api",
                status=resp.status_code,
            )
        return body

    # ── High-level API ─────────────────────────────────────────────────
    async def probe(self) -> None:
        """Smoke-test the token + base URL.

        Hits `inbounds/list` because it's the cheapest endpoint that
        round-trips the auth middleware end-to-end. Used at
        XuiServer-create time to refuse storing a row whose token
        was already invalidated, and as a periodic health-check
        from the UI's "refresh" button.
        """
        await self._request("GET", "/panel/api/inbounds/list")

    async def list_inbounds(self) -> List[Dict[str, Any]]:
        """Return the current inbound list (raw panel shape).

        We don't model the inbound type-tree in this module — that
        belongs in the presets layer (Phase 4) which knows what
        fields each protocol uses. Here we just hand the JSON back
        so callers can render lists / inspect ports / etc.
        """
        body = await self._request("GET", "/panel/api/inbounds/list")
        obj = body.get("obj") or []
        return list(obj) if isinstance(obj, list) else []

    async def get_inbound(self, inbound_id: int) -> Dict[str, Any]:
        body = await self._request("GET", f"/panel/api/inbounds/get/{inbound_id}")
        obj = body.get("obj")
        if not isinstance(obj, dict):
            raise XuiAPIError(f"unexpected get-inbound payload: {obj!r}", kind="format")
        return obj

    async def add_inbound(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create an inbound. `payload` must be the panel's wire shape:
        `{remark, port, protocol, settings, streamSettings, sniffing,
        enable, listen?, ...}`. The presets layer (Phase 4) builds
        these dicts from a higher-level pick + user input."""
        body = await self._request(
            "POST", "/panel/api/inbounds/add",
            json=payload,
            timeout=_LARGE_PAYLOAD_TIMEOUT,
        )
        obj = body.get("obj")
        return obj if isinstance(obj, dict) else {}

    async def update_inbound(
        self, inbound_id: int, payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        body = await self._request(
            "POST", f"/panel/api/inbounds/update/{inbound_id}",
            json=payload,
            timeout=_LARGE_PAYLOAD_TIMEOUT,
        )
        obj = body.get("obj")
        return obj if isinstance(obj, dict) else {}

    async def del_inbound(self, inbound_id: int) -> None:
        await self._request("POST", f"/panel/api/inbounds/del/{inbound_id}")

    async def add_client(
        self, inbound_id: int, settings: Dict[str, Any],
    ) -> None:
        """Add a client to an existing inbound.

        The panel expects `{id: <inbound>, settings: <stringified-json>}`
        where `settings` is `{"clients": [<client-object>]}` re-encoded
        as a string. The double-stringify is a quirk of how 3x-ui
        stores settings (it parses settings as JSON-in-JSON internally).
        """
        import json as _json
        payload = {
            "id": inbound_id,
            "settings": _json.dumps({"clients": [settings]}),
        }
        await self._request("POST", "/panel/api/inbounds/addClient", json=payload)

    async def del_client(self, inbound_id: int, client_uuid: str) -> None:
        await self._request(
            "POST",
            f"/panel/api/inbounds/{inbound_id}/delClient/{client_uuid}",
        )

    async def update_client(
        self, client_uuid: str, inbound_id: int, settings: Dict[str, Any],
    ) -> None:
        import json as _json
        payload = {
            "id": inbound_id,
            "settings": _json.dumps({"clients": [settings]}),
        }
        await self._request(
            "POST", f"/panel/api/inbounds/updateClient/{client_uuid}",
            json=payload,
        )

    async def get_client_traffics_by_email(self, email: str) -> Dict[str, Any]:
        body = await self._request(
            "GET", f"/panel/api/inbounds/getClientTraffics/{email}",
        )
        obj = body.get("obj")
        return obj if isinstance(obj, dict) else {}

    # ── Server util endpoints ──────────────────────────────────────────
    async def get_new_uuid(self) -> str:
        """Generate a fresh UUID via the panel's util endpoint.

        Equivalent of running `xray uuid` on the VPS, but doesn't
        require an SSH session — useful when PiTun is composing an
        inbound payload server-side.
        """
        body = await self._request("GET", "/panel/api/server/getNewUUID")
        obj = body.get("obj")
        if isinstance(obj, dict):
            uuid = obj.get("uuid") or obj.get("id")
            if isinstance(uuid, str):
                return uuid
        if isinstance(obj, str):
            return obj
        raise XuiAPIError(f"unexpected getNewUUID payload: {obj!r}", kind="format")

    async def get_new_x25519_cert(self) -> Tuple[str, str]:
        """Return `(privateKey, publicKey)` for a Reality keypair.

        Replaces shelling out to `xray x25519` over SSH. The panel
        emits `{privateKey, publicKey}` as a dict in the `obj` field.
        """
        body = await self._request(
            "GET", "/panel/api/server/getNewX25519Cert",
        )
        obj = body.get("obj")
        if not isinstance(obj, dict):
            raise XuiAPIError(f"unexpected x25519 payload: {obj!r}", kind="format")
        priv = obj.get("privateKey")
        pub = obj.get("publicKey")
        if not isinstance(priv, str) or not isinstance(pub, str):
            raise XuiAPIError(
                f"x25519 payload missing keys: {obj!r}", kind="format",
            )
        return priv, pub
