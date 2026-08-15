"""Optional GeoIP node-name flag enrichment. No real .mmdb — a fake reader
stands in for MaxMind, so the whole suite runs offline / licence-free."""
import pytest

from app.core import geoip_lookup as g


class _FakeReader:
    def __init__(self, mapping):
        self._m = mapping

    def get(self, ip):
        return self._m.get(ip)

    def close(self):
        pass


@pytest.fixture
def fake_geoip(monkeypatch):
    """Install a fake reader mapping IPs -> MaxMind-style country records."""
    reader = _FakeReader({
        "1.1.1.1": {"country": {"iso_code": "US"}},
        "5.5.5.5": {"country": {"iso_code": "NL"}},
        "9.9.9.9": {},  # record with no country
    })
    g.reset()
    monkeypatch.setattr(g, "_reader", reader)
    monkeypatch.setattr(g, "_reader_loaded", True)
    yield reader
    g.reset()


class TestPureHelpers:
    def test_flag_emoji(self):
        assert g.flag_emoji("NL") == "\U0001F1F3\U0001F1F1"
        assert g.flag_emoji("us") == "\U0001F1FA\U0001F1F8"
        assert g.flag_emoji("") == ""
        assert g.flag_emoji("X") == ""      # too short
        assert g.flag_emoji("USA") == ""    # too long
        assert g.flag_emoji("1N") == ""     # non-alpha

    def test_strip_leading_flag(self):
        assert g.strip_leading_flag("\U0001F1F3\U0001F1F1 node") == "node"
        assert g.strip_leading_flag("\U0001F1F3\U0001F1F1node") == "node"
        assert g.strip_leading_flag("plain") == "plain"


class TestDisabled:
    def test_enrich_is_noop_without_db(self):
        # No reader installed and the default path doesn't exist on the test
        # box -> _get_reader() returns None -> everything passes through.
        g.reset()
        assert g.enrich_name("node", "1.1.1.1") == "node"
        nodes = [{"name": "a", "address": "1.1.1.1"}]
        g.enrich_parsed_nodes(nodes)
        assert nodes[0]["name"] == "a"


class TestEnrich:
    def test_enrich_name_ip(self, fake_geoip):
        assert g.enrich_name("node", "5.5.5.5") == "\U0001F1F3\U0001F1F1 node"
        assert g.enrich_name("node", "1.1.1.1") == "\U0001F1FA\U0001F1F8 node"

    def test_unknown_ip_untouched(self, fake_geoip):
        assert g.enrich_name("node", "9.9.9.9") == "node"   # record w/o country
        assert g.enrich_name("node", "2.2.2.2") == "node"   # not in the DB

    def test_idempotent_no_flag_stacking(self, fake_geoip):
        once = g.enrich_name("node", "5.5.5.5")
        twice = g.enrich_name(once, "5.5.5.5")
        assert once == "\U0001F1F3\U0001F1F1 node"
        assert twice == "\U0001F1F3\U0001F1F1 node"

    def test_enrich_parsed_nodes(self, fake_geoip):
        nodes = [
            {"name": "n1", "address": "5.5.5.5"},
            {"name": "n2", "address": "1.1.1.1"},
            {"name": "n3", "address": "2.2.2.2"},  # unknown -> unchanged
        ]
        g.enrich_parsed_nodes(nodes)
        assert nodes[0]["name"] == "\U0001F1F3\U0001F1F1 n1"
        assert nodes[1]["name"] == "\U0001F1FA\U0001F1F8 n2"
        assert nodes[2]["name"] == "n3"

    def test_hostname_resolves(self, fake_geoip, monkeypatch):
        monkeypatch.setattr(g.socket, "gethostbyname", lambda h: "5.5.5.5")
        assert g.enrich_name("node", "example.com") == "\U0001F1F3\U0001F1F1 node"

    def test_hostname_resolve_failure_untouched(self, fake_geoip, monkeypatch):
        def boom(h):
            raise OSError("no dns")
        monkeypatch.setattr(g.socket, "gethostbyname", boom)
        assert g.enrich_name("node", "nxdomain.invalid") == "node"


class TestResolveSwitch:
    """The model hook runs inside a database flush, where the blocking DNS
    lookup this module otherwise does would hold up the write."""

    def test_hostname_gets_no_flag_without_resolving(self, fake_geoip, monkeypatch):
        def boom(_h):
            raise AssertionError("DNS must not be consulted with resolve=False")

        monkeypatch.setattr(g.socket, "gethostbyname", boom)
        assert g.enrich_name("node", "vpn.example.com", resolve=False) == "node"

    def test_an_ip_literal_still_gets_one(self, fake_geoip):
        assert g.enrich_name("node", "5.5.5.5", resolve=False) == "🇳🇱 node"


