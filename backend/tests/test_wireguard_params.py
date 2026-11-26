"""Regression tests for the WireGuard params serializer.

The setup script stores server parameters in ``/etc/wireguard/pitun-params`` and
sources them later with ``load_params``. ``_params_set`` must therefore write
values that survive a POSIX ``source`` round-trip literally, even when SOCKS
credentials contain quotes, whitespace, newlines, dollars, backticks or command
substitutions.
"""
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
SETUP_SCRIPT = SCRIPTS / "setup-wireguard-server.sh"
BASH = shutil.which("bash")

pytestmark = pytest.mark.skipif(
    BASH is None or not SETUP_SCRIPT.exists(),
    reason="bash or setup-wireguard-server.sh unavailable",
)


def _extract_params_set_code() -> str:
    """Pull the embedded Python serializer out of the shell script."""
    text = SETUP_SCRIPT.read_text(encoding="utf-8")
    # There are several PYEOF blocks; the one we care about imports shlex.
    for match in re.finditer(r"<<'PYEOF'\n(.*?)PYEOF\n\}", text, re.DOTALL):
        block = match.group(1)
        if "import shlex" in block and "shlex.quote" in block:
            return block
    raise RuntimeError("Could not find _params_set Python block in setup script")


def _run_serializer(path: Path, key: str, value: str) -> None:
    """Run the script's own Python block against a temp params file."""
    code = _extract_params_set_code()
    subprocess.run(
        [sys.executable, "-", str(path), key, value],
        input=code.encode("utf-8"),
        check=True,
    )


def _source_value(path: Path, key: str) -> bytes:
    """Source the params file and return the raw value for *key*.

    Also guards against command substitution executing during ``source``.
    """
    script = f"""
set -eu
cd /
source {shlex.quote(path.as_posix())}
[ -z "${{{'PITUN_CMD_EXEC_MARKER'}:-}}" ] || {{ echo PITUN_CMD_EXEC_MARKER=set; exit 1; }}
printf '%s\\0' "${key}"
"""
    result = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        check=True,
    )
    return result.stdout


@pytest.mark.parametrize(
    "value",
    [
        "simple",
        "with spaces",
        'double"quote',
        "single'quote",
        "dollar$sign",
        "back\\slash",
        "tab\tchar",
        "multi\nline\nvalue",
        'mixed "`\\ $\\{var} `',
        "$(echo PITUN_CMD_EXEC_MARKER=set)",
        "`echo PITUN_CMD_EXEC_MARKER=set`",
    ],
    ids=lambda v: repr(v)[:40],
)
def test_params_set_round_trips_arbitrary_values(tmp_path, value):
    params = tmp_path / "pitun-params"
    _run_serializer(params, "TESTVAL", value)

    raw = _source_value(params, "TESTVAL")
    # Output is null-terminated; drop the trailing null.
    assert raw.endswith(b"\0"), raw
    got = raw[:-1].decode("utf-8")
    assert got == value


def test_params_set_updates_existing_key(tmp_path):
    params = tmp_path / "pitun-params"
    _run_serializer(params, "HOST", "first")
    _run_serializer(params, "HOST", "second")
    _run_serializer(params, "PORT", "1080")

    raw = _source_value(params, "HOST")
    assert raw[:-1].decode("utf-8") == "second"

    raw = _source_value(params, "PORT")
    assert raw[:-1].decode("utf-8") == "1080"


def test_old_double_quoted_params_still_load(tmp_path):
    """Files written by the pre-fix serializer must still source correctly."""
    params = tmp_path / "pitun-params"
    params.write_text(
        'SERVER_PUB_IP="192.0.2.5"\n'
        'DEFAULT_ALLOWED_IPS="0.0.0.0/0,::/0"\n'
        'OUTBOUND_SOCKS_USER="plain user"\n',
        encoding="utf-8",
    )

    assert _source_value(params, "SERVER_PUB_IP")[:-1] == b"192.0.2.5"
    assert _source_value(params, "DEFAULT_ALLOWED_IPS")[:-1] == b"0.0.0.0/0,::/0"
    assert _source_value(params, "OUTBOUND_SOCKS_USER")[:-1] == b"plain user"


def test_no_command_execution_on_source(tmp_path):
    """A value that looks like a command substitution must not run on load."""
    params = tmp_path / "pitun-params"
    marker = tmp_path / "pitun-should-not-run"
    _run_serializer(params, "PASS", f"$(touch {shlex.quote(marker.as_posix())})")
    _source_value(params, "PASS")
    assert not marker.exists()


