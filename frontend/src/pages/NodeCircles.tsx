import * as React from 'react'
import { useState } from 'react'
import { Plus, Pencil, Trash2, ToggleLeft, ToggleRight, RefreshCw, Circle } from 'lucide-react'
import { InfoTip } from '@/components/InfoTip'
import { clsx } from 'clsx'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { circleApi } from '@/api/client'
import { useNodes } from '@/hooks/useNodes'
import { useSystemSettings, useUpdateSettings } from '@/hooks/useSystem'
import { useT } from '@/hooks/useT'
import { useConfirm } from '@/components/ConfirmModal'
import { ModalShell } from '@/components/ModalShell'
import type { NodeCircle, NodeCircleCreate } from '@/types'

const MODE_LABELS: Record<string, string> = {
  sequential: 'Sequential',
  random: 'Random',
  best: 'Best',
}

const MODE_COLORS: Record<string, string> = {
  sequential: 'bg-cyan-50 dark:bg-cyan-900/60 text-cyan-700 dark:text-cyan-300',
  random: 'bg-purple-50 dark:bg-purple-900/60 text-purple-700 dark:text-purple-300',
  best: 'bg-emerald-50 dark:bg-emerald-900/60 text-emerald-700 dark:text-emerald-300',
}

function formatInterval(min: number, max: number): string {
  if (min === max) return `Every ${min} min`
  return `Every ${min}\u2013${max} min (random)`
}

function formatLastRotated(iso?: string): string {
  if (!iso) return 'Never'
  // Backend returns naive UTC datetime without Z suffix — add it
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z')
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffMin = Math.floor(diffMs / 60000)
  if (diffMin < 1) return 'Just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffHr = Math.floor(diffMin / 60)
  if (diffHr < 24) return `${diffHr}h ago`
  return d.toLocaleString()
}

interface ModalProps {
  initial?: NodeCircle
  nodeOptions: { id: number; name: string }[]
  onSave: (data: NodeCircleCreate) => void
  onCancel: () => void
  loading?: boolean
}

