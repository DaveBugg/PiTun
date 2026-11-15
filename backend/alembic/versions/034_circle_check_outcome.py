"""NodeCircle: last scheduled check + its outcome, and "rotate even when healthy".

Revision ID: 034
Revises: 033
Create Date: 2026-11-15
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "034"
down_revision: Union[str, None] = "033"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("nodecircle") as batch:
        batch.add_column(sa.Column("rotate_when_healthy", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
        batch.add_column(sa.Column("last_checked", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("last_check_result", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("nodecircle") as batch:
        batch.drop_column("last_check_result")
        batch.drop_column("last_checked")
        batch.drop_column("rotate_when_healthy")
