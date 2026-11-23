"""HttpsCertManager — force re-issue via the host's gen-cert.sh."""
import asyncio
from unittest.mock import MagicMock

import pytest

from app.core.https_cert import HttpsCertManager


def _proc(rc=0, stdout="[gen-cert] issued panel cert", stderr=""):
    p = MagicMock()
    p.returncode = rc
    p.stdout = stdout
    p.stderr = stderr
    return p


def test_reissue_runs_gen_cert_on_host_and_reloads(monkeypatch):
    m = HttpsCertManager()
    monkeypatch.setattr(
        HttpsCertManager, "_host_scripts_dir",
        lambda self: "/opt/pitun/scripts",
    )
    calls = {}

    def fake_host_run(argv, **kwargs):
        calls["argv"] = argv
        return _proc()

    monkeypatch.setattr("app.core.https_cert.host_run", fake_host_run)
    monkeypatch.setattr(HttpsCertManager, "_reload_nginx", lambda self: True)

    result = asyncio.run(m.reissue())
    assert result.ok and result.nginx_reloaded
    assert calls["argv"][0] == "sh"
    assert "gen-cert.sh --force" in calls["argv"][2]
    assert "/opt/pitun/scripts" in calls["argv"][2]


def test_reissue_raises_on_script_failure(monkeypatch):
    m = HttpsCertManager()
    monkeypatch.setattr(
        HttpsCertManager, "_host_scripts_dir",
        lambda self: "/opt/pitun/scripts",
    )
    monkeypatch.setattr(
        "app.core.https_cert.host_run",
        lambda argv, **kwargs: _proc(rc=1, stdout="", stderr="boom"),
    )
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(m.reissue())


def test_host_scripts_dir_parsed_from_mounts(monkeypatch, tmp_path):
    mounts = tmp_path / "mounts"
    mounts.write_text(
        "/dev/sda1 / ext4 rw 0 0\n"
        "/home/user/pitun/scripts /app/scripts bind ro 0 0\n",
        encoding="utf-8",
    )
    real_open = open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/self/mounts":
            return real_open(str(mounts), *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)
    assert HttpsCertManager()._host_scripts_dir() == "/home/user/pitun/scripts"