function CircleModal({ initial, nodeOptions, onSave, onCancel, loading }: ModalProps) {
  const t = useT()
  const [name, setName] = useState(initial?.name ?? '')
  const [mode, setMode] = useState<'sequential' | 'random' | 'best'>(initial?.mode ?? 'sequential')
  const [enabled, setEnabled] = useState(initial?.enabled ?? false)
  // Candidate filters — string-backed like the intervals so the field can
  // be transiently empty. "0" / empty = disabled.
  const [maxLatency, setMaxLatency] = useState(String(initial?.max_latency_ms ?? 0))
  const [minSpeed, setMinSpeed] = useState(String(initial?.min_speed_mbps ?? 0))
  // Interval inputs are kept as strings so the user can transiently clear
  // the field while typing (`Number('')` = 0, which would lock them into
  // a leading-zero state like "035"). We parse on submit; empty/invalid
  // \u2192 1 (the underlying minimum the rotation scheduler accepts).
  const [intervalMin, setIntervalMin] = useState(String(initial?.interval_min ?? 5))
  const [intervalMax, setIntervalMax] = useState(String(initial?.interval_max ?? 15))
  const [selectedIds, setSelectedIds] = useState<Set<number>>(
    new Set(initial?.node_ids ?? [])
  )

  const toggleNode = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // Select all / clear toggle for the node multi-select. If everything
  // is already selected, the button switches to "Clear". On filtered/
  // empty node lists it's effectively a no-op so we don't need a guard.
  const allSelected =
    nodeOptions.length > 0 && selectedIds.size === nodeOptions.length
  const toggleAll = () => {
    if (allSelected) setSelectedIds(new Set())
    else setSelectedIds(new Set(nodeOptions.map((n) => n.id)))
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    // Parse the string inputs late \u2014 empty/invalid coerces to 1, the
    // backend's rotation scheduler treats anything <1 as 1 anyway.
    const minN = Math.max(1, parseInt(intervalMin, 10) || 1)
    const maxN = Math.max(1, parseInt(intervalMax, 10) || 1)
    if (minN > maxN) {
      alert('Min interval must be \u2264 Max interval')
      return
    }
    onSave({
      name,
      enabled,
      mode,
      interval_min: minN,
      interval_max: maxN,
      // Quality filters only apply to "best" mode; zero them otherwise so a
      // hidden value never silently filters a sequential/random circle.
      max_latency_ms: mode === 'best' ? Math.max(0, parseInt(maxLatency, 10) || 0) : 0,
      min_speed_mbps: mode === 'best' ? Math.max(0, parseFloat(minSpeed) || 0) : 0,
      node_ids: Array.from(selectedIds),
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1">Name</label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          autoFocus
          className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
          placeholder="My Node Circle"
        />
      </div>

      <div>
        <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
          Mode
          <InfoTip position="bottom" className="ml-0.5" text={t(
            'Sequential rotates through nodes in order (1 -> 2 -> 3 -> 1). Random picks a different node each time. Best prefers the lowest-latency healthy node.',
            'Sequential ротирует ноды по порядку (1 → 2 → 3 → 1). Random каждый раз выбирает другую ноду. Best предпочитает живую ноду с наименьшим пингом.',
          )} />
        </label>
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value as 'sequential' | 'random' | 'best')}
          className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
        >
          <option value="sequential">Sequential</option>
          <option value="random">Random</option>
          <option value="best">{t('Best (lowest latency + min speed)', 'Лучшая (мин. пинг + мин. скорость)')}</option>
        </select>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
            Interval Min (minutes)
          </label>
          <input
            // String-backed value lets the user transiently empty the
            // field without `Number('')` collapsing the state to 0
            // (which would force an annoying "035" leading-zero shape).
            // Submission parses to int with a min-1 floor.
            type="number"
            inputMode="numeric"
            min={1}
            value={intervalMin}
            onChange={(e) => setIntervalMin(e.target.value)}
            className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
          />
        </div>
        <div>
          <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
            Interval Max (minutes)
          </label>
          <input
            type="number"
            inputMode="numeric"
            min={1}
            value={intervalMax}
            onChange={(e) => setIntervalMax(e.target.value)}
            className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
          />
        </div>
      </div>

      {/* Quality filters — only relevant to "best" mode, which picks the
          lowest-latency candidate that also clears the min-speed floor.
          Hidden (and not submitted) for sequential/random. */}
      {mode === 'best' && (
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
              {t('Max latency (ms)', 'Макс. пинг (мс)')}
              <InfoTip position="bottom" className="ml-0.5" text={t(
                'Skip rotation candidates whose last ping exceeds this. 0 = no limit.',
                'Пропускать кандидатов, чей последний пинг выше этого. 0 = без ограничения.',
              )} />
            </label>
            <input
              type="number"
              inputMode="numeric"
              min={0}
              value={maxLatency}
              onChange={(e) => setMaxLatency(e.target.value)}
              placeholder="0"
              className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
            />
          </div>
          <div>
            <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
              {t('Min speed (Mbps)', 'Мин. скорость (Мбит/с)')}
              <InfoTip position="bottom" className="ml-0.5" text={t(
                'Skip candidates whose last speed test is below this. Untested nodes always pass. 0 = no limit.',
                'Пропускать кандидатов, чей последний замер ниже этого. Непроверенные ноды всегда проходят. 0 = без ограничения.',
              )} />
            </label>
            <input
              type="number"
              inputMode="decimal"
              min={0}
              value={minSpeed}
              onChange={(e) => setMinSpeed(e.target.value)}
              placeholder="0"
              className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
            />
          </div>
        </div>
      )}

      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="flex items-center gap-1 text-xs font-medium text-gray-400">
            Nodes <span className="text-gray-600 font-normal">({selectedIds.size} selected)</span>
            <InfoTip position="bottom" className="ml-0.5" text={t(
              'Select at least 2 nodes for rotation. The circle will rotate through these nodes in the order they appear (sequential mode) or randomly.',
              'Выберите минимум 2 ноды для ротации. Круг будет проходить по нодам в указанном порядке (sequential) или случайно (random).',
            )} />
          </label>
          {/* Select-all toggle button. Shown only when there's something
              to select; switches label between "Select all" / "Clear" so
              one button drives both directions. */}
          {nodeOptions.length > 0 && (
            <button
              type="button"
              onClick={toggleAll}
              className="text-xs text-brand-400 hover:text-brand-700 dark:hover:text-brand-300 transition-colors"
            >
              {allSelected
                ? t('Clear', 'Снять все')
                : t('Select all', 'Выбрать все')}
            </button>
          )}
        </div>
        <div className="rounded-sm bg-gray-800 border border-gray-700 max-h-48 overflow-y-auto divide-y divide-gray-700/50">
          {nodeOptions.length === 0 ? (
            <div className="px-3 py-4 text-sm text-gray-500 text-center">No nodes available</div>
          ) : (
            nodeOptions.map((node) => (
              <label
                key={node.id}
                // `touch-manipulation` removes iOS Safari's 300ms double-tap-
                // zoom delay (otherwise rapid tap-tap-tap on this list races
                // the toggle and looks like checkmarks fall off behind a
                // network round-trip). `select-none` stops a long-press
                // from selecting the label text mid-scroll, which would
                // also swallow the click.
                className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-gray-700/50 transition-colors touch-manipulation select-none"
              >
                <input
                  type="checkbox"
                  checked={selectedIds.has(node.id)}
                  onChange={() => toggleNode(node.id)}
                  className="rounded-sm border-gray-600 bg-gray-700 text-brand-500"
                />
                <span className="text-sm text-gray-200">{node.name}</span>
                <span className="ml-auto text-xs text-gray-600 font-mono">#{node.id}</span>
              </label>
            ))
          )}
        </div>
      </div>

      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="circle-enabled"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
        />
        <label htmlFor="circle-enabled" className="text-sm text-gray-300">Enabled</label>
      </div>

      <div className="flex justify-end gap-3 pt-2 border-t border-gray-800">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={loading}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors"
        >
          {loading ? 'Saving\u2026' : 'Save Circle'}
        </button>
      </div>
    </form>
  )
}

