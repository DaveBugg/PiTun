"""Router mode — phase 0 (inventory + mode gate).

Phase 0 changes no behaviour: it only teaches the box to count its own NICs
and to record which mode it should operate in. What's worth pinning is the
gate — router mode must be impossible to enable on hardware that can't do it,
because a setting the dataplane can never honour is worse than no setting.
"""
from unittest import mock

from app.core import network_config as nc


def _link(name, link_type="ether", **extra):
    d = {"ifname": name, "link_type": link_type, "address": "aa:bb:cc:dd:ee:ff",
         "operstate": "UP", "flags": ["BROADCAST", "MULTICAST", "UP", "LOWER_UP"]}
    d.update(extra)
    return d


class TestPhysicalFilter:
    def test_real_nics_pass(self):
        assert nc._is_physical("eth0", _link("eth0"))
        assert nc._is_physical("enp3s0", _link("enp3s0"))
        # USB dongles show up as enx<mac> — must not be filtered out.
        assert nc._is_physical("enxaabbccddeeff", _link("enxaabbccddeeff"))

    def test_virtual_and_container_interfaces_rejected(self):
        for name in ("lo", "docker0", "br-abc123", "veth1234", "xray0",
                     "wg0", "ppp0", "tun0", "tailscale0"):
            assert not nc._is_physical(name, _link(name)), name

    def test_vlan_subinterface_and_bridge_slave_rejected(self):
        # A VLAN sub-interface is a role *result*, not a role candidate.
        assert not nc._is_physical("eth0.835", _link("eth0.835"))
        # An interface enslaved to a bridge/bond isn't standalone either.
        assert not nc._is_physical("eth1", _link("eth1", master="br0"))

    def test_non_ethernet_link_rejected(self):
        assert not nc._is_physical("lo", _link("lo", link_type="loopback"))


class TestListInterfaces:
    def test_counts_only_physical_and_marks_default_route(self, monkeypatch):
        payload = [
            _link("lo", link_type="loopback"),
            _link("eth0"),
            _link("eth1"),
            _link("docker0"),
            _link("xray0"),
        ]
        import json as _json
        monkeypatch.setattr(
            nc.subprocess, "run",
            lambda *a, **k: mock.Mock(returncode=0, stdout=_json.dumps(payload)),
        )
        monkeypatch.setattr(nc, "read_default_route", lambda: ("eth0", "192.168.1.1"))
        monkeypatch.setattr(nc, "read_interface_address", lambda n: ("192.168.1.50", 24))

        ifaces = nc.list_interfaces()

        assert [i["name"] for i in ifaces] == ["eth0", "eth1"]
        assert ifaces[0]["is_default_route"] is True
        assert ifaces[1]["is_default_route"] is False
        assert ifaces[0]["carrier"] is True

    def test_survives_missing_ip_command(self, monkeypatch):
        def boom(*a, **k):
            raise FileNotFoundError("ip")
        monkeypatch.setattr(nc.subprocess, "run", boom)
        assert nc.list_interfaces() == []


class TestModeGate:
    """`operating_mode` is the only phase-0 setting, and the API is the real
    gate — the UI hiding the option is a convenience, not a guarantee."""

    def test_router_mode_rejected_on_single_nic(self, client, admin_user, auth_headers):
        with mock.patch("app.core.network_config.list_interfaces",
                        return_value=[{"name": "eth0"}]):
            resp = client.patch("/api/system/settings", headers=auth_headers,
                                json={"operating_mode": "router"})
        assert resp.status_code == 400
        assert "2 physical" in resp.json()["detail"]

    def test_router_mode_accepted_on_two_nics(self, client, admin_user, auth_headers):
        """Since phase 1 the mode arrives together with its port roles — the
        Settings page batches the whole form into one PATCH, and a router with
        no ports assigned is not a state worth persisting."""
        async def _noop_apply(_session):
            return {"mode": "router"}

        with mock.patch("app.core.network_config.list_interfaces",
                        return_value=[{"name": "eth0"}, {"name": "eth1"}]), \
             mock.patch("app.core.network_config.wifi_capabilities",
                        return_value={"wireless": False, "ap_capable": False,
                                      "modes": [], "bands": [], "detail": ""}), \
             mock.patch("app.core.router_mode.apply", _noop_apply):
            resp = client.patch("/api/system/settings", headers=auth_headers,
                                json={"operating_mode": "router",
                                      "wan_interface": "eth0",
                                      "lan_interface": "eth1"})
        assert resp.status_code in (200, 204), resp.text
        assert client.get("/api/system/settings",
                          headers=auth_headers).json()["operating_mode"] == "router"

    def test_gateway_mode_never_gated(self, client, admin_user, auth_headers):
        with mock.patch("app.core.network_config.list_interfaces", return_value=[]):
            resp = client.patch("/api/system/settings", headers=auth_headers,
                                json={"operating_mode": "gateway"})
        assert resp.status_code in (200, 204), resp.text

    def test_unknown_mode_rejected(self, client, admin_user, auth_headers):
        resp = client.patch("/api/system/settings", headers=auth_headers,
                            json={"operating_mode": "bridge"})
        assert resp.status_code == 400

    def test_default_is_gateway(self, client, admin_user, auth_headers):
        assert client.get("/api/system/settings",
                          headers=auth_headers).json()["operating_mode"] == "gateway"


class TestInterfacesEndpoint:
    def test_reports_router_capability(self, client, admin_user, auth_headers):
        with mock.patch("app.core.network_config.list_interfaces",
                        return_value=[{"name": "eth0"}, {"name": "eth1"}]):
            body = client.get("/api/network/interfaces", headers=auth_headers).json()
        assert body["count"] == 2
        assert body["router_capable"] is True

    def test_single_nic_is_not_router_capable(self, client, admin_user, auth_headers):
        with mock.patch("app.core.network_config.list_interfaces",
                        return_value=[{"name": "eth0"}]):
            body = client.get("/api/network/interfaces", headers=auth_headers).json()
        assert body["router_capable"] is False


