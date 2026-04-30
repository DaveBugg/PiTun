"""Tests for xray_api helpers — notably `add_outbound` idempotency.

`add_outbound` must transparently recover from the "existing tag found"
error that xray returns when the same tag is re-added (typical after a
NodeCircle rotation). It should silently remove+retry, not leave the user
with a stale outbound."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest


class TestAddOutboundIdempotency:
    def _run(self, coro):
        return asyncio.run(coro)

    def test_add_ok_on_first_try(self):
        from app.core import xray_api

        with patch.object(xray_api, "_run_api",
                          new=AsyncMock(return_value=(0, "ok", ""))) as m:
            ok = self._run(xray_api.add_outbound({"tag": "node-1"}))
        assert ok is True
        # Single `ado` invocation, no retry.
        assert m.await_count == 1
        assert m.await_args_list[0].args[0] == "ado"

    def test_retry_on_existing_tag(self):
        from app.core import xray_api

        # First call fails with the tag-collision message, remove succeeds,
        # second `ado` succeeds.
        calls = []

        async def fake_run_api(cmd, *args):
            calls.append(cmd)
            if cmd == "ado" and calls.count("ado") == 1:
                return (1, "", "failed to add outbound: existing tag found: node-5")
            return (0, "ok", "")

        with patch.object(xray_api, "_run_api", new=fake_run_api):
            ok = self._run(xray_api.add_outbound({"tag": "node-5"}))

        assert ok is True
        # Sequence: ado → rmo → ado
        assert calls == ["ado", "rmo", "ado"]

    def test_failure_unrelated_to_existing_tag_is_not_retried(self):
        from app.core import xray_api

        calls = []
        async def fake_run_api(cmd, *args):
            calls.append(cmd)
            return (1, "", "some other permanent error")

        with patch.object(xray_api, "_run_api", new=fake_run_api):
            ok = self._run(xray_api.add_outbound({"tag": "node-9"}))

        assert ok is False
        # Only the single failing ado, no rmo-retry loop.
        assert calls == ["ado"]

    def test_retry_giving_up_returns_false(self):
        """Both ado attempts fail with existing-tag → must return False."""
        from app.core import xray_api

        calls = []
        async def fake_run_api(cmd, *args):
            calls.append(cmd)
            if cmd == "ado":
                return (1, "", "failed to add outbound: existing tag found: node-3")
            return (0, "", "")  # rmo pretends to succeed

        with patch.object(xray_api, "_run_api", new=fake_run_api):
            ok = self._run(xray_api.add_outbound({"tag": "node-3"}))

        assert ok is False
        # ado → rmo → ado (one retry only, no infinite loop)
        assert calls == ["ado", "rmo", "ado"]
