"""TLS-ClientHello fragmentation wiring (config_gen._apply_fragment).

Client-side anti-DPI: proxy ENTRY hops dial through a `fragment` freedom
outbound. Off by default; only entry hops (no proxySettings) are touched;
freedom/blackhole/dns and reserved tags are never touched.
"""
from app.core.config_gen import _apply_fragment


def _outbounds():
    """A realistic post-assembly outbound set: one plain proxy, one chain
    (node dials through its relay), plus the reserved tail."""
    return [
        # plain proxy entry hop — dials the internet directly
        {"tag": "node-1", "protocol": "vless",
         "streamSettings": {"sockopt": {"mark": 255}}},
        # chained node: its own outbound tunnels through the relay
        {"tag": "node-2", "protocol": "vless",
         "streamSettings": {"sockopt": {"mark": 255}},
         "proxySettings": {"tag": "node-3", "transportLayer": True}},
        # the relay = the actual entry hop for node-2's chain
        {"tag": "node-3", "protocol": "trojan",
         "streamSettings": {"sockopt": {"mark": 255}}},
        {"tag": "direct", "protocol": "freedom",
         "streamSettings": {"sockopt": {"mark": 255}}},
        {"tag": "block", "protocol": "blackhole"},
        {"tag": "dns-out", "protocol": "dns",
         "proxySettings": {"tag": "direct"}},
    ]


def _dialer(ob):
    return (ob.get("streamSettings", {}).get("sockopt", {})).get("dialerProxy")


class TestFragmentDisabled:
    def test_absent_setting_is_a_noop(self):
        obs = _outbounds()
        _apply_fragment(obs, {})
        assert all(o.get("tag") != "fragment" for o in obs)
        assert all(_dialer(o) is None for o in obs)

    def test_explicit_false_is_a_noop(self):
        obs = _outbounds()
        _apply_fragment(obs, {"xray_fragment_enabled": "false"})
        assert all(o.get("tag") != "fragment" for o in obs)


class TestFragmentEnabled:
    def _apply(self, extra=None):
        obs = _outbounds()
        _apply_fragment(obs, {"xray_fragment_enabled": "true", **(extra or {})})
        return obs

    def test_fragment_outbound_appended_with_defaults(self):
        obs = self._apply()
        frag = next(o for o in obs if o.get("tag") == "fragment")
        assert frag["protocol"] == "freedom"
        assert frag["settings"]["fragment"] == {
            "packets": "tlshello", "length": "100-200", "interval": "10-20",
        }
        # It makes the real dial, so it carries the TPROXY-bypass mark.
        assert frag["streamSettings"]["sockopt"]["mark"] == 255

    def test_entry_hops_dial_through_fragment(self):
        obs = self._apply()
        # plain proxy + the chain's relay entry both dial out → fragmented
        assert _dialer(next(o for o in obs if o["tag"] == "node-1")) == "fragment"
        assert _dialer(next(o for o in obs if o["tag"] == "node-3")) == "fragment"

    def test_mid_chain_hop_is_not_fragmented(self):
        obs = self._apply()
        # node-2 tunnels through node-3 (has proxySettings) → left alone,
        # otherwise it would be a double dialer.
        assert _dialer(next(o for o in obs if o["tag"] == "node-2")) is None

    def test_reserved_outbounds_untouched(self):
        obs = self._apply()
        for tag in ("direct", "block", "dns-out"):
            assert _dialer(next(o for o in obs if o["tag"] == tag)) is None

    def test_custom_params_honoured(self):
        obs = self._apply({
            "xray_fragment_packets": "1-3",
            "xray_fragment_length": "5-10",
            "xray_fragment_interval": "1-2",
        })
        frag = next(o for o in obs if o.get("tag") == "fragment")
        assert frag["settings"]["fragment"] == {
            "packets": "1-3", "length": "5-10", "interval": "1-2",
        }

    def test_no_proxy_outbounds_means_no_fragment_outbound(self):
        # bypass mode: only direct/block/dns exist → nothing to fragment,
        # so we must NOT append a dangling fragment outbound.
        obs = [
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ]
        _apply_fragment(obs, {"xray_fragment_enabled": "true"})
        assert all(o.get("tag") != "fragment" for o in obs)
