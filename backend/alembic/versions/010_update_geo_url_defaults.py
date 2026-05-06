"""Update geoip_mmdb_url default to canonical git.io shorturl.

Existing PiTun installs were seeded with the P3TERX MaxMind mirror:
  P3TERX/GeoLite.mmdb/.../GeoLite2-Country.mmdb

We're switching the default to the canonical short URL used across the
v2ray ecosystem:
  https://git.io/GeoLite2-Country.mmdb

GitHub froze new git.io shortlinks in 2022 but existing redirects
(including this one) still resolve to a current GeoLite2 release asset.

Note: GeoSite default is intentionally left at Loyalsoldier and NOT
switched to v2fly upstream. Loyalsoldier provides shortcut categories
(`geosite:ru`, `geosite:!ru`) that upstream v2fly does not expose, and
PiTun's primary audience writes routing rules using those shortcuts.
Switching would silently break their rules.

This migration only updates rows whose value EXACTLY matches the old
default — installations where the user customised the URL via the
GeoData → "Default URLs" UI keep their custom value untouched.

Revision ID: 010
Revises: 009
Create Date: 2026-05-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "010"
down_revision: Union[str, None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_OLD_MMDB = "https://github.com/P3TERX/GeoLite.mmdb/releases/latest/download/GeoLite2-Country.mmdb"
_NEW_MMDB = "https://git.io/GeoLite2-Country.mmdb"


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE settings SET value = :new "
            "WHERE key = 'geoip_mmdb_url' AND value = :old"
        ),
        {"old": _OLD_MMDB, "new": _NEW_MMDB},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "UPDATE settings SET value = :old "
            "WHERE key = 'geoip_mmdb_url' AND value = :new"
        ),
        {"old": _OLD_MMDB, "new": _NEW_MMDB},
    )
