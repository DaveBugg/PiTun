"""Tests for NodeCircle CRUD and validation."""
import json
import pytest


class TestNodeCircleList:
    def test_list_empty(self, client, admin_user, auth_headers):
        resp = client.get("/api/nodecircle", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_with_data(self, client, admin_user, auth_headers, sample_circle):
        resp = client.get("/api/nodecircle", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["name"] == "Test Circle"


class TestNodeCircleCreate:
    def test_create(self, client, admin_user, auth_headers, sample_node):
        resp = client.post(
            "/api/nodecircle",
            json={
                "name": "New Circle",
                "node_ids": [sample_node.id],
                "mode": "sequential",
                "interval_min": 10,
                "interval_max": 30,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "New Circle"
        assert data["node_ids"] == [sample_node.id]
        assert data["mode"] == "sequential"

    def test_create_random_mode(self, client, admin_user, auth_headers, sample_node):
        resp = client.post(
            "/api/nodecircle",
            json={"name": "Random Circle", "node_ids": [sample_node.id], "mode": "random"},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["mode"] == "random"

    def test_create_invalid_mode(self, client, admin_user, auth_headers, sample_node):
        resp = client.post(
            "/api/nodecircle",
            json={"name": "Bad", "node_ids": [sample_node.id], "mode": "invalid"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_create_interval_min_too_low(self, client, admin_user, auth_headers, sample_node):
        resp = client.post(
            "/api/nodecircle",
            json={
                "name": "Bad Interval",
                "node_ids": [sample_node.id],
                "interval_min": 0,
                "interval_max": 10,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_create_interval_max_less_than_min(self, client, admin_user, auth_headers, sample_node):
        resp = client.post(
            "/api/nodecircle",
            json={
                "name": "Reversed",
                "node_ids": [sample_node.id],
                "interval_min": 20,
                "interval_max": 5,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestNodeCircleGet:
    def test_get(self, client, admin_user, auth_headers, sample_circle):
        resp = client.get(f"/api/nodecircle/{sample_circle.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Test Circle"

    def test_get_not_found(self, client, admin_user, auth_headers):
        resp = client.get("/api/nodecircle/9999", headers=auth_headers)
        assert resp.status_code == 404


class TestNodeCircleUpdate:
    def test_update_name(self, client, admin_user, auth_headers, sample_circle):
        resp = client.patch(
            f"/api/nodecircle/{sample_circle.id}",
            json={"name": "Renamed"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed"

    def test_update_mode(self, client, admin_user, auth_headers, sample_circle):
        resp = client.patch(
            f"/api/nodecircle/{sample_circle.id}",
            json={"mode": "random"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["mode"] == "random"

    def test_update_invalid_mode(self, client, admin_user, auth_headers, sample_circle):
        resp = client.patch(
            f"/api/nodecircle/{sample_circle.id}",
            json={"mode": "broken"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_update_not_found(self, client, admin_user, auth_headers):
        resp = client.patch("/api/nodecircle/9999", json={"name": "x"}, headers=auth_headers)
        assert resp.status_code == 404


class TestNodeCircleDelete:
    def test_delete(self, client, admin_user, auth_headers, sample_circle):
        resp = client.delete(f"/api/nodecircle/{sample_circle.id}", headers=auth_headers)
        assert resp.status_code == 204

        resp2 = client.get(f"/api/nodecircle/{sample_circle.id}", headers=auth_headers)
        assert resp2.status_code == 404

    def test_delete_not_found(self, client, admin_user, auth_headers):
        resp = client.delete("/api/nodecircle/9999", headers=auth_headers)
        assert resp.status_code == 404


# ── Rotation with pre-ping ──────────────────────────────────────────────────
#
# Each rotation now probes candidates before switching, taking the
# first alive node. These tests stub out the probe + the seamless-rotate
# helpers so we can verify the SELECTION logic without touching xray
# or real network. Side-effect mocks: `_probe_node` returns a result
# dict (we drive the test by varying it), `_seamless_rotate` is a
# no-op.

import asyncio
from unittest.mock import AsyncMock, patch


def _mk_node(session, **overrides):
    """Make a Node row directly in the DB for circle rotation tests."""
    from app.models import Node
    defaults = dict(
        name="N", protocol="vless", address="1.2.3.4", port=443,
        uuid="abc", transport="tcp", tls="tls",
        enabled=True, is_online=True, order=0,
    )
    defaults.update(overrides)
    n = Node(**defaults)
    session.add(n)
    session.commit()
    session.refresh(n)
    return n


def _mk_circle(session, node_ids: list[int], mode: str = "sequential", current_index: int = 0):
    from app.models import NodeCircle
    c = NodeCircle(
        name="Rotate Test", enabled=True,
        node_ids=json.dumps(node_ids),
        mode=mode, interval_min=5, interval_max=15,
        current_index=current_index,
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


class TestRotatePrePing:
    @pytest.mark.asyncio
    async def test_skips_offline_then_picks_online(self, client, admin_user, auth_headers, session):
        """Rotation probes candidates in order; if first is offline, rolls to next."""
        n0 = _mk_node(session, name="N0", address="10.0.0.0")
        n1 = _mk_node(session, name="N1-dead", address="10.0.0.1")
        n2 = _mk_node(session, name="N2-alive", address="10.0.0.2")
        circle = _mk_circle(session, [n0.id, n1.id, n2.id], mode="sequential", current_index=0)

        # Probe results: first candidate (N1) offline, second (N2) online.
        # Sequential rotation from index 0 tries idx 1, then 2.
        probe_results = {
            "10.0.0.1": {"is_online": False, "error": "timeout"},
            "10.0.0.2": {"is_online": True, "latency_ms": 42},
        }

        async def fake_probe(addr, port, udp, **kw):
            return probe_results.get(addr, {"is_online": False, "error": "unknown"})

        with (
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=fake_probe),
            patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock),
        ):
            from app.core.circle_scheduler import circle_scheduler
            await circle_scheduler.rotate_circle(circle.id)

        # Verify: circle.current_index points at N2 (index 2), active_node_id = n2.id
        from app.models import NodeCircle, Settings as DBSettings
        session.expire_all()
        c = session.get(NodeCircle, circle.id)
        assert c.current_index == 2
        active = session.exec(  # type: ignore[attr-defined]
            __import__("sqlmodel").select(DBSettings).where(DBSettings.key == "active_node_id")
        ).first()
        assert active is not None
        assert active.value == str(n2.id)

    @pytest.mark.asyncio
    async def test_all_offline_no_rotation(self, client, admin_user, auth_headers, session):
        """If every candidate fails the probe, current_index stays put."""
        n0 = _mk_node(session, name="N0", address="10.0.0.0")
        n1 = _mk_node(session, name="N1", address="10.0.0.1")
        n2 = _mk_node(session, name="N2", address="10.0.0.2")
        circle = _mk_circle(session, [n0.id, n1.id, n2.id], current_index=0)

        async def all_dead(addr, port, udp, **kw):
            return {"is_online": False, "error": "timeout"}

        with (
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=all_dead),
            patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock) as seam,
        ):
            from app.core.circle_scheduler import circle_scheduler
            await circle_scheduler.rotate_circle(circle.id)

        # current_index unchanged, _seamless_rotate NOT called
        from app.models import NodeCircle
        session.expire_all()
        c = session.get(NodeCircle, circle.id)
        assert c.current_index == 0
        assert seam.await_count == 0

    @pytest.mark.asyncio
    async def test_disabled_node_skipped(self, client, admin_user, auth_headers, session):
        """Disabled nodes are skipped before probing — don't waste a probe on them."""
        n0 = _mk_node(session, name="N0", address="10.0.0.0")
        n1 = _mk_node(session, name="N1-disabled", address="10.0.0.1", enabled=False)
        n2 = _mk_node(session, name="N2", address="10.0.0.2")
        circle = _mk_circle(session, [n0.id, n1.id, n2.id], current_index=0)

        probed_addrs: list[str] = []

        async def track(addr, port, udp, **kw):
            probed_addrs.append(addr)
            return {"is_online": True, "latency_ms": 10}

        with (
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=track),
            patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock),
        ):
            from app.core.circle_scheduler import circle_scheduler
            await circle_scheduler.rotate_circle(circle.id)

        # N1 disabled → only N2 should have been probed
        assert "10.0.0.1" not in probed_addrs
        assert "10.0.0.2" in probed_addrs

    @pytest.mark.asyncio
    async def test_retry_picks_up_after_first_attempt_fails(self, client, admin_user, auth_headers, session):
        """A probe that fails once but succeeds on retry should not be
        rejected — this catches transient SYN drops / brief packet loss."""
        n0 = _mk_node(session, name="N0", address="10.0.0.0")
        n1 = _mk_node(session, name="N1-flaky", address="10.0.0.1")
        circle = _mk_circle(session, [n0.id, n1.id], current_index=0)

        # Counter so the same address answers differently on
        # successive calls — first attempt fails, second succeeds.
        call_count = {"10.0.0.1": 0}

        async def flaky(addr, port, udp, **kw):
            call_count[addr] = call_count.get(addr, 0) + 1
            if addr == "10.0.0.1" and call_count[addr] == 1:
                return {"is_online": False, "error": "transient timeout"}
            return {"is_online": True, "latency_ms": 25}

        with (
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=flaky),
            patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock),
        ):
            from app.core.circle_scheduler import circle_scheduler
            await circle_scheduler.rotate_circle(circle.id)

        # Rotation happened despite first probe failing — retry caught it.
        from app.models import NodeCircle
        session.expire_all()
        c = session.get(NodeCircle, circle.id)
        assert c.current_index == 1
        # Probe was called twice for n1 (one retry).
        assert call_count["10.0.0.1"] == 2

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self, client, admin_user, auth_headers, session):
        """rotate_circle should return True iff active_node_id was changed."""
        n0 = _mk_node(session, name="N0", address="10.0.0.0")
        n1 = _mk_node(session, name="N1", address="10.0.0.1")
        circle = _mk_circle(session, [n0.id, n1.id], current_index=0)

        async def alive(addr, port, udp, **kw):
            return {"is_online": True, "latency_ms": 10}

        with (
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=alive),
            patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock),
        ):
            from app.core.circle_scheduler import circle_scheduler
            result = await circle_scheduler.rotate_circle(circle.id)
        assert result is True


def _set_active(session, node_id: int):
    from app.models import Settings as DBSettings
    session.add(DBSettings(key="active_node_id", value=str(node_id)))
    session.commit()


def _set_circle_filters(session, circle, **fields):
    for k, v in fields.items():
        setattr(circle, k, v)
    session.add(circle)
    session.commit()


class TestRotateQualityFilters:
    """max_latency_ms / min_speed_mbps candidate filters + best mode +
    smart-skip. All opt-in — with the filters at 0 the behaviour above is
    unchanged."""

    @pytest.mark.asyncio
    async def test_max_latency_filters_slow_candidate(self, client, admin_user, auth_headers, session):
        n0 = _mk_node(session, name="N0", address="10.0.0.0")
        n1 = _mk_node(session, name="N1-slow", address="10.0.0.1", latency_ms=200)
        n2 = _mk_node(session, name="N2-fast", address="10.0.0.2", latency_ms=30)
        circle = _mk_circle(session, [n0.id, n1.id, n2.id], mode="sequential")
        _set_circle_filters(session, circle, max_latency_ms=100)

        probed: list[str] = []
        async def probe(addr, port, udp, **kw):
            probed.append(addr)
            return {"is_online": True, "latency_ms": 10}

        with (
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=probe),
            patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock),
        ):
            from app.core.circle_scheduler import circle_scheduler
            await circle_scheduler.rotate_circle(circle.id)

        assert "10.0.0.1" not in probed          # over budget → never probed
        session.expire_all()
        from app.models import NodeCircle
        assert session.get(NodeCircle, circle.id).current_index == 2

    @pytest.mark.asyncio
    async def test_min_speed_filters_slow_but_keeps_untested(self, client, admin_user, auth_headers, session):
        n0 = _mk_node(session, name="N0", address="10.0.0.0")
        n1 = _mk_node(session, name="N1-slow", address="10.0.0.1", speed_mbps=5.0)
        n2 = _mk_node(session, name="N2-untested", address="10.0.0.2", speed_mbps=None)
        circle = _mk_circle(session, [n0.id, n1.id, n2.id], mode="sequential")
        _set_circle_filters(session, circle, min_speed_mbps=50.0)

        probed: list[str] = []
        async def probe(addr, port, udp, **kw):
            probed.append(addr)
            return {"is_online": True, "latency_ms": 10}

        with (
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=probe),
            patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock),
        ):
            from app.core.circle_scheduler import circle_scheduler
            await circle_scheduler.rotate_circle(circle.id)

        assert "10.0.0.1" not in probed          # below floor → filtered
        assert "10.0.0.2" in probed              # untested → benefit of the doubt
        session.expire_all()
        from app.models import NodeCircle
        assert session.get(NodeCircle, circle.id).current_index == 2

    @pytest.mark.asyncio
    async def test_best_mode_probes_lowest_latency_first(self, client, admin_user, auth_headers, session):
        # Sequential order would try n1 first; best must try n2 (lower latency).
        n0 = _mk_node(session, name="N0", address="10.0.0.0")
        n1 = _mk_node(session, name="N1", address="10.0.0.1", latency_ms=80)
        n2 = _mk_node(session, name="N2", address="10.0.0.2", latency_ms=20)
        circle = _mk_circle(session, [n0.id, n1.id, n2.id], mode="best")

        probed: list[str] = []
        async def probe(addr, port, udp, **kw):
            probed.append(addr)
            return {"is_online": True, "latency_ms": 10}

        with (
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=probe),
            patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock),
        ):
            from app.core.circle_scheduler import circle_scheduler
            await circle_scheduler.rotate_circle(circle.id)

        assert probed[0] == "10.0.0.2"           # lowest-latency probed first
        session.expire_all()
        from app.models import NodeCircle
        assert session.get(NodeCircle, circle.id).current_index == 2

    @pytest.mark.asyncio
    async def test_smart_skip_healthy_active_on_scheduled_tick(self, client, admin_user, auth_headers, session):
        n0 = _mk_node(session, name="N0-active", address="10.0.0.0", is_online=True, latency_ms=30)
        n1 = _mk_node(session, name="N1", address="10.0.0.1")
        circle = _mk_circle(session, [n0.id, n1.id], current_index=0)
        _set_circle_filters(session, circle, max_latency_ms=100)
        _set_active(session, n0.id)

        probed: list[str] = []
        async def probe(addr, port, udp, **kw):
            probed.append(addr)
            return {"is_online": True, "latency_ms": 10}

        with (
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=probe),
            patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock),
        ):
            from app.core.circle_scheduler import circle_scheduler
            result = await circle_scheduler.rotate_circle(circle.id, from_scheduler=True)

        assert result is False                   # healthy active → skip
        assert probed == []                      # nothing even probed
        session.expire_all()
        from app.models import NodeCircle
        assert session.get(NodeCircle, circle.id).current_index == 0

    @pytest.mark.asyncio
    async def test_manual_rotate_ignores_smart_skip(self, client, admin_user, auth_headers, session):
        n0 = _mk_node(session, name="N0-active", address="10.0.0.0", is_online=True, latency_ms=30)
        n1 = _mk_node(session, name="N1", address="10.0.0.1")
        circle = _mk_circle(session, [n0.id, n1.id], current_index=0)
        _set_circle_filters(session, circle, max_latency_ms=100)
        _set_active(session, n0.id)

        async def probe(addr, port, udp, **kw):
            return {"is_online": True, "latency_ms": 10}

        with (
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=probe),
            patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock),
        ):
            from app.core.circle_scheduler import circle_scheduler
            result = await circle_scheduler.rotate_circle(circle.id)  # from_scheduler=False

        assert result is True                    # manual rotate ignores smart-skip
        session.expire_all()
        from app.models import NodeCircle
        assert session.get(NodeCircle, circle.id).current_index == 1


