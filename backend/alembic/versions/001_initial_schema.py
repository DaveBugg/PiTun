"""Initial schema — all existing tables.

Revision ID: 001
Revises: None
Create Date: 2026-04-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("username", sa.String, unique=True, nullable=False, index=True),
        sa.Column("password_hash", sa.String, nullable=False),
    )

    op.create_table(
        "settings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("key", sa.String, unique=True, nullable=False, index=True),
        sa.Column("value", sa.String, nullable=False),
    )

    op.create_table(
        "subscription",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("url", sa.String, nullable=False),
        sa.Column("enabled", sa.Boolean, default=True),
        sa.Column("ua", sa.String, default="clash"),
        sa.Column("filter_regex", sa.String, nullable=True),
        sa.Column("auto_update", sa.Boolean, default=False),
        sa.Column("update_interval", sa.Integer, default=86400),
        sa.Column("last_updated", sa.DateTime, nullable=True),
        sa.Column("node_count", sa.Integer, default=0),
    )

    op.create_table(
        "node",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("enabled", sa.Boolean, default=True),
        sa.Column("protocol", sa.String, nullable=False),
        sa.Column("address", sa.String, nullable=False),
        sa.Column("port", sa.Integer, nullable=False),
        sa.Column("uuid", sa.String, nullable=True),
        sa.Column("password", sa.String, nullable=True),
        sa.Column("transport", sa.String, default="tcp"),
        sa.Column("tls", sa.String, default="none"),
        sa.Column("sni", sa.String, nullable=True),
        sa.Column("fingerprint", sa.String, default="chrome"),
        sa.Column("alpn", sa.String, nullable=True),
        sa.Column("allow_insecure", sa.Boolean, default=False),
        sa.Column("ws_path", sa.String, default="/"),
        sa.Column("ws_host", sa.String, nullable=True),
        sa.Column("ws_headers", sa.String, nullable=True),
        sa.Column("grpc_service", sa.String, nullable=True),
        sa.Column("grpc_mode", sa.String, default="gun"),
        sa.Column("http_path", sa.String, default="/"),
        sa.Column("http_host", sa.String, nullable=True),
        sa.Column("kcp_seed", sa.String, nullable=True),
        sa.Column("kcp_header", sa.String, default="none"),
        sa.Column("reality_pbk", sa.String, nullable=True),
        sa.Column("reality_sid", sa.String, nullable=True),
        sa.Column("reality_spx", sa.String, nullable=True),
        sa.Column("flow", sa.String, nullable=True),
        sa.Column("wg_private_key", sa.String, nullable=True),
        sa.Column("wg_public_key", sa.String, nullable=True),
        sa.Column("wg_preshared_key", sa.String, nullable=True),
        sa.Column("wg_endpoint", sa.String, nullable=True),
        sa.Column("wg_mtu", sa.Integer, default=1420),
        sa.Column("wg_reserved", sa.String, nullable=True),
        sa.Column("wg_local_address", sa.String, nullable=True),
        sa.Column("hy2_obfs", sa.String, nullable=True),
        sa.Column("hy2_obfs_password", sa.String, nullable=True),
        sa.Column("group", sa.String, nullable=True),
        sa.Column("note", sa.String, nullable=True),
        sa.Column("subscription_id", sa.Integer, sa.ForeignKey("subscription.id"), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("last_check", sa.DateTime, nullable=True),
        sa.Column("is_online", sa.Boolean, default=True),
        sa.Column("order", sa.Integer, default=0),
        sa.Column("chain_node_id", sa.Integer, nullable=True),
    )
    op.create_index("ix_node_subscription_id", "node", ["subscription_id"])

    op.create_table(
        "routingrule",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("enabled", sa.Boolean, default=True),
        sa.Column("rule_type", sa.String, nullable=False),
        sa.Column("match_value", sa.String, nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("order", sa.Integer, default=100),
    )
    op.create_index("ix_routingrule_order", "routingrule", ["order"])

    op.create_table(
        "dnsrule",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, default=""),
        sa.Column("enabled", sa.Boolean, default=True),
        sa.Column("domain_match", sa.String, nullable=False),
        sa.Column("dns_server", sa.String, nullable=False),
        sa.Column("dns_type", sa.String, default="plain"),
        sa.Column("order", sa.Integer, default=100),
    )

    op.create_table(
        "dnsquerylog",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("timestamp", sa.DateTime, nullable=False),
        sa.Column("domain", sa.String, nullable=False, index=True),
        sa.Column("resolved_ips", sa.String, default="[]"),
        sa.Column("server_used", sa.String, default=""),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("query_type", sa.String, default="A"),
        sa.Column("rule_matched", sa.String, nullable=True),
        sa.Column("cache_hit", sa.Boolean, default=False),
    )

    op.create_table(
        "balancergroup",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("enabled", sa.Boolean, default=True),
        sa.Column("node_ids", sa.String, default="[]"),
        sa.Column("strategy", sa.String, default="leastPing"),
    )

    op.create_table(
        "nodecircle",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("enabled", sa.Boolean, default=False),
        sa.Column("node_ids", sa.String, default="[]"),
        sa.Column("mode", sa.String, default="sequential"),
        sa.Column("interval_min", sa.Integer, default=5),
        sa.Column("interval_max", sa.Integer, default=15),
        sa.Column("current_index", sa.Integer, default=0),
        sa.Column("last_rotated", sa.DateTime, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("nodecircle")
    op.drop_table("balancergroup")
    op.drop_table("dnsquerylog")
    op.drop_table("dnsrule")
    op.drop_index("ix_routingrule_order", "routingrule")
    op.drop_table("routingrule")
    op.drop_index("ix_node_subscription_id", "node")
    op.drop_table("node")
    op.drop_table("subscription")
    op.drop_table("settings")
    op.drop_table("user")
