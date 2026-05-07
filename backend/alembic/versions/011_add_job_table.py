"""Add job table for the server-tasks subsystem (v1.3.0-beta.1).

Introduces a first-class persistent record for long-running async
operations — initially `kind='deploy'` (Phase 1 auto-deploy of naive
on a registered Server), extensible to other kinds (subscription
refresh, batch import, …) in later releases.

Hybrid persistence model:
  * This DB row holds the JOB METADATA (what kind, against which
    target, status, started/finished timestamps, error summary,
    final result + log tail) — small data, persists across backend
    restarts, queryable for `/server-tasks` UI page with filters.
  * Live stdout/stderr stream during a running job lives in RAM only
    (`core.jobs.JobManager._buffers`), capped at ~2000 lines. On
    completion, the LAST 4 KB of combined stdout+stderr is captured
    into `log_tail` so post-completion UI still has something to show.

`target_id` is nullable + denormalised `target_name` snapshot: the
Server may be renamed or deleted while the job's history outlives
it, and we want the UI to keep showing the human-readable label
without doing N+1 lookups.

Revision ID: 011
Revises: 010
Create Date: 2026-05-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job",
        # uuid hex (32 chars). String PK rather than autoincrement int
        # because jobs are referenced from URLs (/server-tasks/{id})
        # and external WS clients — guessable monotonic ids would
        # leak deploy frequency information.
        sa.Column("id", sa.String(), nullable=False),
        # Discriminator. Currently only "deploy"; "subscription_refresh"
        # / "circle_rotate" / etc. coming later. Index for `/server-tasks?kind=`.
        sa.Column("kind", sa.String(), nullable=False),
        # For deploy: server_id. Nullable for future global jobs
        # (e.g. "rotate all circles", "refresh all subscriptions").
        sa.Column("target_id", sa.Integer(), nullable=True),
        # Snapshot of the human-readable target label at job creation
        # time. Server may be renamed / deleted; this stays. UI uses
        # it to render "vps-frankfurt" without an N+1 join.
        sa.Column("target_name", sa.String(), nullable=True),
        # Protocol for deploy-kind jobs ("naive", later "xray-vless",
        # "hy2"). Nullable for future kinds where it doesn't apply.
        sa.Column("protocol", sa.String(), nullable=True),
        # State machine: running -> succeeded | failed | cancelled.
        # Stale `running` rows older than ~1h on backend startup are
        # healed to `failed` by `core.jobs.JobManager._heal_stale_jobs`.
        sa.Column("status", sa.String(), nullable=False, server_default="running"),
        # Original request body (for "Retry" button). For deploy-kind:
        # JSON-encoded {protocol, config: {domain, email, naive_user, naive_pass?}}.
        # naive_pass kept here — admin needs it to re-run with same
        # credentials. Not surfaced via API list; only via "Retry"
        # which re-issues internally.
        sa.Column("config_json", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        # One-line human summary of failure. None on success.
        sa.Column("error", sa.String(), nullable=True),
        # Final outcome JSON for successful runs. For deploy-kind:
        # {deployment_id, node_id, parsed_uri, exit_code, duration_sec}.
        sa.Column("result_json", sa.Text(), nullable=True),
        # Tail of stdout+stderr (last ~4 KB) captured at finalization.
        # NULL while running; populated on transition to terminal status.
        sa.Column("log_tail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    # Composite indexes for the two main UI query patterns:
    #   * filter by server: `WHERE target_id = ? ORDER BY started_at DESC`
    #   * filter by status: `WHERE status = ? ORDER BY started_at DESC`
    op.create_index("idx_job_target_started", "job", ["target_id", "started_at"])
    op.create_index("idx_job_status_started", "job", ["status", "started_at"])


def downgrade() -> None:
    op.drop_index("idx_job_status_started", table_name="job")
    op.drop_index("idx_job_target_started", table_name="job")
    op.drop_table("job")
