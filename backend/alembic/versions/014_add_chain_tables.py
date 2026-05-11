"""Add proxychain / chainchannel / chainclient / chainclientchannel tables
(v1.3.0-beta.7).

Models the two-hop chain pattern from the user's reference scripts
(setup-eu.sh + setup-relay.sh): one chain wires together an EU
**exit** panel and a RU **relay** panel. The chain has N **channels**,
each one a fully-isolated VLESS+Reality pipe with its own
crypto / SNI / port pair. A **chain client** is one logical user;
adding one spawns N panel-side clients (one per channel) so the
user gets N parallel VLESS URIs all routed through the same exit IP.

Why split (Chain → Channel → ClientChannel) instead of a flat table:
  * Chain owns the (exit_panel, relay_panel) tuple + the exit_sni
    that every channel shares.
  * Channel owns the per-pipe wire config (ports, UUIDs, Reality keys,
    client_sni). Variable N per chain (the user picks a free count).
  * ClientChannel is the M×N join between logical users and pipes —
    each row carries the panel-issued UUID for that specific
    (user, channel) pair + an optional Node FK after export.

Cascade semantics:
  * proxychain.exit_xui_server_id / relay_xui_server_id → xuiserver
    ON DELETE CASCADE. If a panel is unregistered, the chain row
    goes too (the chain isn't useful without both panels).
  * chainchannel.chain_id → proxychain ON DELETE CASCADE. Deleting
    the chain takes its channels.
  * chainclient.chain_id → proxychain ON DELETE CASCADE.
  * chainclientchannel.chain_client_id → chainclient ON DELETE CASCADE.
    chainclientchannel.channel_id → chainchannel ON DELETE CASCADE.
  * chainclientchannel.exported_node_id → node ON DELETE SET NULL.
    Removing the Node leaves the bookkeeping row pointing at None.

Revision ID: 014
Revises: 013
Create Date: 2026-05-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "014"
down_revision: Union[str, None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── proxychain ─────────────────────────────────────────────────────────
    op.create_table(
        "proxychain",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("exit_xui_server_id", sa.Integer(), nullable=False),
        sa.Column("relay_xui_server_id", sa.Integer(), nullable=False),
        sa.Column(
            "exit_sni", sa.String(),
            nullable=False, server_default="www.google.com",
        ),
        # State machine: pending → deployed → (degraded | failed). Kept
        # as a string for cheap migrations; the API layer constrains
        # the value set.
        sa.Column(
            "status", sa.String(length=16),
            nullable=False, server_default="pending",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["exit_xui_server_id"], ["xuiserver.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["relay_xui_server_id"], ["xuiserver.id"], ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_proxychain_exit_xui_server_id",
        "proxychain", ["exit_xui_server_id"],
    )
    op.create_index(
        "ix_proxychain_relay_xui_server_id",
        "proxychain", ["relay_xui_server_id"],
    )

    # ── chainchannel ───────────────────────────────────────────────────────
    op.create_table(
        "chainchannel",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        # Set after orchestration; 0 means "not yet created on panel".
        sa.Column(
            "exit_inbound_remote_id", sa.Integer(),
            nullable=False, server_default="0",
        ),
        sa.Column(
            "relay_inbound_remote_id", sa.Integer(),
            nullable=False, server_default="0",
        ),
        sa.Column("exit_port", sa.Integer(), nullable=False),
        sa.Column("relay_port", sa.Integer(), nullable=False),
        sa.Column(
            "exit_xhttp_path", sa.String(),
            nullable=False, server_default="/api/v1",
        ),
        sa.Column("client_sni", sa.String(), nullable=False),
        # Reality material captured at create time so re-pushing the
        # xrayTemplateConfig after a panel restart is deterministic.
        # See chain orchestrator for the wire-level explanation.
        sa.Column("exit_uuid", sa.String(), nullable=False, server_default=""),
        sa.Column("exit_pbk", sa.String(), nullable=False, server_default=""),
        sa.Column("exit_pvk", sa.String(), nullable=False, server_default=""),
        sa.Column("exit_sid", sa.String(), nullable=False, server_default=""),
        sa.Column("relay_pbk", sa.String(), nullable=False, server_default=""),
        sa.Column("relay_pvk", sa.String(), nullable=False, server_default=""),
        sa.Column("relay_sid", sa.String(), nullable=False, server_default=""),
        sa.Column(
            "relay_inbound_remark", sa.String(),
            nullable=False, server_default="",
        ),
        sa.Column(
            "exit_inbound_remark", sa.String(),
            nullable=False, server_default="",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["chain_id"], ["proxychain.id"], ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "chain_id", "name", name="uq_chainchannel_chain_name",
        ),
    )
    op.create_index(
        "ix_chainchannel_chain_id", "chainchannel", ["chain_id"],
    )

    # ── chainclient ────────────────────────────────────────────────────────
    op.create_table(
        "chainclient",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chain_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["chain_id"], ["proxychain.id"], ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_chainclient_chain_id", "chainclient", ["chain_id"],
    )
    op.create_index(
        "ix_chainclient_label", "chainclient", ["label"],
    )

    # ── chainclientchannel ─────────────────────────────────────────────────
    op.create_table(
        "chainclientchannel",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chain_client_id", sa.Integer(), nullable=False),
        sa.Column("channel_id", sa.Integer(), nullable=False),
        sa.Column("client_uuid", sa.String(), nullable=False),
        sa.Column("exported_node_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["chain_client_id"], ["chainclient.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["channel_id"], ["chainchannel.id"], ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["exported_node_id"], ["node.id"], ondelete="SET NULL",
        ),
        sa.UniqueConstraint(
            "chain_client_id", "channel_id",
            name="uq_chainclientchannel_pair",
        ),
    )
    op.create_index(
        "ix_chainclientchannel_chain_client_id",
        "chainclientchannel", ["chain_client_id"],
    )
    op.create_index(
        "ix_chainclientchannel_channel_id",
        "chainclientchannel", ["channel_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chainclientchannel_channel_id", table_name="chainclientchannel",
    )
    op.drop_index(
        "ix_chainclientchannel_chain_client_id", table_name="chainclientchannel",
    )
    op.drop_table("chainclientchannel")
    op.drop_index("ix_chainclient_label", table_name="chainclient")
    op.drop_index("ix_chainclient_chain_id", table_name="chainclient")
    op.drop_table("chainclient")
    op.drop_index("ix_chainchannel_chain_id", table_name="chainchannel")
    op.drop_table("chainchannel")
    op.drop_index(
        "ix_proxychain_relay_xui_server_id", table_name="proxychain",
    )
    op.drop_index(
        "ix_proxychain_exit_xui_server_id", table_name="proxychain",
    )
    op.drop_table("proxychain")
