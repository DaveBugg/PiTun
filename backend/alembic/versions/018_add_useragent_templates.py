"""Add UserAgentTemplate table and seed it with the former hardcoded presets.

Until v1.4.6 the subscription UA catalogue was two module-level dicts in
`app/api/subscriptions.py` — `_UA_MAP` (9 UA strings) and `_HAPP_PROFILES`
(the per-OS Happ device tuples). Adding a preset for a new panel, or
attaching an extra header the panel gates on, meant a code change and a
redeploy.

This migration moves the catalogue into `useragenttemplate` and seeds it
with **exactly** those nine presets, keyed by the same slugs already
stored in `subscription.ua`. So nothing changes behaviourally on upgrade:
every existing subscription resolves to the identical User-Agent it used
yesterday. The difference is that the rows are now editable — the whole
point of the change is that `happ`'s app version, or `chrome`'s Chrome
build number, can be bumped from the UI when a panel starts rejecting a
stale fingerprint.

`builtin=1` on the seeded rows is informational only (it drives a badge
in the UI and a louder delete confirmation). Built-ins are fully
editable AND deletable; nothing re-seeds them, because resurrecting a
row the operator deliberately deleted would be worse than an empty
dropdown. The runtime keeps a hardcoded fallback map for exactly that
case (`core/ua_templates.BUILTIN_UA_MAP`), so a deleted or renamed
template degrades to the old UA instead of breaking a refresh.

Not a foreign key: `subscription.ua` stays a plain string. A dangling
key has a well-defined fallback, whereas an FK would either block the
delete or cascade into wiping subscriptions.

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


# The seed rows are INLINED here rather than imported from
# `app.core.ua_templates`, for two reasons:
#
# 1. **A migration is a historical snapshot.** If someone later adds a
#    tenth preset to `DEFAULT_UA_TEMPLATES`, an import would make this
#    migration retroactively seed different data than it did for every
#    install that already ran it. Migrations must not move.
#
# 2. **Deploy safety.** `docker-compose.yml` bind-mounts `./backend/app`
#    and `./backend/alembic` as two separate volumes, and `entrypoint.sh`
#    runs `alembic upgrade head` with `MIGRATION_STRICT=1` before the app
#    starts. A hot-deploy that copies the new `alembic/` but not the new
#    `app/` would hit an ImportError here and put the container in a
#    crash loop. With no app import there is nothing to get out of sync.
#
# `tests/test_ua_templates.py::TestMigrationSeedData` asserts these stay
# byte-identical to `DEFAULT_UA_TEMPLATES`, so drift is a failing test
# rather than a silent difference between fresh and upgraded installs.
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
        # Stable slug referenced by `subscription.ua`.
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=False, server_default=""),
        # JSON object of extra request headers merged over the base set.
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
