"""Tests for the decoy-site template gallery (since v1.3.0-beta.6)."""
from __future__ import annotations


class TestTemplatesRegistry:
    def test_resolve_known_single_html_template(self):
        from app.core.templates import resolve_to_env

        env = resolve_to_env("corporate")
        assert "TEMPLATE_HTML_URL" in env
        assert env["TEMPLATE_HTML_URL"].endswith("/corporate.html")
        # Single-html mode must NOT also leak DECOY_REPO; the script
        # would arbitrate but we keep the env clean.
        assert "DECOY_REPO" not in env

    def test_resolve_known_git_template(self):
        from app.core.templates import resolve_to_env

        env = resolve_to_env("pacman")
        assert "DECOY_REPO" in env
        assert "github.com" in env["DECOY_REPO"]
        assert "TEMPLATE_HTML_URL" not in env

    def test_pinned_git_template_emits_commit_sha(self):
        # Templates with a `pinned_commit` should also emit
        # DECOY_REPO_PINNED_COMMIT so the script can `git checkout`
        # the exact known-good state. Pre-pinning callers (no
        # pinned_commit field set) keep the legacy behaviour of
        # following upstream HEAD.
        from app.core.templates import resolve_to_env

        env = resolve_to_env("2048")
        assert "DECOY_REPO" in env
        assert "DECOY_REPO_PINNED_COMMIT" in env
        # Real SHA — 40 hex chars.
        assert len(env["DECOY_REPO_PINNED_COMMIT"]) == 40
        assert all(c in "0123456789abcdef" for c in env["DECOY_REPO_PINNED_COMMIT"])

    def test_resolve_unknown_id_is_empty(self):
        # Unknown / unset ids return {} so callers can `env.update(...)`
        # unconditionally without the script seeing junk vars.
        from app.core.templates import resolve_to_env

        assert resolve_to_env(None) == {}
        assert resolve_to_env("") == {}
        assert resolve_to_env("does-not-exist") == {}

    def test_all_templates_have_required_metadata(self):
        from app.core.templates import TEMPLATES

        seen_ids: set[str] = set()
        for t in TEMPLATES:
            assert t.id and t.label and t.description, t
            assert t.kind in ("single_html", "git_repo"), t
            assert t.id not in seen_ids, f"duplicate template id {t.id!r}"
            seen_ids.add(t.id)


