"""Hysteria2 hiding and hopping: how the port looks to a probe (website or noise),
the port-hopping redirect on the VPS, and how both reach the node and its link.
The panel and the VPS are faked."""
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import xui_hysteria
from app.core.config_gen import _outbound_hy2, hop_params
from app.core.ssh import DeployResult
from app.core.uri_formatter import inbound_client_to_uri, node_to_uri
from app.core.uri_parser import parse_uri
from app.core.xui_presets import get_preset
from app.models import Node, Server, XuiServer

PIN = "cd" * 32
SELF_CERT = "CERT=/etc/x-ui/pitun-hy2/hy2-2443.crt\nKEY=/etc/x-ui/pitun-hy2/hy2-2443.key\nSELF=1\nPIN=" + PIN + "\n"
CA_CERT = ("CERT=/etc/letsencrypt/live/example.test/fullchain.pem\n"
           "KEY=/etc/letsencrypt/live/example.test/privkey.pem\nSELF=0\n")


# ── hop range validation ─────────────────────────────────────────────────────

def test_hop_defaults_and_normalising():
    assert xui_hysteria.validate_hop("", "", 2443) == ("20000-32000", "10-30")
    assert xui_hysteria.validate_hop(" 21000 - 22000 ".replace(" - ", "-"), "5", 2443) == ("21000-22000", "5")


@pytest.mark.parametrize("ports,interval", [
    ("500-900", "10-30"),            # below 1024
    ("20000-70000", "10-30"),        # above 65535
    ("30000-20000", "10-30"),        # reversed
    ("20000", "10-30"),              # not a range
    ("1000-30000", "10-30"),
    ("10-30000", "10-30"),           # includes ssh and privileged ports
    ("20000-60000", "10-30"),        # more than 20000 ports
    ("20000-21000", "1-2"),          # interval below 5 s
    ("20000-21000", "30-10"),
    ("20000-21000", "99999"),
    ("20000-21000; reboot", "10"),
    ("20000-21000", "10-30 && id"),
])
def test_bad_hop_settings_are_refused(ports, interval):
    with pytest.raises(ValueError):
        xui_hysteria.validate_hop(ports, interval, 2443)


def test_the_redirect_rule_is_safe_by_construction():
    s = xui_hysteria._HOP_APPLY
    assert "ct state new udp dport $LO-$HI counter redirect to :$PORT" in s    # replies to our own traffic are never touched
    assert "flush ruleset" not in s and "iptables -F" not in s          # nothing else is ever flushed
    assert "ss -lunH" in s and "already in use" in s                    # a range with a listener is refused
    assert "pitun_hy2_hop_$PORT" in s                                   # its own table, removable on its own
    r = xui_hysteria._HOP_REMOVE
    assert "delete table" in r and "rm -f" in r and "flush" not in r


# ── what the preset puts on the wire ─────────────────────────────────────────

def _payload(**kw):
    return get_preset("hysteria2").build_payload({"port": "2443"}, cert_file="/c", key_file="/k", sni="x.test", **kw)


def test_website_mode_adds_a_masquerade_and_no_obfuscation():
    st = _payload(masquerade={"type": "file", "dir": "/var/www/html"})["streamSettings"]
    assert st["hysteriaSettings"]["masquerade"] == {"type": "file", "dir": "/var/www/html"}
    assert "finalmask" not in st


def test_noise_mode_adds_salamander_and_no_masquerade():
    st = _payload(obfs_password="pw123")["streamSettings"]
    assert st["finalmask"] == {"udp": [{"type": "salamander", "settings": {"password": "pw123"}}]}
    assert "masquerade" not in st["hysteriaSettings"]


def test_plain_mode_adds_neither():
    st = _payload()["streamSettings"]
    assert "finalmask" not in st and "masquerade" not in st["hysteriaSettings"]


