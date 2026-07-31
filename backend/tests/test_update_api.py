"""Update check / request / status endpoints.

The split these tests pin down: the backend may CHECK in-process, but it
must never APPLY — the update replaces this container, so the request is
handed to a host agent through a file, and progress comes back the same
way. That file channel is the only one that survives our own restart.
"""
import json
from unittest import mock

import pytest

from app.config import APP_VERSION
from app.core import updater


@pytest.fixture(name="data_dir")
def data_dir_fixture(tmp_path):
    """Point the updater at a scratch data dir instead of the bind mount."""
    with (
        mock.patch.object(updater, "DATA_DIR", tmp_path),
        mock.patch.object(updater, "STATUS_FILE", tmp_path / "update-status.json"),
        mock.patch.object(updater, "REQUEST_FILE", tmp_path / "update-request.json"),
    ):
        yield tmp_path


class TestVersionComparison:
    @pytest.mark.parametrize(("newer", "older"), [
        ("v1.4.8", "1.4.7"),
        ("1.5.0", "1.4.9"),
        ("1.4.10", "1.4.9"),   # a string compare gets this backwards
        ("1.5.0", "1.5.0-beta.1"),
    ])
    def test_newer(self, newer, older):
        assert updater.is_newer(newer, older)

    @pytest.mark.parametrize(("a", "b"), [
        ("1.4.7", "1.4.7"),
        ("v1.4.7", "1.4.7"),
        ("1.4.7", "1.4.8"),
        ("1.5.0-beta.1", "1.5.0"),
        ("", "1.4.7"),
    ])
    def test_not_newer(self, a, b):
        assert not updater.is_newer(a, b)


