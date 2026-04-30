import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { systemApi } from '@/api/client'
import {
  Settings as SettingsIcon,
  Network,
  Cable,
  Shield,
  Activity,
  Save,
  RefreshCw,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Database,
  Clock,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useT } from '@/hooks/useT'
import { useConfirm } from '@/components/ConfirmModal'
import type { SystemSettings } from '@/types'

// Dynamic key-value form that mirrors `SystemSettings` but preserves
// string-indexed access. The union gives autocomplete for known keys
// while keeping the escape hatch for ad-hoc settings (e.g. TUN or health
// knobs we haven't yet exposed in the canonical type). Using `unknown`
// instead of the old `any` so call sites that actually store a typed
// value have to narrow explicitly.
type SettingValue = string | number | boolean | undefined
type PartialSettings = Partial<Record<keyof SystemSettings, SettingValue>> & Record<string, SettingValue>

const INT_FIELDS = [
  'tproxy_port_tcp', 'tproxy_port_udp', 'socks_port', 'http_port', 'dns_port',
  'health_interval', 'health_timeout', 'health_full_check_interval',
  'geo_update_interval_days', 'geo_update_window_start', 'geo_update_window_end',
]

// Common IANA timezones — short list for the select. The user can edit
// `timezone` directly in the DB if they need something exotic; this
// covers the 99% case (RU/EU/US/Asia majors). Default UTC stays at the
// top of the list as the implicit "no preference" fallback.
const TIMEZONES = [
  'UTC',
  'Europe/Moscow', 'Europe/London', 'Europe/Berlin', 'Europe/Paris',
  'Europe/Kyiv', 'Europe/Istanbul',
  'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
  'Asia/Tbilisi', 'Asia/Almaty', 'Asia/Tashkent', 'Asia/Bangkok',
  'Asia/Shanghai', 'Asia/Tokyo', 'Asia/Seoul', 'Asia/Hong_Kong',
  'Asia/Dubai', 'Asia/Singapore',
  'Australia/Sydney', 'Pacific/Auckland',
]

