"""Add event table for the Recent Events feed on Dashboard.

Stores user-facing notifications about background state transitions
(failover, sidecar restart, geo update, circle rotation, etc.) so the
UI can surface them without forcing the user into Docker logs.

Trim policy (managed by app/core/events.py): keep last 7 days OR last
1000 rows, whichever is smaller — same shape as `dns_logger`.

Revision ID: 006
Revises: 005
Create Date: 2026-04-29
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "event",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("timestamp", sa.DateTime, nullable=False),
        # Dotted code, e.g. "failover.switched". Kept as free text so we
        # can introduce new categories without a migration each time.
        sa.Column("category", sa.String(length=64), nullable=False),
        # "info" | "warning" | "error" — colors the row in the UI.
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        # Optional longer text or JSON-as-string (e.g. exception message).
        sa.Column("details", sa.String(length=1000), nullable=True),
        # Optional id of the entity this event refers to (node id, circle
        # id, subscription id, …). No FK — events outlive deletions.
        sa.Column("entity_id", sa.Integer, nullable=True),
    )
    op.create_index("ix_event_timestamp", "event", ["timestamp"])
    op.create_index("ix_event_category", "event", ["category"])
    op.create_index("ix_event_entity_id", "event", ["entity_id"])


def downgrade() -> None:
    op.drop_index("ix_event_entity_id", "event")
    op.drop_index("ix_event_category", "event")
    op.drop_index("ix_event_timestamp", "event")
    op.drop_table("event")
