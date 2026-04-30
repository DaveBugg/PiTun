"""Add device table for LAN device management.

Revision ID: 002
Revises: 001
Create Date: 2026-04-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "device",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("mac", sa.String, unique=True, nullable=False),
        sa.Column("ip", sa.String, nullable=True),
        sa.Column("hostname", sa.String, nullable=True),
        sa.Column("name", sa.String, nullable=True),
        sa.Column("vendor", sa.String, nullable=True),
        sa.Column("first_seen", sa.DateTime, nullable=False),
        sa.Column("last_seen", sa.DateTime, nullable=False),
        sa.Column("is_online", sa.Boolean, default=True),
        sa.Column("routing_policy", sa.String, default="default"),
    )
    op.create_index("ix_device_mac", "device", ["mac"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_device_mac", "device")
    op.drop_table("device")
