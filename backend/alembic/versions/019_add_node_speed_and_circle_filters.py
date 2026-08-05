"""Add per-node speed test columns + NodeCircle candidate filters.

`node.speed_mbps` / `node.speed_tested_at` cache the last measured
throughput so it survives a backend restart (the UI greys out a reading
older than 6h, and NodeCircle "best"/min_speed read it).

`nodecircle.max_latency_ms` / `min_speed_mbps` are candidate filters
(0 = disabled), and `mode` gains a "best" value handled in code — no
schema change needed for the mode string itself.

Revision ID: 019
Revises: 018
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("node", sa.Column("speed_mbps", sa.Float(), nullable=True))
    op.add_column("node", sa.Column("speed_tested_at", sa.DateTime(), nullable=True))
    op.add_column(
        "nodecircle",
        sa.Column("max_latency_ms", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "nodecircle",
        sa.Column("min_speed_mbps", sa.Float(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("nodecircle", "min_speed_mbps")
    op.drop_column("nodecircle", "max_latency_ms")
    op.drop_column("node", "speed_tested_at")
    op.drop_column("node", "speed_mbps")
