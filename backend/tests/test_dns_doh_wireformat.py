"""DoH resolution must use RFC 8484 wire format, not the JSON API.

Live-caught on the Route Explainer: a DNS rule pointing at AdGuard over
DoH (94.140.14.14) made `_resolve_doh` issue the Google/Cloudflare JSON
query (`?name=&type=A`, `application/dns-json`). AdGuard's `/dns-query`
rejects that with HTTP 400, so resolution — and the reachability stage —
failed. The fix sends an `application/dns-message` POST (RFC 8484), the
universal DoH encoding every server speaks. These tests pin that.
"""
from __future__ import annotations

import struct
from unittest.mock import patch

import pytest

from app.api import dns as dns_mod


def _wire_response(txid: bytes, domain: str, ip: str) -> bytes:
    """Craft a minimal DNS wire-format response: QR=1, RD=1, RA=1,
    RCODE=0, one question echoed, one A answer (compressed name)."""
    header = txid + b"\x81\x80" + struct.pack(">HHHH", 1, 1, 0, 0)
    q = b""
    for label in domain.split("."):
        q += bytes([len(label)]) + label.encode()
    q += b"\x00" + struct.pack(">HH", 1, 1)  # root, QTYPE=A, QCLASS=IN
    rdata = bytes(int(o) for o in ip.split("."))
    answer = b"\xc0\x0c" + struct.pack(">HHIH", 1, 1, 300, 4) + rdata
    return header + q + answer


class TestDohWireFormat:
    def test_build_query_shape(self):
        pkt = dns_mod._build_dns_a_query("example.com", b"\x00\x00")
        assert pkt[:2] == b"\x00\x00"                  # txid
        assert pkt[2:4] == b"\x01\x00"                 # flags: RD=1
        assert pkt[4:6] == b"\x00\x01"                 # QDCOUNT=1
        assert pkt.endswith(b"\x00\x00\x01\x00\x01")   # root + QTYPE=A QCLASS=IN
        assert b"\x07example\x03com" in pkt

    def test_roundtrip_parse(self):
        txid = b"\x00\x00"
        resp = _wire_response(txid, "example.com", "93.184.216.34")
        assert dns_mod._parse_dns_a_records(resp, txid) == ["93.184.216.34"]

    @pytest.mark.asyncio
    async def test_resolve_doh_posts_wire_format(self):
        txid = b"\x00\x00"
        resp_bytes = _wire_response(txid, "example.com", "93.184.216.34")
        captured: dict = {}

        class _Resp:
            content = resp_bytes

            def raise_for_status(self):
                pass

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, content=None, headers=None):
                captured["url"] = url
                captured["content"] = content
                captured["headers"] = headers
                return _Resp()

        with patch.object(dns_mod.httpx, "AsyncClient", lambda *a, **k: _Client()):
            ips, _latency = await dns_mod._resolve_doh("example.com", "94.140.14.14")

        assert ips == ["93.184.216.34"]
        # bare IP gets the /dns-query path …
        assert captured["url"] == "https://94.140.14.14/dns-query"
        # … and RFC 8484 content negotiation, NOT the JSON API.
        assert captured["headers"]["Content-Type"] == "application/dns-message"
        assert captured["headers"]["Accept"] == "application/dns-message"
        # body is the binary DNS query (id=0 per §4.1), not a ?name= string.
        assert captured["content"][:2] == txid
        assert b"\x07example\x03com" in captured["content"]

    @pytest.mark.asyncio
    async def test_resolve_doh_keeps_full_url(self):
        """A full DoH URL is used as-is (no double /dns-query)."""
        txid = b"\x00\x00"
        resp_bytes = _wire_response(txid, "example.com", "1.2.3.4")
        captured: dict = {}

        class _Resp:
            content = resp_bytes

            def raise_for_status(self):
                pass

        class _Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, url, content=None, headers=None):
                captured["url"] = url
                return _Resp()

        with patch.object(dns_mod.httpx, "AsyncClient", lambda *a, **k: _Client()):
            ips, _ = await dns_mod._resolve_doh(
                "example.com", "https://dns.example.test/dns-query"
            )
        assert ips == ["1.2.3.4"]
        assert captured["url"] == "https://dns.example.test/dns-query"
