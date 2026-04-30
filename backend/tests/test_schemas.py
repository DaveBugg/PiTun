"""Tests for Pydantic schema validators."""
import pytest
from pydantic import ValidationError

from app.schemas import (
    SubscriptionBase, BulkRuleCreate, ChangePasswordRequest,
    NodeBase, ModeUpdate, NodeCircleBase,
    BalancerGroupBase, DeviceUpdate, DeviceBulkUpdate,
)


# ── Subscription URL validation ──────────────────────────────────────────────

class TestSubscriptionURL:
    def test_subscription_url_blocks_localhost(self):
        with pytest.raises(ValidationError, match="internal"):
            SubscriptionBase(name="bad", url="http://127.0.0.1/sub")

    def test_subscription_url_blocks_private(self):
        with pytest.raises(ValidationError, match="private"):
            SubscriptionBase(name="bad", url="http://10.0.0.1/sub")

    def test_subscription_url_blocks_192_168(self):
        with pytest.raises(ValidationError, match="private"):
            SubscriptionBase(name="bad", url="http://192.168.1.1/sub")

    def test_subscription_url_allows_public(self):
        sub = SubscriptionBase(name="good", url="https://example.com/sub")
        assert sub.url == "https://example.com/sub"


# ── Bulk rule validation ─────────────────────────────────────────────────────

class TestBulkRuleValidation:
    def test_bulk_rule_validates_type(self):
        with pytest.raises(ValidationError, match="rule_type"):
            BulkRuleCreate(rule_type="invalid", action="direct", values="a.com")

    def test_bulk_rule_validates_action(self):
        with pytest.raises(ValidationError, match="action"):
            BulkRuleCreate(rule_type="domain", action="invalid", values="a.com")

    def test_bulk_rule_valid(self):
        r = BulkRuleCreate(rule_type="domain", action="proxy", values="a.com\nb.com")
        assert r.rule_type == "domain"


# ── Change password ──────────────────────────────────────────────────────────

class TestChangePasswordValidation:
    def test_change_password_min_length(self):
        with pytest.raises(ValidationError, match="8 characters"):
            ChangePasswordRequest(current_password="old", new_password="short")

    def test_change_password_valid(self):
        req = ChangePasswordRequest(current_password="old", new_password="longEnough1!")
        assert req.new_password == "longEnough1!"


# ── NodeBase protocol/transport/tls ──────────────────────────────────────────

_NODE_DEFAULTS = dict(name="n", address="1.2.3.4", port=443)


class TestNodeBaseValidation:
    def test_valid_protocols(self):
        for proto in ("vless", "vmess", "trojan", "ss", "wireguard", "socks", "hy2"):
            n = NodeBase(protocol=proto, **_NODE_DEFAULTS)
            assert n.protocol == proto

    def test_invalid_protocol(self):
        with pytest.raises(ValidationError, match="protocol"):
            NodeBase(protocol="http", **_NODE_DEFAULTS)

    def test_valid_transports(self):
        for t in ("tcp", "ws", "grpc", "h2", "xhttp", "httpupgrade", "kcp", "quic"):
            n = NodeBase(protocol="vless", transport=t, **_NODE_DEFAULTS)
            assert n.transport == t

    def test_invalid_transport(self):
        with pytest.raises(ValidationError, match="transport"):
            NodeBase(protocol="vless", transport="udp", **_NODE_DEFAULTS)

    def test_valid_tls_values(self):
        for t in ("none", "tls", "reality"):
            n = NodeBase(protocol="vless", tls=t, **_NODE_DEFAULTS)
            assert n.tls == t

    def test_invalid_tls(self):
        with pytest.raises(ValidationError, match="tls"):
            NodeBase(protocol="vless", tls="dtls", **_NODE_DEFAULTS)


# ── ModeUpdate ───────────────────────────────────────────────────────────────

class TestModeUpdateValidation:
    def test_valid_modes(self):
        for m in ("global", "rules", "bypass"):
            obj = ModeUpdate(mode=m)
            assert obj.mode == m

    def test_invalid_mode(self):
        with pytest.raises(ValidationError, match="mode"):
            ModeUpdate(mode="direct")


# ── NodeCircleBase ───────────────────────────────────────────────────────────

class TestNodeCircleBaseValidation:
    def test_valid(self):
        c = NodeCircleBase(name="c", node_ids=[1, 2], mode="sequential", interval_min=5, interval_max=15)
        assert c.mode == "sequential"

    def test_invalid_mode(self):
        with pytest.raises(ValidationError, match="mode"):
            NodeCircleBase(name="c", mode="roundrobin")

    def test_interval_min_too_low(self):
        with pytest.raises(ValidationError, match="interval_min"):
            NodeCircleBase(name="c", interval_min=0, interval_max=10)

    def test_interval_max_less_than_min(self):
        with pytest.raises(ValidationError, match="interval_max"):
            NodeCircleBase(name="c", interval_min=20, interval_max=5)


# ── BalancerGroupBase ────────────────────────────────────────────────────────

class TestBalancerGroupBaseValidation:
    def test_valid_strategies(self):
        for s in ("leastPing", "random"):
            b = BalancerGroupBase(name="b", strategy=s)
            assert b.strategy == s

    def test_invalid_strategy(self):
        with pytest.raises(ValidationError, match="strategy"):
            BalancerGroupBase(name="b", strategy="weighted")


# ── DeviceUpdate ─────────────────────────────────────────────────────────────

class TestDeviceUpdateValidation:
    def test_valid_policies(self):
        for p in ("default", "include", "exclude"):
            d = DeviceUpdate(routing_policy=p)
            assert d.routing_policy == p

    def test_invalid_policy(self):
        with pytest.raises(ValidationError, match="routing_policy"):
            DeviceUpdate(routing_policy="block")

    def test_none_policy_ok(self):
        d = DeviceUpdate(name="test")
        assert d.routing_policy is None


# ── DeviceBulkUpdate ─────────────────────────────────────────────────────────

class TestDeviceBulkUpdateValidation:
    def test_valid(self):
        d = DeviceBulkUpdate(device_ids=[1, 2], routing_policy="include")
        assert d.routing_policy == "include"

    def test_invalid_policy(self):
        with pytest.raises(ValidationError, match="routing_policy"):
            DeviceBulkUpdate(device_ids=[1], routing_policy="something")