def test_salamander_password_is_read_back_from_the_inbound():
    assert xui_hysteria.salamander_password({"finalmask": {"udp": [{"type": "salamander", "settings": {"password": "p"}}]}}) == "p"
    assert xui_hysteria.salamander_password({}) is None
    assert xui_hysteria.salamander_password({"finalmask": {"udp": [{"type": "header-dns"}]}}) is None


# ── the node, the xray config and the link ───────────────────────────────────

def _inbound(obfs=None, port=2443):
    stream = {
        "network": "hysteria", "security": "tls",
        "tlsSettings": {"serverName": "x.test", "alpn": ["h3"],
                        "certificates": [{"certificateFile": "/etc/x-ui/pitun-hy2/hy2-2443.crt"}]},
        "hysteriaSettings": {"version": 2, "auth": ""},
    }
    if obfs:
        stream["finalmask"] = {"udp": [{"type": "salamander", "settings": {"password": obfs}}]}
    return {"id": 1, "remark": "hy2", "port": port, "protocol": "hysteria",
            "settings": {"clients": [{"auth": "tok", "email": "pi-1"}]}, "streamSettings": stream}


def test_node_fields_carry_the_obfuscation_and_the_hop():
    f = xui_hysteria.node_fields(_inbound(obfs="pw"), {"auth": "tok"}, "203.0.113.5", PIN,
                                 {"ports": "20000-32000", "interval": "10-30"})
    assert f["hy2_obfs"] == "salamander" and f["hy2_obfs_password"] == "pw"
    assert f["hy2_hop_ports"] == "20000-32000" and f["hy2_hop_interval"] == "10-30"
    plain = xui_hysteria.node_fields(_inbound(), {"auth": "tok"}, "203.0.113.5")
    assert plain["hy2_obfs"] is None and plain["hy2_hop_ports"] is None


def _node(**kw):
    d = dict(id=5, name="n", protocol="hy2", address="203.0.113.5", port=2443, password="tok",
             sni="x.test", alpn="h3", tls="tls", hy2_obfs="salamander", hy2_obfs_password="pw",
             hy2_hop_ports="20000-32000", hy2_hop_interval="10-30")
    d.update(kw)
    return Node(**d)


def test_xray_outbound_gets_salamander_then_the_remote_port_hop():
    """Measured against xray 26.10.31 on a real server: only the `udphop` mask in
    remote mode moves the destination port; `quicParams.udpHop` is accepted and
    does nothing. Obfuscation goes first, hopping after it."""
    out = _outbound_hy2(_node())
    fm = out["streamSettings"]["finalmask"]
    assert fm["udp"] == [
        {"type": "salamander", "settings": {"password": "pw"}},
        {"type": "udphop", "settings": {"mode": "intervalremote", "interval": "10-30",
                                        "remotePorts": "20000-32000"}},
    ]
    assert "quicParams" not in fm


def test_hop_without_obfuscation_is_the_only_mask():
    out = _outbound_hy2(_node(hy2_obfs=None, hy2_obfs_password=None))
    assert [m["type"] for m in out["streamSettings"]["finalmask"]["udp"]] == ["udphop"]


def test_a_node_without_hop_has_no_hop_mask():
    out = _outbound_hy2(_node(hy2_hop_ports=None, hy2_hop_interval=None))
    assert [m["type"] for m in out["streamSettings"]["finalmask"]["udp"]] == ["salamander"]
    assert "finalmask" not in _outbound_hy2(_node(hy2_obfs=None, hy2_obfs_password=None,
                                                  hy2_hop_ports=None))["streamSettings"]


@pytest.mark.parametrize("ports", ["abc", "20000-", "20000-21000;rm", "", None, "1-2-3"])
def test_garbage_hop_values_never_reach_the_xray_config(ports):
    assert hop_params(_node(hy2_hop_ports=ports)) is None


def test_hop_interval_falls_back_to_a_sane_default():
    assert hop_params(_node(hy2_hop_interval="nonsense"))["interval"] == "10-30"
    assert hop_params(_node(hy2_hop_interval=None))["interval"] == "10-30"