class TestWifiCapability:
    """AP mode is a chipset property, not a property of "it's wireless".
    Getting this wrong means tearing down a working setup to start hostapd
    on hardware that can never run it."""

    _IW_AP = """Wiphy phy0
\tSupported interface modes:
\t\t * IBSS
\t\t * managed
\t\t * AP
\t\t * P2P-client
\tBand 1:
\tBand 2:
"""
    _IW_CLIENT_ONLY = """Wiphy phy0
\tSupported interface modes:
\t\t * managed
\tBand 1:
"""

    def test_non_wireless_is_not_ap_capable(self, monkeypatch):
        monkeypatch.setattr(nc.os.path, "exists", lambda p: False)
        monkeypatch.setattr(nc, "_phy_for", lambda i: None)
        cap = nc.wifi_capabilities("eth0")
        assert cap["wireless"] is False and cap["ap_capable"] is False

    def test_ap_capable_adapter(self, monkeypatch):
        monkeypatch.setattr(nc, "is_wireless", lambda i: True)
        monkeypatch.setattr(nc, "_phy_for", lambda i: "phy0")
        monkeypatch.setattr(nc, "host_run",
                            lambda *a, **k: mock.Mock(returncode=0, stdout=self._IW_AP, stderr=""))
        cap = nc.wifi_capabilities("wlan0")
        assert cap["ap_capable"] is True
        assert "AP" in cap["modes"] and "managed" in cap["modes"]
        assert cap["bands"] == ["Band 1", "Band 2"]

    def test_client_only_adapter_rejected(self, monkeypatch):
        monkeypatch.setattr(nc, "is_wireless", lambda i: True)
        monkeypatch.setattr(nc, "_phy_for", lambda i: "phy0")
        monkeypatch.setattr(nc, "host_run",
                            lambda *a, **k: mock.Mock(returncode=0, stdout=self._IW_CLIENT_ONLY, stderr=""))
        cap = nc.wifi_capabilities("wlan0")
        assert cap["ap_capable"] is False
        assert "client-only" in cap["detail"]

    def test_missing_iw_is_unknown_not_unsupported(self, monkeypatch):
        """A missing tool must never read as "your hardware can't do it" —
        that sends the operator chasing the wrong problem."""
        monkeypatch.setattr(nc, "is_wireless", lambda i: True)
        monkeypatch.setattr(nc, "_phy_for", lambda i: "phy0")
        monkeypatch.setattr(nc, "host_run",
                            lambda *a, **k: mock.Mock(returncode=127, stdout="", stderr="iw: not found"))
        cap = nc.wifi_capabilities("wlan0")
        assert cap["ap_capable"] is None
        assert "not installed" in cap["detail"]


class TestPortRoles:
    """WAN faces the ISP, LAN faces the home network. These are the checks that
    stop an unbuildable configuration from ever reaching the dataplane."""

    ETH = [{"name": "eth0"}, {"name": "eth1"}, {"name": "wlan0"}]
    WIRED = {"wireless": False, "ap_capable": False, "modes": [], "bands": [], "detail": ""}

    def _patch(self, client, headers, body, caps=None):
        async def _noop_apply(_session):
            return {"mode": "gateway"}

        # These assert the request validator, not the dataplane — the
        # orchestrator is covered separately in TestOrchestration.
        with mock.patch("app.core.network_config.list_interfaces", return_value=self.ETH), \
             mock.patch("app.core.network_config.wifi_capabilities",
                        return_value=caps or self.WIRED), \
             mock.patch("app.core.router_mode.apply", _noop_apply):
            return client.patch("/api/system/settings", headers=headers, json=body)

    def test_unknown_interface_rejected(self, client, admin_user, auth_headers):
        r = self._patch(client, auth_headers, {"wan_interface": "eth9"})
        assert r.status_code == 400
        assert "not a physical interface" in r.json()["detail"]

    def test_same_port_for_both_roles_rejected(self, client, admin_user, auth_headers):
        r = self._patch(client, auth_headers,
                        {"wan_interface": "eth0", "lan_interface": "eth0"})
        assert r.status_code == 400
        assert "cannot be the same port" in r.json()["detail"]

    def test_client_only_wifi_rejected_as_lan(self, client, admin_user, auth_headers):
        """Serving the LAN over WiFi means running an AP — refuse the radio
        that can't, instead of failing later when hostapd won't start."""
        caps = {"wireless": True, "ap_capable": False, "modes": ["managed"],
                "bands": [], "detail": "adapter is client-only"}
        r = self._patch(client, auth_headers,
                        {"wan_interface": "eth0", "lan_interface": "wlan0"}, caps)
        assert r.status_code == 400
        assert "client-only" in r.json()["detail"]

    def test_undetermined_wifi_rejected_as_lan(self, client, admin_user, auth_headers):
        """Unknown capability is not permission — but the message must point at
        the missing tool, not blame the hardware."""
        caps = {"wireless": True, "ap_capable": None, "modes": [], "bands": [],
                "detail": "`iw` is not installed"}
        r = self._patch(client, auth_headers,
                        {"wan_interface": "eth0", "lan_interface": "wlan0"}, caps)
        assert r.status_code == 400
        assert "iw" in r.json()["detail"]

    def test_ap_capable_wifi_allowed_as_lan(self, client, admin_user, auth_headers):
        caps = {"wireless": True, "ap_capable": True, "modes": ["managed", "AP"],
                "bands": ["Band 1"], "detail": "adapter supports AP mode"}
        r = self._patch(client, auth_headers,
                        {"wan_interface": "eth0", "lan_interface": "wlan0"}, caps)
        assert r.status_code in (200, 204), r.text

    def test_router_mode_without_roles_rejected(self, client, admin_user, auth_headers):
        r = self._patch(client, auth_headers, {"operating_mode": "router"})
        assert r.status_code == 400
        assert "WAN port" in r.json()["detail"] and "LAN port" in r.json()["detail"]

    def test_roles_can_be_set_while_staying_in_gateway_mode(
        self, client, admin_user, auth_headers,
    ):
        """Assigning roles is not the same as switching mode — the operator can
        prepare the configuration and flip the mode separately."""
        r = self._patch(client, auth_headers,
                        {"wan_interface": "eth0", "lan_interface": "eth1"})
        assert r.status_code in (200, 204), r.text
        got = client.get("/api/system/settings", headers=auth_headers).json()
        assert got["wan_interface"] == "eth0"
        assert got["lan_interface"] == "eth1"
        assert got["operating_mode"] == "gateway"


