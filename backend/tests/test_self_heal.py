"""Tests for v1.2.7 config_gen self-heal flow:

  * `_parse_geo_heal_target` — recognises `code not found in *.dat`
    patterns and extracts the offending tag.
  * `_self_heal_disable_geo_rules` — given an offending tag, find every
    enabled `RoutingRule` referencing it, flip `enabled=False`, append
    a structured entry to `Settings.auto_disabled_rules`. Returns the
    list of affected rule ids.
  * `_regenerate_and_write` recursion bound — when `write_config`
    returns a heal target, the function disables matching rules and
    retries up to 5 times before giving up.

We don't run real `xray run -test` (requires the binary). Instead, we
monkey-patch `config_gen.write_config` to return canned heal targets
that drive the heal loop.

Async tests follow the established pattern from test_nodecircle:
`@pytest.mark.asyncio` on each test, use the `client + session`
fixtures (the `client` fixture patches `db_mod._async_engine`
globally), seed via the sync session, then call the async function
which constructs its own AsyncSession via `get_async_engine()`.
Refresh the sync session afterwards to observe the writes.
"""
from __future__ import annotations

import json

import pytest
from sqlmodel import select

from app.core.config_gen import _parse_geo_heal_target
from app.models import RoutingRule, Settings as DBSettings


# ── _parse_geo_heal_target ────────────────────────────────────────────────────


class TestParseGeoHealTarget:
    def test_geosite_pattern(self):
        stderr = (
            "infra/conf: failed to load geosite: CATEGORY-TELEMETRY > "
            "infra/conf: code not found in geosite.dat: CATEGORY-TELEMETRY"
        )
        assert _parse_geo_heal_target(stderr) == ("geosite", "category-telemetry")

    def test_geoip_pattern(self):
        stderr = "infra/conf: code not found in geoip.dat: ZZ"
        assert _parse_geo_heal_target(stderr) == ("geoip", "zz")

    def test_no_pattern_returns_none(self):
        assert _parse_geo_heal_target("Failed to start: some unrelated error") is None
        assert _parse_geo_heal_target("") is None

    def test_handles_extra_whitespace(self):
        stderr = "code not found in geosite.dat:    Some-Tag   "
        assert _parse_geo_heal_target(stderr) == ("geosite", "some-tag")


# ── _self_heal_disable_geo_rules ──────────────────────────────────────────────


async def _heal(kind: str, tag: str):
    """Helper: invoke `_self_heal_disable_geo_rules` against the live
    async engine (set up by the `client` fixture). Returns the list of
    disabled rule ids.
    """
    from app.api.system import _self_heal_disable_geo_rules
    from app.database import get_async_engine
    from sqlmodel.ext.asyncio.session import AsyncSession

    async with AsyncSession(get_async_engine()) as session:
        return await _self_heal_disable_geo_rules(session, kind, tag)


