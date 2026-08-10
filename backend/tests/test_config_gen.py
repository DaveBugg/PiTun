"""Tests for xray config generation -- the core of PiTun."""
import json
import pytest

from app.core.config_gen import generate_config
from app.models import Node, RoutingRule, DNSRule, BalancerGroup


def _make_node(id=1, protocol="vless", **kwargs):
    defaults = dict(
        name="test", address="1.2.3.4", port=443, uuid="test-uuid",
        transport="ws", tls="tls", sni="example.com", enabled=True,
        ws_path="/", ws_host=None, ws_headers=None,
        grpc_service=None, grpc_mode="gun",
        http_path="/", http_host=None,
        kcp_seed=None, kcp_header="none",
        reality_pbk=None, reality_sid=None, reality_spx=None,
        flow=None, fingerprint="chrome", alpn=None, allow_insecure=False,
        password=None, wg_private_key=None, wg_public_key=None,
        wg_preshared_key=None, wg_endpoint=None, wg_mtu=1420,
        wg_reserved=None, wg_local_address=None,
        hy2_obfs=None, hy2_obfs_password=None,
        group=None, note=None, subscription_id=None,
        latency_ms=None, last_check=None, is_online=True, order=0,
        chain_node_id=None,
    )
    defaults.update(kwargs)
    node = Node(id=id, protocol=protocol, **defaults)
    return node


def _default_settings(**overrides):
    s = {
        "mode": "rules", "log_level": "warning", "dns_port": "5353",
        "tproxy_port_tcp": "7893", "tproxy_port_udp": "7894",
        "socks_port": "1080", "http_port": "8080",
        "bypass_private": "true", "fakedns_enabled": "false",
        "dns_sniffing": "true", "inbound_mode": "tproxy",
        "dns_upstream": "8.8.8.8", "dns_mode": "plain",
        "dns_upstream_secondary": "", "dns_fallback": "",
        "bypass_cn_dns": "false", "bypass_ru_dns": "false",
    }
    s.update(overrides)
    return s


def _find_outbound(config, tag):
    for ob in config["outbounds"]:
        if ob["tag"] == tag:
            return ob
    return None


def _find_inbound(config, tag):
    for ib in config["inbounds"]:
        if ib["tag"] == tag:
            return ib
    return None


class TestSpeedProbeInbound:
    """The active node is speed-tested through the LIVE tunnel: config_gen adds
    a loopback `speed-probe` socks inbound + a top-priority rule pinning it to
    the active outbound, so no second temp xray (and no WG session fight)."""

    def test_present_and_routed_to_active_node(self):
        from app.core.config_gen import SPEED_PROBE_PORT
        node = _make_node(id=7, protocol="vless")
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ib = _find_inbound(cfg, "speed-probe")
        assert ib is not None
        assert ib["listen"] == "127.0.0.1"
        assert ib["port"] == SPEED_PROBE_PORT
        assert ib["protocol"] == "socks"
        # First routing rule pins the probe inbound → active node's outbound.
        first = cfg["routing"]["rules"][0]
        assert first.get("inboundTag") == ["speed-probe"]
        assert first.get("outboundTag") == "node-7"

    def test_absent_without_active_node(self):
        node = _make_node(id=7, protocol="vless")
        cfg = generate_config(None, [node], [], "global", _default_settings())
        assert _find_inbound(cfg, "speed-probe") is None
        assert all(r.get("inboundTag") != ["speed-probe"] for r in cfg["routing"]["rules"])


# ============================================================================
# Sockopt mark tests (CRITICAL -- these catch the routing loop bug)
# ============================================================================

class TestSockoptMark:
    def test_vless_outbound_has_mark_255(self):
        node = _make_node(protocol="vless")
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ob = _find_outbound(cfg, "node-1")
        assert ob is not None, f"node-1 not found in outbounds: {json.dumps(cfg['outbounds'], indent=2)}"
        assert ob["streamSettings"]["sockopt"]["mark"] == 255

    def test_vmess_outbound_has_mark_255(self):
        node = _make_node(protocol="vmess")
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ob = _find_outbound(cfg, "node-1")
        assert ob is not None
        assert ob["streamSettings"]["sockopt"]["mark"] == 255

    def test_trojan_outbound_has_mark_255(self):
        node = _make_node(protocol="trojan", password="secret")
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ob = _find_outbound(cfg, "node-1")
        assert ob is not None
        assert ob["streamSettings"]["sockopt"]["mark"] == 255

    def test_wireguard_outbound_has_mark_255(self):
        node = _make_node(
            protocol="wireguard", transport="tcp", tls="none",
            wg_private_key="privkey", wg_public_key="pubkey",
        )
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ob = _find_outbound(cfg, "node-1")
        assert ob is not None
        assert ob["streamSettings"]["sockopt"]["mark"] == 255

    def test_wireguard_drops_ipv6_interface_address(self):
        # A commercial WG config hands out both an IPv4 and an IPv6
        # interface address. Passing the IPv6 one makes xray's userspace
        # WG bring up an IPv6 netstack that errors with "failed to find
        # available ipv6 table" on an IPv4-only host — so we keep only IPv4.
        node = _make_node(
            protocol="wireguard", transport="tcp", tls="none",
            wg_private_key="privkey", wg_public_key="pubkey",
            wg_local_address="10.65.58.254/32,fc00:bbbb:bbbb:bb01::2:3afd/128",
        )
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ob = _find_outbound(cfg, "node-1")
        assert ob is not None
        addrs = ob["settings"]["address"]
        assert addrs == ["10.65.58.254/32"], addrs
        assert all(":" not in a for a in addrs)

    def test_wireguard_keeps_ipv6_when_no_ipv4(self):
        # A rare IPv6-only WG config still gets its address — better a
        # possibly-failing v6 tunnel than none at all.
        node = _make_node(
            protocol="wireguard", transport="tcp", tls="none",
            wg_private_key="privkey", wg_public_key="pubkey",
            wg_local_address="fc00:bbbb::2/128",
        )
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ob = _find_outbound(cfg, "node-1")
        assert ob["settings"]["address"] == ["fc00:bbbb::2/128"]

    @pytest.mark.parametrize("kwargs", [
        {"protocol": "vless", "uuid": "u", "tls": "none"},
        {"protocol": "vmess", "uuid": "u", "tls": "none"},
        {"protocol": "trojan", "password": "p"},
        {"protocol": "ss", "password": "p"},
    ])
    def test_only_wireguard_has_an_interface_address(self, kwargs):
        # The "failed to find available ipv6 table" failure comes from
        # xray's userspace WireGuard bringing up a netstack for the
        # `settings.address` interface CIDRs. NO other protocol has that
        # field — they dial the server via vnext/servers — so the failure
        # mode is WG-exclusive. Guard that it stays that way.
        node = _make_node(transport="tcp", **kwargs)
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ob = _find_outbound(cfg, "node-1")
        assert ob is not None
        assert "address" not in ob["settings"], (
            f"{kwargs['protocol']} grew a WG-style interface address"
        )

    def test_vless_ipv6_server_address_is_not_an_interface_table(self):
        # An IPv6 SERVER address is a dial target (goes in vnext), not an
        # interface address — it can fail to connect on an IPv4-only box,
        # but never with the WG "ipv6 table" error.
        node = _make_node(
            protocol="vless", uuid="u", tls="none",
            address="2001:db8::1",
        )
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ob = _find_outbound(cfg, "node-1")
        assert "address" not in ob["settings"]
        assert ob["settings"]["vnext"][0]["address"] == "2001:db8::1"

    def test_hy2_outbound_has_mark_255(self):
        node = _make_node(protocol="hy2", password="secret")
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ob = _find_outbound(cfg, "node-1")
        assert ob is not None
        assert ob["streamSettings"]["sockopt"]["mark"] == 255

    def test_socks_outbound_has_mark_255(self):
        node = _make_node(protocol="socks", tls="none")
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ob = _find_outbound(cfg, "node-1")
        assert ob is not None
        assert ob["streamSettings"]["sockopt"]["mark"] == 255

    def test_direct_outbound_has_mark_255(self):
        node = _make_node()
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ob = _find_outbound(cfg, "direct")
        assert ob is not None
        assert ob["streamSettings"]["sockopt"]["mark"] == 255

    def test_dns_out_has_mark_255(self):
        node = _make_node()
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ob = _find_outbound(cfg, "dns-out")
        assert ob is not None
        assert ob["streamSettings"]["sockopt"]["mark"] == 255