class TestRouterNat:
    """The forward chain is the security boundary in router mode: it must
    default to drop and let the WAN back in only via conntrack."""

    def _capture(self, monkeypatch):
        """Collect the nft scripts that would be applied."""
        from app.core import nftables as nft
        scripts = []

        async def fake_nft(script):
            scripts.append(script)
            return True

        monkeypatch.setattr(nft, "_nft", fake_nft)
        return nft, scripts

    def test_masquerades_out_of_the_wan_port(self, monkeypatch):
        import asyncio
        nft, scripts = self._capture(monkeypatch)
        assert asyncio.run(nft.apply_router_nat("eth0", "eth1")) is True
        body = "\n".join(scripts)
        assert 'oifname "eth0" masquerade' in body
        assert "type nat hook postrouting" in body

    def test_forward_defaults_to_drop_with_stateful_returns(self, monkeypatch):
        import asyncio
        nft, scripts = self._capture(monkeypatch)
        asyncio.run(nft.apply_router_nat("eth0", "eth1"))
        body = "\n".join(scripts)
        assert "type filter hook forward priority filter; policy drop" in body
        assert "ct state established,related accept" in body
        assert "ct state invalid drop" in body
        # LAN may reach the internet...
        assert 'iifname "eth1" oifname "eth0" accept' in body
        # ...but nothing grants WAN-initiated access into the LAN.
        assert 'iifname "eth0" oifname "eth1" accept' not in body

    def test_replaces_rather_than_stacking(self, monkeypatch):
        """A re-apply must not leave two copies of the ruleset behind."""
        import asyncio
        nft, scripts = self._capture(monkeypatch)
        asyncio.run(nft.apply_router_nat("eth0", "eth1"))
        assert any(s.strip().startswith("delete table inet pitun_router") for s in scripts)

    def test_rejects_bogus_interface_names(self, monkeypatch):
        import asyncio
        nft, scripts = self._capture(monkeypatch)
        assert asyncio.run(nft.apply_router_nat("eth0; rm -rf /", "eth1")) is False
        assert asyncio.run(nft.apply_router_nat("", "eth1")) is False
        assert asyncio.run(nft.apply_router_nat("e" * 20, "eth1")) is False
        assert scripts == [], "nothing should be applied for invalid input"

    def test_rejects_same_port_for_both_sides(self, monkeypatch):
        import asyncio
        nft, scripts = self._capture(monkeypatch)
        assert asyncio.run(nft.apply_router_nat("eth0", "eth0")) is False
        assert scripts == []

    def test_removal_is_idempotent(self, monkeypatch):
        import asyncio
        nft, scripts = self._capture(monkeypatch)
        assert asyncio.run(nft.remove_router_nat()) is True
        assert asyncio.run(nft.remove_router_nat()) is True

    def test_router_table_is_separate_from_the_tproxy_table(self, monkeypatch):
        """Applying router rules must never rewrite the proxy's own table."""
        import asyncio
        nft, scripts = self._capture(monkeypatch)
        asyncio.run(nft.apply_router_nat("eth0", "eth1"))
        body = "\n".join(scripts)
        assert "pitun_router" in body
        assert "table inet pitun " not in body and "table inet pitun\n" not in body