class TestSelfHealDisableRules:
    @pytest.mark.asyncio
    async def test_disables_matching_geosite_rules(
        self, client, admin_user, auth_headers, default_settings, session
    ):
        # Seed: 2 rules referencing the bad tag, 1 unrelated rule
        r1 = RoutingRule(
            name="r1", rule_type="geosite",
            match_value="category-telemetry",
            action="block", enabled=True, order=100,
        )
        r2 = RoutingRule(
            name="r2", rule_type="geosite",
            match_value="category-cn,category-telemetry",
            action="direct", enabled=True, order=101,
        )
        r3 = RoutingRule(
            name="r3", rule_type="geosite",
            match_value="category-cn",
            action="direct", enabled=True, order=102,
        )
        session.add_all([r1, r2, r3])
        session.commit()
        for r in (r1, r2, r3):
            session.refresh(r)

        disabled_ids = await _heal("geosite", "category-telemetry")

        # Verify r1 + r2 disabled, r3 untouched
        session.expire_all()
        for r in (r1, r2, r3):
            session.refresh(r)
        assert r1.enabled is False
        assert r2.enabled is False
        assert r3.enabled is True
        assert set(disabled_ids) == {r1.id, r2.id}

        # auto_disabled_rules Settings JSON populated with both
        row = session.exec(
            select(DBSettings).where(DBSettings.key == "auto_disabled_rules")
        ).first()
        assert row is not None
        data = json.loads(row.value)
        assert isinstance(data, list)
        assert len(data) == 2
        ids_in_inbox = {item["rule_id"] for item in data}
        assert ids_in_inbox == {r1.id, r2.id}
        for item in data:
            assert item["missing_kind"] == "geosite"
            assert item["missing_tag"] == "category-telemetry"
            assert item["disabled_at"]

    @pytest.mark.asyncio
    async def test_disables_inline_geosite_in_domain_rule(
        self, client, admin_user, auth_headers, default_settings, session
    ):
        r = RoutingRule(
            name="mixed", rule_type="domain",
            match_value="domain:google.com,geosite:category-telemetry",
            action="proxy", enabled=True, order=100,
        )
        session.add(r)
        session.commit()
        session.refresh(r)

        disabled_ids = await _heal("geosite", "category-telemetry")

        session.expire_all()
        session.refresh(r)
        assert r.enabled is False
        assert disabled_ids == [r.id]

    @pytest.mark.asyncio
    async def test_disables_geoip_rules(
        self, client, admin_user, auth_headers, default_settings, session
    ):
        r = RoutingRule(
            name="geoip-rule", rule_type="geoip",
            match_value="ZZ",
            action="block", enabled=True, order=100,
        )
        session.add(r)
        session.commit()
        session.refresh(r)

        disabled_ids = await _heal("geoip", "zz")

        session.expire_all()
        session.refresh(r)
        assert r.enabled is False
        assert disabled_ids == [r.id]

    @pytest.mark.asyncio
    async def test_does_not_touch_disabled_rules(
        self, client, admin_user, auth_headers, default_settings, session
    ):
        # An already-disabled rule shouldn't be touched (and shouldn't
        # appear in the inbox).
        r = RoutingRule(
            name="already-disabled", rule_type="geosite",
            match_value="category-telemetry",
            action="block", enabled=False, order=100,
        )
        session.add(r)
        session.commit()

        disabled_ids = await _heal("geosite", "category-telemetry")
        assert disabled_ids == []

    @pytest.mark.asyncio
    async def test_appends_to_existing_inbox(
        self, client, admin_user, auth_headers, default_settings, session
    ):
        # Repeated heals accumulate inbox entries.
        session.add(DBSettings(
            key="auto_disabled_rules",
            value=json.dumps([
                {"rule_id": 9999, "name": "old", "missing_tag": "old-tag"}
            ]),
        ))
        r = RoutingRule(
            name="new", rule_type="geosite",
            match_value="bad-tag",
            action="block", enabled=True, order=100,
        )
        session.add(r)
        session.commit()
        session.refresh(r)

        await _heal("geosite", "bad-tag")

        session.expire_all()
        row = session.exec(
            select(DBSettings).where(DBSettings.key == "auto_disabled_rules")
        ).first()
        data = json.loads(row.value)
        assert len(data) == 2
        ids = {item["rule_id"] for item in data}
        assert 9999 in ids
        assert r.id in ids

    @pytest.mark.asyncio
    async def test_handles_corrupt_inbox_gracefully(
        self, client, admin_user, auth_headers, default_settings, session
    ):
        session.add(DBSettings(key="auto_disabled_rules", value="not valid {{{"))
        r = RoutingRule(
            name="r", rule_type="geosite", match_value="x",
            action="block", enabled=True, order=100,
        )
        session.add(r)
        session.commit()
        session.refresh(r)

        disabled_ids = await _heal("geosite", "x")
        assert disabled_ids == [r.id]

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(
        self, client, admin_user, auth_headers, default_settings, session
    ):
        # If xray complained about a tag that isn't actually referenced
        # by any enabled rule (race / parser disagreement), we return
        # [] without modifying anything.
        disabled_ids = await _heal("geosite", "phantom-tag")
        assert disabled_ids == []

        row = session.exec(
            select(DBSettings).where(DBSettings.key == "auto_disabled_rules")
        ).first()
        # Either no row created, or empty value
        assert row is None or not row.value or row.value == "[]"


# ── _regenerate_and_write recursion bound ─────────────────────────────────────


