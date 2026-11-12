"""A node that cannot carry traffic is dead, not "unmeasured": the circle must
not fall back to it, and must not re-probe it on every rotation."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Node, NodeCircle
from tests.test_circle_speed_gate import _circle, _current, _node


@pytest.fixture(autouse=True)
def _fresh():
    from app.core.circle_scheduler import circle_scheduler
    circle_scheduler._speed_tokens.clear()
    circle_scheduler._dead_until.clear()
    yield
    circle_scheduler._speed_tokens.clear()
    circle_scheduler._dead_until.clear()


async def _rotate(circle_id, results):
    """results: address -> speedtest result dict. Returns the tested addresses."""
    tested = []

    async def probe(addr, port, udp, **kw):
        return {"is_online": True, "latency_ms": 10}

    async def speed(node):
        tested.append(node.address)
        return results[node.address]

    with (
        patch("app.core.healthcheck.health_checker._probe_node", side_effect=probe),
        patch("app.core.speedtest.speedtest_node", side_effect=speed),
        patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock),
        patch("app.core.circle_scheduler.CircleScheduler._http_probe_enabled", new_callable=AsyncMock, return_value=False),
    ):
        from app.core.circle_scheduler import circle_scheduler
        await circle_scheduler.rotate_circle(circle_id)
    return tested


def _dead(addr):
    return {"reachable": False, "download_mbps": None, "max_mbps": None,
            "error": "unreachable: cloudflare: ConnectError"}


def _unmeasured(addr):
    return {"reachable": True, "download_mbps": None, "max_mbps": None, "error": None}


def _fast(addr):
    return {"reachable": True, "download_mbps": 120.0, "max_mbps": 130.0, "error": None}


@pytest.mark.asyncio
async def test_an_unreachable_node_is_never_the_fallback(client, admin_user, session):
    cur = _node(session, name="cur", address="10.0.0.0", latency_ms=500)
    dead = _node(session, name="dead", address="10.0.0.1", latency_ms=20)
    maybe = _node(session, name="maybe", address="10.0.0.2", latency_ms=30)
    c = _circle(session, [cur.id, dead.id, maybe.id])
    await _rotate(c.id, {"10.0.0.1": _dead("x"), "10.0.0.2": _unmeasured("x")})
    assert _current(session, c.id) == maybe.id     # reachable-but-unmeasured beats dead


@pytest.mark.asyncio
async def test_a_dead_node_is_not_probed_again_straight_away(client, admin_user, session):
    cur = _node(session, name="cur", address="10.0.0.0", latency_ms=500)
    dead = _node(session, name="dead", address="10.0.0.1", latency_ms=20)
    good = _node(session, name="good", address="10.0.0.2", latency_ms=30)
    c = _circle(session, [cur.id, dead.id, good.id])
    first = await _rotate(c.id, {"10.0.0.1": _dead("x"), "10.0.0.2": _fast("x")})
    assert first == ["10.0.0.1", "10.0.0.2"]
    # reset so the next rotation has somewhere to go
    session.expire_all()
    cc = session.get(NodeCircle, c.id)
    cc.current_index = 0
    session.add(cc)
    session.commit()
    second = await _rotate(c.id, {"10.0.0.1": _dead("x"), "10.0.0.2": _fast("x")})
    assert "10.0.0.1" not in second                 # remembered as dead


@pytest.mark.asyncio
async def test_all_dead_still_does_not_crash_the_circle(client, admin_user, session):
    cur = _node(session, name="cur", address="10.0.0.0", latency_ms=500)
    a = _node(session, name="a", address="10.0.0.1", latency_ms=20)
    c = _circle(session, [cur.id, a.id])
    await _rotate(c.id, {"10.0.0.1": _dead("x")})
    assert _current(session, c.id) == cur.id        # nothing alive: stays put


@pytest.mark.asyncio
async def test_a_node_remembered_dead_by_its_speed_test_is_not_the_tcp_fallback(client, admin_user, session):
    # Rotation 1 finds `a` dead in the speed test; a failover rotation a minute
    # later must not pick it as the "TCP-only" fallback either.
    cur = _node(session, name="cur", address="10.0.0.0", latency_ms=500)
    a = _node(session, name="a", address="10.0.0.1", latency_ms=20)
    c = _circle(session, [cur.id, a.id])
    await _rotate(c.id, {"10.0.0.1": _dead("x")})
    assert _current(session, c.id) == cur.id
    await _rotate(c.id, {"10.0.0.1": _dead("x")})
    assert _current(session, c.id) == cur.id


def test_only_a_tunnel_that_came_up_and_carried_nothing_is_no_traffic():
    from app.core.speedtest import no_traffic_through_tunnel
    assert no_traffic_through_tunnel({"ok": False, "detail": "cloudflare: ConnectError"})
    assert no_traffic_through_tunnel({"ok": False, "detail": "google: HTTP 503"})
    assert not no_traffic_through_tunnel({"ok": False, "detail": "xray: SOCKS port never opened"})
    assert not no_traffic_through_tunnel({"ok": False, "detail": "dns: no answer"})
    assert not no_traffic_through_tunnel({"ok": False, "detail": "naive sidecar down"})
    assert not no_traffic_through_tunnel({"ok": True, "detail": "google"})


@pytest.mark.asyncio
async def test_circle_http_probe_does_not_blame_the_node_for_a_tunnel_start_failure(client, admin_user, session):
    from app.core.circle_scheduler import CircleScheduler
    n = _node(session, name="n", address="10.0.0.5")
    with patch("app.core.speedtest.reachability_check",
               new=AsyncMock(return_value={"ok": False, "detail": "xray: SOCKS port never opened"})):
        ok, _ = await CircleScheduler()._http_probe(n.id)
    assert ok is True
    with patch("app.core.speedtest.reachability_check",
               new=AsyncMock(return_value={"ok": False, "detail": "cloudflare: ConnectError"})):
        ok, _ = await CircleScheduler()._http_probe(n.id)
    assert ok is False
