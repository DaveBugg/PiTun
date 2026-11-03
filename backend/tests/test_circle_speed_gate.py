"""A "best" circle with a speed target checks the speed of the nodes it is about
to use, takes the first by ping that delivers it, and keeps the cost bounded
whatever intervals the user chose."""
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Node, NodeCircle


@pytest.fixture(autouse=True)
def _fresh_budget():
    from app.core.circle_scheduler import circle_scheduler
    circle_scheduler._speed_tokens.clear()
    yield
    circle_scheduler._speed_tokens.clear()


def _node(session, **kw):
    d = dict(name="N", protocol="vless", address="1.2.3.4", port=443, uuid="abc",
             transport="tcp", tls="tls", enabled=True, is_online=True, order=0)
    d.update(kw)
    n = Node(**d)
    session.add(n)
    session.commit()
    session.refresh(n)
    return n


def _circle(session, ids, **kw):
    d = dict(name="C", enabled=True, node_ids=json.dumps(ids), mode="best",
             interval_min=5, interval_max=15, current_index=0, min_speed_mbps=50.0)
    d.update(kw)
    c = NodeCircle(**d)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c


def _ago(minutes):
    return datetime.now(timezone.utc) - timedelta(minutes=minutes)


async def _rotate(circle_id, speeds):
    """Rotate with every probe alive and speedtest_node answering from `speeds`
    (address -> Mbit/s). Returns the list of addresses that were speed-tested."""
    tested = []

    async def probe(addr, port, udp, **kw):
        return {"is_online": True, "latency_ms": 10}

    async def speed(node):
        tested.append(node.address)
        return {"node_id": node.id, "node_name": node.name, "reachable": True,
                "download_mbps": speeds.get(node.address), "max_mbps": speeds.get(node.address),
                "latency_ms": 10, "error": None}

    with (
        patch("app.core.healthcheck.health_checker._probe_node", side_effect=probe),
        patch("app.core.speedtest.speedtest_node", side_effect=speed),
        patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock),
        patch("app.core.circle_scheduler.CircleScheduler._http_probe_enabled", new_callable=AsyncMock, return_value=False),
    ):
        from app.core.circle_scheduler import circle_scheduler
        await circle_scheduler.rotate_circle(circle_id)
    return tested


def _current(session, circle_id):
    session.expire_all()
    c = session.get(NodeCircle, circle_id)
    return json.loads(c.node_ids)[c.current_index]


@pytest.mark.asyncio
async def test_a_slow_node_with_the_best_ping_is_skipped_for_the_next_by_ping(client, admin_user, session):
    cur = _node(session, name="cur", address="10.0.0.0", latency_ms=500)
    slow = _node(session, name="slow", address="10.0.0.1", latency_ms=20)
    good = _node(session, name="good", address="10.0.0.2", latency_ms=60)
    c = _circle(session, [cur.id, slow.id, good.id])
    tested = await _rotate(c.id, {"10.0.0.1": 8.0, "10.0.0.2": 120.0})
    assert tested == ["10.0.0.1", "10.0.0.2"]            # in ping order, stops at the first good one
    assert _current(session, c.id) == good.id


@pytest.mark.asyncio
async def test_nobody_reaching_the_target_ends_on_the_fastest_measured(client, admin_user, session):
    cur = _node(session, name="cur", address="10.0.0.0", latency_ms=500)
    a = _node(session, name="a", address="10.0.0.1", latency_ms=20)
    b = _node(session, name="b", address="10.0.0.2", latency_ms=30)
    c = _circle(session, [cur.id, a.id, b.id])
    await _rotate(c.id, {"10.0.0.1": 10.0, "10.0.0.2": 35.0})
    assert _current(session, c.id) == b.id


@pytest.mark.asyncio
async def test_a_fresh_good_measurement_is_not_repeated(client, admin_user, session):
    cur = _node(session, name="cur", address="10.0.0.0", latency_ms=500)
    n = _node(session, name="n", address="10.0.0.1", latency_ms=20, speed_mbps=90.0, speed_tested_at=_ago(1))
    c = _circle(session, [cur.id, n.id])
    tested = await _rotate(c.id, {})
    assert tested == [] and _current(session, c.id) == n.id


@pytest.mark.asyncio
async def test_an_old_measurement_is_replaced_by_a_live_one(client, admin_user, session):
    cur = _node(session, name="cur", address="10.0.0.0", latency_ms=500)
    n = _node(session, name="n", address="10.0.0.1", latency_ms=20, speed_mbps=90.0, speed_tested_at=_ago(240))
    c = _circle(session, [cur.id, n.id])
    assert await _rotate(c.id, {"10.0.0.1": 70.0}) == ["10.0.0.1"]


@pytest.mark.asyncio
async def test_a_slow_verdict_is_trusted_longer_than_a_good_one(client, admin_user, session):
    """Ten minutes old and slow: not re-tested (a persistently slow node with a good
    ping must not cost a test on every rotation). The same age and good: re-tested."""
    cur = _node(session, name="cur", address="10.0.0.0", latency_ms=500)
    slow = _node(session, name="slow", address="10.0.0.1", latency_ms=20, speed_mbps=5.0, speed_tested_at=_ago(10))
    good = _node(session, name="good", address="10.0.0.2", latency_ms=40, speed_mbps=90.0, speed_tested_at=_ago(10))
    c = _circle(session, [cur.id, slow.id, good.id])
    tested = await _rotate(c.id, {"10.0.0.2": 95.0})
    assert tested == ["10.0.0.2"] and _current(session, c.id) == good.id


@pytest.mark.asyncio
async def test_one_rotation_never_runs_more_than_the_allowed_number_of_tests(client, admin_user, session):
    cur = _node(session, name="cur", address="10.0.0.0", latency_ms=900)
    ids, speeds = [cur.id], {}
    for i in range(1, 9):
        n = _node(session, name=f"n{i}", address=f"10.0.1.{i}", latency_ms=10 * i)
        ids.append(n.id)
        speeds[n.address] = float(i)                     # all slow
    c = _circle(session, ids)
    tested = await _rotate(c.id, speeds)
    from app.core.circle_scheduler import circle_scheduler
    assert len(tested) == circle_scheduler._SPEED_CHECKS_MAX
    assert _current(session, c.id) != cur.id            # still moved, on the fastest measured


@pytest.mark.asyncio
async def test_without_a_target_no_speed_test_is_run(client, admin_user, session):
    cur = _node(session, name="cur", address="10.0.0.0", latency_ms=500)
    n = _node(session, name="n", address="10.0.0.1", latency_ms=20)
    c = _circle(session, [cur.id, n.id], min_speed_mbps=0.0)
    assert await _rotate(c.id, {}) == [] and _current(session, c.id) == n.id


@pytest.mark.asyncio
async def test_a_failed_speed_test_does_not_stop_the_circle(client, admin_user, session):
    cur = _node(session, name="cur", address="10.0.0.0", latency_ms=500)
    n = _node(session, name="n", address="10.0.0.1", latency_ms=20)
    c = _circle(session, [cur.id, n.id])
    await _rotate(c.id, {"10.0.0.1": None})              # test returned nothing
    assert _current(session, c.id) == n.id               # taken on its ping
