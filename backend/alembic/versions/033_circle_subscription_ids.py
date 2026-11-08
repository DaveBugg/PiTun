"""Add nodecircle.subscription_ids (a circle can follow several subscriptions).

`subscription_id` stays as the first entry for older readers; existing links
are copied into the new list.

Revision ID: 033
Revises: 032
Create Date: 2026-11-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "033"
down_revision: Union[str, None] = "032"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("nodecircle") as batch:
        batch.add_column(sa.Column("subscription_ids", sa.String(), nullable=False, server_default="[]"))
    op.execute(
        "UPDATE nodecircle SET subscription_ids = '[' || subscription_id || ']' "
        "WHERE subscription_id IS NOT NULL"
    )


def downgrade() -> None:
    with op.batch_alter_table("nodecircle") as batch:
        batch.drop_column("subscription_ids")