# ============================================================================
# Mode tests
# ============================================================================

class TestModes:
    def test_bypass_mode_all_direct(self):
        node = _make_node()
        cfg = generate_config(node, [node], [], "bypass", _default_settings())
        last_rule = cfg["routing"]["rules"][-1]
        assert last_rule["outboundTag"] == "direct"
        assert "0.0.0.0/0" in last_rule.get("ip", [])

    def test_global_mode_routes_to_active(self):
        node = _make_node(id=42)
        cfg = generate_config(node, [node], [], "global", _default_settings())
        last_rule = cfg["routing"]["rules"][-1]
        assert last_rule["outboundTag"] == "node-42"

    def test_global_mode_no_active_node(self):
        cfg = generate_config(None, [], [], "global", _default_settings())
        last_rule = cfg["routing"]["rules"][-1]
        assert last_rule["outboundTag"] == "direct"

    def test_rules_mode_default_direct(self):
        node = _make_node()
        cfg = generate_config(node, [node], [], "rules", _default_settings())
        last_rule = cfg["routing"]["rules"][-1]
        assert last_rule["outboundTag"] == "direct"
        assert "0.0.0.0/0" in last_rule.get("ip", [])

    # ── Private-CIDR bypass (the LAN-leak prevention check) ────────────
    #
    # In `global` mode every connection is force-routed through the
    # active node — UNLESS the `bypass_private` toggle is on, in which
    # case RFC 1918 + loopback + link-local + multicast + IPv6 ULA stay
    # direct (otherwise LAN-internal connections would tunnel out the
    # WAN, which is both surprising and breaks LAN services).
    # These tests pin the contract so a future config_gen refactor
    # can't silently drop the LAN-bypass rule.

    def test_global_mode_with_bypass_private_keeps_lan_direct(self):
        """`global + bypass_private=true` must emit a direct rule for
        the private CIDR set BEFORE the catch-all → active-node rule."""
        node = _make_node(id=42)
        cfg = generate_config(
            node, [node], [], "global",
            _default_settings(bypass_private="true"),
        )
        ip_rules = [r for r in cfg["routing"]["rules"]
                    if r.get("type") == "field" and "ip" in r]
        # Find the private-CIDR rule by looking for one whose ip list
        # contains 192.168.0.0/16 (the most user-visible LAN range).
        private_rule = next(
            (r for r in ip_rules if "192.168.0.0/16" in r["ip"]),
            None,
        )
        assert private_rule is not None, (
            "global+bypass_private should emit a direct rule for the "
            "private CIDR set; got rules: " + str(ip_rules)
        )
        assert private_rule["outboundTag"] == "direct"
        # Sanity — RFC 1918 + loopback + link-local + IPv6 LL all present
        for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                     "127.0.0.0/8", "169.254.0.0/16", "::1/128",
                     "fc00::/7", "fe80::/10"):
            assert cidr in private_rule["ip"], f"missing CIDR: {cidr}"
        # The private rule must appear BEFORE the catch-all (otherwise
        # the catch-all eats LAN packets first).
        private_idx = cfg["routing"]["rules"].index(private_rule)
        catchall_idx = next(
            i for i, r in enumerate(cfg["routing"]["rules"])
            if r.get("ip") == ["0.0.0.0/0", "::/0"]
        )
        assert private_idx < catchall_idx, (
            "private CIDR rule must come BEFORE the catch-all"
        )
        # Catch-all goes to the active node
        assert cfg["routing"]["rules"][catchall_idx]["outboundTag"] == "node-42"

    def test_global_mode_without_bypass_private_leaks_lan(self):
        """`bypass_private=false` in global mode: NO private rule;
        every connection (including LAN) hits the catch-all. This is
        the user's choice — we document the trade-off but don't add
        a guard. The test pins the behaviour so the choice stays
        meaningful."""
        node = _make_node(id=42)
        cfg = generate_config(
            node, [node], [], "global",
            _default_settings(bypass_private="false"),
        )
        ip_rules = [r for r in cfg["routing"]["rules"]
                    if r.get("type") == "field" and "ip" in r]
        # No private rule
        for r in ip_rules:
            if r.get("outboundTag") == "direct":
                assert "192.168.0.0/16" not in r.get("ip", []), (
                    "bypass_private=false should NOT emit a direct rule "
                    "for the LAN range"
                )

    def test_rules_mode_with_bypass_private(self):
        """`rules` mode honours `bypass_private` too — the rule comes
        BEFORE the user's own rules in the routing chain so a
        misconfigured user rule can't accidentally tunnel LAN."""
        node = _make_node(id=7)
        cfg = generate_config(
            node, [node], [], "rules",
            _default_settings(bypass_private="true"),
        )
        ip_rules = [r for r in cfg["routing"]["rules"]
                    if r.get("type") == "field" and "ip" in r]
        private_rule = next(
            (r for r in ip_rules if "192.168.0.0/16" in r["ip"]),
            None,
        )
        assert private_rule is not None
        assert private_rule["outboundTag"] == "direct"

    def test_bypass_mode_ignores_bypass_private(self):
        """`bypass` (direct) mode: everything goes direct anyway, so
        the `bypass_private` toggle is a no-op. Pin it so the toggle's
        irrelevance here is intentional and tested."""
        node = _make_node()
        cfg_on = generate_config(node, [node], [], "bypass",
                                 _default_settings(bypass_private="true"))
        cfg_off = generate_config(node, [node], [], "bypass",
                                  _default_settings(bypass_private="false"))
        # Both should produce the same `routing.rules` payload
        assert cfg_on["routing"]["rules"] == cfg_off["routing"]["rules"]
        # And the last rule is the catch-all to direct
        assert cfg_on["routing"]["rules"][-1]["outboundTag"] == "direct"

    def test_global_mode_skips_user_routing_rules(self):
        """User-defined RoutingRule rows are IGNORED in global mode —
        the routing chain consists only of (optional) private bypass +
        catch-all to active node. This is the contract the new
        Routing-page banner (frontend 1.3.5) tells the user about."""
        node = _make_node(id=11)
        # A user rule that would normally send ru-site.example direct
        user_rule = RoutingRule(
            id=99, name="bypass vk", rule_type="domain",
            match_value="ru-site.example", action="direct", order=0, enabled=True,
        )
        cfg = generate_config(
            node, [node], [user_rule], "global", _default_settings(),
        )
        # No rule mentions ru-site.example
        for r in cfg["routing"]["rules"]:
            assert "ru-site.example" not in str(r), (
                "global mode must NOT emit user RoutingRule rows; "
                f"found one referencing ru-site.example: {r}"
            )


