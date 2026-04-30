import { useState } from 'react'
import { Plus, Pencil, Trash2, ToggleLeft, ToggleRight, Layers } from 'lucide-react'
import { InfoTip } from '@/components/InfoTip'
import { clsx } from 'clsx'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { balancersApi } from '@/api/client'
import { useNodes } from '@/hooks/useNodes'
import { useConfirm } from '@/components/ConfirmModal'
import { ModalShell } from '@/components/ModalShell'
import type { BalancerGroup, BalancerGroupCreate } from '@/types'

const STRATEGY_LABELS: Record<string, string> = {
  leastPing: 'Least Ping',
  random: 'Random',
}

const STRATEGY_COLORS: Record<string, string> = {
  leastPing: 'bg-cyan-900/60 text-cyan-300',
  random: 'bg-purple-900/60 text-purple-300',
}

interface ModalProps {
  initial?: BalancerGroup
  nodeOptions: { id: number; name: string }[]
  onSave: (data: BalancerGroupCreate) => void
  onCancel: () => void
  loading?: boolean
}

function BalancerModal({ initial, nodeOptions, onSave, onCancel, loading }: ModalProps) {
  const [name, setName] = useState(initial?.name ?? '')
  const [strategy, setStrategy] = useState<'leastPing' | 'random'>(initial?.strategy ?? 'leastPing')
  const [enabled, setEnabled] = useState(initial?.enabled ?? true)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(
    new Set(initial?.node_ids ?? [])
  )

  const toggleNode = (id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onSave({
      name,
      enabled,
      strategy,
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
          placeholder="My Balancer Group"
        />
      </div>

      <div>
        <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
          Strategy
          <InfoTip className="ml-0.5" text="leastPing — xray measures latency to each node and always picks the fastest one. random — distribute connections randomly across all nodes in the group. Both strategies automatically skip offline nodes." />
        </label>
        <select
          value={strategy}
          onChange={(e) => setStrategy(e.target.value as 'leastPing' | 'random')}
          className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
        >
          <option value="leastPing">Least Ping</option>
          <option value="random">Random</option>
        </select>
      </div>

      <div>
        <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
          Nodes <span className="text-gray-600 font-normal">({selectedIds.size} selected)</span>
          <InfoTip className="ml-0.5" text="Nodes included in this balancer group. All selected nodes must be reachable from RPi. Use health checks (Nodes page) to verify connectivity before adding to a balancer." />
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
          id="balancer-enabled"
          checked={enabled}
          onChange={(e) => setEnabled(e.target.checked)}
          className="rounded border-gray-600 bg-gray-800 text-brand-500"
        />
        <label htmlFor="balancer-enabled" className="text-sm text-gray-300">Enabled</label>
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
          {loading ? 'Saving…' : 'Save Group'}
        </button>
      </div>
    </form>
  )
}

export function Balancers() {
  const [modal, setModal] = useState<'none' | 'add' | 'edit'>('none')
  const [editGroup, setEditGroup] = useState<BalancerGroup | null>(null)
  const qc = useQueryClient()
  const confirm = useConfirm()

  const { data: groups = [] } = useQuery({
    queryKey: ['balancers'],
    queryFn: () => balancersApi.list(),
  })
  const { data: nodes = [] } = useNodes()

  const createGroup = useMutation({
    mutationFn: (data: BalancerGroupCreate) => balancersApi.create(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['balancers'] }); setModal('none') },
  })
  const updateGroup = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<BalancerGroupCreate> }) =>
      balancersApi.update(id, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['balancers'] }); setModal('none') },
  })
  const deleteGroup = useMutation({
    mutationFn: (id: number) => balancersApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['balancers'] }),
  })

  const nodeOptions = nodes.map((n) => ({ id: n.id, name: n.name }))

  const handleSave = (data: BalancerGroupCreate) => {
    if (editGroup) {
      updateGroup.mutate({ id: editGroup.id, data })
    } else {
      createGroup.mutate(data)
    }
  }

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <Layers className="h-5 w-5 text-brand-400" />
          <h1 className="text-xl font-bold text-gray-100">Balancer Groups</h1>
        </div>
        <button
          onClick={() => { setEditGroup(null); setModal('add') }}
          className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors"
        >
          <Plus className="h-4 w-4" />
          Add Group
        </button>
      </div>

      <p className="text-sm text-gray-500">
        Balancer groups let you route traffic through multiple nodes with automatic selection.
        Use <span className="font-mono text-gray-400">balancer:&lt;id&gt;</span> as the action in routing rules.
      </p>

      {groups.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          No balancer groups yet. Add a group to enable load balancing across multiple nodes.
        </div>
      ) : (
        <div className="space-y-3">
          {groups.map((group) => (
            <div
              key={group.id}
              className={clsx(
                'rounded-xl border p-4 transition-colors',
                group.enabled
                  ? 'border-gray-800 bg-gray-900'
                  : 'border-gray-800/50 bg-gray-900/50 opacity-60',
              )}
            >
              <div className="flex items-center gap-3 flex-wrap">
                <span
                  className={clsx(
                    'rounded px-2 py-0.5 text-xs font-mono font-medium',
                    STRATEGY_COLORS[group.strategy] ?? 'bg-gray-700 text-gray-300',
                  )}
                >
                  {STRATEGY_LABELS[group.strategy] ?? group.strategy}
                </span>

                <span className="flex-1 text-sm text-gray-200 font-medium">{group.name}</span>

                <span className="text-xs text-gray-500 font-mono">
                  {group.node_ids.length} node{group.node_ids.length !== 1 ? 's' : ''}
                </span>

                <span className="text-xs text-gray-600 font-mono">
                  balancer:{group.id}
                </span>

                <div className="flex items-center gap-1 ml-auto">
                  <button
                    onClick={() =>
                      updateGroup.mutate({ id: group.id, data: { enabled: !group.enabled } })
                    }
                    title={group.enabled ? 'Disable' : 'Enable'}
                    className="rounded p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
                  >
                    {group.enabled
                      ? <ToggleRight className="h-4 w-4 text-brand-400" />
                      : <ToggleLeft className="h-4 w-4" />}
                  </button>
                  <button
                    onClick={() => { setEditGroup(group); setModal('edit') }}
                    className="rounded p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button
                    onClick={async () => {
                      const ok = await confirm({
                        title: `Delete balancer group "${group.name}"?`,
                        body: 'Routing rules pointing at this group will be left dangling — fix them after deletion.',
                        confirmLabel: 'Delete',
                        danger: true,
                      })
                      if (ok) deleteGroup.mutate(group.id)
                    }}
                    className="rounded p-1.5 text-gray-500 hover:text-red-400 hover:bg-gray-800 transition-colors"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>

              {group.node_ids.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {group.node_ids.map((nid) => {
                    const node = nodes.find((n) => n.id === nid)
                    return (
                      <span
                        key={nid}
                        className="rounded-full bg-gray-800 px-2.5 py-0.5 text-xs text-gray-400 font-mono"
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
        <ModalShell onClose={() => setModal('none')} labelledBy="balancer-modal-title">
          <div className="w-full max-w-lg max-h-[90vh] overflow-y-auto rounded-2xl bg-gray-950 border border-gray-800 p-6">
            <h2 id="balancer-modal-title" className="text-base font-semibold text-gray-100 mb-5">
              {modal === 'add' ? 'Add Balancer Group' : 'Edit Balancer Group'}
            </h2>
            <BalancerModal
              initial={editGroup ?? undefined}
              nodeOptions={nodeOptions}
              onSave={handleSave}
              onCancel={() => setModal('none')}
              loading={createGroup.isPending || updateGroup.isPending}
            />
          </div>
        </ModalShell>
      )}
    </div>
  )
}
