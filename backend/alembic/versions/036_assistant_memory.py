"""AI assistant: facts, conversation summary, plan undo.

Revision ID: 036
Revises: 035
Create Date: 2026-11-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "036"
down_revision: Union[str, None] = "035"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assistantfact",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("assistantconversation") as b:
        b.add_column(sa.Column("summary", sa.String(), nullable=False, server_default=""))
        b.add_column(sa.Column("summary_upto", sa.Integer(), nullable=False, server_default="0"))
    with op.batch_alter_table("assistantplan") as b:
        b.add_column(sa.Column("undo", sa.String(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("assistantplan") as b:
        b.drop_column("undo")
    with op.batch_alter_table("assistantconversation") as b:
        b.drop_column("summary_upto")
        b.drop_column("summary")
    op.drop_table("assistantfact")
