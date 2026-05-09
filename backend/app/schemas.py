from pydantic import BaseModel, field_validator, model_validator
from typing import Optional, List, Any
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

    @field_validator("transport")
    @classmethod
    def validate_transport(cls, v: str) -> str:
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
    # Multi-client deployment provenance (since v1.3.0-beta.4). When this
    # Node was exported from a DeploymentClient (e.g. WireGuard peer),
    # keep the link so the UI can render "from <server name>" alongside
    # the node + raise an `orphan` warning if the upstream peer is gone.
    from_deployment_client_id: Optional[int] = None
    client_orphan: bool = False

    class Config:
        from_attributes = True


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
        # body even reaches the endpoint.
        if v not in ("naive", "wireguard"):
            raise ValueError(
                f"Unsupported protocol: {v!r} (expected 'naive' or 'wireguard')"
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
        # of v1.3.0-beta.4 we accept naive (single-tunnel) and wireguard
        # (multi-client via DeploymentClient). Loosen further when Hy2 /
        # XTLS deployments land. NB: the read path also uses this base
        # — when adding a new protocol, both ServerDeployRequest's
        # validator (in core/deploy.SUPPORTED_PROTOCOLS) AND this set
        # need to grow in lockstep, otherwise listing existing rows
        # 500s on the new protocol.
        valid = {"naive", "wireguard"}
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

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("sequential", "random"):
            raise ValueError("mode must be sequential|random")
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

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, v):
        if v is None:
            return v
        if v not in ("sequential", "random"):
            raise ValueError("mode must be sequential|random")
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


class DNSTestRequest(BaseModel):
    domain: str
    server: Optional[str] = None


class DNSTestResult(BaseModel):
    resolved_ips: List[str]
    latency_ms: int
    server_used: str
    error: Optional[str] = None


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
    download_mbps: Optional[float]
    error: Optional[str] = None


# ─── ARP / Device ─────────────────────────────────────────────────────────────

class ArpDevice(BaseModel):
    ip: str
    mac: str
    hostname: Optional[str] = None
    vendor: Optional[str] = None
    rule_action: Optional[str] = None  # what routing action applies to this device


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

    class Config:
        from_attributes = True


class DeviceUpdate(BaseModel):
    name: Optional[str] = None
    routing_policy: Optional[str] = None

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
