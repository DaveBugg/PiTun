"""/api/endpoints — extra SOCKS5 proxy flows.

The endpoint manager is mocked out: process lifecycle is covered
separately, here we pin the API surface (validation, uniqueness,
limit, guards on active-node / node-delete).
"""
from unittest import mock
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from app.models import BalancerGroup, Node, ProxyEndpoint, Settings


def _node(session, name, port=443, enabled=True):
    n = Node(
        name=name, protocol="vless", address=f"192.0.2.{port % 250}",
        port=port, uuid=f"uuid-{name}", transport="tcp", tls="reality",
        enabled=enabled,
    )
    session.add(n)
    session.commit()
    session.refresh(n)
    return n


@pytest.fixture(autouse=True)
def _no_manager():
    with patch(
        "app.api.endpoints.endpoint_manager.apply", new=AsyncMock()
    ), patch(
        "app.api.endpoints.endpoint_manager.stop", new=AsyncMock()
    ):
        yield


def _create(client, auth_headers, **kw):
    body = {"name": "ep1", "mode": "global"}
    body.update(kw)
    return client.post("/api/endpoints", json=body, headers=auth_headers)


def test_create_autoallocates_1081_and_applies(client, session, admin_user, auth_headers):
    n = _node(session, "n1")
    resp = _create(client, auth_headers, node_id=n.id)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["socks_port"] == 1081
    assert data["node_name"] == "n1"
    assert data["enabled"] is True


def test_second_endpoint_gets_1082(client, session, admin_user, auth_headers):
    n1, n2 = _node(session, "n1"), _node(session, "n2", port=444)
    assert _create(client, auth_headers, name="a", node_id=n1.id).status_code == 201
    resp = _create(client, auth_headers, name="b", node_id=n2.id)
    assert resp.status_code == 201
    assert resp.json()["socks_port"] == 1082


def test_node_cannot_serve_two_flows(client, session, admin_user, auth_headers):
    n1, n2 = _node(session, "n1"), _node(session, "n2", port=444)
    assert _create(client, auth_headers, name="a", node_id=n1.id).status_code == 201
    resp = _create(client, auth_headers, name="b", node_id=n1.id)
    assert resp.status_code == 400
    assert "already assigned" in resp.json()["detail"]
    # …nor become the main flow's active node.
    resp = client.post(
        "/api/system/active-node", json={"node_id": n1.id}, headers=auth_headers,
    )
    assert resp.status_code == 400
    assert "endpoint" in resp.json()["detail"]


def test_active_node_cannot_be_assigned_to_an_endpoint(client, session, admin_user, auth_headers):
    n = _node(session, "main-one")
    session.add(Settings(key="active_node_id", value=str(n.id)))
    session.commit()
    resp = _create(client, auth_headers, node_id=n.id)
    assert resp.status_code == 400
    assert "main flow" in resp.json()["detail"]


def test_balancer_is_exempt_from_uniqueness(client, session, admin_user, auth_headers):
    n1, n2 = _node(session, "n1"), _node(session, "n2", port=444)
    bg = BalancerGroup(name="bg", enabled=True, node_ids=f"[{n1.id}, {n2.id}]")
    session.add(bg)
    session.commit()
    session.refresh(bg)
    assert _create(client, auth_headers, name="b1", balancer_id=bg.id).status_code == 201
    # Same balancer on a second endpoint is fine — exempt by design.
    assert _create(client, auth_headers, name="b2", balancer_id=bg.id).status_code == 201


def test_exactly_one_egress_required(client, session, admin_user, auth_headers):
    assert _create(client, auth_headers).status_code == 400  # neither
    n = _node(session, "n1")
    bg = BalancerGroup(name="bg", enabled=True, node_ids="[]")
    session.add(bg)
    session.commit()
    session.refresh(bg)
    resp = _create(client, auth_headers, node_id=n.id, balancer_id=bg.id)
    assert resp.status_code == 400  # both


def test_port_conflict_and_reserved(client, session, admin_user, auth_headers):
    n1, n2 = _node(session, "n1"), _node(session, "n2", port=444)
    assert _create(client, auth_headers, name="a", node_id=n1.id, socks_port=1500).status_code == 201
    assert _create(client, auth_headers, name="b", node_id=n2.id, socks_port=1500).status_code == 400
    assert _create(client, auth_headers, name="c", node_id=n2.id, socks_port=1080).status_code == 400
    assert _create(client, auth_headers, name="d", node_id=n2.id, socks_port=65501).status_code == 400


def test_limit_is_nine(client, session, admin_user, auth_headers):
    n = _node(session, "n1")
    bg = BalancerGroup(name="bg", enabled=True, node_ids=f"[{n.id}]")
    session.add(bg)
    session.commit()
    session.refresh(bg)
    for i in range(9):
        resp = _create(client, auth_headers, name=f"ep{i}", balancer_id=bg.id)
        assert resp.status_code == 201, resp.text
    resp = _create(client, auth_headers, name="ep9", balancer_id=bg.id)
    assert resp.status_code == 400
    assert "limit" in resp.json()["detail"]


