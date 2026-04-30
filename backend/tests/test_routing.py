"""Tests for routing rules CRUD, reorder, and bulk create."""
import pytest


class TestRoutingRuleCRUD:
    def test_list_rules_empty(self, client, admin_user, auth_headers):
        resp = client.get("/api/routing/rules", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_rule(self, client, admin_user, auth_headers):
        rule_data = {
            "name": "Block ads", "rule_type": "domain",
            "match_value": "ads.example.com", "action": "block",
        }
        resp = client.post("/api/routing/rules", json=rule_data, headers=auth_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Block ads"
        assert data["action"] == "block"
        assert "id" in data

    def test_create_rule_invalid_type(self, client, admin_user, auth_headers):
        rule_data = {
            "name": "Bad rule", "rule_type": "invalid",
            "match_value": "foo", "action": "direct",
        }
        resp = client.post("/api/routing/rules", json=rule_data, headers=auth_headers)
        assert resp.status_code == 422

    def test_create_rule_invalid_action(self, client, admin_user, auth_headers):
        rule_data = {
            "name": "Bad action", "rule_type": "domain",
            "match_value": "foo.com", "action": "invalid",
        }
        resp = client.post("/api/routing/rules", json=rule_data, headers=auth_headers)
        assert resp.status_code == 422

    def test_update_rule(self, client, admin_user, auth_headers, sample_rule):
        resp = client.patch(
            f"/api/routing/rules/{sample_rule.id}",
            json={"name": "Updated rule"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated rule"

    def test_delete_rule(self, client, admin_user, auth_headers, sample_rule):
        resp = client.delete(f"/api/routing/rules/{sample_rule.id}", headers=auth_headers)
        assert resp.status_code == 204

        resp2 = client.get(f"/api/routing/rules/{sample_rule.id}", headers=auth_headers)
        assert resp2.status_code == 404


class TestRoutingReorder:
    def test_reorder_rules(self, client, admin_user, auth_headers, session):
        from app.models import RoutingRule

        rules = []
        for i in range(3):
            r = RoutingRule(
                name=f"Rule {i}", rule_type="domain",
                match_value=f"site{i}.com", action="direct",
                enabled=True, order=i * 10,
            )
            session.add(r)
            session.commit()
            session.refresh(r)
            rules.append(r)

        reversed_ids = [r.id for r in reversed(rules)]
        resp = client.post("/api/routing/rules/reorder", json=reversed_ids, headers=auth_headers)
        assert resp.status_code == 204

        resp2 = client.get("/api/routing/rules", headers=auth_headers)
        result = resp2.json()
        result_ids = [r["id"] for r in result]
        assert result_ids == reversed_ids


class TestBulkCreate:
    def test_bulk_create(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/routing/rules/bulk",
            json={
                "rule_type": "domain",
                "action": "direct",
                "values": "a.com\nb.com\nc.com\nd.com\ne.com",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["created"] == 5
        assert len(data["rule_ids"]) == 1  # single rule with comma-joined values

    def test_bulk_create_invalid_type(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/routing/rules/bulk",
            json={
                "rule_type": "invalid",
                "action": "direct",
                "values": "a.com",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422
