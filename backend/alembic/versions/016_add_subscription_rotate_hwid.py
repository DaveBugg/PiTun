"""Add `rotate_hwid` column to subscription.

When True, the next refresh generates a fresh random X-Hwid header
for this subscription instead of using the machine-id-derived stable
HWID. Lets the operator clear a panel-side HWID throttle without
touching the URL or UA preset.

Default False — most panels device-bind on first-seen HWID, rotating
unexpectedly would silently break working subscriptions.

Revision ID: 016
Revises: 015
Create Date: 2026-05-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("subscription") as batch:
        batch.add_column(
            sa.Column(
                "rotate_hwid",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
    # SQLite's batch_alter_table sometimes leaves the column NULL for
    # pre-existing rows even with `server_default=false`. Belt-and-
    # braces backfill keeps the column consistent (model treats NULL
    # as False at the Python level anyway, but cleaner DB state means
    # the UI never sees an indeterminate checkbox).
    op.execute("UPDATE subscription SET rotate_hwid = 0 WHERE rotate_hwid IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("subscription") as batch:
        batch.drop_column("rotate_hwid")
