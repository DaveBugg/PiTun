"""Add NaiveProxy fields to node table.

Revision ID: 005
Revises: 004
Create Date: 2026-04-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # internal_port: loopback SOCKS port of the naive sidecar container
    # (nullable — only set once a naive node is created)
    op.add_column("node", sa.Column("internal_port", sa.Integer, nullable=True))

    # naive_padding: HTTP/2 padding obfuscation flag (default on)
    # Existing rows get the default via server_default, then we can drop the
    # server_default — ORM handles it for new rows.
    op.add_column(
        "node",
        sa.Column(
            "naive_padding",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column("node", "naive_padding")
    op.drop_column("node", "internal_port")
