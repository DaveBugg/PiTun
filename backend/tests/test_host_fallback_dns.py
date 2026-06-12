"""Tests for additive host fallback DNS (C — DNS page host resolver).

These don't touch a real host. They monkey-patch the network_config
helpers that apply_host_fallback_dns relies on (detect_manager,
host_run, read_default_route, host systemctl/read_file) and assert the
right mechanism is chosen and the right commands are issued.
"""
import pytest
from types import SimpleNamespace

from app.core import network_apply as na
from app.core import network_config as nc


def _ok(stdout="", returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


class TestValidation:
    def test_empty_is_noop(self):
        assert na.apply_host_fallback_dns([])["applied"] is False
        assert na.apply_host_fallback_dns(["", "  "])["applied"] is False

    def test_bad_ip_rejected(self):
        with pytest.raises(na.NetworkApplyError):
            na.apply_host_fallback_dns(["1.1.1.1", "not-an-ip"])


class TestResolvedPath:
    def test_resolved_writes_fallbackdns(self, monkeypatch):
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            return _ok()

        monkeypatch.setattr(nc, "host_systemctl_is_active",
                            lambda s: s == "systemd-resolved")
        monkeypatch.setattr(nc, "host_read_file", lambda p: None)
        monkeypatch.setattr(nc, "host_run", fake_run)

        res = na.apply_host_fallback_dns(["1.1.1.1", "8.8.8.8"])
        assert res["applied"] is True
        assert res["manager"] == "systemd-resolved"
        # tee wrote the drop-in with FallbackDNS
        tee_calls = [c for c in calls if c and c[0] == "tee"]
        assert any("resolved.conf.d" in c[1] for c in tee_calls)
        # restarted resolved
        assert any(c[:2] == ["systemctl", "restart"] for c in calls)

    def test_resolved_idempotent_skips_restart(self, monkeypatch):
        calls = []
        existing = "[Resolve]\nFallbackDNS=1.1.1.1 8.8.8.8\n"
        monkeypatch.setattr(nc, "host_systemctl_is_active",
                            lambda s: s == "systemd-resolved")
        monkeypatch.setattr(nc, "host_read_file", lambda p: existing)
        monkeypatch.setattr(nc, "host_run",
                            lambda argv, **kw: calls.append(argv) or _ok())

        res = na.apply_host_fallback_dns(["1.1.1.1", "8.8.8.8"])
        assert res["applied"] is False
        assert not any(c[:2] == ["systemctl", "restart"] for c in calls)


class TestNetworkManagerPath:
    def _setup_nm(self, monkeypatch, current_dns=""):
        monkeypatch.setattr(nc, "host_systemctl_is_active",
                            lambda s: False)  # resolved inactive
        monkeypatch.setattr(nc, "detect_manager", lambda: "networkmanager")
        monkeypatch.setattr(nc, "read_default_route", lambda: ("eth0", "192.0.2.1"))
        monkeypatch.setattr(na, "_nm_connection_for_iface", lambda i: "Wired connection 1")

    def test_nm_appends_new_servers(self, monkeypatch):
        self._setup_nm(monkeypatch)
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            if argv[:2] == ["nmcli", "-g"]:
                return _ok(stdout="192.168.1.1")  # only router currently
            return _ok()

        monkeypatch.setattr(nc, "host_run", fake_run)
        res = na.apply_host_fallback_dns(["1.1.1.1", "8.8.8.8"])
        assert res["applied"] is True
        # modify call should carry +ipv4.dns with the new servers
        mod = next(c for c in calls if "connection" in c and "modify" in c)
        assert "+ipv4.dns" in mod
        joined = mod[mod.index("+ipv4.dns") + 1]
        assert "1.1.1.1" in joined and "8.8.8.8" in joined
        # fast-fallback options always asserted
        assert "ipv4.dns-options" in mod
        # connection reactivated
        assert any(c[:3] == ["nmcli", "connection", "up"] for c in calls)

    def test_nm_idempotent_when_all_present(self, monkeypatch):
        self._setup_nm(monkeypatch)
        calls = []

        def fake_run(argv, **kw):
            calls.append(argv)
            if argv[:2] == ["nmcli", "-g"]:
                return _ok(stdout="192.168.1.1,1.1.1.1,8.8.8.8")
            return _ok()

        monkeypatch.setattr(nc, "host_run", fake_run)
        res = na.apply_host_fallback_dns(["1.1.1.1", "8.8.8.8"])
        assert res["applied"] is False
        # modify must NOT contain +ipv4.dns (nothing new to add)
        mods = [c for c in calls if "modify" in c]
        assert all("+ipv4.dns" not in c for c in mods)


class TestResolvconfPath:
    def test_appends_to_resolvconf_when_no_manager(self, monkeypatch):
        monkeypatch.setattr(nc, "host_systemctl_is_active", lambda s: False)
        monkeypatch.setattr(nc, "detect_manager", lambda: "unknown")
        monkeypatch.setattr(nc, "host_read_file",
                            lambda p: "nameserver 192.168.1.1\n")
        calls = []
        monkeypatch.setattr(nc, "host_run",
                            lambda argv, **kw: calls.append(argv) or _ok())

        res = na.apply_host_fallback_dns(["1.1.1.1"])
        assert res["applied"] is True
        assert res["manager"] == "resolvconf"
        tee = next(c for c in calls if c and c[0] == "tee")
        assert tee[1] == "/etc/resolv.conf"

    def test_resolvconf_idempotent(self, monkeypatch):
        monkeypatch.setattr(nc, "host_systemctl_is_active", lambda s: False)
        monkeypatch.setattr(nc, "detect_manager", lambda: "unknown")
        monkeypatch.setattr(nc, "host_read_file",
                            lambda p: "nameserver 192.168.1.1\nnameserver 1.1.1.1\n")
        calls = []
        monkeypatch.setattr(nc, "host_run",
                            lambda argv, **kw: calls.append(argv) or _ok())
        res = na.apply_host_fallback_dns(["1.1.1.1"])
        assert res["applied"] is False
        assert not any(c and c[0] == "tee" for c in calls)