# ============================================================================
# Inbound tests
# ============================================================================

class TestInbounds:
    def test_tproxy_inbounds_present(self):
        node = _make_node()
        cfg = generate_config(node, [node], [], "rules", _default_settings(inbound_mode="tproxy"))
        tags = [ib["tag"] for ib in cfg["inbounds"]]
        assert "tproxy-tcp" in tags
        assert "tproxy-udp" in tags
        assert "dns-in" in tags
        assert "socks-in" in tags
        assert "http-in" in tags

    def test_tun_inbound_present(self):
        node = _make_node()
        cfg = generate_config(node, [node], [], "rules", _default_settings(inbound_mode="tun"))
        tags = [ib["tag"] for ib in cfg["inbounds"]]
        assert "tun-in" in tags
        assert "tproxy-tcp" not in tags
        assert "tproxy-udp" not in tags

    def test_socks_http_always_present(self):
        for mode in ("tproxy", "tun"):
            cfg = generate_config(None, [], [], "rules", _default_settings(inbound_mode=mode))
            tags = [ib["tag"] for ib in cfg["inbounds"]]
            assert "socks-in" in tags, f"socks-in missing for inbound_mode={mode}"
            assert "http-in" in tags, f"http-in missing for inbound_mode={mode}"


# ============================================================================
# Stats API tests
# ============================================================================

class TestStatsAPI:
    def test_stats_api_section_present(self):
        cfg = generate_config(None, [], [], "rules", _default_settings())
        assert "stats" in cfg
        assert "api" in cfg
        assert "policy" in cfg

    def test_stats_api_inbound_present(self):
        cfg = generate_config(None, [], [], "rules", _default_settings())
        api_ib = _find_inbound(cfg, "api")
        assert api_ib is not None
        assert api_ib["port"] == 10085
        assert api_ib["listen"] == "127.0.0.1"


# ============================================================================
# Routing rule conversion tests
# ============================================================================

class TestRoutingRuleConversion:
    def test_domain_rule_to_xray(self):
        node = _make_node()
        rule = RoutingRule(
            id=1, name="test", rule_type="domain",
            match_value="google.com", action="direct", enabled=True, order=100,
        )
        cfg = generate_config(node, [node], [rule], "rules", _default_settings())
        # Find the domain rule (not the API, DNS, private, or default rules)
        domain_rules = [r for r in cfg["routing"]["rules"] if "domain" in r]
        assert len(domain_rules) >= 1
        # Bare entries are auto-prefixed with `domain:` (suffix match) by
        # _routing_rule_to_xray — the old behaviour (substring match for
        # bare entries) was a bug that was easy to hit. See config_gen.py.
        assert "domain:google.com" in domain_rules[0]["domain"]
        assert domain_rules[0]["outboundTag"] == "direct"

    def test_domain_rule_keeps_explicit_prefixes(self):
        """Entries that already carry a known matcher prefix pass through unchanged."""
        node = _make_node()
        rule = RoutingRule(
            id=1, name="mixed", rule_type="domain",
            match_value="bare.com,domain:explicit.com,full:exact.host,keyword:foo,regexp:^bar$",
            action="direct", enabled=True, order=100,
        )
        cfg = generate_config(node, [node], [rule], "rules", _default_settings())
        domain_rules = [r for r in cfg["routing"]["rules"] if "domain" in r and any("bar" in str(v) for v in r.get("domain", []))]
        assert len(domain_rules) >= 1
        domains = domain_rules[0]["domain"]
        assert "domain:bare.com" in domains
        assert "domain:explicit.com" in domains
        assert "full:exact.host" in domains
        assert "keyword:foo" in domains
        assert "regexp:^bar$" in domains

    def test_geoip_rule_to_xray(self):
        node = _make_node()
        rule = RoutingRule(
            id=1, name="geo", rule_type="geoip",
            match_value="ru", action="direct", enabled=True, order=100,
        )
        cfg = generate_config(node, [node], [rule], "rules", _default_settings())
        ip_rules = [r for r in cfg["routing"]["rules"] if "ip" in r and any("geoip:ru" in str(v) for v in r.get("ip", []))]
        assert len(ip_rules) >= 1
        assert "geoip:ru" in ip_rules[0]["ip"]

    def test_geosite_rule_to_xray(self):
        node = _make_node()
        rule = RoutingRule(
            id=1, name="geo", rule_type="geosite",
            match_value="cn", action="direct", enabled=True, order=100,
        )
        cfg = generate_config(node, [node], [rule], "rules", _default_settings())
        domain_rules = [r for r in cfg["routing"]["rules"] if "domain" in r and any("geosite:cn" in str(v) for v in r.get("domain", []))]
        assert len(domain_rules) >= 1
        assert "geosite:cn" in domain_rules[0]["domain"]

    def test_proxy_action_uses_active_node(self):
        node = _make_node(id=7)
        rule = RoutingRule(
            id=1, name="proxy", rule_type="domain",
            match_value="proxy.com", action="proxy", enabled=True, order=100,
        )
        cfg = generate_config(node, [node], [rule], "rules", _default_settings())
        domain_rules = [r for r in cfg["routing"]["rules"] if "domain" in r and "domain:proxy.com" in r.get("domain", [])]
        assert len(domain_rules) >= 1
        assert domain_rules[0]["outboundTag"] == "node-7"

    def test_proxy_action_no_active_falls_to_direct(self):
        rule = RoutingRule(
            id=1, name="proxy", rule_type="domain",
            match_value="proxy.com", action="proxy", enabled=True, order=100,
        )
        cfg = generate_config(None, [], [rule], "rules", _default_settings())
        domain_rules = [r for r in cfg["routing"]["rules"] if "domain" in r and "domain:proxy.com" in r.get("domain", [])]
        assert len(domain_rules) >= 1
        assert domain_rules[0]["outboundTag"] == "direct"

    def test_balancer_action(self):
        node = _make_node(id=10)
        rule = RoutingRule(
            id=1, name="bal", rule_type="domain",
            match_value="balanced.com", action="balancer:1", enabled=True, order=100,
        )
        bg = BalancerGroup(id=1, name="test-bg", enabled=True, node_ids="[10]", strategy="leastPing")
        cfg = generate_config(node, [node], [rule], "rules", _default_settings(), balancer_groups=[bg])
        domain_rules = [r for r in cfg["routing"]["rules"] if "domain" in r and "domain:balanced.com" in r.get("domain", [])]
        assert len(domain_rules) >= 1
        assert domain_rules[0].get("balancerTag") == "balancer-1"


