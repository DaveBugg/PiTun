"""Hysteria2 on an x-ui panel: preset, client, share link, node export.

The panel and the VPS are faked; what is pinned here is the wire shape sent to
the panel (matching 3x-ui v3.11's hysteria inbound), the certificate handling
and the node that comes out the other end.
"""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core import xui_hysteria
from app.core.ssh import DeployResult
from app.core.uri_formatter import inbound_client_to_uri
from app.core.uri_parser import parse_uri
from app.core.xui_presets import get_preset
from app.models import Node, Server, XuiServer

PIN = "ab" * 32


def _inbound(auth="tok-auth", port=443, sni="vpn.example.test", cert="/etc/x-ui/pitun-hy2/hy2-443.crt"):
    return {
        "id": 7, "remark": "hy2", "port": port, "protocol": "hysteria",
        "settings": {"version": 2, "clients": [{"auth": auth, "email": "pi-1234", "enable": True}]},
        "streamSettings": {
            "network": "hysteria", "security": "tls",
            "tlsSettings": {"serverName": sni, "alpn": ["h3"],
                            "certificates": [{"certificateFile": cert, "keyFile": cert[:-3] + "key"}]},
            "hysteriaSettings": {"version": 2, "auth": "", "udpIdleTimeout": 60},
        },
    }


# ── the preset ───────────────────────────────────────────────────────────────

def test_preset_payload_matches_the_panels_hysteria_inbound():
    p = get_preset("hysteria2")
    payload = p.build_payload({"port": "443", "remark": "mine"}, cert_file="/c.pem", key_file="/k.pem", sni="vpn.example.test")
    assert payload["protocol"] == "hysteria" and payload["port"] == 443 and payload["remark"] == "mine"
    assert payload["settings"]["version"] == 2
    [c] = payload["settings"]["clients"]
    assert len(c["auth"]) >= 16 and c["email"].startswith("pi-") and "id" not in c
    st = payload["streamSettings"]
    assert st["network"] == "hysteria" and st["security"] == "tls"
    assert st["tlsSettings"]["serverName"] == "vpn.example.test"
    assert st["tlsSettings"]["alpn"] == ["h3"]
    assert st["tlsSettings"]["certificates"] == [{"certificateFile": "/c.pem", "keyFile": "/k.pem"}]
    assert st["hysteriaSettings"]["version"] == 2


def test_each_inbound_gets_its_own_auth_token():
    p = get_preset("hysteria2")
    a = p.build_payload({"port": 443}, cert_file="/c", key_file="/k", sni="x.test")
    b = p.build_payload({"port": 443}, cert_file="/c", key_file="/k", sni="x.test")
    assert a["settings"]["clients"][0]["auth"] != b["settings"]["clients"][0]["auth"]


# ── the certificate on the VPS ───────────────────────────────────────────────

def _fake_ssh(monkeypatch, stdout, ok=True):
    calls = []

    async def fake(**kw):
        calls.append(kw)
        return DeployResult(ok=ok, exit_code=0 if ok else 1, stdout=stdout, error=None if ok else "boom")

    monkeypatch.setattr(xui_hysteria, "exec_remote_script", fake)
    return calls


SRV = SimpleNamespace(host="203.0.113.5", port=22, user="root", password="pw", private_key=None, passphrase=None)


@pytest.mark.asyncio
async def test_pro_panel_reuses_the_lets_encrypt_certificate(monkeypatch):
    calls = _fake_ssh(
        monkeypatch,
        "CERT=/etc/letsencrypt/live/example.test/fullchain.pem\n"
        "KEY=/etc/letsencrypt/live/example.test/privkey.pem\nSELF=0\n",
    )
    got = await xui_hysteria.prepare_certificate(SRV, domain="vpn.example.test", sni="vpn.example.test", port=443)
    assert got == {"cert_file": "/etc/letsencrypt/live/example.test/fullchain.pem",
                   "key_file": "/etc/letsencrypt/live/example.test/privkey.pem",
                   "pin": None, "self_signed": False}
    assert calls[0]["env"] == {"DOMAIN": "vpn.example.test", "SNI": "vpn.example.test", "PORT": "443"}


@pytest.mark.asyncio
async def test_bare_panel_gets_a_self_signed_certificate_and_its_pin(monkeypatch):
    _fake_ssh(
        monkeypatch,
        f"CERT=/etc/x-ui/pitun-hy2/hy2-443.crt\nKEY=/etc/x-ui/pitun-hy2/hy2-443.key\nSELF=1\nPIN={PIN.upper()}\n",
    )
    got = await xui_hysteria.prepare_certificate(SRV, domain=None, sni="www.example.com", port=443)
    assert got["self_signed"] is True and got["pin"] == PIN


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_sni", ["a b", "x;rm -rf /", "$(id)", "", "-leading.example"])
async def test_a_hostile_sni_never_reaches_the_shell(monkeypatch, bad_sni):
    calls = _fake_ssh(monkeypatch, "")
    with pytest.raises(RuntimeError):
        await xui_hysteria.prepare_certificate(SRV, domain=None, sni=bad_sni, port=443)
    assert calls == []


