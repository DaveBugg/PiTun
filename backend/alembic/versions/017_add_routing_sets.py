"""Add RoutingSet table + per-device-group routing.

Introduces named routing rule sets so the operator can apply a different
list of routing rules to selected devices (parental controls for kids'
devices, work-VPN for a laptop, etc.). Existing installs see zero
behaviour change — all RoutingRule and Device rows get
`routing_set_id = NULL`, which the new code treats as "global rule" and
"unassigned device", preserving the v1.3.x single-rule-list semantics.

Per-set isolation is implemented at the nftables layer (fwmark per MAC),
NOT at the schema layer — see core/nftables.py + core/config_gen.py for
the runtime story. The schema just stores the assignment.

Revision ID: 017
Revises: 016
Create Date: 2026-05-31
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # New table — RoutingSet. tproxy_port is auto-allocated by the API
    # layer at create-time (next free port in 65500..65535). One port
    # handles TCP+UDP via xray's dokodemo-door with network: "tcp,udp"
    # — Linux kernel lets TCP and UDP coexist on the same port number.
    # Hard limit: 36 sets per install (more than any homelab needs).
    op.create_table(
        "routingset",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tproxy_port", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.UniqueConstraint("name", name="uq_routingset_name"),
        sa.UniqueConstraint("tproxy_port", name="uq_routingset_tproxy_port"),
    )
    op.create_index("ix_routingset_name", "routingset", ["name"])
    op.create_index("ix_routingset_tproxy_port", "routingset", ["tproxy_port"])

    # FK column on RoutingRule. Nullable: NULL == "global rule, applies
    # to all devices" — same as the v1.3.x default behaviour, so the
    # absence of a backfill is a feature, not a bug.
    with op.batch_alter_table("routingrule") as batch:
        batch.add_column(
            sa.Column(
                "routing_set_id",
                sa.Integer(),
                sa.ForeignKey("routingset.id", name="fk_routingrule_routingset"),
                nullable=True,
            )
        )
    op.create_index(
        "ix_routingrule_routing_set_id",
        "routingrule",
        ["routing_set_id"],
    )

    # FK column on Device. Nullable: NULL == "unassigned, only global
    # rules apply" — preserves pre-1.4 behaviour for every existing row.
    with op.batch_alter_table("device") as batch:
        batch.add_column(
            sa.Column(
                "routing_set_id",
                sa.Integer(),
                sa.ForeignKey("routingset.id", name="fk_device_routingset"),
                nullable=True,
            )
        )
    op.create_index(
        "ix_device_routing_set_id",
        "device",
        ["routing_set_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_device_routing_set_id", table_name="device")
    with op.batch_alter_table("device") as batch:
        batch.drop_constraint("fk_device_routingset", type_="foreignkey")
        batch.drop_column("routing_set_id")

    op.drop_index("ix_routingrule_routing_set_id", table_name="routingrule")
    with op.batch_alter_table("routingrule") as batch:
        batch.drop_constraint("fk_routingrule_routingset", type_="foreignkey")
        batch.drop_column("routing_set_id")

    op.drop_index("ix_routingset_tproxy_port", table_name="routingset")
    op.drop_index("ix_routingset_name", table_name="routingset")
    op.drop_table("routingset")
