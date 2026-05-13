"""Tests for the inbound preset registry (since v1.3.0-beta.7).

Each preset's `build_payload` is exercised with realistic inputs and
the resulting panel-API payload is asserted against the wire shape
3x-ui v3.0.0 expects (decoded from `web/controller/inbound.go` +
samples observed during Phase 2 smoke testing).
"""
from __future__ import annotations

import pytest

from app.core.xui_presets import (
    PRESETS,
    get_preset,
    list_presets,
)


class TestPresetRegistry:
    def test_six_presets_present(self):
        # Locks the spec list. Adding/removing a preset is a deliberate
        # bump-test event, not a silent migration.
        ids = [p.id for p in list_presets()]
        assert ids == [
            "vless-reality-vision",
            "vless-xhttp-reality",
            "vless-ws",
            "vless-xhttp",
            "trojan-grpc",
            "socks5",
        ]

    def test_each_preset_has_required_metadata(self):
        for p in PRESETS.values():
            assert p.id and p.label and p.description
            assert p.protocol in ("vless", "trojan", "socks")
            assert isinstance(p.fields, list) and len(p.fields) > 0

    def test_get_preset_unknown_returns_none(self):
        assert get_preset("does-not-exist") is None

    def test_reality_presets_dont_need_domain(self):
        for pid in ("vless-reality-vision", "vless-xhttp-reality"):
            p = get_preset(pid)
            assert p is not None and p.needs_domain is False
            assert p.supports_reality is True

    def test_tls_presets_need_domain(self):
        for pid in ("vless-ws", "vless-xhttp", "trojan-grpc"):
            p = get_preset(pid)
            assert p is not None and p.needs_domain is True
            assert p.supports_reality is False

    def test_socks5_is_plain(self):
        p = get_preset("socks5")
        assert p is not None
        assert p.needs_domain is False
        assert p.supports_reality is False
        assert p.protocol == "socks"


class TestVlessRealityVision:
    def test_payload_shape(self):
        p = get_preset("vless-reality-vision")
        assert p is not None
        payload = p.build_payload(
            {"port": "443", "sni": "www.cloudflare.com", "remark": "test"},
            uuid="dead-beef",
            private_key="PRIV_KEY",
            public_key="PUB_KEY",
            short_id="abcd1234",
        )
        assert payload["protocol"] == "vless"
        assert payload["port"] == 443
        assert payload["remark"] == "test"

        s = payload["settings"]
        client = s["clients"][0]
        assert client["id"] == "dead-beef"
        assert client["flow"] == "xtls-rprx-vision"
        assert client["email"].startswith("pi-")

        ss = payload["streamSettings"]
        assert ss["network"] == "tcp"
        assert ss["security"] == "reality"
        rs = ss["realitySettings"]
        assert rs["dest"] == "www.cloudflare.com:443"
        assert rs["serverNames"] == ["www.cloudflare.com"]
        assert rs["privateKey"] == "PRIV_KEY"
        assert rs["shortIds"] == ["abcd1234"]
        assert rs["settings"]["publicKey"] == "PUB_KEY"
        assert rs["settings"]["fingerprint"] == "chrome"

        # Sniffing must be on for vision/Reality to work end-to-end.
        assert payload["sniffing"]["enabled"] is True


class TestVlessXhttpReality:
    def test_uses_xhttp_network_with_path(self):
        p = get_preset("vless-xhttp-reality")
        assert p is not None
        payload = p.build_payload(
            {"port": "8443", "sni": "www.google.com", "xhttp_path": "/abc/xyz"},
            uuid="u",
            private_key="priv",
            public_key="pub",
            short_id="sid",
        )
        ss = payload["streamSettings"]
        assert ss["network"] == "xhttp"
        assert ss["security"] == "reality"
        assert ss["xhttpSettings"]["path"] == "/abc/xyz"
        # `auto` matches what known-good vless-xhttp-reality clients
        # ship — `packet-up` requires explicit `&mode=packet-up` in
        # the link, which most panel-emitted URLs don't carry.
        assert ss["xhttpSettings"]["mode"] == "auto"
        # No flow (vision is TCP-only on Reality).
        client = payload["settings"]["clients"][0]
        assert client["flow"] == ""


