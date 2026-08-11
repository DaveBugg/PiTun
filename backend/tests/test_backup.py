"""Whole-box config backup / restore.

Pins the two properties that make this safe to hand to an operator:
secrets are opt-in on export, and a secret-less bundle can never blank the
credentials of a node that already works.
"""
from app.models import Node, DNSRule, Settings as DBSettings


def _export(client, auth_headers, **params):
    resp = client.get("/api/system/backup", headers=auth_headers, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestExport:
    def test_envelope_and_sections(self, client, admin_user, auth_headers):
        b = _export(client, auth_headers)
        assert b["kind"] == "pitun-config-backup"
        assert b["version"] == 1
        assert b["secrets_included"] is False
        # Settings are seeded on startup, so this section is never empty.
        assert "settings" in b["sections"]
        assert b["counts"]["settings"] == len(b["sections"]["settings"])

    def test_secrets_redacted_by_default(self, client, admin_user, auth_headers, session):
        session.add(Node(name="n", protocol="vless", address="1.1.1.1", port=443,
                         uuid="super-secret-uuid", transport="tcp", enabled=True))
        session.commit()

        redacted = _export(client, auth_headers)
        assert redacted["sections"]["nodes"][0]["uuid"] == ""

        full = _export(client, auth_headers, include_secrets=True)
        assert full["secrets_included"] is True
        assert full["sections"]["nodes"][0]["uuid"] == "super-secret-uuid"


class TestPreview:
    def test_rejects_foreign_bundle(self, client, admin_user, auth_headers):
        resp = client.post("/api/system/backup/preview", headers=auth_headers,
                           json={"bundle": {"kind": "something-else", "version": 1}})
        assert resp.status_code == 400

    def test_preview_writes_nothing(self, client, admin_user, auth_headers, session):
        b = _export(client, auth_headers, include_secrets=True)
        b["sections"]["dns_rules"] = [
            {"id": 991, "name": "preview", "domain_match": "preview.example",
             "dns_server": "1.1.1.1", "dns_type": "plain", "enabled": True, "order": 100},
        ]
        resp = client.post("/api/system/backup/preview", headers=auth_headers,
                           json={"bundle": b, "mode": "merge"})
        assert resp.status_code == 200, resp.text
        plan = {p["section"]: p for p in resp.json()["plan"]}
        assert plan["dns_rules"]["would_add"] == 1
        # Nothing persisted by a preview.
        session.expire_all()
        assert session.get(DNSRule, 991) is None

    def test_warns_when_bundle_has_no_secrets(self, client, admin_user, auth_headers):
        b = _export(client, auth_headers)  # secrets excluded
        resp = client.post("/api/system/backup/preview", headers=auth_headers,
                           json={"bundle": b, "mode": "merge"})
        assert any("WITHOUT secrets" in w for w in resp.json()["warnings"])


class TestRestore:
    def test_merge_adds_rows(self, client, admin_user, auth_headers, session):
        b = _export(client, auth_headers, include_secrets=True)
        b["sections"]["dns_rules"] = [
            {"id": 992, "name": "restored", "domain_match": "restored.example",
             "dns_server": "9.9.9.9", "dns_type": "plain", "enabled": True, "order": 100},
        ]
        resp = client.post("/api/system/backup/restore", headers=auth_headers,
                           json={"bundle": b, "mode": "merge"})
        assert resp.status_code == 200, resp.text
        session.expire_all()
        row = session.get(DNSRule, 992)
        assert row is not None and row.domain_match == "restored.example"

    def test_secretless_restore_keeps_existing_credentials(
        self, client, admin_user, auth_headers, session,
    ):
        """The whole point of the opt-in redaction: restoring a sanitised
        backup must not wipe the UUID of a node that currently works."""
        node = Node(name="keepme", protocol="vless", address="2.2.2.2", port=443,
                    uuid="live-uuid", transport="tcp", enabled=True)
        session.add(node)
        session.commit()
        session.refresh(node)
        node_id = node.id

        b = _export(client, auth_headers)  # redacted — uuid is ""
        assert b["sections"]["nodes"][0]["uuid"] == ""

        resp = client.post("/api/system/backup/restore", headers=auth_headers,
                           json={"bundle": b, "mode": "merge"})
        assert resp.status_code == 200, resp.text

        session.expire_all()
        assert session.get(Node, node_id).uuid == "live-uuid"

    def test_replace_deletes_rows_absent_from_bundle(
        self, client, admin_user, auth_headers, session,
    ):
        session.add(DNSRule(id=993, name="doomed", domain_match="doomed.example",
                            dns_server="1.1.1.1", dns_type="plain", enabled=True))
        session.commit()

        b = _export(client, auth_headers, include_secrets=True)
        # Restore only dns_rules, with an empty set → the row must go.
        b["sections"]["dns_rules"] = []
        resp = client.post("/api/system/backup/restore", headers=auth_headers,
                           json={"bundle": b, "mode": "replace",
                                 "sections": ["dns_rules"]})
        assert resp.status_code == 200, resp.text
        session.expire_all()
        assert session.get(DNSRule, 993) is None

    def test_settings_are_matched_by_key_not_id(
        self, client, admin_user, auth_headers, session,
    ):
        """Settings carry a unique `key`; restoring must update the existing
        row rather than trying to insert a duplicate key."""
        from sqlmodel import select as sm_select
        # Own the fixture rather than relying on startup seeding, which the
        # test DB doesn't run.
        session.add(DBSettings(key="log_level", value="warning"))
        session.commit()

        b = _export(client, auth_headers, include_secrets=True)
        for s in b["sections"]["settings"]:
            if s["key"] == "log_level":
                s["value"] = "debug"

        resp = client.post("/api/system/backup/restore", headers=auth_headers,
                           json={"bundle": b, "mode": "merge",
                                 "sections": ["settings"]})
        assert resp.status_code == 200, resp.text

        session.expire_all()
        after = session.exec(
            sm_select(DBSettings).where(DBSettings.key == "log_level")
        ).all()
        assert len(after) == 1, "restore duplicated a unique settings key"
        assert after[0].value == "debug"
