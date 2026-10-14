"""`xui_panel_bypass`: panel hosts join nftables bypass_dst4 only when the
single global setting is on (default off)."""
import asyncio
import socket as _sock

from sqlmodel import Session
from sqlmodel.ext.asyncio.session import AsyncSession


def _seed(engine, n_panels):
    from app.models import Server, XuiServer
    sync_engine, async_engine = engine
    with Session(sync_engine) as s:
        for i in range(1, n_panels + 1):
            s.add(Server(id=i, name=f"vps-{i}", host=f"panel-{i}.example.test", ssh_user="root"))
        s.commit()
        for i in range(1, n_panels + 1):
            s.add(XuiServer(server_id=i, api_token="t", panel_user="admin", panel_pass="pw", panel_port=2053, panel_basepath="/", mode="bare"))
        s.commit()
    return async_engine


def _fake_dns(monkeypatch):
    table = {"panel-1.example.test": "203.0.113.11", "panel-2.example.test": "203.0.113.12"}

    def fake_getaddrinfo(host, *a, **kw):
        if host in table:
            return [(_sock.AF_INET, None, None, None, (table[host], 0))]
        raise _sock.gaierror()

    monkeypatch.setattr(_sock, "getaddrinfo", fake_getaddrinfo)


def _collect(async_engine, settings_map):
    from app.api.system import _collect_panel_bypass_dsts

    async def _run():
        async with AsyncSession(async_engine) as s:
            return await _collect_panel_bypass_dsts(s, settings_map)

    return asyncio.run(_run())


class TestPanelBypass:
    def test_off_by_default_adds_nothing(self, engine, monkeypatch):
        _fake_dns(monkeypatch)
        async_engine = _seed(engine, 2)
        assert _collect(async_engine, {}) == []
        assert _collect(async_engine, {"xui_panel_bypass": "false"}) == []

    def test_on_covers_every_registered_panel(self, engine, monkeypatch):
        _fake_dns(monkeypatch)
        async_engine = _seed(engine, 2)
        got = _collect(async_engine, {"xui_panel_bypass": "true"})
        assert got == ["203.0.113.11/32", "203.0.113.12/32"]

    def test_unresolvable_panel_is_skipped(self, engine, monkeypatch):
        monkeypatch.setattr(_sock, "getaddrinfo",
                            lambda *a, **kw: (_ for _ in ()).throw(_sock.gaierror()))
        async_engine = _seed(engine, 1)
        assert _collect(async_engine, {"xui_panel_bypass": "true"}) == []

    def test_setting_roundtrips_through_the_api(self, client, admin_user, auth_headers):
        r = client.get("/api/system/settings", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["xui_panel_bypass"] is False
        r = client.patch("/api/system/settings", json={"xui_panel_bypass": True}, headers=auth_headers)
        assert r.status_code in (200, 204), r.text
        r = client.get("/api/system/settings", headers=auth_headers)
        assert r.json()["xui_panel_bypass"] is True
