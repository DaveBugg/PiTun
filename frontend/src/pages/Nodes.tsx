import { useState } from 'react'
import { Plus, Download, Activity, Search, Filter, GripVertical, Gauge } from 'lucide-react'
import { clsx } from 'clsx'
import { useQueryClient, useMutation } from '@tanstack/react-query'
import {
  useNodes,
  useCreateNode,
  useUpdateNode,
  useDeleteNode,
  useCheckNodeHealth,
  useCheckAllNodes,
  useSpeedtest,
} from '@/hooks/useNodes'
import { nodesApi } from '@/api/client'
import { useSystemStatus, useSetActiveNode } from '@/hooks/useSystem'
import { NodeCard } from '@/components/NodeCard'
import { NodeForm } from '@/components/NodeForm'
import { UriImport } from '@/components/UriImport'
import { NaiveSidecarPanel } from '@/components/NaiveSidecarPanel'
import { useConfirm } from '@/components/ConfirmModal'
import { ModalShell } from '@/components/ModalShell'
import type { Node, NodeCreate } from '@/types'

type Modal = 'none' | 'add' | 'edit' | 'import'

export function Nodes() {
  const confirm = useConfirm()
  const [modal, setModal] = useState<Modal>('none')
  const [editNode, setEditNode] = useState<Node | null>(null)
  const [search, setSearch] = useState('')
  const [filterProtocol, setFilterProtocol] = useState('')

  const { data: nodes = [], isLoading } = useNodes()
  const { data: status } = useSystemStatus()
  const createNode = useCreateNode()
  const updateNode = useUpdateNode()
  const deleteNode = useDeleteNode()
  const checkHealth = useCheckNodeHealth()
  const checkAll = useCheckAllNodes()
  const speedtest = useSpeedtest()
  const setActive = useSetActiveNode()

  const [speedResults, setSpeedResults] = useState<Record<number, string>>({})
  const [dragId, setDragId] = useState<number | null>(null)
  const qc = useQueryClient()

  const speedAll = useMutation({
    mutationFn: () => nodesApi.speedtestAll(),
    onSuccess: (results) => {
      const map: Record<number, string> = {}
      for (const r of results) {
        map[r.node_id] = r.download_mbps != null ? `${r.download_mbps} Mbps` : r.error ?? 'failed'
      }
      setSpeedResults(map)
    },
  })

  // Reorder via useMutation so it follows the same pattern as other CRUD
  // operations (toast hooks, optimistic update later, error surface via
  // mutation.error). Previously was a bare `.then(invalidateQueries)` that
  // silently swallowed failures.
  const reorderNodes = useMutation({
    mutationFn: (ids: number[]) => nodesApi.reorder(ids),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nodes'] }),
  })

  const canDrag = !search && !filterProtocol

  const filtered = nodes.filter((n) => {
    const matchSearch = !search || n.name.toLowerCase().includes(search.toLowerCase()) ||
      n.address.toLowerCase().includes(search.toLowerCase())
    const matchProto = !filterProtocol || n.protocol === filterProtocol
    return matchSearch && matchProto
  })

  const protocols = [...new Set(nodes.map((n) => n.protocol))]

  const handleSave = (data: NodeCreate) => {
    if (editNode) {
      updateNode.mutate({ id: editNode.id, data }, { onSuccess: () => setModal('none') })
    } else {
      createNode.mutate(data, { onSuccess: () => setModal('none') })
    }
  }

  const handleSpeedtest = async (node: Node) => {
    setSpeedResults((r) => ({ ...r, [node.id]: 'testing…' }))
    speedtest.mutate(node.id, {
      onSuccess: (r) => {
        setSpeedResults((prev) => ({
          ...prev,
          [node.id]: r.download_mbps != null ? `${r.download_mbps} Mbps` : r.error ?? 'failed',
        }))
      },
      onError: () => {
        setSpeedResults((prev) => ({ ...prev, [node.id]: 'error' }))
      },
    })
  }

  const handleDrop = (targetId: number) => {
    if (dragId === null || dragId === targetId) return
    // Use full node list for reorder, not filtered subset
    const ids = (nodes ?? []).map((n) => n.id)
    const fromIndex = ids.indexOf(dragId)
    const toIndex = ids.indexOf(targetId)
    if (fromIndex === -1 || toIndex === -1) return
    ids.splice(fromIndex, 1)
    ids.splice(toIndex, 0, dragId)
    setDragId(null)
    reorderNodes.mutate(ids)
  }

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-xl font-bold text-gray-100">Nodes</h1>
        <div className="flex items-center gap-2">
          <button
            onClick={() => checkAll.mutate()}
            disabled={checkAll.isPending}
            className="flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
          >
            <Activity className={clsx('h-4 w-4', checkAll.isPending && 'animate-pulse')} />
            Test All
          </button>
          <button
            onClick={() => speedAll.mutate()}
            disabled={speedAll.isPending}
            className="flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
          >
            <Gauge className={clsx('h-4 w-4', speedAll.isPending && 'animate-spin')} />
            {speedAll.isPending ? 'Testing…' : 'Speed All'}
          </button>
          <button
            onClick={() => setModal('import')}
            className="flex items-center gap-1.5 rounded-lg bg-gray-700 px-3 py-2 text-sm text-gray-200 hover:bg-gray-600 transition-colors"
          >
            <Download className="h-4 w-4" />
            Import
          </button>
          <button
            onClick={() => { setEditNode(null); setModal('add') }}
            className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors"
          >
            <Plus className="h-4 w-4" />
            Add Node
          </button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-48">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-500" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search nodes…"
            className="w-full rounded-lg bg-gray-900 border border-gray-800 pl-9 pr-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
          />
        </div>
        <div className="flex items-center gap-1">
          <Filter className="h-4 w-4 text-gray-500" />
          {['', ...protocols].map((p) => (
            <button
              key={p || 'all'}
              onClick={() => setFilterProtocol(p)}
              className={clsx(
                'rounded px-2 py-1 text-xs font-medium transition-colors',
                filterProtocol === p
                  ? 'bg-brand-600 text-white'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700',
              )}
            >
              {p || 'All'}
            </button>
          ))}
        </div>
      </div>

      {/* Node list */}
      {isLoading ? (
        <div className="text-center py-12 text-gray-500">Loading…</div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          {nodes.length === 0
            ? 'No nodes yet. Add or import nodes to get started.'
            : 'No nodes match the current filter.'}
        </div>
      ) : (
        <div className="grid gap-3">
          {filtered.map((node) => (
            <div
              key={node.id}
              draggable={canDrag}
              onDragStart={() => setDragId(node.id)}
              // `dragend` fires on EVERY drag termination — success, Esc
              // cancel, dropped outside a valid target, or a click-hold
              // without movement. `drop` fires only on a successful drop,
              // so relying on it alone leaves `dragId` pointing at a row
              // that's no longer being dragged → that row stays at
              // opacity-50 forever until the next drag starts.
              onDragEnd={() => setDragId(null)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => handleDrop(node.id)}
              className={clsx('flex items-start gap-2', dragId === node.id && 'opacity-50')}
            >
              <div className={clsx('mt-3 shrink-0', canDrag ? 'cursor-grab text-gray-600 hover:text-gray-400' : 'text-gray-800 cursor-not-allowed')}>
                <GripVertical className="h-4 w-4" />
              </div>
              <div className="flex-1 min-w-0">
                <NodeCard
                  node={node}
                  isActive={node.id === status?.active_node_id}
                  onEdit={() => { setEditNode(node); setModal('edit') }}
                  onDelete={async () => {
                    const ok = await confirm({
                      title: `Delete "${node.name}"?`,
                      body: 'Routing rules pointing at this node will be left dangling — fix them after deletion.',
                      confirmLabel: 'Delete',
                      danger: true,
                    })
                    if (ok) deleteNode.mutate(node.id)
                  }}
                  onCheck={() => checkHealth.mutate(node.id)}
                  onSpeedtest={() => handleSpeedtest(node)}
                  onSelect={() => setActive.mutate(node.id)}
                  checkLoading={checkHealth.isPending && checkHealth.variables === node.id}
                  speedLoading={speedtest.isPending && speedtest.variables === node.id}
                />
                {speedResults[node.id] && (
                  <div className="text-xs text-gray-500 mt-1 pl-4 font-mono">
                    Speed: {speedResults[node.id]}
                  </div>
                )}
                {node.protocol === 'naive' && (
                  <NaiveSidecarPanel nodeId={node.id} nodeName={node.name} />
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Add / Edit modal */}
      {(modal === 'add' || modal === 'edit') && (
        <Modal title={modal === 'add' ? 'Add Node' : 'Edit Node'} onClose={() => setModal('none')}>
          <NodeForm
            initial={editNode ?? undefined}
            onSave={handleSave}
            onCancel={() => setModal('none')}
            loading={createNode.isPending || updateNode.isPending}
            nodes={nodes}
          />
        </Modal>
      )}

      {/* Import modal */}
      {modal === 'import' && (
        <Modal title="Import Nodes" onClose={() => setModal('none')}>
          <UriImport
            onDone={(count) => {
              if (count > 0) setModal('none')
            }}
            onCancel={() => setModal('none')}
          />
        </Modal>
      )}
    </div>
  )
}

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <ModalShell onClose={onClose} labelledBy="nodes-modal-title">
      <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-2xl bg-gray-950 border border-gray-800 p-6">
        <h2 id="nodes-modal-title" className="text-base font-semibold text-gray-100 mb-5">{title}</h2>
        {children}
      </div>
    </ModalShell>
  )
}
