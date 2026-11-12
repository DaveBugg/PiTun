"""Settings → DNS "DNS goes through": which outbound the DNS upstream queries use."""
import copy

import pytest

from app.core.config_gen import DNS_UPSTREAM_TAG, generate_config
from tests.test_config_gen import _default_settings, _find_outbound, _make_node


def _cfg(dns_outbound=None, nodes=None, active=1, **extra):
    nodes = nodes or [_make_node(id=1), _make_node(id=2, address="203.0.113.8")]
    s = _default_settings(**extra)
    if dns_outbound is not None:
        s["dns_outbound"] = dns_outbound
    act = next(n for n in nodes if n.id == active)
    return generate_config(act, nodes, [], "global", s)


def _tags(cfg):
    return [s.get("tag") for s in cfg["dns"]["servers"] if isinstance(s, dict)]


def _dns_rule(cfg):
    return [r for r in cfg["routing"]["rules"] if r.get("inboundTag") == [DNS_UPSTREAM_TAG]]


def test_default_changes_nothing():
    base = _cfg()
    assert _cfg(dns_outbound="") == base
    assert DNS_UPSTREAM_TAG not in _tags(base)
    assert _dns_rule(base) == []


def test_unknown_value_is_ignored():
    assert _cfg(dns_outbound="banana") == _cfg()
    assert _cfg(dns_outbound="node-x") == _cfg()


def test_direct_routes_dns_upstreams_directly():
    cfg = _cfg(dns_outbound="direct")
    assert DNS_UPSTREAM_TAG in _tags(cfg)
    assert _dns_rule(cfg)[0]["outboundTag"] == "direct"


def test_node_routes_dns_upstreams_through_that_node_and_builds_its_outbound():
    cfg = _cfg(dns_outbound="node-2", active=1)
    assert _dns_rule(cfg)[0]["outboundTag"] == "node-2"
    assert _find_outbound(cfg, "node-2") is not None


def test_dns_rule_comes_before_the_port_53_pin_and_user_rules():
    cfg = _cfg(dns_outbound="node-2")
    rules = cfg["routing"]["rules"]
    i = rules.index(_dns_rule(cfg)[0])
    pin = next(k for k, r in enumerate(rules) if r.get("port") == "53")
    assert i < pin


def test_missing_or_disabled_node_falls_back_to_normal_routing():
    base = _cfg()
    assert _cfg(dns_outbound="node-99") == base
    nodes = [_make_node(id=1), _make_node(id=2, address="203.0.113.8", enabled=False)]
    assert _cfg(dns_outbound="node-2", nodes=nodes) == _cfg(nodes=nodes)


def test_only_the_upstreams_are_tagged_not_dns_rule_servers():
    from app.models import DNSRule
    nodes = [_make_node(id=1), _make_node(id=2, address="203.0.113.8")]
    s = _default_settings(dns_upstream_secondary="1.1.1.1")
    s["dns_outbound"] = "node-2"
    rule = DNSRule(id=1, name="r", domain_match="example.org", dns_server="9.9.9.9",
                   dns_type="plain", enabled=True, order=0)
    cfg = generate_config(nodes[0], nodes, [], "global", s, [rule])
    servers = [x for x in cfg["dns"]["servers"] if isinstance(x, dict)]
    tagged = [x["address"] for x in servers if x.get("tag") == DNS_UPSTREAM_TAG]
    assert "9.9.9.9" not in tagged
    assert "8.8.8.8" in tagged and "1.1.1.1" in tagged


# ── API ──────────────────────────────────────────────────────────────────────

def test_settings_api_validates_and_roundtrips(client, admin_user, auth_headers, session):
    from app.models import Node
    n = Node(name="n", protocol="vless", address="192.0.2.5", port=443, uuid="u",
             transport="tcp", enabled=True)
    off = Node(name="off", protocol="vless", address="192.0.2.6", port=443, uuid="u",
               transport="tcp", enabled=False)
    session.add(n); session.add(off); session.commit()
    session.refresh(n); session.refresh(off)

    assert client.get("/api/dns/settings", headers=auth_headers).json()["dns_outbound"] == ""
    for bad in ("banana", "node-9999", f"node-{off.id}"):
        r = client.patch("/api/dns/settings", json={"dns_outbound": bad}, headers=auth_headers)
        assert r.status_code == 400, bad
    for good in (f"node-{n.id}", "direct", ""):
        r = client.patch("/api/dns/settings", json={"dns_outbound": good}, headers=auth_headers)
        assert r.status_code == 200 and r.json()["dns_outbound"] == good


