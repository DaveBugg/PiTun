"""The exit-identity probe that rides along with the speed test.

A flag derived from the node's `address` answers the wrong question twice:
a hostname needs DNS that isn't available inside the write flush, and a
chained node's address is its ENTRY hop, in a different country from where
its traffic actually comes out. The speed test already holds a tunnel open
to the internet, so it reads the exit back through it.

No network here — a fake httpx stands in for Cloudflare's trace endpoint.
"""
import pytest

from app.core import speedtest as st


CF_TRACE = """fl=123abc
h=cp.cloudflare.com
ip=203.0.113.7
ts=1786790598.123
visit_scheme=https
uag=Mozilla/5.0
colo=AMS
sliver=none
http=http/2
loc=NL
tls=TLSv1.3
sni=plaintext
warp=off
gateway=off
"""


class _Resp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def _fake_httpx(monkeypatch, handler):
    """Point `httpx.AsyncClient` at `handler(url) -> _Resp | Exception`."""
    import httpx

    class _Client:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return False

        async def get(self, url):
            out = handler(url)
            if isinstance(out, Exception):
                raise out
            return out

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


class TestParseTrace:
    def test_it_reads_ip_and_country(self):
        assert st._parse_trace(CF_TRACE) == ("203.0.113.7", "NL")

    def test_a_missing_loc_is_not_invented(self):
        body = "ip=203.0.113.7\nwarp=off\n"
        assert st._parse_trace(body) == ("203.0.113.7", None)

    def test_junk_is_survivable(self):
        assert st._parse_trace("") == (None, None)
        assert st._parse_trace("<html>404</html>") == (None, None)
        # `loc=XX` is two letters or it is nothing — "T1" is Tor, not a country.
        assert st._parse_trace("ip=1.2.3.4\nloc=T1\n") == ("1.2.3.4", None)
        assert st._parse_trace("ip=1.2.3.4\nloc=\n") == ("1.2.3.4", None)


@pytest.mark.asyncio
class TestProbeExit:
    async def test_it_reports_what_the_far_side_saw(self, monkeypatch):
        _fake_httpx(monkeypatch, lambda _u: _Resp(200, CF_TRACE))
        assert await st.probe_exit(1080) == {
            "exit_ip": "203.0.113.7", "exit_country": "NL",
        }

    async def test_it_moves_on_to_the_second_endpoint(self, monkeypatch):
        seen = []

        def handler(url):
            seen.append(url)
            if len(seen) == 1:
                return RuntimeError("connection reset")
            return _Resp(200, CF_TRACE)

        _fake_httpx(monkeypatch, handler)
        assert (await st.probe_exit(1080))["exit_country"] == "NL"
        assert len(seen) == 2

    async def test_a_non_200_is_not_parsed(self, monkeypatch):
        _fake_httpx(monkeypatch, lambda _u: _Resp(403, "blocked"))
        assert await st.probe_exit(1080) == {"exit_ip": None, "exit_country": None}

    async def test_everything_failing_is_not_an_error(self, monkeypatch):
        """It rides along with a measurement — it must never sink one."""
        _fake_httpx(monkeypatch, lambda _u: OSError("no route"))
        assert await st.probe_exit(1080) == {"exit_ip": None, "exit_country": None}

    async def test_a_withheld_country_falls_back_to_the_local_database(self, monkeypatch):
        """Cloudflare omits `loc=` on some networks. By then we hold a literal
        IP, which is all a GeoLite2 lookup ever needed."""
        _fake_httpx(monkeypatch, lambda _u: _Resp(200, "ip=203.0.113.7\n"))
        from app.core import geoip_lookup as g
        monkeypatch.setattr(g, "country_code", lambda ip: "DE" if ip == "203.0.113.7" else None)
        assert await st.probe_exit(1080) == {
            "exit_ip": "203.0.113.7", "exit_country": "DE",
        }

    async def test_no_database_either_still_keeps_the_ip(self, monkeypatch):
        _fake_httpx(monkeypatch, lambda _u: _Resp(200, "ip=203.0.113.7\n"))
        from app.core import geoip_lookup as g
        g.reset()
        assert await st.probe_exit(1080) == {
            "exit_ip": "203.0.113.7", "exit_country": None,
        }


class _FakeNode:
    """Only the attributes `apply_exit` touches — no database involved."""

    def __init__(self, **kw):
        self.country = kw.get("country")
        self.exit_ip = kw.get("exit_ip")
        self.exit_checked_at = kw.get("exit_checked_at")


class TestApplyExit:
    def test_it_records_both_and_stamps_the_time(self):
        n = _FakeNode()
        assert st.apply_exit(n, {"exit_ip": "203.0.113.7", "exit_country": "nl"}) is True
        assert (n.country, n.exit_ip) == ("NL", "203.0.113.7")
        assert n.exit_checked_at is not None

    def test_nothing_observed_changes_nothing(self):
        """A node that couldn't be reached keeps the flag it already had —
        one failed sweep must not blank a country that was right."""
        n = _FakeNode(country="NL", exit_ip="203.0.113.7")
        assert st.apply_exit(n, {"exit_ip": None, "exit_country": None}) is False
        assert (n.country, n.exit_ip) == ("NL", "203.0.113.7")
        assert n.exit_checked_at is None

    def test_an_unusable_code_does_not_overwrite_a_good_one(self):
        n = _FakeNode(country="NL")
        assert st.apply_exit(n, {"exit_ip": "198.51.100.9", "exit_country": "T1"}) is True
        assert n.country == "NL"          # kept
        assert n.exit_ip == "198.51.100.9"  # the address is still worth having

    def test_a_missing_key_is_treated_as_absent(self):
        n = _FakeNode()
        assert st.apply_exit(n, {}) is False
        assert st.apply_exit(n, {"error": "unreachable: google: timeout"}) is False


class TestTargetsAreNotANewDependency:
    def test_the_exit_probe_reuses_a_host_already_contacted(self):
        """The reachability gate already talks to cp.cloudflare.com on every
        speed test. Reading the exit from the same host adds a request, not
        an endpoint someone has to trust."""
        gate_hosts = {url.split("/")[2] for _l, url in st._REACH_TARGETS}
        assert st._EXIT_TARGETS[0][1].split("/")[2] in gate_hosts
