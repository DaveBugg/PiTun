"""Add custom_ua column to subscription.

Lets each subscription override the User-Agent header with an arbitrary
string. Useful for panels that gate on a specific UA fingerprint we
don't have a preset for. When set, fully replaces the UA derived from
the `ua` preset key; X-* headers from `_get_happ_headers` are still
attached if the custom UA starts with "Happ/" so panels that pair UA
with X-headers don't break.

Revision ID: 007
Revises: 006
Create Date: 2026-04-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("subscription") as batch:
        batch.add_column(sa.Column("custom_ua", sa.String(length=512), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("subscription") as batch:
        batch.drop_column("custom_ua")
