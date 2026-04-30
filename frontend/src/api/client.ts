import axios from 'axios'
import type {
  LoginRequest, TokenResponse, ChangePasswordRequest, UserInfo,
  Node, NodeCreate, NodeUpdate, NodeImportRequest, NodeImportResponse,
  RoutingRule, RoutingRuleCreate, RoutingRuleUpdate, ArpDevice,
  BulkRuleCreate, BulkRuleResult,
  Subscription, SubscriptionCreate, SubscriptionUpdate,
  SystemStatus, SystemSettings, SystemVersions, ProxyMode,
  GeoDataStatus,
  DnsRule, DnsRuleCreate, DnsRuleUpdate, DnsSettings, DnsTestResult,
  DnsQueryLog, DnsQueryStats,
  HealthResult, SpeedTestResult,
  BalancerGroup, BalancerGroupCreate, BalancerGroupUpdate,
  NodeCircle, NodeCircleCreate, NodeCircleUpdate,
  Device, DeviceUpdate, DeviceBulkUpdate, DeviceScanResult,
  SystemMetric,
  NaiveSidecarStatus, NaiveSidecarLogs,
  V2RayImportRequest, V2RayImportResult,
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

export const geodataApi = {
  status: () =>
    http.get<GeoDataStatus>('/geodata/status').then(r => r.data),

  update: (urls?: { geoip_url?: string; geosite_url?: string; mmdb_url?: string; type?: string }) =>
    http.post('/geodata/update', urls ?? {}).then(r => r.data),
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
