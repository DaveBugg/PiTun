"""Hysteria2 outbound shape and the removed `allowInsecure`.

xray 26.x refuses the older hysteria shape ("not hysteria transport") and
treats `allowInsecure: true` as a hard error, either of which stops the whole
config from loading. These tests pin the accepted shape; the real-binary check
is `xray run -test` on the generated config (run on a box that has xray).
"""
import json
import shutil
import subprocess

import pytest

from app.core.config_gen import generate_config
from app.models import Node


def _default_settings(**overrides):
    s = {
        "mode": "rules", "log_level": "warning", "dns_port": "5353",
        "tproxy_port_tcp": "7893", "tproxy_port_udp": "7894",
        "socks_port": "1080", "http_port": "8080",
        "bypass_private": "true", "fakedns_enabled": "false",
        "dns_sniffing": "true", "inbound_mode": "tproxy",
        "dns_upstream": "8.8.8.8", "dns_mode": "plain",
        "dns_upstream_secondary": "", "dns_fallback": "",
        "bypass_cn_dns": "false", "bypass_ru_dns": "false",
    }
    s.update(overrides)
    return s


def _find_outbound(config, tag):
    return next((o for o in config["outbounds"] if o["tag"] == tag), None)


def _make_node(id=1, protocol="vless", **kwargs):
    defaults = dict(
        name="test", address="1.2.3.4", port=443, uuid="test-uuid",
        transport="tcp", tls="tls", sni="example.com", enabled=True,
        fingerprint="chrome", alpn=None, allow_insecure=False,
        password=None, hy2_obfs=None, hy2_obfs_password=None,
        is_online=True, order=0, chain_node_id=None,
    )
    defaults.update(kwargs)
    return Node(id=id, protocol=protocol, **defaults)


def _hy2(**kw):
    return _make_node(id=1, protocol="hy2", address="203.0.113.50", port=29164,
                      password="pw-test", sni="example.com", **kw)


def _outbound(node):
    return _find_outbound(generate_config(node, [node], [], "global", _default_settings()), "node-1")


def test_uses_the_hysteria_transport_with_auth_in_hysteria_settings():
    ob = _outbound(_hy2())
    assert ob["protocol"] == "hysteria"
    assert ob["settings"] == {"version": 2, "address": "203.0.113.50", "port": 29164}
    st = ob["streamSettings"]
    assert st["network"] == "hysteria" and st["security"] == "tls"
    assert st["hysteriaSettings"] == {"version": 2, "auth": "pw-test"}
    assert "password" not in ob["settings"]
    assert st["sockopt"]["mark"] == 255
    assert st["tlsSettings"]["serverName"] == "example.com"


def test_alpn_defaults_to_h3_and_honours_the_node():
    assert _outbound(_hy2())["streamSettings"]["tlsSettings"]["alpn"] == ["h3"]
    assert _outbound(_hy2(alpn="h3, h3-29"))["streamSettings"]["tlsSettings"]["alpn"] == ["h3", "h3-29"]


def test_salamander_obfuscation_is_a_udp_finalmask():
    ob = _outbound(_hy2(hy2_obfs="salamander", hy2_obfs_password="obfs-pw"))
    assert ob["streamSettings"]["finalmask"] == {
        "udp": [{"type": "salamander", "settings": {"password": "obfs-pw"}}],
    }
    assert "obfs" not in ob["settings"]


def test_no_obfuscation_means_no_finalmask():
    assert "finalmask" not in _outbound(_hy2())["streamSettings"]


def test_an_unsupported_obfs_type_is_dropped_not_guessed():
    ob = _outbound(_hy2(hy2_obfs="mystery", hy2_obfs_password="x"))
    assert "finalmask" not in ob["streamSettings"]


@pytest.mark.parametrize("proto,extra", [
    ("vless", dict(uuid="u", tls="tls")),
    ("trojan", dict(password="p", tls="tls")),
    ("hy2", dict(password="p")),
])
def test_allow_insecure_is_never_emitted(proto, extra):
    node = _make_node(id=1, protocol=proto, allow_insecure=True, **extra)
    ob = _outbound(node)
    assert "allowInsecure" not in json.dumps(ob)


def test_hysteria_is_not_wrapped_in_the_tcp_fragmenter():
    node = _hy2()
    settings = {**_default_settings(), "xray_fragment_enabled": "true"}
    cfg = generate_config(node, [node], [], "global", settings)
    ob = _find_outbound(cfg, "node-1")
    assert "dialerProxy" not in ob["streamSettings"]["sockopt"]


@pytest.mark.skipif(shutil.which("xray") is None, reason="xray binary not installed here")
def test_the_real_xray_accepts_the_generated_config(tmp_path):
    node = _hy2(hy2_obfs="salamander", hy2_obfs_password="obfs-pw")
    cfg = generate_config(node, [node], [], "global", _default_settings())
    path = tmp_path / "c.json"
    path.write_text(json.dumps(cfg))
    r = subprocess.run(["xray", "run", "-test", "-config", str(path)], capture_output=True, text=True)
    assert "Configuration OK" in (r.stdout + r.stderr), r.stdout + r.stderr
