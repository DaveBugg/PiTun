"""Roundtrip tests for `app.core.uri_formatter`.

The contract that matters most: `parse_uri_list([node_to_uri(n)])`
must yield a dict whose key fields (protocol, address, port, uuid /
password, transport, tls, reality_*) match the source Node. If a
roundtrip drops information, the export endpoint is silently lossy
and operator-visible — these tests catch that before deploy.
"""
from app.core.uri_formatter import node_to_uri
from app.core.uri_parser import parse_uri_list
from app.models import Node


def _roundtrip(node: Node):
    """Format the node, parse the resulting URI, return the parsed
    dict (or None when nothing was emitted)."""
    uri = node_to_uri(node)
    if uri is None:
        return None, None
    parsed = parse_uri_list(uri)
    assert len(parsed) == 1, f"expected 1 parsed node, got {parsed}"
    return uri, parsed[0]


class TestVless:
    def test_tcp_reality_vision_roundtrip(self):
        src = Node(
            name="my-node",
            protocol="vless",
            address="1.2.3.4",
            port=443,
            uuid="aaaa-bbbb-cccc",
            transport="tcp",
            tls="reality",
            sni="cover.example.com",
            fingerprint="chrome",
            reality_pbk="PBK-data",
            reality_sid="SID01",
            flow="xtls-rprx-vision",
        )
        uri, got = _roundtrip(src)
        assert uri.startswith("vless://aaaa-bbbb-cccc@1.2.3.4:443?")
        assert got["protocol"] == "vless"
        assert got["uuid"] == "aaaa-bbbb-cccc"
        assert got["address"] == "1.2.3.4"
        assert got["port"] == 443
        assert got["transport"] == "tcp"
        assert got["tls"] == "reality"
        assert got["sni"] == "cover.example.com"
        assert got["reality_pbk"] == "PBK-data"
        assert got["reality_sid"] == "SID01"
        assert got["flow"] == "xtls-rprx-vision"
        assert got["fingerprint"] == "chrome"

    def test_xhttp_reality_roundtrip(self):
        src = Node(
            name="xh", protocol="vless", address="1.2.3.4", port=8443,
            uuid="u-1", transport="xhttp", tls="reality",
            sni="api.github.com", reality_pbk="P", reality_sid="S",
            reality_spx="/", fingerprint="chrome",
            http_path="/api/v1/abc",
        )
        uri, got = _roundtrip(src)
        # `splithttp` is the legacy alias xray emits when parsing
        # `type=xhttp` — accept either.
        assert got["transport"] in ("xhttp", "splithttp")
        assert got["tls"] == "reality"
        assert got["http_path"] == "/api/v1/abc"

    def test_ws_tls_roundtrip(self):
        src = Node(
            name="ws", protocol="vless", address="1.2.3.4", port=443,
            uuid="u-2", transport="ws", tls="tls",
            sni="ws.example.com", ws_path="/abc", ws_host="ws.example.com",
            fingerprint="chrome",
        )
        _, got = _roundtrip(src)
        assert got["transport"] == "ws"
        assert got["tls"] == "tls"
        assert got["ws_path"] == "/abc"
        assert got["ws_host"] == "ws.example.com"


class TestTrojan:
    def test_trojan_grpc_roundtrip(self):
        src = Node(
            name="trj", protocol="trojan", address="1.2.3.4", port=443,
            password="trjpass", transport="grpc", tls="tls",
            sni="trj.example.com", grpc_service="/12345/svc",
            fingerprint="chrome",
        )
        _, got = _roundtrip(src)
        assert got["protocol"] == "trojan"
        assert got["password"] == "trjpass"
        assert got["transport"] == "grpc"
        assert got["grpc_service"] == "/12345/svc"


class TestShadowsocks:
    def test_ss_roundtrip(self):
        src = Node(
            name="ss", protocol="ss", address="1.2.3.4", port=8388,
            password="aes-256-gcm:s3cret",
        )
        _, got = _roundtrip(src)
        assert got["protocol"] == "ss"
        assert got["address"] == "1.2.3.4"
        assert got["port"] == 8388
        assert got["password"] == "aes-256-gcm:s3cret"


class TestHysteria2:
    def test_hy2_roundtrip(self):
        src = Node(
            name="hy", protocol="hy2", address="1.2.3.4", port=443,
            password="hypass", sni="hy.example.com",
        )
        _, got = _roundtrip(src)
        assert got["protocol"] == "hy2"
        assert got["password"] == "hypass"
        assert got["sni"] == "hy.example.com"


class TestSkips:
    def test_wireguard_returns_none(self):
        """WG configs need a private key + AllowedIPs block — no
        canonical URI form. Formatter returns None so the export
        endpoint just skips them."""
        src = Node(
            name="wg", protocol="wireguard",
            address="1.2.3.4", port=51820,
            wg_private_key="x" * 44,
            wg_public_key="y" * 44,
        )
        assert node_to_uri(src) is None

    def test_unknown_protocol_returns_none(self):
        src = Node(
            name="x", protocol="nonsense", address="1.2.3.4", port=1,
        )
        assert node_to_uri(src) is None
