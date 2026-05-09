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
        for row in body:
            assert set(row.keys()) == {"id", "label", "description", "kind"}

    def test_requires_auth(self, client):
        resp = client.get("/api/templates")
        assert resp.status_code == 401


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
