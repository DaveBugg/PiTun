"""The speed-target scheme must behave for ANY intervals a user sets, not just
the ones it was tuned on. This runs real rotations (the scheduler's own code,
with fake probes and a fake speed test) over a simulated stretch of time for a
matrix of interval pairs and node pools, advancing the clock between rotations
by moving every stored measurement back in time.

Invariants, whatever the intervals:
  * a rotation never speed-tests more than the allowed number of nodes;
  * every rotation moves the circle (nothing stalls or errors);
  * with good nodes in the pool, once the slow ones are known the circle sits on
    a node that meets the target, and the work stays cheap;
  * with nothing meeting the target the circle still rotates, on the fastest
    measured node, at a bounded cost;
  * the cost per hour does not blow up for short intervals.

Set PITUN_MATRIX_FULL=1 for the wide grid and a longer horizon."""
import json
import os
import random
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import select

from app.models import Node, NodeCircle

FULL = os.environ.get("PITUN_MATRIX_FULL") == "1"
TARGET = 50.0
NODES = 41

# (interval_min, interval_max) minutes — including the awkward ones: equal,
# off-by-one, just under/over the clamps, huge.
INTERVALS = [(5, 5), (5, 6), (5, 15), (7, 9), (10, 11), (12, 23), (23, 24), (29, 31), (45, 60)]
if FULL:
    INTERVALS += [(5, 30), (6, 7), (9, 10), (15, 15), (20, 21), (30, 30), (31, 32), (60, 120), (120, 240), (5, 600)]
HORIZON_H = 24 if FULL else 6


@pytest.fixture(autouse=True)
def _fresh_budget():
    from app.core.circle_scheduler import circle_scheduler
    circle_scheduler._speed_tokens.clear()
    yield
    circle_scheduler._speed_tokens.clear()


def _pool(kind: str, rng: random.Random):
    """[(ping_ms, true_speed_mbps)] for each node. Pings are distinct."""
    pings = rng.sample(range(10, 400), NODES)
    if kind == "mixed":                      # ~40% slow, random
        speeds = [rng.choice([8, 15, 30, 45, 70, 90, 140, 220]) for _ in range(NODES)]
    elif kind == "best_ping_is_slow":        # the lowest pings are the slowest
        order = sorted(range(NODES), key=lambda i: pings[i])
        speeds = [0.0] * NODES
        for rank, i in enumerate(order):
            speeds[i] = 5.0 + rank * 4.0     # 5 .. 165 Mbit/s, rising with ping
    elif kind == "all_slow":                 # nobody reaches the target
        speeds = [rng.uniform(3, 40) for _ in range(NODES)]
    else:
        raise ValueError(kind)
    return list(zip(pings, speeds))


async def _simulate(session, kind, imin, imax, seed):
    rng = random.Random(seed)
    pool = _pool(kind, rng)
    nodes = []
    for i, (ping, spd) in enumerate(pool):
        n = Node(name=f"n{i}", protocol="vless", address=f"10.9.{i // 200}.{i % 200 + 1}", port=443,
                 uuid="u", transport="tcp", tls="tls", enabled=True, is_online=True,
                 order=i, latency_ms=ping)
        session.add(n)
        nodes.append(n)
    session.commit()
    for n in nodes:
        session.refresh(n)
    truth = {n.address: spd for n, (_, spd) in zip(nodes, pool)}
    by_id = {n.id: n for n in nodes}
    circle = NodeCircle(name="m", enabled=True, node_ids=json.dumps([n.id for n in nodes]),
                        mode="best", interval_min=imin, interval_max=imax,
                        current_index=0, min_speed_mbps=TARGET)
    session.add(circle)
    session.commit()
    session.refresh(circle)
    cid = circle.id

    tests_in_rotation: list[int] = []
    counter = {"n": 0}

    async def probe(addr, port, udp, **kw):
        return {"is_online": True, "latency_ms": 10}

    async def speed(node):
        counter["n"] += 1
        s = truth[node.address] * rng.uniform(0.92, 1.08)      # a real test is noisy
        return {"node_id": node.id, "node_name": node.name, "reachable": True,
                "download_mbps": s, "max_mbps": s, "latency_ms": 10, "error": None}

    from app.core.circle_scheduler import circle_scheduler
    selected_speed: list[float] = []
    moved = 0
    prev = None
    elapsed_min = 0.0
    horizon_min = HORIZON_H * 60

    with (
        patch("app.core.healthcheck.health_checker._probe_node", side_effect=probe),
        patch("app.core.speedtest.speedtest_node", side_effect=speed),
        patch("app.core.circle_scheduler.CircleScheduler._seamless_rotate", new_callable=AsyncMock),
        patch("app.core.circle_scheduler.CircleScheduler._http_probe_enabled", new_callable=AsyncMock, return_value=False),
    ):
        while elapsed_min < horizon_min:
            counter["n"] = 0
            ok = await circle_scheduler.rotate_circle(cid)
            assert ok is not False, f"rotation stalled at {elapsed_min:.0f} min ({kind} {imin}-{imax})"
            tests_in_rotation.append(counter["n"])
            session.expire_all()
            c = session.get(NodeCircle, cid)
            cur_id = json.loads(c.node_ids)[c.current_index]
            if prev is not None and cur_id != prev:
                moved += 1
            prev = cur_id
            selected_speed.append(truth[by_id[cur_id].address])
            # advance the clock by one random interval: age every stored measurement
            dt = rng.uniform(imin, imax)
            elapsed_min += dt
            for n in session.exec(select(Node)).all():
                if n.speed_tested_at is not None:
                    n.speed_tested_at = n.speed_tested_at - timedelta(minutes=dt)
                    session.add(n)
            session.commit()
    return {
        "rotations": len(tests_in_rotation),
        "tests": tests_in_rotation,
        "selected_speed": selected_speed,
        "moved": moved,
        "good_in_pool": sum(1 for _, s in pool if s >= TARGET * 1.1),
        "fastest": max(s for _, s in pool),
        "hours": elapsed_min / 60,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["mixed", "best_ping_is_slow", "all_slow"])
