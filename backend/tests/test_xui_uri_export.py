"""`xui://` for a registered panel: the builder is the exact inverse of
`parse_xui_uri`, and the endpoint hands it out for a panel that can be rebuilt."""
import pytest

from app.core.xui_uri import build_xui_uri, parse_xui_uri
from app.models import Server, XuiServer


@pytest.fixture(name="sample_server")
def sample_server_fixture(session):
    s = Server(name="My VPS", host="vps1.example.com", port=22, user="root",
               auth_type="password", password="s3cret")
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _round_trip(**kw):
    base = dict(api_token="tok-abc", host="203.0.113.7", port=34011, basepath="/p4th",
                panel_user="admin", panel_pass="s3cret", domain=None, mode="bare")
    base.update(kw)
    cfg = parse_xui_uri(build_xui_uri(**base))
    assert cfg is not None
    return base, cfg


def test_round_trip_bare():
    b, cfg = _round_trip()
    assert (cfg.api_token, cfg.host, cfg.port, cfg.basepath) == ("tok-abc", "203.0.113.7", 34011, "/p4th")
    assert (cfg.panel_user, cfg.panel_pass, cfg.domain, cfg.mode) == ("admin", "s3cret", None, "bare")


def test_round_trip_pro_with_domain():
    _, cfg = _round_trip(host="panel.example.test", domain="panel.example.test", mode="xui-pro")
    assert cfg.host == "panel.example.test" and cfg.domain == "panel.example.test"
    assert cfg.mode == "xui-pro"


def test_awkward_characters_survive():
    _, cfg = _round_trip(panel_user="a b&c", panel_pass="p@ss/w:rd?=#%", api_token="t+k/en=")
    assert cfg.panel_user == "a b&c"
    assert cfg.panel_pass == "p@ss/w:rd?=#%"
    assert cfg.api_token == "t+k/en="


@pytest.mark.parametrize("path,expected", [("/", "/"), ("", "/"), ("/x/", "/x"), ("x", "/x")])
def test_basepath_normalised(path, expected):
    _, cfg = _round_trip(basepath=path)
    assert cfg.basepath == expected


def _panel(session, server, **kw):
    row = dict(server_id=server.id, api_token="tok-abc", panel_user="admin",
               panel_pass="s3cret", panel_port=2053, panel_basepath="/xyz", mode="bare")
    row.update(kw)
    xs = XuiServer(**row)
    session.add(xs)
    session.commit()
    session.refresh(xs)
    return xs


def test_endpoint_returns_an_importable_uri(client, admin_user, auth_headers, sample_server, session):
    xs = _panel(session, sample_server)
    r = client.get(f"/api/xui/servers/{xs.id}/uri", headers=auth_headers)
    assert r.status_code == 200, r.text
    cfg = parse_xui_uri(r.json()["uri"])
    assert cfg is not None
    assert cfg.host == sample_server.host and cfg.port == 2053 and cfg.api_token == "tok-abc"


def test_pro_panel_uses_its_domain_as_host(client, admin_user, auth_headers, sample_server, session):
    xs = _panel(session, sample_server, mode="xui-pro", domain="panel.example.test")
    cfg = parse_xui_uri(client.get(f"/api/xui/servers/{xs.id}/uri", headers=auth_headers).json()["uri"])
    assert cfg.host == "panel.example.test" and cfg.mode == "xui-pro"


def test_panel_without_a_token_is_refused(client, admin_user, auth_headers, sample_server, session):
    xs = _panel(session, sample_server, api_token="")
    assert client.get(f"/api/xui/servers/{xs.id}/uri", headers=auth_headers).status_code == 409


def test_requires_login(client, sample_server, session):
    xs = _panel(session, sample_server)
    assert client.get(f"/api/xui/servers/{xs.id}/uri").status_code in (401, 403)
