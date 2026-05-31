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
    # `:authority` HTTP/2 header. nginx-fronted gRPC inbounds route by
    # this — e.g. x-ui-pro's trojan-grpc preset defaults it to the
    # panel domain. Optional; left NULL the outbound omits the field
    # and xray uses the destination host as authority.
    grpc_authority: Optional[str] = None

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

    # Multi-client provenance (since v1.3.0-beta.4). Set when this Node was
    # created via "Export to Node" from a `DeploymentClient` row (currently
    # WireGuard peer configs). Lets the Nodes UI show "from <Server> ·
    # <client_name>" instead of treating the Node as a free-floating import.
    # Nullable: pre-multi-client Nodes (URI imports, manual adds) leave it NULL.
    from_deployment_client_id: Optional[int] = None

    # Set when a sync against the server detects the source DeploymentClient
    # is missing on the server (peer was removed externally — manual wg-quick,
    # another PiTun instance, host CLI). The Node row is NOT auto-deleted —
    # admin choice; UI shows a "server-side client deleted" badge so it's
    # visible at a glance that the tunnel will fail to handshake.
    client_orphan: bool = False


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


class DeploymentClient(SQLModel, table=True):
    """Server-side client config tracked separately from Node (v1.3.0-beta.4).

    For multi-client protocols (initially WireGuard), one Server can host
    many independent peer configs. We persist them here BEFORE deciding
    whether to expose them as Nodes — gives the user a "browse server-side
    state" layer without polluting the Node table with every peer the
    server admin happens to have configured.

    Workflow:
      * Initial deploy / Add client (via UI button) → SSH to the server,
        run `setup-wireguard-server.sh add-client <name>`, parse the URI
        it returns, INSERT a row here with status='available'.
      * Sync (UI button) → `setup-wireguard-server.sh list-clients`
        returns JSON of currently-configured peers. Reconcile:
          - server-only → INSERT here (admin from another machine added
            it; we want it visible)
          - PiTun-only → flip status='orphan' (peer was removed
            externally; we keep the row for forensics)
      * Export to Node → create a `Node` row, copy the wg_* fields,
        link Node.from_deployment_client_id back to this row, set
        DeploymentClient.status='exported'. Multiple Nodes may be
        exported from the same client (rare but allowed — e.g. when
        re-importing into a fresh PiTun instance after an init).
      * Remove client → SSH `remove-client <name>` + DELETE this row.
        If any Node was exported, it's NOT auto-deleted — instead its
        client_orphan flag goes true on the next sync (or immediately
        from the Remove handler).

    Naive (single-tunnel by nature) keeps the existing
    ServerDeployment.last_node_id flow in beta.4 and may be unified
    into this model later for consistency.
    """

    __table_args__ = (
        UniqueConstraint(
            "deployment_id", "name", name="uq_deploymentclient_deployment_name"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    deployment_id: int = Field(foreign_key="serverdeployment.id", index=True)
    # Human-readable peer name. Sane charset enforced by add-client
    # subcommand (alphanumeric + - _ only) so the script's local
    # variables / file paths can't be tampered with.
    name: str = Field(max_length=100)

    # WireGuard peer fields. Values mirror Node.wg_* one-to-one so
    # "Export to Node" is a flat copy. Stored here as well because the
    # server's wg0.conf only has the peer's PUBLIC key + AllowedIPs;
    # the client's PRIVATE key is generated at add-client time and not
    # retrievable from the server afterwards. Lose this row → lose
    # the client conf entirely.
    wg_private_key: Optional[str] = None
    wg_public_key: Optional[str] = None
    wg_preshared_key: Optional[str] = None
    wg_endpoint: Optional[str] = None      # "host:port"
    wg_mtu: int = 1420
    wg_local_address: Optional[str] = None  # "10.66.66.2/24,fd42:42:42::2/64"

    # Server context (denormalized so export-to-Node is one SELECT).
    dns_servers: Optional[str] = None      # "1.1.1.1,1.0.0.1"
    allowed_ips: Optional[str] = None      # "0.0.0.0/0,::/0"

    # Free-form JSON for protocol extras / verbatim conf backup.
    # WG: full INI conf (so admin can re-download .conf even after
    # exporting). Future protocols can stash whatever they need here.
    config_json: Optional[str] = None

    # Lifecycle: available | exported | orphan
    status: str = Field(default="available", max_length=16)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_synced_at: Optional[datetime] = None


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


class RoutingSet(SQLModel, table=True):
    """Named group of routing rules applied only to selected devices.

    The default state of PiTun has a single global rule list applied to all
    LAN traffic. A RoutingSet lets the operator define an alternative rule
    list ("Kids", "WorkLaptop", "Guests"…) and assign N devices to it. Per-set
    rules are evaluated FIRST for the assigned devices, then traffic falls
    through to global rules (`RoutingRule.routing_set_id IS NULL`).

    Isolation is implemented at the nftables PREROUTING + xray inbound layer:
    each set gets a unique TPROXY port. nftables matches the device MAC and
    TPROXYs into the set's port; xray spins up a per-set inbound with
    `tag=tproxy-set-<name>`; xray's router then matches `inboundTag` to
    decide which rules apply. This is DHCP-resistant (MAC-based, not
    IP-based) and survives lease changes without needing config regeneration.

    Reserved port range: 65500..65535 (loopback only, see
    `api/routing_sets.py:TPROXY_PORT_MIN/MAX`). 36 ports → 36 max sets,
    which is overkill for a home LAN. One port handles TCP+UDP via
    xray's `dokodemo-door` protocol with `network: "tcp,udp"` — Linux
    kernel lets TCP and UDP coexist on the same port (different socket
    families). Inbound listens only on 127.0.0.1 so no LAN exposure.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    description: Optional[str] = None
    order: int = 0  # render order in UI tabs

    # Auto-allocated on POST /api/routing-sets/. Range 65500..65535.
    # Unique per row; never re-used after a set is deleted (allocator just
    # picks the next-free port in the range, gaps are fine).
    tproxy_port: int = Field(unique=True, index=True)

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


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

    # NULL = global rule, applies to all devices (default behaviour
    # preserved on upgrade since the migration backfills nothing).
    # Set to a RoutingSet.id to make this rule only apply to devices
    # assigned to that set.
    routing_set_id: Optional[int] = Field(
        default=None,
        foreign_key="routingset.id",
        index=True,
    )


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
    # When True, generate a fresh random X-Hwid per refresh request for
    # this subscription (only effective on Happ-style presets). Off by
    # default because most panels device-bind on first-seen HWID — but
    # when a panel starts throttling requests from the same HWID
    # (observed in the wild as the panel returning degraded "proxy"
    # placeholder entries instead of real nodes) rotating gets a clean
    # response.
    rotate_hwid: bool = False


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

    # NULL = unassigned (device sees only global rules — preserves old
    # behaviour on upgrade). Set to a RoutingSet.id to apply that set's
    # rules in addition to globals (per-set rules hit first, then
    # global fallback). Orthogonal to `routing_policy`: an excluded
    # device bypasses TPROXY entirely so its routing_set_id is moot.
    routing_set_id: Optional[int] = Field(
        default=None,
        foreign_key="routingset.id",
        index=True,
    )


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


class XuiServer(SQLModel, table=True):
    """Bookkeeping for an x-ui-pro / 3x-ui panel deployed on a Server
    (since v1.3.0-beta.7).

    One row per Server (`UniqueConstraint` on server_id) — re-deploys
    UPDATE in place rather than create a parallel row, so chain
    references and exported clients survive a re-install.

    The parent `Server` row holds VPS-level fields (host, ssh_port,
    ssh_user, password/key). This row holds *panel-level* fields:
      * api_token       — Bearer token for /panel/api/* calls
      * panel_user/pass — for the human admin to log into the panel
      * panel_port      — random high port the panel listens on
                          (separate from SSH; ufw-opened at install)
      * panel_basepath  — `/<random>` URL prefix
      * domain / mode   — `xui-pro` w/ Let's Encrypt cert OR `bare`
                          self-signed; gates fakesite features in UI

    Threat model: identical to Server.password — plain-text in the
    SQLite DB (file is root-only on the host). Compromise of the DB
    file gives the attacker the api_token, which is full panel
    access. Acceptable for PiTun's LAN-only scope.
    """

    __table_args__ = (
        UniqueConstraint("server_id", name="uq_xuiserver_server"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    server_id: int = Field(foreign_key="server.id", index=True)

    api_token: str
    panel_user: str
    panel_pass: str

    panel_port: int
    panel_basepath: str         # always starts with "/", no trailing "/"

    domain: Optional[str] = None
    mode: str = "bare"          # bare | xui-pro

    # Health (filled on probe + on each /sync UI action)
    last_check: Optional[datetime] = None
    last_check_error: Optional[str] = None

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class XuiClient(SQLModel, table=True):
    """A panel-side client config that PiTun manages (since v1.3.0-beta.7).

    Mirrors the DeploymentClient pattern from WireGuard: PiTun keeps a
    cached copy of every client it created on the panel + the bits
    needed to reconstruct a routable Node URL without re-asking the
    panel each time. Hand-added clients (created via the panel UI by
    the human) are NOT mirrored here — we only insert on our own
    `addClient` API calls and identify those by the `pi-XXXXXXXX`
    label format.

    Mapping back to a Node:
      * `exported_node_id` is set when the user clicks "Export to Node".
        Until then, the client is "browseable but not routed". After
        export, the Node carries the full xray-outbound config and
        PiTun's routing layer treats it like any other VLESS / Trojan
        / SOCKS5 outbound.
      * Multiple Nodes may be exported from the same XuiClient (rare
        but allowed — e.g. re-importing into a fresh PiTun instance).
        FK is ON DELETE SET NULL, so deleting a Node doesn't cascade
        the client row away.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    xui_server_id: int = Field(foreign_key="xuiserver.id", index=True)

    # Panel-side identifiers — sent to the API for delete/update.
    inbound_remote_id: int = Field(index=True)
    # Per-client UUID (vless / trojan; for socks5 inbounds this is the
    # username instead — `socks5` clients are stored as user/pass pairs
    # not UUIDs by the panel).
    client_uuid: str = ""

    # Display label written into the panel's `email` field at creation.
    # Format: `pi-XXXXXXXX` (8 hex). Reading this back from the panel
    # is how we identify PiTun-managed vs hand-added clients.
    label: str = Field(index=True)

    # Cached metadata — refreshed by /sync. Lets the UI render the
    # client list without round-tripping through the panel for every
    # row.
    inbound_protocol: str = ""    # vless | trojan | shadowsocks | socks
    inbound_port: int = 0
    inbound_remark: str = ""

    # Free-form blob with the full client config (uuid/flow/sni/pbk/
    # sid for vless+reality, password/method for trojan, ...). Used
    # by "show config" + "export to Node" without an extra panel
    # call.
    config_json: str = "{}"

    # Set on "Export to Node" click. ON DELETE SET NULL — deleting
    # the Node leaves this row pointing at None, NOT cascading away.
    exported_node_id: Optional[int] = Field(default=None, foreign_key="node.id")

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    last_synced_at: Optional[datetime] = None


class ProxyChain(SQLModel, table=True):
    """Two-hop proxy chain (since v1.3.0-beta.7).

    One row per (exit_panel, relay_panel) pair. Models the chain
    pattern from the user's `setup-eu.sh` + `setup-relay.sh` reference
    scripts: a client connects to RU `relay` (which DPI sees as TLS
    to `client_sni`), the relay routes traffic through an EU `exit`
    panel (which the connection between relay and exit looks like
    HTTPS to `exit_sni` — typically `www.google.com`).

    A chain has N **channels** (rows in `ChainChannel`), each one a
    parallel pipe with its own:
      * exit-side inbound (xhttp + Reality on the EU panel, dest =
        exit_sni:443) + matching outbound on the relay
      * relay-side inbound (TCP + Reality on the RU panel, dest =
        client_sni:443)
      * routing rule on the relay: `inboundTag=<relay-tag>` →
        `outboundTag=<exit-tag>`

    A `ChainClient` is one logical user — when created it spawns N
    panel-side clients (one per channel), so the user gets N VLESS
    URIs (e.g. "VPN-VK" / "VPN-MAX" / ...) all backed by the same
    exit IP. Importing any of them as a `Node` makes PiTun route
    through that chain end-to-end.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str

    # The two panels this chain wires together. Both ON DELETE CASCADE
    # — if the user removes either panel from PiTun (which itself
    # cascades from the underlying Server row), the chain rows are
    # gone too, but the panels themselves stay intact server-side
    # until the operator uninstalls them.
    exit_xui_server_id: int = Field(foreign_key="xuiserver.id", index=True)
    relay_xui_server_id: int = Field(foreign_key="xuiserver.id", index=True)

    # SNI the exit-side inbounds masquerade under. Reused across all
    # channels — DPI on the relay→exit hop sees one consistent host.
    exit_sni: str = "www.google.com"

    # State machine:
    #   `pending`    — row exists, channels created, but panel API
    #                  calls haven't completed yet (mid-orchestration).
    #   `deployed`   — inbounds + outbounds + routing applied; Xray
    #                  restarted on the relay; chain is live.
    #   `failed`     — orchestration aborted; row kept so the UI can
    #                  show the failure + offer a retry/delete.
    #   `degraded`   — `/sync` found drift between PiTun state and
    #                  the panels (someone hand-edited an inbound).
    status: str = "pending"

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
    last_synced_at: Optional[datetime] = None
    # Free-form error message from the most recent failed
    # orchestration attempt. Cleared on a successful re-deploy.
    last_error: Optional[str] = Field(default=None, max_length=1000)


class ChainChannel(SQLModel, table=True):
    """One "pipe" inside a ProxyChain (since v1.3.0-beta.7).

    Each channel is a fully-isolated VLESS+Reality tunnel: separate
    UUID, separate x25519 keypair on both sides, separate shortId,
    its own client_sni / xhttp_path / port pair. Multiple channels
    inside the same chain only share the *exit panel* and the
    *exit_sni* — everything else is per-channel so a DPI fingerprint
    on one channel doesn't burn the others.
    """

    __table_args__ = (
        # Each channel within a chain has a unique name (used as a
        # tag suffix in xrayTemplateConfig — must be stable + safe).
        UniqueConstraint("chain_id", "name", name="uq_chainchannel_chain_name"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    chain_id: int = Field(foreign_key="proxychain.id", index=True)

    # Display + tag name. Constrained to alnum + `-` + `_` at the API
    # layer (xray's tag matching is exact-string).
    name: str
    order: int = 0

    # Panel-assigned inbound ids — set after the create-orchestrator
    # has run /panel/api/inbounds/add. Used by /sync and delete to
    # find the rows again. 0 means "not yet created" (mid-orchestration).
    exit_inbound_remote_id: int = 0
    relay_inbound_remote_id: int = 0

    # Wire-level config — captured at create time so re-pushing the
    # xrayTemplateConfig after a panel restart is deterministic.
    exit_port: int                    # what the relay dials on the exit
    relay_port: int                   # what the client dials on the relay
    exit_xhttp_path: str = "/api/v1"  # xhttp path used on the exit side
    client_sni: str                   # what DPI sees on client→relay

    # Reality material — both sides have their own keypair so the
    # tunnel between client→relay and relay→exit don't share crypto.
    # `exit_*` keys are on the EU panel; `relay_*` keys are on the
    # RU panel. uuid is the per-channel auth identity for the
    # outbound that the relay uses to dial the exit.
    exit_uuid: str = ""
    exit_pbk: str = ""
    exit_pvk: str = ""
    exit_sid: str = ""

    relay_pbk: str = ""
    relay_pvk: str = ""
    relay_sid: str = ""

    # Remarks the user sees in the panel UI when they peek behind
    # the curtain.
    relay_inbound_remark: str = ""
    exit_inbound_remark: str = ""

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class ChainClient(SQLModel, table=True):
    """One logical user on a ProxyChain (since v1.3.0-beta.7).

    Maps to N panel-side clients (one per `ChainChannel`) — when the
    user clicks "Add chain client", PiTun calls `addClient` on every
    channel's relay-side inbound, getting N UUIDs back. Each can be
    independently exported to a Node, but they all flow through the
    same exit IP end-to-end so the user gets multiple SNI masquerade
    options without juggling separate accounts.

    Per-channel state (UUIDs + node IDs) lives on
    `ChainClientChannel` rows so the table fan-out matches the
    cardinality on the panel.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    chain_id: int = Field(foreign_key="proxychain.id", index=True)

    # Display label written into every panel-side client's `email`
    # field. Format: `pi-XXXXXXXX` — same convention as
    # standalone XuiClient rows, so /sync recognises them as
    # PiTun-managed.
    label: str = Field(index=True)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )


class ChainClientChannel(SQLModel, table=True):
    """Per-channel state for a ChainClient.

    Joins (ChainClient, ChainChannel) → (panel client UUID,
    optional exported Node). Cascades from ChainClient (delete the
    logical user → all N panel-side clients drop) but ON DELETE SET
    NULL from Node (deleting a routed Node doesn't take the
    bookkeeping row with it).
    """

    __table_args__ = (
        UniqueConstraint(
            "chain_client_id", "channel_id",
            name="uq_chainclientchannel_pair",
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    chain_client_id: int = Field(foreign_key="chainclient.id", index=True)
    channel_id: int = Field(foreign_key="chainchannel.id", index=True)

    # Panel-assigned per-channel UUID — what the user pastes into a
    # VLESS client (along with the channel's relay_pbk / relay_sid /
    # client_sni / relay_port).
    client_uuid: str

    # ON DELETE SET NULL — see ChainClient docstring.
    exported_node_id: Optional[int] = Field(
        default=None, foreign_key="node.id",
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
    )