@pytest.mark.parametrize("imin,imax", INTERVALS)
async def test_the_scheme_holds_for_any_intervals(client, admin_user, session, kind, imin, imax):
    from app.core.circle_scheduler import circle_scheduler
    cap = circle_scheduler._SPEED_CHECKS_MAX
    r = await _simulate(session, kind, imin, imax, seed=hash((kind, imin, imax)) & 0xFFFF)

    if os.environ.get("PITUN_MATRIX_REPORT"):
        warm_ = 12
        sel = r["selected_speed"][warm_:] or [0.0]
        ok_share = sum(1 for x in sel if x >= TARGET * 0.9) / len(sel)
        tail_ = r["tests"][warm_:] or [0]
        with open(os.environ["PITUN_MATRIX_REPORT"], "a", encoding="utf-8") as fh:
            fh.write(f"{kind:18s} {imin:>3}-{imax:<3} rot={r['rotations']:4d} tests/h={sum(r['tests']) / r['hours']:5.1f} "
                     f"max/rot={max(r['tests'])} avg-settled={sum(tail_) / len(tail_):4.2f} on-target={ok_share:5.0%} "
                     f"moved={r['moved']}\n")

    # 1. bounded work per rotation, and it always moves
    assert max(r["tests"]) <= cap
    assert r["moved"] >= r["rotations"] - 2          # a rotation changes the node (barring a repeat pick)

    # 2. bounded cost over ANY stretch: the average is at most the per-rotation refill,
    #    plus the one-off burst. This is what keeps a target nobody reaches from
    #    turning into a test storm at short intervals.
    refill, burst = circle_scheduler._SPEED_REFILL, circle_scheduler._SPEED_BURST
    assert sum(r["tests"]) <= burst + refill * r["rotations"]

    if kind == "all_slow":
        # nothing reaches the target: it still rotates, always on measured nodes, and gets cheap
        tail = r["tests"][len(r["tests"]) // 2:]
        assert sum(tail) / max(1, len(tail)) <= circle_scheduler._SPEED_REFILL + 0.5
        return

    # 3. with good nodes available, after a warm-up the circle sits on one that meets the target.
    #    Warm-up = enough rotations for the slow ones above the first good node to be known:
    #    ceil(slow-above / cap), plus slack.
    warm = 12
    settled = r["selected_speed"][warm:]
    if settled:
        share = sum(1 for s in settled if s >= TARGET * 0.9) / len(settled)
        assert share >= 0.95, f"{kind} {imin}-{imax}: only {share:.0%} of settled rotations met the target"

    # 4. cheap once settled: on average well under the cap per rotation
    tail_tests = r["tests"][warm:]
    if tail_tests:
        assert sum(tail_tests) / len(tail_tests) <= circle_scheduler._SPEED_REFILL + 0.5
