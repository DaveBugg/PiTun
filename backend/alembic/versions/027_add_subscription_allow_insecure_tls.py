"""Add subscription.allow_insecure_tls.

A subscription fetch used to skip certificate validation unconditionally. It
now validates by default; subscriptions that already exist keep working as
they did (the flag is switched on for them), new ones are verified unless the
operator ticks "Allow self-signed certificate".

Revision ID: 027
Revises: 026
Create Date: 2026-10-14
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "027"
down_revision: Union[str, None] = "026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "subscription",
        sa.Column("allow_insecure_tls", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.execute("UPDATE subscription SET allow_insecure_tls = 1")


def downgrade() -> None:
    with op.batch_alter_table("subscription") as batch:
        batch.drop_column("allow_insecure_tls")
