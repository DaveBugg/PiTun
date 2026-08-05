"""Throwaway-xray lifecycle for the speed test and the route-explain probe.

Both spawn a short-lived xray. Two defects lived here: the startup-timeout
branch returned a 3-tuple that blew up the caller's unpack BEFORE `proc` was
bound (so the `finally` cleanup reaped nothing and the process leaked with a
credential-bearing temp config), and both picked their port by guessing.
"""
import asyncio
import inspect
from unittest import mock
from unittest.mock import AsyncMock

from app.core import speedtest as st
from app.models import Node


def _node():
    return Node(
        id=1, name="n", protocol="vless", address="1.2.3.4", port=443,
        uuid="u", transport="tcp", tls="none", enabled=True,
    )


class _FakeProc:
    """A temp xray that starts but never binds its SOCKS port."""

    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9

    async def wait(self):
        return self.returncode

    async def communicate(self):
        return b"", b""


class TestStartTempXrayTimeoutBranch:
    def test_timeout_branch_returns_four_items(self):
        """Every branch must be unpackable as (port, proc, path, err)."""
        src = inspect.getsource(st._start_temp_xray)
        returns = [
            line.strip() for line in src.splitlines()
            if line.strip().startswith("return ")
        ]
        assert returns, "no return statements found"
        for r in returns:
            # crude but effective: count top-level commas in the tuple
            body = r[len("return "):]
            depth = 0
            commas = 0
            for ch in body:
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == "," and depth == 0:
                    commas += 1
            assert commas == 3, f"not a 4-tuple: {r}"

    def test_leaked_process_is_reaped_and_error_surfaced(self):
        fake = _FakeProc()

        async def _fake_start(node, chain, entry_ip):
            return 19999, fake, None, "xray: SOCKS port never opened"

        with (
            mock.patch.object(st, "_resolve_chain", new_callable=AsyncMock,
                              return_value=[_node()]),
            mock.patch.object(st.HealthChecker, "_resolve_direct",
                              new_callable=AsyncMock, return_value="1.2.3.4"),
            mock.patch.object(st, "_resolve_test_urls", new_callable=AsyncMock,
                              return_value=["http://example.com/10mb"]),
            mock.patch.object(st, "_start_temp_xray", side_effect=_fake_start),
            mock.patch.object(st, "_run_curl_speedtest",
                              new_callable=AsyncMock) as curl,
        ):
            result = asyncio.run(st.speedtest_node(_node()))

        # The real reason reaches the UI instead of a generic failure…
        assert "SOCKS port never opened" in (result.get("error") or "")
        # …curl never ran against a dead port…
        curl.assert_not_awaited()
        # …and the process was actually reaped rather than leaked.
        assert fake.terminated or fake.killed


class TestUnifiedMeasure:
    """The unified speedtest_node: reachability gate first, then avg + peak."""

    def _start_ok(self, node, chain, entry_ip):
        return 19999, _FakeProc(), None, None

    def test_unreachable_skips_speed_targets(self):
        with (
            mock.patch.object(st, "_resolve_chain", new_callable=AsyncMock, return_value=[_node()]),
            mock.patch.object(st.HealthChecker, "_resolve_direct",
                              new_callable=AsyncMock, return_value="1.2.3.4"),
            mock.patch.object(st, "_start_temp_xray", side_effect=self._start_ok),
            mock.patch.object(st, "_probe_reachable", new_callable=AsyncMock,
                              return_value=(False, None, "google: timeout")),
            mock.patch.object(st, "_measure_download", new_callable=AsyncMock) as meas,
        ):
            result = asyncio.run(st.speedtest_node(_node()))

        assert result["reachable"] is False
        assert (result.get("error") or "").startswith("unreachable")
        assert result["download_mbps"] is None
        # The whole point: a dead node never grinds the speed fallbacks.
        meas.assert_not_awaited()

    def test_reachable_measures_avg_and_peak(self):
        with (
            mock.patch.object(st, "_resolve_chain", new_callable=AsyncMock, return_value=[_node()]),
            mock.patch.object(st.HealthChecker, "_resolve_direct",
                              new_callable=AsyncMock, return_value="1.2.3.4"),
            mock.patch.object(st, "_start_temp_xray", side_effect=self._start_ok),
            mock.patch.object(st, "_probe_reachable", new_callable=AsyncMock,
                              return_value=(True, 42, "google")),
            mock.patch.object(st, "_measure_download", new_callable=AsyncMock,
                              return_value=(123.4, 200.0)),
        ):
            result = asyncio.run(st.speedtest_node(_node()))

        assert result["reachable"] is True
        assert result["download_mbps"] == 123.4   # avg after warm-up
        assert result["max_mbps"] == 200.0        # peak steady window
        assert result["latency_ms"] == 42
        assert result.get("error") is None

    def test_reachable_but_all_targets_dry(self):
        # 204 works but every speed target yields nothing → reachable, no speed.
        with (
            mock.patch.object(st, "_resolve_chain", new_callable=AsyncMock, return_value=[_node()]),
            mock.patch.object(st.HealthChecker, "_resolve_direct",
                              new_callable=AsyncMock, return_value="1.2.3.4"),
            mock.patch.object(st, "_start_temp_xray", side_effect=self._start_ok),
            mock.patch.object(st, "_probe_reachable", new_callable=AsyncMock,
                              return_value=(True, 30, "cloudflare")),
            mock.patch.object(st, "_measure_download", new_callable=AsyncMock, return_value=None),
        ):
            result = asyncio.run(st.speedtest_node(_node()))

        assert result["reachable"] is True
        assert result["download_mbps"] is None
        assert result.get("error")


class TestReservedPorts:
    def test_speedtest_port_is_bindable_and_unique(self):
        ports = {st._reserve_local_port() for _ in range(5)}
        # A free-port probe must not hand out the same port twice in a row.
        assert len(ports) > 1
        for p in ports:
            assert 1024 < p <= 65535

    def test_probe_port_is_not_the_old_fixed_constant(self):
        from app.core.route_explain_probe import _reserve_local_port

        ports = {_reserve_local_port() for _ in range(5)}
        assert len(ports) > 1
        assert ports != {15359}

    def test_speedtest_port_falls_back_when_bind_fails(self):
        with mock.patch.object(st.socket, "socket", side_effect=OSError("no fd")):
            port = st._reserve_local_port()
        assert 19000 <= port <= 19999

    def test_probe_port_falls_back_when_bind_fails(self):
        from app.core import route_explain_probe as rep

        with mock.patch.object(rep.socket, "socket", side_effect=OSError("no fd")):
            assert rep._reserve_local_port() == 15359
