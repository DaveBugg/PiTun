"""Tests for `app.core.deploy.extract_uri` — URI parser for the
auto-deploy stdout contract.

Contract enforced by `scripts/setup-naive-server.sh`:
  * Last meaningful line of stdout is `URI=<scheme>://<rest>`
  * Format must survive `_NoNewlineFilter` (no \\n / \\r in the URI itself)
  * Scheme matches the protocol the deploy was invoked for

These tests don't touch SSH or the install script — they cover the
regex + selection logic in isolation.
"""
from __future__ import annotations

from app.core.deploy import (
    SUPPORTED_PROTOCOLS,
    build_naive_env,
    build_plan,
    extract_uri,
    load_script,
)


# ── extract_uri ──────────────────────────────────────────────────────────────


class TestExtractUri:
    def test_naive_canonical_last_line(self):
        stdout = (
            "Setting up caddy...\n"
            "Caddy installed.\n"
            "URI=naive+https://user:pass@example.com:443/?padding=1#example.com\n"
        )
        assert extract_uri(stdout, "naive") == \
            "naive+https://user:pass@example.com:443/?padding=1#example.com"

    def test_returns_none_when_no_uri_line(self):
        stdout = "Lots of output\nbut no URI marker\n"
        assert extract_uri(stdout, "naive") is None

    def test_empty_stdout_returns_none(self):
        assert extract_uri("", "naive") is None

    def test_picks_last_uri_when_multiple_present(self):
        # Mid-script diagnostic line vs canonical end-of-script line —
        # the LAST `URI=` wins so a script that echoes intermediate
        # values for debugging doesn't shadow the real one.
        stdout = (
            "URI=naive+https://stale@old.example.com:443\n"
            "Reconfiguring...\n"
            "URI=naive+https://fresh@new.example.com:443\n"
        )
        result = extract_uri(stdout, "naive")
        assert result == "naive+https://fresh@new.example.com:443"

    def test_prefers_scheme_match_over_recency_when_mixed(self):
        # If the script printed two URIs of different schemes (rare but
        # possible during script evolution), prefer the one matching the
        # requested protocol's scheme.
        stdout = (
            "URI=vless://stray@host.example.com:443\n"
            "URI=naive+https://canonical@example.com:443\n"
        )
        # Asked for naive → naive scheme wins
        assert extract_uri(stdout, "naive") == \
            "naive+https://canonical@example.com:443"

    def test_handles_crlf_line_endings(self):
        # Some scripts produce CRLF when piped through tools or logged.
        stdout = "Starting...\r\nURI=naive+https://u:p@example.com:443\r\n"
        assert extract_uri(stdout, "naive") == "naive+https://u:p@example.com:443"

    def test_strips_trailing_whitespace(self):
        stdout = "URI=naive+https://u:p@example.com:443   \n"
        result = extract_uri(stdout, "naive")
        assert result == "naive+https://u:p@example.com:443"

    def test_case_insensitive_uri_keyword(self):
        # Future scripts might write `Uri=` or even `uri=` — accept both.
        for prefix in ("URI=", "Uri=", "uri="):
            stdout = f"{prefix}naive+https://u:p@example.com:443\n"
            assert extract_uri(stdout, "naive") == \
                "naive+https://u:p@example.com:443"

    def test_unknown_protocol_falls_back_to_last_uri(self):
        # extract_uri with a protocol we don't have a scheme map for
        # returns whatever the last URI= line says (advisory).
        # Currently SUPPORTED_PROTOCOLS = ('naive',) so any other arg
        # has no expected scheme to match against.
        stdout = "URI=something://value\n"
        assert extract_uri(stdout, "future-protocol") == "something://value"

    def test_uri_line_must_be_at_line_start(self):
        # `something URI=foo` (URI= mid-line) is NOT the contract.
        stdout = "Decorative URI=naive+https://nope.example.com:443\n"
        # Our regex anchors with `^URI=`, so this should be skipped.
        assert extract_uri(stdout, "naive") is None


# ── load_script + build_plan + build_naive_env ───────────────────────────────


class TestScriptLoader:
    def test_load_naive_script_includes_uri_marker(self):
        # The setup-naive-server.sh shipped in the repo must include
        # the `URI=` contract line — if a maintainer ever drops it,
        # this test fails LOUDLY at CI time instead of silently in
        # production deploys returning status='deployed_no_uri'.
        script = load_script("naive")
        assert script
        assert "URI=" in script, (
            "setup-naive-server.sh must end with a `URI=...` line "
            "(machine-readable contract for auto-deploy)"
        )

    def test_load_unsupported_protocol_raises(self):
        import pytest
        with pytest.raises(ValueError):
            load_script("hysteria-9000")


class TestBuildNaiveEnv:
    def test_minimal(self):
        env = build_naive_env(domain="x.example.com", email="me@example.com")
        assert env["DOMAIN"] == "x.example.com"
        assert env["EMAIL"] == "me@example.com"
        assert env["NAIVE_USER"] == "pitun"
        # Auto-generated password — non-empty, urlsafe-ish
        assert env["NAIVE_PASS"]
        assert len(env["NAIVE_PASS"]) >= 20

    def test_user_overrides(self):
        env = build_naive_env(
            domain="x.example.com", email="me@example.com",
            naive_user="vasya", naive_pass="hunter2",
        )
        assert env["NAIVE_USER"] == "vasya"
        assert env["NAIVE_PASS"] == "hunter2"

    def test_missing_domain_raises(self):
        import pytest
        with pytest.raises(ValueError, match="domain"):
            build_naive_env(domain="", email="me@example.com")

    def test_missing_email_raises(self):
        import pytest
        with pytest.raises(ValueError, match="email"):
            build_naive_env(domain="x.example.com", email="")

    def test_random_pass_unique_per_call(self):
        e1 = build_naive_env(domain="x", email="y")
        e2 = build_naive_env(domain="x", email="y")
        assert e1["NAIVE_PASS"] != e2["NAIVE_PASS"]


class TestBuildPlan:
    def test_naive_plan_loads_script_and_env(self):
        plan = build_plan("naive", {
            "domain": "x.example.com", "email": "me@example.com",
        })
        assert plan.protocol == "naive"
        assert plan.script_content
        assert "URI=" in plan.script_content
        assert plan.env["DOMAIN"] == "x.example.com"
        assert plan.env["EMAIL"] == "me@example.com"

    def test_unsupported_protocol_raises(self):
        import pytest
        with pytest.raises(ValueError):
            build_plan("wireguard", {})

    def test_naive_missing_required_config_raises(self):
        import pytest
        # No domain → ValueError from build_naive_env
        with pytest.raises(ValueError):
            build_plan("naive", {"email": "me@example.com"})


# ── Module-level invariants ──────────────────────────────────────────────────


def test_supported_protocols_is_only_naive_in_beta_1():
    # Hard-coded sanity check — when we add xray/hy2 in beta.2, this
    # test is the explicit bump point. Keeps Phase 1 scoped tight.
    assert SUPPORTED_PROTOCOLS == ("naive",)
