import { useState, useEffect } from 'react'
import {
  Wifi, WifiOff, Radar, Search, RotateCcw, Pencil, Trash2,
  ShieldCheck, ShieldBan, ShieldMinus, Check, X,
  Activity, Clock, ListFilter,
} from 'lucide-react'
import { useConfirm } from '@/components/ConfirmModal'
import { clsx } from 'clsx'
import { useDevices, useUpdateDevice, useDeleteDevice, useScanDevices, useBulkPolicy, useResetAllPolicies } from '@/hooks/useDevices'
import { useSystemSettings, useUpdateSettings } from '@/hooks/useSystem'
import { useT } from '@/hooks/useT'
import type { Device, DeviceRoutingPolicy } from '@/types'

const POLICY_META: Record<DeviceRoutingPolicy, { label: string; color: string; icon: typeof ShieldCheck }> = {
  default:  { label: 'Default',  color: 'bg-gray-700 text-gray-300',          icon: ShieldMinus },
  include:  { label: 'Include',  color: 'bg-green-900/60 text-green-300',     icon: ShieldCheck },
  exclude:  { label: 'Exclude',  color: 'bg-red-900/60 text-red-300',         icon: ShieldBan },
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime()
  const mins = Math.floor(diff / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

export function Devices() {
  const t = useT()
  const confirm = useConfirm()
  const [search, setSearch] = useState('')
  const [filterPolicy, setFilterPolicy] = useState<'' | DeviceRoutingPolicy>('')
  const [filterOnline, setFilterOnline] = useState<'' | 'online' | 'offline'>('')
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [editId, setEditId] = useState<number | null>(null)
  const [editName, setEditName] = useState('')

  const MODE_OPTIONS = [
    { value: 'all',          label: 'All devices',   desc: t('Route traffic for all LAN devices', 'Маршрутизировать трафик всех устройств LAN') },
    { value: 'include_only', label: 'Include only',  desc: t('Route only devices with "include" policy', 'Только устройства с политикой "include"') },
    { value: 'exclude_list', label: 'Exclude list',  desc: t('Route all except devices with "exclude" policy', 'Все устройства кроме помеченных "exclude"') },
  ] as const

  const { data: devices = [], isLoading } = useDevices()
  const { data: settings } = useSystemSettings()
  const updateDevice = useUpdateDevice()
  const deleteDevice = useDeleteDevice()
  const scanDevices = useScanDevices()
  const bulkPolicy = useBulkPolicy()
  const resetAll = useResetAllPolicies()
  const updateSettings = useUpdateSettings()

  // Clear selection when filters change
  useEffect(() => {
    setSelected(new Set())
  }, [search, filterPolicy, filterOnline])

  const filtered = devices.filter((d) => {
    const q = search.toLowerCase()
    const matchSearch = !q ||
      d.mac.toLowerCase().includes(q) ||
      (d.ip ?? '').toLowerCase().includes(q) ||
      (d.name ?? '').toLowerCase().includes(q) ||
      (d.hostname ?? '').toLowerCase().includes(q) ||
      (d.vendor ?? '').toLowerCase().includes(q)
    const matchPolicy = !filterPolicy || d.routing_policy === filterPolicy
    const matchOnline = !filterOnline ||
      (filterOnline === 'online' ? d.is_online : !d.is_online)
    return matchSearch && matchPolicy && matchOnline
  })

  const onlineCount = devices.filter(d => d.is_online).length
  const offlineCount = devices.length - onlineCount

  // Counts per policy (default / include / exclude) for the status plate
  const policyCount = devices.reduce<Record<DeviceRoutingPolicy, number>>(
    (acc, d) => { acc[d.routing_policy] = (acc[d.routing_policy] || 0) + 1; return acc },
    { default: 0, include: 0, exclude: 0 } as Record<DeviceRoutingPolicy, number>,
  )

  // Last-seen timestamp across all devices — proxy for "last successful
  // LAN scan" since the scanner refreshes every visible device.
  const lastSeenIso = devices.reduce<string | null>((acc, d) => {
    if (!d.last_seen) return acc
    return !acc || d.last_seen > acc ? d.last_seen : acc
  }, null)

  const toggleSelect = (id: number) => {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const toggleSelectAll = () => {
    if (selected.size === filtered.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(filtered.map(d => d.id)))
    }
  }

  const applyBulkPolicy = (policy: DeviceRoutingPolicy) => {
    if (selected.size === 0) return
    bulkPolicy.mutate({ device_ids: [...selected], routing_policy: policy }, {
      onSuccess: () => setSelected(new Set()),
    })
  }

  const startEdit = (d: Device) => {
    setEditId(d.id)
    setEditName(d.name ?? '')
  }

  const saveEdit = (d: Device) => {
    updateDevice.mutate({ id: d.id, data: { name: editName } }, {
      onSuccess: () => setEditId(null),
    })
  }

  const cyclePolicy = (d: Device) => {
    const order: DeviceRoutingPolicy[] = ['default', 'include', 'exclude']
    const next = order[(order.indexOf(d.routing_policy) + 1) % order.length]
    updateDevice.mutate({ id: d.id, data: { routing_policy: next } })
  }

  const deviceMode = settings?.device_routing_mode ?? 'all'

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-xl font-bold text-gray-100">Devices</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {devices.length} total &middot; {onlineCount} online
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={async () => {
              const ok = await confirm({
                title: t('Reset all device policies?', 'Сбросить политики всех устройств?'),
                body: t(
                  'Every device returns to "default" routing — include/exclude lists are wiped. Cannot be undone.',
                  'Все устройства вернутся к политике "default" — списки include/exclude обнуляются. Это необратимо.',
                ),
                confirmLabel: t('Reset All', 'Сбросить все'),
                danger: true,
              })
              if (ok) resetAll.mutate()
            }}
            disabled={resetAll.isPending}
            className="flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
          >
            <RotateCcw className={clsx('h-4 w-4', resetAll.isPending && 'animate-spin')} />
            Reset All
          </button>
          <button
            onClick={() => scanDevices.mutate()}
            disabled={scanDevices.isPending}
            className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors disabled:opacity-50"
          >
            <Radar className={clsx('h-4 w-4', scanDevices.isPending && 'animate-pulse')} />
            {scanDevices.isPending ? 'Scanning…' : 'Scan LAN'}
          </button>
        </div>
      </div>

      {/* Scan result toast */}
      {scanDevices.isSuccess && scanDevices.data && (
        <div className="rounded-lg bg-brand-900/30 border border-brand-700/50 px-4 py-2 text-sm text-brand-300">
          Scan complete: {scanDevices.data.discovered} discovered, {scanDevices.data.updated} updated, {scanDevices.data.total} total
        </div>
      )}

      {/* Status plates — at-a-glance health row, mirrors the Service
          Status grid on Dashboard. Display-only (not clickable). */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {/* Online */}
        <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-3">
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <Wifi className="h-3.5 w-3.5 shrink-0" />
            <span className="uppercase tracking-wide font-medium truncate">{t('Online', 'Онлайн')}</span>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <span className={clsx(
              'h-2 w-2 rounded-full shrink-0',
              onlineCount > 0 ? 'bg-green-500 animate-pulse' : 'bg-gray-600',
            )} />
            <span className={clsx('text-sm font-semibold truncate', onlineCount > 0 ? 'text-green-400' : 'text-gray-500')}>
              {onlineCount} {t('of', 'из')} {devices.length}
            </span>
          </div>
          <div className="mt-0.5 text-xs text-gray-600 truncate">{t('proxied via PiTun', 'трафик через PiTun')}</div>
        </div>

        {/* Offline */}
        <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-3">
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <WifiOff className="h-3.5 w-3.5 shrink-0" />
            <span className="uppercase tracking-wide font-medium truncate">{t('Offline', 'Оффлайн')}</span>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <span className={clsx(
              'h-2 w-2 rounded-full shrink-0',
              offlineCount === 0 ? 'bg-green-500 animate-pulse' : 'bg-gray-500',
            )} />
            <span className="text-sm font-semibold truncate text-gray-300">{offlineCount}</span>
          </div>
          <div className="mt-0.5 text-xs text-gray-600 truncate">{t('not seen on LAN', 'не видны в LAN')}</div>
        </div>

        {/* Last scan */}
        <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-3">
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <Clock className="h-3.5 w-3.5 shrink-0" />
            <span className="uppercase tracking-wide font-medium truncate">{t('Last scan', 'Последний скан')}</span>
          </div>
          <div className="mt-2 flex items-center gap-2">
            <span className={clsx(
              'h-2 w-2 rounded-full shrink-0',
              lastSeenIso ? 'bg-green-500 animate-pulse' : 'bg-gray-600',
            )} />
            <span className="text-sm font-semibold truncate text-gray-300">
              {lastSeenIso ? timeAgo(lastSeenIso) : t('never', 'никогда')}
            </span>
          </div>
          <div className="mt-0.5 text-xs text-gray-600 truncate">{t('background ARP', 'фоновый ARP')}</div>
        </div>

        {/* Policies breakdown */}
        <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-3">
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <ListFilter className="h-3.5 w-3.5 shrink-0" />
            <span className="uppercase tracking-wide font-medium truncate">{t('Policies', 'Политики')}</span>
          </div>
          <div className="mt-2 flex items-center gap-3 text-xs font-mono">
            <span className="text-gray-300" title={t('default policy', 'по умолчанию')}>{policyCount.default}</span>
            <span className="text-green-400" title={t('include policy', 'include')}>{policyCount.include}</span>
            <span className="text-red-400" title={t('exclude policy', 'exclude')}>{policyCount.exclude}</span>
          </div>
          <div className="mt-0.5 text-xs text-gray-600 truncate">def / incl / excl</div>
        </div>
      </div>

      {/* Device routing mode — Proxy-Mode-style tile selector (matches the
          Dashboard's Proxy Mode visual: rounded-xl tile, brand border on
          active, icon + label + description). */}
      <div>
        <h2 className="text-sm font-medium text-gray-400 mb-3">{t('Device routing mode', 'Режим маршрутизации устройств')}</h2>
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {MODE_OPTIONS.map(opt => {
            const active = deviceMode === opt.value
            const Icon = opt.value === 'all'
              ? Activity
              : opt.value === 'include_only'
                ? ShieldCheck
                : ShieldBan
            return (
              <button
                key={opt.value}
                onClick={() => updateSettings.mutate({ device_routing_mode: opt.value })}
                className={clsx(
                  'rounded-xl border p-4 text-left transition-all',
                  active
                    ? 'border-brand-600 bg-brand-900/20 text-brand-300'
                    : 'border-gray-800 bg-gray-900/30 text-gray-400 hover:border-gray-700 hover:text-gray-200',
                )}
              >
                <Icon className="h-5 w-5 mb-2" />
                <div className="text-sm font-medium">{opt.label}</div>
                <div className="text-xs opacity-70 mt-0.5">{opt.desc}</div>
              </button>
            )
          })}
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-48">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-500" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search by MAC, IP, name, hostname, vendor…"
            className="w-full rounded-lg bg-gray-900 border border-gray-800 pl-9 pr-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
          />
        </div>

        <div className="flex items-center gap-1">
          {(['', 'online', 'offline'] as const).map(v => (
            <button
              key={v || 'any'}
              onClick={() => setFilterOnline(v)}
              className={clsx(
                'rounded px-2 py-1 text-xs font-medium transition-colors',
                filterOnline === v
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700',
              )}
            >
              {v || 'Any'}
            </button>
          ))}
        </div>

        <div className="flex items-center gap-1">
          {(['', 'default', 'include', 'exclude'] as const).map(p => (
            <button
              key={p || 'all'}
              onClick={() => setFilterPolicy(p)}
              className={clsx(
                'rounded px-2 py-1 text-xs font-medium transition-colors',
                filterPolicy === p
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700',
              )}
            >
              {p || 'All policies'}
            </button>
          ))}
        </div>
      </div>

      {/* Bulk actions */}
      {selected.size > 0 && (
        <div className="flex items-center gap-2 rounded-lg bg-gray-900 border border-gray-800 px-4 py-2">
          <span className="text-sm text-gray-400">{selected.size} selected</span>
          <span className="text-gray-700">|</span>
          <button
            onClick={() => applyBulkPolicy('include')}
            className="flex items-center gap-1 text-xs font-medium text-green-400 hover:text-green-300 transition-colors"
          >
            <ShieldCheck className="h-3.5 w-3.5" /> Include
          </button>
          <button
            onClick={() => applyBulkPolicy('exclude')}
            className="flex items-center gap-1 text-xs font-medium text-red-400 hover:text-red-300 transition-colors"
          >
            <ShieldBan className="h-3.5 w-3.5" /> Exclude
          </button>
          <button
            onClick={() => applyBulkPolicy('default')}
            className="flex items-center gap-1 text-xs font-medium text-gray-400 hover:text-gray-300 transition-colors"
          >
            <ShieldMinus className="h-3.5 w-3.5" /> Default
          </button>
          <button
            onClick={() => setSelected(new Set())}
            className="ml-auto text-xs text-gray-500 hover:text-gray-300 transition-colors"
          >
            Clear selection
          </button>
        </div>
      )}

      {/* Table */}
      {isLoading ? (
        <div className="text-center py-12 text-gray-500">Loading…</div>
      ) : devices.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          No devices discovered yet. Click "Scan LAN" to find devices on your network.
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          No devices match the current filter.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-gray-800">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-800 bg-gray-900/60">
                <th className="px-3 py-2.5 text-left w-10">
                  <input
                    type="checkbox"
                    checked={selected.size === filtered.length && filtered.length > 0}
                    onChange={toggleSelectAll}
                    className="rounded border-gray-700 bg-gray-950 text-brand-500 focus:ring-0 focus:ring-offset-0"
                  />
                </th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Name / Hostname</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">IP</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">MAC</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Vendor</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Policy</th>
                <th className="px-3 py-2.5 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Last Seen</th>
                <th className="px-3 py-2.5 text-right text-xs font-medium text-gray-500 uppercase tracking-wider w-24">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800/60">
              {filtered.map(d => {
                const pm = POLICY_META[d.routing_policy]
                const PolicyIcon = pm.icon
                const isEditing = editId === d.id

                return (
                  <tr
                    key={d.id}
                    className={clsx(
                      'hover:bg-gray-900/40 transition-colors',
                      selected.has(d.id) && 'bg-brand-600/5',
                    )}
                  >
                    <td className="px-3 py-2.5">
                      <input
                        type="checkbox"
                        checked={selected.has(d.id)}
                        onChange={() => toggleSelect(d.id)}
                        className="rounded border-gray-700 bg-gray-950 text-brand-500 focus:ring-0 focus:ring-offset-0"
                      />
                    </td>
                    <td className="px-3 py-2.5">
                      {d.is_online ? (
                        <span className="inline-flex items-center gap-1 text-xs text-green-400">
                          <Wifi className="h-3.5 w-3.5" /> Online
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-xs text-gray-500">
                          <WifiOff className="h-3.5 w-3.5" /> Offline
                        </span>
                      )}
                    </td>
                    <td className="px-3 py-2.5">
                      {isEditing ? (
                        <div className="flex items-center gap-1">
                          <input
                            autoFocus
                            value={editName}
                            onChange={(e) => setEditName(e.target.value)}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') saveEdit(d)
                              if (e.key === 'Escape') setEditId(null)
                            }}
                            className="rounded bg-gray-950 border border-gray-700 px-2 py-1 text-sm text-gray-100 focus:border-brand-500 focus:outline-none w-36"
                          />
                          <button onClick={() => saveEdit(d)} className="text-green-400 hover:text-green-300">
                            <Check className="h-3.5 w-3.5" />
                          </button>
                          <button onClick={() => setEditId(null)} className="text-gray-500 hover:text-gray-300">
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      ) : (
                        <div>
                          <div className="text-gray-100 font-medium">
                            {d.name || d.hostname || '—'}
                          </div>
                          {d.name && d.hostname && (
                            <div className="text-xs text-gray-500">{d.hostname}</div>
                          )}
                        </div>
                      )}
                    </td>
                    <td className="px-3 py-2.5 font-mono text-gray-300">{d.ip || '—'}</td>
                    <td className="px-3 py-2.5 font-mono text-gray-400 text-xs">{d.mac}</td>
                    <td className="px-3 py-2.5 text-gray-500 text-xs max-w-32 truncate" title={d.vendor ?? ''}>
                      {d.vendor || '—'}
                    </td>
                    <td className="px-3 py-2.5">
                      <button
                        onClick={() => cyclePolicy(d)}
                        className={clsx(
                          'inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium transition-colors',
                          pm.color,
                        )}
                        title="Click to cycle policy"
                      >
                        <PolicyIcon className="h-3 w-3" />
                        {pm.label}
                      </button>
                    </td>
                    <td className="px-3 py-2.5 text-xs text-gray-500" title={d.last_seen}>
                      {timeAgo(d.last_seen)}
                    </td>
                    <td className="px-3 py-2.5 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => startEdit(d)}
                          className="rounded p-1.5 text-gray-500 hover:bg-gray-800 hover:text-gray-300 transition-colors"
                          title="Rename"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={async () => {
                            const ok = await confirm({
                              title: t(`Delete device ${d.name || d.mac}?`, `Удалить устройство ${d.name || d.mac}?`),
                              body: t('Removed from the database. Re-discovered on the next LAN scan.', 'Удалится из БД. При следующем скане LAN снова найдётся.'),
                              confirmLabel: t('Delete', 'Удалить'),
                              danger: true,
                            })
                            if (ok) deleteDevice.mutate(d.id)
                          }}
                          className="rounded p-1.5 text-gray-500 hover:bg-gray-800 hover:text-red-400 transition-colors"
                          title="Delete"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
