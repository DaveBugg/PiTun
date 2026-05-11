import axios from 'axios'
import type {
  LoginRequest, TokenResponse, ChangePasswordRequest, UserInfo,
  Node, NodeCreate, NodeUpdate, NodeImportRequest, NodeImportResponse,
  RoutingRule, RoutingRuleCreate, RoutingRuleUpdate, ArpDevice,
  BulkRuleCreate, BulkRuleResult,
  Subscription, SubscriptionCreate, SubscriptionUpdate,
  SystemStatus, SystemSettings, SystemVersions, ProxyMode,
  GeoDataStatus, GeoUpdateProgress,
  DecoyTemplate,
  DnsRule, DnsRuleCreate, DnsRuleUpdate, DnsSettings, DnsTestResult,
  DnsQueryLog, DnsQueryStats,
  HealthResult, SpeedTestResult,
  BalancerGroup, BalancerGroupCreate, BalancerGroupUpdate,
  NodeCircle, NodeCircleCreate, NodeCircleUpdate,
  Device, DeviceUpdate, DeviceBulkUpdate, DeviceScanResult,
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

export const nodesApi = {
  list: (params?: { enabled?: boolean; group?: string }) =>
    http.get<Node[]>('/nodes', { params }).then(r => r.data),

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
  list: (params?: { online_only?: boolean; policy?: string }) =>
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

// ── Diagnostics ─────────────────────────────────────────────────────────────

export const diagnosticsApi = {
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
  test: (id: number) =>
    http.post<ServerTestResult>(`/servers/${id}/test`).then(r => r.data),
  testAll: () =>
    http.post<ServerTestAllResult>('/servers/test-all').then(r => r.data),

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
  ) =>
    http.post<DeployJobAccepted>(`/servers/${serverId}/deploy`, body).then(r => r.data),

  // ── WireGuard server-side clients (since v1.3.0-beta.4) ──────────────────
  // CRUD over the multi-client peer layer. All endpoints scoped to one
  // (server, protocol=wireguard) deployment; sync reconciles against
  // the server's actual peer list.

  listClients: (serverId: number) =>
    http.get<DeploymentClientList>(
      `/servers/${serverId}/deployments/wireguard/clients`,
    ).then(r => r.data),

  addClient: (serverId: number, body: DeploymentClientCreate) =>
    http.post<DeploymentClient>(
      `/servers/${serverId}/deployments/wireguard/clients`,
      body,
    ).then(r => r.data),

  removeClient: (serverId: number, name: string) =>
    http.delete(
      `/servers/${serverId}/deployments/wireguard/clients/${encodeURIComponent(name)}`,
    ),

  syncClients: (serverId: number) =>
    http.post<DeploymentClientSyncResult>(
      `/servers/${serverId}/deployments/wireguard/clients/sync`,
    ).then(r => r.data),

  getClientConf: (serverId: number, name: string) =>
    http.get<DeploymentClientConf>(
      `/servers/${serverId}/deployments/wireguard/clients/${encodeURIComponent(name)}/conf`,
    ).then(r => r.data),

  exportClientToNode: (
    serverId: number, name: string, body: ExportClientToNodeRequest = {},
  ) =>
    http.post<Node>(
      `/servers/${serverId}/deployments/wireguard/clients/${encodeURIComponent(name)}/export-node`,
      body,
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
  probeServer: async (id: number): Promise<XuiServer> => {
    const r = await http.post(`/xui/servers/${id}/probe`)
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
  ): Promise<XuiInbound> => {
    const r = await http.post(`/xui/servers/${serverId}/inbounds`, body)
    return r.data
  },

  deleteInbound: async (serverId: number, inboundId: number): Promise<void> => {
    await http.delete(`/xui/servers/${serverId}/inbounds/${inboundId}`)
  },

  // ── Clients ─────────────────────────────────────────────────────────────
  addClient: async (
    serverId: number,
    inboundId: number,
    body: { label?: string; extras?: Record<string, unknown> } = {},
  ): Promise<XuiClientType> => {
    const r = await http.post(
      `/xui/servers/${serverId}/inbounds/${inboundId}/clients`,
      body,
    )
    return r.data
  },

  deleteClient: async (
    serverId: number, inboundId: number, clientUuid: string,
  ): Promise<void> => {
    await http.delete(
      `/xui/servers/${serverId}/inbounds/${inboundId}/clients/${encodeURIComponent(clientUuid)}`,
    )
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
  }): Promise<ChainRead> => {
    const r = await http.post('/xui/chains', params)
    return r.data
  },

  deleteChain: async (id: number): Promise<void> => {
    await http.delete(`/xui/chains/${id}`)
  },

  // Chain clients
  listChainClients: async (chainId: number): Promise<ChainClientRead[]> => {
    const r = await http.get(`/xui/chains/${chainId}/clients`)
    return r.data
  },

  /** Spawns N panel-side clients (one per channel). Backend label
   *  default = `pi-XXXXXXXX` (8 hex). */
  addChainClient: async (
    chainId: number, body: { label?: string } = {},
  ): Promise<ChainClientRead> => {
    const r = await http.post(`/xui/chains/${chainId}/clients`, body)
    return r.data
  },

  deleteChainClient: async (
    chainId: number, chainClientId: number,
  ): Promise<void> => {
    await http.delete(`/xui/chains/${chainId}/clients/${chainClientId}`)
  },

  /** Convert N (chain-client × channel) pairs into N Node rows.
   *  Empty `channel_ids` means "all channels"; idempotent on
   *  channels that were already exported (skipped silently). */
  exportChainClientNodes: async (
    chainId: number, chainClientId: number,
    body: { channel_ids?: number[]; name_prefix?: string } = {},
  ): Promise<{ exported_node_ids: number[] }> => {
    const r = await http.post(
      `/xui/chains/${chainId}/clients/${chainClientId}/export-nodes`,
      body,
    )
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
