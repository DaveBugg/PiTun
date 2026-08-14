"""Router mode — phase 0 (inventory + mode gate).

Phase 0 changes no behaviour: it only teaches the box to count its own NICs
and to record which mode it should operate in. What's worth pinning is the
gate — router mode must be impossible to enable on hardware that can't do it,
because a setting the dataplane can never honour is worse than no setting.
"""
from unittest import mock

from app.core import network_config as nc


def _async_value(value):
    """An awaitable returning `value` — for stubbing async helpers."""
    async def _coro(*_a, **_kw):
        return value
    return _coro()


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
        # Being enslaved is NOT disqualifying. Router mode puts the LAN ports
        # into its own bridge, and hiding them at that moment made the panel
        # report one port on a three-port box — and made role validation
        # reject the stored config, including the request to leave router
        # mode. The bridge is reported as `master` instead.
        assert nc._is_physical("eth1", _link("eth1", master="br0"))

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

    def test_replaces_atomically_rather_than_stacking(self, monkeypatch):
        """A re-apply must not leave two copies of the ruleset behind — and it
        must not leave *none* either. As a separate `delete` followed by a
        load, a script that failed to parse left the box with no firewall at
        all, which is worse than either version of it."""
        import asyncio
        nft, scripts = self._capture(monkeypatch)
        asyncio.run(nft.apply_router_nat("eth0", "eth1"))

        assert len(scripts) == 1, "replacing the ruleset must be one transaction"
        body = scripts[0]
        # Declaring the table empty first makes the delete valid even on the
        # very first apply, so it isn't an error on a fresh box.
        create_empty = body.index("table inet pitun_router {}")
        delete = body.index("delete table inet pitun_router")
        rules = body.index("chain postrouting")
        assert create_empty < delete < rules

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

    def test_removal_is_idempotent_when_the_table_is_absent(self, monkeypatch):
        """Teardown runs on every boot, including on gateway-mode boxes where
        the table never existed — that must be quiet success, not an error."""
        import asyncio
        nft, scripts = self._capture(monkeypatch)
        # rc=1 from `nft list table` is how an absent table reports itself.
        monkeypatch.setattr(nft, "_run_exec",
                            lambda *a: _async_value((1, "", "No such file or directory")))
        assert asyncio.run(nft.remove_router_nat()) is True
        assert asyncio.run(nft.remove_router_nat()) is True
        assert scripts == [], "an absent table must not be deleted again"

    def test_removal_reports_failure_instead_of_claiming_success(self, monkeypatch):
        """Teardown is the escape hatch; telling it the firewall came down when
        it did not is the one lie it cannot afford."""
        import asyncio
        from app.core import nftables as nft

        async def present(*_a):
            return (0, f"table inet pitun_router {{}}", "")

        async def refuse(_script):
            return False

        monkeypatch.setattr(nft, "_run_exec", present)
        monkeypatch.setattr(nft, "_nft", refuse)
        assert asyncio.run(nft.remove_router_nat()) is False

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
        hostile to the ISP's network.

        `bind-dynamic` rather than `bind-interfaces`: DHCP starts before the
        access point, and hostapd taking the radio bounces the link. A
        bind-interfaces dnsmasq keeps the dead socket and never rebinds while
        the container still reports "running" — DHCP silently gone on exactly
        the wifi-LAN path that has never been exercised.
        """
        from app.core.dhcp import render_dnsmasq_conf
        conf = render_dnsmasq_conf(self._cfg())
        assert "interface=eth1" in conf
        assert "bind-dynamic" in conf
        assert "bind-interfaces" not in conf

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
        async def fake_wifi_stop():
            calls.append(("wifi_stop",))
            return {"running": False}

        monkeypatch.setattr(rm.dhcp_mod, "start", fake_dhcp_start)
        monkeypatch.setattr(rm.dhcp_mod, "stop", fake_dhcp_stop)
        # apply() now stops a subsystem that is switched off, so the wifi path
        # is exercised even when wifi_enabled is false.
        monkeypatch.setattr(rm.wifi_mod, "stop", fake_wifi_stop)
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

    def test_auto_channel_never_asks_hostapd_to_choose(self):
        """`channel=0` turns on hostapd's ACS, which needs noise-floor data
        the driver may not report (mt7921 doesn't). hostapd then loops
        "insufficient survey data" forever WITHOUT exiting, so the container
        stays healthy while nothing is on the air. Verified on real hardware:
        channel 0 never associates, channel 6 reaches AP-ENABLED."""
        from app.core.wifi import render_hostapd_conf
        conf = render_hostapd_conf(self._cfg(band="2.4", channel=0))
        assert "channel=0" not in conf
        assert "channel=6" in conf

        conf5 = render_hostapd_conf(self._cfg(band="5", channel=0))
        assert "channel=0" not in conf5
        assert "channel=36" in conf5

    def test_an_explicit_channel_is_left_alone(self):
        from app.core.wifi import render_hostapd_conf
        assert "channel=11" in render_hostapd_conf(self._cfg(band="2.4", channel=11))

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


class TestAccessPointReallyOnTheAir:
    """"The process is alive" is not "there is a WiFi network". hostapd can
    stay running while the radio never leaves managed mode, which is what a
    stuck channel selection looks like: healthy container, no SSID. The
    orchestrator rolls back on failure, so it has to be told."""

    def test_radio_probe_reports_ap_mode(self, monkeypatch):
        from app.core import wifi as w

        def fake_run(argv, **kw):
            assert argv[:2] == ["iw", "dev"]
            return mock.Mock(returncode=0, stdout="\ttype AP\n\tssid PiTun\n")

        monkeypatch.setattr("app.core.network_config.host_run", fake_run)
        assert w._radio_is_ap("wlan0", timeout=2.0) is True

    def test_radio_probe_gives_up_when_it_stays_managed(self, monkeypatch):
        from app.core import wifi as w
        monkeypatch.setattr(
            "app.core.network_config.host_run",
            lambda argv, **kw: mock.Mock(returncode=0, stdout="\ttype managed\n"),
        )
        assert w._radio_is_ap("wlan0", timeout=2.0) is False

    def test_start_fails_when_the_radio_never_reaches_ap_mode(self, monkeypatch):
        import pytest
        from app.core import wifi as w

        container = mock.Mock()
        container.status = "running"
        container.logs.return_value = b"ACS: Channel 1 has insufficient survey data"
        client = mock.Mock()
        client.containers.run.return_value = container

        monkeypatch.setattr(w, "write_conf", lambda *a, **k: None)
        monkeypatch.setattr(w, "_docker_client", lambda: client)
        monkeypatch.setattr(w, "_radio_is_ap", lambda *a, **k: False)

        cfg = w.WifiConfig(interface="wlan0", ssid="PiTun",
                           passphrase="correcthorse", country="DE",
                           band="2.4", channel=6)
        with pytest.raises(w.WifiConfigError, match="never entered AP mode"):
            w._start_sync(cfg)
        # A half-started AP must not be left behind for the next attempt.
        container.remove.assert_called_once()


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

        with pytest.raises(rm.RouterModeError, match="no LAN port is a wireless adapter"):
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


class TestWanConfig:
    """Uplink acquisition. The failures here are the ones that look like a
    dead line rather than a configuration error."""

    def _cfg(self, **over):
        from app.core.wan import WanConfig
        base = dict(interface="eth0", mode="dhcp")
        base.update(over)
        return WanConfig(**base)

    def test_effective_interface_follows_pppoe_and_vlan(self):
        """NAT keyed to the physical port while traffic leaves on ppp0 gives a
        router that forwards nothing, with nothing to explain it."""
        from app.core.wan import effective_interface
        assert effective_interface(self._cfg()) == "eth0"
        assert effective_interface(self._cfg(vlan_id=835)) == "eth0.835"
        assert effective_interface(self._cfg(mode="pppoe", username="u", password="p")) == "ppp0"
        # PPPoE wins: the session runs over the tagged link, and traffic still
        # leaves on ppp0.
        assert effective_interface(
            self._cfg(mode="pppoe", vlan_id=835, username="u", password="p")) == "ppp0"

    def test_vlan_id_range(self):
        from app.core.wan import validate, WanConfigError
        import pytest
        for bad in (4095, 9999, -1):
            with pytest.raises(WanConfigError, match="1-4094"):
                validate(self._cfg(vlan_id=bad))
        validate(self._cfg(vlan_id=1))
        validate(self._cfg(vlan_id=4094))

    def test_static_gateway_must_be_reachable(self):
        """An unreachable gateway surfaces as "no internet" long after this
        screen, so it's caught while the operator is still looking at it."""
        from app.core.wan import validate, WanConfigError
        import pytest
        with pytest.raises(WanConfigError, match="outside the WAN subnet"):
            validate(self._cfg(mode="static", address="203.0.113.7/24",
                               gateway="198.51.100.1"))
        validate(self._cfg(mode="static", address="203.0.113.7/24",
                           gateway="203.0.113.1"))

    def test_static_address_needs_a_prefix(self):
        from app.core.wan import validate, WanConfigError
        import pytest
        with pytest.raises(WanConfigError, match="prefix"):
            validate(self._cfg(mode="static", address="203.0.113.7",
                               gateway="203.0.113.1"))

    def test_pppoe_requires_credentials(self):
        from app.core.wan import validate, WanConfigError
        import pytest
        with pytest.raises(WanConfigError, match="username"):
            validate(self._cfg(mode="pppoe", password="p"))
        with pytest.raises(WanConfigError, match="password"):
            validate(self._cfg(mode="pppoe", username="u"))

    def test_mac_clone_must_look_like_a_mac(self):
        from app.core.wan import validate, WanConfigError
        import pytest
        with pytest.raises(WanConfigError, match="not a MAC"):
            validate(self._cfg(mac_clone="00-11-22-33-44-55"))
        validate(self._cfg(mac_clone="00:11:22:33:44:55"))

    def test_password_never_appears_in_the_loggable_view(self):
        from app.core.wan import redact
        cfg = self._cfg(mode="pppoe", username="user@isp", password="hunter2")
        out = redact(cfg)
        assert out["password"] == "********"
        assert "hunter2" not in str(out)


class TestWanApply:
    def _apply(self, monkeypatch, cfg, *, manager="networkmanager", fail_on=None):
        from app.core import wan
        calls = []

        def fake_nmcli(*args, **kw):
            calls.append(" ".join(args))
            rc = 1 if (fail_on and fail_on in " ".join(args)) else 0
            return type("R", (), {"returncode": rc, "stdout": "", "stderr": "nope"})()

        monkeypatch.setattr(wan, "_nmcli", fake_nmcli)
        monkeypatch.setattr(wan, "detect_manager", lambda: manager)
        return wan, calls

    def test_non_networkmanager_says_so_plainly(self, monkeypatch):
        """Rather than half-working: networkd cannot do PPPoE at all."""
        import pytest
        from app.core.wan import WanConfig
        wan, _ = self._apply(monkeypatch, None, manager="networkd")
        with pytest.raises(wan.WanConfigError, match="NetworkManager"):
            wan.apply(WanConfig(interface="eth0", mode="dhcp"))

    def test_old_connection_is_replaced_not_stacked(self, monkeypatch):
        """Two profiles claiming the same port come up as whichever NM picked,
        which is worse to debug than a clean failure."""
        from app.core.wan import WanConfig
        wan, calls = self._apply(monkeypatch, None)
        wan.apply(WanConfig(interface="eth0", mode="dhcp"))
        assert any(c.startswith("connection delete pitun-wan") for c in calls)
        delete_i = next(i for i, c in enumerate(calls) if "delete pitun-wan" in c)
        add_i = next(i for i, c in enumerate(calls) if "connection add" in c)
        assert delete_i < add_i

    def test_vlan_carries_the_addressing(self, monkeypatch):
        """The addressing mode belongs on the tagged link, not the raw port."""
        from app.core.wan import WanConfig
        wan, calls = self._apply(monkeypatch, None)
        wan.apply(WanConfig(interface="eth0", mode="dhcp", vlan_id=835))
        joined = " | ".join(calls)
        assert "type vlan" in joined and "id 835" in joined
        eth_conn = [c for c in calls if "type ethernet" in c][0]
        assert "ifname eth0.835" in eth_conn

    def test_static_passes_address_gateway_and_dns(self, monkeypatch):
        from app.core.wan import WanConfig
        wan, calls = self._apply(monkeypatch, None)
        wan.apply(WanConfig(interface="eth0", mode="static",
                            address="203.0.113.7/24", gateway="203.0.113.1",
                            dns=["1.1.1.1", "8.8.8.8"]))
        add = [c for c in calls if "connection add" in c][0]
        assert "ipv4.method manual" in add
        assert "ipv4.addresses 203.0.113.7/24" in add
        assert "ipv4.gateway 203.0.113.1" in add
        assert "ipv4.dns 1.1.1.1,8.8.8.8" in add

    def test_mac_clone_is_set_on_the_connection(self, monkeypatch):
        """On the device it would be lost on reconnect, and an ISP that binds
        to a MAC refuses service the moment the original reappears."""
        from app.core.wan import WanConfig
        wan, calls = self._apply(monkeypatch, None)
        wan.apply(WanConfig(interface="eth0", mode="dhcp", mac_clone="00:11:22:33:44:55"))
        mod = [c for c in calls if "cloned-mac-address" in c]
        assert mod and "connection modify pitun-wan" in mod[0]

    def test_a_link_that_will_not_come_up_is_an_error(self, monkeypatch):
        import pytest
        from app.core.wan import WanConfig
        wan, calls = self._apply(monkeypatch, None, fail_on="connection up")
        with pytest.raises(wan.WanConfigError, match="would not come up"):
            wan.apply(WanConfig(interface="eth0", mode="dhcp"))


class TestWanInOrchestrator:
    """The uplink shapes everything downstream: NAT must follow the interface
    traffic actually leaves on, not the port the cable is in."""

    def _run(self, monkeypatch, settings_extra):
        import asyncio
        from app.core import router_mode as rm
        seen = {}

        async def fake_nat(wan, lan, **kw):
            seen["nat_wan"] = wan
            return True

        async def noop_dict():
            return {"running": False}

        monkeypatch.setattr(rm.nft, "apply_router_nat", fake_nat)
        monkeypatch.setattr(rm.nft, "remove_router_nat", lambda: noop_dict())
        monkeypatch.setattr(rm.dhcp_mod, "start", lambda cfg: noop_dict())
        monkeypatch.setattr(rm.dhcp_mod, "stop", noop_dict)
        monkeypatch.setattr(rm.wifi_mod, "stop", noop_dict)
        monkeypatch.setattr(rm, "set_ip_forward", lambda on: True)
        monkeypatch.setattr(rm.nc, "list_interfaces",
                            lambda: [{"name": "eth0"}, {"name": "eth1"}])
        monkeypatch.setattr(rm.nc, "read_interface_address", lambda i: ("192.168.10.1", 24))
        # Mirror the real contract: apply() returns a summary dict whose
        # "interface" is the link the session actually produced.
        def fake_wan_apply(cfg):
            seen["wan_applied"] = cfg.mode
            return {"mode": cfg.mode,
                    "interface": rm.wan_mod.effective_interface(cfg),
                    "steps": []}

        monkeypatch.setattr(rm.wan_mod, "apply", fake_wan_apply)

        base = {"operating_mode": "router", "wan_interface": "eth0",
                "lan_interface": "eth1"}
        base.update(settings_extra)

        class S:
            async def exec(self, statement=None, *a, **k):
                if statement is not None and "device" in str(statement).lower():
                    rows = []
                else:
                    rows = [type("R", (), {"key": k2, "value": v})()
                            for k2, v in base.items()]
                return type("Res", (), {"all": lambda self: rows})()

        return asyncio.run(rm.apply(S())), seen

    def test_plain_dhcp_uplink_is_left_alone(self, monkeypatch):
        """Replacing a working DHCP connection would drop the line for no
        gain — the host already does exactly this."""
        res, seen = self._run(monkeypatch, {})
        assert "wan" not in res["applied"]
        assert "wan_applied" not in seen
        assert seen["nat_wan"] == "eth0"

    def test_vlan_moves_nat_to_the_tagged_interface(self, monkeypatch):
        res, seen = self._run(monkeypatch, {"wan_vlan_id": "835"})
        assert seen["nat_wan"] == "eth0.835"
        assert res["wan_interface"] == "eth0.835"
        assert "wan" in res["applied"]

    def test_pppoe_moves_nat_to_ppp0(self, monkeypatch):
        res, seen = self._run(monkeypatch, {
            "wan_mode": "pppoe", "wan_pppoe_user": "u", "wan_pppoe_password": "p",
        })
        assert seen["nat_wan"] == "ppp0"
        assert res["wan_mode"] == "pppoe"

    def test_mac_clone_alone_still_builds_the_connection(self, monkeypatch):
        res, seen = self._run(monkeypatch, {"wan_mac_clone": "00:11:22:33:44:55"})
        assert "wan" in res["applied"]
        assert seen["nat_wan"] == "eth0"

    def test_bad_wan_settings_fail_before_anything_is_applied(self, monkeypatch):
        import pytest
        from app.core import router_mode as rm
        with pytest.raises(rm.RouterModeError, match="WAN settings are not usable"):
            self._run(monkeypatch, {"wan_mode": "static",
                                    "wan_static_address": "203.0.113.7",
                                    "wan_static_gateway": "203.0.113.1"})


class TestWatchdog:
    """Gateway mode has a fallback — point devices back at the router. Router
    mode has none, so a bad apply needs an undo that doesn't depend on anyone
    being able to reach the box."""

    def _session(self):
        import asyncio
        from app.database import get_async_engine
        from sqlmodel.ext.asyncio.session import AsyncSession
        return AsyncSession, get_async_engine, asyncio

    def test_arm_then_confirm_clears_the_deadline(self, client, admin_user,
                                                  auth_headers, session):
        import asyncio
        from app.core import router_watchdog as w
        from app.models import Settings as DBSettings
        from sqlmodel import select

        async def go():
            from sqlmodel.ext.asyncio.session import AsyncSession
            from app.database import get_async_engine
            async with AsyncSession(get_async_engine()) as s:
                await w.arm(s, 120)
                assert await w.pending(s) is not None
                assert await w.confirm(s) is True
                assert await w.pending(s) is None
                # A second confirm is a no-op, not an error.
                assert await w.confirm(s) is False

        asyncio.run(go())

    def test_expired_window_reverts_to_gateway(self, monkeypatch):
        """The whole point: nobody confirmed, so the box goes back to the mode
        that keeps the LAN working."""
        import asyncio
        from app.core import router_watchdog as w

        reverted = {}

        async def fake_teardown():
            reverted["torn_down"] = True
            return {"mode": "gateway"}

        async def fake_get(session, key):
            from datetime import datetime, timedelta, timezone
            if key == w.PENDING_KEY:
                return (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
            return ""

        sets = {}

        async def fake_set(session, key, value):
            sets[key] = value

        import app.core.router_mode as rm
        monkeypatch.setattr(rm, "teardown", fake_teardown)
        monkeypatch.setattr(w, "_get", fake_get)
        monkeypatch.setattr(w, "_set", fake_set)

        async def go():
            class S:
                pass
            return await w.revert(S(), "test")

        asyncio.run(go())
        assert reverted["torn_down"] is True
        assert sets["operating_mode"] == "gateway"
        assert sets[w.PENDING_KEY] == ""

    def test_unparseable_deadline_is_treated_as_expired(self, monkeypatch):
        """Erring toward the safe mode: an unreadable deadline must not mean
        'pending forever'."""
        import asyncio
        from datetime import datetime, timezone
        from app.core import router_watchdog as w

        async def fake_get(session, key):
            return "not-a-date"

        monkeypatch.setattr(w, "_get", fake_get)

        async def go():
            class S:
                pass
            return await w.pending(S())

        result = asyncio.run(go())
        assert result is not None and result < datetime.now(timezone.utc)

    def test_confirm_endpoint_reports_nothing_pending(self, client, admin_user,
                                                      auth_headers):
        r = client.post("/api/network/router-mode/confirm", headers=auth_headers)
        assert r.status_code == 200
        assert r.json()["confirmed"] is False

    def test_pending_endpoint_counts_down(self, client, admin_user, auth_headers):
        import asyncio
        from app.core import router_watchdog as w

        async def go():
            from sqlmodel.ext.asyncio.session import AsyncSession
            from app.database import get_async_engine
            async with AsyncSession(get_async_engine()) as s:
                await w.arm(s, 120)

        asyncio.run(go())
        body = client.get("/api/network/router-mode/pending",
                          headers=auth_headers).json()
        assert body["pending"] is True
        assert 0 < body["seconds_left"] <= 120

        client.post("/api/network/router-mode/confirm", headers=auth_headers)
        after = client.get("/api/network/router-mode/pending",
                           headers=auth_headers).json()
        assert after["pending"] is False


class TestPppoeInterfaceIsRead:
    """The firewall matches interfaces by NAME, so a guessed ppp0 that turns
    out to be ppp1 loads cleanly and then matches nothing: no NAT, LAN egress
    hitting policy drop, and the WAN input filter inert."""

    def test_live_name_overrides_the_guess(self, monkeypatch):
        from app.core import wan
        monkeypatch.setattr(wan, "detect_manager", lambda: "networkmanager")
        monkeypatch.setattr(wan, "_nmcli",
                            lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
        monkeypatch.setattr(wan, "live_pppoe_interface", lambda: "ppp1")
        res = wan.apply(wan.WanConfig(interface="eth0", mode="pppoe",
                                      username="u", password="p"))
        assert res["interface"] == "ppp1"

    def test_no_ppp_link_is_an_error_not_a_guess(self, monkeypatch):
        """Reporting success while building the firewall around a name that
        doesn't exist would leave the uplink wide open."""
        import pytest
        from app.core import wan
        monkeypatch.setattr(wan, "detect_manager", lambda: "networkmanager")
        monkeypatch.setattr(wan, "_nmcli",
                            lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
        monkeypatch.setattr(wan, "live_pppoe_interface", lambda: None)
        with pytest.raises(wan.WanConfigError, match="no ppp interface"):
            wan.apply(wan.WanConfig(interface="eth0", mode="pppoe",
                                    username="u", password="p"))


class TestBackupRedactsSettingSecrets:
    """The settings section is key/value, so per-column redaction never
    reached it — the WiFi PSK and the ISP password were exported verbatim in
    the bundle the UI calls safe to share."""

    def test_secret_settings_are_blanked(self, client, admin_user, auth_headers, session):
        from app.models import Settings as DBSettings
        for k, v in (("wifi_passphrase", "wifi-secret-1"),
                     ("wan_pppoe_password", "isp-secret-2"),
                     ("wifi_ssid", "HomeNet")):
            session.add(DBSettings(key=k, value=v))
        session.commit()

        body = client.get("/api/system/backup", headers=auth_headers).text
        assert "wifi-secret-1" not in body
        assert "isp-secret-2" not in body
        # Non-secret settings must still round-trip.
        assert "HomeNet" in body

    def test_include_secrets_still_exports_them(self, client, admin_user, auth_headers, session):
        from app.models import Settings as DBSettings
        session.add(DBSettings(key="wifi_passphrase", value="wifi-secret-3"))
        session.commit()
        body = client.get("/api/system/backup", headers=auth_headers,
                          params={"include_secrets": True}).text
        assert "wifi-secret-3" in body


class TestSettingsGateAfterReview:
    """Regressions for defects the second review pass found — each one fails
    silently, so only a test keeps them fixed."""

    def _patch(self, client, headers, body, *, wireless=False):
        async def _noop_apply(_session):
            return {"mode": "gateway"}

        with mock.patch("app.core.network_config.list_interfaces",
                        return_value=[{"name": "eth0"}, {"name": "eth1"}]), \
             mock.patch("app.core.network_config.wifi_capabilities",
                        return_value={"wireless": wireless, "ap_capable": wireless,
                                      "modes": [], "bands": [], "detail": ""}), \
             mock.patch("app.core.network_config.is_wireless", lambda i: wireless), \
             mock.patch("app.core.router_mode.apply", _noop_apply):
            return client.patch("/api/system/settings", headers=headers, json=body)

    def test_empty_secret_means_unchanged_not_cleared(
        self, client, admin_user, auth_headers,
    ):
        """The UI renders write-only secrets as empty fields meaning "leave
        as-is". Writing that through destroys a working key with no way to
        recover it from the panel."""
        from app.models import Settings as DBSettings
        from sqlmodel import select as sm_select

        r = self._patch(client, auth_headers, {"wifi_passphrase": "real-key-123"})
        assert r.status_code in (200, 204), r.text

        r2 = self._patch(client, auth_headers, {"wifi_passphrase": ""})
        assert r2.status_code in (200, 204), r2.text

        # Read straight from the DB — the API never returns this value.
        from app.database import get_async_engine
        import asyncio
        from sqlmodel.ext.asyncio.session import AsyncSession

        async def _read():
            async with AsyncSession(get_async_engine()) as s:
                row = (await s.exec(
                    sm_select(DBSettings).where(DBSettings.key == "wifi_passphrase")
                )).first()
                return row.value if row else None

        assert asyncio.run(_read()) == "real-key-123"

    def test_wifi_on_a_wired_lan_is_refused_at_the_gate(
        self, client, admin_user, auth_headers,
    ):
        """apply() checks this inside the block that rolls everything back, so
        catching it later would drop NAT, DHCP and the uplink on a working
        router just to report a typo."""
        r = self._patch(client, auth_headers,
                        {"wifi_enabled": True, "lan_interface": "eth1"},
                        wireless=False)
        assert r.status_code == 400
        assert "none of the LAN ports" in r.json()["detail"]

    def test_wifi_enabled_alone_still_reaches_validation(
        self, client, admin_user, auth_headers,
    ):
        """A bare wifi_enabled patch used to skip the gate entirely."""
        r = self._patch(client, auth_headers, {"wifi_enabled": True}, wireless=False)
        # Either refused outright, or accepted because no LAN port is set yet —
        # what must NOT happen is reaching apply() with an invalid combination.
        assert r.status_code in (200, 204, 400)


class TestWanCannotReachContainers:
    """The input chain's `ct state new drop` is the whole safety argument for
    the uplink — but Docker-published services are DNAT'd and traverse FORWARD,
    never INPUT. An unqualified docker0 accept therefore bypasses it entirely,
    and the wan_blocked counter reads zero while it happens."""

    def _rules(self, monkeypatch):
        import asyncio
        from app.core import nftables as nft
        scripts = []

        async def fake_nft(script):
            scripts.append(script)
            return True

        monkeypatch.setattr(nft, "_nft", fake_nft)
        asyncio.run(nft.apply_router_nat("eth0", "eth1"))
        body = "\n".join(scripts)
        return [l.strip() for l in body.split("chain forward")[1]
                .split("}")[0].splitlines()
                if l.strip() and not l.strip().startswith("#")]

    def test_no_unqualified_container_accept(self, monkeypatch):
        for rule in self._rules(monkeypatch):
            if "docker0" in rule or "br-*" in rule:
                assert rule.count("ifname") >= 2, (
                    f"container rule must name both sides, else WAN-side DNAT "
                    f"matches it: {rule!r}"
                )

    def test_containers_can_still_reach_the_internet(self, monkeypatch):
        rules = self._rules(monkeypatch)
        assert any('iifname "docker0" oifname "eth0" accept' == r for r in rules)

    def test_lan_can_still_reach_published_ports(self, monkeypatch):
        """The panel is one of them — losing this locks everyone out of the UI
        and leaves nobody able to confirm the router."""
        rules = self._rules(monkeypatch)
        assert any('iifname "eth1" ct status dnat accept' == r for r in rules)


class TestDisablingASubsystemStopsIt:
    """Turning WiFi off is what an operator does to kill a leaked passphrase.
    Returning 200 while hostapd keeps broadcasting it is the worst outcome."""

    def _run(self, monkeypatch, extra):
        import asyncio
        from app.core import router_mode as rm
        calls = []

        async def ok_nat(*a, **k):
            return True

        async def noop():
            return {"running": False}

        async def dhcp_start(cfg):
            calls.append("dhcp_start")
            return {"running": True}

        async def dhcp_stop():
            calls.append("dhcp_stop")
            return {"running": False}

        async def wifi_start(cfg):
            calls.append("wifi_start")
            return {"running": True}

        async def wifi_stop():
            calls.append("wifi_stop")
            return {"running": False}

        monkeypatch.setattr(rm.nft, "apply_router_nat", ok_nat)
        monkeypatch.setattr(rm.nft, "remove_router_nat", lambda: noop())
        monkeypatch.setattr(rm.dhcp_mod, "start", dhcp_start)
        monkeypatch.setattr(rm.dhcp_mod, "stop", dhcp_stop)
        monkeypatch.setattr(rm.wifi_mod, "start", wifi_start)
        monkeypatch.setattr(rm.wifi_mod, "stop", wifi_stop)
        monkeypatch.setattr(rm, "set_ip_forward", lambda on: True)
        monkeypatch.setattr(rm.nc, "list_interfaces",
                            lambda: [{"name": "eth0"}, {"name": "eth1"}])
        monkeypatch.setattr(rm.nc, "read_interface_address", lambda i: ("192.168.10.1", 24))
        monkeypatch.setattr(rm.nc, "is_wireless", lambda i: True)

        base = {"operating_mode": "router", "wan_interface": "eth0",
                "lan_interface": "eth1"}
        base.update(extra)

        class S:
            async def exec(self, statement=None, *a, **k):
                rows = ([] if statement is not None and "device" in str(statement).lower()
                        else [type("R", (), {"key": k2, "value": v})() for k2, v in base.items()])
                return type("Res", (), {"all": lambda self: rows})()

        asyncio.run(rm.apply(S()))
        return calls

    def test_wifi_off_stops_the_access_point(self, monkeypatch):
        calls = self._run(monkeypatch, {"wifi_enabled": "false"})
        assert "wifi_stop" in calls and "wifi_start" not in calls

    def test_dhcp_off_stops_the_server(self, monkeypatch):
        calls = self._run(monkeypatch, {"dhcp_enabled": "false"})
        assert "dhcp_stop" in calls and "dhcp_start" not in calls


class TestLeaseNameCannotBreakDnsmasq:
    """Device names come from DHCP hostnames and mDNS, so they are
    attacker-influenced, and dhcp-host is a comma-separated line."""

    def test_injection_characters_are_stripped(self):
        from app.core.router_mode import _safe_lease_name
        for raw, banned in (
            ("nas,192.168.1.9,evil", ","),
            ("printer#comment", "#"),
            ("tv\nserver=1.2.3.4", "\n"),
            ("laptop=x", "="),
        ):
            assert banned not in _safe_lease_name(raw), raw

    def test_ordinary_names_survive_readably(self):
        from app.core.router_mode import _safe_lease_name
        assert _safe_lease_name("Living Room TV") == "Living-Room-TV"
        assert _safe_lease_name("nas-01") == "nas-01"


class TestRouterModeOwnsTheUplink:
    """Two legacy paths assumed PiTun was never the router itself."""

    def test_gateway_ip_is_not_overwritten_with_the_wan_address(
        self, client, admin_user, auth_headers, session,
    ):
        """`interface` is the legacy install-time key and in router mode often
        names the port now facing the ISP. Reading it here meant every panel
        poll committed the ISP address as "us on the LAN"."""
        from app.models import Settings as DBSettings
        for k, v in (("operating_mode", "router"), ("interface", "eth0"),
                     ("lan_interface", "eth1"), ("wan_interface", "eth0"),
                     ("gateway_ip", "192.168.10.1")):
            session.add(DBSettings(key=k, value=v))
        session.commit()

        with mock.patch("app.api.system._detect_ip", return_value="203.0.113.7"):
            r = client.get("/api/system/settings", headers=auth_headers)
        assert r.status_code == 200

        session.expire_all()
        from sqlmodel import select as sm_select
        row = session.exec(
            sm_select(DBSettings).where(DBSettings.key == "gateway_ip")
        ).first()
        assert row.value == "192.168.10.1", "the WAN address must not land here"

    def test_legacy_network_apply_is_refused_in_router_mode(
        self, client, admin_user, auth_headers, session,
    ):
        """One click from a stale Settings tab would rewrite the default route
        every LAN client depends on — and it survives a reboot."""
        from app.models import Settings as DBSettings
        session.add(DBSettings(key="operating_mode", value="router"))
        session.commit()

        r = client.post("/api/network/apply", headers=auth_headers,
                        json={"gateway": "192.168.1.1"})
        assert r.status_code == 409
        assert "running as a router" in r.json()["detail"]

        r2 = client.post("/api/network/rollback", headers=auth_headers, json={})
        assert r2.status_code == 409

    def test_legacy_network_apply_still_works_in_gateway_mode(
        self, client, admin_user, auth_headers,
    ):
        """The guard must not break the feature for everyone else."""
        with mock.patch("app.api.network.network_apply.apply") as m_apply:
            m_apply.return_value = mock.Mock(
                id="b1", to_dict=lambda: {"id": "b1"},
            )
            r = client.post("/api/network/apply", headers=auth_headers,
                            json={"gateway": "192.168.1.1"})
        assert r.status_code != 409


class TestWifiValidationMatchesTheRadio:
    """Validation that is looser than hostapd now costs more than a warning:
    a rejected AP start rolls the whole router back, so a typo in the WiFi
    password would drop NAT, DHCP and the uplink with it."""

    def _cfg(self, **over):
        from app.core.wifi import WifiConfig
        base = dict(interface="wlan0", ssid="PiTun", passphrase="correcthorse",
                    country="DE", band="2.4", channel=6)
        base.update(over)
        return WifiConfig(**base)

    def test_passphrase_is_counted_in_bytes(self):
        """WPA-PSK is defined over bytes. A Cyrillic passphrase of 8
        characters is 16 bytes — len() would wave it through and hostapd
        would refuse."""
        from app.core.wifi import render_hostapd_conf, WifiConfigError
        import pytest
        with pytest.raises(WifiConfigError, match="ASCII"):
            render_hostapd_conf(self._cfg(passphrase="парольчик"))

    def test_non_ascii_is_named_as_the_problem(self):
        from app.core.wifi import render_hostapd_conf, WifiConfigError
        import pytest
        with pytest.raises(WifiConfigError, match="ASCII"):
            render_hostapd_conf(self._cfg(passphrase="passwörd123"))

    def test_control_characters_in_the_ssid_are_rejected(self):
        """The SSID is written verbatim into a line-oriented config, so a
        newline injects a directive rather than naming a network."""
        from app.core.wifi import render_hostapd_conf, WifiConfigError
        import pytest
        with pytest.raises(WifiConfigError, match="control characters"):
            render_hostapd_conf(self._cfg(ssid="Home\nwpa_passphrase=hacked"))

    def test_ordinary_ascii_passphrase_still_works(self):
        from app.core.wifi import render_hostapd_conf
        conf = render_hostapd_conf(self._cfg(passphrase="Str0ng-Pass!"))
        assert "wpa_passphrase=Str0ng-Pass!" in conf


class TestReservationUniqueness:
    """Two devices on one reserved address renders two dhcp-host lines for it,
    which dnsmasq treats as fatal — DHCP for the whole LAN goes with it."""

    def test_duplicate_reservation_is_refused_with_the_holder_named(
        self, client, admin_user, auth_headers, session,
    ):
        from app.models import Device
        a = Device(mac="aa:11:22:33:44:55", name="NAS")
        b = Device(mac="bb:11:22:33:44:55", name="Printer")
        session.add(a); session.add(b)
        session.commit()
        session.refresh(a); session.refresh(b)

        r1 = client.patch(f"/api/devices/{a.id}", headers=auth_headers,
                          json={"dhcp_reserved_ip": "192.168.10.50"})
        assert r1.status_code in (200, 204), r1.text

        r2 = client.patch(f"/api/devices/{b.id}", headers=auth_headers,
                          json={"dhcp_reserved_ip": "192.168.10.50"})
        assert r2.status_code == 400
        # Naming the current holder is the difference between a fixable error
        # and a puzzle.
        assert "NAS" in r2.json()["detail"]

    def test_keeping_your_own_reservation_is_not_a_clash(
        self, client, admin_user, auth_headers, session,
    ):
        from app.models import Device
        d = Device(mac="cc:11:22:33:44:55", name="TV",
                   dhcp_reserved_ip="192.168.10.51")
        session.add(d)
        session.commit()
        session.refresh(d)

        r = client.patch(f"/api/devices/{d.id}", headers=auth_headers,
                         json={"dhcp_reserved_ip": "192.168.10.51", "name": "Telly"})
        assert r.status_code in (200, 204), r.text

    def test_many_devices_without_reservations_do_not_collide(
        self, client, admin_user, auth_headers, session,
    ):
        """SQLite treats NULLs as distinct, so the unreserved majority is
        unaffected by the unique index."""
        from app.models import Device
        for i in range(5):
            session.add(Device(mac=f"dd:11:22:33:44:{i:02x}"))
        session.commit()
        assert client.get("/api/devices", headers=auth_headers).status_code == 200


class TestUplinkExposure:
    """Publishing the panel on the WAN is reasonable behind another router and
    reckless in front of the internet. The address decides which it is, so the
    check is enforced rather than left to the operator's memory of their own
    topology."""

    def test_ports_are_parsed_forgivingly(self):
        from app.core.router_mode import _parse_ports
        assert _parse_ports("22, 443 8080") == [22, 443, 8080]
        assert _parse_ports("22,22,22") == [22]
        # Junk is dropped rather than failing the apply: a stray character in
        # a port list should not take the router down.
        assert _parse_ports("22, https, -1, 70000, ") == [22]
        assert _parse_ports("") == []

    def test_admin_access_adds_the_panel_ports(self):
        from app.core.router_mode import _wan_allowed_ports
        tcp, udp = _wan_allowed_ports({"wan_admin_access": "true"})
        assert tcp == [80, 443] and udp == []

        tcp, _ = _wan_allowed_ports({"wan_admin_access": "true", "wan_allow_tcp": "22"})
        assert tcp == [22, 80, 443]

    def test_ssh_access_adds_only_ssh(self):
        from app.core.router_mode import _wan_allowed_ports
        tcp, udp = _wan_allowed_ports({"wan_ssh_access": "true"})
        assert tcp == [22] and udp == []

    def test_both_toggles_combine(self):
        from app.core.router_mode import _wan_allowed_ports
        tcp, _ = _wan_allowed_ports(
            {"wan_ssh_access": "true", "wan_admin_access": "true"})
        assert tcp == [22, 80, 443]

    def test_nothing_is_opened_by_default(self):
        from app.core.router_mode import _wan_allowed_ports
        assert _wan_allowed_ports({}) == ([], [])
        assert _wan_allowed_ports(
            {"wan_admin_access": "false", "wan_ssh_access": "false"}) == ([], [])

    def test_public_uplink_refuses_to_be_opened(self, monkeypatch):
        import pytest
        from app.core import router_mode as rm
        # A genuinely routable address. Note 203.0.113.0/24 and the other
        # documentation ranges would NOT do: Python already reports them as
        # not globally reachable, so they'd pass the check.
        monkeypatch.setattr(rm.nc, "read_interface_address", lambda i: ("93.184.216.34", 24))
        with pytest.raises(rm.RouterModeError, match="public one"):
            rm._refuse_public_wan_exposure("eth0", [443], [])

    def test_carrier_grade_nat_counts_as_behind_something(self, monkeypatch):
        """100.64/10 is the ISP's own NAT — as unreachable as a home LAN."""
        from app.core import router_mode as rm
        monkeypatch.setattr(rm.nc, "read_interface_address", lambda i: ("100.72.3.9", 10))
        rm._refuse_public_wan_exposure("eth0", [443], [])

    def test_private_uplink_may_be_opened(self, monkeypatch):
        from app.core import router_mode as rm
        monkeypatch.setattr(rm.nc, "read_interface_address", lambda i: ("192.168.1.50", 24))
        rm._refuse_public_wan_exposure("eth0", [80, 443], [])

    def test_opening_nothing_is_never_refused(self, monkeypatch):
        from app.core import router_mode as rm
        monkeypatch.setattr(rm.nc, "read_interface_address", lambda i: ("93.184.216.34", 24))
        rm._refuse_public_wan_exposure("eth0", [], [])

    def test_an_address_we_cannot_read_does_not_block(self, monkeypatch):
        """DHCP may still be in flight; absence of proof is not proof."""
        from app.core import router_mode as rm
        monkeypatch.setattr(rm.nc, "read_interface_address", lambda i: (None, None))
        rm._refuse_public_wan_exposure("eth0", [443], [])


class TestMultiPortLan:
    """A LAN can be several sockets plus the radio. They have to end up as one
    segment — same subnet, one DHCP scope, clients able to see each other —
    which means a bridge, and it means every downstream consumer must be told
    the bridge's name rather than any single port's."""

    def test_members_keep_the_primary_first(self):
        from app.core.router_mode import _lan_members
        assert _lan_members(
            {"lan_interface": "enp2s0", "lan_extra_interfaces": "wlp3s0, eth2"}
        ) == ["enp2s0", "wlp3s0", "eth2"]

    def test_duplicates_and_blanks_are_dropped(self):
        from app.core.router_mode import _lan_members
        assert _lan_members(
            {"lan_interface": "enp2s0", "lan_extra_interfaces": "enp2s0,, enp2s0 wlp3s0"}
        ) == ["enp2s0", "wlp3s0"]

    def test_a_single_port_lan_has_one_member(self):
        from app.core.router_mode import _lan_members
        assert _lan_members({"lan_interface": "wlp3s0"}) == ["wlp3s0"]
        assert _lan_members({}) == []


    def test_the_address_is_found_on_whichever_member_holds_it(self, monkeypatch):
        """Tearing the bridge down leaves the address on a wired member, which
        need not be the port named as primary. Demanding it be moved back by
        hand before the next apply would be a rake laid by our own teardown."""
        from app.core import router_mode as rm

        held = {"enp2s0": ("192.168.50.1", 24)}
        monkeypatch.setattr(rm.nc, "read_interface_address",
                            lambda n: held.get(n, (None, None)))
        monkeypatch.setattr(rm.wifi_mod, "bridge_exists", lambda *a, **k: False)

        ip, prefix = rm._lan_addressing(
            "wlp3s0", {"lan_interface": "wlp3s0", "lan_extra_interfaces": "enp2s0"})
        assert (ip, prefix) == ("192.168.50.1", 24)

    def test_no_member_has_an_address_is_still_an_error(self, monkeypatch):
        import pytest
        from app.core import router_mode as rm
        monkeypatch.setattr(rm.nc, "read_interface_address", lambda n: (None, None))
        monkeypatch.setattr(rm.wifi_mod, "bridge_exists", lambda *a, **k: False)
        with pytest.raises(rm.RouterModeError, match="has no IPv4 address"):
            rm._lan_addressing(
                "wlp3s0", {"lan_interface": "wlp3s0", "lan_extra_interfaces": "enp2s0"})

    def test_bridge_enslaves_every_wired_member(self, monkeypatch):
        from app.core import wifi as w
        calls = []

        def fake_ip(*args, **kw):
            calls.append(" ".join(args))
            # The bridge does not exist until we create it.
            rc = 1 if args[:3] == ("link", "show", w.BRIDGE_NAME) else 0
            return mock.Mock(returncode=rc, stdout="", stderr="")

        monkeypatch.setattr(w, "_ip", fake_ip)
        res = w.create_lan_bridge(["enp2s0", "eth2"], "192.168.50.1/24")
        assert res["created"] is True
        assert f"link add name {w.BRIDGE_NAME} type bridge" in calls
        for port in ("enp2s0", "eth2"):
            assert f"link set {port} master {w.BRIDGE_NAME}" in calls
            # Every member is flushed: a secondary still holding its own
            # address would answer on a subnet nothing routes any more.
            assert f"addr flush dev {port}" in calls
        assert f"addr add 192.168.50.1/24 dev {w.BRIDGE_NAME}" in calls

    def test_a_failed_enslave_puts_the_address_back(self, monkeypatch):
        import pytest
        from app.core import wifi as w
        calls = []

        def fake_ip(*args, **kw):
            calls.append(" ".join(args))
            if args[:3] == ("link", "show", w.BRIDGE_NAME):
                return mock.Mock(returncode=1, stdout="", stderr="")
            if args[:2] == ("link", "set") and args[2] == "eth2":
                return mock.Mock(returncode=1, stdout="", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(w, "_ip", fake_ip)
        with pytest.raises(w.WifiConfigError, match="Could not enslave eth2"):
            w.create_lan_bridge(["enp2s0", "eth2"], "192.168.50.1/24")
        # Better a working un-bridged LAN than neither: the address returns to
        # the primary and the half-built bridge is removed.
        assert "addr add 192.168.50.1/24 dev enp2s0" in calls
        assert f"link delete {w.BRIDGE_NAME}" in calls


    def test_the_radio_gives_up_its_address_without_being_enslaved(self, monkeypatch):
        """When the operator nominated the radio as the primary LAN port, the
        gateway address starts out on it. hostapd enslaves it later — but if
        the address stays there in the meantime, the same address answers on
        both the radio and the bridge and which one a client reaches is a
        coin toss."""
        from app.core import wifi as w
        calls = []

        def fake_ip(*args, **kw):
            calls.append(" ".join(args))
            rc = 1 if args[:3] == ("link", "show", w.BRIDGE_NAME) else 0
            return mock.Mock(returncode=rc, stdout="", stderr="")

        monkeypatch.setattr(w, "_ip", fake_ip)
        w.create_lan_bridge(["enp2s0"], "192.168.50.1/24", also_flush=["wlp3s0"])

        assert "addr flush dev wlp3s0" in calls
        # Flushed, but NOT enslaved: hostapd does that itself via `bridge=`.
        assert f"link set wlp3s0 master {w.BRIDGE_NAME}" not in calls
        assert f"link set enp2s0 master {w.BRIDGE_NAME}" in calls

    def test_dissolve_reads_the_kernel_not_the_config(self, monkeypatch):
        """Teardown runs from states nobody planned, including after the
        settings that built the bridge changed underneath it."""
        from app.core import wifi as w
        calls = []

        def fake_ip(*args, **kw):
            calls.append(" ".join(args))
            if args[:3] == ("link", "show", w.BRIDGE_NAME):
                return mock.Mock(returncode=0, stdout="", stderr="")
            if args[:4] == ("-o", "link", "show", "master"):
                return mock.Mock(
                    returncode=0,
                    stdout=(
                        "3: enp2s0: <BROADCAST> mtu 1500 master br-lan\n"
                        "4: wlp3s0: <BROADCAST> mtu 1500 master br-lan\n"
                    ),
                    stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(w, "_ip", fake_ip)
        monkeypatch.setattr("app.core.network_config.read_interface_address",
                            lambda n: ("192.168.50.1", 24))
        monkeypatch.setattr("app.core.network_config.is_wireless",
                            lambda n: n == "wlp3s0")

        res = w.dissolve_lan_bridge()
        assert res["removed"] is True
        assert set(res["members"]) == {"enp2s0", "wlp3s0"}
        # The address goes to the wired member: the radio is torn down with
        # hostapd and would not keep it.
        assert res["address_on"] == "enp2s0"
        assert "addr add 192.168.50.1/24 dev enp2s0" in calls
        assert f"link delete {w.BRIDGE_NAME}" in calls

    def test_dissolving_nothing_is_not_an_error(self, monkeypatch):
        from app.core import wifi as w
        monkeypatch.setattr(w, "_ip", lambda *a, **k: mock.Mock(returncode=1, stdout="", stderr=""))
        assert w.dissolve_lan_bridge()["removed"] is False


class TestLeavingRouterModeIsNeverBlocked:
    """Gateway is the resting state and the escape hatch. Role validation
    describes what a working ROUTER needs; applying it to a request that
    switches the router off is how an operator gets stranded — which is
    exactly what happened once the LAN ports were bridged and stopped
    looking like standalone role candidates."""

    def test_switch_to_gateway_survives_an_unusable_router_config(
        self, client, admin_user, auth_headers, monkeypatch,
    ):
        from app.core import network_config as nc
        # The box reports only the uplink: the LAN ports are enslaved to the
        # bridge router mode built, or simply gone.
        monkeypatch.setattr(nc, "list_interfaces", lambda: [
            {"name": "eno1", "mac": "aa:bb:cc:dd:ee:01", "up": True,
             "carrier": True, "ipv4": "192.168.1.50", "cidr": 24,
             "is_default_route": True, "wireless": False, "ap_capable": False,
             "wifi_detail": "", "wifi_modes": [], "master": ""},
        ])
        r = client.patch("/api/system/settings",
                         headers=auth_headers,
                         json={"operating_mode": "gateway"})
        assert r.status_code in (200, 204), r.text


class TestIcmpCounterCanActuallySeeWhatItClaims:
    """The counter exists to detect a path-MTU black hole. `fragmentation
    needed` for a flow already in progress is what conntrack marks RELATED,
    so a counter placed after `ct state established,related accept` never
    sees one — it read zero on a healthy link and could not have detected
    the failure it was added for."""

    def _ruleset(self, **kw):
        import asyncio
        from app.core import nftables as nft
        cap = {}

        async def fake(script):
            cap["s"] = script
            return True

        original, nft._nft = nft._nft, fake
        try:
            asyncio.run(nft.apply_router_nat("eth0", "eth1", **kw))
        finally:
            nft._nft = original
        return cap["s"]

    def test_icmp_is_counted_before_conntrack(self):
        script = self._ruleset()
        body = script.split("chain input")[1].split("chain forward")[0]
        lines = [ln.strip() for ln in body.splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        counter = next(i for i, ln in enumerate(lines) if "wan_icmp_in" in ln)
        conntrack = next(i for i, ln in enumerate(lines)
                         if "ct state established,related accept" in ln)
        assert counter < conntrack

    def test_the_counting_rule_has_no_verdict(self):
        """It must fall through: giving it `accept` would admit unsolicited
        ICMP that the blanket drop below is there to refuse."""
        script = self._ruleset()
        rule = next(ln for ln in script.splitlines() if "wan_icmp_in" in ln
                    and "counter" in ln)
        for verdict in (" accept", " drop", " reject"):
            assert not rule.rstrip().endswith(verdict)

    def test_unsolicited_icmp_is_still_accepted_once(self):
        """Exactly one rule admits ICMP, and it is not the counting one —
        otherwise every packet would be counted twice."""
        script = self._ruleset()
        body = script.split("chain input")[1].split("chain forward")[0]
        accepts = [ln for ln in body.splitlines()
                   if "icmp type" in ln and ln.strip().endswith("accept")]
        assert len(accepts) == 1
        assert "wan_icmp_in" not in accepts[0]


class TestANoOpSaveDoesNotRebuildTheNetwork:
    """Applying router mode tears the LAN bridge down and rebuilds it, and
    restarts hostapd and dnsmasq. From a client's point of view that is
    indistinguishable from the router going down and coming back. Doing it
    for a save that changed nothing kicked everyone off a working router —
    and re-armed the confirm window, so a box nobody thought to confirm
    again reverted three minutes later."""

    def test_rewriting_the_same_values_leaves_the_dataplane_alone(
        self, client, admin_user, auth_headers, monkeypatch,
    ):
        from app.core import router_mode as rm
        applied = []
        armed = []

        async def spy_apply(session):
            applied.append(True)
            return {"mode": "gateway"}

        monkeypatch.setattr(rm, "apply", spy_apply)
        from app.core import router_watchdog as rw
        monkeypatch.setattr(rw, "arm", lambda *a, **k: armed.append(True))

        # Store a value first: writing a setting that has no row yet IS a
        # change, so the no-op has to be measured against a box that already
        # holds the value — which is every real one.
        first = client.patch("/api/system/settings", headers=auth_headers,
                             json={"dhcp_lease_hours": 12})
        assert first.status_code in (200, 204), first.text
        applied.clear()
        armed.clear()

        same = client.patch("/api/system/settings", headers=auth_headers,
                            json={"dhcp_lease_hours": 12})
        assert same.status_code in (200, 204), same.text
        assert applied == [], "a no-op save must not reconcile the dataplane"
        assert armed == []

    def test_a_real_change_still_reconciles(
        self, client, admin_user, auth_headers, monkeypatch,
    ):
        from app.core import router_mode as rm
        applied = []

        async def spy_apply(session):
            applied.append(True)
            return {"mode": "gateway"}

        monkeypatch.setattr(rm, "apply", spy_apply)
        cur = client.get("/api/system/settings", headers=auth_headers).json()
        r = client.patch("/api/system/settings", headers=auth_headers,
                         json={"dhcp_lease_hours": int(cur["dhcp_lease_hours"]) + 1})
        assert r.status_code in (200, 204), r.text
        assert applied == [True]


class TestMssClamping:
    """A PPPoE uplink carries 1492 bytes and a tagged VLAN 1496, but LAN
    clients announce MSS for their own 1500-byte link. Without clamping, the
    router depends on ICMP 'fragmentation needed' reaching the sender — which
    the public internet drops often enough that it is not a plan. The symptom
    is the nastiest kind: DNS resolves, ping works, small pages load, large
    transfers hang forever."""

    def _forward_chain(self, wan="ppp0", lan="br-lan"):
        import asyncio
        from app.core import nftables as nft
        cap = {}

        async def fake(script):
            cap["s"] = script
            return True

        original, nft._nft = nft._nft, fake
        try:
            asyncio.run(nft.apply_router_nat(wan, lan))
        finally:
            nft._nft = original
        return cap["s"].split("chain forward")[1].split("chain ")[0]

    def test_syn_packets_are_clamped_to_the_route_mtu(self):
        body = self._forward_chain()
        rule = [ln.strip() for ln in body.splitlines() if "maxseg" in ln]
        assert len(rule) == 1, "exactly one clamp rule"
        assert "tcp flags syn" in rule[0]
        # `rt mtu`, not a hardcoded 1452: the figure differs between PPPoE,
        # VLAN and a plain uplink, and reading the route keeps it correct
        # when the uplink changes shape.
        assert "size set rt mtu" in rule[0]

    def test_the_clamp_is_not_tied_to_one_direction(self):
        """The SYN and the SYN-ACK cross in opposite directions and both
        carry an MSS that needs clamping."""
        body = self._forward_chain()
        rule = next(ln.strip() for ln in body.splitlines() if "maxseg" in ln)
        assert "iifname" not in rule and "oifname" not in rule

    def test_the_clamp_runs_before_any_accept(self):
        """It sets an option and falls through; placed after an accept it
        would never be reached for the flows that matter."""
        body = self._forward_chain()
        lines = [ln.strip() for ln in body.splitlines()
                 if ln.strip() and not ln.strip().startswith("#")]
        clamp = next(i for i, ln in enumerate(lines) if "maxseg" in ln)
        first_accept = next(i for i, ln in enumerate(lines) if ln.endswith("accept"))
        assert clamp < first_accept


class TestTrafficActuallyUsesTheUplink:
    """A default route leaving by some other port means the ISP link is up,
    NAT and the firewall are attached to it, and not one packet uses it.
    Everything else reads healthy while the router does nothing."""

    def test_effective_wan_follows_pppoe_and_vlan(self, monkeypatch):
        from app.core import router_mode as rm
        monkeypatch.setattr(rm.wan_mod, "live_pppoe_interface", lambda: "ppp1")
        assert rm.effective_wan(
            {"wan_interface": "eth0", "wan_mode": "pppoe"}) == "ppp1"
        assert rm.effective_wan(
            {"wan_interface": "eth0", "wan_mode": "dhcp", "wan_vlan_id": "835"}
        ) == "eth0.835"
        assert rm.effective_wan(
            {"wan_interface": "eth0", "wan_mode": "dhcp"}) == "eth0"
        assert rm.effective_wan({}) == ""

    def test_a_default_route_off_the_uplink_is_reported(self, monkeypatch):
        import asyncio
        from app.core import router_mode as rm
        monkeypatch.setattr(rm.nft, "router_counters", _async({
            "wan_dhcp_in": {"packets": 0}, "wan_icmp_in": {"packets": 0},
            "wan_blocked": {"packets": 0},
        }))
        monkeypatch.setattr(rm.nc, "read_interface_address", lambda n: ("10.0.0.2", 24))
        monkeypatch.setattr(rm.nc, "read_default_route", lambda: ("eno1", "192.168.1.1"))

        findings = asyncio.run(rm.diagnose_wan("ppp0"))
        hit = [f for f in findings if "not the uplink" in f["title"]]
        assert hit and hit[0]["level"] == "warn"
        assert "eno1" in hit[0]["title"]

    def test_nothing_reported_when_the_route_uses_the_uplink(self, monkeypatch):
        import asyncio
        from app.core import router_mode as rm
        monkeypatch.setattr(rm.nft, "router_counters", _async({
            "wan_dhcp_in": {"packets": 0}, "wan_icmp_in": {"packets": 0},
            "wan_blocked": {"packets": 0},
        }))
        monkeypatch.setattr(rm.nc, "read_interface_address", lambda n: ("10.0.0.2", 24))
        monkeypatch.setattr(rm.nc, "read_default_route", lambda: ("ppp0", "10.0.0.1"))

        findings = asyncio.run(rm.diagnose_wan("ppp0"))
        assert not [f for f in findings if "not the uplink" in f["title"]]


def _async(value):
    async def _fn(*a, **k):
        return value
    return _fn


class TestPublishingThePanelOnTheUplink:
    """Opening a port in INPUT is only half the story. Services on the host —
    SSH — are delivered locally and hit INPUT. The panel is an nginx container
    with published ports, so a request to the box's address is DNAT'd and then
    FORWARDED to the container, never reaching INPUT at all, and the forward
    policy is drop. Observed on hardware: with both toggles on, SSH answered
    from the uplink and the panel did not."""

    def _chains(self, **kw):
        import asyncio
        from app.core import nftables as nft
        cap = {}

        async def fake(script):
            cap["s"] = script
            return True

        original, nft._nft = nft._nft, fake
        try:
            asyncio.run(nft.apply_router_nat("eth0", "br-lan", **kw))
        finally:
            nft._nft = original
        s = cap["s"]
        return (s.split("chain input")[1].split("chain forward")[0],
                s.split("chain forward")[1].split("chain ")[0])

    def test_published_ports_are_allowed_in_forward_too(self):
        _, fwd = self._chains(wan_allow_tcp=[22, 80, 443])
        rule = [ln.strip() for ln in fwd.splitlines()
                if "ct status dnat" in ln and "eth0" in ln]
        assert len(rule) == 1
        assert "tcp dport { 22, 80, 443 }" in rule[0]

    def test_the_forward_allowance_is_scoped_to_the_published_ports(self):
        """Not a blanket `ct status dnat accept` on the uplink: that would
        expose every other container port that happens to be mapped."""
        _, fwd = self._chains(wan_allow_tcp=[80])
        rule = next(ln.strip() for ln in fwd.splitlines()
                    if "ct status dnat" in ln and "eth0" in ln)
        assert "dport" in rule
        assert rule != 'iifname "eth0" ct status dnat accept'

    def test_nothing_is_published_when_no_ports_are_opened(self):
        _, fwd = self._chains()
        assert not [ln for ln in fwd.splitlines()
                    if "ct status dnat" in ln and "eth0" in ln]

    def test_udp_ports_get_the_same_treatment(self):
        _, fwd = self._chains(wan_allow_udp=[51820])
        rule = [ln.strip() for ln in fwd.splitlines()
                if "ct status dnat" in ln and "udp dport" in ln]
        assert len(rule) == 1 and "51820" in rule[0]


class TestBootReconcile:
    """Boot is not an operator pressing Save. The hardware may still be
    arriving — a wifi adapter appears only once its firmware has loaded — and
    a failure is unattended, so the half-state it leaves behind matters more
    than the error message."""

    def test_waits_for_a_port_that_arrives_late(self, monkeypatch):
        import asyncio
        from app.core import router_mode as rm

        seen = {"calls": 0}

        def late(*_a, **_k):
            seen["calls"] += 1
            # The radio shows up on the third look, as it does on real
            # hardware once mt7921 has loaded its firmware.
            names = ["eno1", "enp2s0"] + (["wlp3s0"] if seen["calls"] >= 3 else [])
            return [{"name": n} for n in names]

        monkeypatch.setattr(rm.nc, "list_interfaces", late)
        missing = asyncio.run(rm.wait_for_ports(["eno1", "wlp3s0"], timeout=30))
        assert missing == []
        assert seen["calls"] >= 3

    def test_gives_up_on_a_port_that_never_arrives(self, monkeypatch):
        import asyncio
        from app.core import router_mode as rm
        monkeypatch.setattr(rm.nc, "list_interfaces", lambda: [{"name": "eno1"}])
        missing = asyncio.run(rm.wait_for_ports(["eno1", "usb0"], timeout=3))
        assert missing == ["usb0"]

    def test_a_boot_that_cannot_restore_leaves_a_clean_gateway(self, monkeypatch):
        """The failure mode this exists to prevent: settings claiming router,
        no NAT, no bridge, and the sidecars resurrected by Docker's restart
        policy — one serving DHCP for a bridge that no longer exists, the
        other crash-looping on a missing radio."""
        import asyncio, pytest
        from app.core import router_mode as rm

        torn = []

        async def spy_teardown():
            torn.append(True)
            return {"mode": "gateway"}

        monkeypatch.setattr(rm, "teardown", spy_teardown)
        monkeypatch.setattr(rm.nc, "list_interfaces", lambda: [{"name": "eno1"}])

        async def settings(_s):
            return {"operating_mode": "router", "wan_interface": "eno1",
                    "lan_interface": "wlp3s0"}

        monkeypatch.setattr(rm, "_settings_map", settings)

        with pytest.raises(rm.RouterModeError, match="did not appear"):
            asyncio.run(rm.reconcile_on_boot(object()))
        assert torn == [True], "the dataplane must be torn down, not left half-applied"

    def test_gateway_at_boot_just_tears_down(self, monkeypatch):
        import asyncio
        from app.core import router_mode as rm
        torn = []

        async def spy_teardown():
            torn.append(True)
            return {"mode": "gateway"}

        monkeypatch.setattr(rm, "teardown", spy_teardown)

        async def settings(_s):
            return {"operating_mode": "gateway"}

        monkeypatch.setattr(rm, "_settings_map", settings)
        assert asyncio.run(rm.reconcile_on_boot(object()))["mode"] == "gateway"
        assert torn == [True]


class TestRouterModeKeepsTrying:
    """The boot reconcile is one attempt against hardware that may not have
    arrived — a radio still loading firmware, a LAN port whose address
    NetworkManager will not assign until a cable is plugged in. One shot means
    plugging that cable in a minute later changes nothing."""

    def _tick(self, monkeypatch, *, mode, up, apply_raises=False):
        import asyncio
        from app.core import router_watchdog as rw
        from app.core import router_mode as rm, nftables as nft

        rw._last_retry = 0.0
        rw._retry_logged = False
        applied = []

        async def spy_apply(session):
            applied.append(True)
            if apply_raises:
                raise rm.RouterModeError("port has no address")
            return {"mode": "router"}

        async def counters():
            return {"wan_blocked": {"packets": 1}} if up else {}

        monkeypatch.setattr(rm, "apply", spy_apply)
        monkeypatch.setattr(nft, "router_counters", counters)

        class _Row:
            value = mode

        class _Res:
            def first(self_inner):
                return _Row()

        class _S:
            async def exec(self_inner, *_a, **_k):
                return _Res()

        asyncio.run(rw._retry_router_mode(_S()))
        return applied

    def test_retries_when_configured_as_router_but_not_running(self, monkeypatch):
        assert self._tick(monkeypatch, mode="router", up=False) == [True]

    def test_leaves_a_running_router_alone(self, monkeypatch):
        assert self._tick(monkeypatch, mode="router", up=True) == []

    def test_does_nothing_in_gateway_mode(self, monkeypatch):
        assert self._tick(monkeypatch, mode="gateway", up=False) == []

    def test_a_failing_retry_does_not_raise(self, monkeypatch):
        # The tick must keep running: this is the loop that also reverts an
        # unconfirmed apply, and killing it would disable the rescue.
        assert self._tick(monkeypatch, mode="router", up=False,
                          apply_raises=True) == [True]

    def test_retries_are_rate_limited(self, monkeypatch):
        import asyncio
        from app.core import router_watchdog as rw
        self._tick(monkeypatch, mode="router", up=False)
        # Second call immediately after must be skipped by the interval guard.
        from app.core import router_mode as rm
        again = []

        async def spy(session):
            again.append(True)
            return {"mode": "router"}

        monkeypatch.setattr(rm, "apply", spy)

        class _S:
            async def exec(self_inner, *_a, **_k):
                raise AssertionError("should not even read settings")

        asyncio.run(rw._retry_router_mode(_S()))
        assert again == []


class TestHardwarePresentButUnusable:
    """"No wireless adapters found" is the wrong thing to tell someone whose
    card is sitting on the PCI bus. It happened here: a firmware package had
    been purged, the driver loaded, hardware init failed, and no netdev was
    created — every layer above reported an absence and nobody would have
    thought to look for a missing binary blob."""

    def _sysfs(self, tmp_path, devices):
        """Build a fake /sys/bus/pci/devices tree.

        Slot names here use dashes where a real bus uses colons: Windows
        cannot put a colon in a filename, and the code treats the name as an
        opaque label it never parses.
        """
        for slot, spec in devices.items():
            d = tmp_path / slot
            d.mkdir(parents=True)
            (d / "class").write_text(spec["class"])
            (d / "vendor").write_text(spec.get("vendor", "0x0000"))
            (d / "device").write_text(spec.get("device", "0x0000"))
            if spec.get("net"):
                (d / "net" / spec["net"]).mkdir(parents=True)
            # `uevent` is how the real thing reports the bound driver, and it
            # is a plain file — no symlink privileges needed to fake it.
            lines = []
            if spec.get("driver"):
                lines.append("DRIVER=" + spec["driver"])
            lines.append("PCI_SLOT_NAME=" + slot)
            uevent = "\n".join(lines) + "\n"
            (d / "uevent").write_text(uevent)
        return str(tmp_path)

    def test_a_card_with_a_driver_and_no_interface_is_flagged(self, tmp_path, monkeypatch):
        from app.core import network_config as nc
        root = self._sysfs(tmp_path, {
            # Working wired port — has a netdev, so it is not a finding.
            "0000-01-00.0": {"class": "0x020000", "driver": "r8169", "net": "eno1"},
            # The radio: driver bound, no interface. Firmware, in practice.
            "0000-03-00.0": {"class": "0x028000", "vendor": "0x14c3",
                             "device": "0x0616", "driver": "mt7921e"},
        })
        monkeypatch.setattr(nc, "_PCI_DEVICES", root)
        found = nc.unclaimed_network_devices()
        assert len(found) == 1
        assert found[0]["slot"] == "0000-03-00.0"
        assert found[0]["kind"] == "wireless"
        assert found[0]["driver"] == "mt7921e"
        assert found[0]["reason"] == "driver_but_no_interface"
        assert found[0]["vendor"] == "14c3" and found[0]["device"] == "0616"

    def test_a_card_with_no_driver_reads_differently(self, tmp_path, monkeypatch):
        """The two cases point somewhere different: one needs a driver, the
        other has one and needs firmware."""
        from app.core import network_config as nc
        root = self._sysfs(tmp_path, {
            "0000-04-00.0": {"class": "0x020000", "vendor": "0x8086", "device": "0x1234"},
        })
        monkeypatch.setattr(nc, "_PCI_DEVICES", root)
        found = nc.unclaimed_network_devices()
        assert [f["reason"] for f in found] == ["no_driver"]
        assert found[0]["kind"] == "wired"

    def test_working_hardware_produces_no_findings(self, tmp_path, monkeypatch):
        from app.core import network_config as nc
        root = self._sysfs(tmp_path, {
            "0000-01-00.0": {"class": "0x020000", "driver": "r8169", "net": "eno1"},
            "0000-03-00.0": {"class": "0x028000", "driver": "mt7921e", "net": "wlp3s0"},
            # Not a network controller at all.
            "0000-00-02.0": {"class": "0x030000", "driver": "i915"},
        })
        monkeypatch.setattr(nc, "_PCI_DEVICES", root)
        assert nc.unclaimed_network_devices() == []

    def test_a_missing_sysfs_is_not_an_error(self, monkeypatch):
        from app.core import network_config as nc
        monkeypatch.setattr(nc, "_PCI_DEVICES", "/nonexistent/pci")
        assert nc.unclaimed_network_devices() == []
