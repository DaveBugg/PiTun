"""Tests for balancer group CRUD and cascade delete."""
import pytest


class TestBalancerCRUD:
    def test_list_empty(self, client, admin_user, auth_headers):
        resp = client.get("/api/balancers", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_balancer(self, client, admin_user, auth_headers, sample_node):
        resp = client.post(
            "/api/balancers",
            json={
                "name": "My Balancer",
                "node_ids": [sample_node.id],
                "strategy": "leastPing",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "My Balancer"
        assert data["strategy"] == "leastPing"
        assert sample_node.id in data["node_ids"]


class TestDeleteBalancerCascade:
    def test_delete_balancer_cleans_rules(self, client, admin_user, auth_headers, session):
        from app.models import BalancerGroup, RoutingRule
        import json as json_mod

        # Create a balancer
        bg = BalancerGroup(name="ToDelete", enabled=True, node_ids="[]", strategy="random")
        session.add(bg)
        session.commit()
        session.refresh(bg)

        # Create a routing rule pointing to that balancer
        rule = RoutingRule(
            name="Rule for balancer", rule_type="domain",
            match_value="test.com", action=f"balancer:{bg.id}",
            enabled=True, order=100,
        )
        session.add(rule)
        session.commit()
        session.refresh(rule)
        rule_id = rule.id

        # Delete the balancer
        resp = client.delete(f"/api/balancers/{bg.id}", headers=auth_headers)
        assert resp.status_code == 204

        # Verify the routing rule is also deleted
        resp2 = client.get(f"/api/routing/rules/{rule_id}", headers=auth_headers)
        assert resp2.status_code == 404
