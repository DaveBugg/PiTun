"""Server-tasks API — list / detail / cancel / WS stream for `Job` rows
(since v1.3.0-beta.1, Phase 2.2).

Routes mounted under `/api/server-tasks` (with the dash matching the URL
the user picked: `/server-tasks` instead of the Pythonic `/server_tasks`).

  GET   /server-tasks                          — list (paginated, filterable)
  GET   /server-tasks/{job_id}                 — single, full row
  POST  /server-tasks/{job_id}/cancel          — cancel a running job
  WS    /server-tasks/{job_id}/stream          — live stdout/stderr feed

The WS handler authenticates via `?token=<jwt>` query parameter — same
pattern as `api/logs.py:stream_logs` since browsers can't set
Authorization headers on WebSockets.

Frame format (text frames, JSON):
  {"event": "log", "kind": "stdout"|"stderr", "line": "..."}
  {"event": "done", "status": "succeeded"|"failed"|"cancelled"}

Each subscriber gets the FULL backlog (up to ~2000 lines) on connect,
then live updates as the runner produces them. After finalization we
emit `done` and close.
"""
from __future__ import annotations

import json
import logging
from typing import List, Optional

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
)

from app.core.jobs import job_manager
from app.schemas import JobListResponse, JobRead, JobSummaryRead

logger = logging.getLogger(__name__)

# Path uses the dash form (`/server-tasks`) because the user picked
# that URL in the v1.3.0 design. Pythonic underscored module name is
# fine — only the prefix is user-visible.
#
# Two routers: REST goes through the auth-gated mount in main.py; the
# WS endpoint lives on a separate router that skips the global Bearer
# dependency (browsers can't set Authorization on WebSockets) and
# validates `?token=<jwt>` itself. Same split as how api/logs.py is
# mounted independently.
router = APIRouter(prefix="/server-tasks", tags=["server-tasks"])
ws_router = APIRouter(prefix="/server-tasks", tags=["server-tasks"])


# ── Helpers ──────────────────────────────────────────────────────────────────

def _summary_to_read(s) -> JobSummaryRead:
    """Project a `core.jobs.JobSummary` dataclass to the API model."""
    return JobSummaryRead(
        id=s.id,
        kind=s.kind,
        target_id=s.target_id,
        target_name=s.target_name,
        protocol=s.protocol,
        status=s.status,
        started_at=s.started_at,
        finished_at=s.finished_at,
        error=s.error,
        result=s.result,
    )


def _row_to_read(row) -> JobRead:
    """Project a `models.Job` row to the full `JobRead` shape, unpacking
    the JSON columns. Used by `GET /{job_id}`.
    """
    result_obj: Optional[dict] = None
    if row.result_json:
        try:
            result_obj = json.loads(row.result_json)
        except Exception:  # noqa: BLE001
            result_obj = None

    config_obj: Optional[dict] = None
    if row.config_json:
        try:
            config_obj = json.loads(row.config_json)
            # Defence in depth — strip any plain-text secret keys an
            # earlier release might have stored. naive_pass is the
            # canonical example: lives on the Node URI, never wanted
            # in API responses.
            if isinstance(config_obj, dict):
                for k in ("naive_pass", "password", "private_key", "passphrase"):
                    config_obj.pop(k, None)
        except Exception:  # noqa: BLE001
            config_obj = None

    return JobRead(
        id=row.id,
        kind=row.kind,
        target_id=row.target_id,
        target_name=row.target_name,
        protocol=row.protocol,
        status=row.status,
        started_at=row.started_at,
        finished_at=row.finished_at,
        error=row.error,
        result=result_obj,
        log_tail=row.log_tail,
        config=config_obj,
    )


# ── List ─────────────────────────────────────────────────────────────────────


