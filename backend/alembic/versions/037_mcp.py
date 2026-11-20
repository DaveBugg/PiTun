"""MCP server: access tokens, conversations opened by MCP clients; facts the
model saved wait for confirmation.

Revision ID: 037
Revises: 036
Create Date: 2026-11-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "037"
down_revision: Union[str, None] = "036"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mcptoken",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("prefix", sa.String(), nullable=False, server_default=""),
        sa.Column("scope", sa.String(), nullable=False, server_default="read"),
        sa.Column("created_by", sa.String(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_mcptoken_token_hash", "mcptoken", ["token_hash"])
    with op.batch_alter_table("assistantconversation") as b:
        b.add_column(sa.Column("source", sa.String(), nullable=False, server_default="chat"))
        b.add_column(sa.Column("mcp_token_id", sa.Integer(), nullable=True))
    with op.batch_alter_table("assistantfact") as b:
        b.add_column(sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade() -> None:
    with op.batch_alter_table("assistantfact") as b:
        b.drop_column("confirmed")
    with op.batch_alter_table("assistantconversation") as b:
        b.drop_column("mcp_token_id")
        b.drop_column("source")
    op.drop_index("ix_mcptoken_token_hash", table_name="mcptoken")
    op.drop_table("mcptoken")
