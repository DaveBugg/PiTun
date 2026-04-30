import { Server, Pencil, Trash2, Activity, Zap, ChevronRight } from 'lucide-react'
import { clsx } from 'clsx'
import type { Node } from '@/types'
import { StatusBadge, ProtocolBadge } from './StatusBadge'

interface Props {
  node: Node
  isActive?: boolean
  onEdit?: () => void
  onDelete?: () => void
  onCheck?: () => void
  onSpeedtest?: () => void
  onSelect?: () => void
  checkLoading?: boolean
  speedLoading?: boolean
}

export function NodeCard({
  node,
  isActive,
  onEdit,
  onDelete,
  onCheck,
  onSpeedtest,
  onSelect,
  checkLoading,
  speedLoading,
}: Props) {
  return (
    <div
      className={clsx(
        'rounded-xl border p-4 transition-colors',
        isActive
          ? 'border-brand-600 bg-brand-900/20'
          : 'border-gray-800 bg-gray-900/30 hover:border-gray-700',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-3 min-w-0">
          <div
            className={clsx(
              'mt-0.5 flex h-8 w-8 items-center justify-center rounded-lg flex-shrink-0',
              isActive ? 'bg-brand-600' : 'bg-gray-800',
            )}
          >
            <Server className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium text-gray-100 truncate">{node.name}</span>
              {isActive && (
                <span className="rounded-full bg-brand-600/20 px-2 py-0.5 text-xs text-brand-400">
                  Active
                </span>
              )}
            </div>
            <div className="mt-1 flex items-center gap-2 flex-wrap">
              <ProtocolBadge protocol={node.protocol} />
              <span className="text-xs text-gray-500 font-mono">
                {node.address}:{node.port}
              </span>
              {node.transport !== 'tcp' && (
                <span className="text-xs text-gray-600 uppercase">{node.transport}</span>
              )}
              {node.tls !== 'none' && (
                <span className="text-xs text-gray-600 uppercase">{node.tls}</span>
              )}
              {node.chain_node_id && (
                <span className="text-xs text-blue-400 font-mono flex items-center gap-1">
                  🔗 chained
                </span>
              )}
            </div>
            <div className="mt-2">
              <StatusBadge online={node.is_online} latency={node.latency_ms ?? undefined} />
            </div>
          </div>
        </div>

        <div className="flex items-center gap-1 flex-shrink-0">
          {onSelect && (
            <button
              onClick={onSelect}
              title="Set as active"
              className="rounded p-1.5 text-gray-500 hover:bg-gray-800 hover:text-brand-400 transition-colors"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
          )}
          {onCheck && (
            <button
              onClick={onCheck}
              title="Health check"
              disabled={checkLoading}
              className="rounded p-1.5 text-gray-500 hover:bg-gray-800 hover:text-green-400 transition-colors disabled:opacity-50"
            >
              <Activity className={clsx('h-4 w-4', checkLoading && 'animate-pulse')} />
            </button>
          )}
          {onSpeedtest && (
            <button
              onClick={onSpeedtest}
              title="Speed test"
              disabled={speedLoading}
              className="rounded p-1.5 text-gray-500 hover:bg-gray-800 hover:text-yellow-400 transition-colors disabled:opacity-50"
            >
              <Zap className={clsx('h-4 w-4', speedLoading && 'animate-pulse')} />
            </button>
          )}
          {onEdit && (
            <button
              onClick={onEdit}
              title="Edit"
              className="rounded p-1.5 text-gray-500 hover:bg-gray-800 hover:text-gray-100 transition-colors"
            >
              <Pencil className="h-4 w-4" />
            </button>
          )}
          {onDelete && (
            <button
              onClick={onDelete}
              title="Delete"
              className="rounded p-1.5 text-gray-500 hover:bg-gray-800 hover:text-red-400 transition-colors"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
