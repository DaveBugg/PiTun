"""Extra SOCKS5 proxy endpoints (each is its own xray process).

Revision ID: 038
Revises: 037
Create Date: 2026-11-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "038"
down_revision: Union[str, None] = "037"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "proxyendpoint",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("socks_port", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False, server_default="global"),
        sa.Column("node_id", sa.Integer(), sa.ForeignKey("node.id"), nullable=True),
        sa.Column("balancer_id", sa.Integer(), sa.ForeignKey("balancergroup.id"), nullable=True),
        sa.Column("routing_set_id", sa.Integer(), sa.ForeignKey("routingset.id"), nullable=True),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_proxyendpoint_name", "proxyendpoint", ["name"], unique=True)
    op.create_index("ix_proxyendpoint_socks_port", "proxyendpoint", ["socks_port"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_proxyendpoint_socks_port", table_name="proxyendpoint")
    op.drop_index("ix_proxyendpoint_name", table_name="proxyendpoint")
    op.drop_table("proxyendpoint")
