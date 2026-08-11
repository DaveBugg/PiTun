from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Dict, Optional, List, Any
from datetime import datetime


# ─── Auth ─────────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

class UserRead(BaseModel):
    id: int
    username: str
    class Config:
        from_attributes = True


# ─── Node ────────────────────────────────────────────────────────────────────

class NodeBase(BaseModel):
    name: str
    enabled: bool = True
    protocol: str
    address: str
    port: int
    # Optional link to a Server (the VPS hosting this node's upstream).
    # Purely informational — does not affect routing/connection.
    server_id: Optional[int] = None
    uuid: Optional[str] = None
    password: Optional[str] = None
    transport: str = "tcp"
    tls: str = "none"
    sni: Optional[str] = None
    fingerprint: str = "chrome"
    alpn: Optional[str] = None
    allow_insecure: bool = False
    ws_path: str = "/"
    ws_host: Optional[str] = None
    ws_headers: Optional[str] = None
    grpc_service: Optional[str] = None
    grpc_mode: str = "gun"
    grpc_authority: Optional[str] = None
    http_path: str = "/"
    http_host: Optional[str] = None
    kcp_seed: Optional[str] = None
    kcp_header: str = "none"
    reality_pbk: Optional[str] = None
    reality_sid: Optional[str] = None
    reality_spx: Optional[str] = None
    flow: Optional[str] = None
    wg_private_key: Optional[str] = None
    wg_public_key: Optional[str] = None
    wg_preshared_key: Optional[str] = None
    wg_endpoint: Optional[str] = None
    wg_mtu: int = 1420
    wg_reserved: Optional[str] = None
    wg_local_address: Optional[str] = None
    hy2_obfs: Optional[str] = None
    hy2_obfs_password: Optional[str] = None
    # NaiveProxy
    internal_port: Optional[int] = None
    naive_padding: bool = True
    group: Optional[str] = None
    note: Optional[str] = None
    subscription_id: Optional[int] = None
    order: int = 0
    chain_node_id: Optional[int] = None

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, v: str) -> str:
        valid = {"vless", "vmess", "trojan", "ss", "wireguard", "socks", "hy2", "naive"}
        if v not in valid:
            raise ValueError(f"protocol must be one of {valid}")
        return v

    @field_validator("transport", mode="before")
    @classmethod
    def validate_transport(cls, v: str) -> str:
        # Xray renamed the plain "tcp" transport to "raw" (v25.x). A
        # subscription importing a node with type=raw stored it verbatim,
        # and this validator then rejected it on read — 500ing the WHOLE
        # /nodes list off one node. They are the same transport.
        if v == "raw":
            v = "tcp"
        valid = {"tcp", "ws", "grpc", "h2", "xhttp", "httpupgrade", "kcp", "quic"}
        if v not in valid:
            raise ValueError(f"transport must be one of {valid}")
        return v

    @field_validator("tls")
    @classmethod
    def validate_tls(cls, v: str) -> str:
        valid = {"none", "tls", "reality"}
        if v not in valid:
            raise ValueError(f"tls must be one of {valid}")
        return v


class NodeCreate(NodeBase):
    pass


class NodeUpdate(NodeBase):
    name: Optional[str] = None
    protocol: Optional[str] = None
    address: Optional[str] = None
    port: Optional[int] = None

    @field_validator("protocol", mode="before")
    @classmethod
    def validate_protocol(cls, v: Optional[str]) -> Optional[str]:  # type: ignore[override]
        if v is None:
            return v
        valid = {"vless", "vmess", "trojan", "ss", "wireguard", "socks", "hy2", "naive"}
        if v not in valid:
            raise ValueError(f"protocol must be one of {valid}")
        return v

    @field_validator("transport", mode="before")
    @classmethod
    def validate_transport(cls, v: Optional[str]) -> Optional[str]:  # type: ignore[override]
        if v is None:
            return v
        if v == "raw":  # Xray's new name for the "tcp" transport
            v = "tcp"
        valid = {"tcp", "ws", "grpc", "h2", "xhttp", "httpupgrade", "kcp", "quic"}
        if v not in valid:
            raise ValueError(f"transport must be one of {valid}")
        return v

    @field_validator("tls", mode="before")
    @classmethod
    def validate_tls(cls, v: Optional[str]) -> Optional[str]:  # type: ignore[override]
        if v is None:
            return v
        valid = {"none", "tls", "reality"}
        if v not in valid:
            raise ValueError(f"tls must be one of {valid}")
        return v


class NodeRead(NodeBase):
    id: int
    latency_ms: Optional[int] = None
    last_check: Optional[datetime] = None
    is_online: bool = True
    # Last speed reading (Mbps) + when it was taken. The UI shows the age
    # and greys/reddens a reading older than ~6h.
    speed_mbps: Optional[float] = None
    speed_max_mbps: Optional[float] = None
    speed_tested_at: Optional[datetime] = None
    # Multi-client deployment provenance (since v1.3.0-beta.4). When this
    # Node was exported from a DeploymentClient (e.g. WireGuard peer),
    # keep the link so the UI can render "from <server name>" alongside
    # the node + raise an `orphan` warning if the upstream peer is gone.
    from_deployment_client_id: Optional[int] = None
    client_orphan: bool = False
    # `chain_node_id` points at a Node that no longer exists. Computed on
    # read rather than stored: it reflects what is actually true right
    # now, so it also catches rows broken by an older version or an
    # external DB edit. A chained node whose relay is gone silently
    # stops working — xray skips the outbound, probes and speed tests
    # return nothing — and nothing in the list used to say why.
    chain_orphan: bool = False

    class Config:
        from_attributes = True


class NodePage(BaseModel):
    """Envelope returned by `GET /api/nodes/page` (since v1.3.3).

    The legacy `GET /api/nodes` endpoint stays as-is (returns
    `List[NodeRead]`) because reorder / export / circle-scheduler etc.
    all need the full unfiltered set. The paginated endpoint is for
    the Nodes UI page which now supports 1000+ rows from subscriptions.
    """

    items: List[NodeRead]
    total: int
    limit: int
    offset: int


class NaiveSidecarStatus(BaseModel):
    """Docker container status for a NaiveProxy node's sidecar."""
    exists: bool
    running: bool
    status: str
    started_at: Optional[str] = None
    restart_count: int = 0
    internal_port: Optional[int] = None


class NaiveSidecarLogs(BaseModel):
    node_id: int
    logs: str


class NodeImportRequest(BaseModel):
    uris: str  # newline-separated or single URI
    # When set AND exactly one node parses (single-config file import, e.g.
    # a WireGuard .conf), override that node's name with this value — backs
    # the "name from filename" toggle in the file-upload UI. Ignored for
    # multi-URI input so a paste of many URIs never collapses to one name.
    name_override: Optional[str] = None


class NodeImportResponse(BaseModel):
    imported: int
    skipped: int
    nodes: List[NodeRead]
    errors: List[str]


