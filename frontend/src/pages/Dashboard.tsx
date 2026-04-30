import { useState, useRef, useEffect } from 'react'
import {
  Play, Square, RotateCw,
  ChevronDown, Globe, GitBranch, Slash, Network, ArrowUp, ArrowDown, Circle, RefreshCw,
  Server, Shield, Database, Cloud,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useSystemStatus, useSystemVersions, useStartProxy, useStopProxy, useRestartProxy, useSetMode, useSetActiveNode } from '@/hooks/useSystem'
import { useNodes } from '@/hooks/useNodes'
import { StatusBadge, ModeBadge } from '@/components/StatusBadge'
import { InfoTip } from '@/components/InfoTip'
import { MetricsCharts } from '@/components/MetricsCharts'
import { RecentEvents } from '@/components/RecentEvents'
import { systemApi, circleApi } from '@/api/client'
import { useT } from '@/hooks/useT'
import type { ProxyMode, SystemSettings, NodeCircle as NodeCircleType } from '@/types'

type InboundMode = 'tproxy' | 'tun' | 'both'

const MODES_BASE: { value: ProxyMode; label: string; icon: React.ElementType }[] = [
  { value: 'global', icon: Globe,     label: 'Global'  },
  { value: 'rules',  icon: GitBranch, label: 'Rules'   },
  { value: 'bypass', icon: Slash,     label: 'Bypass'  },
]

const INBOUND_MODES_BASE: { value: InboundMode; label: string }[] = [
  { value: 'tproxy', label: 'TPROXY' },
  { value: 'tun',    label: 'TUN'    },
  { value: 'both',   label: 'Both'   },
]

