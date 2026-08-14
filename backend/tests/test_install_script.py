"""Unit tests for `install.sh`.

The installer is not sourceable — it runs top to bottom — so the version
comparison is lifted out of the file itself and evaluated, rather than a copy
of it being retyped here. A test that agrees with a paraphrase of the code
would have passed while the shipped comparison was wrong.
"""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

INSTALL_SH = Path(__file__).resolve().parents[2] / "install.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    BASH is None or not INSTALL_SH.exists(), reason="bash or install.sh unavailable",
)


def _comparison_lines() -> str:
    """The lines install.sh uses to decide 'is the target older?'."""
    text = INSTALL_SH.read_text(encoding="utf-8", errors="ignore")
    wanted = [
        ln.strip() for ln in text.splitlines()
        if re.match(r'^\s*_(running|target)_cmp=', ln)
    ]
    assert wanted, "install.sh no longer prepares comparable version strings"
    return "\n".join(wanted)


def is_downgrade(running: str, target: str) -> bool:
    """Run the installer's own comparison for one pair of versions."""
    body = f"""
_running_num="{running}"
_target_num="{target}"
{_comparison_lines()}
if [[ "$_running_num" != "$_target_num" \\
      && "$(printf '%s\\n%s\\n' "$_running_cmp" "$_target_cmp" | sort -V | tail -1)" == "$_running_cmp" ]]; then
    echo DOWNGRADE
else
    echo OK
fi
"""
    r = subprocess.run([BASH, "-c", body], capture_output=True, text=True,
                       env=dict(os.environ), timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip() == "DOWNGRADE"


class TestDowngradeDetection:
    def test_beta_to_its_own_release_is_an_upgrade(self):
        """The single most common upgrade there is, and it was refused:
        `sort -V` is not semver aware — it reads `1.6.0-beta.3` as `1.6.0`
        plus extra characters and sorts it AFTER the finished release."""
        assert not is_downgrade("1.6.0-beta.3", "1.6.0")
        assert not is_downgrade("1.6.0-beta.1", "1.6.0")

    def test_release_to_its_own_beta_is_a_downgrade(self):
        assert is_downgrade("1.6.0", "1.6.0-beta.3")

    def test_ordinary_upgrades_are_allowed(self):
        assert not is_downgrade("1.5.3", "1.6.0")
        assert not is_downgrade("1.6.0", "1.6.1")
        assert not is_downgrade("1.6.0", "2.0.0")

    def test_ordinary_downgrades_are_caught(self):
        assert is_downgrade("1.6.0", "1.5.3")
        assert is_downgrade("1.6.1", "1.6.0")

    def test_betas_are_ordered_among_themselves(self):
        assert not is_downgrade("1.6.0-beta.2", "1.6.0-beta.3")
        assert is_downgrade("1.6.0-beta.3", "1.6.0-beta.2")

    def test_the_same_version_is_neither(self):
        assert not is_downgrade("1.6.0", "1.6.0")
        assert not is_downgrade("1.6.0-beta.3", "1.6.0-beta.3")


class TestUpdateAgentIsInstalled:
    """The panel's Update button writes a request file; a host-side systemd
    path unit is what carries it out. The installer never set that up, so the
    button sat at "waiting for the update agent" on every box ever installed
    — and the hint it showed named `--install-timer`, which is the separate
    scheduled check and does not service the button at all."""

    def test_the_installer_installs_the_agent(self):
        text = INSTALL_SH.read_text(encoding="utf-8", errors="ignore")
        assert "--install-agent" in text

    def test_it_does_not_quietly_enable_scheduled_updates(self):
        """Installing the agent must not also install the daily timer:
        acting on a request somebody made is not the same as updating a box
        on its own, and the second is the operator's decision."""
        text = INSTALL_SH.read_text(encoding="utf-8", errors="ignore")
        active = [
            ln for ln in text.splitlines()
            if "--install-timer" in ln and not ln.strip().startswith("#")
        ]
        assert active == []