class TestSpeedPersistence:
    @pytest.mark.asyncio
    async def test_speedtest_persists_on_node(self, client, admin_user, auth_headers, session):
        n = _mk_node(session, name="SpeedNode", address="10.9.9.9")

        async def fake_speedtest(node):
            return {"node_id": node.id, "node_name": node.name,
                    "download_mbps": 87.5, "error": None}

        with patch("app.core.speedtest.speedtest_node", side_effect=fake_speedtest):
            resp = client.post(f"/api/nodes/{n.id}/speedtest", headers=auth_headers)

        assert resp.status_code == 200
        session.expire_all()
        from app.models import Node
        node = session.get(Node, n.id)
        assert node.speed_mbps == 87.5
        assert node.speed_tested_at is not None

    @pytest.mark.asyncio
    async def test_returns_false_when_all_dead(self, client, admin_user, auth_headers, session):
        """rotate_circle returns False on every abort path so failover
        layer can fall through."""
        n0 = _mk_node(session, name="N0", address="10.0.0.0")
        n1 = _mk_node(session, name="N1", address="10.0.0.1")
        circle = _mk_circle(session, [n0.id, n1.id], current_index=0)

        async def all_dead(addr, port, udp, **kw):
            return {"is_online": False, "error": "timeout"}

        with (
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=all_dead),
            patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock),
        ):
            from app.core.circle_scheduler import circle_scheduler
            result = await circle_scheduler.rotate_circle(circle.id)
        assert result is False

    @pytest.mark.asyncio
    async def test_node_removed_during_probe(self, client, admin_user, auth_headers, session):
        """If user removes the chosen node from the circle while probing,
        rotation aborts cleanly."""
        n0 = _mk_node(session, name="N0", address="10.0.0.0")
        n1 = _mk_node(session, name="N1", address="10.0.0.1")
        circle = _mk_circle(session, [n0.id, n1.id], current_index=0)

        async def probe_then_mutate(addr, port, udp, **kw):
            # While "probing", mutate the circle to remove the candidate
            from app.models import NodeCircle
            c = session.get(NodeCircle, circle.id)
            c.node_ids = json.dumps([n0.id])  # n1 removed
            session.add(c)
            session.commit()
            return {"is_online": True, "latency_ms": 10}

        with (
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=probe_then_mutate),
            patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock) as seam,
        ):
            from app.core.circle_scheduler import circle_scheduler
            await circle_scheduler.rotate_circle(circle.id)

        # Rotation aborted — current_index should NOT have changed,
        # _seamless_rotate not called.
        from app.models import NodeCircle
        session.expire_all()
        c = session.get(NodeCircle, circle.id)
        assert c.current_index == 0
        assert seam.await_count == 0


