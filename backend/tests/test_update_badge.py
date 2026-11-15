"""Daily update check: cached result behind the header badge."""
from unittest import mock

import pytest

from app.core import updater


@pytest.fixture(autouse=True)
def _reset_cache():
    updater._last_auto = None
    yield
    updater._last_auto = None


def test_nothing_known_before_the_first_check(client, admin_user, auth_headers):
    r = client.get("/api/system/update/available", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["update_available"] is False and r.json()["checked_at"] is None


def test_a_found_update_is_served_from_the_cache(client, admin_user, auth_headers):
    updater.remember_check({"latest": "v99.0.0", "error": None, "update_available": True})
    body = client.get("/api/system/update/available", headers=auth_headers).json()
    assert body["update_available"] is True and body["latest"] == "v99.0.0" and body["checked_at"]


def test_a_failed_check_keeps_the_earlier_result():
    updater.remember_check({"latest": "v99.0.0", "error": None})
    updater.remember_check({"latest": None, "error": "GitHub is unreachable"})
    assert updater.cached_check()["latest"] == "v99.0.0"


def test_after_updating_the_badge_goes_away():
    updater.remember_check({"latest": updater.APP_VERSION, "error": None})
    assert updater.cached_check()["update_available"] is False


def test_manual_check_refreshes_the_cache(client, admin_user, auth_headers):
    fake = {"current": updater.APP_VERSION, "latest": "v99.0.0", "update_available": True,
            "network_path": "direct", "error": None}
    with mock.patch("app.core.updater.check_for_update", new=mock.AsyncMock(return_value=fake)):
        assert client.get("/api/system/update/check", headers=auth_headers).status_code == 200
    assert updater.cached_check()["update_available"] is True


def test_requires_auth(client):
    assert client.get("/api/system/update/available").status_code == 401
