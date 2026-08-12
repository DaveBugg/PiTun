"""Add device.dhcp_reserved_ip — explicit DHCP reservations for router mode.

A reservation has to be its own field rather than reusing `device.ip`, which
is whatever ARP scanning last observed. Turning an observation into a
reservation would silently pin whichever address the device happened to hold —
possibly one outside the DHCP pool we're about to serve, or one that belonged
to a different device yesterday. The operator says which addresses are fixed.

NULL (the default, and what every existing row gets) means "no reservation" —
the device takes whatever the pool offers, exactly as before.

Revision ID: 024
Revises: 023
Create Date: 2026-08-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "024"
down_revision: Union[str, None] = "023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("device", sa.Column("dhcp_reserved_ip", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("device", "dhcp_reserved_ip")