# ── Failover ↔ Circle integration ────────────────────────────────────────────
#
# When HealthChecker._failover decides to recover from a failed active
# node, it now first looks for an enabled NodeCircle containing that
# node. If found → delegates to circle_scheduler.rotate_circle (which
# pre-pings + retries, picking first alive sibling). On success, no
# need to consult `failover_node_ids`. On abort (all dead / race) it
# falls through to the existing list-based failover.

class TestFailoverViaCircle:
    @pytest.mark.asyncio
    async def test_finds_circle_and_delegates(self, client, admin_user, auth_headers, session):
        """failed node is in an enabled circle → delegate to rotate_circle."""
        from app.core.healthcheck import health_checker
        from app.models import Settings as DBSettings

        n0 = _mk_node(session, name="active-broken", address="10.0.0.0")
        n1 = _mk_node(session, name="circle-mate", address="10.0.0.1")
        circle = _mk_circle(session, [n0.id, n1.id], current_index=0)

        # Enable failover globally + leave failover_node_ids EMPTY so we
        # can prove the circle path is what saved the day.
        session.add(DBSettings(key="failover_enabled", value="true"))
        session.add(DBSettings(key="failover_node_ids", value="[]"))
        session.add(DBSettings(key="active_node_id", value=str(n0.id)))
        session.commit()

        # Mock circle_scheduler.rotate_circle as if it succeeded — we
        # don't need to re-test rotation logic here, just the wiring.
        with (
            patch("app.core.circle_scheduler.circle_scheduler.rotate_circle",
                  new_callable=AsyncMock, return_value=True) as mock_rotate,
        ):
            await health_checker._failover(n0.id)

        # rotate_circle was called with the circle id
        assert mock_rotate.await_count == 1
        assert mock_rotate.await_args.args == (circle.id,)

    @pytest.mark.asyncio
    async def test_no_circle_uses_failover_list(self, client, admin_user, auth_headers, session):
        """Failed node NOT in any circle → use failover_node_ids list."""
        from app.core.healthcheck import health_checker
        from app.models import Settings as DBSettings

        n0 = _mk_node(session, name="orphan-active", address="10.0.0.0")
        n1 = _mk_node(session, name="rescue", address="10.0.0.1")

        # No circle. Configure failover list with n1.
        session.add(DBSettings(key="failover_enabled", value="true"))
        session.add(DBSettings(key="failover_node_ids", value=json.dumps([n1.id])))
        session.add(DBSettings(key="active_node_id", value=str(n0.id)))
        session.commit()

        async def probe_alive(addr, port, udp, **kw):
            return {"is_online": True, "latency_ms": 10}

        with (
            patch("app.core.circle_scheduler.circle_scheduler.rotate_circle",
                  new_callable=AsyncMock) as mock_rotate,
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=probe_alive),
            patch("app.core.healthcheck.health_checker._reload_xray", new_callable=AsyncMock),
        ):
            await health_checker._failover(n0.id)

        # Circle scheduler NOT called (no circle for this node)
        assert mock_rotate.await_count == 0

        # active_node_id was set to n1 via the list path. Settings uses
        # `id` as PK with `key` UNIQUE, so query by .key not .get().
        session.expire_all()
        from sqlmodel import select as _sel
        active = session.exec(_sel(DBSettings).where(DBSettings.key == "active_node_id")).first()
        assert active is not None
        assert active.value == str(n1.id)

    @pytest.mark.asyncio
    async def test_circle_aborts_falls_through_to_list(self, client, admin_user, auth_headers, session):
        """Circle exists but rotation aborts → fall through to failover_node_ids."""
        from app.core.healthcheck import health_checker
        from app.models import Settings as DBSettings

        n0 = _mk_node(session, name="active-broken", address="10.0.0.0")
        n1 = _mk_node(session, name="circle-mate-also-dead", address="10.0.0.1")
        n2 = _mk_node(session, name="external-rescue", address="10.0.0.2")
        circle = _mk_circle(session, [n0.id, n1.id], current_index=0)

        session.add(DBSettings(key="failover_enabled", value="true"))
        session.add(DBSettings(key="failover_node_ids", value=json.dumps([n2.id])))
        session.add(DBSettings(key="active_node_id", value=str(n0.id)))
        session.commit()

        async def probe_external_alive(addr, port, udp, **kw):
            return {"is_online": True, "latency_ms": 5}

        with (
            patch("app.core.circle_scheduler.circle_scheduler.rotate_circle",
                  new_callable=AsyncMock, return_value=False) as mock_rotate,
            patch("app.core.healthcheck.health_checker._probe_node", side_effect=probe_external_alive),
            patch("app.core.healthcheck.health_checker._reload_xray", new_callable=AsyncMock),
        ):
            await health_checker._failover(n0.id)

        # Circle WAS tried first
        assert mock_rotate.await_count == 1
        # And then we fell through to the list and switched to n2
        session.expire_all()
        from sqlmodel import select as _sel
        active = session.exec(_sel(DBSettings).where(DBSettings.key == "active_node_id")).first()
        assert active is not None
        assert active.value == str(n2.id)

    @pytest.mark.asyncio
    async def test_failover_disabled_stays_put(self, client, admin_user, auth_headers, session):
        """failover_enabled=false → don't even try circle path."""
        from app.core.healthcheck import health_checker
        from app.models import Settings as DBSettings

        n0 = _mk_node(session, name="active", address="10.0.0.0")
        n1 = _mk_node(session, name="circle-mate", address="10.0.0.1")
        _mk_circle(session, [n0.id, n1.id], current_index=0)

        session.add(DBSettings(key="failover_enabled", value="false"))
        session.add(DBSettings(key="active_node_id", value=str(n0.id)))
        session.commit()

        with (
            patch("app.core.circle_scheduler.circle_scheduler.rotate_circle",
                  new_callable=AsyncMock) as mock_rotate,
        ):
            await health_checker._failover(n0.id)

        assert mock_rotate.await_count == 0
