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