# ============================================================================
# Chain tunnel tests
# ============================================================================

class TestChainTunnel:
    def test_chain_node_proxy_settings(self):
        chain_node = _make_node(id=2, name="chain", address="5.5.5.5")
        main_node = _make_node(id=1, name="main", chain_node_id=2)
        all_nodes = [main_node, chain_node]
        cfg = generate_config(main_node, all_nodes, [], "global", _default_settings())
        ob = _find_outbound(cfg, "node-1")
        assert ob is not None
        assert "proxySettings" in ob, f"proxySettings missing: {json.dumps(ob, indent=2)}"
        assert ob["proxySettings"]["tag"] == "node-2"

    def test_chain_node_outbound_included(self):
        chain_node = _make_node(id=2, name="chain", address="5.5.5.5")
        main_node = _make_node(id=1, name="main", chain_node_id=2)
        all_nodes = [main_node, chain_node]
        cfg = generate_config(main_node, all_nodes, [], "global", _default_settings())
        chain_ob = _find_outbound(cfg, "node-2")
        assert chain_ob is not None, "Chain node outbound not found in config"

    def test_self_chain_ignored(self):
        node = _make_node(id=1, chain_node_id=1)
        cfg = generate_config(node, [node], [], "global", _default_settings())
        ob = _find_outbound(cfg, "node-1")
        assert ob is not None
        assert "proxySettings" not in ob

    def test_three_hop_chain_fully_wired(self):
        # exit(1) → mid(2) → entry(3). Every hop must carry proxySettings
        # except the entry, which dials direct. (Regression: previously
        # only the first hop wired and node-2 silently dialed direct.)
        entry = _make_node(id=3, name="entry", address="3.3.3.3")
        mid = _make_node(id=2, name="mid", address="2.2.2.2", chain_node_id=3)
        exit_ = _make_node(id=1, name="exit", address="1.1.1.1", chain_node_id=2)
        cfg = generate_config(exit_, [exit_, mid, entry], [], "global", _default_settings())

        assert _find_outbound(cfg, "node-1")["proxySettings"]["tag"] == "node-2"
        assert _find_outbound(cfg, "node-2")["proxySettings"]["tag"] == "node-3"
        assert "proxySettings" not in _find_outbound(cfg, "node-3")
        # transportLayer flag preserved on each hop (→ sockopt.dialerProxy)
        assert _find_outbound(cfg, "node-1")["proxySettings"]["transportLayer"] is True
        assert _find_outbound(cfg, "node-2")["proxySettings"]["transportLayer"] is True

    def test_chain_through_wireguard_is_skipped(self):
        # xray can't tunnel THROUGH a WireGuard outbound (0 bytes at
        # runtime, verified live). config_gen must drop such a link so the
        # node dials direct instead of into a dead WG tunnel.
        wg_relay = _make_node(id=2, protocol="wireguard", name="wg-relay", address="2.2.2.2",
                              wg_private_key="p", wg_public_key="pub",
                              wg_local_address="10.0.0.2/32")
        exit_ = _make_node(id=1, protocol="vless", name="exit", address="1.1.1.1", chain_node_id=2)
        cfg = generate_config(exit_, [exit_, wg_relay], [], "global", _default_settings())
        assert "proxySettings" not in _find_outbound(cfg, "node-1")

    def test_wireguard_exit_over_stream_is_wired(self):
        # The valid direction: WireGuard as the EXIT hop, chaining through a
        # stream relay (WG-over-VLESS works at runtime). MUST wire.
        vless = _make_node(id=2, protocol="vless", name="relay", address="2.2.2.2")
        wg_exit = _make_node(id=1, protocol="wireguard", name="wg-exit", address="1.1.1.1",
                             wg_private_key="p", wg_public_key="pub",
                             wg_local_address="10.0.0.2/32", chain_node_id=2)
        cfg = generate_config(wg_exit, [wg_exit, vless], [], "global", _default_settings())
        ob = _find_outbound(cfg, "node-1")
        assert ob["protocol"] == "wireguard"
        assert ob["proxySettings"]["tag"] == "node-2"
        assert "proxySettings" not in _find_outbound(cfg, "node-2")

    def test_chain_cycle_truncated(self):
        # 1 → 2 → 1 (cycle). Must terminate (no infinite recursion) and
        # break the loop: node-1 chains to node-2, but node-2's chain back
        # to node-1 is dropped so node-2 dials direct.
        n1 = _make_node(id=1, name="a", address="1.1.1.1", chain_node_id=2)
        n2 = _make_node(id=2, name="b", address="2.2.2.2", chain_node_id=1)
        cfg = generate_config(n1, [n1, n2], [], "global", _default_settings())
        assert _find_outbound(cfg, "node-1")["proxySettings"]["tag"] == "node-2"
        assert "proxySettings" not in _find_outbound(cfg, "node-2")


