// ── Auth ──────────────────────────────────────────────────────────────────────
export interface LoginRequest {
  username: string
  password: string
}
export interface TokenResponse {
  access_token: string
  token_type: string
}
export interface ChangePasswordRequest {
  current_password: string
  new_password: string
}
export interface UserInfo {
  id: number
  username: string
}

// ── Node ──────────────────────────────────────────────────────────────────────

export type Protocol = 'vless' | 'vmess' | 'trojan' | 'ss' | 'wireguard' | 'socks' | 'hy2' | 'naive'
export type Transport = 'tcp' | 'ws' | 'grpc' | 'h2' | 'xhttp' | 'httpupgrade' | 'kcp' | 'quic'
export type TlsMode = 'none' | 'tls' | 'reality'

export interface Node {
  id: number
  name: string
  enabled: boolean
  protocol: Protocol
  address: string
  port: number
  uuid?: string
  password?: string
  transport: Transport
  tls: TlsMode
  sni?: string
  fingerprint: string
  alpn?: string
  allow_insecure: boolean
  ws_path: string
  ws_host?: string
  ws_headers?: string
  grpc_service?: string
  grpc_mode: string
  http_path: string
  http_host?: string
  kcp_seed?: string
  kcp_header: string
  reality_pbk?: string
  reality_sid?: string
  reality_spx?: string
  flow?: string
  wg_private_key?: string
  wg_public_key?: string
  wg_preshared_key?: string
  wg_endpoint?: string
  wg_mtu: number
  wg_reserved?: string
  wg_local_address?: string
  hy2_obfs?: string
  hy2_obfs_password?: string
  // NaiveProxy
  internal_port?: number
  naive_padding?: boolean
  group?: string
  note?: string
  subscription_id?: number
  latency_ms?: number
  last_check?: string
  is_online: boolean
  order: number
  chain_node_id?: number
}

export type NodeCreate = Omit<Node, 'id' | 'latency_ms' | 'last_check' | 'is_online'>
export type NodeUpdate = Partial<NodeCreate>

export interface NodeImportRequest {
  uris: string
}

export interface NodeImportResponse {
  imported: number
  skipped: number
  nodes: Node[]
  errors: string[]
}

// ── NaiveProxy sidecar ───────────────────────────────────────────────────────

export interface NaiveSidecarStatus {
  exists: boolean
  running: boolean
  status: string
  started_at?: string | null
  restart_count: number
  internal_port?: number | null
}

export interface NaiveSidecarLogs {
  node_id: number
  logs: string
}

// ── Routing ───────────────────────────────────────────────────────────────────

export type RuleType = 'mac' | 'src_ip' | 'dst_ip' | 'domain' | 'port' | 'protocol' | 'geoip' | 'geosite'
export type RuleAction = 'proxy' | 'direct' | 'block' | `node:${number}` | `balancer:${number}`

export interface RoutingRule {
  id: number
  name: string
  enabled: boolean
  rule_type: RuleType
  match_value: string
  action: RuleAction
  order: number
}

export type RoutingRuleCreate = Omit<RoutingRule, 'id'>
export type RoutingRuleUpdate = Partial<RoutingRuleCreate>

export interface BulkRuleCreate {
  rule_type: RuleType
  action: string
  values: string
  enabled?: boolean
}
export interface BulkRuleResult {
  created: number
  rule_ids: number[]
}

export interface ArpDevice {
  ip: string
  mac: string
  hostname?: string
  vendor?: string
  rule_action?: string
}

// ── Subscription ──────────────────────────────────────────────────────────────

export interface Subscription {
  id: number
  name: string
  url: string
  enabled: boolean
  ua: string
  // Optional override for the User-Agent header. When set, replaces
  // the UA derived from `ua`. Useful for panels with non-standard
  // fingerprint requirements.
  custom_ua?: string
  filter_regex?: string
  auto_update: boolean
  update_interval: number
  last_updated?: string
  node_count: number
  last_error?: string
}

export type SubscriptionCreate = Omit<Subscription, 'id' | 'last_updated' | 'node_count' | 'last_error'>
export type SubscriptionUpdate = Partial<SubscriptionCreate>

// ── System ────────────────────────────────────────────────────────────────────

export type ProxyMode = 'global' | 'rules' | 'bypass'

