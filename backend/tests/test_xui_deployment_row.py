"""A server whose panel was registered (xui:// address, servers import) must read
as "x-ui configured" like one PiTun installed itself — that row is what shows the
badge and offers the uninstall."""
import pytest
from sqlmodel import select

from app.models import Server, ServerDeployment, XuiServer


@pytest.fixture(name="sample_server")
def sample_server_fixture(session):
    s = Server(name="My VPS", host="vps1.example.com", port=22, user="root",
               auth_type="password", password="s3cret")
    session.add(s)
    session.commit()
    session.refresh(s)
    return s


def _xui_rows(session, server_id):
    session.expire_all()
    return session.exec(
        select(ServerDeployment)
        .where(ServerDeployment.server_id == server_id)
        .where(ServerDeployment.protocol == "xui")
    ).all()


def _bundle(sample):
    return {
        "kind": "pitun-servers-export", "version": 3, "include_secrets": True,
        "servers": [{
            "name": sample.name, "host": sample.host, "port": sample.port,
            "user": sample.user, "auth_type": "password", "password": "pw",
            "panel": {"api_token": "tok", "panel_user": "a", "panel_pass": "p", "panel_port": 2053, "panel_basepath": "/p", "mode": "bare"},
        }],
    }


def test_importing_a_server_with_its_panel_marks_the_server_configured(
    client, admin_user, auth_headers, sample_server, session,
):
    assert _xui_rows(session, sample_server.id) == []
    r = client.post("/api/servers/import-json", json=_bundle(sample_server), headers=auth_headers)
    assert r.status_code == 200 and r.json()["panels_restored"] == 1
    rows = _xui_rows(session, sample_server.id)
    assert len(rows) == 1 and rows[0].status == "deployed"
    listed = client.get(f"/api/servers/{sample_server.id}/deployments", headers=auth_headers).json()
    assert [d["protocol"] for d in listed] == ["xui"]


def test_reimporting_repairs_a_missing_mark_and_never_doubles_it(
    client, admin_user, auth_headers, sample_server, session,
):
    client.post("/api/servers/import-json", json=_bundle(sample_server), headers=auth_headers)
    for row in _xui_rows(session, sample_server.id):
        session.delete(row)
    session.commit()
    assert _xui_rows(session, sample_server.id) == []
    client.post("/api/servers/import-json", json=_bundle(sample_server), headers=auth_headers)
    client.post("/api/servers/import-json", json=_bundle(sample_server), headers=auth_headers)
    assert len(_xui_rows(session, sample_server.id)) == 1


def test_unregistering_the_panel_removes_the_badge(
    client, admin_user, auth_headers, sample_server, session,
):
    client.post("/api/servers/import-json", json=_bundle(sample_server), headers=auth_headers)
    xs = session.exec(select(XuiServer).where(XuiServer.server_id == sample_server.id)).first()
    assert xs is not None
    assert client.delete(f"/api/xui/servers/{xs.id}", headers=auth_headers).status_code in (200, 204)
    assert _xui_rows(session, sample_server.id) == []
