import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { diagnosticsApi } from '@/api/client'
import {
  Activity,
  Wifi,
  Globe,
  Shield,
  Cpu,
  HardDrive,
  Thermometer,
  Network,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  Terminal,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Router,
  Radio,
} from 'lucide-react'
import { clsx } from 'clsx'

// ── Health Checks Section ───────────────────────────────────────────────────

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-red-950/30 border border-red-900/50 px-4 py-3 text-xs text-red-300">
      <XCircle className="h-4 w-4 text-red-400 flex-shrink-0" />
      {message}
    </div>
  )
}

function HealthChecks() {
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['diagnostics', 'health'],
    queryFn: diagnosticsApi.healthChecks,
    staleTime: 60_000,
  })

  const iconMap: Record<string, typeof Activity> = {
    gateway: Router,
    dns: Globe,
    dns_udp: Globe,
    internet: Wifi,
    xray: Shield,
    nftables: Shield,
    tun: Network,
  }

  const labelMap: Record<string, string> = {
    gateway: 'Default Gateway',
    dns: 'DNS Resolution',
    dns_udp: 'DNS over UDP',
    internet: 'Internet Access',
    xray: 'Xray Process',
    nftables: 'nftables Rules',
    tun: 'TUN Interface',
  }

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Activity className="h-4 w-4 text-brand-400" />
          Health Checks
        </h2>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-gray-400 hover:bg-gray-800 hover:text-gray-200 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={clsx('h-3 w-3', isFetching && 'animate-spin')} />
          Refresh
        </button>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-0.5 p-2">
        {isError ? (
          <div className="col-span-full"><ErrorBox message="Failed to load health checks" /></div>
        ) : isLoading ? (
          Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-16 rounded-lg bg-gray-800/50 animate-pulse" />
          ))
        ) : (
          data?.checks.map((c) => {
            const Icon = iconMap[c.name] || Activity
            const isInfo = c.info === true  // neutral/informational status
            const colorClass = isInfo
              ? 'border-blue-900/50 bg-blue-950/30'
              : c.ok
                ? 'border-green-900/50 bg-green-950/30'
                : 'border-red-900/50 bg-red-950/30'
            const iconBg = isInfo ? 'bg-blue-900/50' : c.ok ? 'bg-green-900/50' : 'bg-red-900/50'
            const iconColor = isInfo ? 'text-blue-400' : c.ok ? 'text-green-400' : 'text-red-400'
            const textColor = isInfo ? 'text-blue-400' : c.ok ? 'text-green-400' : 'text-red-400'
            return (
              <div
                key={c.name}
                className={clsx('flex items-center gap-3 rounded-lg px-3 py-3 border', colorClass)}
              >
                <div className={clsx('flex h-8 w-8 items-center justify-center rounded-lg flex-shrink-0', iconBg)}>
                  <Icon className={clsx('h-4 w-4', iconColor)} />
                </div>
                <div className="min-w-0">
                  <div className="text-xs font-medium text-gray-300 truncate">
                    {labelMap[c.name] || c.name}
                  </div>
                  <div className={clsx('text-[11px] truncate', textColor)}>
                    {c.detail}
                  </div>
                </div>
                {isInfo
                  ? <AlertTriangle className="h-4 w-4 text-blue-400 ml-auto flex-shrink-0" />
                  : c.ok
                    ? <CheckCircle2 className="h-4 w-4 text-green-500 ml-auto flex-shrink-0" />
                    : <XCircle className="h-4 w-4 text-red-500 ml-auto flex-shrink-0" />
                }
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}

// ── Network Section ─────────────────────────────────────────────────────────

function NetworkInfo() {
  const [expanded, setExpanded] = useState<Record<string, boolean>>({
    interfaces: true,
    gateway: true,
    routes: false,
    listeners: false,
  })
  const { data, isLoading, isError } = useQuery({
    queryKey: ['diagnostics', 'network'],
    queryFn: diagnosticsApi.network,
    staleTime: 60_000,
  })

  const toggle = (key: string) => setExpanded((e) => ({ ...e, [key]: !e[key] }))

  const section = (key: string, icon: typeof Network, label: string, children: React.ReactNode) => (
    <div key={key} className="border-b border-gray-800 last:border-0">
      <button
        onClick={() => toggle(key)}
        className="flex items-center gap-2 w-full px-4 py-2.5 text-left hover:bg-gray-800/50 transition-colors"
      >
        {expanded[key] ? <ChevronDown className="h-3.5 w-3.5 text-gray-500" /> : <ChevronRight className="h-3.5 w-3.5 text-gray-500" />}
        {(() => { const I = icon; return <I className="h-4 w-4 text-brand-400" /> })()}
        <span className="text-sm font-medium text-gray-300">{label}</span>
      </button>
      {expanded[key] && <div className="px-4 pb-3">{children}</div>}
    </div>
  )

  if (isLoading) return <div className="h-32 rounded-xl border border-gray-800 bg-gray-900/50 animate-pulse" />
  if (isError) return <ErrorBox message="Failed to load network info" />

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50">
      <div className="px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Network className="h-4 w-4 text-brand-400" />
          Network
        </h2>
      </div>

      {/* Gateway & Recommendation */}
      {data?.gateway && section('gateway', Router, `Gateway: ${data.gateway.gateway || 'N/A'}`, (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="rounded-lg bg-gray-800/80 px-3 py-2">
              <span className="text-gray-500">IP address</span>
              <div className="text-gray-200 font-mono mt-0.5">{data.gateway.my_ip || '—'}</div>
            </div>
            <div className="rounded-lg bg-gray-800/80 px-3 py-2">
              <span className="text-gray-500">Subnet</span>
              <div className="text-gray-200 font-mono mt-0.5">{data.gateway.subnet || '—'}</div>
            </div>
            <div className="rounded-lg bg-gray-800/80 px-3 py-2">
              <span className="text-gray-500">Gateway</span>
              <div className="text-gray-200 font-mono mt-0.5">{data.gateway.gateway || '—'}</div>
            </div>
            <div className="rounded-lg bg-gray-800/80 px-3 py-2">
              <span className="text-gray-500">Interface</span>
              <div className="text-gray-200 font-mono mt-0.5">{data.gateway.device || '—'}</div>
            </div>
          </div>
          {data.gateway.recommendation && (
            <div className="flex items-start gap-2 rounded-lg bg-blue-950/30 border border-blue-900/50 px-3 py-2 text-xs text-blue-300">
              <AlertTriangle className="h-3.5 w-3.5 text-blue-400 flex-shrink-0 mt-0.5" />
              {data.gateway.recommendation}
            </div>
          )}
        </div>
      ))}

      {/* Interfaces */}
      {data?.interfaces && section('interfaces', Radio, `Interfaces (${data.interfaces.length})`, (
        <div className="space-y-1">
          {data.interfaces.map((iface) => (
            <div key={iface.name} className="flex items-center gap-3 rounded-lg bg-gray-800/80 px-3 py-2 text-xs">
              <span className={clsx(
                'h-2 w-2 rounded-full flex-shrink-0',
                iface.state === 'UP' ? 'bg-green-500' : 'bg-gray-600',
              )} />
              <span className="font-mono text-gray-200 w-16">{iface.name}</span>
              <span className={clsx(
                'text-[10px] px-1.5 py-0.5 rounded font-medium',
                iface.state === 'UP' ? 'bg-green-900/50 text-green-400' : 'bg-gray-700 text-gray-400',
              )}>{iface.state}</span>
              <span className="text-gray-400 font-mono truncate">{iface.addresses.join(', ')}</span>
            </div>
          ))}
        </div>
      ))}

      {/* Routes */}
      {data?.routes && section('routes', Globe, `Routes (${data.routes.length})`, (
        <div className="rounded-lg bg-gray-950 border border-gray-800 p-2 max-h-48 overflow-y-auto">
          {data.routes.map((r, i) => (
            <div key={i} className="font-mono text-xs text-gray-400 py-0.5 px-2 hover:bg-gray-800/50 rounded">
              {r.route}
            </div>
          ))}
        </div>
      ))}

      {/* Listening Ports */}
      {data?.listeners && section('listeners', Terminal, `Listening Ports (${data.listeners.length})`, (
        <div className="rounded-lg bg-gray-950 border border-gray-800 overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 border-b border-gray-800">
                <th className="text-left px-3 py-1.5 font-medium">Proto</th>
                <th className="text-left px-3 py-1.5 font-medium">Listen</th>
                <th className="text-left px-3 py-1.5 font-medium">Process</th>
              </tr>
            </thead>
            <tbody>
              {data.listeners.map((l, i) => (
                <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                  <td className="px-3 py-1.5 font-mono text-gray-400">{l.proto}</td>
                  <td className="px-3 py-1.5 font-mono text-gray-200">{l.listen}</td>
                  <td className="px-3 py-1.5 text-gray-500 truncate max-w-[200px]">{l.process}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ))}
    </div>
  )
}

// ── Resources Section ───────────────────────────────────────────────────────

const TEMP_WARNING = 60
const TEMP_CRITICAL = 75
const TEMP_MAX = 85

function Resources() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ['diagnostics', 'resources'],
    queryFn: diagnosticsApi.resources,
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  if (isLoading) return <div className="h-32 rounded-xl border border-gray-800 bg-gray-900/50 animate-pulse" />
  if (isError) return <ErrorBox message="Failed to load system resources" />

  const memPercent = data?.memory?.total_mb
    ? Math.round((data.memory.used_mb / data.memory.total_mb) * 100)
    : 0

  const diskPercent = data?.disk?.use_percent
    ? parseInt(data.disk.use_percent)
    : 0

  const loadPercent = data?.load_avg?.[0] && data?.cpu_count
    ? Math.min(100, Math.round((parseFloat(data.load_avg[0]) / data.cpu_count) * 100))
    : 0

  const bar = (percent: number, color: string) => (
    <div className="h-2 w-full rounded-full bg-gray-800 overflow-hidden">
      <div
        className={clsx('h-full rounded-full transition-all', color)}
        style={{ width: `${percent}%` }}
      />
    </div>
  )

  const barColor = (p: number) =>
    p > 90 ? 'bg-red-500' : p > 70 ? 'bg-yellow-500' : 'bg-green-500'

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50">
      <div className="px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Cpu className="h-4 w-4 text-brand-400" />
          System Resources
        </h2>
        {data?.uptime && (
          <div className="text-xs text-gray-500 mt-0.5">{data.uptime}</div>
        )}
      </div>
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 p-4">
        {/* CPU Load */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Cpu className="h-3.5 w-3.5 text-gray-500" />
            <span className="text-xs text-gray-400">CPU Load</span>
          </div>
          {bar(loadPercent, barColor(loadPercent))}
          <div className="text-xs text-gray-300 font-mono">
            {data?.load_avg?.join(' / ') || '—'}
            <span className="text-gray-600 ml-1">({data?.cpu_count} cores)</span>
          </div>
        </div>

        {/* Memory */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <HardDrive className="h-3.5 w-3.5 text-gray-500" />
            <span className="text-xs text-gray-400">Memory</span>
          </div>
          {bar(memPercent, barColor(memPercent))}
          <div className="text-xs text-gray-300 font-mono">
            {data?.memory?.used_mb || 0} / {data?.memory?.total_mb || 0} MB
            <span className="text-gray-600 ml-1">({memPercent}%)</span>
          </div>
        </div>

        {/* Disk */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <HardDrive className="h-3.5 w-3.5 text-gray-500" />
            <span className="text-xs text-gray-400">Disk</span>
          </div>
          {bar(diskPercent, barColor(diskPercent))}
          <div className="text-xs text-gray-300 font-mono">
            {data?.disk?.used || '—'} / {data?.disk?.total || '—'}
            <span className="text-gray-600 ml-1">({data?.disk?.use_percent || '—'})</span>
          </div>
        </div>

        {/* Temperature */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Thermometer className="h-3.5 w-3.5 text-gray-500" />
            <span className="text-xs text-gray-400">Temperature</span>
          </div>
          {data?.temperature != null ? (
            <>
              {bar(
                Math.min(100, Math.round((data.temperature / TEMP_MAX) * 100)),
                data.temperature > TEMP_CRITICAL ? 'bg-red-500' : data.temperature > TEMP_WARNING ? 'bg-yellow-500' : 'bg-green-500',
              )}
              <div className={clsx(
                'text-xs font-mono',
                data.temperature > TEMP_CRITICAL ? 'text-red-400' : data.temperature > TEMP_WARNING ? 'text-yellow-400' : 'text-gray-300',
              )}>
                {data.temperature}°C
              </div>
            </>
          ) : (
            <div className="text-xs text-gray-600">N/A</div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Docker Logs Section ─────────────────────────────────────────────────────

function DockerLogs() {
  const [lines, setLines] = useState(100)
  const [level, setLevel] = useState('')
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['diagnostics', 'logs', lines, level],
    queryFn: () => diagnosticsApi.logs(lines, level),
    staleTime: 30_000,
  })

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Terminal className="h-4 w-4 text-brand-400" />
          Backend Logs
        </h2>
        <div className="flex items-center gap-2">
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            className="rounded-lg bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-300 focus:outline-none"
          >
            <option value="">All levels</option>
            <option value="ERROR">ERROR</option>
            <option value="WARNING">WARNING</option>
            <option value="INFO">INFO</option>
          </select>
          <select
            value={lines}
            onChange={(e) => setLines(Number(e.target.value))}
            className="rounded-lg bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-300 focus:outline-none"
          >
            <option value={50}>50 lines</option>
            <option value={100}>100 lines</option>
            <option value={200}>200 lines</option>
            <option value={500}>500 lines</option>
          </select>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-gray-400 hover:bg-gray-800 hover:text-gray-200 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={clsx('h-3 w-3', isFetching && 'animate-spin')} />
          </button>
        </div>
      </div>
      <div className="relative">
        {isError ? (
          <div className="p-4"><ErrorBox message="Failed to load logs" /></div>
        ) : isLoading ? (
          <div className="h-64 animate-pulse bg-gray-800/30" />
        ) : (
          <div className="max-h-96 overflow-y-auto overflow-x-auto p-2 bg-gray-950 rounded-b-xl">
            <pre className="text-[11px] leading-relaxed font-mono">
              {data?.lines.map((line, i) => {
                const isErr = /ERROR|CRITICAL/i.test(line)
                const isWarn = /WARNING|WARN/i.test(line)
                return (
                  <div
                    key={i}
                    className={clsx(
                      'px-2 py-0.5 rounded hover:bg-gray-800/50',
                      isErr ? 'text-red-400' : isWarn ? 'text-yellow-400' : 'text-gray-400',
                    )}
                  >
                    {line}
                  </div>
                )
              })}
            </pre>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Main Page ───────────────────────────────────────────────────────────────

export function Diagnostics() {
  return (
    <div className="p-6 space-y-4 max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-100">Diagnostics</h1>
          <p className="text-sm text-gray-500 mt-0.5">System health, network analysis, resources & logs</p>
        </div>
      </div>

      <HealthChecks />
      <Resources />
      <NetworkInfo />
      <DockerLogs />
    </div>
  )
}
