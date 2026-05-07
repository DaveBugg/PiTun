"""Tests for v1.2.7 routing-rule validation surface:

  * `_validate_geo_tags` — pre-flight HTTP 400 on rule save when the
    referenced `geosite:X` / `geoip:X` is missing from the loaded `.dat`.
  * `/routing/auto-disabled` inbox endpoints (GET / re-enable / delete /
    dismiss-all).

Tests use direct cache injection (`monkeypatch.setattr` on the
`AVAILABLE_*_TAGS` module attributes) instead of going through the
parser, since the parser already has its own coverage in
`test_geo_parser.py`.
"""
from __future__ import annotations

import json

import pytest

from app.api.routing import _validate_geo_tags
from app.models import RoutingRule, Settings as DBSettings
from fastapi import HTTPException


# ── _validate_geo_tags (direct unit tests) ───────────────────────────────────


class TestValidateGeoTags:
    """Direct calls — `_validate_geo_tags` is sync and pure (reads
    module-level cache, raises HTTPException). No DB session needed.
    """

    def _seed(self, monkeypatch, geosite=None, geoip=None):
        from app.core import geo as geo_mod
        monkeypatch.setattr(geo_mod, "AVAILABLE_GEOSITE_TAGS", set(geosite or []))
        monkeypatch.setattr(geo_mod, "AVAILABLE_GEOIP_TAGS", set(geoip or []))

    def test_geosite_present_in_cache_passes(self, monkeypatch):
        self._seed(monkeypatch, geosite={"category-cn", "category-ru"})
        # Doesn't raise
        _validate_geo_tags("geosite", "category-cn")

    def test_geosite_missing_raises_400(self, monkeypatch):
        self._seed(monkeypatch, geosite={"category-cn"})
        with pytest.raises(HTTPException) as excinfo:
            _validate_geo_tags("geosite", "category-telemetry")
        assert excinfo.value.status_code == 400
        assert "geosite:category-telemetry" in excinfo.value.detail.lower() \
            or "category-telemetry" in excinfo.value.detail.lower()

    def test_geoip_missing_raises_400(self, monkeypatch):
        self._seed(monkeypatch, geoip={"cn", "ru"})
        with pytest.raises(HTTPException) as excinfo:
            _validate_geo_tags("geoip", "us")
        assert excinfo.value.status_code == 400

    def test_case_insensitive_match(self, monkeypatch):
        # Cache stores lower-cased; match_value can be any case.
        self._seed(monkeypatch, geosite={"category-cn"})
        _validate_geo_tags("geosite", "CATEGORY-CN")  # passes
        _validate_geo_tags("geosite", "Category-Cn")  # passes

    def test_comma_separated_all_must_be_present(self, monkeypatch):
        self._seed(monkeypatch, geosite={"category-cn", "category-ru"})
        # All present → ok
        _validate_geo_tags("geosite", "category-cn,category-ru")
        # One missing → reject
        with pytest.raises(HTTPException) as excinfo:
            _validate_geo_tags("geosite", "category-cn,category-telemetry")
        assert "category-telemetry" in excinfo.value.detail.lower()

    def test_domain_rule_inline_geosite_validated(self, monkeypatch):
        # `rule_type='domain'` with mixed values containing `geosite:X`
        # must check the embedded tag too.
        self._seed(monkeypatch, geosite={"category-cn"})
        # Plain domain entry mixed with valid geosite → ok
        _validate_geo_tags("domain", "domain:google.com,geosite:category-cn")
        # Plain domain mixed with invalid geosite → reject
        with pytest.raises(HTTPException):
            _validate_geo_tags("domain", "domain:google.com,geosite:nonexistent")

    def test_domain_rule_inline_geoip_validated(self, monkeypatch):
        self._seed(monkeypatch, geoip={"cn"})
        with pytest.raises(HTTPException):
            _validate_geo_tags("domain", "geoip:nonexistent-country")

    def test_other_rule_types_skipped(self, monkeypatch):
        # mac/src_ip/dst_ip/port/protocol — no validation regardless
        self._seed(monkeypatch, geosite=set())
        for rt in ("mac", "src_ip", "dst_ip", "port", "protocol"):
            _validate_geo_tags(rt, "any-value-here")  # should not raise

    def test_empty_match_value_passes(self, monkeypatch):
        self._seed(monkeypatch, geosite=set())
        _validate_geo_tags("geosite", "")
        _validate_geo_tags("geosite", None)  # type: ignore[arg-type]

    def test_empty_cache_fails_open(self, monkeypatch):
        # When `.dat` hasn't been parsed yet (e.g. backend just started
        # without geo files), validation should NOT reject — that would
        # brick the rule-CRUD surface. Empty cache = "we don't know,
        # let it through".
        self._seed(monkeypatch, geosite=set(), geoip=set())
        # Both kinds: should pass through
        _validate_geo_tags("geosite", "anything-goes")
        _validate_geo_tags("geoip", "ZZ")
        _validate_geo_tags("domain", "geosite:foo,geoip:bar")

    def test_message_includes_fix_hint(self, monkeypatch):
        # The error message must include the "switch geo profile or
        # remove rule" hint so the user knows what to do.
        self._seed(monkeypatch, geosite={"cn"})
        with pytest.raises(HTTPException) as excinfo:
            _validate_geo_tags("geosite", "category-telemetry")
        assert "geo profile" in excinfo.value.detail.lower() \
            or "switch" in excinfo.value.detail.lower() \
            or "remove" in excinfo.value.detail.lower()


