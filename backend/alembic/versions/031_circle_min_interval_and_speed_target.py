"""Circle intervals of at least 5 minutes, and a 50 Mbit/s speed target by default.

"best" circles now speed-test the nodes with the best ping before switching, so
they must not rotate every minute, and a target of 0 (the old "no limit") would
silently turn the check off for circles that never set one. Existing circles are
brought in line: interval_min < 5 becomes 5 (interval_max kept >= interval_min),
and a "best" circle with no target gets 50. A circle can still opt out by setting
the target to 0 after this.

Revision ID: 031
Revises: 030
Create Date: 2026-11-03
"""
from typing import Sequence, Union

from alembic import op


revision: str = "031"
down_revision: Union[str, None] = "030"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE nodecircle SET interval_min = 5 WHERE interval_min < 5")
    op.execute("UPDATE nodecircle SET interval_max = interval_min WHERE interval_max < interval_min")
    op.execute("UPDATE nodecircle SET min_speed_mbps = 50 WHERE mode = 'best' AND (min_speed_mbps IS NULL OR min_speed_mbps = 0)")


def downgrade() -> None:
    pass
