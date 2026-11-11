"""A circle that measures speed needs more time than the plain rotation deadline."""
import asyncio

import pytest

from app.core.circle_scheduler import CircleScheduler
from app.models import NodeCircle


def _circle(session, **kw):
    c = NodeCircle(name="c", node_ids="[1,2]", interval_min=5, interval_max=10, enabled=True, **kw)
    session.add(c)
    session.commit()
    session.refresh(c)
    return c.id


@pytest.mark.parametrize(("kw", "expect"), [
    (dict(mode="best", min_speed_mbps=50.0), True),     # slow measuring is allowed to finish
    (dict(mode="best", min_speed_mbps=0.0), False),      # plain ping rotation keeps the short deadline
    (dict(mode="sequential"), False),
])
def test_deadline_has_room_for_speed_checks(engine, session, monkeypatch, kw, expect):
    import app.database as db_mod
    monkeypatch.setattr(db_mod, "_async_engine", engine[1])
    cid = _circle(session, **kw)
    sched = CircleScheduler()
    monkeypatch.setattr(CircleScheduler, "_ROTATE_DEADLINE_S", 0.2)
    monkeypatch.setattr(CircleScheduler, "_SPEED_BUDGET_S", 1)
    monkeypatch.setattr(CircleScheduler, "_http_probe_enabled", staticmethod(lambda: asyncio.sleep(0, result=False)))

    async def slow(self, circle_id, from_scheduler=False):
        await asyncio.sleep(0.5)
        return True

    monkeypatch.setattr(CircleScheduler, "_do_rotate", slow)
    assert asyncio.run(sched.rotate_circle(cid)) is expect
