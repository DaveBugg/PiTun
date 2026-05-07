from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint
from typing import Optional
from datetime import datetime, timezone


class DNSQueryLog(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    domain: str = Field(index=True)
    resolved_ips: str = "[]"          # JSON: ["1.2.3.4"]
    server_used: str = ""
    latency_ms: Optional[int] = None
    query_type: str = "A"             # A | AAAA | CNAME
    rule_matched: Optional[str] = None
    cache_hit: bool = False


class Node(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    enabled: bool = True

    # Protocol: vless|vmess|trojan|ss|wireguard|socks|hy2
    protocol: str
    address: str
    port: int

    # Auth
    uuid: Optional[str] = None
    password: Optional[str] = None

    # Transport: tcp|ws|grpc|h2|xhttp|httpupgrade|kcp|quic
    transport: str = "tcp"

    # TLS: none|tls|reality
    tls: str = "none"
    sni: Optional[str] = None
    fingerprint: str = "chrome"
    alpn: Optional[str] = None
    allow_insecure: bool = False

    # WebSocket
    ws_path: str = "/"
    ws_host: Optional[str] = None
    ws_headers: Optional[str] = None  # JSON string

    # gRPC
    grpc_service: Optional[str] = None
    grpc_mode: str = "gun"

    # H2 / XHTTP / HTTPUpgrade
    http_path: str = "/"
    http_host: Optional[str] = None

    # mKCP
    kcp_seed: Optional[str] = None
    kcp_header: str = "none"

    # Reality
    reality_pbk: Optional[str] = None
    reality_sid: Optional[str] = None
    reality_spx: Optional[str] = None

    # XTLS Vision
    flow: Optional[str] = None

    # WireGuard specifics
    wg_private_key: Optional[str] = None
    wg_public_key: Optional[str] = None
    wg_preshared_key: Optional[str] = None
    wg_endpoint: Optional[str] = None
    wg_mtu: int = 1420
    wg_reserved: Optional[str] = None  # JSON "[0,0,0]"
    wg_local_address: Optional[str] = None  # "10.0.0.2/32,..."

    # Hysteria2
    hy2_obfs: Optional[str] = None
    hy2_obfs_password: Optional[str] = None

    # NaiveProxy (HTTPS forward proxy via Caddy + forwardproxy plugin)
    # Auth reuses `uuid` (user) and `password` (pass) — same convention as socks.
    # Extra fields:
    #   internal_port — 127.0.0.1 port of the sidecar container's SOCKS listener
    #                   (allocated from NAIVE_PORT_RANGE_* on first enable)
    #   naive_padding — enable HTTP/2 padding obfuscation (recommended on)
    internal_port: Optional[int] = None
    naive_padding: bool = True

    # Grouping / meta
    group: Optional[str] = None
    note: Optional[str] = None
    subscription_id: Optional[int] = Field(default=None, foreign_key="subscription.id")

    # Health
    latency_ms: Optional[int] = None
    last_check: Optional[datetime] = None
    is_online: bool = True

    # Order in list
    order: int = 0

    # Chain tunnel: if set, this node's outbound traffic goes through another node
    chain_node_id: Optional[int] = None

    # Optional link to the Server this node is deployed on. Purely informational
    # — set when the user uses the Servers tab to spin up a VPN endpoint and
    # wants to remember which physical machine hosts it. Cleared on server
    # delete (FK ON DELETE SET NULL handled in migration).
    server_id: Optional[int] = Field(default=None, foreign_key="server.id")


class ServerDeployment(SQLModel, table=True):
    """A configured "deployment plan" for a specific protocol on a Server.

    Acts as the persistent memory of "what credentials did the user
    pick last time they generated a script for this server", so:
      - Re-opening the script-generator modal pre-fills with last values
      - User doesn't lose the auto-generated NaiveProxy password
      - One-click "Create Node from this deployment" can populate a Node
        row with the right host / user / password automatically

    One row per (server_id, protocol). Re-running the script generator
    UPDATEs the existing row instead of inserting a new one.

    `config_json` is a free-form JSON blob whose shape depends on the
    protocol — for naive: `{"domain", "email", "naive_user", "naive_pass"}`.
    Future protocols (wireguard, hysteria2) will use different keys; the
    JSON column avoids per-protocol column proliferation.

    Threat model: same as Server (LAN-only deployment, plain SQLite).
    See SECURITY.md.
    """

    __table_args__ = (
        UniqueConstraint("server_id", "protocol", name="uq_server_protocol"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", index=True)
    protocol: str             # "naive" — extensible to "wireguard", "hysteria2", …
    config_json: str          # JSON blob, shape per-protocol (see docstring)
    status: str = "configured"  # configured | deployed | failed

    # Set when the user clicks "Create node from this deployment". Lets
    # the UI show "Already linked to Node X" rather than offering the
    # button again. ON DELETE SET NULL via FK so deleting the node
    # doesn't take the deployment with it.
    last_node_id: Optional[int] = Field(default=None, foreign_key="node.id")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Server(SQLModel, table=True):
    """A remote machine the user manages from PiTun.

    Stores SSH connection info + free-form metadata so the user can keep a
    catalogue of their VPS instances and run deployment scripts against them
    (e.g. "install naive", "install wireguard").

    Threat model: PiTun is LAN-only. Credentials are stored in plain SQLite
    — same protection level as Node passwords. The DB file is root-only on
    the host. Do not expose port 80 to the public internet. See SECURITY.md.

    Credentials are write-only via the API: GET responses include
    `has_password` / `has_private_key` booleans rather than the secrets
    themselves, so they don't leak through logs / screenshots / XSS. Empty
    string on PATCH means "leave existing", explicit `null` means "clear".
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: Optional[str] = None

    host: str            # IP or DNS name
    port: int = 22
    user: str = "root"

    # auth_type: "password" | "key"
    auth_type: str = "password"
    password: Optional[str] = None
    private_key: Optional[str] = None     # PEM-formatted, multi-line
    passphrase: Optional[str] = None      # for the encrypted private key

    # Status / health (filled by /test endpoint)
    status: str = "unknown"               # online | offline | unknown
    last_check: Optional[datetime] = None
    last_check_error: Optional[str] = None
    latency_ms: Optional[int] = None

    # Bookkeeping
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RoutingRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    enabled: bool = True

    # Match type: mac|src_ip|dst_ip|domain|port|protocol|geoip|geosite
    rule_type: str

    # Comma-separated values, CIDR notation, domain keywords, etc.
    match_value: str

    # Action: proxy|direct|block|node:<id>
    action: str

    # Lower number = higher priority
    order: int = 100


class Subscription(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    url: str
    enabled: bool = True
    ua: str = "clash"
    filter_regex: Optional[str] = None
    auto_update: bool = False
    update_interval: int = 86400  # seconds
    last_updated: Optional[datetime] = None
    node_count: int = 0
    last_error: Optional[str] = None
    # Optional override for the User-Agent header. When set, replaces
    # the UA derived from the `ua` preset (`_UA_MAP[ua]`). Only useful
    # for panels that gate on a fingerprint we don't ship a preset for
    # — most subscriptions should leave this empty and pick a preset.
    custom_ua: Optional[str] = None


class DNSRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    enabled: bool = True
    # Match: domain keyword / geosite:XX / full domain
    domain_match: str  # e.g. "geosite:cn", "netflix.com", "keyword:google"
    # DNS server to use for matched domains
    dns_server: str  # e.g. "114.114.114.114", "https://dns.google/dns-query"
    dns_type: str = "plain"  # plain | doh | dot
    order: int = 100


class Settings(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(unique=True, index=True)
    value: str


class BalancerGroup(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    enabled: bool = True
    node_ids: str = "[]"  # JSON list of node IDs: "[1, 2, 3]"
    strategy: str = "leastPing"  # "leastPing" | "random"


class NodeCircle(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    enabled: bool = False
    node_ids: str = "[]"          # JSON list of node IDs in order
    mode: str = "sequential"      # "sequential" | "random"
    interval_min: int = 5         # minimum minutes between rotations
    interval_max: int = 15        # maximum minutes (for random interval)
    current_index: int = 0        # current position in the circle
    last_rotated: Optional[datetime] = None


class Device(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    mac: str = Field(unique=True, index=True)
    ip: Optional[str] = None
    hostname: Optional[str] = None
    name: Optional[str] = None
    vendor: Optional[str] = None
    first_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_seen: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_online: bool = True
    routing_policy: str = "default"  # "default" | "include" | "exclude"


class SystemMetric(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), index=True)
    cpu_percent: float = 0.0
    ram_used_mb: float = 0.0
    ram_total_mb: float = 0.0
    disk_used_gb: float = 0.0
    disk_total_gb: float = 0.0
    net_sent_bytes: int = 0
    net_recv_bytes: int = 0


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    password_hash: str


class Event(SQLModel, table=True):
    """User-facing notification of a background state transition.

    Populated by `app/core/events.py:record_event` from the various
    schedulers/loops (failover, naive supervisor, circle scheduler,
    geo scheduler, subscription updater). Surfaced in the UI via the
    Dashboard "Recent Events" card.

    `category` uses dotted free-text codes (e.g. "failover.switched")
    so we can introduce new categories without a migration. `severity`
    is one of "info" | "warning" | "error" — colors the row.
    `title`/`details` are stored as ASCII English; the frontend renders
    localized labels via a category map and shows `details` verbatim.
    `entity_id` is optional and points at a Node / NodeCircle /
    Subscription / etc. There is no FK — events outlive deletions
    so the history stays intact.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )
    category: str = Field(max_length=64, index=True)
    severity: str = Field(max_length=16)
    title: str = Field(max_length=200)
    details: Optional[str] = Field(default=None, max_length=1000)
    entity_id: Optional[int] = Field(default=None, index=True)


class Job(SQLModel, table=True):
    """Persistent record of a long-running async operation
    (v1.3.0 server-tasks subsystem).

    Currently: `kind="deploy"` for SSH-driven proxy install on a
    registered Server. Extensible to subscription_refresh,
    circle_rotate, batch_import, etc. in later releases.

    Hybrid persistence model — see `core/jobs.py.JobManager`:
      * This row holds METADATA (status, timings, error summary,
        config for retry, log_tail snapshot at finalization).
      * Live stdout/stderr stream during a running job lives in
        RAM only (`JobManager._buffers`), capped at ~2000 lines.
        On completion, last ~4 KB combined is captured into
        `log_tail` so post-completion UI still shows something.

    On backend restart, `status='running'` rows older than ~1 hour
    are healed to `status='failed'` with a "backend restarted"
    error by `core.jobs.JobManager._heal_stale_jobs()`.
    """
    id: str = Field(primary_key=True)               # uuid hex (32 chars)
    kind: str = Field(index=True, max_length=32)    # "deploy" | future kinds
    target_id: Optional[int] = Field(default=None, index=True)
    target_name: Optional[str] = Field(default=None, max_length=200)
    protocol: Optional[str] = Field(default=None, max_length=32)
    status: str = Field(default="running", index=True, max_length=16)
    config_json: Optional[str] = None
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
    )
    finished_at: Optional[datetime] = None
    error: Optional[str] = Field(default=None, max_length=500)
    result_json: Optional[str] = None
    log_tail: Optional[str] = None                  # ~4 KB combined, NULL while running