def test_serializer_does_not_log_secrets(tmp_path):
    """The serializer block must not emit anything to stdout/stderr."""
    path = tmp_path / "params"
    result = subprocess.run(
        [sys.executable, "-", str(path), "SECRET", "hunter2"],
        input=_extract_params_set_code().encode("utf-8"),
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout == b""
    assert b"hunter2" not in result.stderr


def test_setup_script_does_not_log_socks_credentials():
    """No log/warn/info line in the script should print SOCKS_USER/SOCKS_PASS."""
    text = SETUP_SCRIPT.read_text(encoding="utf-8")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(("log ", "warn ", "info ")):
            continue
        assert "SOCKS_PASS" not in stripped, f"script logs a secret: {line}"
        assert "SOCKS_USER" not in stripped, f"script logs a secret: {line}"


# ── remove-outbound-socks wg-quick restart behavior ──────────────────────────


def _build_test_script(tmp_path: Path) -> Path:
    """Return a copy of the setup script with root/OS checks and /etc paths replaced."""
    text = SETUP_SCRIPT.read_text(encoding="utf-8")
    text = re.sub(
        r'\[\[ \$EUID -ne 0 \]\] && err "Run as root: sudo bash \$0 <sub-command>"\n',
        "",
        text,
    )
    text = re.sub(
        r'# ── OS detection.*?esac\n',
        'OS_ID="debian"\nOS_VER="12"\nOS_MAJOR="12"\n',
        text,
        count=1,
        flags=re.DOTALL,
    )

    params = tmp_path / "pitun-params"
    conf = tmp_path / "wg0.conf"
    unit = tmp_path / "pitun-sing-box.service"
    conf_dir = tmp_path / "sing-box"
    outbound = conf_dir / "pitun-outbound.json"
    rpfilter = tmp_path / "rpfilter.conf"

    text = text.replace(
        'WG_PARAMS="/etc/wireguard/pitun-params"',
        f'WG_PARAMS="{params.as_posix()}"',
    )
    text = text.replace(
        'WG_CONF="/etc/wireguard/${WG_IF}.conf"',
        f'WG_CONF="{conf.as_posix()}"',
    )
    text = text.replace(
        'SINGBOX_UNIT="/etc/systemd/system/pitun-sing-box.service"',
        f'SINGBOX_UNIT="{unit.as_posix()}"',
    )
    text = text.replace(
        'SINGBOX_CONF_DIR="/etc/sing-box"',
        f'SINGBOX_CONF_DIR="{conf_dir.as_posix()}"',
    )
    text = text.replace(
        'SINGBOX_CONF="${SINGBOX_CONF_DIR}/pitun-outbound.json"',
        f'SINGBOX_CONF="{outbound.as_posix()}"',
    )
    text = text.replace(
        'RPFILTER_DROPIN="/etc/sysctl.d/99-pitun-socks-rpfilter.conf"',
        f'RPFILTER_DROPIN="{rpfilter.as_posix()}"',
    )

    script = tmp_path / "setup-wireguard-test.sh"
    script.write_text(text, encoding="utf-8")
    return script


def _make_fake_bin(tmp_path: Path, log_path: Path) -> Path:
    """Create a PATH directory with mocked systemctl/ip/sysctl/python3."""
    bindir = tmp_path / "bin"
    bindir.mkdir()

    systemctl = bindir / "systemctl"
    systemctl.write_text(
        '#!/bin/bash\n'
        'case "$1" in\n'
        '  is-active)\n'
        '    [[ "${PITUN_WG_ACTIVE:-inactive}" == "active" ]] && exit 0 || exit 3\n'
        '    ;;\n'
        '  restart)\n'
        '    echo "$@" >> "$PITUN_SYSTEMCTL_LOG"\n'
        '    [[ "${PITUN_RESTART_FAIL:-0}" == "1" ]] && exit 1 || exit 0\n'
        '    ;;\n'
        '  *)\n'
        '    exit 0\n'
        '    ;;\n'
        'esac\n',
        encoding="utf-8",
    )
    systemctl.chmod(0o755)

    for name in ("ip", "sysctl", "python3"):
        exe = bindir / name
        if name == "python3":
            exe.write_text('#!/bin/bash\nexec python "$@"\n', encoding="utf-8")
        else:
            exe.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
        exe.chmod(0o755)

    return bindir


def _run_remove_outbound_socks(tmp_path: Path, active: str = "inactive", restart_fail: str = "0"):
    script = _build_test_script(tmp_path)
    log = tmp_path / "systemctl.log"
    bindir = _make_fake_bin(tmp_path, log)

    params = tmp_path / "pitun-params"
    conf = tmp_path / "wg0.conf"
    params.write_text("# test params\n", encoding="utf-8")
    conf.write_text("# test conf\n", encoding="utf-8")

    env = os.environ.copy()
    env["PATH"] = f"{bindir.as_posix()}{os.pathsep}{env.get('PATH', '')}"
    env["PITUN_SYSTEMCTL_LOG"] = log.as_posix()
    env["PITUN_WG_ACTIVE"] = active
    env["PITUN_RESTART_FAIL"] = restart_fail

    result = subprocess.run(
        [BASH, str(script), "remove-outbound-socks"],
        capture_output=True,
        text=True,
        env=env,
    )
    return result, log, params


def test_remove_outbound_socks_skips_restart_when_wg_quick_inactive(tmp_path):
    """Disabling outbound SOCKS must succeed when wg-quick is already stopped."""
    result, log, params = _run_remove_outbound_socks(tmp_path, active="inactive")
    assert result.returncode == 0, result.stderr
    assert "OUTBOUND_SOCKS=disabled" in result.stdout
    log_text = log.read_text(encoding="utf-8") if log.exists() else ""
    assert "restart wg-quick@wg0" not in log_text
    # Route/file cleanup and params clearing still happened.
    assert "OUTBOUND_SOCKS_HOST=''" in params.read_text(encoding="utf-8")


def test_remove_outbound_socks_restarts_active_wg_quick(tmp_path):
    """An active wg-quick must be restarted so direct egress takes effect."""
    result, log, _ = _run_remove_outbound_socks(tmp_path, active="active")
    assert result.returncode == 0, result.stderr
    assert "OUTBOUND_SOCKS=disabled" in result.stdout
    assert "restart wg-quick@wg0" in log.read_text(encoding="utf-8")


def test_remove_outbound_socks_fails_when_active_restart_fails(tmp_path):
    """A restart failure of an active service must remain a real error."""
    result, log, _ = _run_remove_outbound_socks(
        tmp_path, active="active", restart_fail="1"
    )
    assert result.returncode != 0
    assert "restart wg-quick@wg0" in log.read_text(encoding="utf-8")
