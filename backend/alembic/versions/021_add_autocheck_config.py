"""Add autocheckconfig table (background auto-speedtest sweep config).

Singleton (row id=1, created on demand by the API/scheduler). Holds the
enable flag, sweep interval, and scope (all / subscription / group / nodes)
for the periodic auto-speedtest that keeps node.speed_mbps fresh.

Revision ID: 021
Revises: 020
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "021"
down_revision: Union[str, None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "autocheckconfig",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("0")),
        sa.Column("interval_minutes", sa.Integer(), nullable=False, server_default="360"),
        sa.Column("scope_kind", sa.String(), nullable=False, server_default="all"),
        sa.Column("scope_value", sa.String(), nullable=False, server_default=""),
        sa.Column("last_sweep", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("autocheckconfig")
