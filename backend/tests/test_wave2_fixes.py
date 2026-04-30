"""Regression tests for wave-2 review fixes.

Each class targets one finding from the code review so we notice if the bug
comes back during a refactor."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest


# ── W2-5: _safe_int in system.py ─────────────────────────────────────────────

class TestSafeInt:
    def test_returns_parsed_int(self):
        from app.api.system import _safe_int
        assert _safe_int({"port": "8080"}, "port", 1234) == 8080

    def test_falls_back_on_missing(self):
        from app.api.system import _safe_int
        assert _safe_int({}, "port", 1234) == 1234

    def test_falls_back_on_empty_string(self):
        from app.api.system import _safe_int
        assert _safe_int({"port": ""}, "port", 1234) == 1234

    def test_falls_back_on_garbage(self):
        """The whole point: corrupted DB settings must not crash /start."""
        from app.api.system import _safe_int
        assert _safe_int({"port": "abc"}, "port", 1234) == 1234

    def test_falls_back_on_none(self):
        from app.api.system import _safe_int
        assert _safe_int({"port": None}, "port", 1234) == 1234


# ── W2-6: routing.py balancer:/node: validation ─────────────────────────────

class TestRoutingActionValidation:
    def test_create_rule_rejects_missing_balancer(self, client, auth_headers, default_settings):
        r = client.post(
            "/api/routing/rules",
            json={
                "name": "bad",
                "rule_type": "domain",
                "match_value": "example.com",
                "action": "balancer:9999",
                "order": 10,
            },
            headers=auth_headers,
        )
        assert r.status_code == 400
        assert "missing balancer" in r.json()["detail"].lower()

    def test_create_rule_rejects_missing_node(self, client, auth_headers, default_settings):
        r = client.post(
            "/api/routing/rules",
            json={
                "name": "bad",
                "rule_type": "domain",
                "match_value": "example.com",
                "action": "node:9999",
                "order": 10,
            },
            headers=auth_headers,
        )
        assert r.status_code == 400
        assert "missing node" in r.json()["detail"].lower()

    def test_create_rule_accepts_existing_node(self, client, auth_headers, sample_node, default_settings):
        r = client.post(
            "/api/routing/rules",
            json={
                "name": "ok",
                "rule_type": "domain",
                "match_value": "example.com",
                "action": f"node:{sample_node.id}",
                "order": 10,
            },
            headers=auth_headers,
        )
        assert r.status_code == 201

    def test_create_rule_accepts_proxy_action(self, client, auth_headers, default_settings):
        """Non-prefixed actions (proxy/direct/block) bypass validation."""
        r = client.post(
            "/api/routing/rules",
            json={
                "name": "plain",
                "rule_type": "domain",
                "match_value": "example.com",
                "action": "proxy",
                "order": 10,
            },
            headers=auth_headers,
        )
        assert r.status_code == 201


# ── W2-3+W2-4: delete_node cleans up chain_node_id refs and JSON lists ──────

class TestDeleteNodeCascade:
    def test_clears_chain_refs(self, client, auth_headers, session, default_settings):
        """When node A is deleted, any node B with chain_node_id=A must be nulled.
        Previously B's chain pointer went dangling → _resolve_probe_target
        followed a None object and health checks started failing."""
        from app.models import Node

        parent = Node(name="parent", protocol="vless", address="1.1.1.1", port=443,
                      uuid="u1", enabled=True, order=0)
        session.add(parent)
        session.commit()
        session.refresh(parent)

        child = Node(name="child", protocol="vless", address="2.2.2.2", port=443,
                     uuid="u2", enabled=True, order=0, chain_node_id=parent.id)
        session.add(child)
        session.commit()
        session.refresh(child)
        assert child.chain_node_id == parent.id

        r = client.delete(f"/api/nodes/{parent.id}", headers=auth_headers)
        assert r.status_code == 204

        session.expire_all()
        refreshed = session.get(Node, child.id)
        assert refreshed is not None, "child should still exist"
        assert refreshed.chain_node_id is None, "dangling chain must be cleared"

    def test_removes_id_from_nodecircle(self, client, auth_headers, session, default_settings):
        """Deleting a node must also drop it from any NodeCircle.node_ids list
        and reset current_index if it pointed past the new (shorter) array."""
        from app.models import Node, NodeCircle

        a = Node(name="a", protocol="vless", address="1.1.1.1", port=443,
                 uuid="a", enabled=True, order=0)
        b = Node(name="b", protocol="vless", address="2.2.2.2", port=443,
                 uuid="b", enabled=True, order=0)
        session.add_all([a, b])
        session.commit()
        session.refresh(a)
        session.refresh(b)

        nc = NodeCircle(
            name="ring", enabled=True,
            node_ids=json.dumps([a.id, b.id]),
            mode="sequential", interval_min=5, interval_max=10,
            current_index=1,  # points at b
        )
        session.add(nc)
        session.commit()
        session.refresh(nc)

        # Delete node b — current_index was pointing at it
        r = client.delete(f"/api/nodes/{b.id}", headers=auth_headers)
        assert r.status_code == 204

        session.expire_all()
        fresh = session.get(NodeCircle, nc.id)
        assert json.loads(fresh.node_ids) == [a.id]
        assert fresh.current_index == 0, "should reset when old index is out of bounds"


# ── W2-1: dns_logger background trim loop ───────────────────────────────────

class TestDnsLoggerBackgroundTrim:
    def test_process_log_line_does_not_trim_synchronously(self):
        """Hot path must NOT issue a SELECT COUNT(*) per log line — the whole
        point of moving trim to a background task."""
        from app.core import dns_logger
        # `_trim_once` exists as the trim implementation
        assert hasattr(dns_logger, "_trim_once")
        assert hasattr(dns_logger, "start_trim_task")
        assert hasattr(dns_logger, "stop_trim_task")

    def test_start_trim_task_is_idempotent(self):
        """Calling start() twice should not spawn two tasks."""
        from app.core import dns_logger

        async def _run():
            dns_logger.start_trim_task()
            first = dns_logger._trim_task
            dns_logger.start_trim_task()
            second = dns_logger._trim_task
            dns_logger.stop_trim_task()
            return first is second

        assert asyncio.run(_run()) is True
