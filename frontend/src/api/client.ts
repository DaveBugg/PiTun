import axios from 'axios'
import type {
  LoginRequest, TokenResponse, ChangePasswordRequest, UserInfo,
  Node, NodeCreate, NodeUpdate, NodeImportRequest, NodeImportResponse,
  NodePage, NodePageParams,
  RoutingRule, RoutingRuleCreate, RoutingRuleUpdate, ArpDevice,
  BulkRuleCreate, BulkRuleResult,
  Subscription, SubscriptionCreate, SubscriptionUpdate,
  UserAgentTemplate, UserAgentTemplateCreate, UserAgentTemplateUpdate,
  UserAgentTemplateImportResult,
  SystemStatus, SystemSettings, SystemVersions, ProxyMode,
  GeoDataStatus, GeoUpdateProgress,
  DecoyTemplate,
  DnsRule, DnsRuleCreate, DnsRuleUpdate, DnsSettings, DnsTestResult,
  DnsQueryLog, DnsQueryStats,
  HealthResult, SpeedTestResult,
  BalancerGroup, BalancerGroupCreate, BalancerGroupUpdate,
  NodeCircle, NodeCircleCreate, NodeCircleUpdate,
  AutoCheck, AutoCheckUpdate,
  Device, DeviceUpdate, DeviceBulkUpdate, DeviceScanResult,
  RoutingSet, RoutingSetCreate, RoutingSetUpdate, RoutingSetCapacity,
  RoutingPortRule, RoutingImportDestination, RoutingImportPreviewResult,
  RoutingImportResolution, RoutingImportCommitResult,
  DeviceBulkSetAssign,
  SystemMetric,
  NaiveSidecarStatus, NaiveSidecarLogs,
  V2RayImportRequest, V2RayImportResult,
  Server, ServerCreate, ServerUpdate, ServerTestResult, ServerTestAllResult,
  ServerDeployment, ServerDeploymentUpsert, ServerDeploymentProtocol,
  DeployJobAccepted, JobListResponse, JobRead, JobStatus,
  DeploymentClient, DeploymentClientList, DeploymentClientConf,
  DeploymentClientCreate, DeploymentClientSyncResult,
  ExportClientToNodeRequest,
} from '@/types'

const BASE = import.meta.env.VITE_API_BASE_URL || '/api'

export const http = axios.create({
  baseURL: BASE,
  headers: { 'Content-Type': 'application/json' },
})

