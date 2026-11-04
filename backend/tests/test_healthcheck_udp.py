"""UDP protocols must be probed over UDP — a TCP connect to a Hysteria2 port always fails."""
import asyncio
from types import SimpleNamespace

from app.core.healthcheck import HealthChecker


class _Session:
    def __init__(self, *nodes):
        self.nodes = {n.id: n for n in nodes}

    async def get(self, _model, node_id):
        return self.nodes.get(node_id)


def _node(id, protocol, chain=None, enabled=True):
    return SimpleNamespace(id=id, protocol=protocol, address=f"192.0.2.{id}", port=8443,
                           chain_node_id=chain, enabled=enabled)


def _target(session, node):
    return asyncio.run(HealthChecker._resolve_probe_target(session, node))


def test_hysteria2_is_probed_over_udp():
    n = _node(1, "hy2")
    assert _target(_Session(n), n) == ("192.0.2.1", 8443, True)


def test_wireguard_is_still_probed_over_udp():
    n = _node(1, "wireguard")
    assert _target(_Session(n), n)[2] is True


def test_stream_protocols_stay_on_tcp():
    for proto in ("vless", "vmess", "trojan", "shadowsocks"):
        n = _node(1, proto)
        assert _target(_Session(n), n)[2] is False, proto


def test_a_chained_node_follows_its_parents_transport():
    parent = _node(1, "vless")
    child = _node(2, "hy2", chain=1)
    assert _target(_Session(parent, child), child) == ("192.0.2.1", 8443, False)


def test_the_protocol_name_a_parsed_hysteria2_link_gets_is_the_one_probed_over_udp():
    """The stored protocol is "hy2", not "hysteria2" — a probe list keyed on the
    wrong spelling silently changes nothing."""
    from app.core.uri_parser import parse_uri

    parsed = parse_uri("hysteria2://secret@192.0.2.9:8443/?sni=example.com#n")
    assert parsed and parsed["protocol"] == "hy2"
    n = _node(1, parsed["protocol"])
    assert _target(_Session(n), n)[2] is True
