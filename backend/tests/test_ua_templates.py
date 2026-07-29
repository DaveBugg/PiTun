"""Tests for the User-Agent template catalogue.

Three layers are covered:

* `/api/user-agents` CRUD + export/import — including the two guards that
  keep a subscription from silently changing fingerprint (key rename
  re-points its subscriptions; delete refuses while in use).
* `core.ua_templates` validation — the header rules exist because httpx
  0.28 forwards a CR/LF inside a header value unchanged (request
  splitting) and raises on non-ASCII. Both are asserted directly.
* `build_subscription_headers` — the precedence chain and merge order
  that decide what actually goes on the wire, plus an end-to-end check
  that `_fetch_subscription_unlocked` sends the template's headers.
"""
import asyncio
import json
from unittest import mock
from unittest.mock import AsyncMock

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(engine, coro_factory):
    """Run a coroutine that needs an AsyncSession on the test engine."""
    _, async_engine = engine

    async def _inner():
        async with AsyncSession(async_engine) as session:
            return await coro_factory(session)

    return asyncio.run(_inner())


def _make_sub(session, **kwargs):
    from app.models import Subscription

    sub = Subscription(**{
        "name": "Sub",
        "url": "https://example.com/sub",
        "enabled": True,
        "ua": "clash",
        **kwargs,
    })
    session.add(sub)
    session.commit()
    session.refresh(sub)
    return sub


VALID_BODY = {
    "key": "my-panel",
    "name": "My Panel",
    "user_agent": "MyPanel/1.0",
    "headers": {"X-Token": "abc"},
    "description": "notes",
    "order": 15,
}


# ── CRUD ──────────────────────────────────────────────────────────────────────

