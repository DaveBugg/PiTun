"""Add node.pinned_cert_sha256.

xray removed `allowInsecure` (a hard error when true) and replaced it with
`pinnedPeerCertSha256`. The node now carries the SHA-256 of the certificate to
trust, so a self-signed server is accepted by pinning it instead of by turning
verification off.

Revision ID: 028
Revises: 027
Create Date: 2026-11-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "028"
down_revision: Union[str, None] = "027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("node", sa.Column("pinned_cert_sha256", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("node") as batch:
        batch.drop_column("pinned_cert_sha256")