@router.get("", response_model=JobListResponse)
async def list_server_tasks(
    server_id: Optional[int] = Query(
        None, description="Filter to jobs targeting this server (Job.target_id)"
    ),
    kind: Optional[str] = Query(
        None, description="Filter by job kind (currently only 'deploy')"
    ),
    status: Optional[str] = Query(
        None,
        description="Filter by status: running | succeeded | failed | cancelled",
    ),
    limit: int = Query(50, ge=1, le=200, description="Max rows per page"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
):
    """Paginated list of jobs, newest first. All filters are optional;
    omitting them returns the global feed.

    The `server_id` query param is the user-facing alias for
    `Job.target_id` — keeps the URL semantic ("show me jobs for THIS
    server") rather than leaking the generic-job internal column name.
    """
    summaries = await job_manager.list_jobs(
        target_id=server_id,
        kind=kind,
        status=status,
        limit=limit,
        offset=offset,
    )
    return JobListResponse(jobs=[_summary_to_read(s) for s in summaries])


# ── Detail ───────────────────────────────────────────────────────────────────


@router.get("/{job_id}", response_model=JobRead)
async def get_server_task(job_id: str):
    row = await job_manager.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _row_to_read(row)


# ── Cancel ───────────────────────────────────────────────────────────────────


@router.post("/{job_id}/cancel", status_code=202)
async def cancel_server_task(job_id: str):
    """Cancel a running job. Idempotent-ish: returns 404 only if the
    job_id doesn't exist; for an already-finished job we still return
    202 + `{cancelled: false}` so the client doesn't have to special-case
    a race where the deploy finished between the user's click and our
    receipt.

    Important: this cancels the LOCAL coroutine pumping output. The
    install script keeps running on the remote VPS — see
    `core.jobs.JobManager.cancel` docstring for rationale.
    """
    row = await job_manager.get_job(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    cancelled = await job_manager.cancel(job_id)
    return {"job_id": job_id, "cancelled": cancelled}


# ── WS: live log stream ──────────────────────────────────────────────────────


@ws_router.websocket("/{job_id}/stream")
async def stream_server_task(websocket: WebSocket, job_id: str, token: str = Query(default="")):
    """Live log stream for a job.

    Protocol:
      * Client connects with `?token=<jwt>` (browser WS can't set headers)
      * Server emits text frames with JSON payloads:
          {"event": "log", "kind": "stdout"|"stderr", "line": "..."}
          {"event": "done", "status": "succeeded"|"failed"|"cancelled"}
      * Server closes after `done` or on disconnect.

    On unknown / already-drained job_id, we accept the socket, emit a
    single `done` frame with the persisted status (or `unknown`), and
    close. The frontend can then fall back to the polling endpoint to
    render the captured log_tail.
    """
    # Validate JWT — same flow as api/logs.py:stream_logs
    if not token:
        await websocket.close(code=4001, reason="Missing token")
        return
    try:
        from app.core.auth import verify_token
        verify_token(token)
    except Exception:
        await websocket.close(code=4001, reason="Invalid token")
        return

    await websocket.accept()
    # `%r` on user-controlled `job_id` — CWE-117 hardening. See the
    # matching note in `api/nodes.py`: root logger's `_NoNewlineFilter`
    # already neutralises \n/\r, the `%r` adds repr()-quoting that
    # CodeQL recognises as a log sanitiser.
    logger.debug("Server-tasks stream client connected: job=%r", job_id)

    try:
        # If the job's RAM buffer is gone (already drained past grace
        # period or never existed), `subscribe()` yields nothing — we
        # still want to emit a `done` frame with whatever DB row we
        # have so the client knows there's nothing more to wait for.
        had_lines = False
        async for entry in job_manager.subscribe(job_id):
            had_lines = True
            kind, line = entry
            try:
                await websocket.send_text(
                    json.dumps({"event": "log", "kind": kind, "line": line})
                )
            except Exception:  # noqa: BLE001
                # Client disappeared — break out, finally block sends
                # the close.
                return

        # Either the job finalized or the buffer was already gone.
        # Resolve the persisted status from DB and emit done.
        row = await job_manager.get_job(job_id)
        final_status = row.status if row is not None else "unknown"
        try:
            await websocket.send_text(
                json.dumps({"event": "done", "status": final_status})
            )
        except Exception:  # noqa: BLE001
            pass
    except WebSocketDisconnect:
        logger.debug("Server-tasks stream client disconnected: job=%r", job_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Server-tasks stream error for job=%r: %s", job_id, exc)
    finally:
        try:
            await websocket.close()
        except Exception:  # noqa: BLE001
            pass
        logger.debug("Server-tasks stream closed: job=%r", job_id)