@pytest.mark.asyncio
async def test_a_failed_ssh_run_is_a_short_error(monkeypatch):
    _fake_ssh(monkeypatch, "", ok=False)
    with pytest.raises(RuntimeError) as e:
        await xui_hysteria.prepare_certificate(SRV, domain=None, sni="x.test", port=443)
    assert "boom" not in str(e.value)


@pytest.mark.asyncio
async def test_an_unexpected_certificate_path_is_refused(monkeypatch):
    _fake_ssh(monkeypatch, "CERT=/tmp/$(id).pem\nKEY=/tmp/k.pem\nSELF=0\n")
    with pytest.raises(RuntimeError):
        await xui_hysteria.prepare_certificate(SRV, domain=None, sni="x.test", port=443)


@pytest.mark.asyncio
async def test_pin_is_only_returned_for_a_self_signed_certificate(monkeypatch):
    _fake_ssh(monkeypatch, "SELF=0\n")
    assert await xui_hysteria.remote_cert_pin(SRV, "/etc/letsencrypt/live/x/fullchain.pem") is None
    _fake_ssh(monkeypatch, f"SELF=1\nPIN={PIN}\n")
    assert await xui_hysteria.remote_cert_pin(SRV, "/etc/x-ui/pitun-hy2/hy2-443.crt") == PIN
    assert await xui_hysteria.remote_cert_pin(SRV, "relative/or;evil") is None


# ── node + share link ────────────────────────────────────────────────────────

def test_node_fields_make_a_hy2_node_from_the_clients_auth():
    f = xui_hysteria.node_fields(_inbound(), {"auth": "tok-auth", "email": "pi-1234"}, "203.0.113.5", PIN)
    assert f["protocol"] == "hy2" and f["password"] == "tok-auth" and f["uuid"] is None
    assert f["address"] == "203.0.113.5" and f["port"] == 443
    assert f["sni"] == "vpn.example.test" and f["alpn"] == "h3" and f["tls"] == "tls"
    assert f["pinned_cert_sha256"] == PIN
    Node(name="x", **f)   # every key is a real Node column


def test_share_link_is_a_hysteria2_uri_that_round_trips():
    uri = inbound_client_to_uri(_inbound(), {"auth": "tok-auth", "email": "pi-1234"}, "203.0.113.5", PIN)
    assert uri.startswith("hysteria2://tok-auth@203.0.113.5:443")
    assert "sni=vpn.example.test" in uri and "alpn=h3" in uri and f"pinSHA256={PIN}" in uri
    back = parse_uri(uri)
    assert back["protocol"] == "hy2" and back["password"] == "tok-auth"
    assert back["pinned_cert_sha256"] == PIN and back["alpn"] == "h3"


def test_share_link_without_a_pin_has_none():
    uri = inbound_client_to_uri(_inbound(), {"auth": "tok-auth", "email": "e"}, "203.0.113.5")
    assert "pinSHA256" not in uri


def test_client_without_auth_gives_no_link():
    assert inbound_client_to_uri(_inbound(), {"email": "e"}, "203.0.113.5") is None


# ── the client body and the firewall ─────────────────────────────────────────

def test_build_client_uses_auth_not_id_for_hysteria():
    from app.api.xui import _build_client
    c = _build_client(label="pi-ab", uuid="would-be-uuid", extras={"protocol": "hysteria"})
    assert c["auth"] and "id" not in c and "password" not in c and c["email"] == "pi-ab"
    c2 = _build_client(label="pi-ab", uuid="u", extras={"protocol": "hysteria", "auth": "mine"})
    assert c2["auth"] == "mine"


@pytest.mark.asyncio
async def test_ufw_opens_and_closes_udp_for_hysteria(monkeypatch):
    from app.core import xui_chain
    sent = []

    async def fake(**kw):
        sent.append(kw["script_content"])
        return DeployResult(ok=True, exit_code=0)

    monkeypatch.setattr(xui_chain, "exec_remote_script", fake)
    srv = SimpleNamespace(host="h", port=22, user="root", password="p", private_key=None)
    await xui_chain._ufw_chain_ports(srv, [443], action="allow", proto="udp")
    await xui_chain._ufw_chain_ports(srv, [443], action="delete-allow", proto="udp")
    await xui_chain._ufw_chain_ports(srv, [8443], action="allow")
    assert "ufw allow 443/udp" in sent[0] and "ufw delete allow 443/udp" in sent[1]
    assert "ufw allow 8443/tcp" in sent[2]
    with pytest.raises(ValueError):
        await xui_chain._ufw_chain_ports(srv, [443], action="allow", proto="sctp")


