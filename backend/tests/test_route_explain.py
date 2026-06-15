"""Tests for the Route Explainer matcher (layer A)."""
from app.models import RoutingRule, DNSRule, RoutingSet
from app.core import route_explain as rex


def _rule(id, rtype, val, action, order=100, enabled=True, set_id=None):
    r = RoutingRule(id=id, name=f"r{id}", rule_type=rtype, match_value=val,
                    action=action, enabled=enabled, order=order)
    r.routing_set_id = set_id
    return r


def _dns(id, domains, server, dtype="plain", order=100, enabled=True):
    return DNSRule(id=id, name=f"d{id}", domain_match=domains,
                   dns_server=server, dns_type=dtype, order=order, enabled=enabled)


# ── action_from_outbound (inverse mapping used by the xray-probe merge) ───────


class TestActionFromOutbound:
    def test_direct(self):
        assert rex.action_from_outbound("direct") == "direct"

    def test_block(self):
        assert rex.action_from_outbound("block") == "block"

    def test_node_is_proxy(self):
        assert rex.action_from_outbound("node-2") == "proxy"
        assert rex.action_from_outbound("node-17") == "proxy"

    def test_balancer(self):
        assert rex.action_from_outbound("balancer-3") == "balancer"

    def test_unknown_passthrough(self):
        assert rex.action_from_outbound("weird-tag") == "weird-tag"


# ── DNS matcher ──────────────────────────────────────────────────────────────


class TestExplainDns:
    def test_literal_domain_rule_matches(self):
        rules = [_dns(1, "domain:youtube.com,domain:youtu.be", "94.140.14.14", "dot")]
        x = rex.explain_dns("www.youtube.com", is_ip=False, dns_rules=rules,
                            settings_map={})
        assert x.matched_rule_id == 1
        assert x.server == "94.140.14.14"
        assert x.server_type == "dot"
        assert x.uses_global_upstream is False

    def test_no_match_falls_to_global(self):
        rules = [_dns(1, "domain:youtube.com", "94.140.14.14")]
        x = rex.explain_dns("apple.com", is_ip=False, dns_rules=rules,
                            settings_map={"dns_upstream": "1.1.1.1"})
        assert x.uses_global_upstream is True
        assert x.server == "1.1.1.1"

    def test_ip_target_skips_dns(self):
        x = rex.explain_dns("1.2.3.4", is_ip=True, dns_rules=[], settings_map={})
        assert x.is_ip is True
        assert x.server is None

    def test_geosite_dns_rule_flags_uncertainty(self):
        rules = [_dns(1, "geosite:category-ads", "0.0.0.0")]
        x = rex.explain_dns("doubleclick.net", is_ip=False, dns_rules=rules,
                            settings_map={"dns_upstream": "8.8.8.8"})
        # literal matcher can't decide geosite → falls to global + flag
        assert x.geosite_uncertain is True
        assert x.uses_global_upstream is True

    def test_query_strategy_surfaced(self):
        x = rex.explain_dns("x.com", is_ip=False, dns_rules=[],
                            settings_map={"dns_query_strategy": "UseIPv4"})
        assert x.query_strategy == "UseIPv4"


# ── Routing matcher ──────────────────────────────────────────────────────────


