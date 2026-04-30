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
        assert "google.com" in domain_rules[0]["domain"]
        assert domain_rules[0]["outboundTag"] == "direct"

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
        domain_rules = [r for r in cfg["routing"]["rules"] if "domain" in r and "proxy.com" in r.get("domain", [])]
        assert len(domain_rules) >= 1
        assert domain_rules[0]["outboundTag"] == "node-7"

    def test_proxy_action_no_active_falls_to_direct(self):
        rule = RoutingRule(
            id=1, name="proxy", rule_type="domain",
            match_value="proxy.com", action="proxy", enabled=True, order=100,
        )
        cfg = generate_config(None, [], [rule], "rules", _default_settings())
        domain_rules = [r for r in cfg["routing"]["rules"] if "domain" in r and "proxy.com" in r.get("domain", [])]
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
        domain_rules = [r for r in cfg["routing"]["rules"] if "domain" in r and "balanced.com" in r.get("domain", [])]
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

class TestDNS:
    def test_dot_uses_tcp_scheme(self):
        """xray-core doesn't support native DoT (PR #2042 never merged), so
        the `dot` mode falls back to plaintext DNS-over-TCP on port 53.
        UI surfaces this as "DNS over TCP (not encrypted)"."""
        cfg = generate_config(None, [], [], "rules", _default_settings(dns_mode="dot"))
        servers = cfg["dns"]["servers"]
        str_servers = [s for s in servers if isinstance(s, str)]
        assert any(s.startswith("tcp://") and s.endswith(":53") for s in str_servers), \
            f"No tcp://...:53 server found: {servers}"

    def test_doh_uses_https(self):
        cfg = generate_config(None, [], [], "rules", _default_settings(dns_mode="doh"))
        servers = cfg["dns"]["servers"]
        str_servers = [s for s in servers if isinstance(s, str)]
        assert any(s.startswith("https://") for s in str_servers), f"No https:// server found: {servers}"

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
        obj_servers = [s for s in cfg["dns"]["servers"] if isinstance(s, dict)]
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