# ── through the API ──────────────────────────────────────────────────────────

class _FakePanel:
    """Just enough of XuiClient for create_inbound / export."""
    created = None

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def add_inbound(self, wire):
        type(self).created = wire
        return {"id": 7, "port": wire["port"]}

    async def restart_xray(self):
        return None

    async def get_inbound(self, inbound_id):
        return _inbound()


@pytest.fixture
def panel(session):
    srv = Server(name="vps", host="203.0.113.5", port=22, user="root", auth_type="password", password="pw")
    session.add(srv)
    session.commit()
    session.refresh(srv)
    xs = XuiServer(server_id=srv.id, api_token="tok", panel_user="admin", panel_pass="pw",
                   panel_port=2053, panel_basepath="/p", mode="bare")
    session.add(xs)
    session.commit()
    session.refresh(xs)
    return xs, srv


def test_create_hysteria2_inbound_prepares_the_cert_and_opens_udp(client, admin_user, auth_headers, panel, monkeypatch):
    xs, _ = panel
    import app.api.xui as api
    monkeypatch.setattr(api, "XuiClient", _FakePanel)
    ufw = []

    async def fake_ufw(srv, ports, *, action, proto="tcp"):
        ufw.append((ports, action, proto))

    monkeypatch.setattr(api, "_ufw_chain_ports", fake_ufw)
    _fake_ssh(
        monkeypatch,
        f"CERT=/etc/x-ui/pitun-hy2/hy2-443.crt\nKEY=/etc/x-ui/pitun-hy2/hy2-443.key\nSELF=1\nPIN={PIN}\n",
    )

    r = client.post(f"/api/xui/servers/{xs.id}/inbounds",
                    json={"preset_id": "hysteria2", "values": {"port": "443"}}, headers=auth_headers)
    assert r.status_code == 201, r.text
    wire = _FakePanel.created
    assert wire["protocol"] == "hysteria"
    stream = json.loads(wire["streamSettings"])
    assert stream["network"] == "hysteria"
    assert stream["tlsSettings"]["certificates"][0]["certificateFile"] == "/etc/x-ui/pitun-hy2/hy2-443.crt"
    assert json.loads(wire["settings"])["clients"][0]["auth"]
    assert ufw == [([443], "allow", "udp")]


def test_a_ca_signed_cert_forces_the_sni_to_the_panel_domain(client, admin_user, auth_headers, session, panel, monkeypatch):
    xs, _ = panel
    xs.domain, xs.mode = "vpn.example.test", "xui-pro"
    session.add(xs)
    session.commit()
    import app.api.xui as api
    monkeypatch.setattr(api, "XuiClient", _FakePanel)
    monkeypatch.setattr(api, "_ufw_chain_ports", AsyncMock())
    _fake_ssh(
        monkeypatch,
        "CERT=/etc/letsencrypt/live/example.test/fullchain.pem\n"
        "KEY=/etc/letsencrypt/live/example.test/privkey.pem\nSELF=0\n",
    )
    r = client.post(f"/api/xui/servers/{xs.id}/inbounds",
                    json={"preset_id": "hysteria2", "values": {"port": "443", "sni": "something.else"}},
                    headers=auth_headers)
    assert r.status_code == 201, r.text
    assert json.loads(_FakePanel.created["streamSettings"])["tlsSettings"]["serverName"] == "vpn.example.test"


def test_export_makes_a_pinned_hy2_node_and_is_idempotent(client, admin_user, auth_headers, session, panel, monkeypatch):
    xs, srv = panel
    import app.api.xui as api
    monkeypatch.setattr(api, "XuiClient", _FakePanel)
    _fake_ssh(monkeypatch, f"SELF=1\nPIN={PIN}\n")
    url = f"/api/xui/servers/{xs.id}/inbounds/7/clients/pi-1234/export-node"
    r1 = client.post(url, json={}, headers=auth_headers)
    assert r1.status_code == 200, r1.text
    r2 = client.post(url, json={}, headers=auth_headers)
    assert r2.json()["node_id"] == r1.json()["node_id"] and r2.json()["reused"] is True
    session.expire_all()
    node = session.get(Node, r1.json()["node_id"])
    assert node.protocol == "hy2" and node.password == "tok-auth"
    assert node.pinned_cert_sha256 == PIN and node.server_id == srv.id


def test_share_uri_endpoint_includes_the_pin(client, admin_user, auth_headers, panel, monkeypatch):
    xs, _ = panel
    import app.api.xui as api
    monkeypatch.setattr(api, "XuiClient", _FakePanel)
    _fake_ssh(monkeypatch, f"SELF=1\nPIN={PIN}\n")
    r = client.get(f"/api/xui/servers/{xs.id}/inbounds/7/clients/pi-1234/share-uri", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert f"pinSHA256={PIN}" in r.json()["uri"]
