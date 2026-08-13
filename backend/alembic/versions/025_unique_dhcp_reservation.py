"""Make device.dhcp_reserved_ip unique.

Two devices reserving the same address renders two `dhcp-host` lines for it,
which dnsmasq treats as fatal: it refuses to start, and the whole LAN loses
DHCP as leases expire. The panel is then reachable only from a client still
holding one.

SQLite treats NULLs as distinct in a unique index, so the unreserved majority
is unaffected — only two rows claiming the same address collide.

Any duplicates already present are cleared rather than the migration failing:
the operator is upgrading, not debugging, and the reservation is re-enterable
while an install that refuses to start is not. The lowest device id keeps the
address so one of the two stays as intended.

Revision ID: 025
Revises: 024
Create Date: 2026-08-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE device SET dhcp_reserved_ip = NULL
        WHERE dhcp_reserved_ip IS NOT NULL
          AND id NOT IN (
              SELECT MIN(id) FROM device
              WHERE dhcp_reserved_ip IS NOT NULL
              GROUP BY dhcp_reserved_ip
          )
        """
    )
    op.create_index(
        "ix_device_dhcp_reserved_ip",
        "device",
        ["dhcp_reserved_ip"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_device_dhcp_reserved_ip", table_name="device")
