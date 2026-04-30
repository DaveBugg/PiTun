"""Add last_error column to subscription table.

Revision ID: 004
Revises: 003
Create Date: 2026-04-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("subscription", sa.Column("last_error", sa.String, nullable=True))


def downgrade() -> None:
    op.drop_column("subscription", "last_error")
