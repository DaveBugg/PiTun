"""Add node.hy2_hop_ports / hy2_hop_interval (Hysteria2 port hopping).

Revision ID: 032
Revises: 031
Create Date: 2026-11-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "032"
down_revision: Union[str, None] = "031"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("node") as batch:
        batch.add_column(sa.Column("hy2_hop_ports", sa.String(), nullable=True))
        batch.add_column(sa.Column("hy2_hop_interval", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("node") as batch:
        batch.drop_column("hy2_hop_interval")
        batch.drop_column("hy2_hop_ports")
