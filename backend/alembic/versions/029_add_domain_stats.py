"""Add the domainstat table.

Opt-in per-domain counters (hits, first/last seen, recent public IPs) used by
the Domains page. Small by design: capped, aged out, clearable by hand.

Revision ID: 029
Revises: 028
Create Date: 2026-11-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "029"
down_revision: Union[str, None] = "028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "domainstat",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_seen", sa.DateTime(), nullable=False),
        sa.Column("last_seen", sa.DateTime(), nullable=False),
        sa.Column("ips", sa.String(), nullable=False, server_default="[]"),
    )
    op.create_index("ix_domainstat_domain", "domainstat", ["domain"], unique=True)
    op.create_index("ix_domainstat_last_seen", "domainstat", ["last_seen"])


def downgrade() -> None:
    op.drop_index("ix_domainstat_last_seen", table_name="domainstat")
    op.drop_index("ix_domainstat_domain", table_name="domainstat")
    op.drop_table("domainstat")