export interface SystemStatus {
  running: boolean
  pid?: number
  uptime_seconds?: number
  mode: ProxyMode
  active_node_id?: number
  active_node_name?: string
  nftables_active: boolean
  // `version` is the xray binary version; `app_version` is the backend
  // release from `backend/app/config.py:APP_VERSION`. Optional because
  // older backends don't send it — the sidebar hides the line if absent.
  version?: string
  app_version?: string
}

// Full version snapshot for the VersionPopover / Settings About tab.
// Populated live from `/api/system/versions` — PiTun-owned fields come
// from APP_VERSION, everything else is introspected at request time.
// Every field except `pitun.backend` is optional; the UI renders "—".
export interface SystemVersions {
  pitun: {
    backend: string
    naive_image?: string
  }
  runtime: {
    xray?: string
    python?: string
  }
  third_party: {
    nginx?: string
    socket_proxy?: string
  }
  host: {
    kernel?: string
    os?: string
    docker?: string
    arch?: string
  }
  data: {
    alembic_rev?: string
    geoip_mtime?: string     // ISO timestamp
    geosite_mtime?: string   // ISO timestamp
  }
}

export interface SystemSettings {
  mode: ProxyMode
  active_node_id?: number
  failover_enabled: boolean
  failover_node_ids: number[]
  tproxy_port_tcp: number
  tproxy_port_udp: number
  socks_port: number
  http_port: number
  dns_port: number
  dns_mode: string
  dns_upstream: string
  dns_upstream_secondary?: string
  dns_fallback?: string
  fakedns_enabled?: boolean
  fakedns_pool?: string
  fakedns_pool_size?: number
  dns_sniffing?: boolean
  bypass_cn_dns?: boolean
  bypass_ru_dns?: boolean
  bypass_private: boolean
  log_level: string
  geoip_url: string
  geosite_url: string
  geoip_mmdb_url?: string
  // TUN mode
  inbound_mode: 'tproxy' | 'tun' | 'both'
  tun_address?: string
  tun_mtu?: number
  tun_stack?: string
  tun_auto_route?: boolean
  tun_strict_route?: boolean
  // QUIC blocking
  block_quic?: boolean
  // Kill switch + auto restart
  kill_switch?: boolean
  auto_restart_xray?: boolean
  // DNS query logging
  dns_query_log_enabled?: boolean
  // Device routing
  device_routing_mode?: 'all' | 'include_only' | 'exclude_list'
  // Tun extras (backend settings we don't always render)
  tun_address6?: string
  tun_endpoint_nat?: boolean
  tun_sniff?: boolean
  // Network overrides (written from Settings page)
  interface?: string
  gateway_ip?: string
  lan_cidr?: string
  router_ip?: string
  // Health check
  health_interval?: number
  health_timeout?: number
  health_fail_threshold?: number
  health_full_check_interval?: number
  // Misc
  disable_ipv6?: boolean
  dns_over_tcp?: boolean
  dns_query_log_max?: number
  device_scan_interval?: number
  // GeoScheduler
  geo_auto_update?: boolean
  geo_update_interval_days?: number
  geo_update_window_start?: number
  geo_update_window_end?: number
  // IANA timezone (e.g. "Europe/Moscow") for display + scheduling.
  // Default "UTC" when unset.
  timezone?: string
}

// ── V2Ray / xray routing-rule shape (for JSON import) ───────────────────────
//
// The xray routing rule is a tagged union: many optional fields, one (or
// more) of which determines the match semantics. Previously typed as any[];
// this gives us at least structural type-safety at the import boundary so
// the frontend catches missing keys without needing full JSON-schema.
export interface V2RayRule {
  type?: string
  remarks?: string
  domain?: string[]
  ip?: string[]
  port?: string | number
  protocol?: string[]
  source?: string[]
  user?: string[]
  inboundTag?: string[]
  outboundTag?: string
  balancerTag?: string
  network?: string
  attrs?: Record<string, string>
  [k: string]: unknown   // escape hatch for fields we don't model yet
}

export interface V2RayImportRequest {
  rules: V2RayRule[]
  mode: 'as_is' | 'invert'
  clear_existing: boolean
}

export interface V2RayImportResult {
  imported: number
  skipped: number
  rule_ids: number[]
}

// ── GeoData ───────────────────────────────────────────────────────────────────

