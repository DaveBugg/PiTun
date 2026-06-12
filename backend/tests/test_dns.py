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


# ── DNS changes auto-reload xray (fix: DNS settings/rules apply on save) ──────

from unittest.mock import patch, AsyncMock, PropertyMock


class TestDnsAutoReload:
    """Every DNS settings/rule mutation must regenerate the xray config
    and reload xray so the change takes effect immediately — same
    contract routing rules have. Pre-fix, DNS changes only landed on the
    next reload (Start/Restart, circle rotation, backend restart), which
    made DNS Rules look broken until something else reloaded xray.

    We patch xray as running + the heavy regen/reload helpers and assert
    they fire. host_fallback_dns is host-network (not xray) so a
    settings PATCH of ONLY that must NOT reload xray.
    """

    def _patches(self):
        return (
            patch("app.core.xray.XrayManager.is_running",
                  new_callable=PropertyMock, return_value=True),
            patch("app.api.system._regenerate_and_write", new_callable=AsyncMock),
            patch("app.core.xray.xray_manager.reload", new_callable=AsyncMock),
        )

    def test_settings_patch_reloads_xray(self, client, auth_headers):
        p_run, p_regen, p_reload = self._patches()
        with p_run, p_regen as regen, p_reload as reload_:
            r = client.patch("/api/dns/settings",
                             json={"dns_query_strategy": "UseIP"},
                             headers=auth_headers)
        assert r.status_code == 200
        assert regen.await_count >= 1
        assert reload_.await_count >= 1

    def test_host_fallback_only_does_not_reload_xray(self, client, auth_headers):
        """host_fallback_dns is host-resolver, not xray — no reload."""
        p_run, p_regen, p_reload = self._patches()
        with (p_run, p_regen as regen, p_reload as reload_,
              patch("app.core.network_apply.apply_host_fallback_dns",
                    return_value={"applied": False})):
            r = client.patch("/api/dns/settings",
                             json={"host_fallback_dns": "1.1.1.1"},
                             headers=auth_headers)
        assert r.status_code == 200
        assert regen.await_count == 0
        assert reload_.await_count == 0

    def test_create_rule_reloads_xray(self, client, auth_headers):
        p_run, p_regen, p_reload = self._patches()
        with p_run, p_regen, p_reload as reload_:
            r = client.post("/api/dns/rules",
                            json={"name": "yt", "domain_match": "youtube.com",
                                  "dns_server": "94.140.14.14", "dns_type": "dot",
                                  "order": 10, "enabled": True},
                            headers=auth_headers)
        assert r.status_code == 201
        assert reload_.await_count >= 1

    def test_delete_rule_reloads_xray(self, client, auth_headers):
        created = client.post("/api/dns/rules",
                              json={"name": "x", "domain_match": "x.com",
                                    "dns_server": "1.1.1.1", "dns_type": "plain",
                                    "order": 10, "enabled": True},
                              headers=auth_headers).json()
        p_run, p_regen, p_reload = self._patches()
        with p_run, p_regen, p_reload as reload_:
            r = client.delete(f"/api/dns/rules/{created['id']}", headers=auth_headers)
        assert r.status_code == 204
        assert reload_.await_count >= 1

    def test_no_reload_when_xray_stopped(self, client, auth_headers):
        """xray down → early return, /system/start builds fresh config."""
        with (patch("app.core.xray.XrayManager.is_running",
                    new_callable=PropertyMock, return_value=False),
              patch("app.api.system._regenerate_and_write",
                    new_callable=AsyncMock) as regen):
            r = client.patch("/api/dns/settings",
                             json={"dns_mode": "plain"},
                             headers=auth_headers)
        assert r.status_code == 200
        assert regen.await_count == 0