class TestCheckEndpoint:
    def _patch_fetch(self, payload, path="active node"):
        async def fake(url, settings_map):
            return payload, path
        return mock.patch.object(updater, "_fetch", side_effect=fake)

    def test_reports_an_available_update(
        self, client, admin_user, auth_headers, default_settings,
    ):
        with self._patch_fetch({"tag_name": "v99.0.0", "body": "notes"}):
            resp = client.get("/api/system/update/check", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["latest"] == "v99.0.0"
        assert body["update_available"] is True
        assert body["current"] == APP_VERSION
        assert body["network_path"] == "active node"

    def test_same_version_is_not_an_update(
        self, client, admin_user, auth_headers, default_settings,
    ):
        with self._patch_fetch({"tag_name": f"v{APP_VERSION}"}):
            resp = client.get("/api/system/update/check", headers=auth_headers)
        assert resp.json()["update_available"] is False

    def test_unreachable_github_is_reported_not_silently_up_to_date(
        self, client, admin_user, auth_headers, default_settings,
    ):
        # "No update" and "we could not look" must not render the same —
        # that difference matters when a dead tunnel is the cause.
        with self._patch_fetch(None, path="unreachable"):
            resp = client.get("/api/system/update/check", headers=auth_headers)
        body = resp.json()
        assert body["update_available"] is False
        assert body["latest"] is None
        assert body["network_path"] == "unreachable"
        assert "unreachable" in (body["error"] or "").lower()

    def test_prerelease_channel_takes_the_first_listed_release(
        self, client, admin_user, auth_headers, default_settings,
    ):
        with self._patch_fetch([{"tag_name": "v99.1.0-beta.1"}, {"tag_name": "v1.0.0"}]):
            resp = client.get(
                "/api/system/update/check?prerelease=true", headers=auth_headers,
            )
        assert resp.json()["latest"] == "v99.1.0-beta.1"


class TestStartEndpoint:
    def test_writes_a_request_the_agent_can_read(
        self, client, admin_user, auth_headers, data_dir,
    ):
        resp = client.post(
            "/api/system/update/start",
            json={"version": "v9.9.9", "force": True},
            headers=auth_headers,
        )
        assert resp.status_code == 202
        req = json.loads((data_dir / "update-request.json").read_text())
        assert req["version"] == "v9.9.9"
        assert req["force"] is True

    def test_seeds_a_status_so_the_ui_has_something_to_show(
        self, client, admin_user, auth_headers, data_dir,
    ):
        resp = client.post(
            "/api/system/update/start", json={}, headers=auth_headers,
        )
        body = resp.json()
        assert body["state"] == "queued"
        assert body["request_pending"] is True

    def test_refuses_to_queue_a_second_update(
        self, client, admin_user, auth_headers, data_dir,
    ):
        client.post("/api/system/update/start", json={}, headers=auth_headers)
        resp = client.post("/api/system/update/start", json={}, headers=auth_headers)
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert "already in progress" in detail["error"]
        assert detail["hint"]

    def test_request_is_written_atomically(
        self, client, admin_user, auth_headers, data_dir,
    ):
        # The agent fires on the file APPEARING, so a partially written
        # request would be consumed as garbage.
        client.post(
            "/api/system/update/start", json={"version": "v9.9.9"},
            headers=auth_headers,
        )
        leftovers = list(data_dir.glob(".update-request.*"))
        assert leftovers == [], "temp file left behind"


class TestStatusEndpoint:
    def test_absent_file_reads_as_idle(
        self, client, admin_user, auth_headers, data_dir,
    ):
        resp = client.get("/api/system/update/status", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["state"] == "idle"

    def test_reports_agent_progress_verbatim(
        self, client, admin_user, auth_headers, data_dir,
    ):
        (data_dir / "update-status.json").write_text(json.dumps({
            "state": "running", "pct": 70, "step": "images",
            "message": "Loading pre-built Docker images",
            "from": "1.4.7", "to": "v1.4.8", "ok": None,
        }))
        body = client.get("/api/system/update/status", headers=auth_headers).json()
        assert body["state"] == "running"
        assert body["pct"] == 70
        assert body["step"] == "images"
        # `from`/`to` are reserved words in Python; the schema aliases them.
        assert body["from"] == "1.4.7"
        assert body["to"] == "v1.4.8"

    def test_survives_a_corrupted_status_file(
        self, client, admin_user, auth_headers, data_dir,
    ):
        (data_dir / "update-status.json").write_text("{ not json")
        body = client.get("/api/system/update/status", headers=auth_headers).json()
        assert body["state"] == "unknown"
        assert "not valid JSON" in body["message"]

    def test_pending_request_is_surfaced(
        self, client, admin_user, auth_headers, data_dir,
    ):
        # If this stays true, the host agent isn't installed or running —
        # the UI needs to be able to say so instead of spinning forever.
        (data_dir / "update-request.json").write_text("{}")
        body = client.get("/api/system/update/status", headers=auth_headers).json()
        assert body["request_pending"] is True


class TestDowngradeWarning:
    """Installing a build older than 1.4.8 REMOVES the in-UI updater. The
    box stays updatable from a shell, but the operator has to learn that
    before the panel disappears, not after."""

    def _patch_fetch(self, payload):
        async def fake(url, settings_map):
            return payload, "direct"
        return mock.patch.object(updater, "_fetch", side_effect=fake)

    @pytest.mark.parametrize("old", ["v1.4.7", "1.4.0", "1.3.9"])
    def test_flags_versions_without_the_ui(
        self, client, admin_user, auth_headers, default_settings, old,
    ):
        with self._patch_fetch({"tag_name": old}):
            body = client.get("/api/system/update/check", headers=auth_headers).json()
        assert body["target_lacks_update_ui"] is True
        assert body["update_ui_since"] == updater.UPDATE_UI_SINCE

    @pytest.mark.parametrize("ok", ["v1.4.8", "1.4.8-beta.1", "1.5.0", "v2.0.0"])
    def test_does_not_flag_versions_that_have_it(
        self, client, admin_user, auth_headers, default_settings, ok,
    ):
        with self._patch_fetch({"tag_name": ok}):
            body = client.get("/api/system/update/check", headers=auth_headers).json()
        assert body["target_lacks_update_ui"] is False

    def test_not_flagged_when_we_already_lack_the_ui(
        self, client, admin_user, auth_headers, default_settings,
    ):
        # Nothing is lost by moving between two builds that never had it.
        with (
            mock.patch.object(updater, "APP_VERSION", "1.4.5"),
            self._patch_fetch({"tag_name": "v1.4.7"}),
        ):
            body = client.get("/api/system/update/check", headers=auth_headers).json()
        assert body["target_lacks_update_ui"] is False

    def test_unreachable_check_does_not_claim_a_downgrade(
        self, client, admin_user, auth_headers, default_settings,
    ):
        with self._patch_fetch(None):
            body = client.get("/api/system/update/check", headers=auth_headers).json()
        assert body["target_lacks_update_ui"] is False
