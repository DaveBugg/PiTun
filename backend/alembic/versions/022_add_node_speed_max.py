"""Add node.speed_max_mbps (peak from the last speed test).

The unified speed measurement now records both the average (after warm-up,
`speed_mbps`) and the peak steady window (`speed_max_mbps`) from the same
run — manual button, "speed all", and the background auto-check sweep.

Revision ID: 022
Revises: 021
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "022"
down_revision: Union[str, None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("node", sa.Column("speed_max_mbps", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("node", "speed_max_mbps")