def test_the_share_link_round_trips_obfuscation_and_hop():
    uri = node_to_uri(_node())
    assert "obfs=salamander" in uri and "obfs-password=pw" in uri and "mport=20000-32000" in uri
    back = parse_uri(uri)
    assert back["hy2_obfs"] == "salamander" and back["hy2_obfs_password"] == "pw"
    assert back["hy2_hop_ports"] == "20000-32000" and back["hy2_hop_interval"] == "10-30"


def test_inbound_share_link_includes_what_the_server_does():
    uri = inbound_client_to_uri(_inbound(obfs="pw"), {"auth": "tok", "email": "pi-1"}, "203.0.113.5", PIN,
                                {"ports": "20000-32000", "interval": "10-30"})
    assert "obfs-password=pw" in uri and "mport=20000-32000" in uri and "pinSHA256=" in uri


# ── create_inbound: which mode, and the hop rule ─────────────────────────────

class _FakePanel:
    created = None
    reject = False

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def add_inbound(self, wire):
        from app.core.xui_api import XuiAPIError
        if type(self).reject:
            raise XuiAPIError("no", kind="api")
        type(self).created = wire
        return {"id": 7, "port": wire["port"]}

    async def restart_xray(self):
        return None


@pytest.fixture
def setup(session, monkeypatch):
    _FakePanel.created, _FakePanel.reject = None, False
    srv = Server(name="vps", host="203.0.113.5", port=22, user="root", auth_type="password", password="pw")
    session.add(srv)
    session.commit()
    session.refresh(srv)
    xs = XuiServer(server_id=srv.id, api_token="t", panel_user="a", panel_pass="p",
                   panel_port=2053, panel_basepath="/p", mode="bare")
    session.add(xs)
    session.commit()
    session.refresh(xs)
    import app.api.xui as api
    monkeypatch.setattr(api, "XuiClient", _FakePanel)
    monkeypatch.setattr(api, "_ufw_chain_ports", AsyncMock())
    return xs, api


def _ssh(monkeypatch, stdout):
    calls = []

    async def fake(**kw):
        calls.append(kw)
        return DeployResult(ok=True, exit_code=0, stdout=stdout, stderr="")
    monkeypatch.setattr(xui_hysteria, "exec_remote_script", fake)
    return calls


def _stream():
    return json.loads(_FakePanel.created["streamSettings"])


def _create(client, auth_headers, xs, **values):
    return client.post(f"/api/xui/servers/{xs.id}/inbounds",
                       json={"preset_id": "hysteria2", "values": {"port": "2443", **values}}, headers=auth_headers)


def test_auto_on_a_bare_panel_is_noise(client, admin_user, auth_headers, setup, monkeypatch):
    xs, _ = setup
    _ssh(monkeypatch, SELF_CERT)
    assert _create(client, auth_headers, xs).status_code == 201
    st = _stream()
    assert st["finalmask"]["udp"][0]["type"] == "salamander" and len(st["finalmask"]["udp"][0]["settings"]["password"]) >= 16
    assert "masquerade" not in st["hysteriaSettings"]


def test_auto_on_a_pro_panel_with_a_real_cert_is_the_website(client, admin_user, auth_headers, session, setup, monkeypatch):
    xs, _ = setup
    xs.domain, xs.mode = "vpn.example.test", "xui-pro"
    session.add(xs)
    session.commit()
    _ssh(monkeypatch, CA_CERT)
    assert _create(client, auth_headers, xs).status_code == 201
    st = _stream()
    # relays to the panel's own site: the same answer the name gives over TCP
    assert st["hysteriaSettings"]["masquerade"] == {"type": "proxy", "url": "https://vpn.example.test", "rewriteHost": True}
    assert "finalmask" not in st