def test_test_node_endpoint_resolves_through_the_tunnel(client, admin_user, auth_headers, session):
    from unittest import mock
    from unittest.mock import AsyncMock
    from app.models import Node
    n = Node(name="via", protocol="vless", address="192.0.2.7", port=443, uuid="u",
             transport="tcp", enabled=True)
    session.add(n); session.commit(); session.refresh(n)

    seen = {}

    async def fake_doh(domain, server, proxy=None):
        seen.update(domain=domain, server=server, proxy=proxy)
        return ["192.0.2.50"], 12

    with mock.patch("app.core.speedtest._live_socks_ready", new=AsyncMock(return_value=True)), \
         mock.patch("app.api.dns._resolve_doh", new=fake_doh):
        r = client.post("/api/dns/test-node", headers=auth_headers,
                        json={"domain": "example.com", "node_id": n.id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resolved_ips"] == ["192.0.2.50"] and body["error"] is None
    assert "via" in body["server_used"]
    assert seen["proxy"].startswith("socks5h://127.0.0.1:")


def test_test_node_endpoint_reports_failure_and_rejects_private_server(client, admin_user, auth_headers, session):
    from unittest import mock
    from unittest.mock import AsyncMock
    from app.models import Node
    n = Node(name="via", protocol="vless", address="192.0.2.7", port=443, uuid="u",
             transport="tcp", enabled=True)
    session.add(n); session.commit(); session.refresh(n)

    async def boom(domain, server, proxy=None):
        raise RuntimeError("no route")

    with mock.patch("app.core.speedtest._live_socks_ready", new=AsyncMock(return_value=True)), \
         mock.patch("app.api.dns._resolve_doh", new=boom):
        r = client.post("/api/dns/test-node", headers=auth_headers,
                        json={"domain": "example.com", "node_id": n.id})
        assert r.json()["error"]
        r = client.post("/api/dns/test-node", headers=auth_headers,
                        json={"domain": "example.com", "node_id": n.id, "server": "10.0.0.1"})
        assert r.status_code == 400
    assert client.post("/api/dns/test-node", headers=auth_headers,
                       json={"domain": "example.com", "node_id": 9999}).status_code == 404


def test_node_dialled_by_hostname_is_not_used_for_dns():
    nodes = [_make_node(id=1), _make_node(id=2, address="proxy.example.com")]
    assert _cfg(dns_outbound="node-2", nodes=nodes) == _cfg(nodes=nodes)


def test_chained_node_checks_the_entry_hop_address():
    relay = _make_node(id=3, address="proxy.example.com")
    exit_ = _make_node(id=2, address="203.0.113.8", chain_node_id=3)
    nodes = [_make_node(id=1), exit_, relay]
    assert _cfg(dns_outbound="node-2", nodes=nodes) == _cfg(nodes=nodes)


def test_a_stale_choice_does_not_block_saving_other_dns_settings(client, admin_user, auth_headers, session):
    from app.models import Node
    n = Node(name="n", protocol="vless", address="192.0.2.5", port=443, uuid="u",
             transport="tcp", enabled=True)
    session.add(n)
    session.commit()
    session.refresh(n)
    assert client.patch("/api/dns/settings", json={"dns_outbound": f"node-{n.id}"},
                        headers=auth_headers).status_code == 200
    n.enabled = False
    session.add(n)
    session.commit()
    r = client.patch("/api/dns/settings",
                     json={"dns_outbound": f"node-{n.id}", "dns_query_strategy": "UseIP"},
                     headers=auth_headers)
    assert r.status_code == 200 and r.json()["dns_query_strategy"] == "UseIP"
    # a NEW choice of a missing node is still refused
    r = client.patch("/api/dns/settings", json={"dns_outbound": "node-9999"}, headers=auth_headers)
    assert r.status_code == 400


def test_hostname_node_is_refused_by_the_api(client, admin_user, auth_headers, session):
    from app.models import Node
    n = Node(name="h", protocol="vless", address="proxy.example.com", port=443, uuid="u",
             transport="tcp", enabled=True)
    session.add(n)
    session.commit()
    session.refresh(n)
    r = client.patch("/api/dns/settings", json={"dns_outbound": f"node-{n.id}"}, headers=auth_headers)
    assert r.status_code == 400 and "hostname" in r.json()["detail"]


@pytest.mark.parametrize("upstream,expected", [
    ("tls://1.1.1.1", "1.1.1.1"),
    ("quic://dns.example.com:853", "dns.example.com"),
    ("https://dns.example.com/dns-query", "https://dns.example.com/dns-query"),
    ("192.168.1.53", "8.8.8.8"),          # LAN resolver: test with a public one
    ("", "8.8.8.8"),
])
def test_test_node_turns_the_saved_upstream_into_a_doh_server(client, admin_user, auth_headers, session,
                                                               upstream, expected):
    from unittest import mock
    from unittest.mock import AsyncMock
    from app.models import Node, Settings
    n = Node(name="via", protocol="vless", address="192.0.2.7", port=443, uuid="u",
             transport="tcp", enabled=True)
    session.add(n)
    session.add(Settings(key="dns_upstream", value=upstream))
    session.commit(); session.refresh(n)
    seen = {}

    async def fake_doh(domain, server, proxy=None):
        seen["server"] = server
        return ["192.0.2.50"], 5

    with mock.patch("app.core.speedtest._live_socks_ready", new=AsyncMock(return_value=True)), \
         mock.patch("app.api.dns._resolve_doh", new=fake_doh):
        r = client.post("/api/dns/test-node", headers=auth_headers,
                        json={"domain": "example.com", "node_id": n.id})
    assert r.status_code == 200, r.text
    assert seen["server"] == expected


def test_test_node_refuses_private_server_hidden_behind_a_scheme_and_disabled_nodes(
        client, admin_user, auth_headers, session):
    from app.models import Node
    n = Node(name="via", protocol="vless", address="192.0.2.7", port=443, uuid="u",
             transport="tcp", enabled=True)
    off = Node(name="off", protocol="vless", address="192.0.2.8", port=443, uuid="u",
               transport="tcp", enabled=False)
    session.add(n); session.add(off); session.commit(); session.refresh(n); session.refresh(off)
    r = client.post("/api/dns/test-node", headers=auth_headers,
                    json={"domain": "example.com", "node_id": n.id, "server": "tls://10.0.0.1"})
    assert r.status_code == 400
    r = client.post("/api/dns/test-node", headers=auth_headers,
                    json={"domain": "example.com", "node_id": off.id})
    assert r.status_code == 400
