"""Tests for the v1.2.6 NaiveSupervisor exponential backoff schedule.

The supervisor's `_backoff_delay(node_id)` returns the seconds to sleep
before the *next* restart attempt for a given node. The schedule is
keyed off the count of timestamps recorded in the sliding window:

    1st restart (1 entry in history) → 0.5s base
    2nd restart (2 entries)          → 1.0s base
    3rd restart (3 entries)          → 2.0s base
    4th restart (4 entries)          → 4.0s base
    5th restart (5 entries)          → 8.0s base (capped)

±25% jitter is applied multiplicatively. The cap stops growth — without
it the 6th attempt would be 16s, 7th = 32s, etc.
"""
from __future__ import annotations

import time

import pytest

from app.core.naive_supervisor import (
    NaiveSupervisor,
    _RESTART_BACKOFF_BASE_SEC,
    _RESTART_BACKOFF_CAP_SEC,
    _RESTART_BACKOFF_JITTER,
)


@pytest.fixture
def supervisor():
    """Fresh supervisor with empty history. Tests poke `_restart_history`
    directly to simulate a node having had N prior attempts."""
    return NaiveSupervisor()


# ── Schedule (deterministic by zeroing jitter) ────────────────────────────────


class TestBackoffSchedule:
    def test_first_restart_is_base_delay(self, supervisor, monkeypatch):
        # Force jitter = 0 by stubbing random.uniform to always return 0
        monkeypatch.setattr(
            "app.core.naive_supervisor.random.uniform", lambda lo, hi: 0.0
        )
        # One entry in history = "this is the 1st attempt"
        supervisor._restart_history[1] = [time.monotonic()]
        delay = supervisor._backoff_delay(1)
        assert delay == pytest.approx(_RESTART_BACKOFF_BASE_SEC)  # 0.5

    def test_doubles_on_each_attempt(self, supervisor, monkeypatch):
        monkeypatch.setattr(
            "app.core.naive_supervisor.random.uniform", lambda lo, hi: 0.0
        )

        expected_schedule = [0.5, 1.0, 2.0, 4.0, 8.0]
        for attempt_count, expected in enumerate(expected_schedule, start=1):
            from collections import deque
            supervisor._restart_history[42] = deque(
                [time.monotonic()] * attempt_count
            )
            delay = supervisor._backoff_delay(42)
            assert delay == pytest.approx(expected), (
                f"attempt {attempt_count}: expected {expected}s, got {delay:.3f}s"
            )

    def test_cap_clamps_late_attempts(self, supervisor, monkeypatch):
        monkeypatch.setattr(
            "app.core.naive_supervisor.random.uniform", lambda lo, hi: 0.0
        )
        from collections import deque

        # Even at 20 prior attempts, delay must not exceed the cap
        supervisor._restart_history[7] = deque([time.monotonic()] * 20)
        delay = supervisor._backoff_delay(7)
        assert delay == pytest.approx(_RESTART_BACKOFF_CAP_SEC)

    def test_no_history_yields_base_delay(self, supervisor, monkeypatch):
        # If `_should_restart` was never called for this node and the
        # history dict has no entry, treat as 1st attempt.
        monkeypatch.setattr(
            "app.core.naive_supervisor.random.uniform", lambda lo, hi: 0.0
        )
        # No call to setdefault first — _backoff_delay must not crash
        delay = supervisor._backoff_delay(99999)
        assert delay == pytest.approx(_RESTART_BACKOFF_BASE_SEC)


# ── Jitter range ──────────────────────────────────────────────────────────────


class TestBackoffJitter:
    def test_jitter_within_25_percent_band(self, supervisor):
        from collections import deque

        supervisor._restart_history[1] = deque([time.monotonic()])  # 1st attempt → base 0.5

        # Sample 200 times, each must fall within ±25% of base
        lo = _RESTART_BACKOFF_BASE_SEC * (1 - _RESTART_BACKOFF_JITTER)
        hi = _RESTART_BACKOFF_BASE_SEC * (1 + _RESTART_BACKOFF_JITTER)
        for _ in range(200):
            d = supervisor._backoff_delay(1)
            assert lo <= d <= hi + 1e-9, f"delay {d:.4f} outside [{lo:.4f}, {hi:.4f}]"

    def test_jitter_produces_variation(self, supervisor):
        # Without jitter the result would be deterministic — if all 100
        # samples were identical, jitter is broken.
        from collections import deque
        supervisor._restart_history[1] = deque([time.monotonic()])
        samples = {round(supervisor._backoff_delay(1), 6) for _ in range(100)}
        assert len(samples) > 1, "jitter is not actually randomising the delay"

    def test_jitter_applied_at_capped_attempts_too(self, supervisor):
        from collections import deque
        supervisor._restart_history[1] = deque([time.monotonic()] * 20)
        # Even at the cap, jitter still varies the actual delay slightly
        lo = _RESTART_BACKOFF_CAP_SEC * (1 - _RESTART_BACKOFF_JITTER)
        hi = _RESTART_BACKOFF_CAP_SEC * (1 + _RESTART_BACKOFF_JITTER)
        for _ in range(50):
            d = supervisor._backoff_delay(1)
            assert lo <= d <= hi + 1e-9


# ── Sanity: never returns a negative delay ───────────────────────────────────


def test_never_returns_negative(supervisor):
    """Even with worst-case jitter on attempt 0, max(0.0, ...) keeps
    the result non-negative. asyncio.sleep would error on negative
    values."""
    from collections import deque
    for n in range(0, 25):
        if n > 0:
            supervisor._restart_history[1] = deque([time.monotonic()] * n)
        for _ in range(20):
            d = supervisor._backoff_delay(1)
            assert d >= 0.0, f"negative delay at attempt {n}: {d}"