class TestExplainRouting:
    def _walk(self, rules, **kw):
        defaults = dict(
            target="example.com", is_ip=False, resolved_ip="93.184.216.34",
            port=443, protocol="tcp", mode="rules", bypass_private=True,
            rules=rules, active_node_id=7, node_labels={7: "node-seven"},
        )
        defaults.update(kw)
        return rex.explain_routing(**defaults)

    def test_domain_proxy_resolves_active_node(self):
        rules = [_rule(1, "domain", "example.com", "proxy", order=10)]
        x = self._walk(rules)
        assert x.matched_rule_id == 1
        assert x.action == "proxy"
        assert x.outbound == "node-7"
        assert x.outbound_label == "node-seven"

    def test_domain_block(self):
        rules = [_rule(1, "domain", "example.com", "block", order=10)]
        x = self._walk(rules)
        assert x.outbound == "block"

    def test_node_action_explicit(self):
        rules = [_rule(1, "domain", "example.com", "node:3", order=10)]
        x = self._walk(rules)
        assert x.outbound == "node-3"

    def test_first_match_wins_by_order(self):
        rules = [
            _rule(1, "domain", "example.com", "block", order=5),
            _rule(2, "domain", "example.com", "proxy", order=10),
        ]
        x = self._walk(rules)
        assert x.matched_rule_id == 1
        assert x.outbound == "block"

    def test_no_match_catch_all_direct(self):
        rules = [_rule(1, "domain", "other.com", "proxy", order=10)]
        x = self._walk(rules)
        assert x.outbound == "direct"
        assert "catch-all" in (x.matched_rule_name or "")

    def test_dst_ip_cidr_match(self):
        rules = [_rule(1, "dst_ip", "93.184.216.0/24", "block", order=10)]
        x = self._walk(rules, is_ip=True, target="93.184.216.34")
        assert x.outbound == "block"

    def test_port_match(self):
        rules = [_rule(1, "port", "443", "proxy", order=10)]
        x = self._walk(rules)
        assert x.outbound == "node-7"

    def test_private_ip_bypassed_first(self):
        rules = [_rule(1, "dst_ip", "192.168.0.0/16", "proxy", order=10)]
        x = self._walk(rules, is_ip=True, target="192.168.1.50",
                       resolved_ip="192.168.1.50")
        assert x.outbound == "direct"
        assert "private" in " ".join(x.notes).lower()

    def test_port_53_forced_direct(self):
        rules = [_rule(1, "domain", "example.com", "proxy", order=10)]
        x = self._walk(rules, port=53)
        assert x.outbound == "direct"
        assert "53" in (x.matched_rule_name or "")

    def test_geosite_rule_marks_uncertain(self):
        rules = [_rule(1, "geosite", "category-ads", "block", order=10)]
        x = self._walk(rules)
        assert x.certain is False
        assert x.blocking_rule is not None
        assert "geosite" in x.blocking_rule.lower()

    def test_global_mode_everything_proxied(self):
        x = self._walk([], mode="global")
        assert x.action == "proxy"
        assert x.outbound == "node-7"

    def test_bypass_mode_everything_direct(self):
        x = self._walk([], mode="bypass")
        assert x.outbound == "direct"

    def test_per_set_rules_evaluated_first(self):
        # global rule says proxy; set rule says block — device in set
        # must hit the set's block first.
        rs = RoutingSet(id=1, name="Kids", tproxy_port=65500)
        rules = [
            _rule(1, "domain", "example.com", "proxy", order=5, set_id=None),
            _rule(2, "domain", "example.com", "block", order=50, set_id=1),
        ]
        x = self._walk(rules, set_context=(rs, [2]))
        assert x.set_id == 1
        assert x.set_name == "Kids"
        assert x.matched_rule_id == 2
        assert x.outbound == "block"


# ── Endpoint integration (HTTP, no live xray/network) ────────────────────────

from unittest.mock import patch, AsyncMock


class TestExtractHost:
    def _h(self, s):
        from app.api.diagnostics import _extract_host
        return _extract_host(s)

    def test_full_url_strips_scheme_and_path(self):
        assert self._h("https://cloudconvert.com/md-to-pdf") == "cloudconvert.com"

    def test_url_with_query_and_trailing_slash(self):
        assert self._h("https://www.markdowntopdf.com/?a=1#frag") == "www.markdowntopdf.com"

    def test_host_port_strips_port(self):
        assert self._h("example.com:8443") == "example.com"

    def test_userinfo_stripped(self):
        assert self._h("http://user:pass@example.com/x") == "example.com"

    def test_bare_hostname_untouched(self):
        assert self._h("www.youtube.com") == "www.youtube.com"

    def test_bare_ipv4_untouched(self):
        assert self._h("1.2.3.4") == "1.2.3.4"

    def test_ipv6_bracketed_with_port(self):
        assert self._h("[2001:db8::1]:443") == "2001:db8::1"

    def test_bare_ipv6_untouched(self):
        assert self._h("2001:db8::1") == "2001:db8::1"


