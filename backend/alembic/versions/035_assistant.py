"""AI assistant: conversations, messages, plans.

Revision ID: 035
Revises: 034
Create Date: 2026-11-19
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "035"
down_revision: Union[str, None] = "034"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "assistantconversation",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False, server_default="New chat"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("redaction_map", sa.String(), nullable=False, server_default="{}"),
    )
    op.create_table(
        "assistantmessage",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("assistantconversation.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.String(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_assistantmessage_conversation_id", "assistantmessage", ["conversation_id"])
    op.create_table(
        "assistantplan",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("assistantconversation.id"), nullable=False),
        sa.Column("title", sa.String(), nullable=False, server_default=""),
        sa.Column("summary", sa.String(), nullable=False, server_default=""),
        sa.Column("steps", sa.String(), nullable=False, server_default="[]"),
        sa.Column("before", sa.String(), nullable=False, server_default="[]"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("results", sa.String(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
        sa.Column("decided_by", sa.String(), nullable=True),
    )
    op.create_index("ix_assistantplan_conversation_id", "assistantplan", ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_assistantplan_conversation_id", table_name="assistantplan")
    op.drop_table("assistantplan")
    op.drop_index("ix_assistantmessage_conversation_id", table_name="assistantmessage")
    op.drop_table("assistantmessage")
    op.drop_table("assistantconversation")
