"""`remove_argosbx` in setup-xui-server.sh.

x-ui-pro installs a sing-box stack (ArgoSBX) that PiTun never uses; the
installer removes it again. The script is not sourceable, so the function is
lifted out of the file itself and run in a sandbox: fake directories, a fake
`crontab`, and a throwaway process whose executable lives inside the fake
agsbx directory. Nothing outside the temp dir is touched.
"""
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "setup-xui-server.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    BASH is None or not SCRIPT.exists() or sys.platform == "win32",
    reason="needs bash and /proc (Linux)",
)

CRON_BEFORE = (
    "0 0 * * * sudo su -c 'x-ui restart > /dev/null 2>&1 && systemctl reload v2raya warp-plus tor"
    " && agsbx res > /dev/null 2>&1'\n"
    "0 0 1 * * certbot renew\n"
)


def _function_source() -> str:
    text = SCRIPT.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"^ARGOSBX_DIR=.*?^remove_argosbx\(\) \{.*?^\}\n", text, re.S | re.M)
    assert m, "remove_argosbx block not found in setup-xui-server.sh"
    return m.group(0)


@pytest.fixture
def sandbox(tmp_path):
    agsbx = tmp_path / "root" / "agsbx"
    agsbx.mkdir(parents=True)
    (agsbx / "jhsub.txt").write_text("hy2://secret@host:1\n")
    (tmp_path / "root" / "bin").mkdir()
    (tmp_path / "root" / "bin" / "agsbx").write_text("#!/bin/sh\n")
    web = tmp_path / "www"
    web.mkdir()
    (web / "Rnd0m.txt").write_text("hy2://secret@host:1\n")          # the copy x-ui-pro leaves
    (web / "index.nginx-debian.html").write_text("<html>welcome</html>\n")
    cron = tmp_path / "cron.txt"
    cron.write_text(CRON_BEFORE)
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "crontab").write_text(
        "#!/bin/sh\n"
        f'CRON="{cron}"\n'
        # like the real crontab: read all of stdin first, then replace the file
        'if [ "$1" = "-l" ]; then cat "$CRON"; else data="$(cat)"; printf "%s\\n" "$data" > "$CRON"; fi\n'
    )
    (shim / "crontab").chmod((shim / "crontab").stat().st_mode | stat.S_IEXEC)
    # a sleeper whose *executable* sits inside the fake agsbx dir
    # (a shell, not `sleep`: on some runners `sleep` is a multi-call binary that
    # refuses to run under another name)
    sleeper = agsbx / "sing-box"
    shutil.copy(shutil.which("sh"), sleeper)
    proc = subprocess.Popen([str(sleeper), "-c", "sleep 300; true"])
    yield dict(root=tmp_path, agsbx=agsbx, web=web, cron=cron, shim=shim, proc=proc)
    if proc.poll() is None:
        proc.kill()
    proc.wait()


def _run(box, keep=None, agsbx_dir=None):
    env = dict(os.environ)
    env["PATH"] = f"{box['shim']}:{env['PATH']}"
    env["ARGOSBX_DIR"] = str(agsbx_dir or box["agsbx"])
    env["ARGOSBX_BIN"] = str(box["root"] / "root" / "bin" / "agsbx")
    env["WEBROOT_DIR"] = str(box["web"])
    if keep:
        env["KEEP_ARGOSBX"] = keep
    script = (
        "set -euo pipefail\n"
        'info() { echo "[i] $*"; }\n'
        + _function_source()
        + "remove_argosbx\n"
        'echo "rc=$?"\n'
    )
    return subprocess.run([BASH, "-c", script], env=env, capture_output=True, text=True, timeout=30)


def test_removes_the_stack_and_keeps_the_rest(sandbox):
    r = _run(sandbox)
    assert r.returncode == 0, r.stderr
    assert not sandbox["agsbx"].exists()
    assert not (sandbox["root"] / "root" / "bin" / "agsbx").exists()
    # the subscription copy goes, the nginx default page stays
    assert not (sandbox["web"] / "Rnd0m.txt").exists()
    assert (sandbox["web"] / "index.nginx-debian.html").exists()
    # cron: agsbx part dropped, the nightly x-ui restart and certbot kept
    cron = sandbox["cron"].read_text()
    assert "agsbx" not in cron
    assert "x-ui restart" in cron and "systemctl reload v2raya warp-plus tor" in cron
    assert "certbot renew" in cron
    # the process whose executable lived in the agsbx dir is stopped
    for _ in range(20):
        if sandbox["proc"].poll() is not None:
            break
        time.sleep(0.1)
    assert sandbox["proc"].poll() is not None


def test_keep_flag_leaves_everything(sandbox):
    r = _run(sandbox, keep="yes")
    assert r.returncode == 0, r.stderr
    assert sandbox["agsbx"].exists()
    assert (sandbox["web"] / "Rnd0m.txt").exists()
    assert "agsbx res" in sandbox["cron"].read_text()
    assert sandbox["proc"].poll() is None


def test_is_a_noop_when_there_is_no_agsbx(sandbox, tmp_path):
    missing = tmp_path / "nothing" / "agsbx"
    r = _run(sandbox, agsbx_dir=missing)
    assert r.returncode == 0, r.stderr
    assert sandbox["agsbx"].exists(), "must not touch a different directory"
    assert "agsbx res" in sandbox["cron"].read_text()


def test_refuses_a_directory_that_is_not_named_agsbx(sandbox, tmp_path):
    other = tmp_path / "root" / "important"
    other.mkdir()
    (other / "data").write_text("keep me")
    r = _run(sandbox, agsbx_dir=other)
    assert r.returncode == 0, r.stderr
    assert (other / "data").read_text() == "keep me"


def test_running_it_twice_is_harmless(sandbox):
    assert _run(sandbox).returncode == 0
    assert _run(sandbox).returncode == 0


def test_the_cleanup_runs_after_both_installers():
    text = SCRIPT.read_text(encoding="utf-8", errors="ignore")
    call = text.rindex("\nremove_argosbx\n")
    assert text.index('|| err "x-ui-pro installer exited non-zero"') < call
    assert text.index('|| err "3x-ui installer exited non-zero"') < call
    assert text.index("remove_argosbx()") < text.index('log "Running x-ui-pro installer')


def test_cleanup_runs_after_the_mode_branch_not_only_for_x_ui_pro():
    """Detection decides, not the mode: the call sits at top level after the
    install branch, so a bare install that finds a leftover stack removes it too."""
    lines = SCRIPT.read_text(encoding="utf-8", errors="ignore").splitlines()
    calls = [i for i, ln in enumerate(lines) if ln.strip() == "remove_argosbx"]
    assert len(calls) == 1, "exactly one call expected"
    assert lines[calls[0]] == "remove_argosbx", "must not be nested in an if-branch"
    branch_end = max(i for i, ln in enumerate(lines[:calls[0]]) if ln == "fi")
    assert any("bare" in ln.lower() for ln in lines[:branch_end]), "bare branch precedes the call"


def test_base_packages_include_sudo():
    # x-ui-pro.sh shells out to sudo; minimal VPS images don't ship it.
    text = SCRIPT.read_text(encoding="utf-8", errors="ignore")
    block = text.split("Installing base packages", 1)[1].split("\n\n", 1)[0]
    assert " sudo" in block