class TestModelListener:
    """Enrichment used to be called at the two import paths while nodes are
    created at nine, so a node from a server deploy or an x-ui panel never
    got a flag — which is exactly how the feature came to look broken."""

    def _target(self, name, address):
        class _N:
            pass
        n = _N()
        n.name, n.address = name, address
        return n

    def _hook(self, monkeypatch):
        """Install the listener against a stand-in and hand back the callback."""
        captured = {}

        class _FakeEvent:
            @staticmethod
            def listen(_model, when, fn):
                captured.setdefault("whens", []).append(when)
                captured["fn"] = fn

        import sys, types
        fake_sa = types.ModuleType("sqlalchemy")
        fake_sa.event = _FakeEvent
        monkeypatch.setitem(sys.modules, "sqlalchemy", fake_sa)
        g.install_node_listener()
        return captured

    def test_it_hooks_both_insert_and_update(self, fake_geoip, monkeypatch):
        cap = self._hook(monkeypatch)
        assert set(cap["whens"]) == {"before_insert", "before_update"}

    def test_a_node_written_by_any_path_gets_flagged(self, fake_geoip, monkeypatch):
        cap = self._hook(monkeypatch)
        node = self._target("vless-reality-deployed", "5.5.5.5")
        cap["fn"](None, None, node)
        assert node.name == "🇳🇱 vless-reality-deployed"

    def test_flags_do_not_stack_on_rewrite(self, fake_geoip, monkeypatch):
        cap = self._hook(monkeypatch)
        node = self._target("🇳🇱 vless", "5.5.5.5")
        cap["fn"](None, None, node)
        cap["fn"](None, None, node)
        assert node.name == "🇳🇱 vless"

    def test_a_broken_lookup_never_blocks_the_write(self, fake_geoip, monkeypatch):
        cap = self._hook(monkeypatch)
        monkeypatch.setattr(g, "enrich_name", lambda *a, **k: 1 / 0)
        node = self._target("vless", "5.5.5.5")
        cap["fn"](None, None, node)          # must not raise
        assert node.name == "vless"

    def test_it_does_not_strip_a_flag_it_cannot_re_derive(self, fake_geoip, monkeypatch):
        """The bug behind "flags never appear on hostname nodes".

        `/apply-country-flags` resolves DNS, works out the country and writes
        the flag — and the very commit that saves it fires this hook, which
        re-derives with `resolve=False`, misses, and strips the flag straight
        back off. The button then reports renaming nodes that show no flag."""
        cap = self._hook(monkeypatch)
        node = self._target("🇳🇱 vless", "vpn.example.com")
        cap["fn"](None, None, node)
        assert node.name == "🇳🇱 vless"

    def test_an_observed_exit_country_flags_a_hostname_node(self, fake_geoip, monkeypatch):
        """No DNS, no address lookup — the speed test already saw the exit."""
        cap = self._hook(monkeypatch)
        node = self._target("vless", "vpn.example.com")
        node.country = "NL"
        cap["fn"](None, None, node)
        assert node.name == "🇳🇱 vless"

    def test_a_moved_exit_replaces_the_old_flag(self, fake_geoip, monkeypatch):
        cap = self._hook(monkeypatch)
        node = self._target("🇺🇸 vless", "vpn.example.com")
        node.country = "NL"
        cap["fn"](None, None, node)
        assert node.name == "🇳🇱 vless"

    def test_the_exit_outranks_the_address(self, fake_geoip, monkeypatch):
        """A chained node dials an entry hop in one country and surfaces in
        another; the address is the wrong one of the two to show."""
        cap = self._hook(monkeypatch)
        node = self._target("vless", "1.1.1.1")   # address says US
        node.country = "NL"                        # exit said NL
        cap["fn"](None, None, node)
        assert node.name == "🇳🇱 vless"


class TestObservedCountry:
    """`country=` is an observation, not an inference — it wins, and it works
    with no MaxMind database installed at all."""

    def test_it_flags_without_any_database(self):
        g.reset()
        assert g.enrich_name("node", "vpn.example.com", country="nl") == "🇳🇱 node"

    def test_a_malformed_code_falls_back_to_the_address(self, fake_geoip):
        assert g.enrich_name("node", "1.1.1.1", country="ZZZZ") == "🇺🇸 node"
        assert g.enrich_name("node", "1.1.1.1", country="") == "🇺🇸 node"

    def test_resolve_country_prefers_it(self, fake_geoip):
        assert g.resolve_country("1.1.1.1", country="NL") == "NL"
        assert g.resolve_country("1.1.1.1") == "US"
        assert g.resolve_country("2.2.2.2") is None


class TestKeepOnMiss:
    def test_a_miss_leaves_the_name_exactly_as_it_was(self, fake_geoip):
        assert g.enrich_name("🇳🇱 node", "vpn.example.com", resolve=False,
                             keep_on_miss=True) == "🇳🇱 node"

    def test_a_hit_still_replaces_a_stale_flag(self, fake_geoip):
        assert g.enrich_name("🇺🇸 node", "5.5.5.5", keep_on_miss=True) == "🇳🇱 node"

    def test_the_default_is_unchanged(self, fake_geoip):
        """Import paths keep stripping on a miss — only the write hook and
        the one-off button ask to preserve."""
        assert g.enrich_name("🇺🇸 node", "2.2.2.2") == "node"
