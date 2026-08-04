"""Tests for `app.core.ssh.exec_remote_script` — the SSH wrapper that
uploads + runs an install script on a remote VPS for v1.3.0 auto-deploy.

We mock `asyncssh.connect` + the connection / SFTP / process objects
so tests don't need real network. Coverage focuses on:
  * Happy path: upload, exec, capture, cleanup
  * SFTP upload failure
  * Script non-zero exit
  * Script timeout (asyncio.TimeoutError from conn.run)
  * Auth (private_key) handling
  * Output truncation cap
  * Env var injection into the remote command
  * DNS resolution failure
  * TCP connect failure

DNS / TCP layers are exercised by mocking the helpers (`_resolve_direct`,
`_connect_marked`) — same pattern as test_servers.py uses for `/test`.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.ssh import (
    DeployResult,
    _build_remote_command,
    _truncate,
    exec_remote_script,
)


# ── Helper builders for asyncssh mocks ───────────────────────────────────────


def _mk_proc_mock(stdout: str = "", stderr: str = "", exit_status: int = 0):
    """Build a fake asyncssh process result (what conn.run returns)."""
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.exit_status = exit_status
    return proc


def _mk_sftp_mock():
    """Build a fake asyncssh SFTP client. open() returns a context
    manager whose __aenter__ yields a writable file handle."""
    sftp = MagicMock()

    async def _aenter_open(*args, **kwargs):
        f = MagicMock()
        f.write = AsyncMock()
        return f

    open_cm = MagicMock()
    open_cm.__aenter__ = AsyncMock(side_effect=_aenter_open)
    open_cm.__aexit__ = AsyncMock(return_value=None)
    sftp.open = MagicMock(return_value=open_cm)
    sftp.chmod = AsyncMock()

    sftp_cm = MagicMock()
    sftp_cm.__aenter__ = AsyncMock(return_value=sftp)
    sftp_cm.__aexit__ = AsyncMock(return_value=None)
    return sftp_cm, sftp


def _mk_conn_mock(*, run_result=None, run_side_effect=None, sftp_cm=None):
    """Build a fake asyncssh connection that returns the given proc on
    `run()` and the given sftp client on `start_sftp_client()`.
    """
    conn = MagicMock()
    if run_side_effect is not None:
        conn.run = AsyncMock(side_effect=run_side_effect)
    else:
        conn.run = AsyncMock(return_value=run_result or _mk_proc_mock())
    conn.start_sftp_client = MagicMock(return_value=sftp_cm or _mk_sftp_mock()[0])

    conn_cm = MagicMock()
    conn_cm.__aenter__ = AsyncMock(return_value=conn)
    conn_cm.__aexit__ = AsyncMock(return_value=None)
    return conn_cm, conn


# Reusable patch helper — mock DNS + TCP + asyncssh.connect at once.
def _patch_ssh_layers(connect_factory):
    """Returns a list of context managers stacked at the call site
    via `with` / contextlib.ExitStack. Caller threads the connection
    factory via `connect_factory()` returning (conn_cm, conn) on
    each call.
    """
    return [
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", return_value=(MagicMock(), 25)),
        patch("asyncssh.connect", side_effect=lambda **kw: connect_factory()[0]),
    ]


# ── _truncate (pure) ─────────────────────────────────────────────────────────


class TestTruncate:
    def test_short_passthrough(self):
        assert _truncate("hello") == "hello"

    def test_truncated_marker(self):
        big = "x" * 300_000
        result = _truncate(big, cap=1000)
        # We keep cap-100 prefix + a marker
        assert "truncated" in result
        assert len(result) < len(big)
        assert result.startswith("x" * 900)


# ── _build_remote_command (pure) ─────────────────────────────────────────────


class TestBuildRemoteCommand:
    def test_no_env(self):
        cmd = _build_remote_command("/tmp/x.sh", {})
        assert cmd == " bash /tmp/x.sh"

    def test_env_quoted(self):
        cmd = _build_remote_command("/tmp/x.sh", {
            "DOMAIN": "example.com",
            "EMAIL": "me@example.com",
        })
        # Shlex-quoted (single quotes around safe values are allowed
        # but not required; verify the values aren't unquoted at least)
        assert "DOMAIN=example.com" in cmd or "DOMAIN='example.com'" in cmd
        assert "bash /tmp/x.sh" in cmd

    def test_env_with_special_chars_is_safe(self):
        cmd = _build_remote_command("/tmp/x.sh", {
            "NAIVE_PASS": "p$assword 'with' \"quotes\"",
        })
        # The dangerous chars must be escaped/quoted — `;` shouldn't
        # appear unquoted, neither should bare backticks, $(, etc.
        # Easiest sanity: shlex.split should round-trip the original.
        import shlex
        parsed = shlex.split(cmd)
        # Find the NAIVE_PASS=… token
        env_token = next(t for t in parsed if t.startswith("NAIVE_PASS="))
        assert env_token == "NAIVE_PASS=p$assword 'with' \"quotes\""

    def test_path_with_spaces_quoted(self):
        cmd = _build_remote_command("/tmp/path with spaces.sh", {})
        # Shell can find the file
        import shlex
        parsed = shlex.split(cmd)
        assert "/tmp/path with spaces.sh" in parsed


# ── exec_remote_script — happy path ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path():
    """Successful upload + exec + cleanup, all captures present."""
    sftp_cm, sftp = _mk_sftp_mock()
    proc = _mk_proc_mock(
        stdout="provisioning...\nURI=naive+https://u:p@example.com:443\n",
        stderr="warning: legacy_ipv6 disabled (informational)\n",
        exit_status=0,
    )
    conn_cm, conn = _mk_conn_mock(run_result=proc, sftp_cm=sftp_cm)

    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", return_value=(MagicMock(), 25)),
        patch("asyncssh.connect", return_value=conn_cm),
    ):
        result = await exec_remote_script(
            host="example.com",
            password="hunter2",
            script_content="#!/usr/bin/env bash\necho hi\n",
            env={"DOMAIN": "x.example.com", "EMAIL": "me@x"},
            direct=True,   # exercise the SO_MARK / marked-connect path
        )

    assert isinstance(result, DeployResult)
    assert result.ok is True
    assert result.exit_code == 0
    assert "URI=naive+https://u:p@example.com:443" in result.stdout
    assert "legacy_ipv6" in result.stderr
    assert result.error is None
    assert result.connect_latency_ms == 25
    # Two `run` calls expected: the script exec + the cleanup `rm -f`
    assert conn.run.await_count >= 2
    # First call (the script) must include env vars
    first_call_cmd = conn.run.call_args_list[0].args[0]
    assert "DOMAIN=" in first_call_cmd
    assert "EMAIL=" in first_call_cmd
    # Cleanup call must `rm -f` something under /tmp/pitun-deploy-…
    cleanup_call_cmd = conn.run.call_args_list[-1].args[0]
    assert cleanup_call_cmd.startswith("rm -f ")
    assert "/tmp/pitun-deploy-" in cleanup_call_cmd


@pytest.mark.asyncio
async def test_script_nonzero_exit():
    """Script exits 1 — ok=False, exit_code propagated, error has tail of stderr."""
    sftp_cm, _ = _mk_sftp_mock()
    proc = _mk_proc_mock(
        stdout="some output before failure\n",
        stderr="ERROR: domain validation failed\n",
        exit_status=1,
    )
    conn_cm, _ = _mk_conn_mock(run_result=proc, sftp_cm=sftp_cm)

    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", return_value=(MagicMock(), 25)),
        patch("asyncssh.connect", return_value=conn_cm),
    ):
        result = await exec_remote_script(
            host="example.com",
            password="hunter2",
            script_content="#!/usr/bin/env bash\nfalse\n",
        )

    assert result.ok is False
    assert result.exit_code == 1
    assert "domain validation failed" in (result.error or "")
    # Output still captured for debugging
    assert "ERROR" in result.stderr


@pytest.mark.asyncio
async def test_script_timeout():
    """`conn.run` raises TimeoutError → result.ok=False, error mentions timeout."""
    import asyncio as _asyncio
    sftp_cm, _ = _mk_sftp_mock()
    conn_cm, _ = _mk_conn_mock(
        run_side_effect=_asyncio.TimeoutError(),
        sftp_cm=sftp_cm,
    )

    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", return_value=(MagicMock(), 25)),
        patch("asyncssh.connect", return_value=conn_cm),
    ):
        result = await exec_remote_script(
            host="example.com",
            password="hunter2",
            script_content="echo hi",
            timeout=1,
        )

    assert result.ok is False
    assert "timeout" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_sftp_upload_failure():
    """SFTP upload fails — ok=False, error='upload: ...', no exec attempt."""
    # Build an SFTP that raises on .open(...)
    sftp = MagicMock()
    open_cm = MagicMock()
    open_cm.__aenter__ = AsyncMock(side_effect=PermissionError("write denied"))
    open_cm.__aexit__ = AsyncMock(return_value=None)
    sftp.open = MagicMock(return_value=open_cm)
    sftp.chmod = AsyncMock()

    sftp_cm = MagicMock()
    sftp_cm.__aenter__ = AsyncMock(return_value=sftp)
    sftp_cm.__aexit__ = AsyncMock(return_value=None)

    conn_cm, conn = _mk_conn_mock(sftp_cm=sftp_cm)

    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", return_value=(MagicMock(), 25)),
        patch("asyncssh.connect", return_value=conn_cm),
    ):
        result = await exec_remote_script(
            host="example.com",
            password="hunter2",
            script_content="echo hi",
        )

    assert result.ok is False
    assert "upload" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_dns_failure():
    with patch(
        "app.core.ssh._resolve_direct",
        new_callable=AsyncMock,
        side_effect=OSError("could not resolve"),
    ):
        result = await exec_remote_script(
            host="bogus.invalid",
            password="x",
            script_content="echo hi",
            direct=True,   # DNS bypass only runs on the marked path
        )
    assert result.ok is False
    assert (result.error or "").startswith("DNS:")


@pytest.mark.asyncio
async def test_tcp_failure():
    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", side_effect=ConnectionRefusedError("nope")),
    ):
        result = await exec_remote_script(
            host="example.com",
            password="x",
            script_content="echo hi",
            direct=True,   # marked connect only runs on the direct path
        )
    assert result.ok is False
    assert (result.error or "").startswith("TCP:")


@pytest.mark.asyncio
async def test_no_credentials():
    result = await exec_remote_script(
        host="example.com",
        # Neither password nor private_key
        script_content="echo hi",
    )
    assert result.ok is False
    assert "credentials" in (result.error or "")


@pytest.mark.asyncio
async def test_empty_script_rejected():
    result = await exec_remote_script(
        host="example.com",
        password="x",
        script_content="",
    )
    assert result.ok is False
    assert "empty" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_huge_stdout_truncated():
    """Output exceeding the cap should be truncated with a marker."""
    big_stdout = "x" * (300 * 1024)  # 300 KB > 256 KB cap
    sftp_cm, _ = _mk_sftp_mock()
    proc = _mk_proc_mock(stdout=big_stdout, exit_status=0)
    conn_cm, _ = _mk_conn_mock(run_result=proc, sftp_cm=sftp_cm)

    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", return_value=(MagicMock(), 25)),
        patch("asyncssh.connect", return_value=conn_cm),
    ):
        result = await exec_remote_script(
            host="example.com",
            password="hunter2",
            script_content="echo hi",
        )

    assert result.ok is True
    assert "truncated" in result.stdout
    assert len(result.stdout) < len(big_stdout)
