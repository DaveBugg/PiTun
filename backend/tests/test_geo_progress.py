"""Tests for the geo-update live progress endpoint (since v1.3.0-beta.6).

Verifies the in-process state singleton, the API surface, and the
per-file error containment (one file failing doesn't abort the others).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class TestGeoProgressState:
    """Direct tests against the singleton — no FastAPI involved."""

    def setup_method(self):
        # Module-level state leaks across tests; reset by starting a
        # fresh empty job and immediately marking it finished.
        from app.core import geo_progress
        geo_progress._state = geo_progress.GeoUpdateState()

    def test_initial_state_is_inactive(self):
        from app.core import geo_progress
        s = geo_progress.get_state()
        assert s.active is False
        assert s.files == {}

    def test_start_job_seeds_queued_files(self):
        from app.core import geo_progress
        job_id = geo_progress.start_job(["geoip", "geosite"])
        s = geo_progress.get_state()
        assert s.job_id == job_id
        assert s.active is True
        assert set(s.files.keys()) == {"geoip", "geosite"}
        assert all(f.stage == "queued" for f in s.files.values())

    def test_lifecycle_transitions(self):
        from app.core import geo_progress
        geo_progress.start_job(["geoip"])
        geo_progress.set_stage("geoip", "downloading", source_url="http://x/y")
        geo_progress.update_bytes("geoip", 1024, 4096)
        geo_progress.set_stage("geoip", "verifying")
        geo_progress.mark_done("geoip")
        geo_progress.mark_tag_cache_refreshed()
        geo_progress.finish()

        s = geo_progress.get_state()
        f = s.files["geoip"]
        assert f.stage == "done"
        assert f.bytes_downloaded == 1024
        assert f.bytes_total == 4096
        assert f.source_url == "http://x/y"
        assert f.started_at is not None
        assert f.finished_at is not None
        assert s.tag_cache_refreshed is True
        assert s.active is False  # finished_at set

    def test_set_error_isolates_file(self):
        from app.core import geo_progress
        geo_progress.start_job(["geoip", "geosite"])
        geo_progress.set_stage("geoip", "downloading")
        geo_progress.set_error("geoip", "HTTPStatusError: 404")
        geo_progress.mark_done("geosite")

        s = geo_progress.get_state()
        assert s.files["geoip"].stage == "failed"
        assert "404" in s.files["geoip"].error
        assert s.files["geosite"].stage == "done"

    def test_unknown_file_name_is_noop(self):
        # Defensive — accidental typos in caller code shouldn't crash
        # the running download.
        from app.core import geo_progress
        geo_progress.start_job(["geoip"])
        geo_progress.set_stage("geosite", "downloading")  # not in job
        geo_progress.update_bytes("mmdb", 1, 2)            # not in job
        s = geo_progress.get_state()
        assert "geosite" not in s.files
        assert "mmdb" not in s.files


class TestGeoProgressEndpoint:
    """End-to-end through FastAPI — the read endpoint + the trigger
    endpoint's state-seeding behaviour."""

    def test_progress_endpoint_returns_idle_at_boot(
        self, client, admin_user, auth_headers,
    ):
        # Fresh `client` fixture means a fresh backend process (per
        # conftest), so the singleton is at its zero state.
        from app.core import geo_progress
        geo_progress._state = geo_progress.GeoUpdateState()

        resp = client.get("/api/geodata/update/progress", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is False
        assert body["files"] == {}

    def test_update_endpoint_seeds_progress_state_synchronously(
        self, client, admin_user, auth_headers,
    ):
        # Mock the actual download to a no-op so the test doesn't try
        # to fetch from GitHub. The endpoint should still seed the
        # progress state SYNCHRONOUSLY (before returning 202) so the
        # immediate poll right after .mutate() sees `active=true`.
        with (
            patch("app.core.geo.update_geoip", new=AsyncMock(return_value=None)),
            patch("app.core.geo.update_geosite", new=AsyncMock(return_value=None)),
            patch("app.core.geo.update_mmdb", new=AsyncMock(return_value=None)),
            patch("app.core.geo.refresh_tag_cache", new=lambda: None),
        ):
            resp = client.post(
                "/api/geodata/update",
                json={"type": "geoip"},
                headers=auth_headers,
            )
            assert resp.status_code == 202
            assert resp.json()["job_id"]

            # Immediately check progress — must already have the geoip
            # row with `queued` (or beyond, if the background task
            # already raced ahead). Either way, the file MUST be
            # present and the job_id MUST match.
            prog = client.get(
                "/api/geodata/update/progress", headers=auth_headers,
            ).json()
            assert prog["job_id"] == resp.json()["job_id"]
            assert "geoip" in prog["files"]
            # Should be 'queued' or any later stage; NOT empty.
            assert prog["files"]["geoip"]["stage"] in (
                "queued", "downloading", "verifying", "applying", "done",
            )
