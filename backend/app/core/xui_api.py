"""Authenticated client for the 3x-ui v3.0.0+ panel API.

Default auth is `Authorization: Bearer <api_token>`. A handful of
UI-internal endpoints (notably `/panel/setting/update`) live outside
`/panel/api/*` and reject Bearer — the panel docs themselves note
that Bearer applies only to `/panel/api/*`. For those we fall back
to the cookie+CSRF login flow (POST `/login` + `/csrf-token`) on
demand; the credentials come from `panel_user` / `panel_pass`.

Why Bearer-only at the API layer
--------------------------------
v3.0.0's `web/middleware/security.go::CSRFMiddleware` short-circuits
when `c.GetBool("api_authed")` is true (set by `APIController.checkAPIAuth`
on a valid Bearer match). That means Bearer callers don't need:
  * a session cookie (no `/login` POST)
  * a CSRF token (no `GET /csrf-token` dance)
  * to track expiring sessions
This module relies on that contract. `setup-xui-server.sh` pins
`XUI_VERSION=v3.1.0` to keep the client/endpoint contract stable.
PiTun cannot manage v3.0.x panels because the per-client endpoints
moved to a separate `/panel/api/clients/*` controller in v3.1.0
(see "Endpoint surface" below).

Endpoint surface (3x-ui v3.1.0+)
--------------------------------
  GET    /panel/api/inbounds/list                  → list all
  GET    /panel/api/inbounds/get/<id>              → one inbound
  POST   /panel/api/inbounds/add                   → create
  POST   /panel/api/inbounds/del/<id>              → delete
  POST   /panel/api/inbounds/update/<id>           → update

  # NEW in v3.1.0 — clients moved to a first-class controller.
  # The v3.0.x endpoints listed below were REMOVED:
  #   POST /panel/api/inbounds/addClient
  #   POST /panel/api/inbounds/<id>/delClient/<uuid>
  #   POST /panel/api/inbounds/updateClient/<uuid>
  #   GET  /panel/api/inbounds/getClientTraffics/<email>
  # Lookup key shifted from UUID to **email** (globally unique).
  POST   /panel/api/clients/add                    → create+attach
  POST   /panel/api/clients/del/<email>            → delete by email
  POST   /panel/api/clients/update/<email>         → update by email
  GET    /panel/api/clients/traffic/<email>        → per-client stats

  GET    /panel/api/server/getNewUUID              → util
  GET    /panel/api/server/getNewX25519Cert        → util (Reality keypair)

UI-internal endpoints (cookie+CSRF) used by PiTun:
  POST   /panel/setting/update                     → push xrayTemplateConfig
  POST   /login        (with X-CSRF-Token)         → bootstrap session
  GET    /csrf-token                               → mint CSRF token

Token rotation endpoints (`getApiToken` / `regenerateApiToken`) live
under `/panel/setting/*` as well and remain install-time only — the
deploy-runner re-runs `setup-xui-server.sh` for that.

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
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.core.net import direct_active as _direct_active

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
    """E.g. `http://203.0.113.10:55975/abc123`. NO trailing slash —
    `xui_uri.parse_xui_uri` already normalises this. The panel routes
    `/foo` and `/foo/` identically, but our concatenation of `/login`
    etc. would produce `//login` with a trailing slash on the base."""

    api_token: str
    verify_tls: bool = False
    timeout: float = _DEFAULT_TIMEOUT
    # When True, panel HTTP goes DIRECT (SO_MARK bypass), off the active
    # node's tunnel — the operator's "Direct connection" toggle. Defaults
    # to the request-scoped flag (`net.read_direct` dependency), so an
    # endpoint that doesn't pass `direct` still honours `?direct=`. Outside
    # a request (schedulers, background jobs) the factory returns False =
    # through the active node.
    direct: bool = field(default_factory=lambda: _direct_active())
    # Cookie+CSRF credentials. Optional because the vast majority of
    # call sites only need Bearer (inbounds/clients/server-utils). The
    # chain orchestrator's relay client passes them so it can update
    # xrayTemplateConfig via /panel/setting/update.
    panel_user: Optional[str] = None
    panel_pass: Optional[str] = None
    _http: Optional[httpx.AsyncClient] = None
    _csrf_token: Optional[str] = None
    # UI-internal controller mount point. v3.6.0 moved /panel/xray/* and
    # /panel/setting/* under /panel/api/; older panels only serve the old
    # mount. Probed lazily by _admin_request, cached per instance.
    _admin_prefix: Optional[str] = None

    def __post_init__(self) -> None:
        # Hard scheme allowlist — `base_url` comes from a XuiServer DB
        # row written at admin-controlled `xui://` import time, but
        # treat it as defense-in-depth: reject anything that isn't a
        # plain `http://` / `https://` URL up front so a future code
        # path that pipes user input into `base_url` can't reach the
        # HTTP layer with `file://` / `gopher://` / etc. Also closes
        # CodeQL's `py/full-ssrf` finding on `_request` (the rule
        # treats this kind of explicit scheme check as a sanitiser).
        if not isinstance(self.base_url, str):
            raise ValueError("XuiClient.base_url must be a string")
        scheme = self.base_url.split("://", 1)[0].lower() if "://" in self.base_url else ""
        if scheme not in ("http", "https"):
            raise ValueError(
                f"XuiClient.base_url scheme must be http(s), got {scheme!r}",
            )

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
            client_kwargs: Dict[str, Any] = dict(
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
            if self.direct:
                # SO_MARK bypass — dial the panel off the active node's
                # tunnel. `transport` carries verify itself, so drop the
                # top-level verify to avoid httpx's "both set" error.
                from app.core.net import httpx_direct_transport
                client_kwargs.pop("verify", None)
                client_kwargs["transport"] = httpx_direct_transport()
            self._http = httpx.AsyncClient(**client_kwargs)
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

    # ── Cookie + CSRF (UI-internal endpoints) ─────────────────────────
    async def _ensure_cookie_session(self) -> str:
        """Mint a CSRF token + log in if we don't have a live session.
        Returns the CSRF token to attach to the next unsafe request.

        Idempotent: if we already have a CSRF token AND the httpx
        client cookie jar holds a session cookie, no network calls.
        On a 401/403 from a downstream cookie call the caller can
        force a refresh by `self._csrf_token = None` and retrying.
        """
        client = self._ensure_client()
        if self._csrf_token and client.cookies:
            return self._csrf_token
        if not self.panel_user or not self.panel_pass:
            raise XuiAPIError(
                "cookie-mode endpoint requires panel_user + panel_pass",
                kind="auth",
            )
        # 1. GET /csrf-token. Doesn't need auth.
        url = f"{self.base_url}/csrf-token"
        try:
            r = await client.get(url, headers={"X-Requested-With": "XMLHttpRequest"})
        except httpx.HTTPError as exc:
            raise XuiAPIError(
                f"csrf-token transport error: {exc}", kind="transport",
            ) from exc
        if not r.is_success:
            raise XuiAPIError(
                f"csrf-token HTTP {r.status_code}: {r.text[:200]}",
                kind="http", status=r.status_code,
            )
        try:
            csrf = (r.json() or {}).get("obj")
        except ValueError:
            csrf = None
        if not isinstance(csrf, str) or not csrf:
            raise XuiAPIError(
                f"csrf-token returned no obj: {r.text[:200]}", kind="format",
            )

        # 2. POST /login. The panel accepts JSON {username, password,
        # twoFactorCode?} and sets a session cookie on success. CSRF
        # token must be in the header.
        login_url = f"{self.base_url}/login"
        try:
            r = await client.post(
                login_url,
                json={"username": self.panel_user, "password": self.panel_pass},
                headers={
                    "X-CSRF-Token": csrf,
                    "X-Requested-With": "XMLHttpRequest",
                },
            )
        except httpx.HTTPError as exc:
            raise XuiAPIError(
                f"/login transport error: {exc}", kind="transport",
            ) from exc
        if not r.is_success:
            raise XuiAPIError(
                f"/login HTTP {r.status_code}: {r.text[:200]}",
                kind="auth", status=r.status_code,
            )
        try:
            body = r.json() or {}
        except ValueError:
            body = {}
        if not body.get("success"):
            raise XuiAPIError(
                f"/login rejected: {body.get('msg') or r.text[:200]}",
                kind="auth", status=r.status_code,
            )

        self._csrf_token = csrf
        return csrf

    async def _cookie_request(
        self,
        method: str,
        path: str,
        *,
        form: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Issue a UI-internal request authenticated by session cookie
        + CSRF header. Body is form-encoded by default — the shape most
        UI endpoints accept — but `json_body` switches it, because a few
        (`apiTokens/create` among them) take JSON and silently reject a
        form. One auto-retry on 401/403 after refresh.
        """
        client = self._ensure_client()
        url = f"{self.base_url}{path}"
        for attempt in range(2):
            csrf = await self._ensure_cookie_session()
            headers = {
                "X-CSRF-Token": csrf,
                "X-Requested-With": "XMLHttpRequest",
                "Content-Type": ("application/json" if json_body is not None
                                 else "application/x-www-form-urlencoded"),
            }
            try:
                kwargs: Dict[str, Any] = (
                    {"json": json_body} if json_body is not None
                    else {"data": form or {}}
                )
                resp = await client.request(
                    method, url, headers=headers,
                    timeout=timeout if timeout is not None else self.timeout,
                    **kwargs,
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
            if resp.status_code in (401, 403) and attempt == 0:
                self._csrf_token = None
                client.cookies.clear()
                continue
            if not resp.is_success:
                raise XuiAPIError(
                    f"HTTP {resp.status_code} on {method} {path}: {resp.text[:300]}",
                    kind="http", status=resp.status_code,
                )
            try:
                body = resp.json()
            except ValueError as exc:
                raise XuiAPIError(
                    f"non-JSON response on {method} {path}: {resp.text[:200]}",
                    kind="format", status=resp.status_code,
                ) from exc
            if not isinstance(body, dict) or not body.get("success"):
                msg = body.get("msg") if isinstance(body, dict) else None
                raise XuiAPIError(
                    f"panel rejected {method} {path}: {msg or body!r}",
                    kind="api", status=resp.status_code,
                )
            return body
        # unreachable — loop either returns or raises on second iter
        raise XuiAPIError(f"unreachable cookie retry exhausted on {path}", kind="api")

    async def _admin_request(
        self,
        method: str,
        path: str,
        *,
        form: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Cookie request against a UI-internal controller, tolerant of
        the mount-point move: `path` is relative to the controller root
        (e.g. `/xray/update`). Tries `/panel/api` first (v3.6.0+); a 404
        or an HTML (SPA-shell) response means an older panel, retry at
        `/panel`. The winning prefix is cached for the client's lifetime.
        """
        if self._admin_prefix is not None:
            return await self._cookie_request(
                method, self._admin_prefix + path, form=form, timeout=timeout,
            )
        try:
            body = await self._cookie_request(
                method, "/panel/api" + path, form=form, timeout=timeout,
            )
        except XuiAPIError as exc:
            wrong_mount = (
                (exc.kind == "http" and exc.status == 404)
                or exc.kind == "format"
            )
            if not wrong_mount:
                raise
            body = await self._cookie_request(
                method, "/panel" + path, form=form, timeout=timeout,
            )
            self._admin_prefix = "/panel"
            return body
        self._admin_prefix = "/panel/api"
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
            json=_coerce_inbound_payload(payload),
            timeout=_LARGE_PAYLOAD_TIMEOUT,
        )
        obj = body.get("obj")
        return obj if isinstance(obj, dict) else {}

    async def update_inbound(
        self, inbound_id: int, payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        body = await self._request(
            "POST", f"/panel/api/inbounds/update/{inbound_id}",
            json=_coerce_inbound_payload(payload),
            timeout=_LARGE_PAYLOAD_TIMEOUT,
        )
        obj = body.get("obj")
        return obj if isinstance(obj, dict) else {}

    async def del_inbound(self, inbound_id: int) -> None:
        await self._request("POST", f"/panel/api/inbounds/del/{inbound_id}")

    async def add_client(
        self, inbound_id: int, settings: Dict[str, Any],
    ) -> None:
        """Create a client and attach it to the given inbound (v3.1.0+).

        Callers pass a v3.0.x-shaped client dict (`{id, email, flow,
        password, limitIp, totalGB, expiryTime, enable, subId, tgId,
        comment, reset, ...}`). We translate to v3.1.0's
        `POST /panel/api/clients/add` shape:

            {
              "client": <ClientRecord — renamed id→uuid>,
              "inboundIds": [<inbound_id>]
            }

        Email is globally unique on v3.1.0 (`uniqueIndex` in
        `ClientRecord`), so callers that previously created the
        "same email × N inbounds" pattern (chain orchestrator)
        must suffix per-inbound or use `add_client_multi`.
        """
        client_record = _to_client_record(settings)
        payload = {
            "client": client_record,
            "inboundIds": [inbound_id],
        }
        await self._request("POST", "/panel/api/clients/add", json=payload)

    async def add_client_multi(
        self, inbound_ids: List[int], settings: Dict[str, Any],
    ) -> None:
        """v3.1.0-native: one client attached to N inbounds in one call.

        Cheaper than N `add_client` round-trips and matches the new
        panel UI shape (clients are first-class, inbound attachments
        are a many-to-many join). PiTun's chain orchestrator currently
        prefers per-channel suffix-emails (see `xui_chain.py`) so this
        helper is reserved for future per-feature use.
        """
        client_record = _to_client_record(settings)
        payload = {
            "client": client_record,
            "inboundIds": list(inbound_ids),
        }
        await self._request("POST", "/panel/api/clients/add", json=payload)

    async def del_client(self, inbound_id: int, client_uuid: str) -> None:
        """Delete a client by uuid (v3.1.0 keys by email).

        The v3.1.0 endpoint is `POST /panel/api/clients/del/<email>`
        (no body), but every legacy caller hands us `(inbound_id,
        uuid)`. We resolve uuid → email via the inbound's hydrated
        `settings.clients[]` (still populated for back-compat on
        `GET /panel/api/inbounds/get/<id>`).

        Side effect: if a client is attached to multiple inbounds
        (`add_client_multi`), this deletes the ClientRecord entirely
        — all attachments go with it. Callers that need per-attachment
        cleanup should use `detach_client` instead.
        """
        email = await self._resolve_client_email(inbound_id, client_uuid)
        await self._request(
            "POST", f"/panel/api/clients/del/{email}",
        )

    async def detach_client(
        self, email: str, inbound_ids: List[int],
    ) -> None:
        """Detach an email from specific inbounds, keep the client.

        Use when you want to remove a multi-attached client from one
        of its inbounds without nuking the whole ClientRecord.
        """
        await self._request(
            "POST", f"/panel/api/clients/{email}/detach",
            json={"inboundIds": list(inbound_ids)},
        )

    async def update_client(
        self, client_uuid: str, inbound_id: int, settings: Dict[str, Any],
    ) -> None:
        """Update a client by uuid (v3.1.0 keys by email).

        Same uuid → email resolution dance as `del_client`. The
        payload is a flat ClientRecord (no inbound boundary).
        """
        email = await self._resolve_client_email(inbound_id, client_uuid)
        client_record = _to_client_record(settings)
        await self._request(
            "POST", f"/panel/api/clients/update/{email}",
            json=client_record,
        )

    async def get_client_traffics_by_email(self, email: str) -> Dict[str, Any]:
        """Per-client traffic stats (v3.1.0 endpoint moved)."""
        body = await self._request(
            "GET", f"/panel/api/clients/traffic/{email}",
        )
        obj = body.get("obj")
        return obj if isinstance(obj, dict) else {}

    async def _resolve_client_email(
        self, inbound_id: int, client_uuid: str,
    ) -> str:
        """Look up the email of a client by its uuid within an inbound.

        `GET /panel/api/inbounds/get/<id>` still hydrates
        `settings.clients[]` for back-compat in v3.1.0 — we scan that
        list for the matching id and pull the email field. If not
        found, raise `XuiAPIError` with a clear message so the API
        layer can 502 the request instead of silently no-op'ing.
        """
        body = await self._request(
            "GET", f"/panel/api/inbounds/get/{inbound_id}",
        )
        obj = body.get("obj") or {}
        settings = parse_inbound_field(obj.get("settings"))
        # `client_uuid` is really a natural id: vless/vmess clients are
        # keyed by `id`, but trojan/shadowsocks/socks clients have no
        # `id` at all — their identity is `password` (or `user`), and
        # that's what the UI sends. Matching on `id` alone made every
        # delete/update of a non-vless client fail with a 502.
        needle = (client_uuid or "").lower()
        for client in (settings.get("clients") or []):
            candidates = (
                client.get("id"), client.get("email"),
                client.get("password"), client.get("user"),
            )
            if any(str(c or "").lower() == needle for c in candidates):
                email = client.get("email")
                if email:
                    return str(email)
                break
        raise XuiAPIError(
            f"client id={client_uuid!r} not found in inbound "
            f"{inbound_id} (cannot resolve email for v3.1.0 endpoint)",
            kind="not_found",
        )

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

    async def restart_xray(self) -> None:
        """POST /panel/api/server/restartXrayService. Bearer-authed —
        documented under the Server API section of the panel docs.
        """
        await self._request(
            "POST", "/panel/api/server/restartXrayService",
            timeout=_LARGE_PAYLOAD_TIMEOUT,
        )

    async def ensure_api_token(self, name: str = "pitun") -> str:
        """Find or create the panel's own API token, using the login.

        Every Bearer call needs this token, and until now the only thing
        that produced one was our install script — so a panel installed by
        hand, or one whose `xui://` line was lost, could not be registered
        at all. The operator has the credentials they log in with; this
        turns those into the token, exactly as the script does.

        Reuses a token already named `name` rather than minting one per
        attempt: a panel accumulating a token per import is a mess, and any
        `xui://` handed out earlier keeps working.

        Both controller mounts are tried — v3.6.0 moved the settings
        controllers under `/panel/api/`, and older panels only serve the
        old path.
        """
        if not (self.panel_user and self.panel_pass):
            raise XuiAPIError(
                "Panel login is required to obtain an API token",
                kind="auth",
            )

        last: Optional[str] = None
        for mount in ("/panel/api/setting", "/panel/setting"):
            try:
                listing = await self._cookie_request("GET", f"{mount}/apiTokens")
            except XuiAPIError as exc:
                # The wrong mount answers with the SPA shell or a 404; that
                # is not a failure, it is the probe doing its job.
                last = str(exc)
                continue
            if not isinstance(listing, dict) or listing.get("success") is not True:
                last = f"{mount}/apiTokens did not answer as the tokens controller"
                continue

            for item in listing.get("obj") or []:
                if (isinstance(item, dict) and item.get("name") == name
                        and item.get("token")):
                    logger.info("Reusing existing panel API token %r", name)
                    return str(item["token"])

            created = await self._cookie_request(
                "POST", f"{mount}/apiTokens/create", json_body={"name": name},
            )
            token = ((created or {}).get("obj") or {}).get("token")
            if token:
                logger.info("Created panel API token %r", name)
                return str(token)
            last = f"{mount}/apiTokens/create returned no token"

        raise XuiAPIError(
            f"Could not obtain an API token from the panel ({last or 'no mount answered'})",
            kind="api",
        )

    async def get_xray_setting(self) -> Dict[str, Any]:
        """Fetch the panel's current xray template + metadata.

        Returns the parsed `obj` dict — keys typically include
        `xraySetting` (the full xray config dict), `outboundTestUrl`,
        `inboundTags`, `clientReverseTags`. Cookie+CSRF auth against
        the XraySettingController (mount handled by _admin_request).
        """
        body = await self._admin_request(
            "POST", "/xray/", timeout=_LARGE_PAYLOAD_TIMEOUT,
        )
        obj = body.get("obj")
        # The panel double-encodes: `obj` is a JSON STRING. Parse once.
        if isinstance(obj, str):
            import json as _json
            try:
                obj = _json.loads(obj)
            except ValueError as exc:
                raise XuiAPIError(
                    f"/panel/xray/ obj not JSON-decodable: {obj[:200]}",
                    kind="format",
                ) from exc
        if not isinstance(obj, dict):
            raise XuiAPIError(
                f"/panel/xray/ unexpected shape: {type(obj).__name__}",
                kind="format",
            )
        return obj

    async def test_outbound(
        self,
        outbound: Dict[str, Any],
        *,
        mode: str = "",
        all_outbounds: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Probe an outbound via /panel/xray/testOutbound.

        `mode=""` (default) does a full HTTP probe through the
        outbound to the panel's configured test URL (defaults to
        Google's generate_204). `mode="tcp"` is a faster dial-only
        check that doesn't pass any L7 traffic. Cookie+CSRF auth.

        Returns the parsed `obj` from the response — a dict typically
        with `success`, `delay`, `error` (when the probe failed).
        """
        import json as _json
        form: Dict[str, str] = {
            "outbound": _json.dumps(outbound, separators=(",", ":")),
        }
        if mode:
            form["mode"] = mode
        if all_outbounds is not None:
            form["allOutbounds"] = _json.dumps(
                all_outbounds, separators=(",", ":"),
            )
        body = await self._admin_request(
            "POST", "/xray/testOutbound",
            form=form, timeout=_LARGE_PAYLOAD_TIMEOUT,
        )
        obj = body.get("obj")
        return obj if isinstance(obj, dict) else {"raw": obj}

    async def push_xray_setting(
        self,
        xray_setting: Dict[str, Any],
        outbound_test_url: Optional[str] = None,
    ) -> None:
        """Replace the panel's xray template via /panel/xray/update.

        Body is form-encoded with two fields:
          * `xraySetting`     — full xray config as a JSON string
          * `outboundTestUrl` — defaults to Google's generate_204 if
                                empty / omitted
        Cookie+CSRF auth.
        """
        import json as _json
        form: Dict[str, str] = {
            "xraySetting": _json.dumps(xray_setting, separators=(",", ":")),
            "outboundTestUrl": (
                outbound_test_url
                or "https://www.google.com/generate_204"
            ),
        }
        await self._admin_request(
            "POST", "/xray/update",
            form=form, timeout=_LARGE_PAYLOAD_TIMEOUT,
        )


# Per-client field projection for `POST /panel/api/clients/add` body.
#
# v3.1.0 has TWO different Go structs for clients (confusing!):
#   * `model.Client`       — the per-inbound shape, embedded in
#                            `settings.clients[]`. JSON key for UUID
#                            is `"id"` (struct field `ID`).
#   * `model.ClientRecord` — the new first-class clients table. JSON
#                            key for UUID is `"uuid"` (struct field
#                            `UUID`).
#
# `ClientCreatePayload.Client` is type `model.Client`. So when the
# panel decodes our POST body, it reads `client.id`, NOT `client.uuid`.
# Earlier versions of this code shipped `"uuid"` and the panel happily
# ignored it (no error), then `fillProtocolDefaults` saw an empty
# `client.ID` and generated a fresh UUID server-side. Result: PiTun's
# DB stored the UUID it pre-generated via `/server/getNewUUID`, but
# the panel stored a different one → user's Node URI auth'd against
# the wrong UUID → chain silently broken.
#
# Fix: emit `id` in the client payload (matches model.Client JSON tag).
# v3.0.x callers also use `id`, so no rename happens at the input
# layer either — `_to_client_record` is effectively a passthrough with
# type coercion. Note: subsequent reads via `/clients/list` return
# `uuid` (ClientRecord shape) — that's the panel's storage layer
# leaking through; ignore the rename when reading.
_CLIENT_RECORD_KEYS = (
    "email", "id", "password", "auth", "flow", "security", "reverse",
    "limitIp", "totalGB", "expiryTime", "enable", "subId", "tgId",
    "comment", "reset",
)


def parse_inbound_field(value: Any) -> Dict[str, Any]:
    """Defensive parser for `Inbound.settings` / `streamSettings` /
    `sniffing` JSON-of-JSON fields.

    v3.0.x panels emitted these as **JSON-encoded strings** (Go struct
    field type was `string`, the panel stored them stringified inside
    the outer JSON). The PiTun client + frontend `JSON.parse`'d the
    inner string to get the actual dict.

    v3.1.0 added a `MarshalJSON` on the Inbound model that emits
    them as **nested JSON objects** directly. The wire shape is now:
        settings: { clients: [...], decryption: "none", ... }
    instead of:
        settings: "{\\"clients\\":[...],\\"decryption\\":\\"none\\"}"

    JSON.parse / json.loads on a dict throws TypeError, so every
    callsite that assumed the stringified form crashes silently and
    falls back to empty {}. This helper normalises both shapes:
    string → parse it; dict → pass through; anything else → {}.

    Used by both `xui_api._resolve_client_email` and the inbound-
    sync route handlers so backward compat with any straggler v3.0.x
    install survives, even though `setup-xui-server.sh` pins v3.1.0.
    """
    import json as _json
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        if not value:
            return {}
        try:
            parsed = _json.loads(value)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


# Fields whose Go type is int64 / bool on model.Client and ClientRecord.
# Legacy PiTun payloads ship empty-string defaults ("tgId": "") which the
# strict Go json decoder rejects; coerce before any write to the panel.
_NUMERIC_CLIENT_FIELDS = ("limitIp", "totalGB", "expiryTime", "tgId", "reset")
_BOOL_CLIENT_FIELDS = ("enable",)


def _coerce_int(v: Any) -> int:
    if v is None or v == "":
        return 0
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _coerce_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes", "y", "on")
    return False


def _coerce_inbound_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise clients embedded in an add/update-inbound payload.

    v3.1.0 only type-checked clients on the /panel/api/clients/* writes;
    v3.6.0 also parses `settings.clients[]` into model.Client structs on
    inbounds/add|update, so the legacy `""` defaults reject the whole
    inbound. Handles `settings` as dict or JSON string and preserves
    whichever shape the caller built.
    """
    import json as _json
    settings = payload.get("settings")
    parsed = parse_inbound_field(settings)
    clients = parsed.get("clients")
    if not isinstance(clients, list) or not clients:
        return payload
    coerced = []
    for cl in clients:
        if isinstance(cl, dict):
            cl = dict(cl)
            for k in _NUMERIC_CLIENT_FIELDS:
                if k in cl:
                    cl[k] = _coerce_int(cl[k])
            for k in _BOOL_CLIENT_FIELDS:
                if k in cl:
                    cl[k] = _coerce_bool(cl[k])
        coerced.append(cl)
    parsed = {**parsed, "clients": coerced}
    out = dict(payload)
    out["settings"] = (
        _json.dumps(parsed) if isinstance(settings, str) else parsed
    )
    return out


def _to_client_record(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Translate a v3.0.x-style client dict into a v3.1.0 ClientRecord.

    Two transformations:
    1. `id` → `uuid` field rename (v3.0.x callers shipped `id`, v3.1.0's
       ClientRecord uses `uuid`).
    2. Type coercion for fields whose Go type is `int64` / `bool` —
       v3.0.x panels were lenient and accepted empty-string defaults
       (`"tgId": ""`), but v3.1.0 (Go's stdlib json.Unmarshal) rejects
       `"cannot unmarshal string into Go struct field Client.client.tgId
       of type int64"`. Callers across PiTun shipped strings for
       legacy reasons; we normalise here so every callsite doesn't
       have to update independently.

    Unknown keys are dropped silently so callers that include legacy
    fields (e.g. `subscription_url`) don't poison the request body.
    """
    out: Dict[str, Any] = {}
    # Both legacy v3.0.x payloads and the v3.1.0 model.Client JSON shape
    # use `id` for the UUID. If a caller hand-shipped `uuid` (because
    # they read from ClientRecord), accept that as a fallback alias.
    if "id" in settings and settings.get("id"):
        out["id"] = settings["id"]
    elif "uuid" in settings and settings.get("uuid"):
        out["id"] = settings["uuid"]
    for k in _CLIENT_RECORD_KEYS:
        if k == "id":
            continue  # already handled above
        if k not in settings:
            continue
        v = settings[k]
        if k in _NUMERIC_CLIENT_FIELDS:
            out[k] = _coerce_int(v)
        elif k in _BOOL_CLIENT_FIELDS:
            out[k] = _coerce_bool(v)
        else:
            out[k] = v
    return out
