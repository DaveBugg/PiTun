import {
  Server, Pencil, Trash2, Activity, Gauge, Power, Globe, Link2, AlertCircle,
} from 'lucide-react'
import { clsx } from 'clsx'
import type { Node } from '@/types'
import { StatusBadge } from './StatusBadge'
import { useServers } from '@/hooks/useServers'

// Readings older than this render in a warning colour — the speed is
// probably stale and worth re-testing.
const SPEED_STALE_MS = 6 * 60 * 60 * 1000

/** Persisted speed reading + its age. Returns null when the node has
 *  never been speed-tested. `stale` flips true past SPEED_STALE_MS. */
function fmtMbps(v: number): string {
  return v >= 100 ? String(Math.round(v)) : v.toFixed(1)
}

function speedInfo(
  mbps?: number | null, iso?: string | null, maxMbps?: number | null,
): { text: string; stale: boolean } | null {
  if (mbps == null) return null
  // avg, plus the peak when it's meaningfully higher (avg / peak).
  const speed = (maxMbps != null && maxMbps > mbps + 0.05)
    ? `${fmtMbps(mbps)} / ${fmtMbps(maxMbps)}`
    : fmtMbps(mbps)
  if (!iso) return { text: `${speed} Mbps`, stale: false }
  // Backend sends naive UTC (no Z) — normalise before parsing.
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z')
  const diffMs = Date.now() - d.getTime()
  const min = Math.floor(diffMs / 60000)
  const age =
    min < 1 ? 'just now'
      : min < 60 ? `${min}m ago`
      : min < 1440 ? `${Math.floor(min / 60)}h ago`
      : `${Math.floor(min / 1440)}d ago`
  return { text: `${speed} Mbps · ${age}`, stale: diffMs >= SPEED_STALE_MS }
}

interface Props {
  node: Node
  isActive?: boolean
  onEdit?: () => void
  onDelete?: () => void
  onCheck?: () => void
  onSpeedtest?: () => void
  onReachability?: () => void
  onExportUri?: () => void
  onSelect?: () => void
  checkLoading?: boolean
  speedLoading?: boolean
  reachLoading?: boolean
}

export function NodeCard({
  node,
  isActive,
  onEdit,
  onDelete,
  onCheck,
  onSpeedtest,
  onReachability,
  onExportUri,
  onSelect,
  checkLoading,
  speedLoading,
  reachLoading,
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
              {/* Order: ip:port first (most identifying), then
                  protocol → transport → security. Shared palette
                  with the X-ui InboundCard so both lists feel
                  coherent: protocol=blue, transport=green,
                  reality=purple, tls=orange. `tcp` is the implicit
                  default for vless-reality-vision and similar — we
                  hide it to keep the row tight. */}
              <span className="text-xs text-gray-500 font-mono">
                {node.address}:{node.port}
              </span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-900/30 text-blue-300 border border-blue-700/40 uppercase font-mono">
                {node.protocol}
              </span>
              {node.transport !== 'tcp' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-900/30 text-green-300 border border-green-700/40 uppercase font-mono">
                  {node.transport}
                </span>
              )}
              {node.tls === 'reality' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-900/30 text-purple-300 border border-purple-700/40 uppercase font-mono">
                  reality
                </span>
              )}
              {node.tls === 'tls' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-orange-900/30 text-orange-300 border border-orange-700/40 uppercase font-mono">
                  tls
                </span>
              )}
              {node.chain_node_id && !node.chain_orphan && (
                <span className="text-xs text-blue-400 font-mono flex items-center gap-1">
                  🔗 chained
                </span>
              )}
              {/* The relay this node hops through is gone. It keeps its
                  link but silently stops carrying traffic — xray skips
                  the outbound, probes and speed tests come back empty —
                  so the list has to say so instead of showing a healthy
                  "chained" badge. */}
              {node.chain_orphan && (
                <span
                  className="text-xs text-red-300 font-mono flex items-center gap-1 rounded border border-red-800/50 bg-red-950/30 px-1.5 py-0.5"
                  title={`Chained through node #${node.chain_node_id}, which no longer exists — edit the node to pick a live relay or clear the chain.`}
                >
                  ⛓️‍💥 chain broken
                </span>
              )}
            </div>
            <div className="mt-2 flex items-center gap-2 flex-wrap">
              <StatusBadge online={node.is_online} latency={node.latency_ms ?? undefined} />
              {/* Persisted speed reading + age. Reddens past 6h so a
                  stale number doesn't read as current. Hidden until the
                  node has been speed-tested at least once. */}
              {(() => {
                const s = speedInfo(node.speed_mbps, node.speed_tested_at, node.speed_max_mbps)
                if (!s) return null
                return (
                  <span
                    className={clsx(
                      'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px]',
                      s.stale
                        ? 'border-red-800/50 bg-red-950/30 text-red-300'
                        : 'border-gray-700 bg-gray-800/40 text-gray-300',
                    )}
                    title={s.stale
                      ? 'Speed reading is over 6 hours old — re-run the speed test'
                      : 'Last speed test — average / peak'}
                  >
                    <Gauge className="h-3 w-3" />
                    {s.text}
                  </span>
                )
              })()}
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
              title={isActive ? 'Active node' : 'Activate this node'}
              className={clsx(
                'rounded p-1.5 transition-colors',
                isActive
                  ? 'bg-brand-600/20 text-brand-400 ring-1 ring-brand-500/40'
                  : 'text-gray-500 hover:bg-gray-800 hover:text-brand-400',
              )}
            >
              <Power className="h-4 w-4" />
            </button>
          )}
          {onCheck && (
            <button
              onClick={onCheck}
              title="Health check (TCP)"
              disabled={checkLoading}
              className="rounded p-1.5 text-gray-500 hover:bg-gray-800 hover:text-green-400 transition-colors disabled:opacity-50"
            >
              <Activity className={clsx('h-4 w-4', checkLoading && 'animate-pulse')} />
            </button>
          )}
          {onReachability && (
            <button
              onClick={onReachability}
              title="Internet check — open google.com through this node"
              disabled={reachLoading}
              className="rounded p-1.5 text-gray-500 hover:bg-gray-800 hover:text-sky-400 transition-colors disabled:opacity-50"
            >
              <Globe className={clsx('h-4 w-4', reachLoading && 'animate-pulse')} />
            </button>
          )}
          {onSpeedtest && (
            <button
              onClick={onSpeedtest}
              title="Speed test"
              disabled={speedLoading}
              className="rounded p-1.5 text-gray-500 hover:bg-gray-800 hover:text-yellow-400 transition-colors disabled:opacity-50"
            >
              <Gauge className={clsx('h-4 w-4', speedLoading && 'animate-pulse')} />
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
          {onExportUri && (
            <button
              onClick={onExportUri}
              title="Copy share URL"
              className="rounded p-1.5 text-gray-500 hover:bg-gray-800 hover:text-gray-100 transition-colors"
            >
              <Link2 className="h-4 w-4" />
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