def test_auto_on_a_pro_panel_with_a_self_signed_cert_falls_back_to_noise(client, admin_user, auth_headers, session, setup, monkeypatch):
    xs, _ = setup
    xs.domain, xs.mode = "vpn.example.test", "xui-pro"
    session.add(xs)
    session.commit()
    _ssh(monkeypatch, SELF_CERT)
    assert _create(client, auth_headers, xs).status_code == 201
    assert "finalmask" in _stream()


@pytest.mark.parametrize("mode,expect_noise,expect_site", [("noise", True, False), ("site", False, True), ("none", False, False)])
def test_explicit_modes(client, admin_user, auth_headers, setup, monkeypatch, mode, expect_noise, expect_site):
    xs, _ = setup
    _ssh(monkeypatch, SELF_CERT)
    assert _create(client, auth_headers, xs, mask=mode).status_code == 201
    st = _stream()
    assert ("finalmask" in st) is expect_noise
    assert ("masquerade" in st["hysteriaSettings"]) is expect_site
    if expect_site:     # a bare panel has no fake site to serve: a small page of its own
        assert st["hysteriaSettings"]["masquerade"]["type"] == "string"


def test_unknown_mode_is_refused(client, admin_user, auth_headers, setup, monkeypatch):
    xs, _ = setup
    _ssh(monkeypatch, SELF_CERT)
    assert _create(client, auth_headers, xs, mask="telepathy").status_code == 400


def test_hop_on_installs_the_redirect_before_the_inbound(client, admin_user, auth_headers, setup, monkeypatch):
    xs, _ = setup
    calls = _ssh(monkeypatch, SELF_CERT + "HOP=ok\n")
    r = _create(client, auth_headers, xs, hop="on", hop_ports="21000-22000", hop_interval="8-12")
    assert r.status_code == 201, r.text
    hop_calls = [c for c in calls if "HOPRANGE" in (c.get("env") or {})]
    assert len(hop_calls) == 1
    assert hop_calls[0]["env"] == {"PORT": "2443", "HOPRANGE": "21000-22000", "HOPINTERVAL": "8-12"}


def test_a_refused_hop_range_creates_nothing(client, admin_user, auth_headers, setup, monkeypatch):
    xs, _ = setup
    _ssh(monkeypatch, SELF_CERT)
    r = _create(client, auth_headers, xs, hop="on", hop_ports="10-30000")
    assert r.status_code == 502 and "Port hopping" in r.json()["detail"]
    assert _FakePanel.created is None


def test_a_hop_range_in_use_on_the_server_creates_nothing(client, admin_user, auth_headers, setup, monkeypatch):
    xs, _ = setup
    calls = []

    async def fake(**kw):
        calls.append(kw)
        if "HOPRANGE" in (kw.get("env") or {}):
            return DeployResult(ok=False, exit_code=4, stdout="ERR=UDP ports in the range are already in use: 21500\n", stderr="")
        return DeployResult(ok=True, exit_code=0, stdout=SELF_CERT, stderr="")
    monkeypatch.setattr(xui_hysteria, "exec_remote_script", fake)
    r = _create(client, auth_headers, xs, hop="on", hop_ports="21000-22000")
    assert r.status_code == 502 and "21500" in r.json()["detail"]
    assert _FakePanel.created is None


def test_if_the_panel_rejects_the_inbound_the_hop_rule_is_taken_back(client, admin_user, auth_headers, setup, monkeypatch):
    xs, _ = setup
    calls = _ssh(monkeypatch, SELF_CERT + "HOP=ok\n")
    _FakePanel.reject = True
    r = _create(client, auth_headers, xs, hop="on")
    assert r.status_code == 502
    scripts = [c["script_content"] for c in calls]
    assert xui_hysteria._HOP_REMOVE in scripts


def test_without_hop_no_rule_is_touched(client, admin_user, auth_headers, setup, monkeypatch):
    xs, _ = setup
    calls = _ssh(monkeypatch, SELF_CERT)
    assert _create(client, auth_headers, xs).status_code == 201
    assert not [c for c in calls if "HOPRANGE" in (c.get("env") or {})]
