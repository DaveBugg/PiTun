"""Add account-lockout columns to user.

`user.failed_attempts` counts consecutive failed logins; once it crosses
the threshold, `user.lock_until` is set to a future time and logins are
rejected until it passes. Both reset on a successful login. Defence in
depth on top of the rate-limit middleware + Turnstile.

Revision ID: 020
Revises: 019
Create Date: 2026-08-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "020"
down_revision: Union[str, None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("user", sa.Column("lock_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("user", "lock_until")
    op.drop_column("user", "failed_attempts")