class TestDhcpConfig:
    """The generated dnsmasq config is what the whole LAN depends on, so the
    interesting assertions are semantic: what clients are told, and which
    misconfigurations are refused before a daemon ever sees them."""

    def _cfg(self, **over):
        from app.core.dhcp import DhcpConfig
        base = dict(interface="eth1", lan_cidr="192.168.10.0/24",
                    lan_address="192.168.10.1", pool_start="192.168.10.100",
                    pool_end="192.168.10.200", lease_hours=12)
        base.update(over)
        return DhcpConfig(**base)

    def test_dns_is_disabled_so_xray_keeps_port_53(self):
        from app.core.dhcp import render_dnsmasq_conf
        conf = render_dnsmasq_conf(self._cfg())
        assert "port=0" in conf.splitlines(), "dnsmasq must not serve DNS"

    def test_clients_are_pointed_at_pitun_for_gateway_and_dns(self):
        from app.core.dhcp import render_dnsmasq_conf
        conf = render_dnsmasq_conf(self._cfg())
        assert "dhcp-option=3,192.168.10.1" in conf   # router
        assert "dhcp-option=6,192.168.10.1" in conf   # DNS
        assert "dhcp-range=192.168.10.100,192.168.10.200,255.255.255.0,12h" in conf

    def test_binds_only_to_the_lan_port(self):
        """A DHCP server answering on the WAN side would be both useless and
        hostile to the ISP's network."""
        from app.core.dhcp import render_dnsmasq_conf
        conf = render_dnsmasq_conf(self._cfg())
        assert "interface=eth1" in conf
        assert "bind-interfaces" in conf

    def test_gateway_address_inside_the_pool_is_refused(self):
        """dnsmasq won't catch this; a client handed the gateway's own address
        breaks the LAN in a way that's miserable to diagnose."""
        from app.core.dhcp import render_dnsmasq_conf, DhcpConfigError
        import pytest
        with pytest.raises(DhcpConfigError, match="inside the DHCP pool"):
            render_dnsmasq_conf(self._cfg(lan_address="192.168.10.150"))

    def test_pool_outside_subnet_is_refused(self):
        from app.core.dhcp import render_dnsmasq_conf, DhcpConfigError
        import pytest
        with pytest.raises(DhcpConfigError, match="outside the LAN subnet"):
            render_dnsmasq_conf(self._cfg(pool_end="10.0.0.5"))

    def test_inverted_pool_is_refused(self):
        from app.core.dhcp import render_dnsmasq_conf, DhcpConfigError
        import pytest
        with pytest.raises(DhcpConfigError, match="range is empty"):
            render_dnsmasq_conf(
                self._cfg(pool_start="192.168.10.200", pool_end="192.168.10.100")
            )

    def test_reservations_outside_the_subnet_are_dropped_not_emitted(self):
        from app.core.dhcp import render_dnsmasq_conf, StaticLease
        conf = render_dnsmasq_conf(self._cfg(static_leases=[
            StaticLease(mac="aa:bb:cc:dd:ee:ff", ip="192.168.10.50", name="nas"),
            StaticLease(mac="11:22:33:44:55:66", ip="10.9.9.9", name="elsewhere"),
            StaticLease(mac="99:88:77:66:55:44", ip="not-an-ip"),
        ]))
        assert "dhcp-host=aa:bb:cc:dd:ee:ff,192.168.10.50,nas" in conf
        assert "10.9.9.9" not in conf
        assert "not-an-ip" not in conf

    def test_suggested_pool_never_collides_with_the_gateway(self):
        from app.core.dhcp import default_pool_for, render_dnsmasq_conf
        import ipaddress
        for gw in ("192.168.10.1", "192.168.10.254", "192.168.10.150"):
            start, end = default_pool_for("192.168.10.0/24", gw)
            g = ipaddress.ip_address(gw)
            assert not (ipaddress.ip_address(start) <= g <= ipaddress.ip_address(end)), gw
            # And the suggestion must actually render.
            render_dnsmasq_conf(self._cfg(
                lan_address=gw, pool_start=start, pool_end=end))


class TestWanExposure:
    """Router mode is the first time PiTun has an interface facing the
    internet. xray binds DNS/SOCKS/HTTP to 0.0.0.0, which was harmless when
    every interface faced the LAN and is an open resolver plus a pair of open
    proxies the moment there's an uplink. The uplink therefore accepts nothing
    new at all — replies only."""

    def _apply(self, monkeypatch, **kw):
        import asyncio
        from app.core import nftables as nft
        scripts = []

        async def fake_nft(script):
            scripts.append(script)
            return True

        monkeypatch.setattr(nft, "_nft", fake_nft)
        asyncio.run(nft.apply_router_nat("eth0", "eth1", **kw))
        return "\n".join(scripts)

    def test_nothing_new_is_accepted_from_the_internet(self, monkeypatch):
        """One blanket rule instead of a port list — a list has to be
        maintained, and a forgotten port is an exposed service."""
        body = self._apply(monkeypatch)
        assert 'iifname "eth0" ct state new counter name "wan_blocked" drop' in body
        assert "ct state established,related accept" in body
        assert "ct state invalid drop" in body

    def test_dhcp_client_replies_still_get_in(self, monkeypatch):
        """They arrive as NEW rather than RELATED, so the blanket drop would
        otherwise stop the uplink from ever getting an address."""
        body = self._apply(monkeypatch)
        assert 'iifname "eth0" udp sport 67 udp dport 68 counter name "wan_dhcp_in" accept' in body

    def test_pmtu_and_traceroute_icmp_survive(self, monkeypatch):
        """Dropping these makes connections hang instead of fail — one of the
        worst failure modes to diagnose."""
        body = self._apply(monkeypatch)
        assert "destination-unreachable" in body and "time-exceeded" in body

    def test_input_policy_stays_accept_and_drops_are_wan_scoped(self, monkeypatch):
        """A drop policy would cut the LAN path too. Every restriction is
        bound to the WAN interface so LAN administration always survives."""
        body = self._apply(monkeypatch)
        assert "type filter hook input priority filter; policy accept" in body
        input_chain = body.split("chain input")[1].split("chain forward")[0]
        for line in input_chain.splitlines():
            if "drop" in line and not line.strip().startswith("#"):
                assert 'iifname "eth0"' in line or "ct state invalid" in line, line

    def test_published_ports_can_be_opened_explicitly(self, monkeypatch):
        body = self._apply(monkeypatch, wan_allow_tcp=[51820, 443], wan_allow_udp=[51821])
        assert 'iifname "eth0" tcp dport { 443, 51820 } accept' in body
        assert 'iifname "eth0" udp dport { 51821 } accept' in body

    def test_out_of_range_ports_are_ignored(self, monkeypatch):
        body = self._apply(monkeypatch, wan_allow_tcp=[443, 0, 70000, -1])
        assert "{ 443 }" in body
        for bad in ("70000", "-1"):
            assert bad not in body


