"""Set-aware routing import (preview + commit): dedupe, conflict detection,
destination resolution (global / existing set / new set), append semantics.

The export side is client-side (frontend assembles the native envelope from
already-loaded rules + sets), so there's no backend export endpoint to test —
these cover the import analysis the frontend drives.
"""
import pytest
from sqlmodel import select

from app.models import RoutingRule, RoutingSet


def _r(rule_type, match_value, action, name=""):
    return {"name": name, "enabled": True, "rule_type": rule_type,
            "match_value": match_value, "action": action, "order": 100}


def _seed_global(session, *rules):
    for rt, mv, ac in rules:
        session.add(RoutingRule(name=f"{rt}:{mv}", enabled=True, rule_type=rt,
                                match_value=mv, action=ac, order=100))
    session.commit()


class TestImportPreview:
    def test_new_rules_into_global(self, client, session, auth_headers, default_settings):
        r = client.post("/api/routing/import/preview", headers=auth_headers, json={
            "rules": [_r("domain", "youtube.com", "proxy"), _r("domain", "ru-site.example", "direct")],
            "destination": {"kind": "global"},
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["destination_exists"] is True
        assert d["will_add"] == 2
        assert d["identical_skipped"] == 0
        assert d["conflicts"] == []

    def test_identical_rule_skipped(self, client, session, auth_headers, default_settings):
        _seed_global(session, ("domain", "youtube.com", "proxy"))
        r = client.post("/api/routing/import/preview", headers=auth_headers, json={
            "rules": [_r("domain", "youtube.com", "proxy")],
            "destination": {"kind": "global"},
        })
        d = r.json()
        assert d["will_add"] == 0
        assert d["identical_skipped"] == 1

    def test_match_value_order_insensitive_identical(self, client, session, auth_headers, default_settings):
        _seed_global(session, ("domain", "a.com,b.com", "proxy"))
        r = client.post("/api/routing/import/preview", headers=auth_headers, json={
            "rules": [_r("domain", "B.com, a.com", "proxy")],
            "destination": {"kind": "global"},
        })
        assert r.json()["identical_skipped"] == 1

    def test_conflict_same_match_different_action(self, client, session, auth_headers, default_settings):
        _seed_global(session, ("domain", "youtube.com", "direct"))
        r = client.post("/api/routing/import/preview", headers=auth_headers, json={
            "rules": [_r("domain", "youtube.com", "proxy")],
            "destination": {"kind": "global"},
        })
        d = r.json()
        assert d["will_add"] == 0
        assert len(d["conflicts"]) == 1
        c = d["conflicts"][0]
        assert c["incoming_action"] == "proxy"
        assert c["existing_action"] == "direct"
        assert c["rule_type"] == "domain"

    def test_collapsed_duplicates_in_file(self, client, session, auth_headers, default_settings):
        r = client.post("/api/routing/import/preview", headers=auth_headers, json={
            "rules": [_r("domain", "a.com", "proxy"), _r("domain", "a.com", "proxy")],
            "destination": {"kind": "global"},
        })
        d = r.json()
        assert d["will_add"] == 1
        assert d["collapsed_duplicates"] == 1

    def test_invalid_action_skipped(self, client, session, auth_headers, default_settings):
        r = client.post("/api/routing/import/preview", headers=auth_headers, json={
            "rules": [_r("domain", "a.com", "node:999"), _r("domain", "b.com", "proxy")],
            "destination": {"kind": "global"},
        })
        d = r.json()
        assert d["invalid_skipped"] == 1
        assert d["will_add"] == 1

    def test_new_set_destination_not_yet_existing(self, client, session, auth_headers, default_settings):
        r = client.post("/api/routing/import/preview", headers=auth_headers, json={
            "rules": [_r("domain", "a.com", "proxy")],
            "destination": {"kind": "set", "new_set_name": "Kids"},
        })
        d = r.json()
        assert d["destination_exists"] is False
        assert d["will_add"] == 1
        # nothing was actually created
        assert session.exec(select(RoutingSet).where(RoutingSet.name == "Kids")).first() is None


class TestImportCommit:
    def test_append_into_global(self, client, session, auth_headers, default_settings):
        r = client.post("/api/routing/import/commit", headers=auth_headers, json={
            "rules": [_r("domain", "youtube.com", "proxy"), _r("port", "443", "direct")],
            "destination": {"kind": "global"},
        })
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["added"] == 2 and d["set_id"] is None
        rows = session.exec(select(RoutingRule)).all()
        assert {x.match_value for x in rows} == {"youtube.com", "443"}
        assert all(x.routing_set_id is None for x in rows)

    def test_conflict_keep_leaves_existing(self, client, session, auth_headers, default_settings):
        _seed_global(session, ("domain", "youtube.com", "direct"))
        r = client.post("/api/routing/import/commit", headers=auth_headers, json={
            "rules": [_r("domain", "youtube.com", "proxy")],
            "destination": {"kind": "global"},
            "default_conflict_choice": "keep",
        })
        d = r.json()
        assert d["skipped_conflicts"] == 1 and d["added"] == 0 and d["replaced"] == 0
        rows = session.exec(select(RoutingRule).where(RoutingRule.match_value == "youtube.com")).all()
        assert len(rows) == 1 and rows[0].action == "direct"

    def test_conflict_replace_swaps_action(self, client, session, auth_headers, default_settings):
        _seed_global(session, ("domain", "youtube.com", "direct"))
        prev = client.post("/api/routing/import/preview", headers=auth_headers, json={
            "rules": [_r("domain", "youtube.com", "proxy")],
            "destination": {"kind": "global"},
        }).json()
        key = prev["conflicts"][0]["key"]
        r = client.post("/api/routing/import/commit", headers=auth_headers, json={
            "rules": [_r("domain", "youtube.com", "proxy")],
            "destination": {"kind": "global"},
            "resolutions": [{"key": key, "choice": "replace"}],
        })
        d = r.json()
        assert d["replaced"] == 1
        rows = session.exec(select(RoutingRule).where(RoutingRule.match_value == "youtube.com")).all()
        assert len(rows) == 1 and rows[0].action == "proxy"

    def test_commit_creates_new_set(self, client, session, auth_headers, default_settings):
        r = client.post("/api/routing/import/commit", headers=auth_headers, json={
            "rules": [_r("domain", "a.com", "proxy"), _r("domain", "b.com", "direct")],
            "destination": {"kind": "set", "new_set_name": "Kids", "new_set_description": "kid devices"},
        })
        assert r.status_code == 201, r.text
        d = r.json()
        rs = session.exec(select(RoutingSet).where(RoutingSet.name == "Kids")).first()
        assert rs is not None and d["set_id"] == rs.id and d["set_name"] == "Kids"
        assert d["added"] == 2
        rows = session.exec(select(RoutingRule).where(RoutingRule.routing_set_id == rs.id)).all()
        assert {x.match_value for x in rows} == {"a.com", "b.com"}

    def test_commit_into_existing_set_isolated_from_global(self, client, session, auth_headers, default_settings):
        rs = RoutingSet(name="Work", tproxy_port=65501, order=0)
        session.add(rs)
        _seed_global(session, ("domain", "global-only.com", "proxy"))
        session.refresh(rs)
        r = client.post("/api/routing/import/commit", headers=auth_headers, json={
            "rules": [_r("domain", "work.com", "proxy")],
            "destination": {"kind": "set", "set_id": rs.id},
        })
        assert r.status_code == 201, r.text
        in_set = session.exec(select(RoutingRule).where(RoutingRule.routing_set_id == rs.id)).all()
        assert {x.match_value for x in in_set} == {"work.com"}
        # global rule untouched
        g = session.exec(select(RoutingRule).where(RoutingRule.routing_set_id.is_(None))).all()
        assert {x.match_value for x in g} == {"global-only.com"}

    def test_new_set_dup_name_rejected(self, client, session, auth_headers, default_settings):
        session.add(RoutingSet(name="Kids", tproxy_port=65502, order=0))
        session.commit()
        r = client.post("/api/routing/import/commit", headers=auth_headers, json={
            "rules": [_r("domain", "a.com", "proxy")],
            "destination": {"kind": "set", "new_set_name": "Kids"},
        })
        assert r.status_code == 400
