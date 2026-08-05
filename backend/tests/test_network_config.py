"""Tests for the routing self-loop guards (gateway == the box's own IP).

The recurring "a fresh device set its PiTun gateway to itself" footgun:
a box left on DHCP in gateway mode receives its own address as option 3,
so the default route points at itself and all outbound traffic dies.
These cover the three places we now catch it — read_state warning,
apply() rejection, probe_gateway() rejection — without touching a real
host (the network_config helpers are monkey-patched).
"""
import pytest

from app.core import network_apply as na
from app.core import network_config as nc


def _state(**kw) -> nc.NetworkState:
    base = dict(
        interface="eth0", manager="networkmanager", mode="dhcp",
        ip="192.168.1.50", cidr=24, gateway="192.168.1.1",
        dns=["1.1.1.1"], warnings=[],
    )
    base.update(kw)
    return nc.NetworkState(**base)


class TestReadStateSelfLoop:
    def _patch(self, monkeypatch, *, gateway, ip):
        monkeypatch.setattr(nc, "detect_manager", lambda: "networkmanager")
        monkeypatch.setattr(nc, "read_default_route", lambda: ("eth0", gateway))
        monkeypatch.setattr(nc, "read_interface_address", lambda i: (ip, 24))
        monkeypatch.setattr(nc, "read_dns_servers", lambda: ["1.1.1.1"])
        monkeypatch.setattr(nc, "detect_mode", lambda m, i: "dhcp")

    def test_self_loop_warns_first_and_skips_pitun_probe(self, monkeypatch):
        self._patch(monkeypatch, gateway="192.168.1.50", ip="192.168.1.50")
        # If the self-loop short-circuit works, _upstream_is_pitun is never
        # called — make it explode so a regression is loud.
        monkeypatch.setattr(nc, "_upstream_is_pitun",
                            lambda gw: (_ for _ in ()).throw(AssertionError("probed")))

        state = nc.read_state()

        assert state.warnings, "expected a self-loop warning"
        assert "self-loop" in state.warnings[0].lower()
        assert "192.168.1.50" in state.warnings[0]
        # The misleading "ANOTHER PiTun" text must NOT appear for a self-loop.
        assert not any("ANOTHER PiTun" in w for w in state.warnings)

    def test_distinct_gateway_runs_pitun_probe(self, monkeypatch):
        self._patch(monkeypatch, gateway="192.168.1.1", ip="192.168.1.50")
        monkeypatch.setattr(nc, "_upstream_is_pitun", lambda gw: True)

        state = nc.read_state()

        assert any("ANOTHER PiTun" in w for w in state.warnings)
        assert not any("self-loop" in w.lower() for w in state.warnings)

    def test_no_warning_when_gateway_is_the_router(self, monkeypatch):
        self._patch(monkeypatch, gateway="192.168.1.1", ip="192.168.1.50")
        monkeypatch.setattr(nc, "_upstream_is_pitun", lambda gw: False)

        state = nc.read_state()

        assert not any("self-loop" in w.lower() for w in state.warnings)


class TestApplyRejectsSelfLoop:
    def test_apply_gateway_equal_to_own_ip_raises(self, monkeypatch):
        monkeypatch.setattr(nc, "read_state", lambda: _state(ip="192.168.1.50"))

        with pytest.raises(na.NetworkApplyError) as exc:
            na.apply(na.ApplyRequest(gateway="192.168.1.50"))
        assert "self-loop" in str(exc.value).lower()

    def test_apply_distinct_gateway_passes_the_self_loop_guard(self, monkeypatch):
        # A different gateway must get PAST the self-loop check. We stop it
        # at the next gate (unsupported manager) so the test needs no real
        # host mutation — proving only that the self-loop guard didn't fire.
        monkeypatch.setattr(nc, "read_state",
                            lambda: _state(ip="192.168.1.50", manager="dhcpcd"))

        with pytest.raises(na.NetworkApplyError) as exc:
            na.apply(na.ApplyRequest(gateway="192.168.1.1"))
        assert "self-loop" not in str(exc.value).lower()
        assert "not supported" in str(exc.value).lower()


class TestProbeRejectsOwnIp:
    def test_probe_own_ip_is_unreachable_with_clear_message(self, monkeypatch):
        monkeypatch.setattr(nc, "read_state", lambda: _state(ip="192.168.1.50", cidr=24))

        res = na.probe_gateway("192.168.1.50")

        assert res["reachable"] is False
        assert "own address" in res["detail"].lower()

    def test_probe_other_ip_proceeds_past_self_check(self, monkeypatch):
        # A different in-subnet IP must not hit the own-address early return.
        # Force the host commands to "unreachable" so we don't shell out.
        monkeypatch.setattr(nc, "read_state", lambda: _state(ip="192.168.1.50", cidr=24))
        monkeypatch.setattr(
            nc, "host_run",
            lambda argv, **kw: type("R", (), {"returncode": 1, "stdout": "", "stderr": ""})(),
        )

        res = na.probe_gateway("192.168.1.1")

        assert res["reachable"] is False
        assert "own address" not in res["detail"].lower()
