"""Updating the xray core inside an x-ui panel from PiTun: the guard rails and
the endpoints (the panel itself is faked)."""
import pytest

from app.core import xui_xray
from app.models import Server, XuiServer

STATUS_NEW = {"panelVersion": "3.11.0", "xray": {"state": "running", "errorMsg": "", "version": "26.10.21"}}
STATUS_OLD = {"panelVersion": "3.6.0", "xray": {"state": "running", "errorMsg": "", "version": "26.7.28"}}
OFFERED = ["v26.10.31", "v26.10.21", "v26.9.30", "v26.9.9", "v26.7.28", "v26.7.11"]


def test_versions_parse_with_or_without_the_v():
    assert xui_xray.parse_version("v26.10.31") == (26, 10, 31)
    assert xui_xray.parse_version("3.11.0") == (3, 11, 0)
    for bad in ("", None, "26.10", "latest", "v26.10.31-beta", "26.10.31; rm -rf /"):
        assert xui_xray.parse_version(bad) is None
    assert xui_xray.normalize_tag("26.10.31") == "v26.10.31"


@pytest.mark.parametrize("panel,target,blocked", [
    ("3.11.0", "v26.10.31", False),
    ("3.8.0", "v26.9.9", False),
    ("3.6.0", "v26.10.31", True),     # templates are not migrated by this panel
    ("3.6.0", "v26.9.9", True),
    ("3.6.0", "v26.7.28", False),     # older cores are fine under an old panel
    ("3.6.0", "v26.7.11", False),
    (None, "v26.10.31", True),        # unknown panel: do not guess
])
def test_new_cores_need_a_new_enough_panel(panel, target, blocked):
    assert bool(xui_xray.check_target(panel, target)) is blocked


def test_describe_marks_current_newer_and_blocked_versions():
    d = xui_xray.describe(STATUS_OLD, OFFERED)
    assert d["panel_version"] == "3.6.0" and d["xray_version"] == "26.7.28" and d["xray_state"] == "running"
    by = {v["version"]: v for v in d["versions"]}
    assert by["26.7.28"]["current"] is True
    assert by["26.10.31"]["newer"] is True and by["26.10.31"]["blocked"]
    assert by["26.7.11"]["newer"] is False and by["26.7.11"]["blocked"] is None
    assert [v["version"] for v in d["versions"]][0] == "26.10.31"     # newest first
    ok = xui_xray.describe(STATUS_NEW, OFFERED)
    assert all(v["blocked"] is None for v in ok["versions"])


# ── endpoints ────────────────────────────────────────────────────────────────

class FakePanel:
    status = STATUS_NEW
    installed = []
    after = None            # status reported once the install has run
    fail_install = False

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_server_status(self):
        return type(self).after if (type(self).installed and type(self).after) else type(self).status

    async def list_xray_versions(self):
        return list(OFFERED)

    async def install_xray(self, version):
        from app.core.xui_api import XuiAPIError
        if type(self).fail_install:
            raise XuiAPIError("download failed", kind="api")
        type(self).installed.append(version)


@pytest.fixture
def panel(session, monkeypatch):
    FakePanel.status, FakePanel.installed, FakePanel.after, FakePanel.fail_install = STATUS_NEW, [], None, False
    srv = Server(name="vps", host="203.0.113.5", port=22, user="root", auth_type="password", password="pw")
    session.add(srv)
    session.commit()
    session.refresh(srv)
    xs = XuiServer(server_id=srv.id, api_token="t", panel_user="a", panel_pass="p",
                   panel_port=2053, panel_basepath="/p", mode="bare")
    session.add(xs)
    session.commit()
    session.refresh(xs)
    import app.api.xui as api
    monkeypatch.setattr(api, "XuiClient", FakePanel)

    async def no_sleep(_):
        return None
    monkeypatch.setattr("asyncio.sleep", no_sleep)
    return xs


def test_get_returns_the_running_core_and_the_offered_versions(client, admin_user, auth_headers, panel):
    r = client.get(f"/api/xui/servers/{panel.id}/xray", headers=auth_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["xray_version"] == "26.10.21" and d["panel_version"] == "3.11.0"
    assert d["versions"][0]["version"] == "26.10.31" and d["versions"][0]["newer"] is True


def test_update_installs_and_confirms_the_new_version(client, admin_user, auth_headers, panel):
    FakePanel.after = {"panelVersion": "3.11.0", "xray": {"state": "running", "errorMsg": "", "version": "26.10.31"}}
    r = client.post(f"/api/xui/servers/{panel.id}/xray/update", json={"version": "26.10.31"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["ok"] is True and d["changed"] is True and d["xray_version"] == "26.10.31"
    assert d["previous_version"] == "26.10.21"
    assert FakePanel.installed == ["v26.10.31"]


def test_update_reports_failure_and_names_the_rollback(client, admin_user, auth_headers, panel):
    FakePanel.after = {"panelVersion": "3.11.0", "xray": {"state": "error", "errorMsg": "bad", "version": "26.10.31"}}
    d = client.post(f"/api/xui/servers/{panel.id}/xray/update", json={"version": "26.10.31"}, headers=auth_headers).json()
    assert d["ok"] is False and "26.10.21" in d["message"]


def test_old_panels_are_refused_a_new_core(client, admin_user, auth_headers, panel):
    FakePanel.status = STATUS_OLD
    r = client.post(f"/api/xui/servers/{panel.id}/xray/update", json={"version": "26.10.31"}, headers=auth_headers)
    assert r.status_code == 409 and "3.8.0" in r.json()["detail"]
    assert FakePanel.installed == []


def test_old_panels_may_still_roll_back_or_stay_on_old_cores(client, admin_user, auth_headers, panel):
    FakePanel.status = STATUS_OLD
    FakePanel.after = {"panelVersion": "3.6.0", "xray": {"state": "running", "errorMsg": "", "version": "26.7.11"}}
    r = client.post(f"/api/xui/servers/{panel.id}/xray/update", json={"version": "26.7.11"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["ok"] is True


def test_already_running_version_is_a_no_op(client, admin_user, auth_headers, panel):
    r = client.post(f"/api/xui/servers/{panel.id}/xray/update", json={"version": "26.10.21"}, headers=auth_headers)
    assert r.status_code == 200 and r.json()["changed"] is False
    assert FakePanel.installed == []


@pytest.mark.parametrize("bad", ["latest", "26.10", "v26.10.31/../x", "26.10.31;id", ""])
def test_garbage_versions_are_rejected_before_any_panel_call(client, admin_user, auth_headers, panel, bad):
    r = client.post(f"/api/xui/servers/{panel.id}/xray/update", json={"version": bad}, headers=auth_headers)
    assert r.status_code == 422
    assert FakePanel.installed == []


def test_a_version_the_panel_does_not_offer_is_refused(client, admin_user, auth_headers, panel):
    r = client.post(f"/api/xui/servers/{panel.id}/xray/update", json={"version": "26.11.1"}, headers=auth_headers)
    assert r.status_code == 409 and FakePanel.installed == []


def test_a_failed_install_is_a_short_502(client, admin_user, auth_headers, panel):
    FakePanel.fail_install = True
    r = client.post(f"/api/xui/servers/{panel.id}/xray/update", json={"version": "26.10.31"}, headers=auth_headers)
    assert r.status_code == 502 and "Traceback" not in r.text