# ============================================================================
# Balancer tests
# ============================================================================

class TestBalancers:
    def test_balancer_group_in_routing(self):
        node1 = _make_node(id=1, name="n1", address="1.1.1.1")
        node2 = _make_node(id=2, name="n2", address="2.2.2.2")
        bg = BalancerGroup(id=5, name="my-bg", enabled=True, node_ids="[1,2]", strategy="random")
        rule = RoutingRule(
            id=1, name="bal-rule", rule_type="domain",
            match_value="lb.com", action="balancer:5", enabled=True, order=100,
        )
        cfg = generate_config(node1, [node1, node2], [rule], "rules", _default_settings(), balancer_groups=[bg])
        assert "balancers" in cfg["routing"]
        balancers = cfg["routing"]["balancers"]
        bg_entry = next((b for b in balancers if b["tag"] == "balancer-5"), None)
        assert bg_entry is not None, f"balancer-5 not found: {json.dumps(balancers, indent=2)}"
        assert "node-1" in bg_entry["selector"]
        assert "node-2" in bg_entry["selector"]
        assert bg_entry["strategy"]["type"] == "random"


# ============================================================================
# DNS tests
# ============================================================================

def _dns_addresses(cfg) -> list:
    """Helper: extract the `address` field from every DNS server entry,
    regardless of dict-vs-string shape. Since v1.3.5 every entry is a
    dict (with `outboundTag: direct`) — pre-1.3.5 the primary upstream
    was a bare string. This helper hides the form change so the tests
    can assert on intent (the upstream address) rather than shape."""
    out = []
    for s in cfg["dns"]["servers"]:
        if isinstance(s, str):
            out.append(s)
        elif isinstance(s, dict):
            out.append(s.get("address", ""))
    return out


