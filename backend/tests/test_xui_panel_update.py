"""Updating the x-ui panel from PiTun to the release PiTun has tested: pinned
updater over SSH, database copy first, nothing runs without it (the panel and
the SSH side are faked)."""
import re
from pathlib import Path

import pytest

from app.core import xui_panel
from app.core.ssh import DeployResult
from app.models import Server, XuiServer

BACKUP = "/etc/x-ui/x-ui.db.pitun-pre-update-20261102-120000"
SETUP = Path(__file__).resolve().parents[2] / "scripts" / "setup-xui-server.sh"


def test_backup_path_is_only_taken_from_the_expected_place():
    assert xui_panel.parse_backup_path(f"x\nPITUN_BACKUP={BACKUP}\n") == BACKUP
    assert xui_panel.parse_backup_path("PITUN_BACKUP=/etc/passwd") is None
    assert xui_panel.parse_backup_path("PITUN_BACKUP_ERR=no database") is None
    assert xui_panel.parse_backup_path("") is None


def test_describe_compares_against_the_pinned_target():
    assert xui_panel.describe("3.6.0")["update_available"] is True
    assert xui_panel.describe(xui_panel.TARGET_VERSION)["update_available"] is False
    assert xui_panel.describe("3.12.0")["update_available"] is False   # newer than tested: never "downgrade"
    assert xui_panel.describe(None)["update_available"] is False
    assert xui_panel.describe("3.6.0")["latest"] == xui_panel.TARGET_VERSION


def test_updater_is_pinned_by_commit_and_checksum_and_never_by_branch():
    s = xui_panel.UPDATE_SCRIPT
    assert xui_panel.UPDATE_SH_COMMIT in s and xui_panel.UPDATE_SH_SHA256 in s
    assert f'XUI_UPDATE_TAG="{xui_panel.TARGET_TAG}"' in s
    assert "/main/" not in s and "/master/" not in s and "__" not in s
    assert re.fullmatch(r"[0-9a-f]{40}", xui_panel.UPDATE_SH_COMMIT)
    assert re.fullmatch(r"[0-9a-f]{64}", xui_panel.UPDATE_SH_SHA256)


@pytest.mark.skipif(not SETUP.exists(), reason="scripts/ not shipped in this tree")
def test_target_matches_what_the_installer_installs():
    text = SETUP.read_text(encoding="utf-8", errors="ignore")
    version = re.search(r'XUI_VERSION="\$\{XUI_VERSION:-(v[\d.]+)\}"', text).group(1)
    commit = re.search(r'XUI_INSTALL_SHA="\$\{XUI_INSTALL_SHA:-([0-9a-f]{40})\}"', text).group(1)
    assert version == xui_panel.TARGET_TAG
    assert commit == xui_panel.UPDATE_SH_COMMIT


class FakePanel:
    version = "3.6.0"
    updated = False
    down_calls = 0

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get_server_status(self):
        from app.core.xui_api import XuiAPIError
        cls = type(self)
        if cls.updated and cls.down_calls:
            cls.down_calls -= 1
            raise XuiAPIError("restarting", kind="transport")
        return {"panelVersion": xui_panel.TARGET_VERSION if cls.updated else cls.version,
                "xray": {"state": "running", "version": "26.7.28"}}


@pytest.fixture
def panel(session, monkeypatch):
    FakePanel.version, FakePanel.updated, FakePanel.down_calls = "3.6.0", False, 0
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


def _ssh(monkeypatch, backup, update, calls):
    """Fake the two scripts: the backup one and the updater."""
    async def fake(**kw):
        script = kw["script_content"]
        if script == xui_panel.BACKUP_SCRIPT:
            calls.append("backup")
            return backup
        calls.append("update")
        if update.ok:
            FakePanel.updated = True
            FakePanel.down_calls = 2
        return update
    monkeypatch.setattr("app.core.ssh.exec_remote_script", fake)


def _backup_ok():
    return DeployResult(ok=True, exit_code=0, stdout=f"PITUN_BACKUP={BACKUP}\n", stderr="")


def _update_ok():
    return DeployResult(ok=True, exit_code=0, stdout="PITUN_UPDATE_EXIT=0\n", stderr="")


def test_get_reports_current_and_the_tested_target(client, admin_user, auth_headers, panel):
    d = client.get(f"/api/xui/servers/{panel.id}/panel-update", headers=auth_headers).json()
    assert d["current"] == "3.6.0" and d["latest"] == xui_panel.TARGET_VERSION and d["update_available"]


def test_update_backs_up_first_then_runs_the_pinned_updater(client, admin_user, auth_headers, panel, monkeypatch):
    calls = []
    _ssh(monkeypatch, _backup_ok(), _update_ok(), calls)
    d = client.post(f"/api/xui/servers/{panel.id}/panel-update", headers=auth_headers).json()
    assert calls == ["backup", "update"]
    assert d["ok"] is True and d["changed"] is True
    assert d["previous_version"] == "3.6.0" and d["panel_version"] == xui_panel.TARGET_VERSION
    assert d["backup"] == BACKUP


def test_no_backup_means_the_updater_never_runs(client, admin_user, auth_headers, panel, monkeypatch):
    calls = []
    _ssh(monkeypatch, DeployResult(ok=False, exit_code=2, stdout="PITUN_BACKUP_ERR=no database", stderr=""),
         _update_ok(), calls)
    r = client.post(f"/api/xui/servers/{panel.id}/panel-update", headers=auth_headers)
    assert r.status_code == 502 and "nothing was changed" in r.json()["detail"]
    assert calls == ["backup"]


def test_a_backup_path_outside_the_expected_place_is_not_trusted(client, admin_user, auth_headers, panel, monkeypatch):
    calls = []
    _ssh(monkeypatch, DeployResult(ok=True, exit_code=0, stdout="PITUN_BACKUP=/tmp/x\n", stderr=""),
         _update_ok(), calls)
    assert client.post(f"/api/xui/servers/{panel.id}/panel-update", headers=auth_headers).status_code == 502
    assert calls == ["backup"]


def test_a_panel_at_or_above_the_tested_release_is_left_alone(client, admin_user, auth_headers, panel, monkeypatch):
    FakePanel.version = "3.12.0"
    calls = []
    _ssh(monkeypatch, _backup_ok(), _update_ok(), calls)
    d = client.post(f"/api/xui/servers/{panel.id}/panel-update", headers=auth_headers).json()
    assert d["changed"] is False and calls == []


def test_updater_failure_is_reported_with_the_reason_and_the_backup(client, admin_user, auth_headers, panel, monkeypatch):
    calls = []
    _ssh(monkeypatch, _backup_ok(),
         DeployResult(ok=False, exit_code=5, stdout="PITUN_UPDATE_ERR=updater checksum mismatch, refusing to run it\n",
                      stderr=""), calls)
    d = client.post(f"/api/xui/servers/{panel.id}/panel-update", headers=auth_headers).json()
    assert d["ok"] is False and "checksum mismatch" in d["message"] and BACKUP in d["message"]
