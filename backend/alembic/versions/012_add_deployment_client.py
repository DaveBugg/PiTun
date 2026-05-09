"""Add deploymentclient table + node FKs (v1.3.0-beta.4).

Introduces a *client config* layer between Server-side deployment state
and PiTun's Node table for multi-client protocols (initially WireGuard).

Why a separate layer?

  * **One server, many clients.** A WireGuard server can host any number
    of peer configs. Each peer is independent — its own keys, IP, name —
    and conceptually corresponds to one device that wants to dial in.
    Modeling each peer as a Node directly would mix concerns: a Node is
    "a proxy outbound this PiTun routes through", whereas a peer is "a
    config the server admins thinks exists". They're different lifecycles.

  * **Selective import.** Sometimes a server is shared between multiple
    PiTun instances (e.g. home + office). Each PiTun should see ALL
    server-side clients (so admin can pick the right one for this site)
    but only IMPORT the ones actually used. The DeploymentClient row
    holds the conf; the Node is created on explicit "Export to Node".

  * **Sync without surprise.** When the user clicks "Sync from server",
    we re-list peers via SSH and reconcile against this table. New
    server-side peers show up here; PiTun-side peers missing on the
    server flip to status='orphan'. Nodes never silently appear or
    disappear from a sync.

For naive (which is single-tunnel by nature, not multi-client), the
existing `ServerDeployment.last_node_id` flow keeps working unchanged.
A future migration may unify naive into this model for consistency.

Revision ID: 012
Revises: 011
Create Date: 2026-05-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "deploymentclient",
        sa.Column("id", sa.Integer(), nullable=False),
        # FK to ServerDeployment (server, protocol). Cascade delete:
        # if the deployment is removed (e.g. user clicks "Uninstall WG
        # on this server"), all its clients are obviously gone too.
        sa.Column("deployment_id", sa.Integer(), nullable=False),
        # Human-readable peer name. Unique per deployment (you can have
        # both `phone` on server-A and `phone` on server-B, but only
        # one `phone` per (server, protocol)). The script's add-client
        # subcommand enforces a sane charset; we still constrain
        # length here to keep table indexes tight.
        sa.Column("name", sa.String(length=100), nullable=False),
        # ── WireGuard peer fields ──────────────────────────────────────
        # Mirrors `Node`'s wg_* columns so "Export to Node" is a 1:1
        # field copy. Stored here too because Server-side `wg0.conf`
        # holds only public keys + AllowedIPs; the *client* private
        # key is generated at add-client time and not retrievable from
        # the server afterwards. Lose this row → lose the client conf.
        sa.Column("wg_private_key", sa.String(), nullable=True),
        sa.Column("wg_public_key", sa.String(), nullable=True),
        sa.Column("wg_preshared_key", sa.String(), nullable=True),
        sa.Column("wg_endpoint", sa.String(), nullable=True),  # host:port
        sa.Column("wg_mtu", sa.Integer(), nullable=False, server_default="1420"),
        # Comma-separated CIDR list, e.g. "10.66.66.2/24,fd42:42:42::2/64".
        sa.Column("wg_local_address", sa.String(), nullable=True),
        # ── Server context (denormalized for fast Node creation) ───────
        # DNS server list pushed by the server when peer dialed in,
        # e.g. "1.1.1.1,1.0.0.1". Stored here to spare the export-to-Node
        # path an extra fetch from ServerDeployment.config.
        sa.Column("dns_servers", sa.String(), nullable=True),
        # AllowedIPs the server config dictates for this peer (typically
        # "0.0.0.0/0,::/0" for full-tunnel; could be split-tunnel).
        sa.Column("allowed_ips", sa.String(), nullable=True),
        # ── Generic config blob ────────────────────────────────────────
        # JSON dict for protocol-specific extras that don't deserve their
        # own column. Currently used for: full WG INI conf as a verbatim
        # backup string (so the user can re-download the .conf even after
        # we've Node-exported), client IP allocations, etc.
        sa.Column("config_json", sa.Text(), nullable=True),
        # ── Lifecycle ──────────────────────────────────────────────────
        # State machine:
        #   `available` — fresh; not exported to a Node yet
        #   `exported`  — at least one Node was created from this client.
        #                 Multiple Nodes possible if the same client conf
        #                 was exported repeatedly (rare but allowed).
        #   `orphan`    — sync detected this client is missing on the
        #                 server. Kept for forensics + so admin can
        #                 decide whether to re-create on the server or
        #                 delete this row (and let any Node go orphan).
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="available",
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["serverdeployment.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "deployment_id", "name", name="uq_deploymentclient_deployment_name"
        ),
    )
    op.create_index(
        "ix_deploymentclient_deployment_id",
        "deploymentclient",
        ["deployment_id"],
    )

    # Node FKs back to the source client. SQLite doesn't easily let us
    # ALTER … ADD COLUMN with a FK constraint inline — instead we add
    # plain Integer/Boolean columns. The reverse navigation is rare
    # enough (only Nodes UI badge logic) that the lack of an enforced
    # FK is acceptable; orphan rows are recoverable.
    with op.batch_alter_table("node") as batch:
        # NULL when the Node was created via URI import / manual entry
        # (existing pre-multi-client flow). Set when Node was created
        # by the "Export to Node" action.
        batch.add_column(
            sa.Column("from_deployment_client_id", sa.Integer(), nullable=True)
        )
        # Flagged true when:
        #   * a sync against the server detects the client is gone
        #     (peer was removed manually via wg-quick / by another PiTun
        #     instance / via the host's CLI), AND
        #   * this Node was previously exported from that DeploymentClient.
        # The Node row is NOT auto-deleted (admin choice); UI shows a
        # "server-side client deleted" badge so the admin knows the
        # tunnel will fail to handshake until they decide what to do.
        batch.add_column(
            sa.Column(
                "client_orphan",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("node") as batch:
        batch.drop_column("client_orphan")
        batch.drop_column("from_deployment_client_id")

    op.drop_index(
        "ix_deploymentclient_deployment_id", table_name="deploymentclient"
    )
    op.drop_table("deploymentclient")