class TestOrchestration:
    """Router mode is all-or-nothing. A box that NATs but hands out no
    addresses — or hands out addresses pointing at a gateway that doesn't
    forward — is broken in a way that reads as "the internet died" to
    everyone on the LAN, so a partial apply must roll all the way back."""

    def _mod(self, monkeypatch, *, nat_ok=True, dhcp_raises=None, forward_ok=True):
        import asyncio
        from app.core import router_mode as rm
        calls = []

        async def fake_nat(wan, lan, **kw):
            calls.append(("nat", wan, lan))
            return nat_ok

        async def fake_remove():
            calls.append(("nat_removed",))
            return True

        async def fake_dhcp_start(cfg):
            calls.append(("dhcp_start", cfg.interface))
            if dhcp_raises:
                raise dhcp_raises
            return {"running": True}

        async def fake_dhcp_stop():
            calls.append(("dhcp_stop",))
            return {"running": False}

        monkeypatch.setattr(rm.nft, "apply_router_nat", fake_nat)
        monkeypatch.setattr(rm.nft, "remove_router_nat", fake_remove)
        monkeypatch.setattr(rm.dhcp_mod, "start", fake_dhcp_start)
        monkeypatch.setattr(rm.dhcp_mod, "stop", fake_dhcp_stop)
        monkeypatch.setattr(rm, "set_ip_forward", lambda on: forward_ok)
        monkeypatch.setattr(rm.nc, "list_interfaces",
                            lambda: [{"name": "eth0"}, {"name": "eth1"}])
        monkeypatch.setattr(rm.nc, "read_interface_address",
                            lambda i: ("192.168.10.1", 24))
        return rm, calls, asyncio

    class _Session:
        """Serves settings rows; every other query (devices, for DHCP
        reservations) comes back empty."""

        def __init__(self, mapping, devices=()):
            self._m = mapping
            self._devices = list(devices)

        async def exec(self, statement=None, *_a, **_kw):
            if statement is not None and "device" in str(statement).lower():
                rows = self._devices
            else:
                rows = [type("R", (), {"key": k, "value": v})()
                        for k, v in self._m.items()]
            return type("Res", (), {"all": lambda self: rows})()

    def test_gateway_mode_tears_everything_down(self, monkeypatch):
        rm, calls, aio = self._mod(monkeypatch)
        res = aio.run(rm.apply(self._Session({"operating_mode": "gateway"})))
        assert res["mode"] == "gateway"
        assert ("dhcp_stop",) in calls and ("nat_removed",) in calls

    def test_full_apply_in_dependency_order(self, monkeypatch):
        rm, calls, aio = self._mod(monkeypatch)
        res = aio.run(rm.apply(self._Session({
            "operating_mode": "router", "wan_interface": "eth0",
            "lan_interface": "eth1",
        })))
        assert res["mode"] == "router"
        assert res["applied"] == ["ip_forward", "nat", "dhcp"]
        # A pool was chosen automatically and can't contain the gateway.
        assert res["dhcp_pool"][0] != "192.168.10.1"

    def test_dhcp_failure_rolls_back_the_firewall(self, monkeypatch):
        """The dangerous case: NAT is up, DHCP isn't. Leaving that in place
        means a LAN that can't get addresses but whose traffic is being
        rewritten — undo it."""
        import pytest
        rm, calls, aio = self._mod(monkeypatch, dhcp_raises=RuntimeError("no image"))
        with pytest.raises(rm.RouterModeError):
            aio.run(rm.apply(self._Session({
                "operating_mode": "router", "wan_interface": "eth0",
                "lan_interface": "eth1",
            })))
        assert ("nat_removed",) in calls, "firewall must be removed on rollback"

    def test_nat_failure_stops_before_dhcp_starts(self, monkeypatch):
        import pytest
        rm, calls, aio = self._mod(monkeypatch, nat_ok=False)
        with pytest.raises(rm.RouterModeError):
            aio.run(rm.apply(self._Session({
                "operating_mode": "router", "wan_interface": "eth0",
                "lan_interface": "eth1",
            })))
        assert not any(c[0] == "dhcp_start" for c in calls)

    def test_missing_roles_refused_before_touching_anything(self, monkeypatch):
        import pytest
        rm, calls, aio = self._mod(monkeypatch)
        with pytest.raises(rm.RouterModeError, match="WAN and a LAN"):
            aio.run(rm.apply(self._Session({"operating_mode": "router"})))
        assert calls == []

    def test_lan_without_an_address_is_refused(self, monkeypatch):
        """The LAN port has to *be* the gateway, so it needs an address."""
        import pytest
        rm, calls, aio = self._mod(monkeypatch)
        monkeypatch.setattr(rm.nc, "read_interface_address", lambda i: (None, None))
        with pytest.raises(rm.RouterModeError, match="no IPv4 address"):
            aio.run(rm.apply(self._Session({
                "operating_mode": "router", "wan_interface": "eth0",
                "lan_interface": "eth1",
            })))
        assert calls == []


class TestWanDiagnosis:
    """Turning the two silent failures into something readable."""

    def _diag(self, monkeypatch, counters):
        import asyncio
        from app.core import router_mode as rm

        async def fake_counters():
            return counters

        monkeypatch.setattr(rm.nft, "router_counters", fake_counters)
        return asyncio.run(rm.diagnose_wan())

    def test_no_dhcp_replies_is_called_out(self, monkeypatch):
        out = self._diag(monkeypatch, {
            "wan_dhcp_in": {"packets": 0}, "wan_icmp_in": {"packets": 5},
            "wan_blocked": {"packets": 100},
        })
        dhcp = [f for f in out if "DHCP" in f["title"]][0]
        assert dhcp["level"] == "warn"

    def test_pmtu_blackhole_is_explained_by_symptom(self, monkeypatch):
        """The symptom — big transfers hang while small ones work — is what
        the operator actually observes, so that's what we describe."""
        out = self._diag(monkeypatch, {
            "wan_dhcp_in": {"packets": 3}, "wan_icmp_in": {"packets": 0},
            "wan_blocked": {"packets": 10},
        })
        icmp = [f for f in out if "ICMP" in f["title"]][0]
        assert icmp["level"] == "warn"
        assert "hang" in icmp["detail"]

    def test_healthy_uplink_reports_ok(self, monkeypatch):
        out = self._diag(monkeypatch, {
            "wan_dhcp_in": {"packets": 4}, "wan_icmp_in": {"packets": 9},
            "wan_blocked": {"packets": 2000},
        })
        assert all(f["level"] == "ok" for f in out)

    def test_blocked_count_is_not_alarming(self, monkeypatch):
        """Background scanning from the internet is constant and normal —
        reporting it as a problem would train the operator to ignore us."""
        out = self._diag(monkeypatch, {
            "wan_dhcp_in": {"packets": 1}, "wan_icmp_in": {"packets": 1},
            "wan_blocked": {"packets": 999999},
        })
        blocked = [f for f in out if "dropped" in f["title"]][0]
        assert blocked["level"] == "ok"

    def test_inactive_router_mode_is_not_an_error(self, monkeypatch):
        out = self._diag(monkeypatch, {})
        assert len(out) == 1 and out[0]["level"] == "ok"


