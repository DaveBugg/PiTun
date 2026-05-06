"""Add server table + node.server_id link.

Introduces the Servers feature: a catalogue of SSH-reachable VPS instances
that the user manages from PiTun. Each server can be referenced by zero or
more nodes (1:N) — the link is purely informational and survives an empty
delete via ON DELETE SET NULL.

Plain-text credential storage is intentional and matches existing Node
password storage. Threat model is documented in SECURITY.md (LAN-only
deployment, do not expose to public internet).

Revision ID: 008
Revises: 007
Create Date: 2026-05-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "server",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("host", sa.String(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False, server_default="22"),
        sa.Column("user", sa.String(), nullable=False, server_default="root"),
        sa.Column("auth_type", sa.String(), nullable=False, server_default="password"),
        sa.Column("password", sa.String(), nullable=True),
        sa.Column("private_key", sa.Text(), nullable=True),
        sa.Column("passphrase", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="unknown"),
        sa.Column("last_check", sa.DateTime(), nullable=True),
        sa.Column("last_check_error", sa.String(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # Add server_id FK to node table. Use batch_alter_table so SQLite (no
    # native ALTER) gets the table-rename + recreate dance for free.
    with op.batch_alter_table("node") as batch:
        batch.add_column(sa.Column("server_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_node_server_id",
            "server",
            ["server_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("node") as batch:
        batch.drop_constraint("fk_node_server_id", type_="foreignkey")
        batch.drop_column("server_id")
    op.drop_table("server")