class TestVlessWs:
    def test_xui_pro_reverse_proxy_shape(self):
        """Domain-mode presets follow xui-pro's reverse-proxy
        convention: inbound listens on a random high port, path is
        prefixed with that port, and `externalProxy` points the
        client-facing endpoint at <domain>:443 + TLS (nginx upstairs
        terminates TLS for us)."""
        p = get_preset("vless-ws")
        assert p is not None
        payload = p.build_payload(
            {"ws_path": "secret"},
            uuid="u-uuid",
            domain="proxy.example.com",
        )
        # Random port — NOT 443 (nginx holds 443).
        assert 30000 <= payload["port"] < 40000
        ss = payload["streamSettings"]
        assert ss["network"] == "ws"
        # nginx terminates TLS; inbound itself is plain ws.
        assert ss["security"] == "none"
        assert "tlsSettings" not in ss
        # Path = `/<inbound_port>/<tail>`.
        assert ss["wsSettings"]["path"] == f"/{payload['port']}/secret"
        assert ss["wsSettings"]["headers"]["Host"] == "proxy.example.com"
        # externalProxy points clients at the panel domain on :443.
        ep = ss["externalProxy"]
        assert isinstance(ep, list) and len(ep) == 1
        assert ep[0]["dest"] == "proxy.example.com"
        assert ep[0]["port"] == 443
        assert ep[0]["forceTls"] == "tls"

    def test_random_ws_path_when_omitted(self):
        p = get_preset("vless-ws")
        assert p is not None
        payload = p.build_payload(
            {}, uuid="u", domain="d.example",
        )
        # `/<port>/<hex>` shape — non-empty tail.
        path = payload["streamSettings"]["wsSettings"]["path"]
        assert path.startswith(f"/{payload['port']}/")
        assert len(path) > len(f"/{payload['port']}/")


class TestTrojanGrpc:
    def test_password_and_servicename_random_when_omitted(self):
        p = get_preset("trojan-grpc")
        assert p is not None
        payload = p.build_payload(
            {}, domain="t.example.com",
        )
        assert payload["protocol"] == "trojan"
        # Inbound on a random high port (xui-pro reverse-proxy).
        assert 30000 <= payload["port"] < 40000
        client = payload["settings"]["clients"][0]
        assert "id" not in client  # Trojan uses `password`, not `id`.
        assert client["password"] and len(client["password"]) > 10
        ss = payload["streamSettings"]
        assert ss["network"] == "grpc"
        assert ss["security"] == "none"
        assert "tlsSettings" not in ss
        # gRPC service name carries the port prefix nginx routes on,
        # with a leading slash to match the panel-UI display form.
        assert ss["grpcSettings"]["serviceName"].startswith(
            f"/{payload['port']}/",
        )
        # Multi-stream H2 + authority — matches the upstream-recommended
        # config for nginx-fronted gRPC.
        assert ss["grpcSettings"]["multiMode"] is True
        assert ss["grpcSettings"]["authority"] == "t.example.com"
        ep = ss["externalProxy"]
        assert ep[0]["dest"] == "t.example.com"
        assert ep[0]["port"] == 443
        assert ep[0]["forceTls"] == "tls"


class TestSocks5:
    def test_plain_user_pass(self):
        p = get_preset("socks5")
        assert p is not None
        payload = p.build_payload({"port": "1080", "user": "u1", "password": "pw1"})
        assert payload["protocol"] == "socks"
        assert payload["port"] == 1080
        assert payload["streamSettings"]["security"] == "none"
        accounts = payload["settings"]["accounts"]
        assert accounts == [{"user": "u1", "pass": "pw1"}]
        # Sniffing should be DISABLED for plain socks (the protocol IS
        # the metadata; sniffing it would just waste cycles).
        assert payload["sniffing"]["enabled"] is False

    def test_random_creds_when_omitted(self):
        p = get_preset("socks5")
        assert p is not None
        payload = p.build_payload({"port": "1080"})
        accounts = payload["settings"]["accounts"]
        assert accounts[0]["user"].startswith("pi")
        assert len(accounts[0]["pass"]) > 10