# ── Rule CRUD endpoints — pre-flight integration ─────────────────────────────


class TestRuleCRUDValidation:
    """End-to-end through the API: prove the validator is wired into
    `POST /routing/rules` and `PATCH /routing/rules/{id}`.
    """

    def _seed_cache(self, monkeypatch, geosite=None, geoip=None):
        from app.core import geo as geo_mod
        monkeypatch.setattr(geo_mod, "AVAILABLE_GEOSITE_TAGS", set(geosite or []))
        monkeypatch.setattr(geo_mod, "AVAILABLE_GEOIP_TAGS", set(geoip or []))

    def test_create_rejects_missing_geosite(
        self, client, admin_user, auth_headers, default_settings, monkeypatch
    ):
        self._seed_cache(monkeypatch, geosite={"category-cn"})
        resp = client.post(
            "/api/routing/rules",
            json={
                "name": "telemetry rule",
                "rule_type": "geosite",
                "match_value": "category-telemetry",
                "action": "block",
                "enabled": True,
                "order": 100,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 400, resp.text
        body = resp.json()
        assert "category-telemetry" in body.get("detail", "").lower()

    def test_create_accepts_valid_geosite(
        self, client, admin_user, auth_headers, default_settings, monkeypatch
    ):
        self._seed_cache(monkeypatch, geosite={"category-cn"})
        resp = client.post(
            "/api/routing/rules",
            json={
                "name": "cn rule",
                "rule_type": "geosite",
                "match_value": "category-cn",
                "action": "direct",
                "enabled": True,
                "order": 100,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text

    def test_patch_rejects_change_to_missing_tag(
        self, client, admin_user, auth_headers, default_settings, sample_rule, monkeypatch
    ):
        # `sample_rule` is a domain rule. Patch it to a geosite rule
        # with a missing tag → should be rejected.
        self._seed_cache(monkeypatch, geosite={"category-cn"})
        resp = client.patch(
            f"/api/routing/rules/{sample_rule.id}",
            json={"rule_type": "geosite", "match_value": "missing-tag"},
            headers=auth_headers,
        )
        assert resp.status_code == 400


# ── Auto-disabled inbox endpoints ────────────────────────────────────────────


class TestAutoDisabledInbox:
    """Coverage for `/routing/auto-disabled/*`. We populate the
    `auto_disabled_rules` Settings key directly (simulating what the
    self-heal pass writes) rather than going through the full
    config_gen → xray → heal flow (covered separately in test_self_heal).
    """

    def _seed_inbox(self, session, items: list[dict]):
        """Insert/replace the auto_disabled_rules Settings row."""
        from sqlmodel import select
        existing = session.exec(
            select(DBSettings).where(DBSettings.key == "auto_disabled_rules")
        ).first()
        if existing:
            existing.value = json.dumps(items)
            session.add(existing)
        else:
            session.add(DBSettings(key="auto_disabled_rules", value=json.dumps(items)))
        session.commit()

    def test_get_empty_when_no_setting(
        self, client, admin_user, auth_headers, default_settings
    ):
        resp = client.get("/api/routing/auto-disabled", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    def test_get_returns_seeded_items(
        self, client, admin_user, auth_headers, default_settings, sample_rule, session
    ):
        self._seed_inbox(session, [
            {
                "rule_id": sample_rule.id,
                "name": sample_rule.name,
                "rule_type": "geosite",
                "match_value": "category-telemetry",
                "missing_kind": "geosite",
                "missing_tag": "category-telemetry",
                "disabled_at": "2026-05-07T12:00:00+00:00",
            }
        ])
        resp = client.get("/api/routing/auto-disabled", headers=auth_headers)
        assert resp.status_code == 200
        items = resp.json()["items"]
        assert len(items) == 1
        assert items[0]["rule_id"] == sample_rule.id
        assert items[0]["missing_tag"] == "category-telemetry"

    def test_get_handles_corrupt_json(
        self, client, admin_user, auth_headers, default_settings, session
    ):
        # Bad JSON in the Settings row shouldn't crash the endpoint.
        session.add(DBSettings(key="auto_disabled_rules", value="not valid json {{"))
        session.commit()
        resp = client.get("/api/routing/auto-disabled", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"items": []}

    def test_re_enable_flips_rule_and_removes_from_inbox(
        self, client, admin_user, auth_headers, default_settings, sample_rule, session
    ):
        # Disable the rule in DB and add it to inbox
        sample_rule.enabled = False
        session.add(sample_rule)
        session.commit()
        self._seed_inbox(session, [
            {
                "rule_id": sample_rule.id,
                "name": sample_rule.name,
                "rule_type": "geosite",
                "match_value": "category-telemetry",
                "missing_kind": "geosite",
                "missing_tag": "category-telemetry",
                "disabled_at": "2026-05-07T12:00:00+00:00",
            }
        ])

        resp = client.post(
            f"/api/routing/auto-disabled/{sample_rule.id}/re-enable",
            headers=auth_headers,
        )
        assert resp.status_code == 204

        # Rule re-enabled
        session.refresh(sample_rule)
        assert sample_rule.enabled is True

        # Inbox empty
        resp = client.get("/api/routing/auto-disabled", headers=auth_headers)
        assert resp.json() == {"items": []}

    def test_delete_removes_rule_and_inbox_entry(
        self, client, admin_user, auth_headers, default_settings, sample_rule, session
    ):
        self._seed_inbox(session, [
            {
                "rule_id": sample_rule.id,
                "name": sample_rule.name,
                "rule_type": "geosite",
                "match_value": "category-telemetry",
                "missing_kind": "geosite",
                "missing_tag": "category-telemetry",
                "disabled_at": "2026-05-07T12:00:00+00:00",
            }
        ])

        resp = client.delete(
            f"/api/routing/auto-disabled/{sample_rule.id}",
            headers=auth_headers,
        )
        assert resp.status_code == 204

        # Rule gone from DB
        from sqlmodel import select
        result = session.exec(
            select(RoutingRule).where(RoutingRule.id == sample_rule.id)
        ).first()
        assert result is None

        # Inbox empty
        resp = client.get("/api/routing/auto-disabled", headers=auth_headers)
        assert resp.json() == {"items": []}

    def test_dismiss_all_clears_inbox_only(
        self, client, admin_user, auth_headers, default_settings, sample_rule, session
    ):
        # Pre-condition: rule is disabled, inbox has the entry
        sample_rule.enabled = False
        session.add(sample_rule)
        session.commit()
        self._seed_inbox(session, [
            {
                "rule_id": sample_rule.id,
                "name": sample_rule.name,
                "rule_type": "geosite",
                "match_value": "category-telemetry",
                "missing_kind": "geosite",
                "missing_tag": "category-telemetry",
                "disabled_at": "2026-05-07T12:00:00+00:00",
            }
        ])

        resp = client.post(
            "/api/routing/auto-disabled/dismiss",
            headers=auth_headers,
        )
        assert resp.status_code == 204

        # Inbox empty
        resp = client.get("/api/routing/auto-disabled", headers=auth_headers)
        assert resp.json() == {"items": []}

        # BUT the rule itself is still disabled — dismiss only clears
        # the banner, doesn't touch the underlying rules
        session.refresh(sample_rule)
        assert sample_rule.enabled is False

    def test_re_enable_unknown_rule_id_still_clears_inbox(
        self, client, admin_user, auth_headers, default_settings, session
    ):
        # If the rule was deleted out from under us, re-enable should
        # gracefully no-op on the rule side but still clean the inbox.
        self._seed_inbox(session, [
            {
                "rule_id": 99999,
                "name": "ghost",
                "rule_type": "geosite",
                "match_value": "category-x",
                "missing_kind": "geosite",
                "missing_tag": "category-x",
                "disabled_at": "2026-05-07T12:00:00+00:00",
            }
        ])
        resp = client.post(
            "/api/routing/auto-disabled/99999/re-enable",
            headers=auth_headers,
        )
        # Should still succeed (no rule to re-enable, but inbox cleared)
        assert resp.status_code == 204
        resp = client.get("/api/routing/auto-disabled", headers=auth_headers)
        assert resp.json() == {"items": []}


# ── /api/geodata/categories endpoint ─────────────────────────────────────────


class TestGeoCategoriesEndpoint:
    def test_returns_sorted_lists_from_cache(
        self, client, admin_user, auth_headers, monkeypatch
    ):
        from app.core import geo as geo_mod
        monkeypatch.setattr(geo_mod, "AVAILABLE_GEOSITE_TAGS", {"category-cn", "category-ads-all", "ru"})
        monkeypatch.setattr(geo_mod, "AVAILABLE_GEOIP_TAGS", {"us", "cn", "ru"})

        resp = client.get("/api/geodata/categories", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        # Sorted output
        assert body["geosite"] == ["category-ads-all", "category-cn", "ru"]
        assert body["geoip"] == ["cn", "ru", "us"]

    def test_empty_cache_returns_empty_arrays(
        self, client, admin_user, auth_headers, monkeypatch
    ):
        from app.core import geo as geo_mod
        monkeypatch.setattr(geo_mod, "AVAILABLE_GEOSITE_TAGS", set())
        monkeypatch.setattr(geo_mod, "AVAILABLE_GEOIP_TAGS", set())

        resp = client.get("/api/geodata/categories", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == {"geosite": [], "geoip": []}
