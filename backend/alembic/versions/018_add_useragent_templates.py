"""Add UserAgentTemplate table, seeded with the built-in UA presets.

The presets are keyed by the same slugs already stored in
`subscription.ua` and carry the same User-Agent strings, so every
existing subscription resolves to an identical request after the
upgrade — the rows are simply editable now.

`subscription.ua` stays a plain string rather than a foreign key: a
dangling key falls back to `core/ua_templates.BUILTIN_UA_MAP`, whereas an
FK would either block the delete or cascade into wiping subscriptions.

Revision ID: 018
Revises: 017
Create Date: 2026-07-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "018"
down_revision: Union[str, None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Inlined rather than imported from `app.core.ua_templates`: a migration
# is a historical snapshot, and `app/` and `alembic/` are separate bind
# mounts, so an import would crash-loop the container on a deploy that
# updated one before the other. `TestMigrationSeedData` pins these against
# the runtime copy so the two cannot drift.
_HAPP_NOTE = (
    "X-Device-* / X-Hwid headers are added automatically to match this profile."
)

SEED_ROWS = [
    {"key": "v2ray", "name": "v2rayN", "user_agent": "v2rayN/6.60",
     "headers": "{}", "builtin": True, "order": 10,
     "description": "Most panels serve a base64 URI list to this UA. Safe default."},
    {"key": "clash", "name": "Clash.Meta", "user_agent": "clash.meta/1.18.0",
     "headers": "{}", "builtin": True, "order": 20,
     "description": "Panels serve Clash YAML. PiTun parses the proxies list out of it."},
    {"key": "sing-box", "name": "sing-box", "user_agent": "sing-box/1.8.0",
     "headers": "{}", "builtin": True, "order": 30,
     "description": "Panels serve a sing-box JSON config."},
    {"key": "happ", "name": "Happ (iOS)",
     "user_agent": "Happ/2.7.0/ios/17.4/iPhone15,2",
     "headers": "{}", "builtin": True, "order": 40, "description": _HAPP_NOTE},
    {"key": "happ-android", "name": "Happ (Android)",
     "user_agent": "Happ/2.7.0/android/14/Pixel 8",
     "headers": "{}", "builtin": True, "order": 50, "description": _HAPP_NOTE},
    {"key": "happ-windows", "name": "Happ (Windows)",
     "user_agent": "Happ/2.7.0/windows/11_10.0.26200/DESKTOP-PiTun_x86_64",
     "headers": "{}", "builtin": True, "order": 60, "description": _HAPP_NOTE},
    {"key": "happ-macos", "name": "Happ (macOS)",
     "user_agent": "Happ/2.7.0/macos/14.4/Mac15,7",
     "headers": "{}", "builtin": True, "order": 70, "description": _HAPP_NOTE},
    {"key": "streisand", "name": "Streisand", "user_agent": "Streisand/3.0",
     "headers": "{}", "builtin": True, "order": 80,
     "description": "Gets past some CDN client filters that reject generic UAs."},
    {"key": "chrome", "name": "Chrome (desktop)",
     "user_agent": (
         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
     ),
     "headers": "{}", "builtin": True, "order": 90,
     "description": "Full browser UA. For panels behind a strict CDN bot check."},
]


def upgrade() -> None:
    templates = op.create_table(
        "useragenttemplate",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=False, server_default=""),
        sa.Column("headers", sa.String(), nullable=False, server_default="{}"),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column(
            "builtin", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("order", sa.Integer(), nullable=False, server_default="100"),
        sa.UniqueConstraint("key", name="uq_useragenttemplate_key"),
    )
    op.create_index("ix_useragenttemplate_key", "useragenttemplate", ["key"])

    op.bulk_insert(templates, SEED_ROWS)


def downgrade() -> None:
    op.drop_index("ix_useragenttemplate_key", table_name="useragenttemplate")
    op.drop_table("useragenttemplate")