class TestUaTemplateCrud:
    def test_list_empty(self, client, admin_user, auth_headers):
        resp = client.get("/api/user-agents", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_orders_by_order_then_id(
        self, client, admin_user, auth_headers, seeded_ua_templates
    ):
        resp = client.get("/api/user-agents", headers=auth_headers)
        assert resp.status_code == 200
        keys = [t["key"] for t in resp.json()]
        assert keys == [
            "v2ray", "clash", "sing-box", "happ", "happ-android",
            "happ-windows", "happ-macos", "streisand", "chrome",
        ]
        assert all(t["builtin"] is True for t in resp.json())

    def test_create(self, client, admin_user, auth_headers):
        resp = client.post("/api/user-agents", json=VALID_BODY, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["key"] == "my-panel"
        assert data["user_agent"] == "MyPanel/1.0"
        assert data["headers"] == {"X-Token": "abc"}
        # Operator-created templates are never marked builtin.
        assert data["builtin"] is False
        assert data["usage_count"] == 0

    def test_create_lowercases_and_trims_key(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/user-agents",
            json={**VALID_BODY, "key": "  MiXeD-Case  "},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["key"] == "mixed-case"

    def test_create_duplicate_key_rejected(self, client, admin_user, auth_headers):
        client.post("/api/user-agents", json=VALID_BODY, headers=auth_headers)
        resp = client.post("/api/user-agents", json=VALID_BODY, headers=auth_headers)
        assert resp.status_code == 400
        assert "already exists" in resp.json()["detail"]

    def test_get_one(self, client, admin_user, auth_headers, ua_template):
        resp = client.get(f"/api/user-agents/{ua_template.id}", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["key"] == "panel-x"
        assert resp.json()["headers"]["X-Api-Key"] == "secret-token"

    def test_get_not_found(self, client, admin_user, auth_headers):
        assert client.get("/api/user-agents/9999", headers=auth_headers).status_code == 404

    def test_patch_partial(self, client, admin_user, auth_headers, ua_template):
        resp = client.patch(
            f"/api/user-agents/{ua_template.id}",
            json={"name": "Renamed", "user_agent": "PanelX/9.9.9"},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["name"] == "Renamed"
        assert data["user_agent"] == "PanelX/9.9.9"
        # Untouched fields survive the PATCH.
        assert data["key"] == "panel-x"
        assert data["headers"]["X-Api-Key"] == "secret-token"

    def test_patch_replaces_headers_wholesale(
        self, client, admin_user, auth_headers, ua_template
    ):
        resp = client.patch(
            f"/api/user-agents/{ua_template.id}",
            json={"headers": {"X-Other": "1"}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["headers"] == {"X-Other": "1"}

    def test_patch_can_clear_headers(self, client, admin_user, auth_headers, ua_template):
        resp = client.patch(
            f"/api/user-agents/{ua_template.id}",
            json={"headers": {}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["headers"] == {}

    def test_patch_builtin_is_allowed(
        self, client, admin_user, auth_headers, seeded_ua_templates
    ):
        """The seeded rows are ordinary editable rows — that is the point
        of moving them out of code and into the initial migration."""
        happ = next(t for t in seeded_ua_templates if t.key == "happ")
        resp = client.patch(
            f"/api/user-agents/{happ.id}",
            json={"user_agent": "Happ/9.9.9/ios/18.0/iPhone17,1"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["user_agent"] == "Happ/9.9.9/ios/18.0/iPhone17,1"
        assert resp.json()["builtin"] is True

    def test_patch_not_found(self, client, admin_user, auth_headers):
        resp = client.patch("/api/user-agents/9999", json={"name": "x"}, headers=auth_headers)
        assert resp.status_code == 404

    def test_delete(self, client, admin_user, auth_headers, ua_template):
        resp = client.delete(f"/api/user-agents/{ua_template.id}", headers=auth_headers)
        assert resp.status_code == 204
        assert client.get(
            f"/api/user-agents/{ua_template.id}", headers=auth_headers
        ).status_code == 404

    def test_delete_builtin_is_allowed(
        self, client, admin_user, auth_headers, seeded_ua_templates
    ):
        chrome = next(t for t in seeded_ua_templates if t.key == "chrome")
        resp = client.delete(f"/api/user-agents/{chrome.id}", headers=auth_headers)
        assert resp.status_code == 204

    def test_delete_not_found(self, client, admin_user, auth_headers):
        assert client.delete("/api/user-agents/9999", headers=auth_headers).status_code == 404

    def test_usage_count_reflects_subscriptions(
        self, client, admin_user, auth_headers, session, ua_template
    ):
        _make_sub(session, name="A", ua="panel-x")
        _make_sub(session, name="B", ua="panel-x")
        _make_sub(session, name="C", ua="clash")
        resp = client.get(f"/api/user-agents/{ua_template.id}", headers=auth_headers)
        assert resp.json()["usage_count"] == 2

    def test_requires_auth(self, client, admin_user):
        assert client.get("/api/user-agents").status_code in (401, 403)


# ── Validation ────────────────────────────────────────────────────────────────

class TestUaTemplateValidation:
    @pytest.mark.parametrize("bad_key", [
        "",              # empty
        "   ",           # whitespace only
        "-leading-dash",  # must start alnum
        "has space",
        "has/slash",
        "UPPER ok but not this!",
        "a" * 65,        # too long
    ])
    def test_invalid_key_rejected(self, client, admin_user, auth_headers, bad_key):
        resp = client.post(
            "/api/user-agents", json={**VALID_BODY, "key": bad_key}, headers=auth_headers
        )
        assert resp.status_code == 422, f"{bad_key!r} was accepted"

    def test_empty_name_rejected(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/user-agents", json={**VALID_BODY, "name": "  "}, headers=auth_headers
        )
        assert resp.status_code == 422

    def test_empty_user_agent_rejected(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/user-agents", json={**VALID_BODY, "user_agent": ""}, headers=auth_headers
        )
        assert resp.status_code == 422

    @pytest.mark.parametrize("payload", ["ua\rinjected", "ua\ninjected", "ua\x00null"])
    def test_crlf_in_user_agent_rejected(self, client, admin_user, auth_headers, payload):
        """httpx 0.28 does NOT strip CR/LF from header values — it would
        forward `a\\r\\nX-Admin: 1` as a smuggled extra header. Reject at
        the API boundary (CWE-93)."""
        resp = client.post(
            "/api/user-agents", json={**VALID_BODY, "user_agent": payload},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_non_ascii_user_agent_rejected(self, client, admin_user, auth_headers):
        """`httpx.Headers` encodes str values as ASCII and raises on
        anything else. Better a 422 now than an opaque `last_error` on the
        subscription hours later."""
        resp = client.post(
            "/api/user-agents", json={**VALID_BODY, "user_agent": "Панель/1.0"},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.parametrize("value", ["a\r\nX-Admin: 1", "b\nc", "привет"])
    def test_bad_header_value_rejected(self, client, admin_user, auth_headers, value):
        resp = client.post(
            "/api/user-agents", json={**VALID_BODY, "headers": {"X-Bad": value}},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.parametrize("name", ["X Bad", "X:Bad", "", "   ", "X\nBad"])
    def test_bad_header_name_rejected(self, client, admin_user, auth_headers, name):
        resp = client.post(
            "/api/user-agents", json={**VALID_BODY, "headers": {name: "v"}},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    @pytest.mark.parametrize("name", [
        "User-Agent", "user-agent", "Host", "Content-Length",
        "Transfer-Encoding", "Connection",
    ])
    def test_forbidden_header_names_rejected(self, client, admin_user, auth_headers, name):
        """`User-Agent` has its own field; the rest are owned by the
        transport and overriding them breaks the request."""
        resp = client.post(
            "/api/user-agents", json={**VALID_BODY, "headers": {name: "v"}},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_case_differing_duplicate_headers_rejected(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/user-agents",
            json={**VALID_BODY, "headers": {"X-Dup": "1", "x-dup": "2"}},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_too_many_headers_rejected(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/user-agents",
            json={**VALID_BODY, "headers": {f"X-H{i}": "v" for i in range(33)}},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    def test_empty_header_value_is_legal(self, client, admin_user, auth_headers):
        """An empty value is the documented way to DROP a base header
        (some panels choke on `Accept-Encoding: gzip`)."""
        resp = client.post(
            "/api/user-agents",
            json={**VALID_BODY, "headers": {"Accept-Encoding": ""}},
            headers=auth_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["headers"] == {"Accept-Encoding": ""}

    def test_patch_validates_too(self, client, admin_user, auth_headers, ua_template):
        resp = client.patch(
            f"/api/user-agents/{ua_template.id}",
            json={"user_agent": "bad\r\nX-Admin: 1"},
            headers=auth_headers,
        )
        assert resp.status_code == 422


# ── Key rename + delete guards ────────────────────────────────────────────────

class TestUaTemplateKeyRename:
    def test_rename_repoints_subscriptions(
        self, client, admin_user, auth_headers, session, ua_template
    ):
        """`Subscription.ua` holds the key, not the id. Renaming without
        migrating would orphan the subscription — it would silently fall
        back to the built-in UA map and present a different fingerprint
        on the next refresh."""
        sub = _make_sub(session, ua="panel-x")
        resp = client.patch(
            f"/api/user-agents/{ua_template.id}",
            json={"key": "panel-y"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["key"] == "panel-y"
        assert resp.json()["usage_count"] == 1

        session.expire_all()
        from app.models import Subscription
        assert session.get(Subscription, sub.id).ua == "panel-y"

    def test_rename_does_not_touch_other_subscriptions(
        self, client, admin_user, auth_headers, session, ua_template
    ):
        other = _make_sub(session, name="Other", ua="clash")
        client.patch(
            f"/api/user-agents/{ua_template.id}",
            json={"key": "panel-y"},
            headers=auth_headers,
        )
        session.expire_all()
        from app.models import Subscription
        assert session.get(Subscription, other.id).ua == "clash"

    def test_rename_to_taken_key_rejected(
        self, client, admin_user, auth_headers, ua_template
    ):
        client.post("/api/user-agents", json=VALID_BODY, headers=auth_headers)
        resp = client.patch(
            f"/api/user-agents/{ua_template.id}",
            json={"key": "my-panel"},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_patching_same_key_is_a_noop(
        self, client, admin_user, auth_headers, ua_template
    ):
        """Sending the unchanged key must not trip the uniqueness check
        against the row's own record."""
        resp = client.patch(
            f"/api/user-agents/{ua_template.id}",
            json={"key": "panel-x", "name": "Still Panel X"},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["key"] == "panel-x"


class TestUaTemplateDeleteGuard:
    def test_delete_in_use_returns_409(
        self, client, admin_user, auth_headers, session, ua_template
    ):
        _make_sub(session, name="Live Sub", ua="panel-x")
        resp = client.delete(f"/api/user-agents/{ua_template.id}", headers=auth_headers)
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "Live Sub" in detail
        assert "force=true" in detail

    def test_delete_in_use_with_force(
        self, client, admin_user, auth_headers, session, ua_template
    ):
        sub = _make_sub(session, ua="panel-x")
        resp = client.delete(
            f"/api/user-agents/{ua_template.id}?force=true", headers=auth_headers
        )
        assert resp.status_code == 204
        # The subscription keeps its now-dangling key rather than being
        # rewritten — `build_subscription_headers` has a fallback for it.
        session.expire_all()
        from app.models import Subscription
        assert session.get(Subscription, sub.id).ua == "panel-x"


# ── Export / import ───────────────────────────────────────────────────────────

class TestUaTemplateExportImport:
    def test_export_envelope(self, client, admin_user, auth_headers, ua_template):
        resp = client.get("/api/user-agents/export-json", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["kind"] == "pitun-ua-templates-export"
        assert body["version"] == 1
        assert body["count"] == 1
        assert "attachment" in resp.headers["content-disposition"]
        assert body["templates"][0]["key"] == "panel-x"
        assert body["templates"][0]["headers"]["X-Api-Key"] == "secret-token"
        # `id`/`builtin` are deliberately absent — a bundle describes
        # templates, not this install's row identities.
        assert "id" not in body["templates"][0]

    def test_export_path_is_not_read_as_an_id(self, client, admin_user, auth_headers):
        """`/export-json` must be matched before `/{tpl_id}` — otherwise
        FastAPI reads it as `tpl_id="export-json"` and 422s."""
        resp = client.get("/api/user-agents/export-json", headers=auth_headers)
        assert resp.status_code == 200

    def test_round_trip(self, client, admin_user, auth_headers, ua_template):
        bundle = client.get("/api/user-agents/export-json", headers=auth_headers).json()
        client.delete(f"/api/user-agents/{ua_template.id}", headers=auth_headers)

        resp = client.post("/api/user-agents/import-json", json=bundle, headers=auth_headers)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"imported": 1, "updated": 0, "skipped": 0, "errors": []}

        restored = client.get("/api/user-agents", headers=auth_headers).json()
        assert len(restored) == 1
        assert restored[0]["key"] == "panel-x"
        assert restored[0]["user_agent"] == "PanelX/1.2.3"
        assert restored[0]["headers"] == {
            "X-Api-Key": "secret-token", "Referer": "https://panel.example",
        }

    def test_import_skips_existing_key_by_default(
        self, client, admin_user, auth_headers, ua_template
    ):
        bundle = client.get("/api/user-agents/export-json", headers=auth_headers).json()
        bundle["templates"][0]["user_agent"] = "PanelX/OTHER"

        resp = client.post("/api/user-agents/import-json", json=bundle, headers=auth_headers)
        assert resp.json()["skipped"] == 1
        assert resp.json()["imported"] == 0
        rows = client.get("/api/user-agents", headers=auth_headers).json()
        assert rows[0]["user_agent"] == "PanelX/1.2.3"

    def test_import_overwrite_updates_in_place(
        self, client, admin_user, auth_headers, ua_template
    ):
        """`overwrite=true` keeps the row id, so subscriptions pointing at
        the key are untouched."""
        bundle = client.get("/api/user-agents/export-json", headers=auth_headers).json()
        bundle["templates"][0]["user_agent"] = "PanelX/2.0.0"

        resp = client.post(
            "/api/user-agents/import-json?overwrite=true", json=bundle, headers=auth_headers
        )
        assert resp.json()["updated"] == 1
        assert resp.json()["imported"] == 0
        rows = client.get("/api/user-agents", headers=auth_headers).json()
        assert len(rows) == 1
        assert rows[0]["id"] == ua_template.id
        assert rows[0]["user_agent"] == "PanelX/2.0.0"

    def test_import_replace_wipes_first(
        self, client, admin_user, auth_headers, ua_template
    ):
        bundle = {
            "kind": "pitun-ua-templates-export",
            "version": 1,
            "templates": [
                {"key": "only-one", "name": "Only", "user_agent": "Only/1.0"},
            ],
        }
        resp = client.post(
            "/api/user-agents/import-json?replace=true", json=bundle, headers=auth_headers
        )
        assert resp.json()["imported"] == 1
        rows = client.get("/api/user-agents", headers=auth_headers).json()
        assert [r["key"] for r in rows] == ["only-one"]

    def test_import_marks_rows_as_not_builtin(self, client, admin_user, auth_headers):
        bundle = {
            "kind": "pitun-ua-templates-export",
            "version": 1,
            "templates": [{"key": "v2ray", "name": "v2rayN", "user_agent": "v2rayN/6.60"}],
        }
        client.post("/api/user-agents/import-json", json=bundle, headers=auth_headers)
        rows = client.get("/api/user-agents", headers=auth_headers).json()
        assert rows[0]["builtin"] is False

    def test_import_rejects_wrong_kind(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/user-agents/import-json",
            json={"kind": "pitun-nodes-export", "version": 1, "templates": []},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "kind mismatch" in resp.json()["detail"]

    def test_import_rejects_wrong_version(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/user-agents/import-json",
            json={"kind": "pitun-ua-templates-export", "version": 99, "templates": []},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_import_rejects_missing_array(self, client, admin_user, auth_headers):
        resp = client.post(
            "/api/user-agents/import-json",
            json={"kind": "pitun-ua-templates-export", "version": 1},
            headers=auth_headers,
        )
        assert resp.status_code == 400

    def test_import_bad_row_does_not_abort_the_batch(self, client, admin_user, auth_headers):
        bundle = {
            "kind": "pitun-ua-templates-export",
            "version": 1,
            "templates": [
                {"key": "good-one", "name": "Good", "user_agent": "Good/1.0"},
                {"key": "bad key!", "name": "Bad", "user_agent": "Bad/1.0"},
                {"key": "crlf", "name": "CRLF", "user_agent": "x\r\nX-Admin: 1"},
                {"key": "good-two", "name": "Good2", "user_agent": "Good2/1.0"},
            ],
        }
        resp = client.post("/api/user-agents/import-json", json=bundle, headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["imported"] == 2
        assert len(data["errors"]) == 2
        keys = [r["key"] for r in client.get("/api/user-agents", headers=auth_headers).json()]
        assert sorted(keys) == ["good-one", "good-two"]

    def test_import_error_message_never_echoes_the_value(
        self, client, admin_user, auth_headers
    ):
        """Header values can hold panel API keys. Per-row errors report
        the exception type only — same envelope as nodes' import-json."""
        bundle = {
            "kind": "pitun-ua-templates-export",
            "version": 1,
            "templates": [{
                "key": "leaky",
                "name": "Leaky",
                "user_agent": "Leaky/1.0",
                "headers": {"X-Api-Key": "s3cr3t\r\ninjected"},
            }],
        }
        resp = client.post("/api/user-agents/import-json", json=bundle, headers=auth_headers)
        assert resp.json()["imported"] == 0
        joined = " ".join(resp.json()["errors"])
        assert "s3cr3t" not in joined
        assert "leaky" in joined

    def test_import_duplicate_key_within_bundle(self, client, admin_user, auth_headers):
        bundle = {
            "kind": "pitun-ua-templates-export",
            "version": 1,
            "templates": [
                {"key": "dup", "name": "One", "user_agent": "One/1.0"},
                {"key": "dup", "name": "Two", "user_agent": "Two/1.0"},
            ],
        }
        resp = client.post("/api/user-agents/import-json", json=bundle, headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["imported"] == 1
        assert len(resp.json()["errors"]) == 1

    def test_import_unknown_fields_ignored(self, client, admin_user, auth_headers):
        """Forward-compat: a newer export with extra columns still
        imports into an older PiTun."""
        bundle = {
            "kind": "pitun-ua-templates-export",
            "version": 1,
            "templates": [{
                "key": "fwd", "name": "Fwd", "user_agent": "Fwd/1.0",
                "some_future_column": {"nested": True}, "id": 12345, "builtin": True,
            }],
        }
        resp = client.post("/api/user-agents/import-json", json=bundle, headers=auth_headers)
        assert resp.json()["imported"] == 1
        row = client.get("/api/user-agents", headers=auth_headers).json()[0]
        assert row["key"] == "fwd"
        assert row["id"] != 12345
        assert row["builtin"] is False


# ── Seeding ───────────────────────────────────────────────────────────────────

class TestDefaultSeeding:
    def test_seeds_into_empty_table(self, engine):
        from app.core.ua_templates import (
            DEFAULT_UA_TEMPLATES, ensure_default_ua_templates,
        )

        inserted = _run(engine, ensure_default_ua_templates)
        assert inserted == len(DEFAULT_UA_TEMPLATES) == 9

    def test_is_a_noop_when_rows_exist(self, engine, ua_template):
        """Crucially NOT an upsert: re-seeding on every boot would
        resurrect a template the operator deleted and revert one they
        edited."""
        from app.core.ua_templates import ensure_default_ua_templates

        assert _run(engine, ensure_default_ua_templates) == 0

    def test_seeded_uas_match_the_pre_migration_hardcoded_map(self, engine, session):
        """The seed exists to make the v1.4.6 → v1.4.7 upgrade a no-op on
        the wire. If these ever diverge, every existing subscription
        silently changes fingerprint."""
        from app.core.ua_templates import BUILTIN_UA_MAP, ensure_default_ua_templates
        from app.models import UserAgentTemplate
        from sqlmodel import select as sm_select

        _run(engine, ensure_default_ua_templates)
        session.expire_all()
        rows = session.exec(sm_select(UserAgentTemplate)).all()
        assert {r.key for r in rows} == set(BUILTIN_UA_MAP)
        for row in rows:
            assert row.user_agent == BUILTIN_UA_MAP[row.key], row.key

    def test_upgrade_from_1_4_6_does_not_change_any_subscription_fingerprint(
        self, engine, session
    ):
        """THE upgrade-safety test.

        v1.4.6 resolved a subscription's headers from two hardcoded dicts.
        v1.4.7 resolves them from seeded DB rows. For every preset key an
        existing subscription can be holding, the resulting header dict
        must be byte-identical — otherwise upgrading silently re-fingerprints
        live subscriptions and panels start serving dummies.

        The v1.4.6 logic is reproduced inline rather than imported, so this
        keeps testing the OLD behaviour even after the old code is gone.
        """
        from app.core.ua_templates import (
            BUILTIN_UA_MAP, HAPP_PROFILES, ensure_default_ua_templates,
            get_happ_headers,
        )
        from app.core.ua_templates import build_subscription_headers

        _run(engine, ensure_default_ua_templates)

        def v146_headers(sub) -> dict:
            """Verbatim transcription of `_fetch_subscription_unlocked` @ v1.4.6."""
            custom = (sub.custom_ua or "").strip()
            ua = custom or BUILTIN_UA_MAP.get(sub.ua, BUILTIN_UA_MAP["v2ray"])
            headers = {
                "User-Agent": ua,
                "Accept": "*/*",
                "Accept-Language": "ru-RU,en,*",
                "Accept-Encoding": "gzip, deflate",
            }
            rotate = bool(getattr(sub, "rotate_hwid", False))
            if sub.ua in HAPP_PROFILES:
                headers.update(get_happ_headers(sub.ua, rotate_hwid=rotate))
            elif ua.lower().startswith("happ/"):
                headers.update(get_happ_headers("happ", rotate_hwid=rotate))
            return headers

        for i, key in enumerate(BUILTIN_UA_MAP):
            sub = _make_sub(session, name=f"sub-{i}", ua=key)
            before = v146_headers(sub)
            after = _run(
                engine, lambda s, _sub=sub: build_subscription_headers(s, _sub)
            )
            assert after == before, f"header set changed for ua={key!r}"

        # Same for the custom-UA escape hatch and a Happ-shaped custom UA.
        for i, (key, custom) in enumerate([
            ("clash", "Weird/1.0"),
            ("v2ray", "Happ/2.7.0/ios/17.4/iPhone15,2"),
        ]):
            sub = _make_sub(session, name=f"custom-{i}", ua=key, custom_ua=custom)
            after = _run(
                engine, lambda s, _sub=sub: build_subscription_headers(s, _sub)
            )
            assert after == v146_headers(sub), f"changed for custom_ua={custom!r}"


class TestMigrationSeedData:
    """The seeding migration inlines its rows instead of importing app code.

    That is deliberate — a migration is a historical snapshot, and the two
    bind-mounts (`./backend/app`, `./backend/alembic`) can be updated out
    of step during a hot deploy. The cost is a second copy of the data,
    so pin the two together here: drift becomes a failing test rather
    than a silent difference between a fresh install and an upgraded one.
    """

    @staticmethod
    def _migration_module():
        import importlib.util
        from pathlib import Path

        path = (
            Path(__file__).resolve().parent.parent
            / "alembic" / "versions" / "018_add_useragent_templates.py"
        )
        spec = importlib.util.spec_from_file_location("_mig018", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_migration_seed_matches_default_templates(self):
        from app.core.ua_templates import DEFAULT_UA_TEMPLATES, dump_headers

        expected = [
            {
                "key": spec["key"],
                "name": spec["name"],
                "user_agent": spec["user_agent"],
                "headers": dump_headers(spec.get("headers")),
                "description": spec.get("description"),
                "builtin": True,
                "order": spec.get("order", 100),
            }
            for spec in DEFAULT_UA_TEMPLATES
        ]
        actual = self._migration_module().SEED_ROWS
        assert [dict(sorted(r.items())) for r in actual] == \
               [dict(sorted(r.items())) for r in expected]

    def test_migration_does_not_import_app_code(self):
        """A `from app...` here would crash-loop the container on a deploy
        that updates `alembic/` before `app/` (MIGRATION_STRICT=1)."""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parent.parent
            / "alembic" / "versions" / "018_add_useragent_templates.py"
        ).read_text(encoding="utf-8")
        offenders = [
            line.strip() for line in source.splitlines()
            if line.strip().startswith(("from app.", "import app."))
        ]
        assert offenders == [], f"migration 018 imports app code: {offenders}"


class TestDefaultSeedingExtra:
    def test_seed_rows_are_valid_by_the_api_schema(self):
        """Every seeded row must survive the same validation an operator
        edit goes through — otherwise a built-in becomes un-saveable."""
        from app.core.ua_templates import DEFAULT_UA_TEMPLATES
        from app.schemas import UserAgentTemplateCreate

        for spec in DEFAULT_UA_TEMPLATES:
            UserAgentTemplateCreate(**spec)


# ── Header resolution ─────────────────────────────────────────────────────────

class TestBuildSubscriptionHeaders:
    def _headers(self, engine, sub):
        from app.core.ua_templates import build_subscription_headers
        return _run(engine, lambda s: build_subscription_headers(s, sub))

    def test_uses_template_user_agent(self, engine, session, ua_template):
        sub = _make_sub(session, ua="panel-x")
        headers = self._headers(engine, sub)
        assert headers["User-Agent"] == "PanelX/1.2.3"

    def test_merges_template_headers(self, engine, session, ua_template):
        sub = _make_sub(session, ua="panel-x")
        headers = self._headers(engine, sub)
        assert headers["X-Api-Key"] == "secret-token"
        assert headers["Referer"] == "https://panel.example"
        # Base headers survive alongside.
        assert headers["Accept"] == "*/*"
        assert headers["Accept-Encoding"] == "gzip, deflate"

    def test_custom_ua_beats_template(self, engine, session, ua_template):
        sub = _make_sub(session, ua="panel-x", custom_ua="Override/1.0")
        headers = self._headers(engine, sub)
        assert headers["User-Agent"] == "Override/1.0"
        # …but the template's extra headers still apply.
        assert headers["X-Api-Key"] == "secret-token"

    def test_blank_custom_ua_falls_through(self, engine, session, ua_template):
        sub = _make_sub(session, ua="panel-x", custom_ua="   ")
        assert self._headers(engine, sub)["User-Agent"] == "PanelX/1.2.3"

    def test_unknown_key_falls_back_to_builtin_map(self, engine, session):
        """No row for the key — a deleted template, or a DB built by
        `create_all` before the seeding migration ran."""
        from app.core.ua_templates import BUILTIN_UA_MAP

        sub = _make_sub(session, ua="clash")
        assert self._headers(engine, sub)["User-Agent"] == BUILTIN_UA_MAP["clash"]

    def test_unknown_key_with_no_builtin_falls_back_to_v2ray(self, engine, session):
        from app.core.ua_templates import BUILTIN_UA_MAP

        sub = _make_sub(session, ua="never-existed")
        assert self._headers(engine, sub)["User-Agent"] == BUILTIN_UA_MAP["v2ray"]

    def test_empty_ua_key_falls_back_to_v2ray(self, engine, session):
        from app.core.ua_templates import BUILTIN_UA_MAP

        sub = _make_sub(session, ua="")
        assert self._headers(engine, sub)["User-Agent"] == BUILTIN_UA_MAP["v2ray"]

    @pytest.mark.parametrize("key,os_name", [
        ("happ", "iOS"), ("happ-android", "Android"),
        ("happ-windows", "Windows"), ("happ-macos", "macOS"),
    ])
    def test_happ_profile_injects_matching_x_headers(
        self, engine, session, seeded_ua_templates, key, os_name
    ):
        """Stricter panels cross-validate the UA's OS segment against
        `X-Device-Os`. A mismatch flips their fingerprint check."""
        sub = _make_sub(session, ua=key)
        headers = self._headers(engine, sub)
        assert headers["X-Device-Os"] == os_name
        assert f"/{os_name.lower()}/" in headers["User-Agent"]
        assert headers["X-Hwid"]
        assert headers["X-App-Version"]

    def test_non_happ_template_gets_no_x_headers(self, engine, session, ua_template):
        sub = _make_sub(session, ua="panel-x")
        headers = self._headers(engine, sub)
        assert "X-Hwid" not in headers
        assert "X-Device-Os" not in headers

    def test_happ_shaped_custom_ua_triggers_injection(self, engine, session):
        """A pasted Happ UA on a non-happ key still needs the bundle,
        otherwise the panel serves placeholder nodes."""
        sub = _make_sub(session, ua="clash", custom_ua="Happ/2.7.0/ios/17.4/iPhone15,2")
        assert "X-Hwid" in self._headers(engine, sub)

    def test_hwid_is_stable_without_rotation(self, engine, session, seeded_ua_templates):
        """Most panels device-bind on first-seen HWID — a value that
        changed per refresh would silently break the subscription."""
        sub = _make_sub(session, ua="happ", rotate_hwid=False)
        assert self._headers(engine, sub)["X-Hwid"] == self._headers(engine, sub)["X-Hwid"]

    def test_rotate_hwid_yields_a_fresh_value(self, engine, session, seeded_ua_templates):
        sub = _make_sub(session, ua="happ", rotate_hwid=True)
        assert self._headers(engine, sub)["X-Hwid"] != self._headers(engine, sub)["X-Hwid"]

    def test_hwid_differs_per_profile(self, engine, session, seeded_ua_templates):
        """A real iOS and Android Happ install would never share an HWID."""
        ios = _make_sub(session, name="ios", ua="happ")
        android = _make_sub(session, name="android", ua="happ-android")
        assert self._headers(engine, ios)["X-Hwid"] != self._headers(engine, android)["X-Hwid"]

    def test_template_header_overrides_a_base_header(self, engine, session, session_add_tpl):
        tpl = session_add_tpl(key="k1", headers={"Accept": "application/json"})
        sub = _make_sub(session, ua=tpl.key)
        assert self._headers(engine, sub)["Accept"] == "application/json"

    def test_override_is_case_insensitive_and_does_not_duplicate(
        self, engine, session, session_add_tpl
    ):
        """HTTP header names are case-insensitive but a dict is not — a
        naive merge would send Accept-Encoding twice with conflicting
        values."""
        tpl = session_add_tpl(key="k2", headers={"accept-encoding": "identity"})
        sub = _make_sub(session, ua=tpl.key)
        headers = self._headers(engine, sub)
        matching = [k for k in headers if k.lower() == "accept-encoding"]
        assert matching == ["accept-encoding"]
        assert headers[matching[0]] == "identity"

    def test_empty_override_value_drops_the_header(self, engine, session, session_add_tpl):
        """The documented escape hatch for panels that mis-handle gzip and
        return a truncated body."""
        tpl = session_add_tpl(key="k3", headers={"Accept-Encoding": ""})
        sub = _make_sub(session, ua=tpl.key)
        headers = self._headers(engine, sub)
        assert not [k for k in headers if k.lower() == "accept-encoding"]

    def test_template_header_can_override_a_happ_x_header(
        self, engine, session, session_add_tpl
    ):
        """Template headers are applied last on purpose — they are the
        operator's explicit instruction."""
        session_add_tpl(
            key="happ", user_agent="Happ/2.7.0/ios/17.4/iPhone15,2",
            headers={"X-Device-Locale": "EN"},
        )
        sub = _make_sub(session, ua="happ")
        assert self._headers(engine, sub)["X-Device-Locale"] == "EN"

    def test_malformed_headers_json_is_ignored(self, engine, session, session_add_tpl):
        """A hand-edited or half-written blob must not break every
        refresh of every subscription using the template."""
        tpl = session_add_tpl(key="k4")
        tpl.headers = "{not json at all"
        session.add(tpl)
        session.commit()

        sub = _make_sub(session, ua="k4")
        headers = self._headers(engine, sub)
        assert headers["User-Agent"] == "Test/1.0"
        assert headers["Accept"] == "*/*"

    def test_non_object_headers_json_is_ignored(self, engine, session, session_add_tpl):
        tpl = session_add_tpl(key="k5")
        tpl.headers = '["a", "b"]'
        session.add(tpl)
        session.commit()
        assert self._headers(engine, _make_sub(session, ua="k5"))["Accept"] == "*/*"

    def test_missing_table_degrades_to_the_builtin_map(self, engine, session):
        """Deploy-ordering safety net: new code can reach this before
        `entrypoint.sh` has re-run `alembic upgrade head`. A missing table
        must behave like a missing row, not fail the refresh."""
        from sqlalchemy import text
        from app.core.ua_templates import BUILTIN_UA_MAP

        sub = _make_sub(session, ua="clash")
        session.exec(text("DROP TABLE useragenttemplate"))
        session.commit()

        headers = self._headers(engine, sub)
        assert headers["User-Agent"] == BUILTIN_UA_MAP["clash"]
        assert headers["Accept"] == "*/*"

    def test_resolved_headers_are_sendable_by_httpx(
        self, engine, session, seeded_ua_templates
    ):
        """End of the validation chain: whatever we build must actually
        survive `httpx.Headers`, which is where a CR/LF or non-ASCII
        value would otherwise surface."""
        import httpx

        sub = _make_sub(session, ua="happ")
        raw = httpx.Headers(self._headers(engine, sub)).raw
        assert all(b"\r" not in v and b"\n" not in v for _, v in raw)


@pytest.fixture(name="session_add_tpl")
def session_add_tpl_fixture(session):
    """Factory for ad-hoc templates inside a single test."""
    from app.models import UserAgentTemplate

    def _add(key: str, *, user_agent: str = "Test/1.0", headers: dict | None = None):
        tpl = UserAgentTemplate(
            key=key, name=key, user_agent=user_agent,
            headers=json.dumps(headers or {}),
        )
        session.add(tpl)
        session.commit()
        session.refresh(tpl)
        return tpl

    return _add


# ── End-to-end: the fetch actually sends the template's headers ───────────────

class TestSubscriptionFetchUsesTemplate:
    def test_fetch_sends_template_headers(self, client, admin_user, auth_headers, session):
        """The whole feature is worthless if the resolved headers don't
        reach the wire. Assert on what `httpx.AsyncClient.get` receives."""
        from app.api.subscriptions import _fetch_subscription_unlocked
        from app.models import UserAgentTemplate

        session.add(UserAgentTemplate(
            key="panel-z", name="Panel Z", user_agent="PanelZ/4.5",
            headers=json.dumps({"X-Api-Key": "tok", "Accept-Encoding": ""}),
        ))
        session.commit()
        sub = _make_sub(session, ua="panel-z")

        with mock.patch("app.api.subscriptions.httpx.AsyncClient") as mock_client:
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock.Mock(
                status_code=200,
                text="vless://uuid-1@1.2.3.4:443?type=tcp&security=reality#n1",
                raise_for_status=lambda: None,
            ))
            asyncio.run(_fetch_subscription_unlocked(sub.id))

        sent = instance.get.call_args.kwargs["headers"]
        assert sent["User-Agent"] == "PanelZ/4.5"
        assert sent["X-Api-Key"] == "tok"
        # Empty value in the template dropped the base header entirely.
        assert not [k for k in sent if k.lower() == "accept-encoding"]

    def test_fetch_without_a_template_keeps_pre_1_4_7_behaviour(
        self, client, admin_user, auth_headers, session
    ):
        """Regression guard for the upgrade path: an install whose table
        is empty must send exactly the v1.4.6 header set."""
        from app.api.subscriptions import _fetch_subscription_unlocked
        from app.core.ua_templates import BUILTIN_UA_MAP

        sub = _make_sub(session, ua="clash")

        with mock.patch("app.api.subscriptions.httpx.AsyncClient") as mock_client:
            instance = mock_client.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock.Mock(
                status_code=200,
                text="vless://uuid-2@5.6.7.8:443?type=tcp&security=reality#n2",
                raise_for_status=lambda: None,
            ))
            asyncio.run(_fetch_subscription_unlocked(sub.id))

        sent = instance.get.call_args.kwargs["headers"]
        assert sent == {
            "User-Agent": BUILTIN_UA_MAP["clash"],
            "Accept": "*/*",
            "Accept-Language": "ru-RU,en,*",
            "Accept-Encoding": "gzip, deflate",
        }
