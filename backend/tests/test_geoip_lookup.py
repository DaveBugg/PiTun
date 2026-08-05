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