def test_routing_set_mode_needs_a_set(client, session, admin_user, auth_headers):
    n = _node(session, "n1")
    resp = _create(client, auth_headers, node_id=n.id, mode="routing_set")
    assert resp.status_code == 400


def test_delete_guard_on_assigned_node(client, session, admin_user, auth_headers):
    n = _node(session, "n1")
    assert _create(client, auth_headers, node_id=n.id).status_code == 201
    resp = client.delete(f"/api/nodes/{n.id}", headers=auth_headers)
    assert resp.status_code == 400
    assert "endpoint" in resp.json()["detail"]


def test_toggle_disable_and_delete(client, session, admin_user, auth_headers):
    n = _node(session, "n1")
    ep = _create(client, auth_headers, node_id=n.id).json()
    resp = client.patch(
        f"/api/endpoints/{ep['id']}", json={"enabled": False}, headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False
    resp = client.delete(f"/api/endpoints/{ep['id']}", headers=auth_headers)
    assert resp.status_code == 204
    assert session.get(ProxyEndpoint, ep["id"]) is None


def test_update_reassigns_node_and_frees_the_old_one(client, session, admin_user, auth_headers):
    n1, n2, n3 = _node(session, "n1"), _node(session, "n2", port=444), _node(session, "n3", port=445)
    ep = _create(client, auth_headers, node_id=n1.id).json()
    resp = client.patch(
        f"/api/endpoints/{ep['id']}", json={"node_id": n2.id}, headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["node_id"] == n2.id
    # n1 is free again — a new endpoint may take it.
    assert _create(client, auth_headers, name="b", node_id=n1.id).status_code == 201
    # n2 is now taken.
    assert _create(client, auth_headers, name="c", node_id=n2.id).status_code == 400


def test_create_rolls_back_the_row_when_apply_fails(client, session, admin_user, auth_headers):
    n = _node(session, "n1")
    with patch(
        "app.api.endpoints.endpoint_manager.apply",
        new=AsyncMock(side_effect=RuntimeError("xray refused the config")),
    ):
        resp = _create(client, auth_headers, node_id=n.id)
    assert resp.status_code == 500
    assert "xray refused" in resp.json()["detail"]
    assert list(session.exec(select(ProxyEndpoint)).all()) == []


def test_write_config_tmp_keeps_json_extension(tmp_path, monkeypatch):
    """xray picks the config format from the file extension — the temp
    file must end in .json or `xray run -test` rejects it outright."""
    import asyncio
    from app.core import endpoint_manager as em

    monkeypatch.setattr(
        em.settings, "database_url", f"sqlite:///{tmp_path}/pitun.db",
    )
    seen = {}

    class FakeProc:
        returncode = 1

        async def communicate(self):
            return b"boom", b""

    async def fake_exec(*argv, **kwargs):
        seen["config"] = argv[-1]
        return FakeProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    with pytest.raises(ValueError):
        asyncio.run(em.write_endpoint_config(7, {"inbounds": []}))
    assert seen["config"].endswith(".json")
    # The rejected temp file is cleaned up.
    assert list((tmp_path / "endpoints").iterdir()) == []


def test_apply_disable_does_not_deadlock():
    """apply() holds the manager lock; the disable branch used to call
    the PUBLIC stop() which re-acquires it — a self-deadlock that hung
    PATCH {enabled: false} forever (found live on the test box)."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.core.endpoint_manager import EndpointManager, EndpointProcess

    mgr = EndpointManager()
    ep = SimpleNamespace(
        id=1, name="ep", enabled=False, node_id=1, balancer_id=None,
        mode="global", routing_set_id=None, socks_port=1081,
    )
    proc = EndpointProcess(1, "ep")
    proc.stop = AsyncMock()
    mgr._procs[1] = proc

    async def run():
        await asyncio.wait_for(mgr.apply(None, ep), timeout=5)  # type: ignore[arg-type]

    asyncio.run(run())
    proc.stop.assert_awaited_once()
    assert 1 not in mgr._procs


def test_apply_rename_preserves_existing_process():
    """Renaming an endpoint must update proc.name in place. The existing
    OS process stays bound to the same EndpointProcess object and is
    restarted through the normal restart path; it is never orphaned and
    never replaced by a fresh EndpointProcess object."""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, patch

    from app.core.endpoint_manager import EndpointManager, EndpointProcess

    mgr = EndpointManager()
    ep = SimpleNamespace(
        id=1, name="new-name", enabled=True, node_id=1, balancer_id=None,
        mode="global", routing_set_id=None, socks_port=1081,
    )
    proc = EndpointProcess(1, "old-name")
    proc.restart = AsyncMock()
    proc.start = AsyncMock()
    proc._process = SimpleNamespace(returncode=None, pid=12345)
    mgr._procs[1] = proc

    async def run():
        with patch(
            "app.core.endpoint_manager.build_endpoint_config",
            new=AsyncMock(return_value={"inbounds": []}),
        ), patch(
            "app.core.endpoint_manager.write_endpoint_config",
            new=AsyncMock(),
        ):
            await asyncio.wait_for(mgr.apply(None, ep), timeout=5)  # type: ignore[arg-type]

    asyncio.run(run())
    assert mgr._procs[1] is proc
    assert proc.name == "new-name"
    proc.restart.assert_awaited_once()
    proc.start.assert_not_awaited()



class TestDeleteActiveNodeHealExcludesEndpoints:
    """The active-node heal must not pick a survivor that is already
    assigned to an extra endpoint — that would violate the one-node-per-flow
    invariant enforced by set_active_node."""

    def _mk_ep_node(self, session, name, port, ep_name, socks_port):
        n = _node(session, name, port=port)
        session.add(ProxyEndpoint(name=ep_name, node_id=n.id, socks_port=socks_port, mode="global"))
        session.commit()
        return n

    def _active_value(self, session):
        session.expire_all()
        row = session.exec(
            select(Settings).where(Settings.key == "active_node_id")
        ).first()
        return row.value if row else None

    def test_heal_skips_assigned_first_survivor(self, client, session, admin_user, auth_headers):
        active = _node(session, "active")
        assigned = self._mk_ep_node(session, "assigned", 444, "ep1", 1081)
        free = _node(session, "free", port=445)
        session.add(Settings(key="active_node_id", value=str(active.id)))
        session.commit()

        with patch(
            "app.core.xray.XrayManager.is_running",
            new_callable=mock.PropertyMock, return_value=False,
        ):
            resp = client.delete(f"/api/nodes/{active.id}", headers=auth_headers)

        assert resp.status_code == 204
        assert self._active_value(session) == str(free.id)

    def test_heal_clears_when_all_survivors_assigned(
        self, client, session, admin_user, auth_headers,
    ):
        active = _node(session, "active")
        self._mk_ep_node(session, "assigned-1", 444, "ep1", 1081)
        self._mk_ep_node(session, "assigned-2", 445, "ep2", 1082)
        session.add(Settings(key="active_node_id", value=str(active.id)))
        session.commit()

        with patch(
            "app.core.xray.XrayManager.is_running",
            new_callable=mock.PropertyMock, return_value=False,
        ):
            resp = client.delete(f"/api/nodes/{active.id}", headers=auth_headers)

        assert resp.status_code == 204
        assert self._active_value(session) == ""


class TestBootActiveNodeHealExcludesEndpoints:
    """The boot-time heal uses the same one-node-per-flow exclusion as the
    delete-time heal."""

    @pytest.mark.asyncio
    async def test_boot_heal_skips_assigned_survivor(self, engine):
        sync_engine, async_engine = engine
        from sqlmodel import Session
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.main import _heal_active_node_on_boot

        with Session(sync_engine) as s:
            assigned = Node(
                name="assigned", protocol="vless", address="192.0.2.10", port=443,
                uuid="u", transport="tcp", tls="none", enabled=True,
            )
            free = Node(
                name="free", protocol="vless", address="192.0.2.11", port=444,
                uuid="u", transport="tcp", tls="none", enabled=True,
            )
            s.add(assigned)
            s.add(free)
            s.commit()
            s.refresh(assigned)
            s.refresh(free)
            free_id = free.id
            s.add(ProxyEndpoint(name="ep1", node_id=assigned.id, socks_port=1081, mode="global"))
            s.add(Settings(key="active_node_id", value="9999"))
            s.commit()

        async with AsyncSession(async_engine) as s:
            await _heal_active_node_on_boot(s)

        with Session(sync_engine) as s:
            row = s.exec(select(Settings).where(Settings.key == "active_node_id")).first()
            assert row.value == str(free_id)

    @pytest.mark.asyncio
    async def test_boot_heal_clears_when_all_survivors_assigned(self, engine):
        sync_engine, async_engine = engine
        from sqlmodel import Session
        from sqlmodel.ext.asyncio.session import AsyncSession
        from app.main import _heal_active_node_on_boot

        with Session(sync_engine) as s:
            a1 = Node(
                name="a1", protocol="vless", address="192.0.2.10", port=443,
                uuid="u", transport="tcp", tls="none", enabled=True,
            )
            a2 = Node(
                name="a2", protocol="vless", address="192.0.2.11", port=444,
                uuid="u", transport="tcp", tls="none", enabled=True,
            )
            s.add(a1)
            s.add(a2)
            s.commit()
            s.refresh(a1)
            s.refresh(a2)
            s.add(ProxyEndpoint(name="ep1", node_id=a1.id, socks_port=1081, mode="global"))
            s.add(ProxyEndpoint(name="ep2", node_id=a2.id, socks_port=1082, mode="global"))
            s.add(Settings(key="active_node_id", value="9999"))
            s.commit()

        async with AsyncSession(async_engine) as s:
            await _heal_active_node_on_boot(s)

        with Session(sync_engine) as s:
            row = s.exec(select(Settings).where(Settings.key == "active_node_id")).first()
            assert row.value == ""