export interface GeoDataStatus {
  geoip_exists: boolean
  geoip_size?: number
  geoip_mtime?: string
  geosite_exists: boolean
  geosite_size?: number
  geosite_mtime?: string
  mmdb_exists: boolean
  mmdb_size?: number
  mmdb_mtime?: string
}

// ── DNS ───────────────────────────────────────────────────────────────────────

export interface DnsRule {
  id: number
  name: string
  enabled: boolean
  domain_match: string
  dns_server: string
  dns_type: 'plain' | 'doh' | 'dot'
  order: number
}

export type DnsRuleCreate = Omit<DnsRule, 'id'>
export type DnsRuleUpdate = Partial<DnsRuleCreate>

export interface DnsSettings {
  dns_mode: string
  dns_upstream: string
  dns_upstream_secondary?: string
  dns_fallback?: string
  dns_port: number
  fakedns_enabled: boolean
  fakedns_pool: string
  fakedns_pool_size: number
  dns_sniffing: boolean
  bypass_cn_dns: boolean
  bypass_ru_dns: boolean
  dns_disable_fallback: boolean
}

export interface DnsTestResult {
  resolved_ips: string[]
  latency_ms: number
  server_used: string
  error?: string
}

// ── Health ────────────────────────────────────────────────────────────────────

export interface HealthResult {
  node_id: number
  node_name: string
  is_online: boolean
  latency_ms?: number
  error?: string
}

export interface SpeedTestResult {
  node_id: number
  node_name: string
  download_mbps?: number
  error?: string
}

// ── DNS Query Log ──────────────────────────────────────────────────────────────

export interface DnsQueryLog {
  id: number
  timestamp: string
  domain: string
  resolved_ips: string[]
  server_used: string
  latency_ms?: number
  query_type: string
  cache_hit: boolean
  rule_matched?: string
}

export interface DnsQueryStats {
  total_queries: number
  unique_domains: number
  cache_hit_rate: number
  top_domains: Array<{ domain: string; count: number }>
  queries_last_hour: number
}

// ── Balancer Groups ────────────────────────────────────────────────────────────

export interface BalancerGroup {
  id: number
  name: string
  enabled: boolean
  node_ids: number[]
  strategy: 'leastPing' | 'random'
}
export type BalancerGroupCreate = Omit<BalancerGroup, 'id'>
export type BalancerGroupUpdate = Partial<BalancerGroupCreate>

// ── NodeCircle ───────────────────────────────────────────────────────────────
export interface NodeCircle {
  id: number
  name: string
  enabled: boolean
  node_ids: number[]
  mode: 'sequential' | 'random'
  interval_min: number
  interval_max: number
  current_index: number
  last_rotated?: string
  current_node_name?: string
}
export type NodeCircleCreate = Omit<NodeCircle, 'id' | 'current_index' | 'last_rotated' | 'current_node_name'>
export type NodeCircleUpdate = Partial<NodeCircleCreate>

// ── Device Management ─────────────────────────────────────────────────────────

export type DeviceRoutingPolicy = 'default' | 'include' | 'exclude'

export interface Device {
  id: number
  mac: string
  ip?: string
  hostname?: string
  name?: string
  vendor?: string
  first_seen: string
  last_seen: string
  is_online: boolean
  routing_policy: DeviceRoutingPolicy
}

export interface DeviceUpdate {
  name?: string
  routing_policy?: DeviceRoutingPolicy
}

export interface DeviceBulkUpdate {
  device_ids: number[]
  routing_policy: DeviceRoutingPolicy
}

export interface DeviceScanResult {
  discovered: number
  updated: number
  total: number
}

export interface SystemMetric {
  ts: string
  cpu: number
  ram_used: number
  ram_total: number
  disk_used: number
  disk_total: number
  net_sent: number
  net_recv: number
}

// ── Recent Events ────────────────────────────────────────────────────────────
//
// Backend writes ASCII English titles/details — frontend renders icons +
// localized labels via `EVENT_CATEGORY_META` in components/RecentEvents.tsx.
// New categories can be added on the backend without a frontend change;
// unknown categories fall back to a generic icon + the raw category code.

export type EventSeverity = 'info' | 'warning' | 'error'

export interface Event {
  id: number
  timestamp: string
  category: string
  severity: EventSeverity
  title: string
  details?: string
  entity_id?: number
}
