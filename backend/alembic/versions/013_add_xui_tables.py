"""Add xuiserver + xuiclient tables (v1.3.0-beta.7).

PiTun-side bookkeeping for x-ui-pro / 3x-ui panels:

  * **XuiServer** — one row per panel deployed on a Server. Holds the
    Bearer api_token + panel admin user/pass + the random port + URL
    base path the panel sits on. Separate from `Server` because a VPS
    can have an x-ui panel OR be just an SSH target without one.

  * **XuiClient** — one row per panel-side client config that PiTun
    explicitly created (identified by the `pi-XXXXXXXX` label format).
    Mirrors the `DeploymentClient` pattern from WireGuard: caches
    enough state to render the client + export it to a Node later
    without re-asking the panel.

Why no `XuiInbound` table? Inbound CRUD is always live-fetched via the
panel API — caching adds drift handling without enough payoff at
beta.7's scope. Chains (Phase 6) will reference inbounds by `(server_id,
remote_inbound_id)` directly. Promotion to a table is an additive
migration if needed later.

Revision ID: 013
Revises: 012
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "xuiserver",
        sa.Column("id", sa.Integer(), nullable=False),
        # FK to Server. Cascade delete: removing a Server (which
        # implicitly removes its naive/wg deployments) takes the xui
        # row with it. Re-deploys go through the existing
        # `/deploy` endpoint which UPDATEs the row in place.
        sa.Column("server_id", sa.Integer(), nullable=False),
        # Panel access — Bearer is the runtime auth, user/pass are
        # for the human admin to log into the web panel directly.
        sa.Column("api_token", sa.String(), nullable=False),
        sa.Column("panel_user", sa.String(), nullable=False),
        sa.Column("panel_pass", sa.String(), nullable=False),
        # Endpoint reachability. Host comes from the parent Server
        # row; here we hold the random port + URL base path the
        # panel listens on.
        sa.Column("panel_port", sa.Integer(), nullable=False),
        # Always starts with "/", no trailing "/". Normalised in
        # xui_uri.parse_xui_uri before insert.
        sa.Column("panel_basepath", sa.String(), nullable=False),
        # x-ui-pro mode → real domain w/ Let's Encrypt cert; bare
        # mode → empty string, panel uses self-signed.
        sa.Column("domain", sa.String(), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="bare"),
        # Health markers. last_check_error is "Unable to reach panel"
        # / "Bearer rejected" / etc. — surfaces in the Servers UI as
        # a red badge so the admin notices a panel that's drifted.
        sa.Column("last_check", sa.DateTime(), nullable=True),
        sa.Column("last_check_error", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["server_id"], ["server.id"], ondelete="CASCADE",
        ),
        sa.UniqueConstraint("server_id", name="uq_xuiserver_server"),
    )
    op.create_index(
        "ix_xuiserver_server_id", "xuiserver", ["server_id"], unique=False,
    )

    op.create_table(
        "xuiclient",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("xui_server_id", sa.Integer(), nullable=False),
        # Panel-side identifiers used for /addClient + /delClient calls.
        sa.Column("inbound_remote_id", sa.Integer(), nullable=False),
        # Per-client UUID for vless/trojan inbounds. socks5 inbounds
        # store user/pass pairs instead of UUIDs — for those rows this
        # field carries the username, and config_json holds the
        # password.
        sa.Column("client_uuid", sa.String(), nullable=False, server_default=""),
        # `pi-XXXXXXXX` label written into the panel's email field at
        # creation. Used to distinguish PiTun-managed clients from
        # hand-added ones during /sync. Indexed because /list-clients
        # filters on it.
        sa.Column("label", sa.String(), nullable=False),
        # Cached metadata for fast UI rendering — refreshed on /sync.
        sa.Column(
            "inbound_protocol", sa.String(length=32),
            nullable=False, server_default="",
        ),
        sa.Column(
            "inbound_port", sa.Integer(),
            nullable=False, server_default="0",
        ),
        sa.Column(
            "inbound_remark", sa.String(),
            nullable=False, server_default="",
        ),
        # Full client config blob (uuid/flow/sni/pbk/sid for vless+
        # reality, password/method for trojan, ...). JSON. Used by
        # "show config" + "export to Node" without panel round-trip.
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        # ON DELETE SET NULL — deleting the Node leaves this client
        # row standing (with exported_node_id=None), preserving panel-
        # side bookkeeping even if the routing layer has dropped it.
        sa.Column("exported_node_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["xui_server_id"], ["xuiserver.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["exported_node_id"], ["node.id"], ondelete="SET NULL",
        ),
    )
    op.create_index(
        "ix_xuiclient_xui_server_id", "xuiclient", ["xui_server_id"], unique=False,
    )
    op.create_index(
        "ix_xuiclient_inbound_remote_id",
        "xuiclient", ["inbound_remote_id"], unique=False,
    )
    op.create_index(
        "ix_xuiclient_label", "xuiclient", ["label"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_xuiclient_label", table_name="xuiclient")
    op.drop_index("ix_xuiclient_inbound_remote_id", table_name="xuiclient")
    op.drop_index("ix_xuiclient_xui_server_id", table_name="xuiclient")
    op.drop_table("xuiclient")
    op.drop_index("ix_xuiserver_server_id", table_name="xuiserver")
    op.drop_table("xuiserver")
