"""config_gen.generate_endpoint_config — global and routing_set modes."""
from app.core.config_gen import generate_endpoint_config
from app.models import BalancerGroup, Node, RoutingRule


def _node(nid, name, port=443):
    return Node(
        id=nid, name=name, protocol="vless", address=f"192.0.2.{nid}",
        port=port, uuid=f"uuid-{nid}", transport="tcp", tls="reality",
        reality_pbk="A" * 43, reality_sid="ab12", sni="example.com",
        enabled=True,
    )


def _tags(config):
    return [o["tag"] for o in config["outbounds"]]


def test_global_mode_routes_everything_to_the_node():
    node = _node(1, "n1")
    cfg = generate_endpoint_config(
        socks_port=1081, node=node, balancer=None,
        set_rules=[], all_nodes=[node], balancer_groups=[],
        settings_map={},
    )
    assert cfg["inbounds"][0]["protocol"] == "socks"
    assert cfg["inbounds"][0]["port"] == 1081
    assert "node-1" in _tags(cfg)
    catch_all = cfg["routing"]["rules"][-1]
    assert catch_all["outboundTag"] == "node-1"
    assert catch_all.get("network") == "tcp,udp"
    # DNS goes through the node, not the local resolver.
    dns_rule = cfg["routing"]["rules"][0]
    assert dns_rule["port"] == "53" and dns_rule["outboundTag"] == "node-1"


def test_routing_set_mode_applies_set_rules_before_the_catch_all():
    node, ref = _node(1, "n1"), _node(2, "n2", port=444)
    rules = [
        RoutingRule(
            id=1, name="ads", rule_type="domain", match_value="ads.example",
            action="block", enabled=True, order=1,
        ),
        RoutingRule(
            id=2, name="via-n2", rule_type="domain", match_value="cdn.example",
            action="node:2", enabled=True, order=2,
        ),
        RoutingRule(
            id=3, name="off", rule_type="domain", match_value="off.example",
            action="direct", enabled=False, order=3,
        ),
    ]
    cfg = generate_endpoint_config(
        socks_port=1081, node=node, balancer=None,
        set_rules=rules, all_nodes=[node, ref], balancer_groups=[],
        settings_map={},
    )
    body = cfg["routing"]["rules"]
    assert body[-1]["outboundTag"] == "node-1"  # catch-all last
    assert any(r.get("outboundTag") == "block" for r in body)
    assert any(r.get("outboundTag") == "node-2" for r in body)
    assert not any("off.example" in str(r.get("domain", "")) for r in body)
    assert "node-2" in _tags(cfg), "rule-referenced node outbound is built"


def test_balancer_mode_builds_balancer_and_members():
    m1, m2 = _node(1, "m1"), _node(2, "m2", port=444)
    bg = BalancerGroup(id=5, name="pool", enabled=True,
                       node_ids="[1, 2]", strategy="leastPing")
    cfg = generate_endpoint_config(
        socks_port=1081, node=None, balancer=bg,
        set_rules=[], all_nodes=[m1, m2], balancer_groups=[bg],
        settings_map={},
    )
    assert "node-1" in _tags(cfg) and "node-2" in _tags(cfg)
    balancers = cfg["routing"]["balancers"]
    assert balancers[0]["tag"] == "balancer-5"
    assert balancers[0]["selector"] == ["node-1", "node-2"]
    assert cfg["routing"]["rules"][-1]["balancerTag"] == "balancer-5"
    # DNS rule must follow the same egress contract as the catch-all.
    dns_rule = cfg["routing"]["rules"][0]
    assert dns_rule["port"] == "53"
    assert dns_rule["balancerTag"] == "balancer-5"
    assert "outboundTag" not in dns_rule

    # Node endpoints keep the original outboundTag behaviour.
    node_cfg = generate_endpoint_config(
        socks_port=1082, node=m1, balancer=None,
        set_rules=[], all_nodes=[m1], balancer_groups=[],
        settings_map={},
    )
    node_dns = node_cfg["routing"]["rules"][0]
    assert node_dns["port"] == "53"
    assert node_dns["outboundTag"] == "node-1"
    assert "balancerTag" not in node_dns


def test_lan_auth_locks_the_socks_inbound():
    node = _node(1, "n1")
    cfg = generate_endpoint_config(
        socks_port=1081, node=node, balancer=None,
        set_rules=[], all_nodes=[node], balancer_groups=[],
        settings_map={
            "lan_proxy_auth_enabled": "true",
            "lan_proxy_auth_user": "u", "lan_proxy_auth_pass": "p",
        },
    )
    s = cfg["inbounds"][0]["settings"]
    assert s["auth"] == "password"
    assert s["accounts"] == [{"user": "u", "pass": "p"}]


def test_exactly_one_egress_enforced():
    node = _node(1, "n1")
    bg = BalancerGroup(id=5, name="pool", enabled=True, node_ids="[1]")
    for kwargs in (dict(node=None, balancer=None), dict(node=node, balancer=bg)):
        try:
            generate_endpoint_config(
                socks_port=1081, set_rules=[], all_nodes=[node],
                balancer_groups=[], settings_map={}, **kwargs,
            )
            raise AssertionError("should have raised ValueError")
        except ValueError:
            pass


def test_dead_balancer_is_a_loud_error():
    bg = BalancerGroup(id=5, name="pool", enabled=True, node_ids="[]")
    try:
        generate_endpoint_config(
            socks_port=1081, node=None, balancer=bg,
            set_rules=[], all_nodes=[], balancer_groups=[bg],
            settings_map={},
        )
        raise AssertionError("should have raised ValueError")
    except ValueError as exc:
        assert "no usable member" in str(exc)