// Auth token interceptor
http.interceptors.request.use((config) => {
  const token = localStorage.getItem('pitun_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 401 interceptor — redirect to login
http.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !error.config?.url?.includes('/auth/') && window.location.pathname !== '/login') {
      localStorage.removeItem('pitun_token')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

// ── Auth ─────────────────────────────────────────────────────────────────────
export const authApi = {
  login: (data: LoginRequest) =>
    http.post<TokenResponse>('/auth/login', data).then(r => r.data),
  changePassword: (data: ChangePasswordRequest) =>
    http.post('/auth/change-password', data),
  me: () =>
    http.get<UserInfo>('/auth/me').then(r => r.data),
}

// ── Nodes ─────────────────────────────────────────────────────────────────────

export interface SpeedEvent {
  phase: 'start' | 'connecting' | 'progress' | 'target_failed' | 'done' | 'error'
  host?: string
  mbps?: number
  mbps_max?: number
  elapsed?: number
  bytes?: number
  error?: string
  node_id?: number
}

/**
 * Live speed test — streams NDJSON progress from the backend so the UI can
 * show "via Cachefly · 45.2 Mbps" ticking up. Uses `fetch` (not axios) for the
 * streaming body reader; auth rides the normal Bearer header, so no token in
 * the query string. `onEvent` fires per line; resolves when the stream ends.
 */
export async function speedtestStream(
  id: number,
  onEvent: (e: SpeedEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = localStorage.getItem('pitun_token')
  const resp = await fetch(`${BASE}/nodes/${id}/speedtest/stream`, {
    method: 'POST',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal,
  })
  if (!resp.ok || !resp.body) throw new Error(`speedtest stream ${resp.status}`)
  const reader = resp.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let nl: number
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim()
      buf = buf.slice(nl + 1)
      if (line) {
        try { onEvent(JSON.parse(line) as SpeedEvent) } catch { /* skip partial */ }
      }
    }
  }
}

export const nodesApi = {
  list: (params?: { enabled?: boolean; group?: string }) =>
    http.get<Node[]>('/nodes', { params }).then(r => r.data),

  /**
   * Paginated + filtered Node listing (since v1.3.3). Used by the
   * Nodes page when subscriptions can pull 1000+ rows. The legacy
   * `list()` above is kept for callers that need the full set
   * (reorder, JSON export, NodeCircle scheduler).
   */
  listPage: (params: NodePageParams = {}) =>
    http.get<NodePage>('/nodes/page', { params }).then(r => r.data),

  get: (id: number) =>
    http.get<Node>(`/nodes/${id}`).then(r => r.data),

  create: (data: NodeCreate) =>
    http.post<Node>('/nodes', data).then(r => r.data),

  update: (id: number, data: NodeUpdate) =>
    http.patch<Node>(`/nodes/${id}`, data).then(r => r.data),

  delete: (id: number) =>
    http.delete(`/nodes/${id}`),

  import: (data: NodeImportRequest, subscriptionId?: number) =>
    http.post<NodeImportResponse>('/nodes/import', data, {
      params: subscriptionId ? { subscription_id: subscriptionId } : {},
    }).then(r => r.data),

  checkHealth: (id: number) =>
    http.post<HealthResult>(`/nodes/${id}/check`).then(r => r.data),

  checkAll: () =>
    http.post<HealthResult[]>('/nodes/check-all').then(r => r.data),

  speedtest: (id: number) =>
    http.post<SpeedTestResult>(`/nodes/${id}/speedtest`).then(r => r.data),

  speedtestAll: () =>
    http.post<SpeedTestResult[]>('/nodes/speedtest-all').then(r => r.data),

  /** Real internet check through the node (fetches Google's 204). */
  reachability: (id: number) =>
    http.post<{ ok: boolean; latency_ms: number | null; detail: string }>(
      `/nodes/${id}/reachability`,
    ).then(r => r.data),

  /** Share URL for a single node (vless:// etc.). */
  uri: (id: number) =>
    http.get<{ uri: string }>(`/nodes/${id}/uri`).then(r => r.data.uri),

  /** Plain-text URI export. Sister to the JSON bundle but emits a
   *  shareable `.txt` file with one `vless://` / `trojan://` / `ss://`
   *  / `vmess://` / `hysteria2://` / `socks://` per line. WireGuard
   *  nodes are skipped (no canonical URI form). */
  exportURIs: async (): Promise<void> => {
    const r = await http.get('/nodes/export-uris', { responseType: 'text' })
    const text = typeof r.data === 'string' ? r.data : String(r.data ?? '')
    const blob = new Blob([text], { type: 'text/plain' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
    a.download = `pitun-nodes-${stamp}.txt`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },

  // Full-fidelity JSON backup/restore. Distinct from `import` which
  // parses VPN URIs — these handle the "back up everything" use case.
  exportJSON: async (): Promise<void> => {
    const r = await http.get('/nodes/export-json', { responseType: 'json' })
    const blob = new Blob([JSON.stringify(r.data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = (r.data?.exported_at?.replace(/[:.]/g, '-') ?? 'pitun-nodes') + '.json'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },

  importJSON: (bundle: unknown, replace = false) =>
    http.post<NodeImportResponse>(
      '/nodes/import-json',
      bundle,
      { params: { replace } },
    ).then(r => r.data),

  reorder: (ids: number[]) =>
    http.post('/nodes/reorder', ids),

  // ── NaiveProxy sidecar ────────────────────────────────────────────────────
  sidecarStatus: (id: number) =>
    http.get<NaiveSidecarStatus>(`/nodes/${id}/sidecar`).then(r => r.data),

  sidecarRestart: (id: number) =>
    http.post<NaiveSidecarStatus>(`/nodes/${id}/sidecar/restart`).then(r => r.data),

  sidecarLogs: (id: number, tail = 200) =>
    http.get<NaiveSidecarLogs>(`/nodes/${id}/sidecar/logs`, { params: { tail } }).then(r => r.data),
}

// ── Routing ───────────────────────────────────────────────────────────────────

export const routingApi = {
  listRules: () =>
    http.get<RoutingRule[]>('/routing/rules').then(r => r.data),

  createRule: (data: RoutingRuleCreate) =>
    http.post<RoutingRule>('/routing/rules', data).then(r => r.data),

  updateRule: (id: number, data: RoutingRuleUpdate) =>
    http.patch<RoutingRule>(`/routing/rules/${id}`, data).then(r => r.data),

  deleteRule: (id: number) =>
    http.delete(`/routing/rules/${id}`),

  deleteAllRules: () =>
    http.delete('/routing/rules'),

  deleteBatchRules: (ids: number[]) =>
    http.post('/routing/rules/delete-batch', ids),

  reorderRules: (ids: number[]) =>
    http.post('/routing/rules/reorder', ids),

  bulkCreate: (data: BulkRuleCreate) =>
    http.post<BulkRuleResult>('/routing/rules/bulk', data).then(r => r.data),

  importV2ray: (data: V2RayImportRequest) =>
    http.post<V2RayImportResult>('/routing/rules/import-v2ray', data).then(r => r.data),

  // Set-aware native import (preview then commit). `rules` is the flattened
  // RoutingPortRule[] from a parsed export file; destination + resolutions
  // are chosen in the import dialog.
  importPreview: (data: { rules: RoutingPortRule[]; destination: RoutingImportDestination }) =>
    http.post<RoutingImportPreviewResult>('/routing/import/preview', data).then(r => r.data),

  importCommit: (data: {
    rules: RoutingPortRule[]
    destination: RoutingImportDestination
    resolutions?: RoutingImportResolution[]
    default_conflict_choice?: 'keep' | 'replace'
  }) =>
    http.post<RoutingImportCommitResult>('/routing/import/commit', data).then(r => r.data),

  listDevices: () =>
    http.get<ArpDevice[]>('/routing/devices').then(r => r.data),

  // ── Auto-disabled rules inbox (v1.2.7) ──────────────────────────────────
  // When `_regenerate_and_write` self-heals after xray rejects the config
  // due to a missing geosite/geoip tag, rules referencing the tag are
  // disabled and surfaced via these endpoints.
  listAutoDisabled: () =>
    http.get<{ items: AutoDisabledRule[] }>('/routing/auto-disabled').then(r => r.data),

  reEnableAutoDisabled: (ruleId: number) =>
    http.post(`/routing/auto-disabled/${ruleId}/re-enable`),

  deleteAutoDisabled: (ruleId: number) =>
    http.delete(`/routing/auto-disabled/${ruleId}`),

  dismissAutoDisabledAll: () =>
    http.post('/routing/auto-disabled/dismiss'),
}

// One auto-disabled inbox entry — server returns these as JSON inside
// the `items` array. Shape is intentionally permissive (extra fields
// from future versions just get ignored).
export interface AutoDisabledRule {
  rule_id: number
  name?: string
  rule_type?: string
  match_value?: string
  missing_kind: 'geosite' | 'geoip'
  missing_tag: string
  disabled_at?: string
}

// ── Subscriptions ─────────────────────────────────────────────────────────────

export const subsApi = {
  list: () =>
    http.get<Subscription[]>('/subscriptions').then(r => r.data),

  create: (data: SubscriptionCreate) =>
    http.post<Subscription>('/subscriptions', data).then(r => r.data),

  update: (id: number, data: SubscriptionUpdate) =>
    http.patch<Subscription>(`/subscriptions/${id}`, data).then(r => r.data),

  delete: (id: number, deleteNodes = true) =>
    http.delete(`/subscriptions/${id}`, { params: { delete_nodes: deleteNodes } }),

  refresh: (id: number) =>
    http.post(`/subscriptions/${id}/refresh`).then(r => r.data),
}

// ── User-Agent templates ──────────────────────────────────────────────────────

export const uaTemplatesApi = {
  list: () =>
    http.get<UserAgentTemplate[]>('/user-agents').then(r => r.data),

  create: (data: UserAgentTemplateCreate) =>
    http.post<UserAgentTemplate>('/user-agents', data).then(r => r.data),

  update: (id: number, data: UserAgentTemplateUpdate) =>
    http.patch<UserAgentTemplate>(`/user-agents/${id}`, data).then(r => r.data),

  /** Without `force`, the backend answers 409 and names the subscriptions. */
  delete: (id: number, force = false) =>
    http.delete(`/user-agents/${id}`, { params: { force } }),

  /** Triggers a browser download. */
  exportJSON: async (): Promise<void> => {
    const r = await http.get('/user-agents/export-json', { responseType: 'json' })
    const blob = new Blob([JSON.stringify(r.data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'pitun-ua-templates.json'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },

  /**
   * Additive by default. `overwrite` updates a matching key in place
   * (keeping its row id); `replace` wipes the table first.
   */
  importJSON: (bundle: unknown, opts: { replace?: boolean; overwrite?: boolean } = {}) =>
    http.post<UserAgentTemplateImportResult>(
      '/user-agents/import-json',
      bundle,
      { params: { replace: !!opts.replace, overwrite: !!opts.overwrite } },
    ).then(r => r.data),
}

// ── System ────────────────────────────────────────────────────────────────────

export const systemApi = {
  status: () =>
    http.get<SystemStatus>('/system/status').then(r => r.data),

  versions: () =>
    http.get<SystemVersions>('/system/versions').then(r => r.data),

  start: () => http.post('/system/start'),
  stop: () => http.post('/system/stop'),
  restart: () => http.post('/system/restart'),
  reloadConfig: () => http.post('/system/reload-config'),

  setMode: (mode: ProxyMode) =>
    http.post('/system/mode', { mode }),

  setActiveNode: (node_id: number) =>
    http.post('/system/active-node', { node_id }),

  getSettings: () =>
    http.get<SystemSettings>('/system/settings').then(r => r.data),

  updateSettings: (data: Partial<SystemSettings>) =>
    http.patch('/system/settings', data),

  getStats: () =>
    http.get<Record<string, { uplink: number; downlink: number }>>('/system/stats').then(r => r.data),

  getMetrics: (period: string = '1h') =>
    http.get<SystemMetric[]>('/system/metrics', { params: { period } }).then(r => r.data),
}

// ── GeoData ───────────────────────────────────────────────────────────────────

// ── Decoy templates (since v1.3.0-beta.6) ───────────────────────────────────

export const templatesApi = {
  /** Curated gallery of decoy-site options + user-uploaded custom
   * .zip archives. The picker renders both kinds in one list. */
  list: () =>
    http.get<DecoyTemplate[]>('/templates').then(r => r.data),

  /** Upload a custom .zip cover. Backend validates: ≤10 MB, no
   * zip-slip, static-asset extension whitelist, must contain
   * index.html. Returns the new template entry on success. */
  upload: (label: string, description: string, archive: File) => {
    const fd = new FormData()
    fd.append('label', label)
    fd.append('description', description)
    fd.append('archive', archive)
    return http
      .post<DecoyTemplate>('/templates/upload', fd, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      .then((r) => r.data)
  },

  /** Remove a custom template. Built-ins return 400 — the UI
   * hides the delete button on those rows. */
  remove: (templateId: string) =>
    http.delete(`/templates/${encodeURIComponent(templateId)}`),
}


export const geodataApi = {
  status: () =>
    http.get<GeoDataStatus>('/geodata/status').then(r => r.data),

  update: (urls?: { geoip_url?: string; geosite_url?: string; mmdb_url?: string; type?: string }) =>
    http.post('/geodata/update', urls ?? {}).then(r => r.data),

  /** Live snapshot of the latest geo-update job. Polled while a
   * download is in flight; idle response (no job since boot) has
   * `active=false` and an empty `files` map. */
  progress: () =>
    http.get<GeoUpdateProgress>('/geodata/update/progress').then(r => r.data),
}

// ── DNS ───────────────────────────────────────────────────────────────────────

export const dnsApi = {
  getSettings: () =>
    http.get<DnsSettings>('/dns/settings').then(r => r.data),

  updateSettings: (data: Partial<DnsSettings>) =>
    http.patch<DnsSettings>('/dns/settings', data).then(r => r.data),

  getRules: () =>
    http.get<DnsRule[]>('/dns/rules').then(r => r.data),

  createRule: (data: DnsRuleCreate) =>
    http.post<DnsRule>('/dns/rules', data).then(r => r.data),

  updateRule: (id: number, data: DnsRuleUpdate) =>
    http.put<DnsRule>(`/dns/rules/${id}`, data).then(r => r.data),

  deleteRule: (id: number) =>
    http.delete(`/dns/rules/${id}`),

  reorderRules: (ids: number[]) =>
    http.post('/dns/rules/reorder', ids),

  test: (domain: string, server?: string) =>
    http.post<DnsTestResult>('/dns/test', { domain, server }).then(r => r.data),

  testViaXray: (domain: string) =>
    http.post<DnsTestResult>('/dns/test-xray', { domain }).then(r => r.data),

  getQueryLogs: (params?: { domain?: string; limit?: number; offset?: number; cache_only?: boolean }) =>
    http.get<DnsQueryLog[]>('/dns/queries', { params }).then(r => r.data),

  clearQueryLogs: () =>
    http.delete('/dns/queries').then(() => undefined),

  getQueryStats: () =>
    http.get<DnsQueryStats>('/dns/queries/stats').then(r => r.data),
}

// ── Balancer Groups ───────────────────────────────────────────────────────────

export const balancersApi = {
  list: () => http.get<BalancerGroup[]>('/balancers').then(r => r.data),
  create: (data: BalancerGroupCreate) => http.post<BalancerGroup>('/balancers', data).then(r => r.data),
  update: (id: number, data: BalancerGroupUpdate) => http.patch<BalancerGroup>(`/balancers/${id}`, data).then(r => r.data),
  delete: (id: number) => http.delete(`/balancers/${id}`),
}

// ── Auto-checks (background speed sweep) ─────────────────────────────────────

export const autocheckApi = {
  get: () => http.get<AutoCheck>('/autocheck').then(r => r.data),
  update: (data: AutoCheckUpdate) => http.put<AutoCheck>('/autocheck', data).then(r => r.data),
  /** Trigger a background sweep now. Pass scopeKind='all' + force=true
   *  (Nodes "Speed All") to re-test every node regardless of the saved
   *  scope AND the staleness guard. */
  run: (scopeKind?: string, force?: boolean) =>
    http.post<{ status: string }>('/autocheck/run', null, {
      params: {
        ...(scopeKind ? { scope_kind: scopeKind } : {}),
        ...(force ? { force: true } : {}),
      },
    }).then(r => r.data),
}

// ── NodeCircle ───────────────────────────────────────────────────────────────

export const circleApi = {
  list: () => http.get<NodeCircle[]>('/nodecircle').then(r => r.data),
  create: (data: NodeCircleCreate) => http.post<NodeCircle>('/nodecircle', data).then(r => r.data),
  update: (id: number, data: NodeCircleUpdate) => http.patch<NodeCircle>(`/nodecircle/${id}`, data).then(r => r.data),
  delete: (id: number) => http.delete(`/nodecircle/${id}`),
  rotate: (id: number) => http.post<NodeCircle>(`/nodecircle/${id}/rotate`).then(r => r.data),
}

// ── Devices ──────────────────────────────────────────────────────────────────

export const devicesApi = {
  /**
   * `routing_set_id`: pass a numeric id to filter to members of that
   * set, or the literal string `"null"` to list global-only (unassigned)
   * devices. Omit to list all devices (current behaviour).
   */
  list: (params?: {
    online_only?: boolean
    policy?: string
    routing_set_id?: number | 'null'
  }) =>
    http.get<Device[]>('/devices', { params }).then(r => r.data),
  get: (id: number) =>
    http.get<Device>(`/devices/${id}`).then(r => r.data),
  update: (id: number, data: DeviceUpdate) =>
    http.patch<Device>(`/devices/${id}`, data).then(r => r.data),
  delete: (id: number) =>
    http.delete(`/devices/${id}`),
  scan: () =>
    http.post<DeviceScanResult>('/devices/scan').then(r => r.data),
  bulkPolicy: (data: DeviceBulkUpdate) =>
    http.post('/devices/bulk-policy', data),
  resetAllPolicies: () =>
    http.post('/devices/reset-all-policies'),
}

// ── Routing sets (v1.4) ─────────────────────────────────────────────────────

export const routingSetsApi = {
  list: () =>
    http.get<RoutingSet[]>('/routing-sets').then(r => r.data),
  /** Returns {used, maximum, available} — used by UI to gate the "+" button. */
  capacity: () =>
    http.get<RoutingSetCapacity>('/routing-sets/capacity').then(r => r.data),
  get: (id: number) =>
    http.get<RoutingSet>(`/routing-sets/${id}`).then(r => r.data),
  create: (data: RoutingSetCreate) =>
    http.post<RoutingSet>('/routing-sets', data).then(r => r.data),
  update: (id: number, data: RoutingSetUpdate) =>
    http.patch<RoutingSet>(`/routing-sets/${id}`, data).then(r => r.data),
  /**
   * `cascade='move-to-global'` reassigns dependent devices/rules to NULL
   * before deletion. Without it, the server returns 409 when there are
   * dependents (the UI should prompt the operator to confirm).
   */
  delete: (id: number, opts?: { cascade?: 'move-to-global' | 'delete' }) =>
    http.delete(`/routing-sets/${id}`, { params: opts }),
  bulkAssignDevices: (data: DeviceBulkSetAssign) =>
    http.post('/routing-sets/devices/bulk', data),
}

// ── Diagnostics ─────────────────────────────────────────────────────────────

/** Advisory release check — applies nothing. */
export interface UpdateCheckResult {
  current: string
  latest: string | null
  update_available: boolean
  /** "active node" | "direct" | "unreachable" — lets the UI tell
   *  "no update" apart from "we couldn't look". */
  network_path: string
  published_at?: string | null
  notes?: string | null
  error?: string | null
  /** Installing `latest` would REMOVE Settings → Updates (a downgrade
   *  below the version that introduced it). The box stays updatable, but
   *  only from a shell — say so BEFORE, not after. */
  target_lacks_update_ui?: boolean
  update_ui_since?: string
}

export interface UpdateStatus {
  state: 'idle' | 'queued' | 'running' | 'done' | 'failed' | 'available' | 'unknown'
  pct: number
  step: string
  message: string
  ok: boolean | null
  from?: string | null
  to?: string | null
  updated_at?: string | null
  /** Still true → the host agent never picked the request up (not
   *  installed, or not running). */
  request_pending: boolean
}

export const updateApi = {
  check: (prerelease = false) =>
    http.get<UpdateCheckResult>('/system/update/check', { params: { prerelease } })
      .then(r => r.data),

  status: () =>
    http.get<UpdateStatus>('/system/update/status').then(r => r.data),

  /** 202 — the work happens in a host agent and outlives this response;
   *  poll `status()` for progress. */
  start: (body: { version?: string; force?: boolean; prerelease?: boolean }) =>
    http.post<UpdateStatus>('/system/update/start', body).then(r => r.data),
}

export interface SniScanResult {
  ok: boolean
  tls13?: boolean
  http2?: boolean
  status?: number | null
  via: string
  detail: string
  /** Certificate the endpoint presents — lets a bare-IP scan surface the
   *  domain(s) behind it (usable as the REALITY serverName). */
  cert_subject?: string | null
  cert_name?: string | null
}

export const diagnosticsApi = {
  /** Check whether a domain is a viable REALITY dest (TLS 1.3 + ALPN h2),
   *  probed through the active node when one is set. */
  sniScan: (domain: string) =>
    http.post<SniScanResult>('/diagnostics/sni-scan', { domain }).then(r => r.data),

  healthChecks: () =>
    http.get<{ checks: Array<{ name: string; ok: boolean; detail: string; info?: boolean }> }>('/diagnostics/health-checks').then(r => r.data),

  network: () =>
    http.get<{
      interfaces: Array<{ name: string; state: string; addresses: string[] }>;
      gateway: { gateway: string | null; device: string | null; my_ip: string; subnet: string; recommendation: string | null };
      routes: Array<{ route: string }>;
      listeners: Array<{ proto: string; listen: string; process: string }>;
    }>('/diagnostics/network').then(r => r.data),

  resources: () =>
    http.get<{
      load_avg: string[];
      cpu_count: number;
      memory: { total_mb: number; used_mb: number; available_mb: number };
      disk: { total: string; used: string; available: string; use_percent: string };
      temperature: number | null;
      uptime: string;
    }>('/diagnostics/resources').then(r => r.data),

  logs: (lines?: number, level?: string) =>
    http.get<{ lines: string[] }>('/diagnostics/logs', { params: { lines: lines || 100, level: level || '' } }).then(r => r.data),

  explain: (body: RouteExplainRequest) =>
    http.post<RouteExplainResult>('/diagnostics/explain', body).then(r => r.data),
}

// ── Route Explainer ───────────────────────────────────────────────────────────

export interface RouteExplainRequest {
  target: string
  port?: number
  protocol?: string
  from_mac?: string | null
  verify_routing?: boolean
  test_reachability?: boolean
}

export interface RouteExplainResult {
  target: string
  port: number
  protocol: string
  is_ip: boolean
  dns: {
    is_ip: boolean
    matched_rule_id: number | null
    matched_rule_name: string | null
    matched_pattern: string | null
    server: string | null
    server_type: string | null
    uses_global_upstream: boolean
    query_strategy: string
    geosite_uncertain: boolean
    resolved_ips: string[]
    resolve_error: string | null
    note: string | null
  }
  routing: {
    matched_rule_id: number | null
    matched_rule_name: string | null
    matched_rule_type: string | null
    matched_value: string | null
    action: string | null
    outbound: string | null
    outbound_label: string | null
    certain: boolean
    blocking_rule: string | null
    rules_evaluated: number
    set_id: number | null
    set_name: string | null
    method: string
    probe_detail: string | null
    notes: string[]
  }
  reachability: {
    tested: boolean
    ok: boolean | null
    http_code: number | null
    latency_ms: number | null
    via: string | null
    detail: string | null
  }
}

// ── Recent Events ───────────────────────────────────────────────────────────
import type { Event as PiEvent } from '@/types'

export const eventsApi = {
  list: (params?: { limit?: number; category?: string; severity?: string; since?: string }) =>
    http.get<PiEvent[]>('/events', { params }).then(r => r.data),

  clear: () => http.delete<{ cleared: boolean }>('/events').then(r => r.data),
}

// ── Servers (managed VPS instances) ───────────────────────────────────────────
//
// Mirror of /api/servers routes. Note `naiveInstallScriptUrl` returns a
// raw URL the user can hand to <a download> rather than fetching itself —
// that way the browser handles the file download natively (correct
// Content-Disposition handling, no need to manage Blob URLs in code).

export const serversApi = {
  list: () => http.get<Server[]>('/servers').then(r => r.data),
  get: (id: number) => http.get<Server>(`/servers/${id}`).then(r => r.data),
  create: (data: ServerCreate) => http.post<Server>('/servers', data).then(r => r.data),
  update: (id: number, data: ServerUpdate) =>
    http.patch<Server>(`/servers/${id}`, data).then(r => r.data),
  delete: (id: number) => http.delete(`/servers/${id}`),
  test: (id: number, direct = false) =>
    http.post<ServerTestResult>(`/servers/${id}/test`, null, { params: { direct } }).then(r => r.data),
  testAll: (direct = false) =>
    http.post<ServerTestAllResult>('/servers/test-all', null, { params: { direct } }).then(r => r.data),

  // Fetch the generated naive-install bash script as text, then trigger
  // a file download via an in-memory Blob URL. We deliberately don't
  // hand the URL to <a download> directly because that path requires
  // moving the JWT into a query string (token-in-URL pollutes browser
  // history + access logs). The fetch path keeps auth in the header
  // and pays a tiny memory cost (script is ~1 KB).
  downloadNaiveInstallScript: async (
    id: number,
    params: { domain: string; email: string; naive_user?: string; naive_pass?: string; template_id?: string; install_php?: boolean; ssh_port?: number },
  ): Promise<void> => {
    const r = await http.get(`/servers/${id}/naive-install-script`, {
      params,
      responseType: 'text',
    })
    const blob = new Blob([r.data], { type: 'text/x-shellscript' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `naive-install-${id}.sh`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },

  /** Per-server WireGuard install bootstrap. Same Blob-download pattern
   * as the Naive variant — see comments above for why we don't use a
   * direct <a download>. All params optional; the underlying script
   * has sensible defaults if a field is omitted. */
  downloadWireguardInstallScript: async (
    id: number,
    params: {
      client_name?: string
      server_port?: number
      dns_1?: string
      dns_2?: string
      allowed_ips?: string
      ssh_port?: number
    },
  ): Promise<void> => {
    const r = await http.get(`/servers/${id}/wireguard-install-script`, {
      params,
      responseType: 'text',
    })
    const blob = new Blob([r.data], { type: 'text/x-shellscript' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `wireguard-install-${id}.sh`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },

  // ── Deployments (persistent install plans per protocol) ───────────────────

  listDeployments: (serverId: number) =>
    http.get<ServerDeployment[]>(`/servers/${serverId}/deployments`).then(r => r.data),

  upsertDeployment: (
    serverId: number,
    protocol: ServerDeploymentProtocol,
    data: ServerDeploymentUpsert,
  ) =>
    http.put<ServerDeployment>(`/servers/${serverId}/deployments/${protocol}`, data).then(r => r.data),

  deleteDeployment: (serverId: number, protocol: ServerDeploymentProtocol) =>
    http.delete(`/servers/${serverId}/deployments/${protocol}`),

  createNodeFromDeployment: (serverId: number, protocol: ServerDeploymentProtocol) =>
    http.post<Node>(`/servers/${serverId}/deployments/${protocol}/create-node`).then(r => r.data),

  // Auto-deploy (since v1.3.0-beta.1). Returns 202 + job_id; client
  // follows progress via serverTasksApi.{get, stream}.
  deploy: (
    serverId: number,
    body: { protocol: ServerDeploymentProtocol; config: Record<string, unknown> },
    direct = false,
  ) =>
    http.post<DeployJobAccepted>(`/servers/${serverId}/deploy`, body, { params: { direct } }).then(r => r.data),

  // ── WireGuard server-side clients (since v1.3.0-beta.4) ──────────────────
  // CRUD over the multi-client peer layer. All endpoints scoped to one
  // (server, protocol=wireguard) deployment; sync reconciles against
  // the server's actual peer list.

  listClients: (serverId: number) =>
    http.get<DeploymentClientList>(
      `/servers/${serverId}/deployments/wireguard/clients`,
    ).then(r => r.data),

  addClient: (serverId: number, body: DeploymentClientCreate, direct = false) =>
    http.post<DeploymentClient>(
      `/servers/${serverId}/deployments/wireguard/clients`,
      body,
      { params: { direct } },
    ).then(r => r.data),

  removeClient: (serverId: number, name: string, direct = false) =>
    http.delete(
      `/servers/${serverId}/deployments/wireguard/clients/${encodeURIComponent(name)}`,
      { params: { direct } },
    ),

  syncClients: (serverId: number, direct = false) =>
    http.post<DeploymentClientSyncResult>(
      `/servers/${serverId}/deployments/wireguard/clients/sync`,
      null,
      { params: { direct } },
    ).then(r => r.data),

  getClientConf: (serverId: number, name: string, direct = false) =>
    http.get<DeploymentClientConf>(
      `/servers/${serverId}/deployments/wireguard/clients/${encodeURIComponent(name)}/conf`,
      { params: { direct } },
    ).then(r => r.data),

  exportClientToNode: (
    serverId: number, name: string, body: ExportClientToNodeRequest = {}, direct = false,
  ) =>
    http.post<Node>(
      `/servers/${serverId}/deployments/wireguard/clients/${encodeURIComponent(name)}/export-node`,
      body,
      { params: { direct } },
    ).then(r => r.data),

  // JSON backup. `includeSecrets=false` (default) strips passwords and
  // private keys — safer to share or commit accidentally. With
  // `includeSecrets=true` the bundle is round-trip-perfect but
  // contains plaintext credentials; treat it like a password file.
  exportJSON: async (includeSecrets: boolean): Promise<void> => {
    const r = await http.get('/servers/export-json', {
      params: { include_secrets: includeSecrets },
      responseType: 'json',
    })
    const blob = new Blob([JSON.stringify(r.data, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const ts = (r.data?.exported_at ?? '').replace(/[:.]/g, '-')
    const tag = includeSecrets ? 'with-secrets' : 'no-secrets'
    a.download = `pitun-servers-${ts}-${tag}.json`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },

  importJSON: (bundle: unknown, replace = false) =>
    http.post<{ imported: number; skipped: number; errors: string[]; has_secrets: boolean }>(
      '/servers/import-json',
      bundle,
      { params: { replace } },
    ).then(r => r.data),
}

// ── Manual scripts (server-agnostic) ─────────────────────────────────────────
//
// Companion to the per-server install-script endpoint. Used by the
// "Manual scripts" cards on the Servers page when the user wants the
// bootstrap before registering a VPS. Same Blob/download mechanic as
// `serversApi.downloadNaiveInstallScript` — keeps JWT in the header,
// out of the URL.

export const scriptsApi = {
  downloadNaiveInstall: async (
    params: { domain: string; email: string; naive_user?: string; naive_pass?: string; template_id?: string; install_php?: boolean; ssh_port?: number },
  ): Promise<void> => {
    const r = await http.get('/scripts/naive-install', {
      params,
      responseType: 'text',
    })
    const blob = new Blob([r.data], { type: 'text/x-shellscript' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'naive-install.sh'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },

  /** Server-agnostic WireGuard manual-install script. Mirrors
   * `downloadNaive` but for the WG sub-command pipeline. All params
   * optional; backend defaults kick in for omitted fields. */
  downloadWireguard: async (
    params: {
      client_name?: string
      server_port?: number
      dns_1?: string
      dns_2?: string
      allowed_ips?: string
      ssh_port?: number
    },
  ): Promise<void> => {
    const r = await http.get('/scripts/wireguard-install', {
      params,
      responseType: 'text',
    })
    const blob = new Blob([r.data], { type: 'text/x-shellscript' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'wireguard-install.sh'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },
}

// ── Server-tasks (Jobs) ─────────────────────────────────────────────────────
//
// Companion to `serversApi.deploy` — list / detail / cancel + a WS
// connector for live log streams. Kept in this file (rather than a
// dedicated module) for symmetry with the other endpoints; React Query
// hooks live in `useServerTasks.ts`.

export const serverTasksApi = {
  list: (params?: {
    server_id?: number; kind?: string; status?: JobStatus;
    limit?: number; offset?: number;
  }) =>
    http.get<JobListResponse>('/server-tasks', { params }).then(r => r.data),

  get: (jobId: string) =>
    http.get<JobRead>(`/server-tasks/${jobId}`).then(r => r.data),

  cancel: (jobId: string) =>
    http.post<{ job_id: string; cancelled: boolean }>(
      `/server-tasks/${jobId}/cancel`,
    ).then(r => r.data),
}

/** Open a WebSocket on `/api/server-tasks/{jobId}/stream`. Returns the
 * underlying WebSocket so the caller can attach `onmessage` / `onclose`
 * / call `.close()` on unmount. The token query param is required by the
 * backend; we read it from localStorage same as createLogSocket.
 */
export function createServerTaskSocket(jobId: string): WebSocket {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsBase = `${proto}//${window.location.host}/api`
  const token = localStorage.getItem('pitun_token') || ''
  return new WebSocket(
    `${wsBase}/server-tasks/${encodeURIComponent(jobId)}/stream?token=${encodeURIComponent(token)}`,
  )
}

// ── x-ui-pro / 3x-ui panel management (since v1.3.0-beta.7) ──────────────────

import type {
  ChainClientRead,
  ChainCreateChannelInput,
  ChainRead,
  InboundPreset,
  XuiClient as XuiClientType,
  XuiInbound,
  XuiServer,
} from '@/types'

export const xuiApi = {
  /** GET /api/xui/presets — the 6 wired-in inbound preset templates. */
  listPresets: async (): Promise<InboundPreset[]> => {
    const r = await http.get('/xui/presets')
    return r.data
  },

  /** GET /api/xui/servers — registered XuiServer rows (one per panel). */
  listServers: async (): Promise<XuiServer[]> => {
    const r = await http.get('/xui/servers')
    return r.data
  },

  getServer: async (id: number): Promise<XuiServer> => {
    const r = await http.get(`/xui/servers/${id}`)
    return r.data
  },

  /** POST /api/xui/servers/import — register a panel from its `xui://`
   *  URI (or discrete fields). The endpoint probes the Bearer token
   *  before persisting, so a 400 here means the panel itself rejected
   *  the credentials. Surface the detail verbatim in the UI. */
  importServer: async (params: {
    server_id: number
    uri?: string
    api_token?: string
    panel_port?: number
    panel_basepath?: string
    panel_user?: string
    panel_pass?: string
    domain?: string
    mode?: 'bare' | 'xui-pro'
  }): Promise<XuiServer> => {
    const r = await http.post('/xui/servers/import', params)
    return r.data
  },

  deleteServer: async (id: number): Promise<void> => {
    await http.delete(`/xui/servers/${id}`)
  },

  /** POST /api/xui/servers/{id}/probe — re-test the Bearer token.
   *  Persisted `last_check` / `last_check_error` come back on the
   *  response so the UI can update the badge in one round-trip. */
  probeServer: async (id: number, direct = false): Promise<XuiServer> => {
    const r = await http.post(`/xui/servers/${id}/probe`, null, { params: { direct } })
    return r.data
  },

  /** POST /api/xui/servers/{id}/sync — reconcile the local
   *  `XuiClient` cache with what's actually on the panel right now.
   *  Counts what was added / updated / removed; cascade-cleans Node
   *  rows whose backing client vanished from the panel. */
  syncServer: async (id: number, direct = false): Promise<{
    xui_server_id: number
    added: number
    updated: number
    removed: number
    chain_skipped: number
    orphan_nodes_removed: number
  }> => {
    const r = await http.post(`/xui/servers/${id}/sync`, null, { params: { direct } })
    return r.data
  },

  /** POST /api/xui/servers/{id}/healthcheck — multi-layer probe
   *  (panel API, xray state, SSH-checked nginx / ufw / cert / disk).
   *  Returns the same `{ok, checks}` shape as the chain healthcheck. */
  healthcheckServer: async (id: number, direct = false): Promise<{
    xui_server_id: number
    ok: boolean
    checks: Array<{ name: string; status: 'ok' | 'warn' | 'fail'; detail?: string | null }>
  }> => {
    const r = await http.post(`/xui/servers/${id}/healthcheck`, null, { params: { direct } })
    return r.data
  },

  /** POST /api/xui/servers/{id}/fakesite/rotate — pick a random
   *  template from /root/randomfakehtml-master and swap into nginx
   *  /var/www/html. Only valid for xui-pro panels. */
  rotateFakesite: async (id: number, direct = false): Promise<{
    ok: boolean; name?: string | null; detail?: string | null
  }> => {
    const r = await http.post(`/xui/servers/${id}/fakesite/rotate`, null, { params: { direct } })
    return r.data
  },

  /** POST /api/xui/servers/{id}/fakesite/upload — multipart .zip
   *  archive that replaces the served fakesite root. Zip must
   *  contain an index.html within the first two levels. ≤100 MB. */
  uploadFakesite: async (id: number, zip: File, direct = false): Promise<{
    ok: boolean; name?: string | null; detail?: string | null
  }> => {
    const fd = new FormData()
    fd.append('file', zip)
    const r = await http.post(`/xui/servers/${id}/fakesite/upload`, fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
      params: { direct },
    })
    return r.data
  },

  // ── Inbounds ────────────────────────────────────────────────────────────
  listInbounds: async (serverId: number): Promise<XuiInbound[]> => {
    const r = await http.get(`/xui/servers/${serverId}/inbounds`)
    return r.data
  },

  createInbound: async (
    serverId: number,
    body: { preset_id: string; values: Record<string, unknown>; remark?: string },
    direct = false,
  ): Promise<XuiInbound> => {
    const r = await http.post(`/xui/servers/${serverId}/inbounds`, body, { params: { direct } })
    return r.data
  },

  deleteInbound: async (serverId: number, inboundId: number, direct = false): Promise<void> => {
    await http.delete(`/xui/servers/${serverId}/inbounds/${inboundId}`, { params: { direct } })
  },

  // ── Clients ─────────────────────────────────────────────────────────────
  addClient: async (
    serverId: number,
    inboundId: number,
    body: { label?: string; extras?: Record<string, unknown> } = {},
    direct = false,
  ): Promise<XuiClientType> => {
    const r = await http.post(
      `/xui/servers/${serverId}/inbounds/${inboundId}/clients`,
      body,
      { params: { direct } },
    )
    return r.data
  },

  deleteClient: async (
    serverId: number, inboundId: number, clientUuid: string, direct = false,
  ): Promise<void> => {
    await http.delete(
      `/xui/servers/${serverId}/inbounds/${inboundId}/clients/${encodeURIComponent(clientUuid)}`,
      { params: { direct } },
    )
  },

  /** Convert one inbound-side client (vless / vmess / trojan / etc.)
   *  into a routable PiTun Node. Idempotent — re-export of the same
   *  client returns the existing node id. `clientId` accepts the
   *  UUID or the client email. */
  exportInboundClientToNode: async (
    serverId: number, inboundId: number, clientId: string,
    body: { name?: string } = {},
  ): Promise<{ node_id: number; reused: boolean }> => {
    const r = await http.post(
      `/xui/servers/${serverId}/inbounds/${inboundId}/clients/${encodeURIComponent(clientId)}/export-node`,
      body,
    )
    return r.data
  },

  /**
   * Fetch a pasteable share URI for one x-ui inbound client — used to
   * render a QR code without persisting a Node. Returns `{ uri, reason }`:
   * `reason` is non-null when the protocol/inbound doesn't yield a URI
   * (e.g. WireGuard or a malformed client).
   */
  getInboundClientShareUri: async (
    serverId: number, inboundId: number, clientId: string,
  ): Promise<{ uri: string | null; reason: string | null }> => {
    const r = await http.get(
      `/xui/servers/${serverId}/inbounds/${inboundId}/clients/${encodeURIComponent(clientId)}/share-uri`,
    )
    return r.data
  },

  // ── Chains (since v1.3.0-beta.7) ───────────────────────────────────────
  listChains: async (): Promise<ChainRead[]> => {
    const r = await http.get('/xui/chains')
    return r.data
  },

  getChain: async (id: number): Promise<ChainRead> => {
    const r = await http.get(`/xui/chains/${id}`)
    return r.data
  },

  /** POST /api/xui/chains — atomic create. Backend may take 5-30s
   *  because it does N×2 add_inbound calls + xrayTemplateConfig push
   *  + Xray restart on the relay. Frontend should show a busy
   *  indicator and accept a slow response. */
  createChain: async (params: {
    name: string
    exit_xui_server_id: number
    relay_xui_server_id: number
    exit_sni: string
    channels: ChainCreateChannelInput[]
  }, direct = false): Promise<ChainRead> => {
    const r = await http.post('/xui/chains', params, { params: { direct } })
    return r.data
  },

  deleteChain: async (id: number, direct = false): Promise<void> => {
    await http.delete(`/xui/chains/${id}`, { params: { direct } })
  },

  /** DELETE /api/xui/chains/{id}/channels/{cid} — remove one channel.
   *  Cleans inbounds on both panels + UFW + relay template + any
   *  Nodes exported from this channel. If it was the last channel,
   *  the whole chain is dropped. */
  deleteChainChannel: async (
    chainId: number, channelId: number, direct = false,
  ): Promise<void> => {
    await http.delete(`/xui/chains/${chainId}/channels/${channelId}`, { params: { direct } })
  },

  /** Run a read-only diagnostic sweep over both panels + the live
   *  xray config. Returns a list of pass/fail/warn checks. */
  healthcheckChain: async (id: number, direct = false): Promise<{
    chain_id: number
    ok: boolean
    checks: { name: string; status: 'ok' | 'warn' | 'fail'; detail?: string }[]
  }> => {
    const r = await http.post(`/xui/chains/${id}/healthcheck`, null, { params: { direct } })
    return r.data
  },

  // Chain clients
  listChainClients: async (chainId: number): Promise<ChainClientRead[]> => {
    const r = await http.get(`/xui/chains/${chainId}/clients`)
    return r.data
  },

  /** Spawns N panel-side clients (one per channel). Backend label
   *  default = `pi-XXXXXXXX` (8 hex). */
  addChainClient: async (
    chainId: number, body: { label?: string } = {}, direct = false,
  ): Promise<ChainClientRead> => {
    const r = await http.post(`/xui/chains/${chainId}/clients`, body, { params: { direct } })
    return r.data
  },

  deleteChainClient: async (
    chainId: number, chainClientId: number, direct = false,
  ): Promise<void> => {
    await http.delete(`/xui/chains/${chainId}/clients/${chainClientId}`, { params: { direct } })
  },

  /** Convert N (chain-client × channel) pairs into N Node rows.
   *  Empty `channel_ids` means "all channels"; idempotent on
   *  channels that were already exported (skipped silently). */
  exportChainClientNodes: async (
    chainId: number, chainClientId: number,
    body: { channel_ids?: number[]; name_prefix?: string } = {}, direct = false,
  ): Promise<{ exported_node_ids: number[] }> => {
    const r = await http.post(
      `/xui/chains/${chainId}/clients/${chainClientId}/export-nodes`,
      body,
      { params: { direct } },
    )
    return r.data
  },
}

// ── Host network configuration (since v1.3.3) ─────────────────────────────────
//
// Read current network state (interface / manager / mode / IP / gateway / DNS)
// from the backend. Used by Settings → Network. Apply/confirm/rollback come
// in sub-tasks 2-3.

export interface NetworkState {
  interface: string
  manager: 'networkmanager' | 'networkd' | 'ifupdown' | 'dhcpcd' | 'unknown'
  mode: 'dhcp' | 'static' | 'manual' | 'unknown'
  ip: string | null
  cidr: number | null
  gateway: string | null
  dns: string[]
  /** Operator-visible diagnostics. E.g. "upstream gateway is another PiTun".
   *  Frontend renders these as banners on the Network page. */
  warnings: string[]
}

export interface NetworkBackup {
  id: string
  created_at: string
  manager: string
  interface: string
  live_state: {
    ip: string | null
    cidr: number | null
    gateway: string | null
    dns: string[]
    mode: string
  }
}

export interface NetworkProbeResult {
  reachable: boolean
  detail: string
}

export interface NetworkApplyResult {
  ok: boolean
  backup: {
    id: string
    created_at: string
    manager: string
    live_state: NetworkBackup['live_state']
  }
  new_state: NetworkState
}

/** `POST /network/rollback` does NOT answer with `backup` — it reports
 *  what it restored FROM, under a different key and without `manager`.
 *  Typing it as `NetworkApplyResult` made `data.backup.id` compile while
 *  being `undefined` at runtime. */
export interface NetworkRollbackResult {
  ok: boolean
  restored_from: {
    id: string
    created_at: string
    live_state: NetworkBackup['live_state']
  }
  new_state: NetworkState
}

export const networkApi = {
  getState: async (): Promise<NetworkState> => {
    const r = await http.get('/network/state')
    return r.data
  },

  /** Pre-flight reachability check on a candidate gateway. Frontend
   *  calls this before submitting `apply` so the operator gets an
   *  early "this IP doesn't ping" warning instead of a half-applied
   *  config that leaves them without internet. */
  probe: async (ip: string): Promise<NetworkProbeResult> => {
    const r = await http.get('/network/probe', { params: { ip } })
    return r.data
  },

  /** Persist + apply gateway/DNS changes. Backend snapshots the
   *  current state first; the returned `backup.id` is what `rollback`
   *  defaults to (newest), but specific ids work too. */
  apply: async (body: { gateway?: string; dns?: string[] }): Promise<NetworkApplyResult> => {
    const r = await http.post('/network/apply', body)
    return r.data
  },

  /** Restore a previous configuration. Empty body = newest backup. */
  rollback: async (backup_id?: string): Promise<NetworkRollbackResult> => {
    const r = await http.post('/network/rollback', { backup_id })
    return r.data
  },

  listBackups: async (): Promise<{ items: NetworkBackup[]; count: number }> => {
    const r = await http.get('/network/backups')
    return r.data
  },

  /** Delete one backup by id. Idempotent. */
  deleteBackup: async (id: string): Promise<void> => {
    await http.delete(`/network/backups/${encodeURIComponent(id)}`)
  },

  /** Wipe all backups. UI is expected to confirm first. */
  clearBackups: async (): Promise<{ ok: boolean; removed: number }> => {
    const r = await http.delete('/network/backups')
    return r.data
  },
}

// ── WebSocket log stream ──────────────────────────────────────────────────────

export function createLogSocket(onLine: (line: string) => void): WebSocket {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const wsBase = `${proto}//${window.location.host}/api`
  const token = localStorage.getItem('pitun_token') || ''
  const ws = new WebSocket(`${wsBase}/logs/stream?token=${encodeURIComponent(token)}`)
  ws.onmessage = (e) => {
    if (e.data) onLine(e.data as string)
  }
  return ws
}