class TestTeardownIsAlwaysPossible:
    """Gateway is the state that keeps the LAN working, so the way back must
    not be blocked by the very failure the operator is escaping."""

    def test_teardown_survives_a_broken_docker_socket(self, monkeypatch):
        import asyncio
        from app.core import router_mode as rm

        async def boom():
            raise RuntimeError("docker socket unreachable")

        async def ok():
            return True

        monkeypatch.setattr(rm.dhcp_mod, "stop", boom)
        monkeypatch.setattr(rm.nft, "remove_router_nat", ok)

        res = asyncio.run(rm.teardown())
        assert res["mode"] == "gateway"
        assert "error" in str(res["steps"]["dhcp"])
        # The firewall still came down even though DHCP teardown failed.
        assert res["steps"]["nat"] is True

    def test_teardown_survives_nft_failure_too(self, monkeypatch):
        import asyncio
        from app.core import router_mode as rm

        async def stopped():
            return {"running": False}

        async def boom():
            raise RuntimeError("nft missing")

        monkeypatch.setattr(rm.dhcp_mod, "stop", stopped)
        monkeypatch.setattr(rm.nft, "remove_router_nat", boom)

        res = asyncio.run(rm.teardown())
        assert res["mode"] == "gateway"
        assert res["steps"]["dhcp"] is True


class TestDhcpReservations:
    """Reservations are an explicit operator choice, not a promotion of the
    address ARP happened to observe — pinning an observation would fix an
    address the device merely held once, possibly outside the pool we serve."""

    def _cfg(self, leases):
        from app.core.dhcp import DhcpConfig
        return DhcpConfig(
            interface="eth1", lan_cidr="192.168.10.0/24",
            lan_address="192.168.10.1", pool_start="192.168.10.100",
            pool_end="192.168.10.200", lease_hours=12, static_leases=leases,
        )

    def test_reservation_for_the_gateway_address_is_dropped(self):
        """Same collision the pool check prevents, arriving by another route."""
        from app.core.dhcp import render_dnsmasq_conf, StaticLease
        conf = render_dnsmasq_conf(self._cfg([
            StaticLease(mac="aa:bb:cc:dd:ee:ff", ip="192.168.10.1", name="oops"),
            StaticLease(mac="11:22:33:44:55:66", ip="192.168.10.60", name="nas"),
        ]))
        assert "192.168.10.1," not in conf.replace("dhcp-option=3,192.168.10.1", "")
        assert "dhcp-host=11:22:33:44:55:66,192.168.10.60,nas" in conf

    def test_reservation_inside_the_pool_is_allowed(self):
        """dnsmasq honours a dhcp-host inside the range — that's the normal
        way to pin an address a device already uses."""
        from app.core.dhcp import render_dnsmasq_conf, StaticLease
        conf = render_dnsmasq_conf(self._cfg([
            StaticLease(mac="aa:bb:cc:dd:ee:ff", ip="192.168.10.150", name="tv"),
        ]))
        assert "dhcp-host=aa:bb:cc:dd:ee:ff,192.168.10.150,tv" in conf

    def test_bad_reservation_is_rejected_by_the_api(self, client, admin_user,
                                                    auth_headers, session):
        from app.models import Device
        d = Device(mac="aa:bb:cc:00:11:22", ip="192.168.1.5")
        session.add(d)
        session.commit()
        session.refresh(d)

        bad = client.patch(f"/api/devices/{d.id}", headers=auth_headers,
                           json={"dhcp_reserved_ip": "not-an-ip"})
        assert bad.status_code == 400
        assert "not a valid IPv4" in bad.json()["detail"]

        ok = client.patch(f"/api/devices/{d.id}", headers=auth_headers,
                          json={"dhcp_reserved_ip": "192.168.10.60"})
        assert ok.status_code in (200, 204), ok.text
        session.expire_all()
        assert session.get(Device, d.id).dhcp_reserved_ip == "192.168.10.60"

    def test_empty_string_clears_the_reservation(self, client, admin_user,
                                                 auth_headers, session):
        from app.models import Device
        d = Device(mac="bb:cc:dd:00:11:22", dhcp_reserved_ip="192.168.10.61")
        session.add(d)
        session.commit()
        session.refresh(d)

        r = client.patch(f"/api/devices/{d.id}", headers=auth_headers,
                         json={"dhcp_reserved_ip": ""})
        assert r.status_code in (200, 204), r.text
        session.expire_all()
        assert session.get(Device, d.id).dhcp_reserved_ip is None