export function Settings() {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: systemApi.getSettings,
    staleTime: 60_000,
  })

  const [draft, setDraft] = useState<PartialSettings>({})
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  const mutation = useMutation({
    // `updateSettings` accepts Partial<SystemSettings>, but our form state
    // holds ad-hoc string keys (with string input values) — they're
    // coerced in handleSave. Cast at the boundary.
    mutationFn: (patch: PartialSettings) =>
      systemApi.updateSettings(patch as Partial<SystemSettings>),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] })
      setSaved(true)
      setError('')
      setDraft({})
      setTimeout(() => setSaved(false), 2000)
    },
    onError: (err: any) => {
      setError(err?.response?.data?.detail || err?.message || 'Failed to save settings')
    },
  })

  const val = (key: string) => {
    if (draft[key] !== undefined) return draft[key]
    const v = (settings as any)?.[key]
    return v ?? ''
  }

  const isChecked = (key: string): boolean => {
    const v = val(key)
    if (typeof v === 'boolean') return v
    if (typeof v === 'string') return v.toLowerCase() === 'true'
    return Boolean(v)
  }

  const set = (key: string, value: any) =>
    setDraft((d) => ({ ...d, [key]: value }))

  const hasChanges = Object.keys(draft).length > 0

  const handleSave = () => {
    if (!hasChanges) return
    setError('')
    const patch: PartialSettings = {}
    for (const [k, v] of Object.entries(draft)) {
      if (INT_FIELDS.includes(k)) {
        const n = parseInt(String(v ?? ''))
        if (isNaN(n) || n < 1) {
          setError(`Invalid value for ${k}: ${v}`)
          return
        }
        patch[k] = n
      } else {
        patch[k] = v
      }
    }
    mutation.mutate(patch)
  }

  if (isLoading) return (
    <div className="p-6">
      <div className="h-8 w-48 rounded bg-gray-800 animate-pulse mb-4" />
      <div className="space-y-4">
        {[1, 2, 3].map(i => <div key={i} className="h-40 rounded-xl bg-gray-800/50 animate-pulse" />)}
      </div>
    </div>
  )

  // ── Input helpers ──────────────────────────────────────────────────────
  const textInput = (key: string, placeholder?: string, hint?: string) => (
    <div>
      <input
        type="text"
        value={val(key)}
        onChange={(e) => set(key, e.target.value)}
        placeholder={placeholder}
        className={clsx(
          'w-full rounded-lg bg-gray-950 border px-3 py-2 text-sm text-gray-100 focus:outline-none transition-colors',
          draft[key] !== undefined ? 'border-brand-500' : 'border-gray-800 focus:border-gray-600',
        )}
      />
      {hint && <p className="text-[11px] text-gray-600 mt-1">{hint}</p>}
    </div>
  )

  const numInput = (key: string, min?: number, max?: number, hint?: string) => (
    <div>
      <input
        type="number"
        value={val(key)}
        onChange={(e) => set(key, e.target.value)}
        min={min}
        max={max}
        className={clsx(
          'w-full rounded-lg bg-gray-950 border px-3 py-2 text-sm text-gray-100 focus:outline-none transition-colors',
          draft[key] !== undefined ? 'border-brand-500' : 'border-gray-800 focus:border-gray-600',
        )}
      />
      {hint && <p className="text-[11px] text-gray-600 mt-1">{hint}</p>}
    </div>
  )

  const toggle = (key: string, label: string, hint?: string, confirmOff?: string) => {
    const on = isChecked(key)
    return (
      <label className="flex items-center justify-between rounded-lg bg-gray-800/50 px-4 py-3 cursor-pointer hover:bg-gray-800 transition-colors">
        <div>
          <div className="text-sm text-gray-200">{label}</div>
          {hint && <div className="text-[11px] text-gray-500 mt-0.5">{hint}</div>}
        </div>
        <div className="relative">
          <input
            type="checkbox"
            className="sr-only"
            checked={on}
            onChange={async (e) => {
              const newValue = e.target.checked
              // Async confirm path. The input is `checked={on}` (controlled),
              // so if we don't call set() the React render keeps the old
              // value — no visual flicker. The await holds the function
              // open until the user picks Cancel/Confirm.
              if (confirmOff && on && !newValue) {
                const ok = await confirm({
                  title: t('Are you sure?', 'Точно?'),
                  body: confirmOff,
                  confirmLabel: t('Disable', 'Отключить'),
                  danger: true,
                })
                if (!ok) return
              }
              set(key, newValue)
            }}
          />
          <div className={clsx('w-10 h-5 rounded-full transition-colors', on ? 'bg-brand-600' : 'bg-gray-700')} />
          <div className={clsx('absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform', on && 'translate-x-5')} />
        </div>
      </label>
    )
  }

  const field = (label: string, children: React.ReactNode) => (
    <div>
      <label className="block text-xs text-gray-500 mb-1.5">{label}</label>
      {children}
    </div>
  )

  const section = (icon: typeof Network, title: string, desc: string, children: React.ReactNode) => (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50">
      <div className="px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          {(() => { const I = icon; return <I className="h-4 w-4 text-brand-400" /> })()}
          {title}
        </h2>
        <p className="text-[11px] text-gray-500 mt-0.5">{desc}</p>
      </div>
      <div className="p-4">{children}</div>
    </div>
  )

  return (
    <div className="p-6 space-y-4 max-w-5xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-100">Settings</h1>
          <p className="text-sm text-gray-500 mt-0.5">Network, ports, safety and health check configuration</p>
        </div>
        <div className="flex items-center gap-2">
          {saved && (
            <span className="flex items-center gap-1 text-xs text-green-400">
              <CheckCircle2 className="h-3.5 w-3.5" /> Saved
            </span>
          )}
          <button
            onClick={handleSave}
            disabled={!hasChanges || mutation.isPending}
            className={clsx(
              'flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors',
              hasChanges
                ? 'bg-brand-600 text-white hover:bg-brand-500'
                : 'bg-gray-800 text-gray-500 cursor-not-allowed',
            )}
          >
            {mutation.isPending
              ? <RefreshCw className="h-4 w-4 animate-spin" />
              : <Save className="h-4 w-4" />
            }
            Save
          </button>
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-red-950/30 border border-red-900/50 px-4 py-2.5 text-xs text-red-300">
          <XCircle className="h-4 w-4 text-red-400 flex-shrink-0" />
          {error}
        </div>
      )}

      {/* Restart warning */}
      {hasChanges && (
        <div className="flex items-center gap-2 rounded-lg bg-yellow-950/30 border border-yellow-900/50 px-4 py-2.5 text-xs text-yellow-300">
          <AlertTriangle className="h-4 w-4 text-yellow-400 flex-shrink-0" />
          Changes to network and ports require xray restart to take effect.
        </div>
      )}

      {/* Network */}
      {section(Network, 'Network', t('Interface, gateway and LAN configuration', 'Интерфейс, шлюз и настройки LAN'), (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
          {field('Interface', textInput('interface', 'eth0', t('Network interface for transparent proxy', 'Сетевой интерфейс для прозрачного прокси')))}
          {field('PiTun IP', (
            <div>
              <input
                type="text"
                value={val('gateway_ip')}
                readOnly
                className="w-full rounded-lg bg-gray-950/60 border border-gray-800 px-3 py-2 text-sm text-gray-400 cursor-not-allowed"
              />
              <p className="text-[11px] text-gray-600 mt-1">{t('Auto-detected from interface — used as gateway by clients', 'Определяется автоматически — используется клиентами как шлюз')}</p>
            </div>
          ))}
          {field('LAN CIDR', textInput('lan_cidr', '192.168.1.0/24', t('Local network subnet', 'Подсеть локальной сети')))}
          {field('Router IP', textInput('router_ip', '192.168.1.1', t('Main router (auto-detected if empty)', 'Основной роутер (автоопределение если пусто)')))}
        </div>
      ))}

      {/* Ports */}
      {section(Cable, 'Service Ports', t('Proxy and DNS listening ports', 'Порты прослушивания прокси и DNS'), (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-4">
          {field('TPROXY TCP', numInput('tproxy_port_tcp', 1024, 65535))}
          {field('TPROXY UDP', numInput('tproxy_port_udp', 1024, 65535))}
          {field('SOCKS5', numInput('socks_port', 1024, 65535))}
          {field('HTTP Proxy', numInput('http_port', 1024, 65535))}
          {field('DNS', numInput('dns_port', 1024, 65535))}
        </div>
      ))}

      {/* Safety */}
      {section(Shield, 'Safety', t('Traffic protection and auto-recovery', 'Защита трафика и автовосстановление'), (
        <div className="space-y-2">
          {toggle('kill_switch', 'Kill Switch', t('Block all traffic if proxy goes down', 'Блокировать весь трафик при падении прокси'))}
          {toggle('auto_restart_xray', 'Auto Restart', t('Automatically restart xray on crash', 'Автоматически перезапускать xray при падении'))}
          {toggle('block_quic', 'Block QUIC', t('Block UDP/443 for better TPROXY compatibility', 'Блокировать UDP/443 для совместимости с TPROXY'))}
          {toggle('bypass_private', 'Bypass Private IPs', t('Skip proxying for 10.x, 192.168.x, etc.', 'Не проксировать трафик 10.x, 192.168.x и т.д.'), t('LAN traffic (including access to this device) will go through VPN.\nYou may lose access to the web panel!\n\nDisable?', 'LAN-трафик (включая доступ к панели) пойдёт через VPN.\nВы можете потерять доступ к веб-панели!\n\nОтключить?'))}
          {toggle('disable_ipv6', 'Disable IPv6', t('Disable IPv6 on system level (sysctl)', 'Отключить IPv6 на уровне системы (sysctl)'))}
          {toggle('dns_over_tcp', 'DNS over TCP', t('Use TCP for DNS queries (fixes networks where UDP:53 is blocked)', 'Использовать TCP для DNS (исправляет сети где UDP:53 заблокирован)'))}
        </div>
      ))}

      {/* Health Check */}
      {section(Activity, 'Health Check',
        t('Active node is checked every "Check Interval"; the full sweep below probes every other enabled node periodically so non-active nodes don\'t drift to a stale "online" state.',
          'Активная нода проверяется каждый "Check Interval"; "Full check interval" определяет как часто проверяются все остальные включённые ноды — чтобы они не висели в "online" если упали.'), (
        <div className="grid grid-cols-3 gap-4">
          {field('Check Interval (sec)', numInput('health_interval', 10, 600, t('Active-node probe cadence', 'Частота проверки активной ноды')))}
          {field('Timeout (sec)', numInput('health_timeout', 1, 30, t('Per-probe TCP timeout', 'Таймаут TCP-проверки')))}
          {field('Full check interval (sec)', numInput('health_full_check_interval', 0, 3600, t('Probe ALL enabled nodes every N seconds (default 300 = 5 min). Set 0 to disable — use Test All on Nodes page for manual checks.', 'Проверять ВСЕ включённые ноды каждые N секунд (по умолчанию 300 = 5 мин). 0 — отключить, ручная проверка через кнопку Test All на странице Nodes.')))}
        </div>
      ))}

      {/* GeoData auto-update */}
      {section(Database, 'GeoData Auto-Update',
        t('Daily refresh of geoip.dat / geosite.dat / GeoLite2.mmdb. Runs in the off-peak window below (timezone-aware). Reloads xray after a successful download so new ranges take effect immediately.',
          'Ежедневное обновление geoip.dat / geosite.dat / GeoLite2.mmdb. Запускается в окне ниже (с учётом часового пояса). После успешной загрузки xray перезагружается чтобы новые диапазоны сразу применились.'), (
        <div className="space-y-3">
          {toggle('geo_auto_update', 'Auto-update enabled',
            t('Turn off to manage geo data manually via the GeoData page',
              'Выключи если хочешь обновлять вручную на странице GeoData'))}
          {/* `<fieldset disabled>` cascades the disabled state to every
              <input> inside, so when the toggle is off the user can't
              edit interval/window — they wouldn't take effect anyway.
              opacity-50 + cursor-not-allowed give the visual cue. */}
          <fieldset
            disabled={!isChecked('geo_auto_update')}
            className={clsx(
              'grid grid-cols-3 gap-4 transition-opacity',
              !isChecked('geo_auto_update') && 'opacity-50 cursor-not-allowed',
            )}
          >
            {field('Interval (days)', numInput('geo_update_interval_days', 1, 30,
              t('Update only if local file is older than N days', 'Обновлять только если файл старше N дней')))}
            {field('Window start (hour)', numInput('geo_update_window_start', 0, 23,
              t('Earliest local hour to fire (default 4 = 04:00)', 'Самый ранний час по локальному времени (по умолчанию 4 = 04:00)')))}
            {field('Window end (hour)', numInput('geo_update_window_end', 1, 24,
              t('Latest local hour to fire (default 6 = 06:00)', 'Самый поздний час по локальному времени (по умолчанию 6 = 06:00)')))}
          </fieldset>
        </div>
      ))}

      {/* Timezone */}
      {section(Clock, 'Timezone',
        t('Used by the GeoData auto-update window and any future "do X at HH:MM" feature. Independent of the container TZ env var — change here, no docker edits needed.',
          'Используется окном автообновления GeoData и любыми будущими планировщиками "сделать X в HH:MM". Не зависит от TZ env var контейнера — меняешь здесь, docker трогать не надо.'), (
        <div className="grid grid-cols-2 gap-4">
          {field('IANA timezone', (
            <select
              value={val('timezone') || 'UTC'}
              onChange={(e) => set('timezone', e.target.value)}
              className={clsx(
                'w-full rounded-lg bg-gray-950 border px-3 py-2 text-sm text-gray-100 focus:outline-none',
                draft['timezone'] !== undefined ? 'border-brand-500' : 'border-gray-800',
              )}
            >
              {TIMEZONES.map((tz) => <option key={tz} value={tz}>{tz}</option>)}
            </select>
          ))}
        </div>
      ))}

      {/* Log Level */}
      {section(SettingsIcon, 'Logging', t('Xray and system log verbosity', 'Уровень детализации логов xray'), (
        <div className="grid grid-cols-2 gap-4">
          {field('Xray Log Level', (
            <select
              value={val('log_level')}
              onChange={(e) => set('log_level', e.target.value)}
              className={clsx(
                'w-full rounded-lg bg-gray-950 border px-3 py-2 text-sm text-gray-100 focus:outline-none',
                draft['log_level'] !== undefined ? 'border-brand-500' : 'border-gray-800',
              )}
            >
              <option value="none">none</option>
              <option value="debug">debug</option>
              <option value="info">info</option>
              <option value="warning">warning</option>
              <option value="error">error</option>
            </select>
          ))}
          {toggle('dns_query_log_enabled', 'DNS Query Logging', t('Log all DNS queries (performance impact)', 'Логировать все DNS-запросы (влияет на производительность)'))}
        </div>
      ))}
    </div>
  )
}