class TestDNS:
    def test_dot_uses_tcp_scheme(self):
        """xray-core doesn't support native DoT (PR #2042 never merged), so
        the `dot` mode falls back to plaintext DNS-over-TCP on port 53.
        UI surfaces this as "DNS over TCP (not encrypted)"."""
        cfg = generate_config(None, [], [], "rules", _default_settings(dns_mode="dot"))
        addrs = _dns_addresses(cfg)
        assert any(s.startswith("tcp://") and s.endswith(":53") for s in addrs), \
            f"No tcp://...:53 server found: {addrs}"

    def test_doh_uses_https(self):
        cfg = generate_config(None, [], [], "rules", _default_settings(dns_mode="doh"))
        addrs = _dns_addresses(cfg)
        assert any(s.startswith("https://") for s in addrs), \
            f"No https:// server found: {addrs}"

    # ── DNS-upstream outboundTag pinning (since v1.3.5) ────────────────
    #
    # Pinning DNS servers to `outboundTag: direct` is the fix for the
    # DNS burn-in lockup. The contract these tests pin: every
    # DNS server entry must end up as a dict with outboundTag=direct,
    # regardless of whether the operator configured it as a bare
    # upstream, a per-domain object, or a fallback. The only exception
    # is the `fakedns` sentinel which has no upstream to dial.

    def test_primary_upstream_pinned_to_direct(self):
        """The plain `dns_upstream=8.8.8.8` setting used to render as
        a bare string. Now it must be a dict with outboundTag=direct
        so user routing rules can't tunnel DNS upstream through the
        proxy. Without this fix, a `port: 0-65535 → proxy` rule (or
        `geoip:!ru → proxy` catch-all) would catch the DNS connection
        and break the whole resolver when the proxy node hiccups."""
        cfg = generate_config(None, [], [], "rules", _default_settings())
        servers = cfg["dns"]["servers"]
        # Every entry must be a dict with outboundTag=direct
        for s in servers:
            assert isinstance(s, dict), (
                f"Bare-string DNS server entry leaks back through "
                f"user routing rules: {s!r}"
            )
            assert s.get("outboundTag") == "direct", (
                f"DNS server missing outboundTag=direct: {s!r}"
            )
        # And the primary upstream is in there
        addrs = [s["address"] for s in servers]
        assert "8.8.8.8" in addrs, addrs

    def test_secondary_upstream_pinned(self):
        cfg = generate_config(
            None, [], [], "rules",
            _default_settings(
                dns_upstream="8.8.8.8",
                dns_upstream_secondary="1.1.1.1",
            ),
        )
        secondary = next(
            (s for s in cfg["dns"]["servers"]
             if isinstance(s, dict) and s.get("address") == "1.1.1.1"),
            None,
        )
        assert secondary is not None, cfg["dns"]["servers"]
        assert secondary["outboundTag"] == "direct"

    def test_fallback_upstream_pinned(self):
        cfg = generate_config(
            None, [], [], "rules",
            _default_settings(
                dns_upstream="8.8.8.8",
                dns_fallback="9.9.9.9",
            ),
        )
        fallback = next(
            (s for s in cfg["dns"]["servers"]
             if isinstance(s, dict) and s.get("address") == "9.9.9.9"),
            None,
        )
        assert fallback is not None, cfg["dns"]["servers"]
        assert fallback["outboundTag"] == "direct"

    def test_per_rule_dns_pinned(self):
        """Per-DNSRule entries (already dicts with `domains`) gain
        outboundTag=direct without losing their existing fields."""
        from app.models import DNSRule
        rules = [DNSRule(
            id=1, name="r", domain_match="ru-site.example",
            dns_server="77.88.8.8", dns_type="plain",
            enabled=True, order=10,
        )]
        cfg = generate_config(
            None, [], [], "rules", _default_settings(), dns_rules=rules,
        )
        yandex = next(
            (s for s in cfg["dns"]["servers"]
             if isinstance(s, dict) and s.get("address") == "77.88.8.8"),
            None,
        )
        assert yandex is not None, cfg["dns"]["servers"]
        assert yandex["outboundTag"] == "direct"
        # Existing fields preserved
        assert "ru-site.example" in yandex.get("domains", [])

    def test_ru_bypass_dns_pinned(self):
        """`bypass_ru_dns=true` adds a Yandex-DNS server for RU domains.
        That entry must also be pinned to direct — otherwise the RU
        bypass becomes self-defeating (RU DNS query goes through the
        VPN that's masquerading as a non-RU exit)."""
        cfg = generate_config(
            None, [], [], "rules",
            _default_settings(bypass_ru_dns="true"),
        )
        ru = next(
            (s for s in cfg["dns"]["servers"]
             if isinstance(s, dict) and s.get("tag") == "ru-dns"),
            None,
        )
        assert ru is not None, cfg["dns"]["servers"]
        assert ru["outboundTag"] == "direct"

    def test_system_dns_port_rule_precedes_user_rules(self):
        """The system-level `port:53 → direct` rule must appear BEFORE
        any user rule in the routing chain. xray evaluates rules
        top-to-bottom and stops at the first match — so an immutable
        system rule for port 53 catches DNS-upstream dials before a
        user's `port: 0-65535 → proxy` catch-all can route them
        through the broken VPN node. This is the real fix for the
        burn-in lockup; the `proxySettings.tag: direct` on dns-out
        only chains outbounds (doesn't override routing for the dial
        destination).

        Pin the rule's position: it must come after the dns-in
        routing rule (so DNS QUERIES still reach dns-out) but BEFORE
        any user RoutingRule. We test all three modes — the system
        rule applies regardless of `mode`."""
        from app.models import RoutingRule

        # A nasty user rule that would otherwise catch DNS dials too.
        bad_user_rule = RoutingRule(
            id=99, name="port-catchall", rule_type="port",
            match_value="0-65535", action="proxy", order=100, enabled=True,
        )
        node = _make_node(id=42)

        for mode in ("rules", "global", "bypass"):
            cfg = generate_config(
                node, [node], [bad_user_rule], mode, _default_settings(),
            )
            rules = cfg["routing"]["rules"]
            # Find the system port-53 rule
            dns_port_rule_idx = next(
                (i for i, r in enumerate(rules)
                 if r.get("port") == "53" and r.get("outboundTag") == "direct"),
                None,
            )
            assert dns_port_rule_idx is not None, (
                f"missing system port:53 → direct rule in mode={mode!r}: {rules}"
            )

            # In `rules` mode the user rule should appear in the chain.
            # In `global`/`bypass` user rules are dropped — so the
            # presence-after check only applies to `rules` mode.
            if mode == "rules":
                user_rule_idx = next(
                    (i for i, r in enumerate(rules)
                     if r.get("port") == "0-65535"),
                    None,
                )
                assert user_rule_idx is not None, "user rule missing in `rules` mode"
                assert dns_port_rule_idx < user_rule_idx, (
                    f"system port:53 rule (idx {dns_port_rule_idx}) "
                    f"must come BEFORE user rule (idx {user_rule_idx})"
                )

    def test_dns_out_outbound_pinned_to_direct(self):
        """The `dns-out` outbound must carry `proxySettings.tag: direct`
        so its upstream-DNS dial (the TCP/UDP connection to the actual
        DNS server) bypasses the user's routing rules. Belt-and-
        suspenders with the per-server `outboundTag: direct` — that one
        wins for queries that go through xray's routing engine; this
        one wins for the dial that the dns-out outbound itself makes.
        Without this, a port-range catch-all routing rule tunnels DNS
        through the broken VPN node (real-world breakage on 1.3.4)."""
        cfg = generate_config(None, [], [], "rules", _default_settings())
        dns_out = next(
            (o for o in cfg["outbounds"] if o.get("tag") == "dns-out"),
            None,
        )
        assert dns_out is not None
        assert dns_out.get("proxySettings", {}).get("tag") == "direct", (
            f"dns-out missing proxySettings.tag=direct: {dns_out!r}"
        )

    def test_query_strategy_defaults_to_useipv4(self):
        """DNS section must default queryStrategy=UseIPv4 to close the
        IPv6 bypass leak. PiTun's TPROXY is IPv4-only; an AAAA answer
        would let a LAN client route IPv6 around the proxy via its
        router-provided default route. Returning only A records keeps
        clients on the intercepted IPv4 path."""
        cfg = generate_config(None, [], [], "rules", _default_settings())
        assert cfg["dns"].get("queryStrategy") == "UseIPv4"

    def test_query_strategy_overridable(self):
        """Advanced operators who handle IPv6 elsewhere can override."""
        cfg = generate_config(
            None, [], [], "rules",
            _default_settings(dns_query_strategy="UseIP"),
        )
        assert cfg["dns"].get("queryStrategy") == "UseIP"

    def test_query_strategy_invalid_falls_back_to_useipv4(self):
        """A garbage value must not reach xray — coerce to the safe
        default rather than emit an invalid config."""
        cfg = generate_config(
            None, [], [], "rules",
            _default_settings(dns_query_strategy="nonsense"),
        )
        assert cfg["dns"].get("queryStrategy") == "UseIPv4"

    def test_fakedns_sentinel_not_wrapped(self):
        """The `fakedns` string is xray's sentinel for the FakeDNS
        protocol — it has no upstream to dial, so wrapping it in an
        outboundTag dict would either be a no-op or confuse xray's
        config parser. Pin the "leave it alone" behaviour."""
        cfg = generate_config(
            None, [], [], "rules",
            _default_settings(fakedns_enabled="true"),
        )
        servers = cfg["dns"]["servers"]
        assert "fakedns" in servers, servers

    def test_per_rule_dns_formats_by_type(self):
        """Per-rule DNS server address is formatted according to `dns_type`
        the same way as the global upstream: doh → https://, dot → tcp://:53,
        plain → raw host. Ensures the per-rule UI matches actual xray behavior."""
        rules = [
            DNSRule(id=1, name="plain", domain_match="a.com",
                    dns_server="1.1.1.1", dns_type="plain", enabled=True, order=10),
            DNSRule(id=2, name="doh", domain_match="b.com",
                    dns_server="1.0.0.1", dns_type="doh", enabled=True, order=20),
            DNSRule(id=3, name="dot", domain_match="c.com",
                    dns_server="9.9.9.9", dns_type="dot", enabled=True, order=30),
        ]
        cfg = generate_config(None, [], [], "rules", _default_settings(), dns_rules=rules)
        # Filter to per-rule entries (those carry a `domains` list).
        # Since v1.3.5 the catch-all upstream is ALSO a dict, but
        # without `domains` — exclude it from the per-rule mapping.
        obj_servers = [s for s in cfg["dns"]["servers"]
                       if isinstance(s, dict) and "domains" in s]
        addrs = {s["address"]: s["domains"] for s in obj_servers}
        assert "1.1.1.1" in addrs and "a.com" in addrs["1.1.1.1"]
        assert "https://1.0.0.1/dns-query" in addrs and "b.com" in addrs["https://1.0.0.1/dns-query"]
        assert "tcp://9.9.9.9:53" in addrs and "c.com" in addrs["tcp://9.9.9.9:53"]

    def test_per_rule_dns_passes_through_user_scheme(self):
        """If the user already typed a scheme (https://..., quic+local://...),
        we must not double-prefix it."""
        rules = [
            DNSRule(id=1, name="user-doh", domain_match="x.com",
                    dns_server="https://cloudflare-dns.com/dns-query",
                    dns_type="doh", enabled=True, order=10),
            DNSRule(id=2, name="user-quic", domain_match="y.com",
                    dns_server="quic+local://dns.adguard.com",
                    dns_type="plain", enabled=True, order=20),
        ]
        cfg = generate_config(None, [], [], "rules", _default_settings(), dns_rules=rules)
        obj_servers = [s for s in cfg["dns"]["servers"] if isinstance(s, dict)]
        addrs = {s["address"] for s in obj_servers}
        assert "https://cloudflare-dns.com/dns-query" in addrs
        assert "quic+local://dns.adguard.com" in addrs


