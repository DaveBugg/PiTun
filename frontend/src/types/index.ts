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
  // Optional link to a Server (the VPS hosting this node's upstream).
  // Purely informational — does not affect routing/connection.
  server_id?: number
  // Optional link to a DeploymentClient — set on Nodes that were
  // exported from a multi-client server-side deployment (WireGuard).
  // Drives the "from server X" source label and the orphan badge.
  from_deployment_client_id?: number | null
  // True when the source DeploymentClient has been removed server-side
  // (sync detected the peer is gone) or via Remove client. The Node
  // keeps working but warns the admin that the upstream peer is dead.
  client_orphan?: boolean
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
  // Last xray config validation error (since v1.2.7). Written by
  // `config_gen.write_config()` when `xray run -test` rejects the
  // generated config — typically a routing rule references a
  // geosite/geoip tag absent from the loaded .dat. Empty / undefined
  // means the most recent validation passed.
  last_xray_validation_error?: string | null
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
  // LAN proxy auth (since v1.3.0-beta.6) — single (user, pass) pair
  // applied to BOTH explicit SOCKS5 and HTTP inbounds. TPROXY stays
  // passwordless. Plaintext on the wire by design (LAN-intruder
  // threat model — see backend SettingsRead docstring).
  lan_proxy_auth_enabled?: boolean
  lan_proxy_auth_user?: string
  lan_proxy_auth_pass?: string
}


// ── Geo update live progress (since v1.3.0-beta.6) ─────────────────────────
//
// Replaces the static "Download queued" toast with a real progress
// view: per-file stage + bytes counter + per-file error surface. The
// frontend polls `GET /api/geodata/update/progress` ~2 Hz while a
// job is active; `useGeoUpdateProgress(active)` flips that on/off.

export type GeoUpdateStage =
  | 'queued'
  | 'downloading'
  | 'verifying'
  | 'applying'
  | 'done'
  | 'failed'

export interface GeoUpdateFileProgress {
  stage: GeoUpdateStage
  bytes_downloaded: number
  bytes_total?: number | null
  started_at?: number | null
  finished_at?: number | null
  error?: string | null
  source_url?: string | null
}