export function NodeCircles() {
  const t = useT()
  const [modal, setModal] = useState<'none' | 'add' | 'edit'>('none')
  const [editCircle, setEditCircle] = useState<NodeCircle | null>(null)
  const [rotatingId, setRotatingId] = useState<number | null>(null)
  const qc = useQueryClient()
  const confirm = useConfirm()

  const { data: circles = [] } = useQuery({
    queryKey: ['nodecircles'],
    queryFn: () => circleApi.list(),
    refetchInterval: 15_000,
  })
  const { data: nodes = [] } = useNodes()

  const createCircle = useMutation({
    mutationFn: (data: NodeCircleCreate) => circleApi.create(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['nodecircles'] }); setModal('none') },
  })
  const updateCircle = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<NodeCircleCreate> }) =>
      circleApi.update(id, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['nodecircles'] }); setModal('none') },
  })
  const toggleEnabled = useMutation({
    mutationFn: ({ id, enabled }: { id: number; enabled: boolean }) =>
      circleApi.update(id, { enabled }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nodecircles'] }),
  })
  const deleteCircle = useMutation({
    mutationFn: (id: number) => circleApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nodecircles'] }),
  })
  const rotateCircle = useMutation({
    mutationFn: (id: number) => circleApi.rotate(id),
    onMutate: (id) => setRotatingId(id),
    onSettled: () => {
      setRotatingId(null)
      qc.invalidateQueries({ queryKey: ['nodecircles'] })
    },
  })

  const nodeOptions = nodes.map((n) => ({ id: n.id, name: n.name }))

  const handleSave = (data: NodeCircleCreate) => {
    if (editCircle) {
      updateCircle.mutate({ id: editCircle.id, data })
    } else {
      createCircle.mutate(data)
    }
  }

  return (
    <div className="p-4 sm:p-6 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <Circle className="h-5 w-5 text-brand-400" />
          <h1 className="text-xl font-bold text-gray-100">NodeCircle</h1>
          <InfoTip position="bottom" text={t(
            'NodeCircle automatically rotates the active proxy node on a timer. This makes traffic analysis harder for DPI systems by changing the exit point regularly. Existing connections finish normally, new connections use the new node.',
            'NodeCircle автоматически ротирует активную прокси-ноду по таймеру. Это затрудняет анализ трафика DPI-системами за счёт смены точки выхода. Существующие соединения завершаются нормально, новые используют новую ноду.',
          )} />
        </div>
        <button
          onClick={() => { setEditCircle(null); setModal('add') }}
          className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors"
        >
          <Plus className="h-4 w-4" />
          Add Circle
        </button>
      </div>

      <p className="text-sm text-gray-500">
        {t(
          'Automatic node rotation for anti-DPI / anti-detection. Xray is reloaded seamlessly (existing connections finish, new ones use the new node).',
          'Автоматическая ротация нод против DPI / антидетект. Xray перезагружается без разрыва соединений (текущие завершаются штатно, новые используют новую ноду).',
        )}
      </p>

      <FailoverToggle />

      {circles.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          No node circles yet. Create a circle with at least 2 nodes to enable automatic rotation.
        </div>
      ) : (
        <div className="space-y-3">
          {circles.map((circle) => (
            <div
              key={circle.id}
              className={clsx(
                'rounded-xl border p-4 transition-colors',
                circle.enabled
                  ? 'border-gray-800 bg-gray-900'
                  : 'border-gray-800 bg-gray-800/40 opacity-90',
              )}
            >
              <div className="flex items-center gap-3 flex-wrap">
                <span
                  className={clsx(
                    'rounded-sm px-2 py-0.5 text-xs font-mono font-medium',
                    MODE_COLORS[circle.mode] ?? 'bg-gray-700 text-gray-300',
                  )}
                >
                  {MODE_LABELS[circle.mode] ?? circle.mode}
                </span>

                <span className="flex-1 text-sm text-gray-200 font-medium">{circle.name}</span>

                <span className="text-xs text-gray-500 font-mono">
                  {circle.node_ids.length} node{circle.node_ids.length !== 1 ? 's' : ''}
                </span>

                <span className="text-xs text-gray-500">
                  {formatInterval(circle.interval_min, circle.interval_max)}
                </span>

                <div className="flex items-center gap-1 ml-auto">
                  <button
                    onClick={() => rotateCircle.mutate(circle.id)}
                    disabled={rotatingId === circle.id || circle.node_ids.length < 2}
                    title="Rotate Now"
                    className="rounded-sm p-1.5 text-gray-500 hover:text-brand-400 hover:bg-gray-800 transition-colors disabled:opacity-40"
                  >
                    <RefreshCw className={clsx(
                      'h-4 w-4',
                      rotatingId === circle.id && 'animate-spin',
                    )} />
                  </button>
                  <button
                    onClick={() =>
                      toggleEnabled.mutate({ id: circle.id, enabled: !circle.enabled })
                    }
                    title={circle.enabled ? 'Disable' : 'Enable'}
                    className="rounded-sm p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
                  >
                    {circle.enabled
                      ? <ToggleRight className="h-4 w-4 text-brand-400" />
                      : <ToggleLeft className="h-4 w-4" />}
                  </button>
                  <button
                    onClick={() => { setEditCircle(circle); setModal('edit') }}
                    className="rounded-sm p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button
                    onClick={async () => {
                      const ok = await confirm({
                        title: `Delete circle "${circle.name}"?`,
                        body: 'Rotation stops; member nodes are kept untouched.',
                        confirmLabel: 'Delete',
                        danger: true,
                      })
                      if (ok) deleteCircle.mutate(circle.id)
                    }}
                    className="rounded-sm p-1.5 text-gray-500 hover:text-red-400 hover:bg-gray-800 transition-colors"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>

              {/* Current node + last rotated */}
              <div className="mt-2 flex items-center gap-4 text-xs text-gray-500">
                {circle.current_node_name && (
                  <span>
                    Active: <span className="text-brand-400 font-medium">{circle.current_node_name}</span>
                  </span>
                )}
                <span>
                  Last rotated: {formatLastRotated(circle.last_rotated)}
                </span>
              </div>

              {/* Node chips */}
              {circle.node_ids.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {circle.node_ids.map((nid, idx) => {
                    const node = nodes.find((n) => n.id === nid)
                    const isActive = idx === circle.current_index
                    return (
                      <span
                        key={nid}
                        className={clsx(
                          'rounded-full px-2.5 py-0.5 text-xs font-mono',
                          isActive
                            ? 'bg-brand-50 text-brand-700 dark:bg-brand-600/30 dark:text-brand-300 ring-1 ring-brand-500/50'
                            : 'bg-gray-800 text-gray-400',
                        )}
                      >
                        {node ? node.name : `node-${nid}`}
                      </span>
                    )
                  })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {(modal === 'add' || modal === 'edit') && (
        <ModalShell onClose={() => setModal('none')} labelledBy="circle-modal-title">
          <div className="w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-2xl bg-gray-950 border border-gray-800 p-6">
            <h2 id="circle-modal-title" className="text-base font-semibold text-gray-100 mb-5">
              {modal === 'add' ? 'Add Node Circle' : 'Edit Node Circle'}
            </h2>
            <CircleModal
              key={editCircle?.id ?? 'add'}
              initial={editCircle ?? undefined}
              nodeOptions={nodeOptions}
              onSave={handleSave}
              onCancel={() => setModal('none')}
              loading={createCircle.isPending || updateCircle.isPending}
            />
          </div>
        </ModalShell>
      )}
    </div>
  )
}

// ── Failover toggle ─────────────────────────────────────────────────────────
//
// Single global setting (`failover_enabled`) that controls whether the
// HealthChecker tries to recover when an active node fails its probes.
// Since 1.2.3, recovery is two-tier:
//   1. If the failed node is in an enabled NodeCircle, ROTATE within the
//      circle (uses the same pre-ping + retry as scheduled rotation).
//   2. Otherwise (or if all circle siblings dead), fall through to the
//      legacy `failover_node_ids` list.
//
// This component lives on the NodeCircles page because Tier 1 is the
// reason most users will want to enable failover today — circle nodes
// double as failover candidates without any extra config.

function FailoverToggle() {
  const t = useT()
  const { data: settings } = useSystemSettings()
  const update = useUpdateSettings()
  const { data: nodes = [] } = useNodes()
  const enabled = !!settings?.failover_enabled
  // failover_node_ids may come back as `number[]`, an empty list, or
  // (when the setting was never set) undefined. Normalise to `Set<number>`
  // for cheap toggling.
  const fallbackIds = React.useMemo(
    () => new Set<number>(settings?.failover_node_ids ?? []),
    [settings?.failover_node_ids],
  )

  const toggle = () => {
    update.mutate({ failover_enabled: !enabled })
  }

  const toggleNode = (id: number) => {
    const next = new Set(fallbackIds)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    update.mutate({ failover_node_ids: Array.from(next).sort((a, b) => a - b) })
  }

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-4 space-y-3">
      <div className="flex items-start gap-3">
        <button
          onClick={toggle}
          disabled={update.isPending}
          className={clsx(
            'mt-0.5 shrink-0 rounded-lg p-1 transition-colors',
            enabled
              ? 'text-brand-400 hover:text-brand-700 dark:hover:text-brand-300'
              : 'text-gray-600 hover:text-gray-400',
          )}
          aria-label={t('Toggle failover', 'Переключить failover')}
        >
          {enabled
            ? <ToggleRight className="h-6 w-6" />
            : <ToggleLeft className="h-6 w-6" />}
        </button>
        <div className="flex-1">
          <div className="flex items-center gap-1.5">
            <span className="text-sm font-medium text-gray-200">
              {t('Auto-failover on health check fail', 'Авто-failover при падении health check')}
            </span>
            <InfoTip position="bottom" text={t(
              'When an active node fails its health checks repeatedly, automatically switch to a working sibling. Two-tier strategy: (1) if the failed node is in an enabled NodeCircle, rotate within the circle (pre-ping each candidate, pick first alive). (2) Otherwise (or if the circle has no live siblings), fall back to the manually-picked list below.',
              'Когда активная нода стабильно проваливает health check, автоматически переключаемся на живую соседнюю. Двухтурная стратегия: (1) если упавшая нода в круге — recovery через круг (pre-ping каждого кандидата). (2) Иначе (или если в круге все мертвы) — список ниже.',
            )} />
          </div>
          <p className="mt-1 text-xs text-gray-500 leading-relaxed">
            {enabled ? (
              t(
                'Active. When the active node fails, PiTun rotates within its NodeCircle (if any) or falls back to the list below.',
                'Включено. При падении активной ноды PiTun ротирует в её NodeCircle (если есть) или к списку ниже.',
              )
            ) : (
              t(
                'Disabled. Failed nodes stay active until you switch manually.',
                'Выключено. Упавшие ноды остаются активными до ручного переключения.',
              )
            )}
          </p>
        </div>
      </div>

      {/* Fallback nodes list — only relevant when failover is on AND the
          user wants the Tier-2 (no-circle) path to find candidates.
          Hidden when toggle is off to reduce visual noise. */}
      {enabled && (
        <div className="border-t border-gray-800 pt-3">
          <div className="flex items-center gap-1.5 mb-2">
            <span className="text-xs font-medium text-gray-400 uppercase tracking-wider">
              {t('Fallback nodes', 'Fallback-ноды')}
            </span>
            <InfoTip position="bottom" text={t(
              'Used only when the failed node is NOT in any enabled NodeCircle (or the circle has no live siblings). PiTun probes these in order; the first alive one becomes active. Leave empty if you only use circles.',
              'Используются когда упавшая нода НЕ в круге (или в круге все мертвы). PiTun пробит их по порядку; первая живая становится активной. Можно оставить пустым если используешь только круги.',
            )} />
            <span className="ml-auto text-[11px] text-gray-600">
              {fallbackIds.size} {t('selected', 'выбрано')}
            </span>
          </div>
          {nodes.length === 0 ? (
            <p className="text-xs text-gray-500">
              {t('No nodes available. Add nodes to use as fallback.', 'Нет нод. Добавьте на странице Nodes.')}
            </p>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {nodes.map((n) => {
                const selected = fallbackIds.has(n.id)
                return (
                  <button
                    key={n.id}
                    onClick={() => toggleNode(n.id)}
                    disabled={update.isPending}
                    className={clsx(
                      'rounded-full px-2.5 py-1 text-xs font-medium transition-colors disabled:opacity-50',
                      selected
                        ? 'bg-brand-50 dark:bg-brand-600/30 border border-brand-400/60 text-brand-700 dark:text-brand-200'
                        : 'bg-gray-800 border border-gray-700 text-gray-400 hover:bg-gray-700 hover:text-gray-300',
                    )}
                    title={`${n.protocol} · ${n.address}:${n.port}`}
                  >
                    {n.name}
                  </button>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
