"""Add `grpc_authority` column to node (v1.3.0-beta.7+).

xui-pro panels emit Trojan + gRPC inbounds behind nginx with an
`:authority` HTTP/2 header (`grpcSettings.authority` on the panel
side). The header is what nginx routes by, so missing it on the
client outbound makes the gRPC handshake hit the wrong location
block — connection just stalls. The `grpc_service` + `grpc_mode`
columns already exist; this adds the matching `authority` slot so
the Node row can carry the full gRPC config end-to-end.

Nullable: existing Nodes (and protocols that don't use gRPC) keep
NULL. config_gen omits the `authority` field from streamSettings
when it's None, so the generated xray outbound stays valid for
the common path.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "015"
down_revision: Union[str, None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "node",
        sa.Column("grpc_authority", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("node", "grpc_authority")
