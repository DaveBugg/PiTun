"""Add nodecircle.subscription_id — auto-sync a circle from a subscription.

Linking a circle to a subscription makes every refresh of that subscription
rebuild the circle's membership: nodes the panel still serves are kept/added,
dropped ones removed, and hand-added members (or ones from a different
subscription) preserved.

NULL default keeps every existing circle manually managed, which is exactly
how they behaved before this migration.

The model declares `foreign_key="subscription.id"` (same convention as
`node.subscription_id`), but this migration adds a plain column: SQLite can't
attach a constraint via ALTER TABLE, and re-creating the table for it buys
nothing here — a stale link is harmless (the sync path simply finds no
subscription and leaves the circle alone) and the app layer already clears
circle references when nodes disappear.

Revision ID: 023
Revises: 022
Create Date: 2026-08-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "023"
down_revision: Union[str, None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("nodecircle", sa.Column("subscription_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("nodecircle", "subscription_id")
