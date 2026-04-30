import { useState } from 'react'
import { Plus, Pencil, Trash2, ToggleLeft, ToggleRight, RefreshCw, Circle } from 'lucide-react'
import { InfoTip } from '@/components/InfoTip'
import { clsx } from 'clsx'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { circleApi } from '@/api/client'
import { useNodes } from '@/hooks/useNodes'
import { useT } from '@/hooks/useT'
import { useConfirm } from '@/components/ConfirmModal'
import { ModalShell } from '@/components/ModalShell'
import type { NodeCircle, NodeCircleCreate } from '@/types'

const MODE_LABELS: Record<string, string> = {
  sequential: 'Sequential',
  random: 'Random',
}

const MODE_COLORS: Record<string, string> = {
  sequential: 'bg-cyan-900/60 text-cyan-300',
  random: 'bg-purple-900/60 text-purple-300',
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
  const [mode, setMode] = useState<'sequential' | 'random'>(initial?.mode ?? 'sequential')
  const [enabled, setEnabled] = useState(initial?.enabled ?? false)
  const [intervalMin, setIntervalMin] = useState(initial?.interval_min ?? 5)
  const [intervalMax, setIntervalMax] = useState(initial?.interval_max ?? 15)
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

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (intervalMin > intervalMax) {
      alert('Min interval must be \u2264 Max interval')
      return
    }
    onSave({
      name,
      enabled,
      mode,
      interval_min: intervalMin,
      interval_max: intervalMax,
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
          className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
          placeholder="My Node Circle"
        />
      </div>

      <div>
        <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
          Mode
          <InfoTip className="ml-0.5" text={t(
            'Sequential rotates through nodes in order (1 -> 2 -> 3 -> 1). Random picks a different node each time (never the same one twice in a row).',
            'Sequential ротирует ноды по порядку (1 → 2 → 3 → 1). Random каждый раз выбирает другую ноду (никогда та же два раза подряд).',
          )} />
        </label>
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value as 'sequential' | 'random')}
          className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
        >
          <option value="sequential">Sequential</option>
          <option value="random">Random</option>
        </select>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
            Interval Min (minutes)
          </label>
          <input
            type="number"
            min={1}
            value={intervalMin}
            onChange={(e) => setIntervalMin(Number(e.target.value))}
            className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
          />
        </div>
        <div>
          <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
            Interval Max (minutes)
          </label>
          <input
            type="number"
            min={1}
            value={intervalMax}
            onChange={(e) => setIntervalMax(Number(e.target.value))}
            className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
          />
        </div>
      </div>

      <div>
        <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
          Nodes <span className="text-gray-600 font-normal">({selectedIds.size} selected)</span>
          <InfoTip className="ml-0.5" text={t(
            'Select at least 2 nodes for rotation. The circle will rotate through these nodes in the order they appear (sequential mode) or randomly.',
            'Выберите минимум 2 ноды для ротации. Круг будет проходить по нодам в указанном порядке (sequential) или случайно (random).',
          )} />
        </label>
        <div className="rounded bg-gray-800 border border-gray-700 max-h-48 overflow-y-auto divide-y divide-gray-700/50">
          {nodeOptions.length === 0 ? (
            <div className="px-3 py-4 text-sm text-gray-500 text-center">No nodes available</div>
          ) : (
            nodeOptions.map((node) => (
              <label
                key={node.id}
                className="flex items-center gap-3 px-3 py-2 cursor-pointer hover:bg-gray-700/50 transition-colors"
              >
                <input
                  type="checkbox"
                  checked={selectedIds.has(node.id)}
                  onChange={() => toggleNode(node.id)}
                  className="rounded border-gray-600 bg-gray-700 text-brand-500"
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
          className="rounded border-gray-600 bg-gray-800 text-brand-500"
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
    <div className="p-6 space-y-5">
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
                  : 'border-gray-800/50 bg-gray-900/50 opacity-60',
              )}
            >
              <div className="flex items-center gap-3 flex-wrap">
                <span
                  className={clsx(
                    'rounded px-2 py-0.5 text-xs font-mono font-medium',
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
                    className="rounded p-1.5 text-gray-500 hover:text-brand-400 hover:bg-gray-800 transition-colors disabled:opacity-40"
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
                    className="rounded p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
                  >
                    {circle.enabled
                      ? <ToggleRight className="h-4 w-4 text-brand-400" />
                      : <ToggleLeft className="h-4 w-4" />}
                  </button>
                  <button
                    onClick={() => { setEditCircle(circle); setModal('edit') }}
                    className="rounded p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
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
                    className="rounded p-1.5 text-gray-500 hover:text-red-400 hover:bg-gray-800 transition-colors"
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
                            ? 'bg-brand-600/30 text-brand-300 ring-1 ring-brand-500/50'
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