class TestExplainEndpoint:
    def test_full_url_target_resolves_to_host(self, client, session, auth_headers, default_settings):
        with patch("app.api.dns._resolve_plain",
                   new=AsyncMock(return_value=(["93.184.216.34"], 3))):
            r = client.post("/api/diagnostics/explain",
                            json={"target": "https://cloudconvert.com/md-to-pdf", "port": 443},
                            headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["target"] == "cloudconvert.com"

    def test_explain_domain_basic(self, client, session, auth_headers, default_settings):
        # seed a routing rule + dns rule
        from app.models import RoutingRule, DNSRule
        session.add(RoutingRule(name="YT", rule_type="domain",
                                match_value="youtube.com", action="proxy",
                                enabled=True, order=10))
        session.add(DNSRule(name="YTdns", domain_match="domain:youtube.com",
                            dns_server="94.140.14.14", dns_type="dot",
                            enabled=True, order=10))
        session.commit()

        # stub DNS resolution so the test doesn't hit the network
        with patch("app.api.dns._resolve_plain",
                   new=AsyncMock(return_value=(["74.125.1.1"], 5))):
            r = client.post("/api/diagnostics/explain",
                            json={"target": "www.youtube.com", "port": 443,
                                  "protocol": "tcp"},
                            headers=auth_headers)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["is_ip"] is False
        assert d["dns"]["server"] == "94.140.14.14"
        assert d["dns"]["resolved_ips"] == ["74.125.1.1"]
        assert d["routing"]["action"] == "proxy"
        assert d["routing"]["method"] == "python_matcher"
        assert d["reachability"]["tested"] is False

    def test_explain_ip_skips_dns(self, client, session, auth_headers, default_settings):
        r = client.post("/api/diagnostics/explain",
                        json={"target": "1.2.3.4", "port": 80},
                        headers=auth_headers)
        assert r.status_code == 200
        d = r.json()
        assert d["is_ip"] is True
        assert d["dns"]["is_ip"] is True

    def test_explain_no_reachability_when_not_requested(
        self, client, session, auth_headers, default_settings
    ):
        with patch("app.api.dns._resolve_plain",
                   new=AsyncMock(return_value=(["93.184.216.34"], 3))):
            r = client.post("/api/diagnostics/explain",
                            json={"target": "example.com"},
                            headers=auth_headers)
        assert r.json()["reachability"]["tested"] is False

    def test_geosite_verified_probe_rewrites_action_and_clears_candidate(
        self, client, session, auth_headers, default_settings
    ):
        """Regression: a geosite rule (e.g. 'Bypass RU sites', action=direct)
        sits ahead of a proxy rule. Layer A can't resolve geosite offline so
        it best-guesses action=direct/matched=that-rule. When the live xray
        probe says the target actually went to node-2, the merge MUST rewrite
        action→proxy and DROP the stale candidate — otherwise the UI shows the
        contradiction 'action: direct / outbound: node-2' (the youtube.com vs
        category-ru confusion seen in the wild)."""
        from app.models import RoutingRule, Node
        session.add(Node(id=2, name="vl-testandroid", protocol="vless",
                         address="198.51.100.7", port=443, uuid="x",
                         enabled=True, order=1))
        session.add(RoutingRule(name="Bypass RU sites", rule_type="geosite",
                                match_value="category-ru,tld-ru", action="direct",
                                enabled=True, order=10))
        session.add(RoutingRule(name="YT", rule_type="domain",
                                match_value="youtube.com", action="proxy",
                                enabled=True, order=20))
        session.commit()  # mode=rules comes from default_settings

        async def _fake_probe(**kwargs):
            return {"ok": True, "outbound": "node-2",
                    "detail": "decision read from live xray access log"}

        with (
            patch("app.api.dns._resolve_plain",
                  new=AsyncMock(return_value=(["173.194.220.91"], 5))),
            patch("app.core.config_gen.generate_config", return_value={}),
            patch("app.core.config_gen.collect_routing_set_context",
                  new=AsyncMock(return_value=([], {}))),
            patch("app.core.route_explain_probe.xray_probe_routing",
                  new=_fake_probe),
        ):
            r = client.post("/api/diagnostics/explain",
                            json={"target": "youtube.com", "port": 443,
                                  "protocol": "tcp", "verify_routing": True},
                            headers=auth_headers)
        assert r.status_code == 200, r.text
        rt = r.json()["routing"]
        assert rt["method"] == "xray_probe"
        assert rt["certain"] is True
        assert rt["outbound"] == "node-2"
        # The contradiction is gone: action mirrors the real outbound …
        assert rt["action"] == "proxy"
        # … and the misleading geosite candidate is dropped, not shown as the match.
        assert rt["matched_rule_name"] is None
        assert rt["matched_rule_type"] is None


# ── xray-probe config builder (layer B, pure transform) ──────────────────────


class TestProbeConfigBuilder:
    def _base(self):
        return {
            "log": {"access": ""}, "api": {"tag": "api"}, "stats": {}, "policy": {},
            "inbounds": [{"tag": "api"}, {"tag": "tproxy-tcp"}, {"tag": "tproxy-set-1"}],
            "outbounds": [
                {"tag": "node-2", "protocol": "vless"},
                {"tag": "direct", "protocol": "freedom"},
                {"tag": "block", "protocol": "blackhole"},
                {"tag": "dns-out", "protocol": "dns"},
            ],
            "routing": {"rules": [
                {"inboundTag": ["api"], "outboundTag": "api"},
                {"inboundTag": ["dns-in", "dns-in-53"], "outboundTag": "dns-out"},
                {"inboundTag": ["tproxy-set-1"], "domain": ["domain:kids.com"], "outboundTag": "block"},
                {"domain": ["geosite:category-ru"], "outboundTag": "direct"},
                {"domain": ["domain:youtube.com"], "outboundTag": "node-2"},
            ]},
        }

    def test_strips_api_stats_policy(self):
        from app.core.route_explain_probe import build_probe_config
        c = build_probe_config(self._base(), probe_port=15359, log_path="/tmp/a.log")
        assert "api" not in c and "stats" not in c and "policy" not in c

    def test_single_socks_inbound(self):
        from app.core.route_explain_probe import build_probe_config
        c = build_probe_config(self._base(), probe_port=15359, log_path="/tmp/a.log")
        assert [i["tag"] for i in c["inbounds"]] == ["probe-in"]
        assert c["inbounds"][0]["protocol"] == "socks"

    def test_drops_inbound_scoped_rules(self):
        from app.core.route_explain_probe import build_probe_config
        c = build_probe_config(self._base(), probe_port=15359, log_path="/tmp/a.log")
        # only the 2 destination rules (no inboundTag) survive
        assert all("inboundTag" not in r for r in c["routing"]["rules"])
        assert len(c["routing"]["rules"]) == 2

    def test_node_outbound_stubbed_keeps_tag(self):
        from app.core.route_explain_probe import build_probe_config
        c = build_probe_config(self._base(), probe_port=15359, log_path="/tmp/a.log")
        node = next(o for o in c["outbounds"] if o["tag"] == "node-2")
        assert node["protocol"] == "freedom"  # stubbed
        # direct/block/dns-out preserved as-is
        assert {o["tag"] for o in c["outbounds"]} == {"node-2", "direct", "block", "dns-out"}

    def test_access_log_path_set(self):
        from app.core.route_explain_probe import build_probe_config
        c = build_probe_config(self._base(), probe_port=15359, log_path="/tmp/zzz.log")
        assert c["log"]["access"] == "/tmp/zzz.log"


class TestAccessLogParser:
    def test_parses_arrow_separator(self):
        from app.core.route_explain_probe import _read_chosen_outbound
        import tempfile, os
        log = "2026 accepted tcp:youtube.com:443 [probe-in -> node-2]\n"
        fd, p = tempfile.mkstemp(); os.write(fd, log.encode()); os.close(fd)
        try:
            assert _read_chosen_outbound(p, "youtube.com") == "node-2"
        finally:
            os.remove(p)

    def test_parses_legacy_doublearrow(self):
        from app.core.route_explain_probe import _read_chosen_outbound
        import tempfile, os
        log = "2026 accepted tcp:vk.com:443 [probe-in >> direct]\n"
        fd, p = tempfile.mkstemp(); os.write(fd, log.encode()); os.close(fd)
        try:
            assert _read_chosen_outbound(p, "vk.com") == "direct"
        finally:
            os.remove(p)
