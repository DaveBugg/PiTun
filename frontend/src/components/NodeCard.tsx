import {
  Server, Pencil, Trash2, Activity, Zap, ChevronRight, AlertCircle,
} from 'lucide-react'
import { clsx } from 'clsx'
import type { Node } from '@/types'
import { StatusBadge, ProtocolBadge } from './StatusBadge'
import { useServers } from '@/hooks/useServers'

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
  // Look up the source server for the "from <name>" label. Cached query —
  // shared with the Servers page, no extra cost when both are mounted.
  const { data: servers = [] } = useServers()
  const sourceServer = node.server_id
    ? servers.find((s) => s.id === node.server_id)
    : undefined
  // A Node is "from a managed server-side client" only when both the
  // server link and the deployment-client link are present (WireGuard
  // peers exported via PiTun). Imported nodes / hand-typed nodes don't
  // get this label.
  const isFromServerClient = !!(node.from_deployment_client_id && sourceServer)
  return (
    <div
      className={clsx(
        'rounded-xl border p-4 transition-colors',
        isActive
          ? 'border-brand-600 bg-brand-900/20'
          : 'border-gray-800 bg-gray-900/30 hover:border-gray-700',
      )}
    >
      {/* Stack content above actions on phones — at flex-row, the
          action icons row pushed past the right edge of narrow cards.
          From sm+ collapse back to side-by-side. `min-w-0` on the
          content column lets long names truncate instead of forcing
          horizontal overflow on the parent flex. */}
      <div className="flex flex-col sm:flex-row sm:items-start sm:justify-between gap-2">
        <div className="flex items-start gap-3 min-w-0 flex-1">
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
              <span className="text-xs text-gray-500 font-mono shrink-0">#{node.id}</span>
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
            <div className="mt-2 flex items-center gap-2 flex-wrap">
              <StatusBadge online={node.is_online} latency={node.latency_ms ?? undefined} />
              {/* Source label — "from <server name>" — for nodes
                  exported from a server-side multi-client deployment
                  (WireGuard, since v1.3.0-beta.4). */}
              {isFromServerClient && (
                <span
                  className="text-xs text-gray-500"
                  title={`Exported from DeploymentClient #${node.from_deployment_client_id}`}
                >
                  from <span className="text-gray-300">{sourceServer!.name}</span>
                </span>
              )}
              {/* Orphan badge — the upstream peer was removed
                  server-side or via "Remove client". The node still
                  works (PSK/keys cached) but the admin should know
                  there's no live peer on the VPS anymore. */}
              {node.client_orphan && (
                <span
                  className="inline-flex items-center gap-1 rounded bg-yellow-900/30 border border-yellow-700/40 px-1.5 py-0.5 text-[11px] text-yellow-300"
                  title="The server-side WireGuard peer this Node was exported from no longer exists. Remove the Node, or re-add the peer on the server and re-sync."
                >
                  <AlertCircle className="h-3 w-3" />
                  orphan
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Actions row — wraps within its own line on phones, stays
            inline on tablet+. `justify-end` on sm+ keeps the visual
            anchor where users expect (right edge of card) without
            pushing content off-screen on narrow viewports. */}
        <div className="flex items-center gap-1 flex-wrap sm:flex-nowrap sm:flex-shrink-0 sm:justify-end">
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
