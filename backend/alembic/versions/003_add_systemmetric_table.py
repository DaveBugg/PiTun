"""Add systemmetric table for monitoring dashboard.

Revision ID: 003
Revises: 002
Create Date: 2026-04-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "systemmetric",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("ts", sa.DateTime, nullable=False),
        sa.Column("cpu_percent", sa.Float, default=0.0),
        sa.Column("ram_used_mb", sa.Float, default=0.0),
        sa.Column("ram_total_mb", sa.Float, default=0.0),
        sa.Column("disk_used_gb", sa.Float, default=0.0),
        sa.Column("disk_total_gb", sa.Float, default=0.0),
        sa.Column("net_sent_bytes", sa.BigInteger, default=0),
        sa.Column("net_recv_bytes", sa.BigInteger, default=0),
    )
    op.create_index("ix_systemmetric_ts", "systemmetric", ["ts"])


def downgrade() -> None:
    op.drop_index("ix_systemmetric_ts", "systemmetric")
    op.drop_table("systemmetric")