# ============================================================================
# RoutingSet — per-device-group routing (v1.4)
# ============================================================================

from app.models import RoutingSet


def _make_routing_set(id, name, tproxy_port, order=0):
    return RoutingSet(
        id=id, name=name, tproxy_port=tproxy_port,
        order=order, description=None,
    )


class TestRoutingSetInbounds:
    def test_no_set_inbounds_when_no_sets(self):
        """No RoutingSets → config matches v1.3.x exactly."""
        cfg = generate_config(
            None, [], [], "rules", _default_settings(),
            routing_sets=None, device_set_macs=None,
        )
        # Only default tproxy inbounds + api/dns/socks/http (no per-set)
        set_inbounds = [
            ib for ib in cfg["inbounds"]
            if ib["tag"].startswith("tproxy-set-")
        ]
        assert set_inbounds == []

    def test_empty_set_skipped(self):
        """Set with zero member devices generates no inbound."""
        rs = _make_routing_set(1, "Kids", 65500)
        cfg = generate_config(
            None, [], [], "rules", _default_settings(),
            routing_sets=[rs],
            device_set_macs={1: []},  # empty
        )
        assert _find_inbound(cfg, "tproxy-set-1") is None

    def test_set_with_devices_gets_inbound_wildcard_bound(self):
        """Per-set TPROXY inbound MUST listen on 0.0.0.0, not 127.0.0.1.

        TPROXY does not rewrite destination — redirected packets keep
        their ORIGINAL dst (e.g. 142.250.190.78:443 for google). A
        loopback-bound IP_TRANSPARENT socket silently drops those
        packets because the kernel only delivers them to a wildcard
        listener. Regression: initial v1.4 shipped 127.0.0.1, which
        marked traffic correctly in nftables but xray accepted zero of
        it (live caught on 1.3 smoke test, see config_gen comment).
        """
        rs = _make_routing_set(1, "Kids", 65500)
        cfg = generate_config(
            None, [], [], "rules", _default_settings(),
            routing_sets=[rs],
            device_set_macs={1: ["aa:bb:cc:dd:ee:01"]},
        )
        inbound = _find_inbound(cfg, "tproxy-set-1")
        assert inbound is not None
        assert inbound["port"] == 65500
        # MUST be 0.0.0.0 — see docstring above. TPROXY sockets only
        # accept packets with the matching fwmark, so wildcard bind
        # isn't a LAN exposure.
        assert inbound["listen"] == "0.0.0.0"
        assert inbound["protocol"] == "dokodemo-door"
        # One port serves BOTH TCP and UDP
        assert inbound["settings"]["network"] == "tcp,udp"
        assert inbound["streamSettings"]["sockopt"]["tproxy"] == "tproxy"
        assert inbound["streamSettings"]["sockopt"]["mark"] == 255

    def test_multiple_sets_get_separate_ports(self):
        rs_a = _make_routing_set(1, "Kids", 65500)
        rs_b = _make_routing_set(2, "Work", 65501)
        cfg = generate_config(
            None, [], [], "rules", _default_settings(),
            routing_sets=[rs_a, rs_b],
            device_set_macs={
                1: ["aa:bb:cc:dd:ee:01"],
                2: ["aa:bb:cc:dd:ee:02"],
            },
        )
        assert _find_inbound(cfg, "tproxy-set-1")["port"] == 65500
        assert _find_inbound(cfg, "tproxy-set-2")["port"] == 65501


