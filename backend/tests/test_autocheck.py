"""Auto-speedtest sweep: scope resolution, sweep behaviour, config API.

`speedtest_node` is always mocked — no real xray / network. Nodes + config
are seeded via the sync `session` fixture; the scheduler uses its own async
sessions against the same test DB (same pattern as test_nodecircle)."""
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession as ASession

from app.database import get_async_engine
from app.models import Node, AutoCheckConfig
from app.core import autocheck_scheduler as acs


def _mk_node(session, **ov):
    d = dict(name="N", protocol="vless", address="1.2.3.4", port=443,
             uuid="u", transport="tcp", tls="tls", enabled=True, is_online=True, order=0)
    d.update(ov)
    n = Node(**d)
    session.add(n)
    session.commit()
    session.refresh(n)
    return n


def _cfg(session, **ov):
    d = dict(id=1, enabled=True, interval_minutes=60, scope_kind="all", scope_value="")
    d.update(ov)
    c = AutoCheckConfig(**d)
    session.add(c)
    session.commit()
    return c


class TestScopeResolution:
    @pytest.mark.asyncio
    async def test_scopes(self, client, admin_user, session):
        a = _mk_node(session, name="a", address="1.1.1.1", subscription_id=7, group="eu")
        b = _mk_node(session, name="b", address="2.2.2.2", subscription_id=7, group="us")
        _mk_node(session, name="c", address="3.3.3.3", group="eu", enabled=False)  # disabled
        async with ASession(get_async_engine()) as s:
            all_ids = set(await acs.resolve_scope_node_ids(s, "all", ""))
            sub_ids = set(await acs.resolve_scope_node_ids(s, "subscription", "7"))
            grp_ids = set(await acs.resolve_scope_node_ids(s, "group", "eu"))
            node_ids = set(await acs.resolve_scope_node_ids(s, "nodes", json.dumps([a.id, b.id])))
            bad_sub = await acs.resolve_scope_node_ids(s, "subscription", "notanint")
            empty_nodes = await acs.resolve_scope_node_ids(s, "nodes", "[]")
        assert all_ids == {a.id, b.id}   # disabled c excluded
        assert sub_ids == {a.id, b.id}
        assert grp_ids == {a.id}         # only enabled eu member
        assert node_ids == {a.id, b.id}
        assert bad_sub == []
        assert empty_nodes == []


class TestRunSweep:
    @pytest.mark.asyncio
    async def test_tests_and_persists(self, client, admin_user, session):
        n1 = _mk_node(session, name="n1", address="10.0.0.1")
        n2 = _mk_node(session, name="n2", address="10.0.0.2")
        _cfg(session, scope_kind="all")

        async def fake_speed(node):
            return {"download_mbps": 50.0 + node.id, "error": None}

        with patch("app.core.speedtest.speedtest_node", side_effect=fake_speed):
            summary = await acs.autocheck_scheduler.run_sweep()

        assert summary["tested"] == 2
        session.expire_all()
        assert session.get(Node, n1.id).speed_mbps is not None
        assert session.get(Node, n2.id).speed_tested_at is not None
        assert session.get(AutoCheckConfig, 1).last_sweep is not None

    @pytest.mark.asyncio
    async def test_staleness_guard_skips_fresh(self, client, admin_user, session):
        fresh = _mk_node(session, name="fresh", address="10.0.0.1",
                         speed_mbps=99.0, speed_tested_at=datetime.now(timezone.utc))
        stale = _mk_node(session, name="stale", address="10.0.0.2",
                         speed_mbps=1.0,
                         speed_tested_at=datetime.now(timezone.utc) - timedelta(hours=3))
        _cfg(session, interval_minutes=60, scope_kind="all")

        tested_ids = []
        async def fake_speed(node):
            tested_ids.append(node.id)
            return {"download_mbps": 42.0}

        with patch("app.core.speedtest.speedtest_node", side_effect=fake_speed):
            summary = await acs.autocheck_scheduler.run_sweep()

        assert fresh.id not in tested_ids   # measured within the interval
        assert stale.id in tested_ids
        assert summary["skipped"] == 1
        assert summary["tested"] == 1

    @pytest.mark.asyncio
    async def test_isolates_node_errors(self, client, admin_user, session):
        bad = _mk_node(session, name="bad", address="10.0.0.1")
        good = _mk_node(session, name="good", address="10.0.0.2")
        _cfg(session, scope_kind="all")

        async def fake_speed(node):
            if node.id == bad.id:
                raise RuntimeError("boom")
            return {"download_mbps": 42.0}

        with patch("app.core.speedtest.speedtest_node", side_effect=fake_speed):
            summary = await acs.autocheck_scheduler.run_sweep()

        assert summary["failed"] == 1     # bad node isolated
        assert summary["tested"] == 1
        session.expire_all()
        assert session.get(Node, good.id).speed_mbps == 42.0
        # A failed check is stamped (time set, reading cleared) so the UI
        # can show a "no speed" badge instead of a blank row.
        bad_row = session.get(Node, bad.id)
        assert bad_row.speed_tested_at is not None
        assert bad_row.speed_mbps is None


class TestAutoCheckAPI:
    def test_get_default(self, client, admin_user, auth_headers):
        r = client.get("/api/autocheck", headers=auth_headers)
        assert r.status_code == 200
        d = r.json()
        assert d["enabled"] is False
        assert d["scope_kind"] == "all"
        assert "is_sweeping" in d

    def test_put_updates_and_persists(self, client, admin_user, auth_headers):
        r = client.put(
            "/api/autocheck",
            json={"enabled": True, "interval_minutes": 120, "scope_kind": "group", "scope_value": "eu"},
            headers=auth_headers,
        )
        assert r.status_code == 200
        assert r.json()["enabled"] is True
        assert r.json()["interval_minutes"] == 120
        r2 = client.get("/api/autocheck", headers=auth_headers)
        assert r2.json()["scope_kind"] == "group"
        assert r2.json()["scope_value"] == "eu"

    def test_put_rejects_bad_scope(self, client, admin_user, auth_headers):
        r = client.put("/api/autocheck", json={"scope_kind": "bogus"}, headers=auth_headers)
        assert r.status_code == 422

    def test_put_rejects_bad_interval(self, client, admin_user, auth_headers):
        r = client.put("/api/autocheck", json={"interval_minutes": 0}, headers=auth_headers)
        assert r.status_code == 422

    def test_run_now(self, client, admin_user, auth_headers):
        # Mock the sweep itself — the endpoint schedules it as a task and
        # returns immediately; we only assert it was accepted.
        with patch.object(acs.autocheck_scheduler, "run_sweep", new_callable=AsyncMock):
            r = client.post("/api/autocheck/run", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "started"
