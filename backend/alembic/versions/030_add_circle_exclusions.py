"""Add nodecircle.excluded_node_ids / excluded_countries.

A subscription-linked circle re-added every node its subscription served on
each refresh, so nodes the operator had unticked came back. These two JSON
lists remember what must stay out.

Revision ID: 030
Revises: 029
Create Date: 2026-11-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "030"
down_revision: Union[str, None] = "029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("nodecircle") as batch:
        batch.add_column(sa.Column("excluded_node_ids", sa.String(), nullable=False, server_default="[]"))
        batch.add_column(sa.Column("excluded_countries", sa.String(), nullable=False, server_default="[]"))


def downgrade() -> None:
    with op.batch_alter_table("nodecircle") as batch:
        batch.drop_column("excluded_countries")
        batch.drop_column("excluded_node_ids")