async def _drive_regenerate():
    """Helper: call `_regenerate_and_write` against the live patched engine."""
    from app.api.system import _regenerate_and_write
    from app.database import get_async_engine
    from sqlmodel.ext.asyncio.session import AsyncSession

    async with AsyncSession(get_async_engine()) as session:
        await _regenerate_and_write(session)


class TestRegenerateAndWriteRecursion:
    @pytest.mark.asyncio
    async def test_stops_after_5_attempts(
        self, client, admin_user, auth_headers, default_settings, session, monkeypatch
    ):
        # Seed enough distinct rules so each iteration finds something
        # to disable, exhausting the recursion cap rather than running
        # out of rules.
        for i in range(10):
            session.add(RoutingRule(
                name=f"r{i}", rule_type="geosite",
                match_value=f"tag{i}",
                action="block", enabled=True, order=100 + i,
            ))
        session.commit()

        call_counter = {"count": 0}

        async def fake_write_config(config, *, validate=True):
            call_counter["count"] += 1
            return ("geosite", f"tag{call_counter['count'] - 1}")

        # `generate_config` and `write_config` are imported INSIDE
        # `_regenerate_and_write`, not at module level. Patch them at
        # the source module so the lazy import picks up the mocks.
        monkeypatch.setattr(
            "app.core.config_gen.generate_config", lambda *a, **kw: {"stub": True}
        )
        monkeypatch.setattr("app.core.config_gen.write_config", fake_write_config)

        await _drive_regenerate()

        # Initial call + 5 retries = 6 max
        assert call_counter["count"] <= 6
        assert call_counter["count"] >= 2

    @pytest.mark.asyncio
    async def test_stops_when_no_rules_match_target(
        self, client, admin_user, auth_headers, default_settings, session, monkeypatch
    ):
        # write_config reports a tag, but no enabled rule references it.
        # `_self_heal_disable_geo_rules` returns [], heal loop bails.
        call_counter = {"count": 0}

        async def fake_write_config(config, *, validate=True):
            call_counter["count"] += 1
            return ("geosite", "phantom-tag-no-rule-references-this")

        # `generate_config` and `write_config` are imported INSIDE
        # `_regenerate_and_write`, not at module level. Patch them at
        # the source module so the lazy import picks up the mocks.
        monkeypatch.setattr(
            "app.core.config_gen.generate_config", lambda *a, **kw: {"stub": True}
        )
        monkeypatch.setattr("app.core.config_gen.write_config", fake_write_config)

        await _drive_regenerate()

        # Single call — no rules to disable, so no retry
        assert call_counter["count"] == 1

    @pytest.mark.asyncio
    async def test_returns_cleanly_when_validation_passes(
        self, client, admin_user, auth_headers, default_settings, session, monkeypatch
    ):
        # write_config returns None on a clean validation — function
        # returns immediately, no heal logic.
        call_counter = {"count": 0}

        async def fake_write_config(config, *, validate=True):
            call_counter["count"] += 1
            return None

        # `generate_config` and `write_config` are imported INSIDE
        # `_regenerate_and_write`, not at module level. Patch them at
        # the source module so the lazy import picks up the mocks.
        monkeypatch.setattr(
            "app.core.config_gen.generate_config", lambda *a, **kw: {"stub": True}
        )
        monkeypatch.setattr("app.core.config_gen.write_config", fake_write_config)

        await _drive_regenerate()
        assert call_counter["count"] == 1

    @pytest.mark.asyncio
    async def test_tolerates_garbage_return_value(
        self, client, admin_user, auth_headers, default_settings, session, monkeypatch
    ):
        # If a future write_config or test mock returns an unexpected
        # type (not None, not a 2-tuple of strings), bail out instead
        # of crashing.
        async def fake_write_config(config, *, validate=True):
            return "this is a plain string, not a tuple"

        # `generate_config` and `write_config` are imported INSIDE
        # `_regenerate_and_write`, not at module level. Patch them at
        # the source module so the lazy import picks up the mocks.
        monkeypatch.setattr(
            "app.core.config_gen.generate_config", lambda *a, **kw: {"stub": True}
        )
        monkeypatch.setattr("app.core.config_gen.write_config", fake_write_config)

        # Should NOT raise
        await _drive_regenerate()