export interface GeoUpdateProgress {
  job_id: string
  active: boolean
  started_at?: number | null
  finished_at?: number | null
  tag_cache_refreshed: boolean
  // Keys are 'geoip' | 'geosite' | 'mmdb' depending on what was
  // requested in this job.
  files: Record<string, GeoUpdateFileProgress>
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

// ── Server (managed VPS instances) ───────────────────────────────────────────
//
// SSH-reachable machine the user manages from PiTun. Phase 1 stores the
// connection info + pings via /api/servers/:id/test; Phase 2 will add
// scripted deploy ("install naive on this server"). The credentials are
// write-only at the API: GET responses include `has_password` etc rather
// than the secrets themselves, so we never see them in DevTools.

export type ServerAuthType = 'password' | 'key'
export type ServerStatus = 'online' | 'offline' | 'unknown'

export interface Server {
  id: number
  name: string
  description?: string | null
  host: string
  port: number
  user: string
  auth_type: ServerAuthType
  has_password: boolean
  has_private_key: boolean
  has_passphrase: boolean
  status: ServerStatus
  last_check?: string | null
  last_check_error?: string | null
  latency_ms?: number | null
  created_at?: string | null
  updated_at?: string | null
}

// On create the secret fields are sent as plain strings. Empty string is
// treated as "no value" by the backend.
export interface ServerCreate {
  name: string
  description?: string | null
  host: string
  port?: number
  user?: string
  auth_type?: ServerAuthType
  password?: string
  private_key?: string
  passphrase?: string
}

// PATCH semantics for secret fields:
//   field absent       — unchanged
//   field === ''       — unchanged (form submitted blank input by mistake)
//   field === null     — cleared
//   field with value   — replaced
export interface ServerUpdate {
  name?: string
  description?: string | null
  host?: string
  port?: number
  user?: string
  auth_type?: ServerAuthType
  password?: string | null
  private_key?: string | null
  passphrase?: string | null
}

export interface ServerTestResult {
  server_id: number
  ok: boolean
  latency_ms?: number | null
  error?: string | null
  remote_info?: string | null
}

export interface ServerTestAllResult {
  results: ServerTestResult[]
}

// ── Server deployments ──────────────────────────────────────────────────────
//
// A persistent record of "what credentials did the user pick last time
// they generated an install script for this server, for this protocol".
// Lets the modal pre-fill on re-open and powers "Create node from this
// deployment".

export type ServerDeploymentProtocol = 'naive' | 'wireguard'

export type ServerDeploymentStatus = 'configured' | 'deployed' | 'failed'

export interface NaiveDeploymentConfig {
  domain?: string
  email?: string
  naive_user?: string
  naive_pass?: string
  /** Decoy-site template id from `GET /api/templates`. When set,
   * the install script either curls a single-file template into
   * `/var/www/html/index.html` or git-clones a multi-file repo,
   * depending on the template's `kind`. Unset = script default
   * (currently `pacman` git repo). Since v1.3.0-beta.6. */
  template_id?: string
}


// ── Decoy-site templates (since v1.3.0-beta.6) ─────────────────────────────
//
// Curated gallery the user picks from in the Naive install forms.
// Backend resolves the id to the right env var (TEMPLATE_HTML_URL or
// DECOY_REPO) when generating the install script. See
// `backend/app/core/templates.py` for the source of truth.

export type DecoyTemplateKind = 'single_html' | 'git_repo' | 'custom'

export interface DecoyTemplate {
  id: string
  label: string
  description: string
  kind: DecoyTemplateKind
  // Populated only when `kind === 'custom'` — used by the picker
  // to render delete buttons and a tooltip with the original
  // filename + upload date.
  custom_byte_size?: number
  custom_filename?: string | null
  custom_created_at?: string | null
}

/** WireGuard server-level state, persisted in `ServerDeployment.config_json`.
 * Per-peer state lives in `DeploymentClient` rows. */
export interface WireGuardDeploymentConfig {
  server_port?: number          // default 51820
  wg_network_4?: string         // default "10.66.66.0/24"
  wg_network_6?: string         // default "fd42:42:42::/64"
  dns_1?: string                // default "1.1.1.1"
  dns_2?: string                // default "1.0.0.1"
  allowed_ips?: string          // default "0.0.0.0/0,::/0"
  // For the deploy request only (not stored on the server-level row):
  client_name?: string          // first client to create on install
  server_pub_ip?: string        // autodetected on the VPS if absent
}

export type DeploymentConfig = NaiveDeploymentConfig | WireGuardDeploymentConfig

export interface ServerDeployment {
  id: number
  server_id: number
  protocol: ServerDeploymentProtocol
  config: DeploymentConfig
  status: ServerDeploymentStatus
  last_node_id?: number | null   // single-tunnel protocols only (naive)
  created_at?: string | null
  updated_at?: string | null
}

export interface ServerDeploymentUpsert {
  protocol: ServerDeploymentProtocol
  config: DeploymentConfig
}


// ── Multi-client protocols (WireGuard, since v1.3.0-beta.4) ─────────────────
//
// A DeploymentClient is one peer config on a server-side WG deployment.
// Sits between ServerDeployment and Node:
//   * Server adds peers (via "Add client" UI) → DeploymentClient rows
//   * Sync re-lists from the server, importing new peers + flagging
//     server-side-deleted ones as 'orphan'
//   * "Export to Node" creates a Node from this row's conf, linking
//     Node.from_deployment_client_id back here.

export type DeploymentClientStatus = 'available' | 'exported' | 'orphan'

/** Public read shape — does NOT include private key or PSK. */
export interface DeploymentClient {
  id: number
  deployment_id: number
  name: string
  wg_public_key?: string | null
  wg_endpoint?: string | null         // "host:port"
  wg_local_address?: string | null    // "10.66.66.2/24,fd42:...:2/64"
  wg_mtu: number
  dns_servers?: string | null         // "1.1.1.1,1.0.0.1"
  allowed_ips?: string | null         // "0.0.0.0/0,::/0"
  status: DeploymentClientStatus
  exported_node_ids: number[]
  created_at?: string | null
  last_synced_at?: string | null
}

export interface DeploymentClientList {
  clients: DeploymentClient[]
}

/** Full WG conf with private key — fetched on demand for download / QR. */
export interface DeploymentClientConf {
  name: string
  wg_conf: string
}

export interface DeploymentClientCreate {
  name: string
}

export interface DeploymentClientSyncResult {
  added: string[]
  unchanged: string[]
  orphaned: string[]
}

export interface ExportClientToNodeRequest {
  node_name?: string
  enabled?: boolean
  /** Bypass the idempotency check and create a duplicate Node even if
   * one already exists for this DeploymentClient. Default false —
   * repeat-clicking "Export to Node" returns the existing Node. */
  force?: boolean
}


// ── Server-tasks (Jobs) ─────────────────────────────────────────────────────
//
// Async job tracking for SSH-driven proxy deploys (since v1.3.0-beta.1).
// `POST /servers/{id}/deploy` no longer blocks for the duration of the
// install — it returns 202 + `DeployJobAccepted` immediately and the
// frontend follows progress via the `/server-tasks` API + WS stream.

export type JobKind = 'deploy'  // future: 'subscription_refresh' etc.
export type JobStatus = 'running' | 'succeeded' | 'failed' | 'cancelled'

/** 202 response shape for `POST /servers/{id}/deploy`. */
export interface DeployJobAccepted {
  job_id: string
  server_id: number
  protocol: ServerDeploymentProtocol
}

/** Result payload stored in `Job.result_json` for deploy jobs. The
 * `status` here is the *deploy outcome* (deployed / deployed_no_uri /
 * failed), distinct from the *job status* (succeeded / failed / cancelled
 * — the wrapper around the runner). On runner-level success, the job
 * status is `succeeded` and the deploy outcome lives in `result.status`. */
export interface DeployJobResult {
  deployment_id: number
  /** Set on single-tunnel deploys (naive). Null on multi-client (WG). */
  node_id: number | null
  /** Set on multi-client deploys (WG) — the freshly-created
   * DeploymentClient row's id. Null on single-tunnel (naive). Frontend
   * uses one or the other to render the post-deploy success affordance. */
  client_id: number | null
  status: 'deployed' | 'deployed_no_uri' | 'failed'
  exit_code: number
  duration_sec: number
  parsed_uri: string | null
  error: string | null
  connect_latency_ms: number | null
}

/** Lightweight projection used by the list endpoint. */
export interface JobSummary {
  id: string
  kind: JobKind
  target_id: number | null
  target_name: string | null
  protocol: string | null
  status: JobStatus
  started_at: string
  finished_at: string | null
  error: string | null
  result: DeployJobResult | Record<string, unknown> | null
}

/** Full row returned by `GET /server-tasks/{id}`. Includes log_tail (post-
 * finalize log snapshot) and config (echo of the start request). */
export interface JobRead extends JobSummary {
  log_tail: string | null
  config: Record<string, unknown> | null
}

export interface JobListResponse {
  jobs: JobSummary[]
}

/** Wire format of WS frames on `/server-tasks/{id}/stream`. */
export type JobStreamFrame =
  | { event: 'log'; kind: 'stdout' | 'stderr'; line: string }
  | { event: 'done'; status: JobStatus | 'unknown' }
