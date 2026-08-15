"""Add node.country / exit_ip / exit_checked_at — the observed exit identity.

The country flag used to be derived from the node's `address`, which answers
only for a literal IP: a hostname needed DNS (unavailable inside the write
flush) and a chained node reported its entry hop rather than where its traffic
actually surfaces. The speed test already opens a tunnel to the internet, so
it now reads the exit address back through it and stores what it saw.

Revision ID: 026
Revises: 025
Create Date: 2026-08-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "026"
down_revision: Union[str, None] = "025"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("node", sa.Column("country", sa.String(length=2), nullable=True))
    op.add_column("node", sa.Column("exit_ip", sa.String(), nullable=True))
    op.add_column("node", sa.Column("exit_checked_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("node", "exit_checked_at")
    op.drop_column("node", "exit_ip")
    op.drop_column("node", "country")
