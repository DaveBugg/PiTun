"""Unit tests for the x-ui Bearer API client + URI parser.

Network is mocked via httpx.MockTransport so the suite stays
hermetic — no live panel required. End-to-end smoke against a
real x-ui panel happens in Phase 8 of the v1.3.0-beta.7 series.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.core.xui_api import XuiAPIError, XuiClient
from app.core.xui_uri import parse_xui_uri


# ── URI parser ────────────────────────────────────────────────────────────


class TestParseXuiUri:
    def test_bare_mode(self):
        uri = (
            "xui://abc123token@1.2.3.4:55975/basepathstr"
            "?user=admin1&pass=secret1&domain=&mode=bare"
        )
        cfg = parse_xui_uri(uri)
        assert cfg is not None
        assert cfg.api_token == "abc123token"
        assert cfg.host == "1.2.3.4"
        assert cfg.port == 55975
        assert cfg.basepath == "/basepathstr"
        assert cfg.panel_user == "admin1"
        assert cfg.panel_pass == "secret1"
        assert cfg.domain is None
        assert cfg.mode == "bare"

    def test_xui_pro_mode(self):
        uri = (
            "xui://tk@panel.example.com:56844/abc123"
            "?user=u&pass=p&domain=panel.example.com&mode=xui-pro"
        )
        cfg = parse_xui_uri(uri)
        assert cfg is not None
        assert cfg.domain == "panel.example.com"
        assert cfg.mode == "xui-pro"

    def test_basepath_normalisation_strips_trailing_slash(self):
        uri = "xui://t@h:1/p/?user=u&pass=p&mode=bare"
        cfg = parse_xui_uri(uri)
        assert cfg is not None
        assert cfg.basepath == "/p"

    def test_basepath_normalisation_adds_leading_slash(self):
        # urlparse already requires a leading slash in the path component
        # for `xui://h:p/path`, so this is mostly defensive — but the
        # normaliser shouldn't regress.
        uri = "xui://t@h:1/p?user=u&pass=p&mode=bare"
        cfg = parse_xui_uri(uri)
        assert cfg is not None
        assert cfg.basepath == "/p"

    def test_url_encoded_credentials(self):
        # `&` in pass would obviously break the &-split; the script
        # picks URL-safe charsets to avoid this. But test that
        # url-encoded `+/=` get decoded properly.
        uri = "xui://t@h:1/p?user=us%2Ber&pass=p%3Dq%2Fr&mode=bare"
        cfg = parse_xui_uri(uri)
        assert cfg is not None
        assert cfg.panel_user == "us+er"
        assert cfg.panel_pass == "p=q/r"

    def test_empty_returns_none(self):
        assert parse_xui_uri("") is None
        assert parse_xui_uri("   ") is None

    def test_wrong_scheme_returns_none(self):
        assert parse_xui_uri("vless://abc@h:443") is None
        assert parse_xui_uri("https://h:443/abc") is None

    def test_missing_token_returns_none(self):
        assert parse_xui_uri("xui://@h:1/p?user=u&pass=p&mode=bare") is None

    def test_missing_user_pass_returns_none(self):
        # The script always emits both — a missing one is corruption.
        assert parse_xui_uri("xui://t@h:1/p?mode=bare") is None
        assert parse_xui_uri("xui://t@h:1/p?user=u&mode=bare") is None
        assert parse_xui_uri("xui://t@h:1/p?pass=p&mode=bare") is None

    def test_invalid_mode_returns_none(self):
        assert parse_xui_uri("xui://t@h:1/p?user=u&pass=p&mode=junk") is None

    def test_missing_port_returns_none(self):
        assert parse_xui_uri("xui://t@h/p?user=u&pass=p&mode=bare") is None


# ── XuiClient ─────────────────────────────────────────────────────────────


def _mock(handler):
    """Wrap a handler dict-or-fn into an httpx MockTransport."""
    return httpx.MockTransport(handler)


def _make_client(transport: httpx.MockTransport) -> XuiClient:
    """Build an XuiClient whose underlying httpx pool uses the mock."""
    c = XuiClient(
        base_url="http://panel.test:1234/p",
        api_token="TESTTOKEN",
        verify_tls=False,
    )
    c._http = httpx.AsyncClient(
        base_url=c.base_url,
        headers={"Authorization": f"Bearer {c.api_token}"},
        transport=transport,
    )
    return c


def _ok(payload):
    return httpx.Response(200, json={"success": True, "msg": "", "obj": payload})


def _fail(msg, code=200):
    return httpx.Response(code, json={"success": False, "msg": msg, "obj": None})


class TestXuiClient:
    @pytest.mark.asyncio
    async def test_list_inbounds_returns_obj_list(self):
        captured = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["url"] = str(req.url)
            captured["auth"] = req.headers.get("Authorization")
            return _ok([{"id": 1, "remark": "a", "port": 443}])

        c = _make_client(_mock(handler))
        try:
            inbounds = await c.list_inbounds()
        finally:
            await c.aclose()
        assert inbounds == [{"id": 1, "remark": "a", "port": 443}]
        assert "/panel/api/inbounds/list" in captured["url"]
        assert captured["auth"] == "Bearer TESTTOKEN"

    @pytest.mark.asyncio
    async def test_add_inbound_passes_payload_as_json(self):
        captured = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["method"] = req.method
            captured["body"] = json.loads(req.read())
            return _ok({"id": 42, "remark": "test"})

        c = _make_client(_mock(handler))
        try:
            res = await c.add_inbound({"remark": "test", "port": 1234})
        finally:
            await c.aclose()
        assert captured["method"] == "POST"
        assert captured["body"] == {"remark": "test", "port": 1234}
        assert res["id"] == 42

    @pytest.mark.asyncio
    async def test_add_client_posts_v3_1_clients_endpoint(self):
        # In v3.1.0 the legacy `POST /panel/api/inbounds/addClient`
        # endpoint was removed. Clients are now first-class: PiTun
        # POSTs to `/panel/api/clients/add` with a `ClientRecord`
        # body + `inboundIds: [N]` for attachment. The `id` field
        # from the caller's v3.0.x-shaped dict is renamed to `uuid`
        # to match the new ClientRecord model.
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["path"] = req.url.path
            captured["body"] = json.loads(req.read())
            return _ok(None)

        c = _make_client(_mock(handler))
        try:
            await c.add_client(
                inbound_id=7,
                settings={"id": "uuid-here", "email": "pi-abc", "flow": "xtls-rprx-vision"},
            )
        finally:
            await c.aclose()

        assert captured["path"] == "/p/panel/api/clients/add"
        body = captured["body"]
        assert body["inboundIds"] == [7]
        # `client` is a flat model.Client — JSON key for UUID is `id`
        # (NOT `uuid` — that's model.ClientRecord shape on the storage
        # side, which we read but don't write). v3.1.0 panel ignored
        # unknown `uuid` field on the write path and silently auto-
        # generated a fresh ID, breaking chains until this was fixed.
        assert body["client"]["id"] == "uuid-here"
        assert body["client"]["email"] == "pi-abc"
        assert body["client"]["flow"] == "xtls-rprx-vision"
        # `uuid` must NOT appear — the panel's model.Client struct has
        # no such field, so emitting it is a silent no-op that gets us
        # a server-side UUID we never see.
        assert "uuid" not in body["client"]

    @pytest.mark.asyncio
    async def test_add_client_multi_attaches_to_many_inbounds(self):
        # v3.1.0-native: one ClientRecord, multiple inbound attachments
        # in a single round-trip. PiTun's chain orchestrator currently
        # prefers per-channel suffix-emails (so it uses `add_client`
        # in a loop), but this helper exists for future per-feature use.
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(req.read())
            return _ok(None)

        c = _make_client(_mock(handler))
        try:
            await c.add_client_multi(
                inbound_ids=[3, 7, 11],
                settings={"id": "u-1", "email": "user1"},
            )
        finally:
            await c.aclose()

        assert captured["body"]["inboundIds"] == [3, 7, 11]
        assert captured["body"]["client"]["id"] == "u-1"

    @pytest.mark.asyncio
    async def test_add_client_coerces_int_fields_from_legacy_strings(self):
        # 3x-ui v3.1.0 ClientRecord declares tgId / limitIp / totalGB /
        # expiryTime / reset as Go int64; the Go json decoder rejects
        # empty-string / null values with:
        #   "cannot unmarshal string into Go struct field
        #    Client.client.tgId of type int64"
        # PiTun's legacy callers (xui_chain.py, xui.py) ship
        # `"tgId": ""` from the v3.0.x era. The adapter coerces those
        # to int(0) before posting so the panel accepts the body.
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            captured["body"] = json.loads(req.read())
            return _ok(None)

        c = _make_client(_mock(handler))
        try:
            await c.add_client(
                inbound_id=5,
                settings={
                    "id": "u-coerce", "email": "pi-coerce",
                    "flow": "xtls-rprx-vision",
                    # All the empty-string legacy fields:
                    "tgId": "", "subId": "",
                    "limitIp": 0, "totalGB": 0, "expiryTime": 0,
                    "enable": True,
                },
            )
        finally:
            await c.aclose()

        client = captured["body"]["client"]
        assert client["tgId"] == 0 and isinstance(client["tgId"], int), (
            f"tgId must be coerced to int(0), got {client['tgId']!r}"
        )
        assert isinstance(client["limitIp"], int)
        assert isinstance(client["totalGB"], int)
        assert isinstance(client["expiryTime"], int)
        assert isinstance(client["enable"], bool)
        # subId stays string (it's `string` on the panel side).
        assert client["subId"] == ""

    @pytest.mark.asyncio
    async def test_del_client_resolves_uuid_to_email(self):
        # v3.1.0's delete endpoint keys by email, not uuid. PiTun's
        # callers still hand us `(inbound_id, uuid)`, so the client
        # fetches the inbound, finds the matching uuid in
        # `settings.clients[]`, and POSTs to `/clients/del/<email>`.
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            path = req.url.path
            if "/panel/api/inbounds/get/" in path:
                # Hydrated inbound — back-compat clients[] still present.
                return _ok({
                    "id": 7,
                    "settings": json.dumps({
                        "clients": [
                            {"id": "other-uuid", "email": "other@x"},
                            {"id": "target-uuid", "email": "target@x"},
                        ],
                    }),
                })
            captured.setdefault("paths", []).append(path)
            return _ok(None)

        c = _make_client(_mock(handler))
        try:
            await c.del_client(inbound_id=7, client_uuid="target-uuid")
        finally:
            await c.aclose()

        assert captured["paths"] == ["/p/panel/api/clients/del/target@x"]

    @pytest.mark.asyncio
    async def test_del_client_raises_when_uuid_not_in_inbound(self):
        # If the resolution lookup fails, surface a clear error so
        # the API layer 502s the request instead of silently no-op'ing.
        from app.core.xui_api import XuiAPIError

        def handler(req):
            if "/panel/api/inbounds/get/" in req.url.path:
                return _ok({
                    "id": 7,
                    "settings": json.dumps({"clients": []}),
                })
            return _ok(None)

        c = _make_client(_mock(handler))
        try:
            with pytest.raises(XuiAPIError) as exc:
                await c.del_client(inbound_id=7, client_uuid="ghost-uuid")
        finally:
            await c.aclose()
        assert "not found" in str(exc.value)
        assert exc.value.kind == "not_found"

    @pytest.mark.asyncio
    async def test_get_client_traffics_uses_v3_1_endpoint(self):
        captured: dict = {}

        def handler(req):
            captured["path"] = req.url.path
            return _ok({"email": "u@x", "up": 100, "down": 200})

        c = _make_client(_mock(handler))
        try:
            res = await c.get_client_traffics_by_email("u@x")
        finally:
            await c.aclose()
        assert captured["path"] == "/p/panel/api/clients/traffic/u@x"
        assert res["up"] == 100 and res["down"] == 200

    @pytest.mark.asyncio
    async def test_get_new_x25519_cert_returns_pair(self):
        def handler(req):
            return _ok({"privateKey": "PRIV", "publicKey": "PUB"})

        c = _make_client(_mock(handler))
        try:
            priv, pub = await c.get_new_x25519_cert()
        finally:
            await c.aclose()
        assert priv == "PRIV"
        assert pub == "PUB"

    @pytest.mark.asyncio
    async def test_panel_says_no_raises_apierror(self):
        # success: false with msg → XuiAPIError(kind="api"), the msg
        # makes it into the exception string so the user sees it.
        def handler(req):
            return _fail("port already in use")

        c = _make_client(_mock(handler))
        try:
            with pytest.raises(XuiAPIError) as exc:
                await c.add_inbound({"port": 443})
        finally:
            await c.aclose()
        assert "port already in use" in str(exc.value)
        assert exc.value.kind == "api"

    @pytest.mark.asyncio
    async def test_401_classified_as_auth_error(self):
        def handler(req):
            return httpx.Response(401, json={"success": False, "msg": "unauthorized"})

        c = _make_client(_mock(handler))
        try:
            with pytest.raises(XuiAPIError) as exc:
                await c.list_inbounds()
        finally:
            await c.aclose()
        assert exc.value.kind == "auth"
        assert exc.value.status == 401

    @pytest.mark.asyncio
    async def test_404_classified_as_not_found(self):
        def handler(req):
            return httpx.Response(404, text="Not Found")

        c = _make_client(_mock(handler))
        try:
            with pytest.raises(XuiAPIError) as exc:
                await c.list_inbounds()
        finally:
            await c.aclose()
        assert exc.value.kind == "not_found"

    @pytest.mark.asyncio
    async def test_non_json_response_classified_as_format(self):
        def handler(req):
            return httpx.Response(200, text="<html>panel down</html>")

        c = _make_client(_mock(handler))
        try:
            with pytest.raises(XuiAPIError) as exc:
                await c.list_inbounds()
        finally:
            await c.aclose()
        assert exc.value.kind == "format"

    @pytest.mark.asyncio
    async def test_probe_uses_inbounds_list(self):
        captured = {}

        def handler(req):
            captured["path"] = req.url.path
            return _ok([])

        c = _make_client(_mock(handler))
        try:
            await c.probe()
        finally:
            await c.aclose()
        assert captured["path"].endswith("/panel/api/inbounds/list")

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        # Sanity-check the `async with` path; the http client should
        # be created on enter and disposed on exit.
        c = XuiClient(base_url="http://h:1/p", api_token="t", verify_tls=False)
        assert c._http is None
        async with c as ctx:
            assert ctx is c
            assert c._http is not None
        assert c._http is None


# ── Admin-mount fallback (/panel/api vs /panel) ───────────────────────────


def _make_cookie_client(transport: httpx.MockTransport) -> XuiClient:
    """Client with cookie credentials for UI-internal endpoints."""
    c = XuiClient(
        base_url="http://panel.test:1234/p",
        api_token="TESTTOKEN",
        verify_tls=False,
        panel_user="admin",
        panel_pass="secret",
    )
    c._http = httpx.AsyncClient(
        base_url=c.base_url,
        headers={"Authorization": f"Bearer {c.api_token}"},
        transport=transport,
    )
    return c


def _cookie_session_handler(req):
    """Serve the csrf + login legs of the cookie flow; None otherwise."""
    if req.url.path == "/p/csrf-token":
        return _ok("csrf-tok")
    if req.url.path == "/p/login":
        return httpx.Response(
            200,
            json={"success": True, "msg": "", "obj": None},
            headers={"set-cookie": "3x-ui=sess; Path=/"},
        )
    return None


_SPA_SHELL = "<!doctype html><html><body>spa</body></html>"


class TestAdminMountFallback:
    @pytest.mark.asyncio
    async def test_v3_6_panel_uses_panel_api_mount(self):
        paths = []

        def handler(req):
            hit = _cookie_session_handler(req)
            if hit is not None:
                return hit
            paths.append(req.url.path)
            if req.url.path == "/p/panel/api/xray/update":
                return _ok(None)
            # Old mount answers with the SPA shell on a v3.6.0 panel.
            return httpx.Response(200, text=_SPA_SHELL)

        c = _make_cookie_client(_mock(handler))
        try:
            await c.push_xray_setting({"outbounds": []})
        finally:
            await c.aclose()
        assert paths == ["/p/panel/api/xray/update"]
        assert c._admin_prefix == "/panel/api"

    @pytest.mark.asyncio
    async def test_v3_1_panel_falls_back_to_panel_mount_and_caches(self):
        paths = []

        def handler(req):
            hit = _cookie_session_handler(req)
            if hit is not None:
                return hit
            paths.append(req.url.path)
            if req.url.path == "/p/panel/xray/update":
                return _ok(None)
            return httpx.Response(404, text="404 page not found")

        c = _make_cookie_client(_mock(handler))
        try:
            await c.push_xray_setting({"outbounds": []})
            # Second call must go straight to the cached old mount.
            await c.push_xray_setting({"outbounds": []})
        finally:
            await c.aclose()
        assert paths == [
            "/p/panel/api/xray/update",
            "/p/panel/xray/update",
            "/p/panel/xray/update",
        ]
        assert c._admin_prefix == "/panel"

    @pytest.mark.asyncio
    async def test_real_api_error_does_not_trigger_fallback(self):
        paths = []

        def handler(req):
            hit = _cookie_session_handler(req)
            if hit is not None:
                return hit
            paths.append(req.url.path)
            return _fail("boom")

        c = _make_cookie_client(_mock(handler))
        try:
            with pytest.raises(XuiAPIError) as exc:
                await c.push_xray_setting({"outbounds": []})
        finally:
            await c.aclose()
        assert exc.value.kind == "api"
        assert paths == ["/p/panel/api/xray/update"]
        assert c._admin_prefix is None


# ── Embedded-client coercion on add/update-inbound (v3.6.0 strictness) ────


class TestInboundClientCoercion:
    @pytest.mark.asyncio
    async def test_add_inbound_coerces_clients_in_string_settings(self):
        captured = {}

        def handler(req):
            captured["body"] = json.loads(req.content)
            return _ok({"id": 7})

        payload = {
            "remark": "t", "port": 443, "protocol": "vless",
            "settings": json.dumps({
                "clients": [{
                    "id": "u-u-i-d", "email": "e",
                    "limitIp": 0, "totalGB": 0, "expiryTime": 0,
                    "enable": True, "tgId": "", "subId": "",
                }],
                "decryption": "none",
            }),
        }
        c = _make_client(_mock(handler))
        try:
            await c.add_inbound(payload)
        finally:
            await c.aclose()
        sent = captured["body"]["settings"]
        assert isinstance(sent, str)  # original shape preserved
        client0 = json.loads(sent)["clients"][0]
        assert client0["tgId"] == 0
        assert client0["enable"] is True
        assert client0["id"] == "u-u-i-d"
        assert json.loads(sent)["decryption"] == "none"

    @pytest.mark.asyncio
    async def test_update_inbound_coerces_clients_in_dict_settings(self):
        captured = {}

        def handler(req):
            captured["body"] = json.loads(req.content)
            return _ok({})

        payload = {
            "remark": "t",
            "settings": {
                "clients": [{"email": "e", "tgId": "", "limitIp": "3",
                             "enable": "true"}],
            },
        }
        c = _make_client(_mock(handler))
        try:
            await c.update_inbound(7, payload)
        finally:
            await c.aclose()
        sent = captured["body"]["settings"]
        assert isinstance(sent, dict)  # original shape preserved
        assert sent["clients"][0]["tgId"] == 0
        assert sent["clients"][0]["limitIp"] == 3
        assert sent["clients"][0]["enable"] is True

    @pytest.mark.asyncio
    async def test_add_inbound_without_clients_untouched(self):
        captured = {}

        def handler(req):
            captured["body"] = json.loads(req.content)
            return _ok({})

        payload = {"remark": "socks", "settings": {"auth": "password"}}
        c = _make_client(_mock(handler))
        try:
            await c.add_inbound(payload)
        finally:
            await c.aclose()
        assert captured["body"]["settings"] == {"auth": "password"}


class TestResolveClientEmailNaturalIds:
    """`del_client` / `update_client` take the client's NATURAL id, which
    is `id` only for vless/vmess. Trojan and shadowsocks clients have no
    `id` at all — the UI sends `password` (or `user`), exactly as the
    export path already accepts. Matching on `id` alone made every
    delete of a non-vless client fail with a 502."""

    def _inbound_with(self, client: dict):
        def handler(req: httpx.Request) -> httpx.Response:
            if "/inbounds/get/" in req.url.path:
                return _ok({"id": 7, "settings": json.dumps({"clients": [client]})})
            return _ok(None)
        return handler

    @pytest.mark.asyncio
    async def test_vless_client_matched_by_id(self):
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            if "/inbounds/get/" in req.url.path:
                return _ok({"id": 7, "settings": json.dumps({"clients": [
                    {"id": "uuid-1", "email": "pi-vless"},
                ]})})
            captured["path"] = req.url.path
            return _ok(None)

        c = _make_client(_mock(handler))
        try:
            await c.del_client(7, "uuid-1")
        finally:
            await c.aclose()
        assert captured["path"].endswith("/panel/api/clients/del/pi-vless")

    @pytest.mark.asyncio
    async def test_trojan_client_matched_by_password(self):
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            if "/inbounds/get/" in req.url.path:
                return _ok({"id": 7, "settings": json.dumps({"clients": [
                    {"password": "s3cret", "email": "pi-trojan"},
                ]})})
            captured["path"] = req.url.path
            return _ok(None)

        c = _make_client(_mock(handler))
        try:
            await c.del_client(7, "s3cret")
        finally:
            await c.aclose()
        assert captured["path"].endswith("/panel/api/clients/del/pi-trojan")

    @pytest.mark.asyncio
    async def test_socks_client_matched_by_user(self):
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            if "/inbounds/get/" in req.url.path:
                return _ok({"id": 7, "settings": json.dumps({"clients": [
                    {"user": "alice", "email": "pi-socks"},
                ]})})
            captured["path"] = req.url.path
            return _ok(None)

        c = _make_client(_mock(handler))
        try:
            await c.del_client(7, "alice")
        finally:
            await c.aclose()
        assert captured["path"].endswith("/panel/api/clients/del/pi-socks")

    @pytest.mark.asyncio
    async def test_client_matched_by_email(self):
        captured: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            if "/inbounds/get/" in req.url.path:
                return _ok({"id": 7, "settings": json.dumps({"clients": [
                    {"password": "pw", "email": "pi-by-email"},
                ]})})
            captured["path"] = req.url.path
            return _ok(None)

        c = _make_client(_mock(handler))
        try:
            await c.del_client(7, "pi-by-email")
        finally:
            await c.aclose()
        assert captured["path"].endswith("/panel/api/clients/del/pi-by-email")

    @pytest.mark.asyncio
    async def test_unknown_client_still_raises_not_found(self):
        def handler(req: httpx.Request) -> httpx.Response:
            if "/inbounds/get/" in req.url.path:
                return _ok({"id": 7, "settings": json.dumps({"clients": [
                    {"id": "uuid-1", "email": "pi-vless"},
                ]})})
            return _ok(None)

        c = _make_client(_mock(handler))
        try:
            with pytest.raises(XuiAPIError) as exc:
                await c.del_client(7, "nope")
        finally:
            await c.aclose()
        assert exc.value.kind == "not_found"
