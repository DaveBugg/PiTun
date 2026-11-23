"""GET /api/servers/{id}/chain-presence — pre-reinstall chain footprint."""
from unittest.mock import patch

from app.models import ProxyChain, Server, XuiServer


def _seed_server(session, with_panel=True):
    srv = Server(name="vps", host="1.2.3.4", port=22, user="root", auth_type="key")
    session.add(srv)
    session.commit()
    session.refresh(srv)
    xs = None
    if with_panel:
        xs = XuiServer(
            server_id=srv.id, api_token="tok", panel_user="u",
            panel_pass="p", panel_port=12345, panel_basepath="/t", mode="bare",
        )
        session.add(xs)
        session.commit()
        session.refresh(xs)
    return srv, xs


class _FakePanelClient:
    def __init__(self, inbounds):
        self._inbounds = inbounds

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def list_inbounds(self):
        return self._inbounds


def test_presence_counts_local_and_marks(client, session, admin_user, auth_headers):
    srv, xs = _seed_server(session)
    session.add(ProxyChain(
        name="c1", relay_xui_server_id=xs.id, exit_xui_server_id=xs.id,
        status="deployed",
    ))
    session.commit()

    inbounds = [
        {"remark": "VPN-A [pitun:999888777666]", "tag": "chain-9-a-relay"},
        {"remark": "old chain row", "tag": "chain-3-x-exit"},
        {"remark": "plain inbound", "tag": "in-8443-tcp"},
    ]
    with patch(
        "app.core.xui_api.XuiClient",
        return_value=_FakePanelClient(inbounds),
    ):
        resp = client.get(f"/api/servers/{srv.id}/chain-presence", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["local_chains"] == 1
    # The scan runs before any install_id exists in this test DB, so the
    # one marked row is foreign; the unmarked chain-* row is legacy.
    assert data["foreign_marked_inbounds"] == 1
    assert data["own_marked_inbounds"] == 0
    assert data["legacy_chain_inbounds"] == 1
    assert data["error"] is None


def test_presence_without_panel_is_db_only(client, session, admin_user, auth_headers):
    srv, _ = _seed_server(session, with_panel=False)
    resp = client.get(f"/api/servers/{srv.id}/chain-presence", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["local_chains"] == 0
    assert data["foreign_marked_inbounds"] is None


def test_presence_panel_error_degrades(client, session, admin_user, auth_headers):
    srv, _ = _seed_server(session)
    with patch("app.core.xui_api.XuiClient", side_effect=RuntimeError("boom")):
        resp = client.get(f"/api/servers/{srv.id}/chain-presence", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["error"]


def test_presence_404(client, admin_user, auth_headers):
    resp = client.get("/api/servers/424242/chain-presence", headers=auth_headers)
    assert resp.status_code == 404
