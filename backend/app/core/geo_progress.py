"""Live progress tracking for geo-data downloads (since v1.3.0-beta.6).

The previous flow accepted `POST /api/geodata/update`, kicked off a
background task, and gave the user nothing else — no progress, no
errors visible, just a static "Download queued" toast. If the HTTPS
fetch hung or the upstream returned 404, the user had to dig in
backend logs to find out.

This module exposes a small in-process state singleton that the
downloader updates per chunk. The frontend polls
`GET /api/geodata/update/progress` ~2 Hz while a job is in flight to
render real progress bars + per-stage status + per-file errors.

Why polling instead of WS/SSE
-----------------------------
A single ~10 MB HTTP fetch with 4 known stages (queued → downloading
→ verifying → done) doesn't justify a WS connection: setup cost +
nginx upgrade headers + reconnect handling >> a 500 ms `fetch()`. The
poll endpoint also degrades gracefully on flaky LAN — a missed sample
is harmless.

Concurrency
-----------
Backend is single-threaded asyncio; the state object is mutated only
from the event loop. No locks needed. Multiple concurrent geo-update
requests are NOT supported by the API layer (`_do_update` is the
only writer); a second `POST /api/geodata/update` while one is in
flight will overwrite the state — by design, since the file replace
is atomic and a fresh job is more useful than a half-stale view.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# Per-file lifecycle. `applying` covers the post-download xray reload
# + tag-cache refresh; it's brief but observable on a Pi 4 (~50-100ms
# for the protobuf parse on 5-10 MB files).
FileStage = str  # one of: queued | downloading | verifying | applying | done | failed


@dataclass
class FileProgress:
    """One row of the progress view — one of geoip / geosite / mmdb."""
    stage: FileStage = "queued"
    # Bytes / total. `total` may be None when the upstream omits
    # Content-Length (rare, but possible behind some CDNs); the UI
    # falls back to a striped indeterminate bar in that case.
    bytes_downloaded: int = 0
    bytes_total: Optional[int] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    # User-facing error message — short string, NOT a stack trace.
    # Cleared on success. The full stack is also written to backend
    # logs so the maintainer can diagnose without the user repro'ing.
    error: Optional[str] = None
    # Source URL used for THIS attempt. Helpful when the user
    # overrides the default in the form and gets a 404 — they need
    # to know which URL was actually fetched.
    source_url: Optional[str] = None


@dataclass
class GeoUpdateState:
    """The single 'latest job' state. `job_id` flips on every new
    `POST /update`, so the frontend can detect job rotation and
    reset its view.

    `files` is keyed by short name ('geoip' / 'geosite' / 'mmdb').
    Only files that were requested in this job are present — a
    geosite-only update doesn't pad the dict with an empty geoip
    row, so the UI naturally shows just the bars that matter.
    """
    job_id: str = ""
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    files: Dict[str, FileProgress] = field(default_factory=dict)
    # Set after the post-download tag-cache refresh succeeds. Lets
    # the frontend show a "Reloaded into xray" badge to confirm the
    # download actually took effect (not just sitting on disk).
    tag_cache_refreshed: bool = False

    @property
    def active(self) -> bool:
        """True while at least one file is still downloading or being
        applied. The frontend polls only while active to keep the
        request rate low at idle."""
        return self.started_at is not None and self.finished_at is None


# Module-level singleton. `_state` is the only public reference; all
# accessors below mutate it. We deliberately don't expose it via a
# class wrapper — there's only ever one job's state in flight, so
# the indirection would be noise.
_state = GeoUpdateState()


def get_state() -> GeoUpdateState:
    """Read-only access for the API layer."""
    return _state


def start_job(files_to_update: List[str]) -> str:
    """Begin a fresh job — wipe previous state and seed `queued` rows
    for each requested file. Returns the new `job_id` for log
    correlation. Caller is responsible for invoking `set_stage` /
    `update_bytes` / `set_error` / `mark_done` / `finish` as the
    work progresses.
    """
    global _state
    now = time.monotonic()
    _state = GeoUpdateState(
        job_id=uuid.uuid4().hex[:12],
        started_at=now,
        files={name: FileProgress(stage="queued") for name in files_to_update},
    )
    return _state.job_id


def set_stage(name: str, stage: FileStage, *, source_url: Optional[str] = None) -> None:
    """Transition `name` (geoip / geosite / mmdb) to a new stage.
    Sets `started_at` on first non-queued transition. Idempotent —
    re-setting the same stage is a no-op."""
    f = _state.files.get(name)
    if f is None:
        return
    if f.stage == stage:
        return
    f.stage = stage
    if stage == "downloading" and f.started_at is None:
        f.started_at = time.monotonic()
    if source_url and not f.source_url:
        f.source_url = source_url


def update_bytes(name: str, downloaded: int, total: Optional[int]) -> None:
    """Per-chunk progress callback. `total` may be None on first
    call if Content-Length wasn't present; the UI falls back to an
    indeterminate bar in that case."""
    f = _state.files.get(name)
    if f is None:
        return
    f.bytes_downloaded = downloaded
    if total is not None:
        f.bytes_total = total


def set_error(name: str, error: str) -> None:
    """Mark a single file as failed. Other files in the same job
    continue independently — a 404 on geoip.dat shouldn't abort the
    geosite.dat download."""
    f = _state.files.get(name)
    if f is None:
        return
    f.stage = "failed"
    # Keep the message under 300 chars — long stack traces should
    # stay in backend logs, not the in-memory state polled by the UI.
    f.error = (error or "unknown error")[:300]
    f.finished_at = time.monotonic()


def mark_done(name: str) -> None:
    """Mark a single file as successfully downloaded + applied."""
    f = _state.files.get(name)
    if f is None:
        return
    f.stage = "done"
    f.finished_at = time.monotonic()


def mark_tag_cache_refreshed() -> None:
    _state.tag_cache_refreshed = True


def finish() -> None:
    """Mark the entire job as finished. The frontend's polling loop
    keeps running for one more cycle so it can capture the final
    state, then stops because `active` flips to False."""
    _state.finished_at = time.monotonic()