class TestTemplatesEndpoint:
    def test_list_returns_canonical_shape(self, client, admin_user, auth_headers):
        resp = client.get("/api/templates", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert len(body) >= 4  # at least pacman + 3 single-file
        # Each row carries the user-facing fields; the source URL is
        # NOT exposed (kept server-side to keep the surface small).
        # Custom-only fields are optional — built-ins null them.
        required = {"id", "label", "description", "kind"}
        allowed_extra = {"custom_byte_size", "custom_filename", "custom_created_at"}
        for row in body:
            assert required.issubset(row.keys())
            assert set(row.keys()) <= required | allowed_extra

    def test_requires_auth(self, client):
        resp = client.get("/api/templates")
        assert resp.status_code == 401


class TestCustomTemplateUpload:
    """Custom .zip upload (Phase 2). Validates the upload endpoint
    + storage round-trip via tmp_path so each test gets an
    isolated data dir."""

    @staticmethod
    def _zip_bytes(files: dict[str, bytes]) -> bytes:
        """Helper: build an in-memory zip from a dict of name→bytes."""
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, data in files.items():
                zf.writestr(name, data)
        return buf.getvalue()

    @staticmethod
    def _isolate_data_dir(monkeypatch, tmp_path):
        """Point custom_templates at a fresh tmp dir for the test."""
        from app.core import custom_templates
        monkeypatch.setattr(
            custom_templates, "_data_dir", lambda: tmp_path,
        )

    def test_upload_round_trip(self, client, admin_user, auth_headers, monkeypatch, tmp_path):
        self._isolate_data_dir(monkeypatch, tmp_path)
        zip_bytes = self._zip_bytes({
            "index.html": b"<!doctype html><html><body>hi</body></html>",
            "style.css": b"body{margin:0}",
        })
        resp = client.post(
            "/api/templates/upload",
            data={"label": "My cover", "description": "A test"},
            files={"archive": ("cover.zip", zip_bytes, "application/zip")},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["label"] == "My cover"
        assert body["kind"] == "custom"
        assert body["custom_byte_size"] == len(zip_bytes)
        assert body["id"].startswith("my-cover-")
        # Archive persists on disk under tmp_path.
        archive_path = tmp_path / "templates" / "custom" / body["id"] / "archive.zip"
        assert archive_path.exists()
        assert archive_path.read_bytes() == zip_bytes
        # GET /templates merges the new entry into the gallery.
        listing = client.get("/api/templates", headers=auth_headers).json()
        assert any(t["id"] == body["id"] for t in listing)

    def test_upload_rejects_zip_slip(self, client, admin_user, auth_headers, monkeypatch, tmp_path):
        self._isolate_data_dir(monkeypatch, tmp_path)
        # `..` in a path → reject.
        zip_bytes = self._zip_bytes({
            "../etc/passwd": b"root:x:0:0::/root:/bin/bash",
            "index.html": b"<html></html>",
        })
        resp = client.post(
            "/api/templates/upload",
            data={"label": "Evil"},
            files={"archive": ("evil.zip", zip_bytes, "application/zip")},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "unsafe" in resp.json()["detail"].lower() or \
               ".." in resp.json()["detail"]

    def test_upload_rejects_disallowed_extension(self, client, admin_user, auth_headers, monkeypatch, tmp_path):
        self._isolate_data_dir(monkeypatch, tmp_path)
        zip_bytes = self._zip_bytes({
            "index.html": b"<html></html>",
            "shell.sh": b"#!/bin/sh\nrm -rf /\n",
        })
        resp = client.post(
            "/api/templates/upload",
            data={"label": "Oops"},
            files={"archive": ("oops.zip", zip_bytes, "application/zip")},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert ".sh" in resp.json()["detail"] or \
               "disallowed" in resp.json()["detail"].lower()

    def test_upload_rejects_no_index_html(self, client, admin_user, auth_headers, monkeypatch, tmp_path):
        self._isolate_data_dir(monkeypatch, tmp_path)
        zip_bytes = self._zip_bytes({"style.css": b"body{}"})
        resp = client.post(
            "/api/templates/upload",
            data={"label": "Empty"},
            files={"archive": ("empty.zip", zip_bytes, "application/zip")},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "index.html" in resp.json()["detail"]

    def test_upload_rejects_missing_label(self, client, admin_user, auth_headers, monkeypatch, tmp_path):
        self._isolate_data_dir(monkeypatch, tmp_path)
        zip_bytes = self._zip_bytes({"index.html": b"<html></html>"})
        resp = client.post(
            "/api/templates/upload",
            data={"label": "   "},
            files={"archive": ("x.zip", zip_bytes, "application/zip")},
            headers=auth_headers,
        )
        assert resp.status_code == 400
        assert "label" in resp.json()["detail"].lower()

    def test_delete_custom_round_trip(self, client, admin_user, auth_headers, monkeypatch, tmp_path):
        self._isolate_data_dir(monkeypatch, tmp_path)
        zip_bytes = self._zip_bytes({"index.html": b"<html></html>"})
        created = client.post(
            "/api/templates/upload",
            data={"label": "Doomed"},
            files={"archive": ("d.zip", zip_bytes, "application/zip")},
            headers=auth_headers,
        ).json()
        # Delete it.
        resp = client.delete(f"/api/templates/{created['id']}", headers=auth_headers)
        assert resp.status_code == 204
        # Second delete → 404 (idempotent for the user, but reports
        # not-found so the UI knows to refresh).
        resp = client.delete(f"/api/templates/{created['id']}", headers=auth_headers)
        assert resp.status_code == 404

    def test_delete_builtin_rejected(self, client, admin_user, auth_headers):
        resp = client.delete("/api/templates/pacman", headers=auth_headers)
        assert resp.status_code == 400
        assert "built-in" in resp.json()["detail"].lower()

    def test_resolve_custom_to_local_archive_env(self, monkeypatch, tmp_path):
        """`resolve_to_env` for a custom id emits the SFTP path
        env so the script knows where the deploy runner SFTP'd
        the archive."""
        from app.core import custom_templates
        from app.core.templates import resolve_to_env

        monkeypatch.setattr(custom_templates, "_data_dir", lambda: tmp_path)
        zip_bytes = self._zip_bytes({"index.html": b"<html></html>"})
        meta = custom_templates.create_custom(
            label="Resolver test",
            description="",
            archive_bytes=zip_bytes,
            original_filename="r.zip",
        )
        env = resolve_to_env(meta.id)
        assert env == {"TEMPLATE_LOCAL_ARCHIVE": "/tmp/pitun-template.zip"}


class TestNaiveEnvWithTemplate:
    """build_naive_env should fold the template id into the env dict
    via the same resolver. Regression guard for the deploy +
    manual-script paths that both go through this builder."""

    def test_build_naive_env_with_single_html_template(self):
        from app.core.deploy import build_naive_env

        env = build_naive_env(
            domain="x.example.com", email="me@example.com",
            template_id="corporate",
        )
        assert "TEMPLATE_HTML_URL" in env
        assert env["DOMAIN"] == "x.example.com"

    def test_build_naive_env_with_git_template(self):
        from app.core.deploy import build_naive_env

        env = build_naive_env(
            domain="x.example.com", email="me@example.com",
            template_id="pacman",
        )
        assert "DECOY_REPO" in env

    def test_build_naive_env_no_template_is_unchanged(self):
        # Backward compat — pre-template-gallery callers should see
        # an env that the script handles via its built-in default.
        from app.core.deploy import build_naive_env

        env = build_naive_env(domain="x.example.com", email="me@example.com")
        assert "TEMPLATE_HTML_URL" not in env
        assert "DECOY_REPO" not in env
