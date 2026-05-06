"""Add server_deployment table.

Persists "what credentials did the user pick last time they generated a
NaiveProxy install script for this server", so:
  - Re-opening the script generator pre-fills with last values
  - The auto-generated naive password isn't lost when the modal closes
  - One-click "Create Node from this deployment" pre-populates a Node
    row with the right host / user / password

Unique on (server_id, protocol) — one deployment plan per protocol per
server. Re-saving updates the existing row.

Revision ID: 009
Revises: 008
Create Date: 2026-05-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "009"
down_revision: Union[str, None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "serverdeployment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("protocol", sa.String(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="configured"),
        sa.Column("last_node_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["server_id"], ["server.id"],
            name="fk_deployment_server_id",
            ondelete="CASCADE",  # delete server → drop its deployments
        ),
        sa.ForeignKeyConstraint(
            ["last_node_id"], ["node.id"],
            name="fk_deployment_last_node_id",
            ondelete="SET NULL",  # delete node → keep deployment, drop link
        ),
        sa.UniqueConstraint("server_id", "protocol", name="uq_server_protocol"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_serverdeployment_server_id",
        "serverdeployment",
        ["server_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_serverdeployment_server_id", table_name="serverdeployment")
    op.drop_table("serverdeployment")
