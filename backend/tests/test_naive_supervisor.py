"""Tests for NaiveSupervisor — docker events → restart orchestration.

The supervisor runs a worker thread that iterates `docker.events()`; in
tests we don't touch docker at all. Instead we invoke the async handler
and rate-limiter directly, mock out `naive_manager.start_node`, and
verify the observable behavior: what gets restarted, when it gets
throttled, and when it's skipped."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core import naive_supervisor as sup_mod


class TestRateLimiter:
    def test_allows_up_to_cap(self):
        sup = sup_mod.NaiveSupervisor()
        with patch.object(sup_mod, "MAX_RESTARTS_PER_WINDOW", 3):
            assert sup._should_restart(1) is True
            assert sup._should_restart(1) is True
            assert sup._should_restart(1) is True
            # 4th hits the cap
            assert sup._should_restart(1) is False

    def test_per_node_independent(self):
        sup = sup_mod.NaiveSupervisor()
        with patch.object(sup_mod, "MAX_RESTARTS_PER_WINDOW", 1):
            assert sup._should_restart(1) is True
            assert sup._should_restart(2) is True  # different node, not blocked
            assert sup._should_restart(1) is False

    def test_window_slides(self):
        """Entries older than the window are dropped, allowing more restarts."""
        sup = sup_mod.NaiveSupervisor()
        with patch.object(sup_mod, "MAX_RESTARTS_PER_WINDOW", 2), \
             patch.object(sup_mod, "RESTART_WINDOW_SEC", 60):
            import time
            # Seed history with two very old entries
            sup._restart_history[1] = __import__("collections").deque(
                [time.monotonic() - 1000, time.monotonic() - 999]
            )
            # Should be allowed because old entries fall off
            assert sup._should_restart(1) is True


class TestHandleEvent:
    def _run(self, coro):
        return asyncio.run(coro)

    def _ev(self, node_id, exit_code="1", name="pitun-naive-5"):
        return {
            "Actor": {"Attributes": {
                "pitun_node_id": str(node_id) if node_id is not None else None,
                "exitCode": exit_code,
                "name": name,
            }},
        }

    def test_ignores_event_without_node_id_label(self):
        sup = sup_mod.NaiveSupervisor()
        with patch.object(sup, "_restart_node", new=AsyncMock()) as m:
            self._run(sup._handle_event({"Actor": {"Attributes": {}}}))
        m.assert_not_awaited()

    def test_ignores_non_integer_label(self):
        sup = sup_mod.NaiveSupervisor()
        ev = {"Actor": {"Attributes": {"pitun_node_id": "not-a-number"}}}
        with patch.object(sup, "_restart_node", new=AsyncMock()) as m:
            self._run(sup._handle_event(ev))
        m.assert_not_awaited()

    def test_dispatches_restart_for_valid_event(self):
        sup = sup_mod.NaiveSupervisor()
        with patch.object(sup, "_restart_node", new=AsyncMock()) as m:
            self._run(sup._handle_event(self._ev(7)))
        m.assert_awaited_once_with(7)

    def test_rate_limited_event_does_not_restart(self):
        sup = sup_mod.NaiveSupervisor()
        with patch.object(sup_mod, "MAX_RESTARTS_PER_WINDOW", 1), \
             patch.object(sup, "_restart_node", new=AsyncMock()) as m:
            # First event: allowed
            self._run(sup._handle_event(self._ev(9)))
            # Second event: over cap, skipped
            self._run(sup._handle_event(self._ev(9)))
        assert m.await_count == 1


class TestRestartNode:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_missing_node_skipped(self):
        sup = sup_mod.NaiveSupervisor()
        fake_session = MagicMock()
        fake_session.get = AsyncMock(return_value=None)
        fake_session.__aenter__ = AsyncMock(return_value=fake_session)
        fake_session.__aexit__ = AsyncMock(return_value=None)

        with patch("sqlmodel.ext.asyncio.session.AsyncSession", return_value=fake_session), \
             patch("app.database.get_async_engine"), \
             patch.object(sup_mod.naive_manager, "start_node",
                          new=AsyncMock()) as start:
            self._run(sup._restart_node(42))
        start.assert_not_awaited()

    def test_disabled_node_is_left_down(self):
        sup = sup_mod.NaiveSupervisor()
        fake_node = MagicMock(enabled=False, protocol="naive")
        fake_session = MagicMock()
        fake_session.get = AsyncMock(return_value=fake_node)
        fake_session.__aenter__ = AsyncMock(return_value=fake_session)
        fake_session.__aexit__ = AsyncMock(return_value=None)

        with patch("sqlmodel.ext.asyncio.session.AsyncSession", return_value=fake_session), \
             patch("app.database.get_async_engine"), \
             patch.object(sup_mod.naive_manager, "start_node",
                          new=AsyncMock()) as start:
            self._run(sup._restart_node(1))
        start.assert_not_awaited()

    def test_non_naive_node_skipped(self):
        sup = sup_mod.NaiveSupervisor()
        fake_node = MagicMock(enabled=True, protocol="vless")
        fake_session = MagicMock()
        fake_session.get = AsyncMock(return_value=fake_node)
        fake_session.__aenter__ = AsyncMock(return_value=fake_session)
        fake_session.__aexit__ = AsyncMock(return_value=None)

        with patch("sqlmodel.ext.asyncio.session.AsyncSession", return_value=fake_session), \
             patch("app.database.get_async_engine"), \
             patch.object(sup_mod.naive_manager, "start_node",
                          new=AsyncMock()) as start:
            self._run(sup._restart_node(1))
        start.assert_not_awaited()

    def test_enabled_naive_node_is_restarted(self):
        sup = sup_mod.NaiveSupervisor()
        fake_node = MagicMock(enabled=True, protocol="naive")
        fake_session = MagicMock()
        fake_session.get = AsyncMock(return_value=fake_node)
        fake_session.__aenter__ = AsyncMock(return_value=fake_session)
        fake_session.__aexit__ = AsyncMock(return_value=None)

        with patch("sqlmodel.ext.asyncio.session.AsyncSession", return_value=fake_session), \
             patch("app.database.get_async_engine"), \
             patch.object(sup_mod.naive_manager, "start_node",
                          new=AsyncMock()) as start:
            self._run(sup._restart_node(1))
        start.assert_awaited_once_with(fake_node)

    def test_start_failure_is_swallowed(self):
        """naive_manager errors must not escape — they'd kill the task."""
        sup = sup_mod.NaiveSupervisor()
        fake_node = MagicMock(enabled=True, protocol="naive")
        fake_session = MagicMock()
        fake_session.get = AsyncMock(return_value=fake_node)
        fake_session.__aenter__ = AsyncMock(return_value=fake_session)
        fake_session.__aexit__ = AsyncMock(return_value=None)

        with patch("sqlmodel.ext.asyncio.session.AsyncSession", return_value=fake_session), \
             patch("app.database.get_async_engine"), \
             patch.object(sup_mod.naive_manager, "start_node",
                          new=AsyncMock(side_effect=RuntimeError("docker down"))):
            # Must not raise
            self._run(sup._restart_node(1))