class TestWifiConfig:
    """What goes on the air. Most of the ways this fails are hardware or
    regulatory, and they fail by producing silence rather than an error, so
    the checks live here rather than being discovered on a rooftop."""

    def _cfg(self, **over):
        from app.core.wifi import WifiConfig
        base = dict(interface="wlan0", ssid="PiTun", passphrase="correcthorse",
                    country="DE", band="2.4", channel=6)
        base.update(over)
        return WifiConfig(**base)

    def test_basic_wpa2_config(self):
        from app.core.wifi import render_hostapd_conf
        conf = render_hostapd_conf(self._cfg())
        assert "interface=wlan0" in conf
        assert "ssid=PiTun" in conf
        assert "wpa_key_mgmt=WPA-PSK" in conf
        assert "hw_mode=g" in conf

    def test_country_code_is_required(self):
        """Without it the radio comes up with no usable channels — the kind of
        failure that looks like broken hardware."""
        from app.core.wifi import render_hostapd_conf, WifiConfigError
        import pytest
        for bad in ("", "Deutschland", "d", "12"):
            with pytest.raises(WifiConfigError, match="country code"):
                render_hostapd_conf(self._cfg(country=bad))

    def test_regulatory_compliance_is_enabled(self):
        from app.core.wifi import render_hostapd_conf
        conf = render_hostapd_conf(self._cfg(country="NL"))
        assert "country_code=NL" in conf
        assert "ieee80211d=1" in conf

    def test_channel_must_belong_to_the_band(self):
        """A 5 GHz channel with hw_mode=g parses fine and then transmits
        nothing, which is the worst kind of misconfiguration."""
        from app.core.wifi import render_hostapd_conf, WifiConfigError
        import pytest
        with pytest.raises(WifiConfigError, match="not a 2.4 GHz channel"):
            render_hostapd_conf(self._cfg(band="2.4", channel=44))
        with pytest.raises(WifiConfigError, match="not a 5 GHz channel"):
            render_hostapd_conf(self._cfg(band="5", channel=6))

    def test_five_ghz_uses_the_right_hw_mode(self):
        from app.core.wifi import render_hostapd_conf
        conf = render_hostapd_conf(self._cfg(band="5", channel=36))
        assert "hw_mode=a" in conf and "channel=36" in conf

    def test_passphrase_length_is_a_protocol_limit(self):
        from app.core.wifi import render_hostapd_conf, WifiConfigError
        import pytest
        for bad in ("short", "x" * 64):
            with pytest.raises(WifiConfigError, match="8-63"):
                render_hostapd_conf(self._cfg(passphrase=bad))

    def test_ssid_is_measured_in_bytes(self):
        """A 32-emoji SSID is well under 32 characters and still too long for
        the driver."""
        from app.core.wifi import render_hostapd_conf, WifiConfigError
        import pytest
        render_hostapd_conf(self._cfg(ssid="x" * 32))          # exactly at the limit
        with pytest.raises(WifiConfigError, match="1-32 bytes"):
            render_hostapd_conf(self._cfg(ssid="😀" * 9))       # 36 bytes
        with pytest.raises(WifiConfigError, match="1-32 bytes"):
            render_hostapd_conf(self._cfg(ssid=""))

    def test_wpa3_transitional_keeps_wpa2_clients(self):
        """Requiring management-frame protection would lock out exactly the
        clients this mixed mode exists to support."""
        from app.core.wifi import render_hostapd_conf
        conf = render_hostapd_conf(self._cfg(security="wpa2wpa3"))
        assert "wpa_key_mgmt=WPA-PSK SAE" in conf
        assert "ieee80211w=1" in conf and "ieee80211w=2" not in conf

    def test_bridge_puts_wifi_on_the_wired_lan(self):
        """One subnet and one DHCP scope for wired and wireless — otherwise
        devices on either side can't see each other."""
        from app.core.wifi import render_hostapd_conf
        conf = render_hostapd_conf(self._cfg(bridge="br-lan"))
        assert "bridge=br-lan" in conf

    def test_passphrase_is_redacted_for_logs(self):
        from app.core.wifi import render_hostapd_conf, redact
        conf = render_hostapd_conf(self._cfg(passphrase="supersecret123"))
        assert "supersecret123" not in redact(conf)
        assert "wpa_passphrase=********" in redact(conf)


class TestLanBridge:
    """Bridging is the most disruptive step in router mode: enslaving a port
    moves its address, dropping every connection on it — including the session
    of whoever is running the command."""

    def _capture(self, monkeypatch, *, exists=False, fail_on=None):
        from app.core import wifi
        calls = []

        def fake_ip(*args, **kw):
            calls.append(" ".join(args))
            rc = 0
            if args[:3] == ("link", "show", wifi.BRIDGE_NAME):
                rc = 0 if exists else 1
            if fail_on and fail_on in " ".join(args):
                rc = 1
            return type("R", (), {"returncode": rc, "stdout": "", "stderr": ""})()

        monkeypatch.setattr(wifi, "_ip", fake_ip)
        return wifi, calls

    def test_address_moves_to_the_bridge(self, monkeypatch):
        wifi, calls = self._capture(monkeypatch)
        res = wifi.create_lan_bridge("eth1", "192.168.10.1/24")
        assert res["created"] is True
        joined = " | ".join(calls)
        assert "link add name br-lan type bridge" in joined
        assert "link set eth1 master br-lan" in joined
        assert "addr add 192.168.10.1/24 dev br-lan" in joined

    def test_bridge_is_created_before_the_address_is_flushed(self, monkeypatch):
        """Otherwise a failure leaves the LAN with no address anywhere."""
        wifi, calls = self._capture(monkeypatch)
        wifi.create_lan_bridge("eth1", "192.168.10.1/24")
        add_i = next(i for i, c in enumerate(calls) if c.startswith("link add name"))
        flush_i = next(i for i, c in enumerate(calls) if c.startswith("addr flush"))
        assert add_i < flush_i

    def test_failed_enslave_puts_the_address_back(self, monkeypatch):
        """Better a working un-bridged LAN than neither."""
        import pytest
        wifi, calls = self._capture(monkeypatch, fail_on="master br-lan")
        with pytest.raises(wifi.WifiConfigError):
            wifi.create_lan_bridge("eth1", "192.168.10.1/24")
        joined = " | ".join(calls)
        assert "addr add 192.168.10.1/24 dev eth1" in joined
        assert "link delete br-lan" in joined

    def test_existing_bridge_is_not_rebuilt(self, monkeypatch):
        wifi, calls = self._capture(monkeypatch, exists=True)
        res = wifi.create_lan_bridge("eth1", "192.168.10.1/24")
        assert res["created"] is False
        assert not any(c.startswith("addr flush") for c in calls)

    def test_removal_returns_the_address_to_the_wired_port(self, monkeypatch):
        wifi, calls = self._capture(monkeypatch, exists=True)
        wifi.remove_lan_bridge("eth1", "192.168.10.1/24")
        joined = " | ".join(calls)
        assert "link set eth1 nomaster" in joined
        assert "link delete br-lan" in joined
        assert "addr add 192.168.10.1/24 dev eth1" in joined

    def test_bogus_input_never_reaches_ip(self, monkeypatch):
        import pytest
        wifi, calls = self._capture(monkeypatch)
        for bad_if, bad_cidr in (
            ("eth1; rm -rf /", "192.168.10.1/24"),
            ("eth1", "not-a-cidr"),
            ("", "192.168.10.1/24"),
        ):
            with pytest.raises(wifi.WifiConfigError):
                wifi.create_lan_bridge(bad_if, bad_cidr)
        assert not any("rm -rf" in c for c in calls)