# ─── Servers (managed VPS instances) ─────────────────────────────────────────
#
# `Server` is the *machine* (SSH-reachable VPS); `Node` is the VPN connection
# we route traffic through. They're 1:N — one VPS can host multiple nodes
# (e.g. naive on :443 and a wireguard endpoint on :51820 share one Server).
#
# Credentials are stored plain in SQLite. PiTun is LAN-only — see the threat
# model in SECURITY.md before changing this. Keep credentials write-only at
# the API boundary: GET responses report `has_password` / `has_private_key`
# booleans, never the secrets themselves.

class ServerBase(BaseModel):
    name: str
    description: Optional[str] = None
    host: str
    port: int = 22
    user: str = "root"
    auth_type: str = "password"

    @field_validator("auth_type")
    @classmethod
    def validate_auth_type(cls, v: str) -> str:
        valid = {"password", "key"}
        if v not in valid:
            raise ValueError(f"auth_type must be one of {valid}")
        return v

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError("port must be 1..65535")
        return v


class ServerCreate(ServerBase):
    # On create, secrets are passed as plain strings. Empty string is
    # treated as "no value" (so a key-auth server can omit `password`).
    password: Optional[str] = None
    private_key: Optional[str] = None
    passphrase: Optional[str] = None


class ServerUpdate(BaseModel):
    """All fields optional for PATCH. Secret fields obey three-state rules:
      - field absent (not in JSON)         → unchanged
      - field present, empty string ("")   → unchanged (avoids accidental wipes
                                              from forms that submit empty inputs)
      - field present, explicit null       → cleared
      - field present with value           → replaced
    """
    name: Optional[str] = None
    description: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    auth_type: Optional[str] = None
    password: Optional[str] = None
    private_key: Optional[str] = None
    passphrase: Optional[str] = None

    @field_validator("auth_type")
    @classmethod
    def validate_auth_type(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        valid = {"password", "key"}
        if v not in valid:
            raise ValueError(f"auth_type must be one of {valid}")
        return v

    @field_validator("port")
    @classmethod
    def validate_port(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if not (1 <= v <= 65535):
            raise ValueError("port must be 1..65535")
        return v


class ServerRead(ServerBase):
    """API response shape — no secrets, only `has_*` booleans."""
    id: int
    has_password: bool = False
    has_private_key: bool = False
    has_passphrase: bool = False
    status: str = "unknown"
    last_check: Optional[datetime] = None
    last_check_error: Optional[str] = None
    latency_ms: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class ServerTestResult(BaseModel):
    server_id: int
    ok: bool
    latency_ms: Optional[int] = None
    error: Optional[str] = None
    # Free-form info from `uname -a` / `cat /etc/os-release` on success,
    # so the user gets feedback that we actually reached the box.
    remote_info: Optional[str] = None


class ServerTestAllResult(BaseModel):
    results: List[ServerTestResult]


# ── Auto-deploy (since v1.3.0) ───────────────────────────────────────────────
#
# `POST /api/servers/{id}/deploy` runs an install script remotely via SSH and,
# on a successful exit, parses a `URI=<uri>` line from stdout and creates a
# Node row. The request mirrors `ServerDeploymentBase` plus `config` is
# protocol-specific (see core/deploy.build_naive_env for the naive contract).

class ServerDeployRequest(BaseModel):
    """Input for `POST /api/servers/{id}/deploy`.

    Naive (single-tunnel — `result.node_id` populated):
      `config`: {"domain": str, "email": str,
                 "naive_user": Optional[str],   # default: "pitun"
                 "naive_pass": Optional[str]}   # default: token_urlsafe(24)

    WireGuard (multi-client — `result.client_id` populated; admin
    exports to Node manually via Manage Clients UI):
      `config`: {"client_name": str,           # required, [a-zA-Z0-9_-]+
                 "server_port": Optional[int],  # default: 51820
                 "wg_network_4": Optional[str], # default: 10.66.66.0/24
                 "wg_network_6": Optional[str], # default: fd42:42:42::/64
                 "dns_1": Optional[str],        # default: 1.1.1.1
                 "dns_2": Optional[str],        # default: 1.0.0.1
                 "allowed_ips": Optional[str],  # default: 0.0.0.0/0,::/0
                 "server_pub_ip": Optional[str]}# default: autodetect on VPS
    """
    protocol: str
    config: dict

    @field_validator("protocol")
    @classmethod
    def _validate_protocol(cls, v: str) -> str:
        # Mirror the gate in core/deploy.SUPPORTED_PROTOCOLS — kept in
        # sync manually since this validator runs before the request
        # body even reaches the endpoint. `xui` added in v1.3.0-beta.7
        # for x-ui-pro / 3x-ui panel installs.
        if v not in ("naive", "wireguard", "xui"):
            raise ValueError(
                f"Unsupported protocol: {v!r} "
                f"(expected 'naive', 'wireguard' or 'xui')"
            )
        return v


class DeployJobAccepted(BaseModel):
    """202 Accepted shape for `POST /api/servers/{id}/deploy` (since v1.3.0
    Phase 2.2). The endpoint now spawns an async job via
    `core.jobs.JobManager.start_deploy` and returns the job_id immediately
    — the client polls `GET /api/server-tasks/{job_id}` or subscribes to
    `WS /api/server-tasks/{job_id}/stream` to follow progress.

    Replaces the synchronous `ServerDeployResponse` from Phase 1: blocking
    a single uvicorn worker for ~5 minutes per deploy starved every
    other request, and there was no way to cancel a stuck deploy.
    """
    job_id: str
    # Echoed for convenience so the frontend can group multiple jobs by
    # server in the /server-tasks listing without an extra GET.
    server_id: int
    protocol: str


# ─── Jobs (server-tasks) ─────────────────────────────────────────────────────
#
# Read-only schemas — jobs are CREATED by `POST /servers/{id}/deploy` and
# similar action endpoints, never directly. See `core/jobs.py.JobManager`
# for the persistence model. Frontend uses these for the `/server-tasks`
# page (list/detail/cancel) and the WS log stream.

class JobRead(BaseModel):
    """Single-job read shape. Used by `GET /server-tasks/{id}`.

    Mirrors `models.Job` but unpacks `result_json` into a typed dict so
    the frontend doesn't need a second JSON.parse step.
    """
    id: str
    kind: str
    target_id: Optional[int]
    target_name: Optional[str]
    protocol: Optional[str]
    status: str   # running | succeeded | failed | cancelled
    started_at: datetime
    finished_at: Optional[datetime]
    error: Optional[str]
    # On success: e.g. {"node_id": 42, "deployment_id": 5, "parsed_uri": "..."}
    result: Optional[dict] = None
    # Snapshot of the last ~4 KB of combined stdout/stderr captured at
    # finalization (see `core.jobs._LOG_TAIL_BYTES`). None while still
    # `running` — clients should subscribe to the WS for live output.
    log_tail: Optional[str] = None
    # Echoed-back deploy config (domain, email, naive_user — never
    # secrets). Useful for the "retry" affordance on the UI.
    config: Optional[dict] = None


class JobSummaryRead(BaseModel):
    """Lightweight projection for `GET /server-tasks` list calls.
    Skips the heavier fields (config, log_tail) so list views stay
    cheap on backend boxes with hundreds of historical jobs.
    """
    id: str
    kind: str
    target_id: Optional[int]
    target_name: Optional[str]
    protocol: Optional[str]
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    error: Optional[str]
    result: Optional[dict] = None


class JobListResponse(BaseModel):
    """Paginated list shape. `total` is omitted intentionally — counting
    every match would defeat the index for big histories. Frontend
    uses `limit + has_more` (limit + 1 query trick) instead, but the
    initial release just returns the page; pagination UX can be
    progressive."""
    jobs: List[JobSummaryRead]


# ─── Server deployments ──────────────────────────────────────────────────────
#
# A "deployment" persists the credentials the user picked when generating
# a per-protocol install script for a Server. One row per (server, protocol)
# — re-saving updates the existing row. See `app/models.py:ServerDeployment`
# for the design rationale.
#
# `config` shape depends on protocol:
#   naive: {"domain": str, "email": str, "naive_user": str, "naive_pass": str}
# Future protocols will use different keys (the API just round-trips a dict).

class ServerDeploymentBase(BaseModel):
    protocol: str
    config: dict

    @field_validator("protocol")
    @classmethod
    def validate_protocol(cls, v: str) -> str:
        # Gate the protocol string on **both** request and response paths.
        # The runner/router code branches on this value, so a typo here
        # would silently create a row nothing else can deserialise. As
        # of v1.3.0-beta.7 we accept naive (single-tunnel), wireguard
        # (multi-client via DeploymentClient), and xui (panel-as-
        # deployment, side-table at XuiServer). Loosen further when
        # Hy2 / XTLS deployments land. NB: the read path also uses
        # this base — when adding a new protocol, both
        # ServerDeployRequest's validator AND this set need to grow
        # in lockstep, otherwise listing existing rows 500s on the
        # new protocol.
        valid = {"naive", "wireguard", "xui"}
        if v not in valid:
            raise ValueError(f"protocol must be one of {valid}")
        return v


class ServerDeploymentUpsert(ServerDeploymentBase):
    """Body for PUT /servers/{id}/deployments — create or replace by protocol."""
    pass


class ServerDeploymentRead(ServerDeploymentBase):
    id: int
    server_id: int
    status: str
    last_node_id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ─── Deployment clients (multi-client protocols, since v1.3.0-beta.4) ────────
#
# A DeploymentClient is one peer config on a server-side deployment.
# Currently only WireGuard uses this layer; naive stays on the simpler
# ServerDeployment.last_node_id flow (single-tunnel by nature).
#
# The status field tracks lifecycle:
#   * `available` — fresh, not exported to a Node yet
#   * `exported`  — at least one Node was created from this client
#   * `orphan`    — sync detected this client is missing on the server
#                   (peer was removed via wg-quick / by another PiTun
#                   instance / via the host's CLI). Row is kept for
#                   forensics; admin decides whether to re-create on
#                   the server or delete locally.

class DeploymentClientBase(BaseModel):
    name: str  # human-readable peer name, e.g. "phone"


class DeploymentClientCreate(DeploymentClientBase):
    """Request body for `POST /servers/{id}/deployments/wireguard/clients`.

    Just the name — server-side keys + IP allocation happen on the
    remote VPS as part of the script run, then the resulting peer
    config is parsed back into a DeploymentClient row.
    """
    pass


class DeploymentClientRead(DeploymentClientBase):
    """API shape for client listing — does NOT include `wg_private_key`
    or `wg_preshared_key` by default. Those are exposed only via
    `GET /clients/{name}/conf` when the user explicitly downloads the
    config (e.g. for QR-code generation on a phone). Same threat model
    as Node: PiTun is LAN-only, but minimising secret exposure across
    list calls is cheap.
    """
    id: int
    deployment_id: int
    wg_public_key: Optional[str] = None
    wg_endpoint: Optional[str] = None       # "host:port"
    wg_local_address: Optional[str] = None  # "10.66.66.2/24,fd42:...:2/64"
    wg_mtu: int = 1420
    dns_servers: Optional[str] = None
    allowed_ips: Optional[str] = None
    status: str
    # Set when the client has been exported to one or more Nodes. Lets
    # the UI show "linked to Node #42" without an extra query.
    exported_node_ids: List[int] = []
    created_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None


class DeploymentClientConf(BaseModel):
    """Full per-client config (including private key) — used by
    `GET /clients/{name}/conf` to return the WireGuard `.conf` text
    for download / QR display. Returned ONCE on demand; never in
    list endpoints.
    """
    name: str
    wg_conf: str  # ready-to-paste [Interface]+[Peer] INI


class DeploymentClientList(BaseModel):
    clients: List[DeploymentClientRead]


class DeploymentClientSyncResult(BaseModel):
    """Outcome of `POST /servers/{id}/deployments/wireguard/clients/sync`.

    Reconcile pass against the server's current peer list:
      * `added`    — server-only peers we just imported into our DB
      * `unchanged` — peers we already knew about, status unchanged
      * `orphaned` — PiTun-side peers that aren't on the server any
                     more (status flipped to `orphan`)
    """
    added: List[str] = []
    unchanged: List[str] = []
    orphaned: List[str] = []


class ExportClientToNodeRequest(BaseModel):
    """Body for `POST /servers/{id}/deployments/wireguard/clients/{name}/export-node`.

    All fields optional; sensible defaults derived from the client +
    server context if omitted:
      * `node_name` defaults to the client name
      * `enabled` left as None on the existing-Node path (keeps the
        previously-saved value) and treated as `True` when creating
        a fresh Node
      * `force` defaults to False — repeated exports of the same
        client return the *existing* Node instead of duplicating;
        set to True to intentionally create another copy (chain
        nodes, multi-PiTun import scenarios)
    """
    node_name: Optional[str] = None
    enabled: Optional[bool] = None
    force: bool = False


# ─── Routing Rules ────────────────────────────────────────────────────────────

class RoutingRuleBase(BaseModel):
    name: str
    enabled: bool = True
    rule_type: str
    match_value: str
    action: str
    order: int = 100
    # NULL = global rule (applies to all devices). Set to a RoutingSet.id
    # to make this rule only apply to devices assigned to that set.
    routing_set_id: Optional[int] = None

    @field_validator("rule_type")
    @classmethod
    def validate_rule_type(cls, v: str) -> str:
        valid = {"mac", "src_ip", "dst_ip", "domain", "port", "protocol", "geoip", "geosite"}
        if v not in valid:
            raise ValueError(f"rule_type must be one of {valid}")
        return v

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in ("proxy", "direct", "block") and not v.startswith("node:") and not v.startswith("balancer:"):
            raise ValueError("action must be proxy|direct|block|node:<id>|balancer:<id>")
        return v


class RoutingRuleCreate(RoutingRuleBase):
    pass


class RoutingRuleUpdate(RoutingRuleBase):
    name: Optional[str] = None
    rule_type: Optional[str] = None
    match_value: Optional[str] = None
    action: Optional[str] = None

    @field_validator("rule_type", mode="before")
    @classmethod
    def validate_rule_type(cls, v: Optional[str]) -> Optional[str]:  # type: ignore[override]
        if v is None:
            return v
        valid = {"mac", "src_ip", "dst_ip", "domain", "port", "protocol", "geoip", "geosite"}
        if v not in valid:
            raise ValueError(f"rule_type must be one of {valid}")
        return v

    @field_validator("action", mode="before")
    @classmethod
    def validate_action(cls, v: Optional[str]) -> Optional[str]:  # type: ignore[override]
        if v is None:
            return v
        if v not in ("proxy", "direct", "block") and not v.startswith("node:") and not v.startswith("balancer:"):
            raise ValueError("action must be proxy|direct|block|node:<id>|balancer:<id>")
        return v


class RoutingRuleRead(RoutingRuleBase):
    id: int

    class Config:
        from_attributes = True


# ─── Routing rule export / import (set-aware, native PiTun format) ─────────────

class RoutingPortRule(BaseModel):
    """One rule in the portable export/import envelope. Carries everything
    needed to recreate a RoutingRule EXCEPT its DB id and set membership
    (the destination is chosen at import time, so `routing_set_id` is
    deliberately absent)."""
    name: str = ""
    enabled: bool = True
    rule_type: str
    match_value: str
    action: str
    order: int = 100

    @field_validator("rule_type")
    @classmethod
    def _vt(cls, v: str) -> str:
        valid = {"mac", "src_ip", "dst_ip", "domain", "port", "protocol", "geoip", "geosite"}
        if v not in valid:
            raise ValueError(f"rule_type must be one of {valid}")
        return v

    @field_validator("action")
    @classmethod
    def _va(cls, v: str) -> str:
        if v not in ("proxy", "direct", "block") and not v.startswith("node:") and not v.startswith("balancer:"):
            raise ValueError("action must be proxy|direct|block|node:<id>|balancer:<id>")
        return v


class RoutingImportDestination(BaseModel):
    """Where imported rules land. `kind`:
      * "global"  → global rules (routing_set_id = NULL)
      * "set"     → an existing set (`set_id`) OR a new one (`new_set_name`)
    """
    kind: str  # "global" | "set"
    set_id: Optional[int] = None
    new_set_name: Optional[str] = None
    new_set_description: Optional[str] = None

    @field_validator("kind")
    @classmethod
    def _vk(cls, v: str) -> str:
        if v not in ("global", "set"):
            raise ValueError("destination kind must be 'global' or 'set'")
        return v


class RoutingImportPreviewRequest(BaseModel):
    rules: List[RoutingPortRule]
    destination: RoutingImportDestination


class RoutingImportConflict(BaseModel):
    """An incoming rule whose (rule_type, normalized match) already exists
    in the destination with a DIFFERENT action — the kind of contradiction
    that makes xray's first-match-wins behaviour ambiguous. Surfaced for
    the user to resolve before commit."""
    key: str                       # stable id: f"{rule_type}|{norm_match}"
    rule_type: str
    match_value: str
    incoming_action: str
    incoming_name: str
    existing_action: str
    existing_rule_ids: List[int]


class RoutingImportPreviewResult(BaseModel):
    destination_label: str
    destination_exists: bool       # False when kind=set + new_set_name (not created yet)
    total_in_file: int
    will_add: int
    identical_skipped: int         # same key AND same action already present
    collapsed_duplicates: int      # duplicate keys within the file itself
    invalid_skipped: int           # missing node:/balancer: target, or geo tag not loaded
    conflicts: List[RoutingImportConflict]


class RoutingImportResolution(BaseModel):
    key: str
    choice: str                    # "keep" (skip incoming) | "replace" (delete existing, use incoming)

    @field_validator("choice")
    @classmethod
    def _vc(cls, v: str) -> str:
        if v not in ("keep", "replace"):
            raise ValueError("choice must be 'keep' or 'replace'")
        return v


class RoutingImportCommitRequest(BaseModel):
    rules: List[RoutingPortRule]
    destination: RoutingImportDestination
    resolutions: List[RoutingImportResolution] = []
    default_conflict_choice: str = "keep"   # applied to any conflict not in `resolutions`


class RoutingImportCommitResult(BaseModel):
    set_id: Optional[int]          # destination set id (created if new); None = global
    set_name: Optional[str] = None
    added: int
    skipped_identical: int
    replaced: int
    skipped_conflicts: int
    invalid_skipped: int
    collapsed_duplicates: int


# ─── Routing Sets ─────────────────────────────────────────────────────────────

class RoutingSetBase(BaseModel):
    name: str
    description: Optional[str] = None
    order: int = 0

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        if len(v) > 64:
            raise ValueError("name must be ≤ 64 chars")
        return v


class RoutingSetCreate(RoutingSetBase):
    pass


class RoutingSetUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    order: Optional[int] = None

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("name must not be empty")
        if len(v) > 64:
            raise ValueError("name must be ≤ 64 chars")
        return v


class RoutingSetRead(RoutingSetBase):
    id: int
    tproxy_port: int
    created_at: datetime

    class Config:
        from_attributes = True


class RoutingSetCapacity(BaseModel):
    """Surface for /api/routing-sets/capacity — shows the operator how
    many more sets can be created before the TPROXY port range is
    exhausted. The frontend uses this to disable the "+" button at limit.
    """
    used: int
    maximum: int
    available: int


# ─── Bulk Rule Import ─────────────────────────────────────────────────────────

class BulkRuleCreate(BaseModel):
    rule_type: str
    action: str
    values: str  # newline or comma separated
    enabled: bool = True

    @field_validator("rule_type")
    @classmethod
    def validate_rule_type(cls, v: str) -> str:
        valid = {"mac", "src_ip", "dst_ip", "domain", "port", "protocol", "geoip", "geosite"}
        if v not in valid:
            raise ValueError(f"rule_type must be one of {valid}")
        return v

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in ("proxy", "direct", "block") and not v.startswith("node:") and not v.startswith("balancer:"):
            raise ValueError("action must be proxy|direct|block|node:<id>|balancer:<id>")
        return v


class BulkRuleResult(BaseModel):
    created: int
    rule_ids: List[int]


# ─── NodeCircle ───────────────────────────────────────────────────────────────

class NodeCircleBase(BaseModel):
    name: str
    enabled: bool = False
    node_ids: List[int] = []
    mode: str = "sequential"
    interval_min: int = 5
    interval_max: int = 15
    # Candidate filters (0 = disabled). See circle_scheduler.
    max_latency_ms: int = 0
    min_speed_mbps: float = 0.0
    # Link to a Subscription (None = manually managed). When set, the
    # subscription's refresh keeps this circle's membership in sync.
    subscription_id: Optional[int] = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("sequential", "random", "best"):
            raise ValueError("mode must be sequential|random|best")
        return v

    @field_validator("interval_min")
    @classmethod
    def validate_interval_min(cls, v: int) -> int:
        if v < 1:
            raise ValueError("interval_min must be at least 1 minute")
        return v

    @model_validator(mode="after")
    def validate_interval_range(self):
        if self.interval_max < self.interval_min:
            raise ValueError("interval_max must be >= interval_min")
        return self

class NodeCircleCreate(NodeCircleBase):
    pass

class NodeCircleUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    node_ids: Optional[List[int]] = None
    mode: Optional[str] = None
    interval_min: Optional[int] = None
    interval_max: Optional[int] = None
    max_latency_ms: Optional[int] = None
    min_speed_mbps: Optional[float] = None
    # Send an explicit `null` to unlink (falls back to manual management).
    subscription_id: Optional[int] = None

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, v):
        if v is None:
            return v
        if v not in ("sequential", "random", "best"):
            raise ValueError("mode must be sequential|random|best")
        return v

class NodeCircleRead(NodeCircleBase):
    id: int
    current_index: int = 0
    last_rotated: Optional[datetime] = None
    # Current active node name from the circle
    current_node_name: Optional[str] = None

    @field_validator("node_ids", mode="before")
    @classmethod
    def parse_node_ids(cls, v):
        if isinstance(v, str):
            import json
            return json.loads(v)
        return v

    class Config:
        from_attributes = True


_AUTOCHECK_SCOPES = ("all", "subscription", "group", "nodes")


class AutoCheckRead(BaseModel):
    enabled: bool
    interval_minutes: int
    scope_kind: str
    scope_value: str
    last_sweep: Optional[datetime] = None
    # Live status (filled by the endpoint, not stored).
    is_sweeping: bool = False

    class Config:
        from_attributes = True


class AutoCheckUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = None
    scope_kind: Optional[str] = None
    scope_value: Optional[str] = None

    @field_validator("interval_minutes")
    @classmethod
    def validate_interval(cls, v):
        if v is not None and v < 1:
            raise ValueError("interval_minutes must be at least 1")
        return v

    @field_validator("scope_kind")
    @classmethod
    def validate_scope_kind(cls, v):
        if v is not None and v not in _AUTOCHECK_SCOPES:
            raise ValueError(f"scope_kind must be one of {_AUTOCHECK_SCOPES}")
        return v


# ─── User-Agent templates ─────────────────────────────────────────────────────

class UserAgentTemplateBase(BaseModel):
    """Editable fields of a UA template.

    Validation is delegated to `app.core.ua_templates` so the API, the
    import endpoint and the seeding path all enforce one ruleset. The
    header checks are not cosmetic: httpx 0.28 happily forwards a CR/LF
    inside a header value (request splitting) and raises on non-ASCII,
    so both have to be rejected before the value reaches the DB.
    """
    key: str
    name: str
    user_agent: str
    headers: Dict[str, str] = {}
    description: Optional[str] = None
    order: int = 100

    @field_validator("key")
    @classmethod
    def validate_key(cls, v: str) -> str:
        from app.core.ua_templates import validate_key
        return validate_key(v)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        from app.core.ua_templates import validate_name
        return validate_name(v)

    @field_validator("user_agent")
    @classmethod
    def validate_user_agent(cls, v: str) -> str:
        from app.core.ua_templates import validate_user_agent
        return validate_user_agent(v)

    @field_validator("headers", mode="before")
    @classmethod
    def validate_headers_field(cls, v):
        from app.core.ua_templates import parse_headers, validate_headers
        # Accept the raw DB column (a JSON string) so the same base class
        # can validate an ORM row on the way out.
        if isinstance(v, str):
            v = parse_headers(v)
        return validate_headers(v)

    @field_validator("description")
    @classmethod
    def validate_description(cls, v: Optional[str]) -> Optional[str]:
        from app.core.ua_templates import validate_description
        return validate_description(v)


class UserAgentTemplateCreate(UserAgentTemplateBase):
    pass


class UserAgentTemplateUpdate(BaseModel):
    """PATCH body — every field optional, same validators as create.

    `mode="before"` + an explicit None passthrough on each validator so
    `exclude_unset` semantics survive: an omitted field stays omitted,
    and an explicit `null` clears `description`.
    """
    key: Optional[str] = None
    name: Optional[str] = None
    user_agent: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    description: Optional[str] = None
    order: Optional[int] = None

    @field_validator("key")
    @classmethod
    def validate_key(cls, v):
        if v is None:
            return None
        from app.core.ua_templates import validate_key
        return validate_key(v)

    @field_validator("name")
    @classmethod
    def validate_name(cls, v):
        if v is None:
            return None
        from app.core.ua_templates import validate_name
        return validate_name(v)

    @field_validator("user_agent")
    @classmethod
    def validate_user_agent(cls, v):
        if v is None:
            return None
        from app.core.ua_templates import validate_user_agent
        return validate_user_agent(v)

    @field_validator("headers", mode="before")
    @classmethod
    def validate_headers_field(cls, v):
        if v is None:
            return None
        from app.core.ua_templates import parse_headers, validate_headers
        if isinstance(v, str):
            v = parse_headers(v)
        return validate_headers(v)

    @field_validator("description")
    @classmethod
    def validate_description(cls, v):
        if v is None:
            return None
        from app.core.ua_templates import validate_description
        return validate_description(v)


class UserAgentTemplateRead(BaseModel):
    """Output model — intentionally NOT a subclass of the validating base.

    A stored row can hold a headers blob today's validator would reject
    (hand-edited DB, or a rule tightened after the row was written).
    Listing templates must not 500 over one bad entry, so the router
    passes headers through `ua_templates.sanitize_headers`, which drops
    offenders and logs them, and nothing here re-validates.
    """
    id: int
    key: str
    name: str
    user_agent: str
    headers: Dict[str, str] = {}
    description: Optional[str] = None
    builtin: bool = False
    order: int = 100
    # How many subscriptions currently reference this template's key.
    # Filled by the list/read endpoints; drives the "in use" column and
    # the delete guard's message.
    usage_count: int = 0

    class Config:
        from_attributes = True


class UserAgentTemplateImportResponse(BaseModel):
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    errors: List[str] = []


# ─── Subscription ─────────────────────────────────────────────────────────────

class SubscriptionBase(BaseModel):
    name: str
    url: str
    enabled: bool = True
    ua: str = "clash"
    custom_ua: Optional[str] = None
    filter_regex: Optional[str] = None
    auto_update: bool = False
    update_interval: int = 86400
    rotate_hwid: bool = False

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if v is None:
            return v
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        from urllib.parse import urlparse
        import ipaddress
        import socket
        host = urlparse(v).hostname or ""
        if not host:
            raise ValueError("URL has no hostname")
        # Block obvious internal names
        if host in ("localhost", "0.0.0.0", "::1") or host.startswith("169.254."):
            raise ValueError("URL must not point to internal addresses")
        # Check if host is an IP literal
        try:
            ip = ipaddress.ip_address(host)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise ValueError("URL must not point to private/internal addresses")
        except ValueError as e:
            if "private" in str(e) or "internal" in str(e):
                raise
            # hostname — resolve and check all IPs
            try:
                resolved = socket.getaddrinfo(host, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                for _, _, _, _, addr in resolved:
                    resolved_ip = ipaddress.ip_address(addr[0])
                    if resolved_ip.is_private or resolved_ip.is_loopback or resolved_ip.is_link_local:
                        raise ValueError(f"URL hostname resolves to private address ({addr[0]})")
            except socket.gaierror:
                pass  # DNS resolution failed — let httpx handle it later
        return v


class SubscriptionCreate(SubscriptionBase):
    pass


class SubscriptionUpdate(SubscriptionBase):
    name: Optional[str] = None
    url: Optional[str] = None


class SubscriptionRead(SubscriptionBase):
    id: int
    last_updated: Optional[datetime] = None
    node_count: int = 0
    last_error: Optional[str] = None

    class Config:
        from_attributes = True


# ─── System ───────────────────────────────────────────────────────────────────

class SystemStatus(BaseModel):
    running: bool
    pid: Optional[int]
    uptime_seconds: Optional[float]
    mode: str
    active_node_id: Optional[int]
    active_node_name: Optional[str]
    nftables_active: bool
    # `version` holds the xray binary version (kept for backward compat
    # with older frontends); `app_version` is the PiTun backend release
    # from `app.config.APP_VERSION`.
    version: Optional[str]
    app_version: Optional[str] = None
    # Last xray config validation error, surfaced verbatim from the
    # `last_xray_validation_error` Settings key written by
    # `config_gen.write_config()` since v1.2.5. Empty string / None
    # means the most recent validation passed. Frontend uses this to
    # show a yellow-red banner in the topbar / Routing page so admins
    # don't stare at a 503 /health with no clue why xray won't start.
    last_xray_validation_error: Optional[str] = None


class SystemVersions(BaseModel):
    """Complete version snapshot for the About / version popover.

    Gathered live on each request — nothing here is cached. PiTun-owned
    versions come from `app.config` (single source of truth); everything
    else is introspected at request time (xray binary, /proc, /etc/os-release,
    docker client, SQLite alembic table, xray geo-data file mtimes).

    Every section is best-effort: a failing docker socket or missing geo
    file degrades the field to `None` rather than failing the whole
    response. The frontend renders "—" for absent values.
    """
    pitun: "PitunVersions"
    runtime: "RuntimeVersions"
    third_party: "ThirdPartyVersions"
    host: "HostVersions"
    data: "DataVersions"


class PitunVersions(BaseModel):
    backend: str                             # APP_VERSION
    naive_image: Optional[str] = None        # tied to APP_VERSION when built via build-offline-bundle.sh


class RuntimeVersions(BaseModel):
    xray: Optional[str] = None
    python: Optional[str] = None


class ThirdPartyVersions(BaseModel):
    nginx: Optional[str] = None              # container image tag (e.g. "1.25-alpine")
    socket_proxy: Optional[str] = None


class HostVersions(BaseModel):
    kernel: Optional[str] = None
    os: Optional[str] = None
    docker: Optional[str] = None
    arch: Optional[str] = None


class DataVersions(BaseModel):
    alembic_rev: Optional[str] = None        # current migration head in the DB
    geoip_mtime: Optional[str] = None        # ISO timestamp of geoip.dat on disk
    geosite_mtime: Optional[str] = None


class UpdateCheckResult(BaseModel):
    """What `/system/update/check` reports. Advisory only — nothing is
    applied until the operator asks for it."""
    current: str
    latest: Optional[str] = None
    update_available: bool = False
    # Which route actually reached GitHub: "active node" | "direct" |
    # "unreachable". Surfaced so the operator can tell "no update" from
    # "we could not look".
    network_path: str = "unknown"
    published_at: Optional[str] = None
    notes: Optional[str] = None
    error: Optional[str] = None
    # True when installing `latest` would REMOVE the in-UI updater —
    # i.e. a downgrade below the version that introduced it. The box
    # stays updatable from a shell, which is exactly what the operator
    # needs to be told before, not after.
    target_lacks_update_ui: bool = False
    update_ui_since: str = "1.4.8"


class UpdateStartRequest(BaseModel):
    # Empty means "whatever GitHub calls latest".
    version: Optional[str] = None
    # Re-install even when the versions match — the repair path.
    force: bool = False
    prerelease: bool = False


class UpdateStatus(BaseModel):
    """Mirror of the agent's status file.

    Written by the host agent, not by us: the update restarts this
    container, so the only progress channel that outlives it is a file on
    the shared volume.
    """
    state: str = "idle"        # idle|queued|running|done|failed|available|unknown
    pct: int = 0
    step: str = ""
    message: str = ""
    ok: Optional[bool] = None
    from_version: Optional[str] = Field(default=None, alias="from")
    to_version: Optional[str] = Field(default=None, alias="to")
    updated_at: Optional[str] = None
    # True while a request is on disk that the agent has not consumed —
    # if this stays true, the agent is not installed or not running.
    request_pending: bool = False

    class Config:
        populate_by_name = True


class ModeUpdate(BaseModel):
    mode: str

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("global", "rules", "bypass"):
            raise ValueError("mode must be global|rules|bypass")
        return v


class ActiveNodeUpdate(BaseModel):
    node_id: int


class SettingsRead(BaseModel):
    mode: str
    active_node_id: Optional[int]
    failover_enabled: bool
    failover_node_ids: List[int]
    # Network
    interface: str = "eth0"
    gateway_ip: str = ""
    lan_cidr: str = ""
    router_ip: str = ""
    # Ports
    tproxy_port_tcp: int
    tproxy_port_udp: int
    socks_port: int
    http_port: int
    dns_port: int
    dns_mode: str
    dns_upstream: str
    dns_upstream_secondary: Optional[str] = None
    dns_fallback: Optional[str] = None
    fakedns_enabled: Optional[bool] = None
    fakedns_pool: Optional[str] = None
    fakedns_pool_size: Optional[int] = None
    dns_sniffing: Optional[bool] = None
    bypass_cn_dns: Optional[bool] = None
    # TLS-ClientHello fragmentation (anti-DPI, client-side). See
    # config_gen._apply_fragment. Off by default.
    xray_fragment_enabled: Optional[bool] = None
    xray_fragment_packets: Optional[str] = None
    xray_fragment_length: Optional[str] = None
    xray_fragment_interval: Optional[str] = None
    bypass_ru_dns: Optional[bool] = None
    bypass_private: bool
    log_level: str
    geoip_url: str
    geosite_url: str
    geoip_mmdb_url: Optional[str] = None
    # TUN mode
    inbound_mode: str = "tproxy"
    tun_address: str = "10.0.0.1/30"
    tun_mtu: int = 9000
    tun_stack: str = "system"
    tun_auto_route: bool = True
    tun_strict_route: bool = True
    # QUIC blocking
    block_quic: bool = True
    # Kill switch
    kill_switch: bool = False
    auto_restart_xray: bool = True
    # DNS query logging
    dns_query_log_enabled: bool = False
    # Device routing
    device_routing_mode: str = "all"  # "all" | "include_only" | "exclude_list"
    # IPv6
    disable_ipv6: bool = False
    # DNS over TCP
    dns_over_tcp: bool = False
    # Health check
    health_interval: int = 30
    health_timeout: int = 5
    health_full_check_interval: int = 300
    # GeoScheduler
    geo_auto_update: bool = True
    geo_update_interval_days: int = 1
    geo_update_window_start: int = 4
    geo_update_window_end: int = 6
    # Display + scheduling timezone (IANA, e.g. "Europe/Moscow"). UTC default.
    timezone: str = "UTC"
    # LAN proxy auth (since v1.3.0-beta.6) — covers SOCKS5 and HTTP
    # inbounds. TPROXY stays passwordless (it's transparent + nftables-
    # gated). One pair of credentials for both inbounds. Plaintext —
    # see _apply_xray_config in core/config_gen for the threat-model
    # rationale.
    lan_proxy_auth_enabled: bool = False
    lan_proxy_auth_user: str = ""
    lan_proxy_auth_pass: str = ""


class SettingsUpdate(BaseModel):
    mode: Optional[str] = None
    active_node_id: Optional[int] = None
    failover_enabled: Optional[bool] = None
    failover_node_ids: Optional[List[int]] = None
    # Network
    interface: Optional[str] = None
    gateway_ip: Optional[str] = None
    lan_cidr: Optional[str] = None
    router_ip: Optional[str] = None
    # Ports
    tproxy_port_tcp: Optional[int] = None
    tproxy_port_udp: Optional[int] = None
    socks_port: Optional[int] = None
    http_port: Optional[int] = None
    dns_port: Optional[int] = None
    dns_mode: Optional[str] = None
    dns_upstream: Optional[str] = None
    dns_upstream_secondary: Optional[str] = None
    dns_fallback: Optional[str] = None
    fakedns_enabled: Optional[bool] = None
    fakedns_pool: Optional[str] = None
    fakedns_pool_size: Optional[int] = None
    dns_sniffing: Optional[bool] = None
    bypass_cn_dns: Optional[bool] = None
    # TLS-ClientHello fragmentation (anti-DPI, client-side). See
    # config_gen._apply_fragment. Off by default.
    xray_fragment_enabled: Optional[bool] = None
    xray_fragment_packets: Optional[str] = None
    xray_fragment_length: Optional[str] = None
    xray_fragment_interval: Optional[str] = None
    bypass_ru_dns: Optional[bool] = None
    bypass_private: Optional[bool] = None
    log_level: Optional[str] = None
    geoip_url: Optional[str] = None
    geosite_url: Optional[str] = None
    geoip_mmdb_url: Optional[str] = None
    # TUN mode
    inbound_mode: Optional[str] = None
    tun_address: Optional[str] = None
    tun_mtu: Optional[int] = None
    tun_stack: Optional[str] = None
    tun_auto_route: Optional[bool] = None
    tun_strict_route: Optional[bool] = None
    # QUIC blocking
    block_quic: Optional[bool] = None
    # Kill switch
    kill_switch: Optional[bool] = None
    auto_restart_xray: Optional[bool] = None
    # DNS query logging
    dns_query_log_enabled: Optional[bool] = None
    # Device routing
    device_routing_mode: Optional[str] = None
    # IPv6
    disable_ipv6: Optional[bool] = None
    # DNS over TCP
    dns_over_tcp: Optional[bool] = None
    # Health check
    health_interval: Optional[int] = None
    health_timeout: Optional[int] = None
    health_full_check_interval: Optional[int] = None
    # GeoScheduler
    geo_auto_update: Optional[bool] = None
    geo_update_interval_days: Optional[int] = None
    geo_update_window_start: Optional[int] = None
    geo_update_window_end: Optional[int] = None
    # Timezone
    timezone: Optional[str] = None
    # LAN proxy auth (see SettingsRead docstring)
    lan_proxy_auth_enabled: Optional[bool] = None
    lan_proxy_auth_user: Optional[str] = None
    lan_proxy_auth_pass: Optional[str] = None


# ─── GeoData ──────────────────────────────────────────────────────────────────

class GeoDataStatus(BaseModel):
    geoip_exists: bool
    geoip_size: Optional[int]
    geoip_mtime: Optional[datetime]
    geosite_exists: bool
    geosite_size: Optional[int]
    geosite_mtime: Optional[datetime]
    mmdb_exists: bool = False
    mmdb_size: Optional[int] = None
    mmdb_mtime: Optional[datetime] = None


class GeoDataUpdateRequest(BaseModel):
    geoip_url: Optional[str] = None
    geosite_url: Optional[str] = None
    mmdb_url: Optional[str] = None
    type: Optional[str] = None  # "geoip" | "geosite" | "mmdb" | "all"


# Live-progress projection of `core.geo_progress.GeoUpdateState`.
# Polled by the frontend ~2 Hz while a job is active. `started_at`
# / `finished_at` are `time.monotonic()` floats in seconds — small
# integers, easy to render as elapsed-time on the client side
# (no timezone juggling). `error` is short user-facing text; full
# stack traces stay in backend logs.
class GeoUpdateFileProgress(BaseModel):
    stage: str  # queued | downloading | verifying | applying | done | failed
    bytes_downloaded: int = 0
    bytes_total: Optional[int] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    source_url: Optional[str] = None


class GeoUpdateProgress(BaseModel):
    """One job's progress snapshot. Empty dict + active=false on
    first call after backend start (no job has run yet)."""
    job_id: str
    active: bool
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    tag_cache_refreshed: bool = False
    files: dict[str, GeoUpdateFileProgress] = {}


# ─── DNS ──────────────────────────────────────────────────────────────────────

class DNSRuleCreate(BaseModel):
    name: str = ""
    enabled: bool = True
    domain_match: str
    dns_server: str
    dns_type: str = "plain"
    order: int = 100


class DNSRuleRead(DNSRuleCreate):
    id: int

    class Config:
        from_attributes = True


class DNSRuleUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    domain_match: Optional[str] = None
    dns_server: Optional[str] = None
    dns_type: Optional[str] = None
    order: Optional[int] = None


class DNSSettingsRead(BaseModel):
    dns_mode: str
    dns_upstream: str
    dns_upstream_secondary: Optional[str] = None
    dns_fallback: Optional[str] = None
    dns_port: int
    fakedns_enabled: bool = False
    fakedns_pool: str = "198.18.0.0/15"
    fakedns_pool_size: int = 65535
    dns_sniffing: bool = True
    bypass_cn_dns: bool = False
    bypass_ru_dns: bool = False
    dns_disable_fallback: bool = True
    # UseIP | UseIPv4 | UseIPv6 — UseIPv4 default closes the IPv6 leak.
    dns_query_strategy: str = "UseIPv4"
    # Comma-separated IPv4 fallback resolvers for the BOX's own DNS
    # (subscriptions/geo/panels/healthchecks). Empty = router-only.
    host_fallback_dns: str = ""


class DNSSettingsUpdate(BaseModel):
    dns_mode: Optional[str] = None
    dns_upstream: Optional[str] = None
    dns_upstream_secondary: Optional[str] = None
    dns_fallback: Optional[str] = None
    dns_port: Optional[int] = None
    fakedns_enabled: Optional[bool] = None
    fakedns_pool: Optional[str] = None
    fakedns_pool_size: Optional[int] = None
    dns_sniffing: Optional[bool] = None
    bypass_cn_dns: Optional[bool] = None
    bypass_ru_dns: Optional[bool] = None
    dns_disable_fallback: Optional[bool] = None
    dns_query_strategy: Optional[str] = None
    host_fallback_dns: Optional[str] = None


class DNSTestRequest(BaseModel):
    domain: str
    server: Optional[str] = None


class DNSTestResult(BaseModel):
    resolved_ips: List[str]
    latency_ms: int
    server_used: str
    error: Optional[str] = None


# ─── Route Explainer ──────────────────────────────────────────────────────────

class RouteExplainRequest(BaseModel):
    target: str                       # domain or IP
    port: int = 443
    protocol: str = "tcp"             # tcp | udp
    from_mac: Optional[str] = None    # optional — evaluate as this device (routing set)
    verify_routing: bool = False      # B: run live-xray probe for geosite/geoip ground truth
    test_reachability: bool = False   # actually connect through the decided path


class RouteExplainDns(BaseModel):
    is_ip: bool
    matched_rule_id: Optional[int] = None
    matched_rule_name: Optional[str] = None
    matched_pattern: Optional[str] = None
    server: Optional[str] = None
    server_type: Optional[str] = None
    uses_global_upstream: bool = False
    query_strategy: str = "UseIPv4"
    geosite_uncertain: bool = False
    resolved_ips: List[str] = []
    resolve_error: Optional[str] = None
    note: Optional[str] = None


class RouteExplainRouting(BaseModel):
    matched_rule_id: Optional[int] = None
    matched_rule_name: Optional[str] = None
    matched_rule_type: Optional[str] = None
    matched_value: Optional[str] = None
    action: Optional[str] = None
    outbound: Optional[str] = None
    outbound_label: Optional[str] = None
    certain: bool = True
    blocking_rule: Optional[str] = None
    rules_evaluated: int = 0
    set_id: Optional[int] = None
    set_name: Optional[str] = None
    method: str = "python_matcher"     # python_matcher | xray_probe
    probe_detail: Optional[str] = None
    notes: List[str] = []


class RouteExplainReachability(BaseModel):
    tested: bool = False
    ok: Optional[bool] = None
    http_code: Optional[int] = None
    latency_ms: Optional[int] = None
    via: Optional[str] = None          # direct | proxy(node-N)
    detail: Optional[str] = None


class RouteExplainResult(BaseModel):
    target: str
    port: int
    protocol: str
    is_ip: bool
    dns: RouteExplainDns
    routing: RouteExplainRouting
    reachability: RouteExplainReachability


# ─── Health ───────────────────────────────────────────────────────────────────

class HealthResult(BaseModel):
    node_id: int
    node_name: str
    is_online: bool
    latency_ms: Optional[int]
    error: Optional[str] = None


class SpeedTestResult(BaseModel):
    node_id: int
    node_name: str
    download_mbps: Optional[float]          # average after warm-up
    max_mbps: Optional[float] = None        # peak steady window
    reachable: Optional[bool] = None        # generate_204 succeeded through the node
    latency_ms: Optional[int] = None        # reachability round-trip
    error: Optional[str] = None


# ─── ARP / Device ─────────────────────────────────────────────────────────────

class ArpDevice(BaseModel):
    ip: str
    mac: str
    hostname: Optional[str] = None
    vendor: Optional[str] = None
    rule_action: Optional[str] = None  # what routing action applies to this device
    # v1.4: read-only routing set membership, resolved by MAC against
    # the Device table. None when the MAC isn't tracked as a managed
    # device, OR the device is unassigned (global-only). The frontend
    # shows this as a badge with a deep-link to the Devices page for
    # actual reassignment (single source of truth for set membership
    # stays on the Devices page).
    routing_set_id: Optional[int] = None
    routing_set_name: Optional[str] = None


# ─── Device Management ───────────────────────────────────────────────────────

class DeviceRead(BaseModel):
    id: int
    mac: str
    ip: Optional[str] = None
    hostname: Optional[str] = None
    name: Optional[str] = None
    vendor: Optional[str] = None
    first_seen: datetime
    last_seen: datetime
    is_online: bool = True
    routing_policy: str = "default"
    # NULL = unassigned (only global routing rules apply). Set to a
    # RoutingSet.id to apply that set's rules in addition to globals.
    routing_set_id: Optional[int] = None

    class Config:
        from_attributes = True


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    routing_policy: Optional[str] = None
    # Sentinel-free: pass `null` to unassign (back to global-only),
    # omit the field entirely to leave the current assignment alone.
    # FastAPI/pydantic v2 distinguishes "field omitted" from
    # "explicit null" via `model_fields_set`; the API handler should
    # check `routing_set_id` is in `update.model_fields_set` before
    # touching the column.
    routing_set_id: Optional[int] = None

    @field_validator("routing_policy", mode="before")
    @classmethod
    def validate_routing_policy(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        valid = {"default", "include", "exclude"}
        if v not in valid:
            raise ValueError(f"routing_policy must be one of {valid}")
        return v


class DeviceBulkUpdate(BaseModel):
    device_ids: List[int]
    routing_policy: str

    @field_validator("routing_policy")
    @classmethod
    def validate_routing_policy(cls, v: str) -> str:
        valid = {"default", "include", "exclude"}
        if v not in valid:
            raise ValueError(f"routing_policy must be one of {valid}")
        return v


class DeviceBulkSetAssign(BaseModel):
    """Bulk-assign N devices to a RoutingSet (or unassign with set_id=None)."""

    device_ids: List[int]
    # None = unassign (move to "global only"). Otherwise the target set id.
    routing_set_id: Optional[int] = None


class DeviceScanResult(BaseModel):
    discovered: int
    updated: int
    total: int


# ─── DNS Query Log ─────────────────────────────────────────────────────────────

class DNSQueryLogRead(BaseModel):
    id: int
    timestamp: datetime
    domain: str
    resolved_ips: List[str]
    server_used: str
    latency_ms: Optional[int] = None
    query_type: str
    cache_hit: bool
    rule_matched: Optional[str] = None

    @field_validator("resolved_ips", mode="before")
    @classmethod
    def parse_ips(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return []
        return v

    class Config:
        from_attributes = True


class DNSQueryStats(BaseModel):
    total_queries: int
    unique_domains: int
    cache_hit_rate: float  # 0.0 - 1.0
    top_domains: List[Any]  # [{"domain": "google.com", "count": 42}]
    queries_last_hour: int


# ─── Balancer Groups ───────────────────────────────────────────────────────────

class BalancerGroupBase(BaseModel):
    name: str
    enabled: bool = True
    node_ids: List[int] = []
    strategy: str = "leastPing"

    @field_validator("strategy")
    @classmethod
    def validate_strategy(cls, v: str) -> str:
        if v not in ("leastPing", "random"):
            raise ValueError("strategy must be leastPing|random")
        return v


class BalancerGroupCreate(BalancerGroupBase):
    pass


class BalancerGroupUpdate(BalancerGroupBase):
    name: Optional[str] = None
    node_ids: Optional[List[int]] = None
    strategy: Optional[str] = None

    @field_validator("strategy", mode="before")
    @classmethod
    def validate_strategy(cls, v):  # type: ignore[override]
        if v is None:
            return v
        if v not in ("leastPing", "random"):
            raise ValueError("strategy must be leastPing|random")
        return v


class BalancerGroupRead(BalancerGroupBase):
    id: int

    @field_validator("node_ids", mode="before")
    @classmethod
    def parse_node_ids(cls, v: Any) -> List[int]:
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return []
        return v

    class Config:
        from_attributes = True


# ─── Recent Events ─────────────────────────────────────────────────────────────
#
# Read-only schema — events are written by the backend via
# `app.core.events.record_event`, never directly through the API.

class EventRead(BaseModel):
    id: int
    timestamp: datetime
    category: str
    severity: str
    title: str
    details: Optional[str] = None
    entity_id: Optional[int] = None

    class Config:
        from_attributes = True