class TestRoutingSetRules:
    def test_per_set_rule_has_inbound_tag(self):
        rs = _make_routing_set(1, "Kids", 65500)
        rule = RoutingRule(
            id=1, name="block ads", rule_type="domain",
            match_value="doubleclick.net", action="block",
            enabled=True, order=10, routing_set_id=1,
        )
        cfg = generate_config(
            None, [], [rule], "rules", _default_settings(),
            routing_sets=[rs],
            device_set_macs={1: ["aa:bb:cc:dd:ee:01"]},
        )
        # Find the rule in routing.rules — must carry inboundTag filter
        per_set_rules = [
            r for r in cfg["routing"]["rules"]
            if r.get("inboundTag") == ["tproxy-set-1"]
        ]
        assert len(per_set_rules) == 1
        assert per_set_rules[0]["outboundTag"] == "block"

    def test_global_rule_no_inbound_tag(self):
        """NULL routing_set_id → no inboundTag → applies to ALL inbounds."""
        rule = RoutingRule(
            id=1, name="block ads global", rule_type="domain",
            match_value="ads.com", action="block",
            enabled=True, order=10, routing_set_id=None,
        )
        cfg = generate_config(
            None, [], [rule], "rules", _default_settings(),
        )
        matching = [
            r for r in cfg["routing"]["rules"]
            if r.get("outboundTag") == "block"
            and r.get("domain")
            and any("ads.com" in d for d in r["domain"])
        ]
        assert len(matching) == 1
        assert "inboundTag" not in matching[0]

    def test_per_set_rules_emitted_before_global(self):
        """First-match-wins: per-set rules must precede globals in
        routing.rules so a device in Kids hits its set rules first
        before falling through to globals."""
        rs = _make_routing_set(1, "Kids", 65500)
        per_set = RoutingRule(
            id=1, name="kids-only", rule_type="domain",
            match_value="kids.com", action="block",
            enabled=True, order=10, routing_set_id=1,
        )
        glob = RoutingRule(
            id=2, name="global-rule", rule_type="domain",
            match_value="global.com", action="block",
            enabled=True, order=20, routing_set_id=None,
        )
        cfg = generate_config(
            None, [], [per_set, glob], "rules", _default_settings(),
            routing_sets=[rs],
            device_set_macs={1: ["aa:bb:cc:dd:ee:01"]},
        )
        rule_indexes = {}
        for i, r in enumerate(cfg["routing"]["rules"]):
            for d in r.get("domain", []):
                if "kids.com" in d:
                    rule_indexes["kids"] = i
                if "global.com" in d:
                    rule_indexes["global"] = i
        assert rule_indexes["kids"] < rule_indexes["global"], (
            f"per-set rule at {rule_indexes['kids']} must precede global "
            f"at {rule_indexes['global']}"
        )

    def test_rule_in_empty_set_dropped(self):
        """Rule pointing at a set with no devices → silently skipped,
        does NOT leak into the default inbound as a global rule."""
        rs = _make_routing_set(1, "Kids", 65500)
        rule = RoutingRule(
            id=1, name="orphan", rule_type="domain",
            match_value="orphan.com", action="block",
            enabled=True, order=10, routing_set_id=1,
        )
        cfg = generate_config(
            None, [], [rule], "rules", _default_settings(),
            routing_sets=[rs],
            device_set_macs={1: []},  # empty Kids
        )
        for r in cfg["routing"]["rules"]:
            for d in r.get("domain", []):
                assert "orphan.com" not in d, (
                    "rule for empty set must not appear in any inbound"
                )

    def test_rule_in_unknown_set_dropped(self):
        """Rule with set_id pointing at non-existent RoutingSet →
        silently skipped (defensive: orphan rule must not leak)."""
        rule = RoutingRule(
            id=1, name="orphan", rule_type="domain",
            match_value="orphan.com", action="block",
            enabled=True, order=10, routing_set_id=9999,
        )
        cfg = generate_config(
            None, [], [rule], "rules", _default_settings(),
            routing_sets=[],
            device_set_macs={},
        )
        for r in cfg["routing"]["rules"]:
            for d in r.get("domain", []):
                assert "orphan.com" not in d

    def test_set_order_respected(self):
        """Sets with lower `order` emit rules first."""
        rs_b = _make_routing_set(2, "B", 65501, order=2)  # later
        rs_a = _make_routing_set(1, "A", 65500, order=1)  # earlier
        rule_a = RoutingRule(
            id=1, name="rule-a", rule_type="domain",
            match_value="a.com", action="block",
            enabled=True, order=10, routing_set_id=1,
        )
        rule_b = RoutingRule(
            id=2, name="rule-b", rule_type="domain",
            match_value="b.com", action="block",
            enabled=True, order=10, routing_set_id=2,
        )
        # Pass sets in REVERSE order to verify the function sorts them
        cfg = generate_config(
            None, [], [rule_a, rule_b], "rules", _default_settings(),
            routing_sets=[rs_b, rs_a],
            device_set_macs={1: ["aa:01"], 2: ["aa:02"]},
        )
        first_kids_idx = None
        first_work_idx = None
        for i, r in enumerate(cfg["routing"]["rules"]):
            if r.get("inboundTag") == ["tproxy-set-1"]:
                first_kids_idx = first_kids_idx if first_kids_idx is not None else i
            if r.get("inboundTag") == ["tproxy-set-2"]:
                first_work_idx = first_work_idx if first_work_idx is not None else i
        assert first_kids_idx is not None and first_work_idx is not None
        assert first_kids_idx < first_work_idx


class TestNodeCircleBalancer:
    """Active-NodeCircle balancer indirection — the foundation of seamless
    rotation: an enabled circle routes proxy traffic at a balancer over ALL
    members (preloaded), so rotation hot-swaps the selected node via the gRPC
    balancerOverride API with no xray restart (live connections survive)."""

    def _wg(self, id, relay_id=5):
        return _make_node(
            id=id, protocol="wireguard",
            wg_private_key="cHJpdmtleXByaXZrZXlwcml2a2V5cHJpdmtleXByaT0=",
            wg_public_key="cHVia2V5cHVia2V5cHVia2V5cHVia2V5cHVia2V5cHU9",
            wg_endpoint=f"203.0.113.{id}:51820", address=f"203.0.113.{id}",
            chain_node_id=relay_id,
        )

    def test_active_circle_emits_balancer_and_routes_via_it(self):
        relay = _make_node(id=5, protocol="vless")
        nodes = [relay, self._wg(14), self._wg(15), self._wg(16)]
        cfg = generate_config(
            self._wg(14), nodes, [], "global", _default_settings(),
            active_circle_id=2, active_circle_node_ids=[14, 15, 16],
        )
        bals = cfg["routing"].get("balancers", [])
        cb = next((b for b in bals if b["tag"] == "circle-2"), None)
        assert cb is not None, "circle balancer must be emitted"
        assert set(cb["selector"]) == {"node-14", "node-15", "node-16"}
        # every member (+ the shared relay) preloaded so rotation can hot-swap
        for tag in ("node-5", "node-14", "node-15", "node-16"):
            assert _find_outbound(cfg, tag) is not None, f"{tag} must be preloaded"
        # global catch-all routes to the balancer, NOT a single node outbound
        catchall = [r for r in cfg["routing"]["rules"] if r.get("ip") == ["0.0.0.0/0", "::/0"]]
        assert any(r.get("balancerTag") == "circle-2" for r in catchall)
        assert not any(str(r.get("outboundTag", "")).startswith("node-") for r in catchall)

    def test_circle_proxy_rule_targets_balancer(self):
        relay = _make_node(id=5, protocol="vless")
        rule = RoutingRule(
            id=1, name="all", rule_type="domain", match_value="example.com",
            action="proxy", enabled=True, order=0, routing_set_id=None,
        )
        cfg = generate_config(
            self._wg(14), [relay, self._wg(14), self._wg(15)], [rule], "rules",
            _default_settings(), active_circle_id=2, active_circle_node_ids=[14, 15],
        )
        assert any(r.get("balancerTag") == "circle-2" for r in cfg["routing"]["rules"]), \
            "proxy rule must target the circle balancer when a circle is active"

    def test_no_active_circle_routes_to_single_node(self):
        node = _make_node(id=14)
        cfg = generate_config(node, [node], [], "global", _default_settings())
        catchall = [r for r in cfg["routing"]["rules"] if r.get("ip") == ["0.0.0.0/0", "::/0"]]
        assert any(r.get("outboundTag") == "node-14" for r in catchall)
        assert not cfg["routing"].get("balancers")

    def test_resolve_active_circle_respects_enabled_and_membership(self):
        from app.core.config_gen import resolve_active_circle

        class _C:
            def __init__(self, id, enabled, ids):
                self.id, self.enabled, self.node_ids = id, enabled, json.dumps(ids)
        assert resolve_active_circle([_C(2, False, [14, 15])], 14) == (None, None)
        assert resolve_active_circle([_C(2, True, [14, 15])], 14) == (2, [14, 15])
        assert resolve_active_circle([_C(2, True, [14, 15])], 99) == (None, None)
