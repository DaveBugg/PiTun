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
        # `client` is a flat ClientRecord — no nested settings string.
        assert body["client"]["uuid"] == "uuid-here"
        assert body["client"]["email"] == "pi-abc"
        assert body["client"]["flow"] == "xtls-rprx-vision"
        # Legacy `id` key must NOT pass through — only `uuid`.
        assert "id" not in body["client"]

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
        assert captured["body"]["client"]["uuid"] == "u-1"

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
