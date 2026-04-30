"""Tests for DNS settings, rules CRUD, query log, and stats."""
import json
import pytest

from app.models import DNSQueryLog, Settings


class TestDNSSettings:
    def test_get_dns_settings(self, client, admin_user, auth_headers, default_settings):
        resp = client.get("/api/dns/settings", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["dns_mode"] == "plain"
        assert data["dns_upstream"] == "8.8.8.8"
        assert data["dns_sniffing"] is True

    def test_update_dns_settings(self, client, admin_user, auth_headers, default_settings):
        resp = client.patch(
            "/api/dns/settings",
            json={"dns_mode": "doh", "dns_upstream": "1.1.1.1", "fakedns_enabled": True},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["dns_mode"] == "doh"
        assert data["dns_upstream"] == "1.1.1.1"
        assert data["fakedns_enabled"] is True

    def test_update_dns_settings_partial(self, client, admin_user, auth_headers, default_settings):
        resp = client.patch(
            "/api/dns/settings",
            json={"dns_sniffing": False},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["dns_sniffing"] is False
        assert resp.json()["dns_mode"] == "plain"


class TestDNSRuleCRUD:
    def test_list_rules_empty(self, client, admin_user, auth_headers):
        resp = client.get("/api/dns/rules", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_rule(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/dns/rules",
            json={
                "name": "Netflix DNS",
                "domain_match": "netflix.com",
                "dns_server": "8.8.4.4",
                "dns_type": "plain",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Netflix DNS"
        assert data["domain_match"] == "netflix.com"
        assert "id" in data

    def test_update_rule(self, client, admin_user, auth_headers, sample_dns_rule):
        resp = client.put(
            f"/api/dns/rules/{sample_dns_rule.id}",
            json={"name": "Updated", "dns_server": "1.0.0.1"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"
        assert resp.json()["dns_server"] == "1.0.0.1"

    def test_update_rule_not_found(self, client, admin_user, auth_headers):
        resp = client.put(
            "/api/dns/rules/9999",
            json={"name": "x"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_delete_rule(self, client, admin_user, auth_headers, sample_dns_rule):
        resp = client.delete(f"/api/dns/rules/{sample_dns_rule.id}", headers=auth_headers)
        assert resp.status_code == 204

    def test_delete_rule_not_found(self, client, admin_user, auth_headers):
        resp = client.delete("/api/dns/rules/9999", headers=auth_headers)
        assert resp.status_code == 404

    def test_reorder_rules(self, client, admin_user, auth_headers, session):
        from app.models import DNSRule

        rules = []
        for i in range(3):
            r = DNSRule(
                name=f"Rule {i}", domain_match=f"site{i}.com",
                dns_server="8.8.8.8", order=i * 10,
            )
            session.add(r)
            session.commit()
            session.refresh(r)
            rules.append(r)

        reversed_ids = [r.id for r in reversed(rules)]
        resp = client.post("/api/dns/rules/reorder", json=reversed_ids, headers=auth_headers)
        # 204 No Content — matches routing.py and nodes.py reorder contract.
        assert resp.status_code == 204

        resp2 = client.get("/api/dns/rules", headers=auth_headers)
        result_ids = [r["id"] for r in resp2.json()]
        assert result_ids == reversed_ids


class TestDNSQueryLog:
    def test_list_queries(self, client, admin_user, auth_headers, sample_dns_queries):
        resp = client.get("/api/dns/queries", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 5

    def test_list_queries_with_domain_filter(self, client, admin_user, auth_headers, sample_dns_queries):
        resp = client.get("/api/dns/queries?domain=google", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert all("google" in d["domain"] for d in data)

    def test_list_queries_with_limit(self, client, admin_user, auth_headers, sample_dns_queries):
        resp = client.get("/api/dns/queries?limit=2", headers=auth_headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_queries_cache_only(self, client, admin_user, auth_headers, sample_dns_queries):
        resp = client.get("/api/dns/queries?cache_only=true", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert all(d["cache_hit"] for d in data)

    def test_clear_queries(self, client, admin_user, auth_headers, sample_dns_queries):
        resp = client.delete("/api/dns/queries", headers=auth_headers)
        assert resp.status_code == 204

        resp2 = client.get("/api/dns/queries", headers=auth_headers)
        assert resp2.json() == []

    def test_query_stats(self, client, admin_user, auth_headers, sample_dns_queries):
        resp = client.get("/api/dns/queries/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_queries"] == 5
        assert data["unique_domains"] == 3
        assert 0 <= data["cache_hit_rate"] <= 1.0
        assert isinstance(data["top_domains"], list)
        assert data["queries_last_hour"] >= 0

    def test_query_stats_empty(self, client, admin_user, auth_headers):
        resp = client.get("/api/dns/queries/stats", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_queries"] == 0
        assert data["cache_hit_rate"] == 0.0
