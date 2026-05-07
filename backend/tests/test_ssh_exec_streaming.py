"""Tests for `app.core.ssh.exec_remote_script_streaming` — the
streaming SSH wrapper used by `core.jobs.JobManager` to fan out
stdout/stderr lines to the live log buffer + WS subscribers as they
arrive (since v1.3.0).

Coverage:
  * Happy path — every output line is delivered via on_line in order
  * stdout + stderr interleave correctly
  * on_line tolerates sync vs async callables
  * on_line errors do not abort the deploy
  * Timeout terminates process + returns ok=False
  * Upload failure → ok=False before exec
  * DNS / TCP / no-creds — same paths as the synchronous variant
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.ssh import DeployResult, exec_remote_script_streaming


# ── Test helpers — mocking asyncssh's create_process model ───────────────────


class _MockStream:
    """Async iterator that yields a fixed list of chunks, then EOFs.
    Mimics asyncssh's stdout/stderr stream interface (which yields
    chunks decoded to str)."""
    def __init__(self, chunks: list[str]):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _mk_proc_mock(
    *,
    stdout_chunks: list[str] | None = None,
    stderr_chunks: list[str] | None = None,
    exit_status: int = 0,
    wait_delay: float = 0.0,
):
    """Build a fake asyncssh process for streaming tests."""
    proc = MagicMock()
    proc.stdout = _MockStream(stdout_chunks or [])
    proc.stderr = _MockStream(stderr_chunks or [])
    proc.exit_status = exit_status

    async def _wait():
        if wait_delay:
            await asyncio.sleep(wait_delay)
    proc.wait = _wait
    proc.terminate = MagicMock()
    return proc


def _mk_sftp_mock():
    """Successful SFTP upload."""
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
    return sftp_cm


def _mk_conn_mock(*, proc, sftp_cm):
    conn = MagicMock()
    conn.run = AsyncMock()  # for cleanup `rm -f`
    conn.start_sftp_client = MagicMock(return_value=sftp_cm)
    conn.create_process = AsyncMock(return_value=proc)

    conn_cm = MagicMock()
    conn_cm.__aenter__ = AsyncMock(return_value=conn)
    conn_cm.__aexit__ = AsyncMock(return_value=None)
    return conn_cm, conn


# ── Happy path ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_streaming_yields_lines_in_order():
    proc = _mk_proc_mock(
        stdout_chunks=["line-1\nline-2\n", "line-3\n", "URI=naive+https://x:y@h:443\n"],
        stderr_chunks=[],
        exit_status=0,
    )
    sftp = _mk_sftp_mock()
    conn_cm, conn = _mk_conn_mock(proc=proc, sftp_cm=sftp)

    received = []

    async def on_line(kind, line):
        received.append((kind, line))

    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", return_value=(MagicMock(), 25)),
        patch("asyncssh.connect", return_value=conn_cm),
    ):
        result = await exec_remote_script_streaming(
            host="example.com",
            password="pw",
            script_content="#!/bin/bash\necho hi\n",
            on_line=on_line,
        )

    assert result.ok is True
    assert result.exit_code == 0
    # All 4 stdout lines arrived in order via on_line
    assert ("stdout", "line-1") in received
    assert ("stdout", "line-2") in received
    assert ("stdout", "line-3") in received
    assert ("stdout", "URI=naive+https://x:y@h:443") in received
    # Sequence preserved
    assert received[0][1] == "line-1"
    assert received[-1][1] == "URI=naive+https://x:y@h:443"
    # full stdout also captured for return-value consumers
    assert "URI=naive+https://x:y@h:443" in result.stdout


@pytest.mark.asyncio
async def test_streaming_interleaves_stdout_stderr():
    proc = _mk_proc_mock(
        stdout_chunks=["a\nb\n"],
        stderr_chunks=["warn-1\nwarn-2\n"],
        exit_status=0,
    )
    sftp = _mk_sftp_mock()
    conn_cm, _ = _mk_conn_mock(proc=proc, sftp_cm=sftp)

    received = []

    async def on_line(kind, line):
        received.append((kind, line))

    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", return_value=(MagicMock(), 25)),
        patch("asyncssh.connect", return_value=conn_cm),
    ):
        result = await exec_remote_script_streaming(
            host="example.com",
            password="pw",
            script_content="echo hi",
            on_line=on_line,
        )

    assert result.ok is True
    kinds = {e[0] for e in received}
    assert kinds == {"stdout", "stderr"}
    # Both streams' lines all delivered
    stdout_lines = {e[1] for e in received if e[0] == "stdout"}
    stderr_lines = {e[1] for e in received if e[0] == "stderr"}
    assert stdout_lines == {"a", "b"}
    assert stderr_lines == {"warn-1", "warn-2"}


# ── on_line callable tolerance ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_streaming_accepts_sync_callable():
    """on_line that returns None (sync function) must be supported —
    tests inject simple lambdas, production uses async.
    """
    proc = _mk_proc_mock(stdout_chunks=["a\nb\n"])
    sftp = _mk_sftp_mock()
    conn_cm, _ = _mk_conn_mock(proc=proc, sftp_cm=sftp)

    received = []

    def sync_on_line(kind, line):
        received.append((kind, line))

    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", return_value=(MagicMock(), 25)),
        patch("asyncssh.connect", return_value=conn_cm),
    ):
        result = await exec_remote_script_streaming(
            host="example.com", password="pw",
            script_content="echo hi",
            on_line=sync_on_line,
        )
    assert result.ok is True
    assert ("stdout", "a") in received
    assert ("stdout", "b") in received


@pytest.mark.asyncio
async def test_streaming_callback_errors_dont_abort_deploy():
    """A buggy on_line (e.g. WS subscriber crashed mid-stream) must
    not take down the actual deploy — log + continue."""
    proc = _mk_proc_mock(stdout_chunks=["a\nb\nc\n"])
    sftp = _mk_sftp_mock()
    conn_cm, _ = _mk_conn_mock(proc=proc, sftp_cm=sftp)

    call_count = {"n": 0}

    async def buggy(kind, line):
        call_count["n"] += 1
        if call_count["n"] == 2:
            raise RuntimeError("subscriber blew up")

    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", return_value=(MagicMock(), 25)),
        patch("asyncssh.connect", return_value=conn_cm),
    ):
        result = await exec_remote_script_streaming(
            host="example.com", password="pw",
            script_content="echo hi",
            on_line=buggy,
        )

    assert result.ok is True
    # Despite the crash on line 2, all 3 lines were processed
    assert call_count["n"] == 3
    # And full stdout still captured for return value
    assert "a" in result.stdout
    assert "c" in result.stdout


# ── Timeout / failure paths ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_streaming_script_nonzero_exit():
    proc = _mk_proc_mock(
        stdout_chunks=["starting...\n"],
        stderr_chunks=["ERROR: boom\n"],
        exit_status=1,
    )
    sftp = _mk_sftp_mock()
    conn_cm, _ = _mk_conn_mock(proc=proc, sftp_cm=sftp)

    received = []
    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", return_value=(MagicMock(), 25)),
        patch("asyncssh.connect", return_value=conn_cm),
    ):
        result = await exec_remote_script_streaming(
            host="example.com", password="pw",
            script_content="false",
            on_line=lambda k, l: received.append((k, l)),
        )

    assert result.ok is False
    assert result.exit_code == 1
    assert "boom" in (result.error or "")
    # stderr line still streamed via on_line
    assert ("stderr", "ERROR: boom") in received


@pytest.mark.asyncio
async def test_streaming_timeout_terminates_process():
    # wait_delay > timeout → asyncio.wait_for cancels gather → terminate()
    proc = _mk_proc_mock(stdout_chunks=["hung..."], wait_delay=10.0)
    sftp = _mk_sftp_mock()
    conn_cm, _ = _mk_conn_mock(proc=proc, sftp_cm=sftp)

    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", return_value=(MagicMock(), 25)),
        patch("asyncssh.connect", return_value=conn_cm),
    ):
        result = await exec_remote_script_streaming(
            host="example.com", password="pw",
            script_content="echo hi",
            on_line=lambda k, l: None,
            timeout=0.2,
        )

    assert result.ok is False
    assert "timeout" in (result.error or "").lower()
    # Process termination attempted
    assert proc.terminate.called


@pytest.mark.asyncio
async def test_streaming_upload_failure_no_exec():
    """SFTP upload fails → exec never starts, ok=False."""
    sftp = MagicMock()
    open_cm = MagicMock()
    open_cm.__aenter__ = AsyncMock(side_effect=PermissionError("write denied"))
    open_cm.__aexit__ = AsyncMock(return_value=None)
    sftp.open = MagicMock(return_value=open_cm)
    sftp.chmod = AsyncMock()
    sftp_cm = MagicMock()
    sftp_cm.__aenter__ = AsyncMock(return_value=sftp)
    sftp_cm.__aexit__ = AsyncMock(return_value=None)

    conn_cm, conn = _mk_conn_mock(proc=_mk_proc_mock(), sftp_cm=sftp_cm)

    received = []
    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", return_value=(MagicMock(), 25)),
        patch("asyncssh.connect", return_value=conn_cm),
    ):
        result = await exec_remote_script_streaming(
            host="example.com", password="pw",
            script_content="echo hi",
            on_line=lambda k, l: received.append((k, l)),
        )

    assert result.ok is False
    assert "upload" in (result.error or "").lower()
    # create_process never reached
    conn.create_process.assert_not_called()
    # No on_line invocations because exec didn't start
    assert received == []


# ── Pre-flight failures (DNS / TCP / no creds / empty script) ────────────────


@pytest.mark.asyncio
async def test_streaming_no_credentials():
    result = await exec_remote_script_streaming(
        host="example.com",
        script_content="echo hi",
        on_line=lambda k, l: None,
    )
    assert result.ok is False
    assert "credentials" in (result.error or "")


@pytest.mark.asyncio
async def test_streaming_empty_script_rejected():
    result = await exec_remote_script_streaming(
        host="example.com", password="x",
        script_content="",
        on_line=lambda k, l: None,
    )
    assert result.ok is False
    assert "empty" in (result.error or "").lower()


@pytest.mark.asyncio
async def test_streaming_dns_failure():
    with patch(
        "app.core.ssh._resolve_direct",
        new_callable=AsyncMock,
        side_effect=OSError("could not resolve"),
    ):
        result = await exec_remote_script_streaming(
            host="bogus.invalid", password="x",
            script_content="echo hi",
            on_line=lambda k, l: None,
        )
    assert result.ok is False
    assert (result.error or "").startswith("DNS:")


@pytest.mark.asyncio
async def test_streaming_tcp_failure():
    with (
        patch("app.core.ssh._resolve_direct", new_callable=AsyncMock, return_value="1.2.3.4"),
        patch("app.core.ssh._connect_marked", side_effect=ConnectionRefusedError("nope")),
    ):
        result = await exec_remote_script_streaming(
            host="example.com", password="x",
            script_content="echo hi",
            on_line=lambda k, l: None,
        )
    assert result.ok is False
    assert (result.error or "").startswith("TCP:")
