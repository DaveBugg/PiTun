import * as React from 'react'
import { useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { diagnosticsApi } from '@/api/client'
import type { RouteExplainRequest } from '@/api/client'
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
  Search,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useT } from '@/hooks/useT'

// ── Health Checks Section ───────────────────────────────────────────────────

function ErrorBox({ message }: { message: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900/50 px-4 py-3 text-xs text-red-700 dark:text-red-300">
      <XCircle className="h-4 w-4 text-red-600 dark:text-red-400 shrink-0" />
      {message}
    </div>
  )
}

function HealthChecks() {
  const t = useT()
  const { data, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['diagnostics', 'health'],
    queryFn: diagnosticsApi.healthChecks,
    staleTime: 60_000,
  })

  const iconMap: Record<string, typeof Activity> = {
    gateway: Router,
    dns: Globe,
    dns_udp: Globe,
    // Legacy single-row `internet` kept for back-compat with old
    // installs running v1.3.6 backend — gone after v1.3.7 ships.
    internet: Wifi,
    internet_direct: Wifi,
    internet_via_vpn: Wifi,
    xray: Shield,
    nftables: Shield,
    tun: Network,
  }

  const labelMap: Record<string, string> = {
    gateway: t('Default Gateway', 'Шлюз по умолчанию'),
    dns: t('DNS Resolution', 'Разрешение DNS'),
    dns_udp: t('DNS over UDP', 'DNS через UDP'),
    internet: t('Internet Access', 'Доступ в интернет'),
    internet_direct: t('Internet (direct)', 'Интернет (напрямую)'),
    internet_via_vpn: t('Internet (via VPN)', 'Интернет (через VPN)'),
    xray: t('Xray Process', 'Процесс Xray'),
    nftables: t('nftables Rules', 'Правила nftables'),
    tun: t('TUN Interface', 'TUN-интерфейс'),
  }

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50">
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Activity className="h-4 w-4 text-brand-400" />
          {t('Health Checks', 'Проверки состояния')}
        </h2>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs text-gray-400 hover:bg-gray-800 hover:text-gray-200 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={clsx('h-3 w-3', isFetching && 'animate-spin')} />
          {t('Refresh', 'Обновить')}
        </button>
      </div>
      {/* Single column on phones — at 2-up the check name + value
          truncated to "De..." / "Xr..." which made the check unreadable.
          Stack to one row each below sm. */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-0.5 p-2">
        {isError ? (
          <div className="col-span-full"><ErrorBox message={t('Failed to load health checks', 'Не удалось загрузить проверки')} /></div>
        ) : isLoading ? (
          Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-16 rounded-lg bg-gray-800/50 animate-pulse" />
          ))
        ) : (
          data?.checks.map((c) => {
            const Icon = iconMap[c.name] || Activity
            const isInfo = c.info === true  // neutral/informational status
            const colorClass = isInfo
              ? 'border-blue-200 dark:border-blue-900/50 bg-blue-50 dark:bg-blue-950/30'
              : c.ok
                ? 'border-green-200 dark:border-green-900/50 bg-green-50 dark:bg-green-950/30'
                : 'border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-950/30'
            const iconBg = isInfo ? 'bg-blue-50 dark:bg-blue-900/50' : c.ok ? 'bg-green-50 dark:bg-green-900/50' : 'bg-red-50 dark:bg-red-900/50'
            const iconColor = isInfo ? 'text-blue-600 dark:text-blue-400' : c.ok ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'
            const textColor = isInfo ? 'text-blue-600 dark:text-blue-400' : c.ok ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400'
            return (
              <div
                key={c.name}
                className={clsx('flex items-center gap-3 rounded-lg px-3 py-3 border', colorClass)}
              >
                <div className={clsx('flex h-8 w-8 items-center justify-center rounded-lg shrink-0', iconBg)}>
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
                  ? <AlertTriangle className="h-4 w-4 text-blue-600 dark:text-blue-400 ml-auto shrink-0" />
                  : c.ok
                    ? <CheckCircle2 className="h-4 w-4 text-green-500 ml-auto shrink-0" />
                    : <XCircle className="h-4 w-4 text-red-500 ml-auto shrink-0" />
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
  const t = useT()
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
  if (isError) return <ErrorBox message={t('Failed to load network info', 'Не удалось загрузить сведения о сети')} />

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50">
      <div className="px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Network className="h-4 w-4 text-brand-400" />
          {t('Network', 'Сеть')}
        </h2>
      </div>

      {/* Gateway & Recommendation */}
      {data?.gateway && section('gateway', Router, `${t('Gateway', 'Шлюз')}: ${data.gateway.gateway || 'N/A'}`, (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="rounded-lg bg-gray-800/80 px-3 py-2">
              <span className="text-gray-500">{t('IP address', 'IP-адрес')}</span>
              <div className="text-gray-200 font-mono mt-0.5">{data.gateway.my_ip || '—'}</div>
            </div>
            <div className="rounded-lg bg-gray-800/80 px-3 py-2">
              <span className="text-gray-500">{t('Subnet', 'Подсеть')}</span>
              <div className="text-gray-200 font-mono mt-0.5">{data.gateway.subnet || '—'}</div>
            </div>
            <div className="rounded-lg bg-gray-800/80 px-3 py-2">
              <span className="text-gray-500">{t('Gateway', 'Шлюз')}</span>
              <div className="text-gray-200 font-mono mt-0.5">{data.gateway.gateway || '—'}</div>
            </div>
            <div className="rounded-lg bg-gray-800/80 px-3 py-2">
              <span className="text-gray-500">{t('Interface', 'Интерфейс')}</span>
              <div className="text-gray-200 font-mono mt-0.5">{data.gateway.device || '—'}</div>
            </div>
          </div>
          {data.gateway.recommendation && (
            <div className="flex items-start gap-2 rounded-lg bg-blue-50 dark:bg-blue-950/30 border border-blue-200 dark:border-blue-900/50 px-3 py-2 text-xs text-blue-700 dark:text-blue-300">
              <AlertTriangle className="h-3.5 w-3.5 text-blue-600 dark:text-blue-400 shrink-0 mt-0.5" />
              {data.gateway.recommendation}
            </div>
          )}
        </div>
      ))}

      {/* Interfaces */}
      {data?.interfaces && section('interfaces', Radio, `${t('Interfaces', 'Интерфейсы')} (${data.interfaces.length})`, (
        <div className="space-y-1">
          {data.interfaces.map((iface) => (
            <div key={iface.name} className="flex items-center gap-3 rounded-lg bg-gray-800/80 px-3 py-2 text-xs">
              <span className={clsx(
                'h-2 w-2 rounded-full shrink-0',
                iface.state === 'UP' ? 'bg-green-500' : 'bg-gray-600',
              )} />
              <span className="font-mono text-gray-200 w-16">{iface.name}</span>
              <span className={clsx(
                'text-[10px] px-1.5 py-0.5 rounded-sm font-medium',
                iface.state === 'UP' ? 'bg-green-50 dark:bg-green-900/50 text-green-600 dark:text-green-400' : 'bg-gray-700 text-gray-400',
              )}>{iface.state}</span>
              <span className="text-gray-400 font-mono truncate">{iface.addresses.join(', ')}</span>
            </div>
          ))}
        </div>
      ))}

      {/* Routes */}
      {data?.routes && section('routes', Globe, `${t('Routes', 'Маршруты')} (${data.routes.length})`, (
        <div className="rounded-lg bg-gray-950 border border-gray-800 p-2 max-h-48 overflow-y-auto">
          {data.routes.map((r, i) => (
            <div key={i} className="font-mono text-xs text-gray-400 py-0.5 px-2 hover:bg-gray-800/50 rounded-sm">
              {r.route}
            </div>
          ))}
        </div>
      ))}

      {/* Listening Ports */}
      {data?.listeners && section('listeners', Terminal, `${t('Listening Ports', 'Слушающие порты')} (${data.listeners.length})`, (
        <div className="rounded-lg bg-gray-950 border border-gray-800 overflow-hidden">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-gray-500 border-b border-gray-800">
                <th className="text-left px-3 py-1.5 font-medium">{t('Proto', 'Протокол')}</th>
                <th className="text-left px-3 py-1.5 font-medium">{t('Listen', 'Адрес')}</th>
                <th className="text-left px-3 py-1.5 font-medium">{t('Process', 'Процесс')}</th>
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
  const t = useT()
  const { data, isLoading, isError } = useQuery({
    queryKey: ['diagnostics', 'resources'],
    queryFn: diagnosticsApi.resources,
    staleTime: 30_000,
    refetchInterval: 60_000,
  })

  if (isLoading) return <div className="h-32 rounded-xl border border-gray-800 bg-gray-900/50 animate-pulse" />
  if (isError) return <ErrorBox message={t('Failed to load system resources', 'Не удалось загрузить ресурсы системы')} />

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
          {t('System Resources', 'Ресурсы системы')}
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
            <span className="text-xs text-gray-400">{t('CPU Load', 'Загрузка CPU')}</span>
          </div>
          {bar(loadPercent, barColor(loadPercent))}
          <div className="text-xs text-gray-300 font-mono">
            {data?.load_avg?.join(' / ') || '—'}
            <span className="text-gray-600 ml-1">({data?.cpu_count} {t('cores', 'ядер')})</span>
          </div>
        </div>

        {/* Memory */}
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <HardDrive className="h-3.5 w-3.5 text-gray-500" />
            <span className="text-xs text-gray-400">{t('Memory', 'Память')}</span>
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
            <span className="text-xs text-gray-400">{t('Disk', 'Диск')}</span>
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
            <span className="text-xs text-gray-400">{t('Temperature', 'Температура')}</span>
          </div>
          {data?.temperature != null ? (
            <>
              {bar(
                Math.min(100, Math.round((data.temperature / TEMP_MAX) * 100)),
                data.temperature > TEMP_CRITICAL ? 'bg-red-500' : data.temperature > TEMP_WARNING ? 'bg-yellow-500' : 'bg-green-500',
              )}
              <div className={clsx(
                'text-xs font-mono',
                data.temperature > TEMP_CRITICAL ? 'text-red-600 dark:text-red-400' : data.temperature > TEMP_WARNING ? 'text-yellow-600 dark:text-yellow-400' : 'text-gray-300',
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
  const t = useT()
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
          {t('Backend Logs', 'Логи бэкенда')}
        </h2>
        <div className="flex items-center gap-2">
          <select
            value={level}
            onChange={(e) => setLevel(e.target.value)}
            className="rounded-lg bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-300 focus:outline-hidden"
          >
            <option value="">{t('All levels', 'Все уровни')}</option>
            <option value="ERROR">ERROR</option>
            <option value="WARNING">WARNING</option>
            <option value="INFO">INFO</option>
          </select>
          <select
            value={lines}
            onChange={(e) => setLines(Number(e.target.value))}
            className="rounded-lg bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-300 focus:outline-hidden"
          >
            <option value={50}>50 {t('lines', 'строк')}</option>
            <option value={100}>100 {t('lines', 'строк')}</option>
            <option value={200}>200 {t('lines', 'строк')}</option>
            <option value={500}>500 {t('lines', 'строк')}</option>
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
          <div className="p-4"><ErrorBox message={t('Failed to load logs', 'Не удалось загрузить логи')} /></div>
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
                      'px-2 py-0.5 rounded-sm hover:bg-gray-800/50',
                      isErr ? 'text-red-600 dark:text-red-400' : isWarn ? 'text-yellow-600 dark:text-yellow-400' : 'text-gray-400',
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

// ── Route Explainer ─────────────────────────────────────────────────────────

function RouteExplainer() {
  const t = useT()
  const [target, setTarget] = useState('')
  const [port, setPort] = useState(443)
  const [protocol, setProtocol] = useState<'tcp' | 'udp'>('tcp')
  const [fromMac, setFromMac] = useState('')
  const [verify, setVerify] = useState(true)
  const [reach, setReach] = useState(false)

  const mut = useMutation({
    mutationFn: (body: RouteExplainRequest) => diagnosticsApi.explain(body),
  })

  const run = () => {
    const tgt = target.trim()
    if (!tgt) return
    mut.mutate({
      target: tgt, port, protocol,
      from_mac: fromMac.trim() || null,
      verify_routing: verify,
      test_reachability: reach,
    })
  }

  const r = mut.data
  const stage = (icon: typeof Search, label: string, children: React.ReactNode, tone?: 'ok' | 'warn' | 'bad') => (
    <div className={clsx(
      'rounded-lg border p-3',
      tone === 'ok' ? 'border-green-200 dark:border-green-900/50 bg-green-50 dark:bg-green-950/20'
        : tone === 'bad' ? 'border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-950/20'
        : tone === 'warn' ? 'border-amber-200 dark:border-amber-900/50 bg-amber-50 dark:bg-amber-950/20'
        : 'border-gray-800 bg-gray-900/40',
    )}>
      <div className="flex items-center gap-2 text-xs font-semibold text-gray-300 mb-2">
        {React.createElement(icon, { className: 'h-3.5 w-3.5' })}
        {label}
      </div>
      <div className="text-xs text-gray-400 space-y-1">{children}</div>
    </div>
  )
  const kv = (k: string, v: React.ReactNode) => (
    <div className="flex gap-2"><span className="text-gray-500 min-w-28">{k}</span><span className="text-gray-300 font-mono break-all">{v}</span></div>
  )

  const actionColor = (a?: string | null) =>
    a === 'block' ? 'text-red-600 dark:text-red-400' : a === 'direct' ? 'text-green-600 dark:text-green-400'
      : a === 'proxy' || a?.startsWith('node:') ? 'text-brand-400' : 'text-gray-300'

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-4">
      <div className="flex items-center gap-2 mb-1">
        <Search className="h-4 w-4 text-brand-400" />
        <h2 className="text-sm font-semibold text-gray-100">{t('Route Explainer', 'Разбор маршрута')}</h2>
      </div>
      <p className="text-xs text-gray-500 mb-3">
        {t(
          'Where does traffic to a target go? Shows the matched DNS rule + server, the matched routing rule + outbound, and (optionally) whether it actually connects.',
          'Куда пойдёт трафик к цели? Показывает сработавшее DNS-правило + сервер, сработавшее правило маршрутизации + outbound и (опционально) реально ли устанавливается соединение.',
        )}
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-6 gap-2 mb-2">
        <input
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') run() }}
          placeholder={t('domain or IP — e.g. youtube.com', 'домен или IP — напр. youtube.com')}
          className="sm:col-span-3 rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
        />
        <input
          type="number" value={port}
          onChange={(e) => setPort(Number(e.target.value) || 443)}
          placeholder={t('port', 'порт')}
          className="rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
        />
        <select
          value={protocol} onChange={(e) => setProtocol(e.target.value as 'tcp' | 'udp')}
          className="rounded-sm bg-gray-800 border border-gray-700 px-2 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
        >
          <option value="tcp">TCP</option>
          <option value="udp">UDP</option>
        </select>
        <button
          onClick={run} disabled={mut.isPending || !target.trim()}
          className="rounded-sm bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 flex items-center justify-center gap-1.5"
        >
          {mut.isPending ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Search className="h-3.5 w-3.5" />}
          {t('Explain', 'Разобрать')}
        </button>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-3 text-xs">
        <input
          value={fromMac} onChange={(e) => setFromMac(e.target.value)}
          placeholder={t('from device MAC (optional — for routing set)', 'MAC устройства (опционально — для routing set)')}
          className="flex-1 min-w-48 rounded-sm bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-300 focus:border-brand-500 focus:outline-hidden font-mono"
        />
        <label className="flex items-center gap-1.5 cursor-pointer text-gray-400">
          <input type="checkbox" checked={verify} onChange={(e) => setVerify(e.target.checked)}
            className="rounded-sm border-gray-600 bg-gray-700" />
          {t('Verify with live xray (exact geosite/geoip)', 'Проверить через живой xray (точные geosite/geoip)')}
        </label>
        <label className="flex items-center gap-1.5 cursor-pointer text-gray-400">
          <input type="checkbox" checked={reach} onChange={(e) => setReach(e.target.checked)}
            className="rounded-sm border-gray-600 bg-gray-700" />
          {t('Test reachability (connect)', 'Проверить доступность (соединение)')}
        </label>
      </div>

      {mut.isError && <ErrorBox message={t('Explain request failed — check backend logs.', 'Запрос разбора не удался — проверьте логи бэкенда.')} />}

      {r && (
        <div className="space-y-2">
          {/* DNS stage */}
          {stage(Globe, 'DNS', r.dns.is_ip ? (
            <div className="text-gray-400">{t('Target is a literal IP — no DNS resolution needed.', 'Цель — это IP-адрес, DNS-разрешение не требуется.')}</div>
          ) : (
            <>
              {r.dns.matched_rule_name
                ? kv(t('matched rule', 'правило'), <>{r.dns.matched_rule_name} <span className="text-gray-500">({r.dns.matched_pattern})</span></>)
                : kv(t('matched rule', 'правило'), <span className="text-gray-500">{t('none — using global upstream', 'нет — используется глобальный upstream')}</span>)}
              {kv(t('server', 'сервер'), <>{r.dns.server} <span className="text-gray-500">[{r.dns.server_type}{r.dns.server_type === 'dot' ? ' = plaintext TCP/53' : ''}]</span></>)}
              {kv(t('resolved', 'разрешено'), r.dns.resolved_ips.length
                ? r.dns.resolved_ips.join(', ')
                : <span className="text-amber-600 dark:text-amber-400">{r.dns.resolve_error || t('no answer', 'нет ответа')}</span>)}
              {kv(t('IP strategy', 'стратегия IP'), r.dns.query_strategy)}
              {r.dns.geosite_uncertain && (
                <div className="text-amber-600 dark:text-amber-400 mt-1">{t('⚠ A geosite DNS rule exists and might also match — global upstream shown.', '⚠ Есть geosite DNS-правило, которое тоже может сработать — показан глобальный upstream.')}</div>
              )}
            </>
          ), r.dns.resolved_ips.length || r.dns.is_ip ? 'ok' : 'warn')}

          {/* Routing stage */}
          {stage(Activity, t('Routing', 'Маршрутизация'), (
            <>
              {r.routing.set_name && kv(t('device set', 'набор устройств'), <span className="text-purple-700 dark:text-purple-300">{r.routing.set_name}</span>)}
              {kv(t('matched rule', 'правило'), r.routing.matched_rule_name
                ? <>{r.routing.matched_rule_name}
                    {!r.routing.certain && r.routing.method !== 'xray_probe' &&
                      <span className="text-amber-600 dark:text-amber-400"> {t('(candidate — needs xray)', '(кандидат — нужен xray)')}</span>}
                  </>
                : r.routing.method === 'xray_probe'
                  ? <span className="text-gray-500">{t('resolved by xray — exact rule not in access log', 'определено xray — точного правила нет в access-логе')}</span>
                  : <span className="text-gray-500">{t('none', 'нет')}</span>)}
              {r.routing.matched_rule_type && kv(t('rule type', 'тип правила'), <>{r.routing.matched_rule_type} = <span className="text-gray-400">{r.routing.matched_value}</span></>)}
              {kv(t('action', 'действие'), <span className={actionColor(r.routing.action)}>{r.routing.action}</span>)}
              {kv('outbound', <>
                <span className={actionColor(r.routing.action)}>{r.routing.outbound}</span>
                {r.routing.outbound_label && <span className="text-gray-500"> ({r.routing.outbound_label})</span>}
              </>)}
              {kv(t('decided by', 'решено'), r.routing.method === 'xray_probe'
                ? <span className="text-green-600 dark:text-green-400">{t('live xray probe (ground truth)', 'живой xray-проб (точный ответ)')}</span>
                : <span>{t('rule matcher', 'сопоставление правил')}</span>)}
              {kv(t('rules walked', 'правил проверено'), r.routing.rules_evaluated)}
              {!r.routing.certain && (
                <div className="text-amber-600 dark:text-amber-400 mt-1">
                  {t('⚠ Uncertain —', '⚠ Неточно —')} {r.routing.blocking_rule} {t('is a geosite/geoip category.', '— это geosite/geoip категория.')}
                  {!verify && t(' Enable "Verify with live xray" for the exact decision.', ' Включите «Проверить через живой xray» для точного результата.')}
                </div>
              )}
              {r.routing.probe_detail && <div className="text-gray-600 mt-1">{r.routing.probe_detail}</div>}
            </>
          ), r.routing.action === 'block' ? 'bad' : r.routing.certain ? 'ok' : 'warn')}

          {/* Reachability stage */}
          {r.reachability.tested && stage(
            r.reachability.ok ? CheckCircle2 : XCircle,
            t('Reachability', 'Доступность'),
            <>
              {kv(t('result', 'результат'), r.reachability.ok
                ? <span className="text-green-600 dark:text-green-400">{t('connected', 'соединение установлено')}</span>
                : <span className="text-red-600 dark:text-red-400">{t('failed', 'не удалось')}</span>)}
              {r.reachability.http_code != null && r.reachability.http_code > 0 && kv('http', r.reachability.http_code)}
              {kv(t('via', 'через'), r.reachability.via)}
              {r.reachability.latency_ms != null && kv(t('latency', 'задержка'), `${r.reachability.latency_ms} ms`)}
              {r.reachability.detail && kv(t('detail', 'детали'), r.reachability.detail)}
            </>,
            r.reachability.ok ? 'ok' : 'bad',
          )}
        </div>
      )}
    </div>
  )
}

// ── Main Page ───────────────────────────────────────────────────────────────

export function Diagnostics() {
  const t = useT()
  return (
    <div className="p-6 space-y-4 max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-100">{t('Diagnostics', 'Диагностика')}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{t('System health, network analysis, resources & logs', 'Состояние системы, анализ сети, ресурсы и логи')}</p>
        </div>
      </div>

      <HealthChecks />
      <RouteExplainer />
      <Resources />
      <NetworkInfo />
      <DockerLogs />
    </div>
  )
}
