"""Crash-restart backoff: 3 s, then 60 s, then 300 s; a stable run resets it."""
from app.core.xray import _RestartBackoff


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_delays_grow_then_cap():
    b = _RestartBackoff(clock=_Clock())
    assert [b.next_delay() for _ in range(5)] == [3.0, 60.0, 300.0, 300.0, 300.0]


def test_stable_run_restarts_the_schedule():
    clock = _Clock()
    b = _RestartBackoff(stable_after=600, clock=clock)
    b.next_delay()
    b.next_delay()
    clock.t += 601
    assert b.next_delay() == 3.0


def test_quick_second_crash_keeps_escalating():
    clock = _Clock()
    b = _RestartBackoff(stable_after=600, clock=clock)
    b.next_delay()
    clock.t += 30
    assert b.next_delay() == 60.0


def test_reset():
    b = _RestartBackoff(clock=_Clock())
    b.next_delay()
    b.next_delay()
    b.reset()
    assert b.next_delay() == 3.0