function formatUptime(seconds?: number | null): string {
  if (!seconds) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}h ${m}m`
  if (m > 0) return `${m}m ${s}s`
  return `${s}s`
}

function formatBytes(bytes?: number | null): string {
  if (!bytes) return '—'
  if (bytes > 1e9) return `${(bytes / 1e9).toFixed(1)} GB`
  if (bytes > 1e6) return `${(bytes / 1e6).toFixed(1)} MB`
  return `${(bytes / 1e3).toFixed(0)} KB`
}

// ── Service status tile — used in the Glance-inspired top-of-dashboard
// "what's the system actually doing right now" row. Status semantics:
//   ok   = green dot (subsystem healthy)
//   warn = yellow dot (degraded but not failing — e.g. some naive sidecars down)
//   err  = red dot (subsystem expected to run but isn't)
//   idle = gray dot (subsystem not in use, e.g. no naive nodes configured)
type ServiceStatus = 'ok' | 'warn' | 'err' | 'idle'

interface ServiceTile {
  icon: React.ElementType
  label: string
  status: ServiceStatus
  value: string                     // primary line ("Running", "Active", "3/4")
  sub?: string                      // secondary line (small gray)
  badge?: React.ReactNode           // optional chip rendered next to value (e.g. ModeBadge)
  actions?: React.ReactNode         // optional inline action buttons (top-right of the tile)
}

function ServiceStatusTile({ icon: Icon, label, status, value, sub, badge, actions }: ServiceTile) {
  // Status → dot color. Use Tailwind status palette which is already
  // tuned for both themes via index.css `[data-theme="light"]` overrides.
  const dotColor = {
    ok:   'bg-green-500',
    warn: 'bg-yellow-500',
    err:  'bg-red-500',
    idle: 'bg-gray-600',
  }[status]
  const valueColor = {
    ok:   'text-green-400',
    warn: 'text-yellow-300',
    err:  'text-red-400',
    idle: 'text-gray-500',
  }[status]
  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-3 relative">
      {/* Header row: icon + label on the left, optional action buttons
          on the right (top-right corner of the tile). Used by the xray
          tile to host Start / Restart / Stop without giving them their
          own row at the top of the page. */}
      <div className="flex items-center justify-between gap-2 text-xs text-gray-500">
        <div className="flex items-center gap-2 min-w-0">
          <Icon className="h-3.5 w-3.5 shrink-0" />
          <span className="uppercase tracking-wide font-medium truncate">{label}</span>
        </div>
        {actions && <div className="flex items-center gap-1 shrink-0">{actions}</div>}
      </div>
      <div className="mt-2 flex items-center gap-2">
        <span className={clsx('h-2 w-2 rounded-full shrink-0', dotColor, status === 'ok' && 'animate-pulse')} />
        <span className={clsx('text-sm font-semibold truncate', valueColor)}>{value}</span>
        {badge && <span className="shrink-0">{badge}</span>}
      </div>
      {sub && <div className="mt-0.5 text-xs text-gray-600 truncate">{sub}</div>}
    </div>
  )
}

export function Dashboard() {
  const t = useT()
  const qc = useQueryClient()
  const { data: status } = useSystemStatus()
  const { data: nodes = [] } = useNodes()
  // Versions are needed for the "GeoData freshness" tile in the Service
  // Status grid below. We pass `enabled: true` because the dashboard is
  // exactly where you want a quick "is geo data stale?" check — unlike
  // the popover which is on-demand.
  const { data: versions } = useSystemVersions({ enabled: true })
  const [showNodePicker, setShowNodePicker] = useState(false)
  const pickerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!showNodePicker) return
    const handler = (e: MouseEvent) => {
      if (pickerRef.current && !pickerRef.current.contains(e.target as HTMLElement)) {
        setShowNodePicker(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showNodePicker])

  const start = useStartProxy()
  const stop = useStopProxy()
  const restart = useRestartProxy()
  const setMode = useSetMode()
  const setActive = useSetActiveNode()

  const { data: sysSettings } = useQuery<SystemSettings>({
    queryKey: ['system', 'settings'],
    queryFn: () => systemApi.getSettings(),
  })

  const { data: trafficStats } = useQuery<Record<string, { uplink: number; downlink: number }>>({
    queryKey: ['system', 'stats'],
    queryFn: () => systemApi.getStats(),
    refetchInterval: status?.running ? 5000 : false,
    enabled: !!status?.running,
  })

  const updateSettings = useMutation({
    mutationFn: (data: Partial<SystemSettings>) => systemApi.updateSettings(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['system', 'settings'] }),
  })

  const activeNode = nodes.find((n) => n.id === status?.active_node_id)
  const onlineNodes = nodes.filter((n) => n.is_online && n.enabled)

  const MODES = MODES_BASE.map((m) => ({
    ...m,
    desc: t(
      { global: 'All traffic through proxy', rules: 'Route by configured rules', bypass: 'All traffic direct (proxy off)' }[m.value],
      { global: 'Весь трафик через прокси', rules: 'Маршрут по настроенным правилам', bypass: 'Весь трафик напрямую (прокси выкл.)' }[m.value],
    ),
  }))

  const INBOUND_MODES = INBOUND_MODES_BASE.map((m) => ({
    ...m,
    desc: t(
      { tproxy: 'nftables + dokodemo-door (default)', tun: 'Virtual TUN interface, no nftables', both: 'TPROXY + TUN simultaneously' }[m.value],
      { tproxy: 'nftables + dokodemo-door (по умолчанию)', tun: 'Виртуальный TUN, без nftables', both: 'TPROXY + TUN одновременно' }[m.value],
    ),
    tip: t(
      { tproxy: 'Recommended mode. Uses Linux kernel TPROXY + nftables to intercept packets. Devices only need to set gateway=RPi IP. No client configuration required.', tun: 'Creates a virtual tun0 interface. xray routes all traffic through it. Useful when TPROXY is unavailable or for specific compatibility scenarios. Requires xray-core ≥ 1.8.', both: '' }[m.value],
      { tproxy: 'Рекомендуемый режим. Использует TPROXY ядра Linux + nftables для перехвата пакетов. Устройствам нужно только установить gateway=RPi IP. Настройка клиента не требуется.', tun: 'Создаёт виртуальный интерфейс tun0. xray направляет через него весь трафик. Полезен когда TPROXY недоступен. Требует xray-core ≥ 1.8.', both: '' }[m.value],
    ),
  }))

  // ── Service Status grid data (Glance-inspired info-dense tiles) ────────
  // Compact at-a-glance row showing the health of each subsystem. Reads
  // existing data: `status` for xray/nftables, `nodes` for naive count,
  // `versions.data.geoip_mtime` for geo freshness. Each tile has a colored
  // dot whose color encodes the state — green (ok) / yellow (warn) /
  // red (err) / gray (n/a). All text colors go through the gray ramp so
  // light/dark themes flip automatically.
  const naiveNodes = nodes.filter((n) => n.protocol === 'naive' && n.enabled)
  const naiveOnline = naiveNodes.filter((n) => n.is_online).length
  const geoipDate = versions?.data?.geoip_mtime
    ? new Date(versions.data.geoip_mtime)
    : null
  // Age display: minute-granular for <1h, hour-granular for 1-23h,
  // day-granular for ≥24h. Russian uses abbreviations (мин./ч./дн.) so
  // we sidestep the gender/plural rules; English uses readable forms.
  // The warn threshold (>30d) still works because we compute days
  // separately.
  const geoipMinutesAgo = geoipDate
    ? Math.max(0, Math.floor((Date.now() - geoipDate.getTime()) / 60_000))
    : null
  const geoipHoursAgo = geoipMinutesAgo !== null
    ? Math.floor(geoipMinutesAgo / 60)
    : null
  const geoipDaysAgo = geoipHoursAgo !== null
    ? Math.floor(geoipHoursAgo / 24)
    : null
  const geoipAgeLabel = (() => {
    if (geoipMinutesAgo === null) return t('unknown', 'нет данных')
    if (geoipMinutesAgo === 0)    return t('just now', 'только что')
    if (geoipMinutesAgo < 60)     return t(`${geoipMinutesAgo}m ago`, `${geoipMinutesAgo} мин. назад`)
    if (geoipHoursAgo! < 24)      return t(`${geoipHoursAgo}h ago`,   `${geoipHoursAgo} ч. назад`)
    if (geoipDaysAgo === 1)       return t('1 day ago', '1 день назад')
    return t(`${geoipDaysAgo} days ago`, `${geoipDaysAgo} дн. назад`)
  })()
  // Compact icon-only lifecycle buttons for the xray tile — live in the
  // tile's top-right `actions` slot. Theme-aware via the gray ramp:
  // - hover bg uses `bg-gray-800` (auto-flips light/dark)
  // - icon tint uses semantic colors (green/red/gray) which already have
  //   `[data-theme="light"]` overrides in index.css.
  // Three states:
  //   xray stopped → show only Start (green)
  //   xray running → show Restart (neutral) + Stop (red)
  // 24×24 px buttons, no text — `title` attr gives the hover hint.
  const xrayActions = !status?.running ? (
    <button
      onClick={() => start.mutate()}
      disabled={start.isPending}
      title={t('Start xray', 'Запустить xray')}
      className="flex items-center justify-center h-6 w-6 rounded text-green-400 hover:bg-gray-800 hover:text-green-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
    >
      <Play className="h-3.5 w-3.5" />
    </button>
  ) : (
    <>
      <button
        onClick={() => restart.mutate()}
        disabled={restart.isPending}
        title={t('Restart xray', 'Перезапустить xray')}
        className="flex items-center justify-center h-6 w-6 rounded text-gray-400 hover:bg-gray-800 hover:text-gray-200 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        <RotateCw className={clsx('h-3.5 w-3.5', restart.isPending && 'animate-spin')} />
      </button>
      <button
        onClick={() => stop.mutate()}
        disabled={stop.isPending}
        title={t('Stop xray', 'Остановить xray')}
        className="flex items-center justify-center h-6 w-6 rounded text-red-400 hover:bg-gray-800 hover:text-red-300 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        <Square className="h-3.5 w-3.5" />
      </button>
    </>
  )

  const services: ServiceTile[] = [
    {
      icon: Server,
      label: 'xray',
      status: status?.running ? 'ok' : 'err',
      value: status?.running ? 'Running' : 'Stopped',
      sub: status?.running
        ? `Uptime ${formatUptime(status.uptime_seconds)} · PID ${status.pid}`
        : '—',
      // Mode badge (Rules / Global / Bypass) shown as a chip next to
      // the value when xray is up — used to live in the old standalone
      // status card; now integrated into the at-a-glance tile.
      badge: status?.running && status.mode ? <ModeBadge mode={status.mode} /> : undefined,
      actions: xrayActions,
    },
    {
      icon: Shield,
      label: 'nftables',
      status: status?.nftables_active ? 'ok' : (status?.mode === 'bypass' ? 'idle' : 'err'),
      value: status?.nftables_active ? 'Active' : 'Inactive',
      sub: status?.mode === 'bypass'
        ? t('bypass mode', 'режим bypass')
        : sysSettings?.inbound_mode?.toUpperCase() ?? 'TPROXY',
    },
    {
      icon: Cloud,
      label: 'NaiveProxy',
      status: naiveNodes.length === 0
        ? 'idle'
        : naiveOnline === naiveNodes.length
          ? 'ok'
          : naiveOnline === 0 ? 'err' : 'warn',
      value: naiveNodes.length === 0
        ? t('no nodes', 'нет нод')
        : `${naiveOnline}/${naiveNodes.length}`,
      sub: naiveNodes.length === 0
        ? '—'
        : t('sidecars online', 'sidecars online'),
    },
    {
      icon: Database,
      label: 'GeoData',
      status: geoipDaysAgo === null
        ? 'idle'
        : geoipDaysAgo > 30 ? 'warn' : 'ok',
      value: geoipAgeLabel,
      sub: 'geoip.dat',
    },
  ]

  return (
    <div className="p-6 space-y-6">
      {/* Page header: title + xray version stamp. Lifecycle controls
          (Start / Restart / Stop) live INSIDE the xray tile of the
          Service Status grid below — they're a property of that
          subsystem, not the whole dashboard. */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-xl font-bold text-gray-100">Dashboard</h1>
        {status?.version && (
          <span className="text-xs text-gray-600 font-mono">xray {status.version}</span>
        )}
      </div>

      {/* Service Status grid — at-a-glance health row across subsystems.
          Compact: each tile takes ~25% on desktop, 50% on mobile. */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {services.map((s) => <ServiceStatusTile key={s.label} {...s} />)}
      </div>

      {/* Mode switcher */}
      <div>
        <h2 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-1">
          Proxy Mode
          <InfoTip className="ml-1" text={t(
            'Controls how traffic is routed. Global — all traffic through the proxy regardless of rules. Rules — route based on configured routing rules (by IP, domain, MAC, etc.). Bypass — proxy is inactive, all traffic goes direct.',
            'Управляет маршрутизацией трафика. Global — весь трафик через прокси вне зависимости от правил. Rules — маршрут по настроенным правилам (IP, домен, MAC и т.д.). Bypass — прокси отключён, весь трафик идёт напрямую.',
          )} />
        </h2>
        <div className="grid grid-cols-3 gap-3">
          {MODES.map(({ value, label, icon: Icon, desc }) => (
            <button
              key={value}
              onClick={() => setMode.mutate(value)}
              disabled={setMode.isPending}
              className={clsx(
                'rounded-xl border p-4 text-left transition-all',
                status?.mode === value
                  ? 'border-brand-600 bg-brand-900/20 text-brand-300'
                  : 'border-gray-800 bg-gray-900/30 text-gray-400 hover:border-gray-700 hover:text-gray-200',
              )}
            >
              <Icon className="h-5 w-5 mb-2" />
              <div className="text-sm font-medium">{label}</div>
              <div className="text-xs opacity-70 mt-0.5">{desc}</div>
            </button>
          ))}
        </div>
      </div>

      {/* Network (Inbound) Mode */}
      <div>
        <h2 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-2">
          <Network className="h-4 w-4" />
          Network Mode
          <InfoTip className="ml-1" text={t(
            'How xray intercepts traffic. TPROXY — kernel-level transparent proxy via nftables, devices set gateway=RPi. TUN — virtual network interface tun0, xray handles routing internally. Both — run TPROXY and TUN simultaneously (rarely needed).',
            'Способ перехвата трафика xray. TPROXY — прозрачный прокси уровня ядра через nftables, устройства ставят gateway=RPi. TUN — виртуальный интерфейс tun0, xray управляет маршрутизацией внутри. Both — TPROXY и TUN одновременно (редко нужно).',
          )} />
        </h2>
        <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-4 space-y-3">
          <div className="grid grid-cols-3 gap-2">
            {INBOUND_MODES.map(({ value, label, desc, tip }) => (
              <button
                key={value}
                onClick={() => updateSettings.mutate({ inbound_mode: value })}
                disabled={updateSettings.isPending}
                className={clsx(
                  'rounded-lg border p-3 text-left text-xs transition-all',
                  sysSettings?.inbound_mode === value
                    ? 'border-brand-600 bg-brand-900/20 text-brand-300'
                    : 'border-gray-700 bg-gray-800 text-gray-400 hover:border-gray-600 hover:text-gray-200',
                )}
              >
                <div className="font-medium text-sm mb-0.5 flex items-center gap-1">
                  {label}
                  {tip && <InfoTip className="ml-0.5" text={tip} />}
                </div>
                <div className="opacity-70">{desc}</div>
              </button>
            ))}
          </div>

          {/* QUIC block toggle */}
          {(sysSettings?.inbound_mode === 'tproxy' || sysSettings?.inbound_mode === 'both') && (
            <label className="flex items-center gap-2 cursor-pointer pt-1">
              <input
                type="checkbox"
                checked={sysSettings?.block_quic ?? true}
                onChange={(e) => updateSettings.mutate({ block_quic: e.target.checked })}
                className="rounded border-gray-600 bg-gray-700 text-brand-600"
              />
              <span className="text-xs text-gray-300 flex items-center gap-1">
                Block QUIC (UDP/443)
                <InfoTip className="ml-0.5" text={t(
                  'QUIC (HTTP/3) is UDP-based and incompatible with TPROXY — connections break due to IP path changes. Blocking UDP/443 forces browsers to fall back to TCP/443 (HTTP/2) which TPROXY handles correctly. QUIC to bypassed destinations (direct rules) is NOT affected.',
                  'QUIC (HTTP/3) основан на UDP и несовместим с TPROXY — соединения рвутся из-за изменений IP-пути. Блокировка UDP/443 заставляет браузеры переходить на TCP/443 (HTTP/2), который TPROXY обрабатывает корректно. QUIC для прямых (direct) назначений не затрагивается.',
                )} />
              </span>
              <span className="text-xs text-gray-600">{t('— forces TCP fallback, fixes TPROXY compatibility', '— форсирует TCP, фиксит совместимость с TPROXY')}</span>
            </label>
          )}

          {/* Kill switch toggle */}
          <label className="flex items-center gap-2 cursor-pointer pt-1">
            <input
              type="checkbox"
              checked={sysSettings?.kill_switch ?? false}
              onChange={(e) => updateSettings.mutate({ kill_switch: e.target.checked })}
              className="rounded border-gray-600 bg-gray-700 text-red-600"
            />
            <span className="text-xs text-gray-300 flex items-center gap-1">
              Kill Switch
              <InfoTip className="ml-0.5" text={t(
                'When enabled, if xray stops or crashes, ALL internet traffic is blocked instead of leaking direct. LAN stays accessible. VPN server IPs are whitelisted so xray can reconnect. Disable to allow direct internet when proxy is off.',
                'При включении, если xray остановится или упадёт, ВЕСЬ интернет-трафик блокируется вместо утечки напрямую. LAN остаётся доступным. IP VPN-серверов в белом списке для переподключения xray. Отключите чтобы разрешить прямой интернет при выключенном прокси.',
              )} />
            </span>
            <span className="text-xs text-gray-600">{t('— block internet if proxy drops', '— блокировать интернет при падении прокси')}</span>
          </label>

          {/* Auto restart toggle */}
          <label className="flex items-center gap-2 cursor-pointer pt-1">
            <input
              type="checkbox"
              checked={sysSettings?.auto_restart_xray ?? true}
              onChange={(e) => updateSettings.mutate({ auto_restart_xray: e.target.checked })}
              className="rounded border-gray-600 bg-gray-700 text-green-600"
            />
            <span className="text-xs text-gray-300 flex items-center gap-1">
              Auto Restart
              <InfoTip className="ml-0.5" text={t(
                'Automatically restart xray if it crashes. Config is verified before restart — invalid configs won\'t cause infinite restart loops. Also starts xray automatically when the container boots.',
                'Автоматически перезапускать xray при падении. Конфиг проверяется перед перезапуском — невалидный конфиг не вызовет бесконечный цикл. Также запускает xray автоматически при старте контейнера.',
              )} />
            </span>
            <span className="text-xs text-gray-600">{t('— recover from crashes, start on boot', '— восстановление после сбоев, автозапуск при старте')}</span>
          </label>

          {sysSettings?.inbound_mode === 'tun' || sysSettings?.inbound_mode === 'both' ? (
            <div className="rounded-lg bg-yellow-900/20 border border-yellow-700/40 px-3 py-2 text-xs text-yellow-300">
              {t('TUN mode requires xray-core ≥ 1.8 with TUN support compiled in.', 'Режим TUN требует xray-core ≥ 1.8 с поддержкой TUN.')}
            </div>
          ) : null}

          {(sysSettings?.inbound_mode === 'tun' || sysSettings?.inbound_mode === 'both') && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 pt-1">
              <div>
                <label className="block text-xs text-gray-500 mb-1">TUN Address</label>
                <input
                  key={sysSettings?.tun_address}
                  className="w-full rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-100 font-mono focus:border-brand-500 focus:outline-none"
                  defaultValue={sysSettings?.tun_address ?? '10.0.0.1/30'}
                  onBlur={(e) => updateSettings.mutate({ tun_address: e.target.value })}
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">MTU</label>
                <input
                  key={sysSettings?.tun_mtu}
                  type="number"
                  className="w-full rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-100 font-mono focus:border-brand-500 focus:outline-none"
                  defaultValue={sysSettings?.tun_mtu ?? 9000}
                  onBlur={(e) => updateSettings.mutate({ tun_mtu: parseInt(e.target.value) || 9000 })}
                />
              </div>
              <div>
                <label className="flex items-center gap-1 text-xs text-gray-500 mb-1">
                  Stack
                  <InfoTip className="ml-0.5" text={t(
                    "Network stack implementation for TUN. 'system' uses the OS network stack (most compatible). 'gvisor' is a userspace stack (better isolation, higher CPU). 'mixed' combines both.",
                    "Реализация сетевого стека для TUN. 'system' — сетевой стек ОС (максимальная совместимость). 'gvisor' — стек пользовательского пространства (лучшая изоляция, выше нагрузка CPU). 'mixed' — комбинация обоих.",
                  )} />
                </label>
                <select
                  className="w-full rounded bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-100 focus:border-brand-500 focus:outline-none"
                  value={sysSettings?.tun_stack ?? 'system'}
                  onChange={(e) => updateSettings.mutate({ tun_stack: e.target.value })}
                >
                  <option value="system">system</option>
                  <option value="gvisor">gvisor</option>
                  <option value="mixed">mixed</option>
                </select>
              </div>
              <div className="flex flex-col justify-end gap-1.5">
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={sysSettings?.tun_auto_route ?? true}
                    onChange={(e) => updateSettings.mutate({ tun_auto_route: e.target.checked })}
                    className="rounded border-gray-600 bg-gray-700 text-brand-600"
                  />
                  <span className="text-xs text-gray-400 flex items-center gap-1">
                    Auto Route
                    <InfoTip className="ml-0.5" text={t(
                      'xray automatically adds system routing rules to redirect traffic through tun0. Disable only if you want to set up routing manually.',
                      'xray автоматически добавляет системные правила маршрутизации для перенаправления трафика через tun0. Отключайте только если хотите настроить маршрутизацию вручную.',
                    )} />
                  </span>
                </label>
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="checkbox"
                    checked={sysSettings?.tun_strict_route ?? true}
                    onChange={(e) => updateSettings.mutate({ tun_strict_route: e.target.checked })}
                    className="rounded border-gray-600 bg-gray-700 text-brand-600"
                  />
                  <span className="text-xs text-gray-400 flex items-center gap-1">
                    Strict Route
                    <InfoTip className="ml-0.5" text={t(
                      'Prevents traffic leaks outside the tunnel by making routing rules strict. Recommended to keep enabled for security.',
                      'Предотвращает утечки трафика вне туннеля за счёт строгих правил маршрутизации. Рекомендуется держать включённым.',
                    )} />
                  </span>
                </label>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Active node / NodeCircle selector */}
      <ActiveNodeSection
        nodes={nodes}
        status={status}
        activeNode={activeNode}
        showNodePicker={showNodePicker}
        setShowNodePicker={setShowNodePicker}
        setActive={setActive}
        pickerRef={pickerRef}
      />

      {/* Proxy endpoints */}
      {sysSettings && (
        <div>
          <h2 className="text-sm font-medium text-gray-400 mb-3 flex items-center gap-1">
            Proxy Endpoints
            <InfoTip className="ml-1" text={t(
              'Three ways to use the proxy simultaneously. TPROXY — transparent (change device gateway). SOCKS5 — configure in browser/app settings (host: RPi IP). HTTP — for apps that only support HTTP proxy. All three share the same routing rules.',
              'Три способа использовать прокси одновременно. TPROXY — прозрачный (измените gateway устройства). SOCKS5 — настройте в браузере/приложении (host: IP RPi). HTTP — для приложений только с HTTP-прокси. Все три используют одинаковые правила маршрутизации.',
            )} />
          </h2>
          <div className="grid grid-cols-3 gap-3">
            {[
              { label: 'TPROXY', port: sysSettings.tproxy_port_tcp, desc: t('gateway=RPi (transparent)', 'gateway=RPi (прозрачный)'), color: 'text-green-400' },
              { label: 'SOCKS5', port: sysSettings.socks_port,       desc: t('explicit proxy (LAN)', 'явный прокси (LAN)'),          color: 'text-blue-400' },
              { label: 'HTTP',   port: sysSettings.http_port,         desc: t('HTTP proxy (LAN)', 'HTTP-прокси (LAN)'),               color: 'text-purple-400' },
            ].map(({ label, port, desc, color }) => (
              <div key={label} className="rounded-xl border border-gray-800 bg-gray-900/30 p-3">
                <div className={`text-xs font-semibold ${color} mb-1`}>{label}</div>
                <div className="font-mono text-gray-100 text-sm">:{port}</div>
                <div className="text-xs text-gray-600 mt-0.5">{desc}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Traffic stats */}
      {trafficStats && Object.keys(trafficStats).length > 0 && (
        <div>
          <h2 className="text-sm font-medium text-gray-400 mb-3">Traffic</h2>
          <div className="rounded-xl border border-gray-800 bg-gray-900/30 divide-y divide-gray-800">
            {Object.entries(trafficStats)
              .filter(([, v]) => v.uplink > 0 || v.downlink > 0)
              .sort(([, a], [, b]) => (b.uplink + b.downlink) - (a.uplink + a.downlink))
              .map(([tag, v]) => {
                const node = nodes.find((n) => `node-${n.id}` === tag)
                const label = node ? node.name : tag
                return (
                  <div key={tag} className="flex items-center justify-between px-4 py-2.5 text-sm">
                    <span className="text-gray-300 truncate max-w-[200px]">{label}</span>
                    <div className="flex items-center gap-4 text-xs font-mono shrink-0">
                      <span className="flex items-center gap-1 text-green-400">
                        <ArrowDown className="h-3 w-3" />
                        {formatBytes(v.downlink)}
                      </span>
                      <span className="flex items-center gap-1 text-blue-400">
                        <ArrowUp className="h-3 w-3" />
                        {formatBytes(v.uplink)}
                      </span>
                    </div>
                  </div>
                )
              })}
          </div>
        </div>
      )}

      {/* System metrics charts */}
      <MetricsCharts />

      {/* Recent Events feed — failover, sidecar, geo, circle rotations */}
      <RecentEvents limit={8} />

      {/* Quick stats */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        {[
          { label: 'Total Nodes',  value: nodes.length },
          { label: 'Online',       value: onlineNodes.length },
          { label: 'Offline',      value: nodes.filter(n => !n.is_online).length },
          { label: 'Groups',       value: new Set(nodes.map(n => n.group).filter(Boolean)).size },
        ].map(({ label, value }) => (
          <div key={label} className="rounded-xl border border-gray-800 bg-gray-900/30 p-4">
            <div className="text-2xl font-bold text-gray-100">{value}</div>
            <div className="text-xs text-gray-500 mt-1">{label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}


// ── Active Node / NodeCircle section ──────────────────────────────────────────

function ActiveNodeSection({ nodes, status, activeNode, showNodePicker, setShowNodePicker, setActive, pickerRef }: any) {
  const t = useT()
  const qc = useQueryClient()
  const [tab, setTab] = useState<'node' | 'circle'>('node')

  const { data: circles = [] } = useQuery<NodeCircleType[]>({
    queryKey: ['nodecircle'],
    queryFn: () => circleApi.list(),
  })

  const enableCircle = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      circleApi.update(id, { enabled }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['nodecircle'] })
      qc.invalidateQueries({ queryKey: ['system'] })
    },
  })

  const rotateNow = useMutation({
    mutationFn: (id: number) => circleApi.rotate(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['nodecircle'] })
      qc.invalidateQueries({ queryKey: ['system'] })
    },
  })

  const activeCircle = circles.find(c => c.enabled)
  const activeCircleId = activeCircle?.id
  const [hasAutoSwitched, setHasAutoSwitched] = useState(false)
  useEffect(() => {
    if (activeCircleId && !hasAutoSwitched) {
      setTab('circle')
      setHasAutoSwitched(true)
    }
  }, [activeCircleId, hasAutoSwitched])

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-gray-400 flex items-center gap-1">
          Active Proxy
          <InfoTip className="ml-1" text={t(
            'Choose between a single fixed node or a NodeCircle that automatically rotates nodes on a timer for anti-DPI protection.',
            'Выбор между одной фиксированной нодой или NodeCircle, который автоматически ротирует ноды по таймеру для защиты от DPI.',
          )} />
        </h2>
        <div className="flex rounded-lg overflow-hidden border border-gray-700">
          <button
            onClick={() => setTab('node')}
            className={clsx(
              'px-3 py-1 text-xs font-medium transition-colors',
              tab === 'node' ? 'bg-brand-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200',
            )}
          >
            Single Node
          </button>
          <button
            onClick={() => { setTab('circle'); setShowNodePicker(false) }}
            className={clsx(
              'px-3 py-1 text-xs font-medium transition-colors',
              tab === 'circle' ? 'bg-brand-600 text-white' : 'bg-gray-800 text-gray-400 hover:text-gray-200',
            )}
          >
            NodeCircle
          </button>
        </div>
      </div>

      {tab === 'node' ? (
        <div ref={pickerRef} className="rounded-xl border border-gray-800 bg-gray-900/30 p-4">
          {activeCircle && (
            <div className="mb-3 rounded-lg bg-yellow-900/20 border border-yellow-700/40 px-3 py-2 text-xs text-yellow-300">
              NodeCircle "{activeCircle.name}" is active — it will override this selection on next rotation.
              <button
                onClick={() => enableCircle.mutate({ id: activeCircle.id, enabled: false })}
                className="ml-2 underline hover:no-underline"
              >
                Disable circle
              </button>
            </div>
          )}
          {activeNode ? (
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium text-gray-100">{activeNode.name}</div>
                <div className="text-xs text-gray-500 font-mono mt-0.5">
                  {activeNode.address}:{activeNode.port}
                </div>
                <StatusBadge
                  online={activeNode.is_online}
                  latency={activeNode.latency_ms ?? undefined}
                  className="mt-2"
                />
              </div>
              <button
                onClick={() => setShowNodePicker((v: boolean) => !v)}
                className="flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700 transition-colors"
              >
                Switch
                <ChevronDown className={clsx('h-4 w-4 transition-transform', showNodePicker && 'rotate-180')} />
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between text-sm text-gray-500">
              <span>No active node selected</span>
              <button
                onClick={() => setShowNodePicker((v: boolean) => !v)}
                className="flex items-center gap-1 rounded-lg bg-brand-600 px-3 py-1.5 text-sm text-white hover:bg-brand-500 transition-colors"
              >
                Select node
                <ChevronDown className="h-4 w-4" />
              </button>
            </div>
          )}

          {showNodePicker && (
            <div className="mt-3 border-t border-gray-800 pt-3 space-y-1 max-h-60 overflow-y-auto">
              {nodes.filter((n: any) => n.enabled).map((n: any) => (
                <button
                  key={n.id}
                  onClick={() => {
                    setActive.mutate(n.id)
                    setShowNodePicker(false)
                  }}
                  className={clsx(
                    'w-full flex items-center justify-between rounded-lg px-3 py-2 text-sm transition-colors',
                    n.id === status?.active_node_id
                      ? 'bg-brand-900/30 text-brand-300'
                      : 'hover:bg-gray-800 text-gray-300',
                  )}
                >
                  <span className="truncate">{n.name}</span>
                  <StatusBadge online={n.is_online} latency={n.latency_ms ?? undefined} />
                </button>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-4 space-y-3">
          {circles.length === 0 ? (
            <div className="text-center py-4 text-sm text-gray-500">
              No NodeCircles configured. <a href="/circles" className="text-brand-400 hover:underline">Create one →</a>
            </div>
          ) : (
            circles.map((c: NodeCircleType) => (
              <div
                key={c.id}
                className={clsx(
                  'rounded-lg border p-3 transition-colors',
                  c.enabled ? 'border-brand-600/50 bg-brand-900/10' : 'border-gray-800 bg-gray-900/50',
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Circle className={clsx('h-4 w-4', c.enabled ? 'text-brand-400' : 'text-gray-600')} />
                    <span className="text-sm font-medium text-gray-100">{c.name}</span>
                    <span className="text-xs text-gray-500">
                      {c.node_ids.length} nodes · {c.mode} · {c.interval_min === c.interval_max ? `${c.interval_min}m` : `${c.interval_min}–${c.interval_max}m`}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => rotateNow.mutate(c.id)}
                      disabled={!c.enabled || c.node_ids.length < 2 || (rotateNow.isPending && rotateNow.variables === c.id)}
                      title="Rotate now"
                      className="rounded p-1.5 text-gray-500 hover:text-brand-400 hover:bg-gray-800 transition-colors disabled:opacity-30"
                    >
                      <RefreshCw className={clsx('h-3.5 w-3.5', rotateNow.isPending && rotateNow.variables === c.id && 'animate-spin')} />
                    </button>
                    <button
                      onClick={() => enableCircle.mutate({ id: c.id, enabled: !c.enabled })}
                      className={clsx(
                        'rounded-lg px-3 py-1 text-xs font-medium transition-colors',
                        c.enabled
                          ? 'bg-brand-600 text-white hover:bg-brand-500'
                          : 'bg-gray-800 text-gray-400 hover:bg-gray-700',
                      )}
                    >
                      {c.enabled ? 'Active' : 'Enable'}
                    </button>
                  </div>
                </div>
                {c.enabled && c.current_node_name && (
                  <div className="mt-2 text-xs text-gray-400">
                    Current: <span className="text-brand-300 font-medium">{c.current_node_name}</span>
                    {c.last_rotated && (
                      <span className="ml-2 text-gray-600">
                        · rotated {new Date(c.last_rotated.endsWith?.('Z') ? c.last_rotated : c.last_rotated + 'Z').toLocaleTimeString()}
                      </span>
                    )}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      )}
    </div>
  )
}