class TestSameRadioGuard:
    """Different netdevs on one phy look like separate interfaces but share
    the hardware — a radio can't be an AP for the LAN and a client on the WAN
    at the same time, and comparing names would miss it."""

    def _patch(self, client, headers, body, phys):
        async def _noop_apply(_session):
            return {"mode": "gateway"}

        with mock.patch("app.core.network_config.list_interfaces",
                        return_value=[{"name": "wlan0"}, {"name": "wlan1"}, {"name": "eth0"}]), \
             mock.patch("app.core.network_config.wifi_capabilities",
                        return_value={"wireless": True, "ap_capable": True,
                                      "modes": ["AP"], "bands": [], "detail": ""}), \
             mock.patch("app.core.network_config._phy_for", lambda i: phys.get(i)), \
             mock.patch("app.core.router_mode.apply", _noop_apply):
            return client.patch("/api/system/settings", headers=headers, json=body)

    def test_two_netdevs_on_one_phy_are_rejected(self, client, admin_user, auth_headers):
        r = self._patch(client, auth_headers,
                        {"wan_interface": "wlan0", "lan_interface": "wlan1"},
                        {"wlan0": "phy0", "wlan1": "phy0"})
        assert r.status_code == 400
        assert "same radio" in r.json()["detail"]

    def test_separate_radios_are_allowed(self, client, admin_user, auth_headers):
        r = self._patch(client, auth_headers,
                        {"wan_interface": "wlan0", "lan_interface": "wlan1"},
                        {"wlan0": "phy0", "wlan1": "phy1"})
        assert r.status_code in (200, 204), r.text

    def test_wired_wan_with_wireless_lan_is_fine(self, client, admin_user, auth_headers):
        r = self._patch(client, auth_headers,
                        {"wan_interface": "eth0", "lan_interface": "wlan0"},
                        {"wlan0": "phy0"})
        assert r.status_code in (200, 204), r.text


class TestWifiOrchestration:
    def test_wifi_on_a_wired_lan_is_refused(self, monkeypatch):
        """Enabling WiFi with a wired LAN port is a contradiction — say so
        instead of starting hostapd on an interface with no radio."""
        import asyncio, pytest
        from app.core import router_mode as rm

        async def ok_nat(*a, **k):
            return True

        async def noop():
            return {"running": False}

        monkeypatch.setattr(rm.nft, "apply_router_nat", ok_nat)
        monkeypatch.setattr(rm.nft, "remove_router_nat", lambda: noop())
        monkeypatch.setattr(rm.dhcp_mod, "start", lambda cfg: noop())
        monkeypatch.setattr(rm.dhcp_mod, "stop", noop)
        monkeypatch.setattr(rm.wifi_mod, "stop", noop)
        monkeypatch.setattr(rm, "set_ip_forward", lambda on: True)
        monkeypatch.setattr(rm.nc, "list_interfaces",
                            lambda: [{"name": "eth0"}, {"name": "eth1"}])
        monkeypatch.setattr(rm.nc, "read_interface_address", lambda i: ("192.168.10.1", 24))
        monkeypatch.setattr(rm.nc, "is_wireless", lambda i: False)

        class S:
            async def exec(self, statement=None, *a, **k):
                if statement is not None and "device" in str(statement).lower():
                    rows = []
                else:
                    rows = [type("R", (), {"key": k2, "value": v})() for k2, v in {
                        "operating_mode": "router", "wan_interface": "eth0",
                        "lan_interface": "eth1", "wifi_enabled": "true",
                    }.items()]
                return type("Res", (), {"all": lambda self: rows})()

        with pytest.raises(rm.RouterModeError, match="not a wireless adapter"):
            asyncio.run(rm.apply(S()))

    def test_teardown_stops_the_access_point(self, monkeypatch):
        import asyncio
        from app.core import router_mode as rm
        stopped = []

        async def stop_dhcp():
            return {"running": False}

        async def stop_wifi():
            stopped.append("wifi")
            return {"running": False}

        async def rm_nat():
            return True

        monkeypatch.setattr(rm.dhcp_mod, "stop", stop_dhcp)
        monkeypatch.setattr(rm.wifi_mod, "stop", stop_wifi)
        monkeypatch.setattr(rm.nft, "remove_router_nat", rm_nat)

        res = asyncio.run(rm.teardown())
        assert stopped == ["wifi"]
        assert res["steps"]["wifi"] is True
