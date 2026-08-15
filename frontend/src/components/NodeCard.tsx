import {
  Server, Pencil, Trash2, Activity, Gauge, Power, Globe, Link2, AlertCircle,
} from 'lucide-react'
import { clsx } from 'clsx'
import type { Node } from '@/types'
import { StatusBadge } from './StatusBadge'
import { useServers } from '@/hooks/useServers'
import { splitCountryPrefix } from '@/lib/countryPrefix'
import { countryFlag, countryName } from '@/lib/countries'
import { useAppStore } from '@/store'

// Readings older than this render in a warning colour — the speed is
// probably stale and worth re-testing.
const SPEED_STALE_MS = 6 * 60 * 60 * 1000

/** Persisted speed reading + its age. Returns null when the node has
 *  never been speed-tested. `stale` flips true past SPEED_STALE_MS. */
function fmtMbps(v: number): string {
  return v >= 100 ? String(Math.round(v)) : v.toFixed(1)
}

// Backend sends naive UTC (no Z) — normalise before parsing.
function fmtAge(iso: string): string {
  const min = Math.floor((Date.now() - new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime()) / 60000)
  return min < 1 ? 'just now'
    : min < 60 ? `${min}m ago`
    : min < 1440 ? `${Math.floor(min / 60)}h ago`
    : `${Math.floor(min / 1440)}d ago`
}

function speedInfo(
  mbps?: number | null, iso?: string | null, maxMbps?: number | null,
): { text: string; stale: boolean; failed?: boolean } | null {
  if (mbps == null) {
    // No reading. If it was never checked (no timestamp) show nothing;
    // if it WAS checked (a sweep/manual test stamped the time) the check
    // failed — surface that instead of leaving the row blank.
    if (!iso) return null
    return { text: `no speed · ${fmtAge(iso)}`, stale: false, failed: true }
  }
  // avg, plus the peak when it's meaningfully higher (avg / peak).
  const speed = (maxMbps != null && maxMbps > mbps + 0.05)
    ? `${fmtMbps(mbps)} / ${fmtMbps(maxMbps)}`
    : fmtMbps(mbps)
  if (!iso) return { text: `${speed} Mbps`, stale: false }
  const diffMs = Date.now() - new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime()
  return { text: `${speed} Mbps · ${fmtAge(iso)}`, stale: diffMs >= SPEED_STALE_MS }
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
  const lang = useAppStore((s) => s.lang)
  // Two sources for the flag, in order of authority. `node.country` is where
  // the traffic was seen coming out, read back through the tunnel by the
  // speed / internet check — true even for a chained node, whose address
  // belongs to the entry hop and not to the exit. Failing that, the prefix
  // baked into the stored name (GeoIP on the address, at import time).
  // Either way it renders as its own badge rather than as the first word of
  // the name, which is how it read before.
  const prefix = splitCountryPrefix(node.name)
  const code = (node.country || prefix.code || '').toUpperCase()
  const country = {
    code,
    flag: countryFlag(code) || prefix.flag,
    name: prefix.name,
  }
  const countryTitle = [
    countryName(code, lang) || code,
    node.exit_ip ? `exit ${node.exit_ip}` : null,
  ].filter(Boolean).join(' · ')
  return (
    <div
      className={clsx(
        'rounded-xl border p-4 transition-colors',
        isActive
          ? 'border-brand-400 bg-brand-50 dark:bg-brand-500/12'
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
              'mt-0.5 flex h-8 w-8 items-center justify-center rounded-lg shrink-0',
              isActive ? 'bg-brand-600 text-white' : 'bg-gray-800 text-gray-400',
            )}
          >
            <Server className="h-4 w-4" />
          </div>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs text-gray-500 font-mono shrink-0">#{node.id}</span>
              {country.code && (
                <span
                  className="shrink-0 rounded-sm border border-gray-700 bg-gray-800/60 px-1.5 py-0.5 text-[10px] font-medium tracking-wide text-gray-300"
                  title={countryTitle}
                >
                  {country.flag}<span className="ml-0.5">{country.code}</span>
                </span>
              )}
              <span className="text-sm font-medium text-gray-100 truncate">{country.name}</span>
              {isActive && (
                <span className="rounded-full bg-brand-50 dark:bg-brand-600/20 px-2 py-0.5 text-xs text-brand-400">
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
              <span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-blue-100 text-blue-700 border border-blue-200 dark:bg-blue-900/30 dark:text-blue-300 dark:border-blue-700/40 uppercase font-mono">
                {node.protocol}
              </span>
              {node.transport !== 'tcp' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-green-100 text-green-700 border border-green-200 dark:bg-green-900/30 dark:text-green-300 dark:border-green-700/40 uppercase font-mono">
                  {node.transport}
                </span>
              )}
              {node.tls === 'reality' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-purple-100 text-purple-700 border border-purple-200 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-700/40 uppercase font-mono">
                  reality
                </span>
              )}
              {node.tls === 'tls' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-orange-100 text-orange-700 border border-orange-200 dark:bg-orange-900/30 dark:text-orange-300 dark:border-orange-700/40 uppercase font-mono">
                  tls
                </span>
              )}
              {node.chain_node_id && !node.chain_orphan && (
                <span className="text-xs text-blue-600 dark:text-blue-400 font-mono flex items-center gap-1">
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
                  className="text-xs text-red-700 dark:text-red-300 font-mono flex items-center gap-1 rounded-sm border border-red-200 dark:border-red-800/50 bg-red-50 dark:bg-red-950/30 px-1.5 py-0.5"
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
                      'inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[11px]',
                      s.failed
                        ? 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800/50 dark:bg-amber-950/30 dark:text-amber-300'
                        : s.stale
                        ? 'border-red-200 bg-red-50 text-red-700 dark:border-red-800/50 dark:bg-red-950/30 dark:text-red-300'
                        : 'border-gray-700 bg-gray-800/40 text-gray-300',
                    )}
                    title={s.failed
                      ? 'Last speed check failed — the node was unreachable or returned no throughput. Retry the speed test.'
                      : s.stale
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
                  className="inline-flex items-center gap-1 rounded-sm bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-yellow-700/40 px-1.5 py-0.5 text-[11px] text-yellow-700 dark:text-yellow-300"
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
        <div className="flex items-center gap-1 flex-wrap sm:flex-nowrap sm:shrink-0 sm:justify-end">
          {onSelect && (
            <button
              onClick={onSelect}
              title={isActive ? 'Active node' : 'Activate this node'}
              className={clsx(
                'rounded-sm p-1.5 transition-colors',
                isActive
                  ? 'bg-brand-50 text-brand-700 dark:bg-brand-600/20 dark:text-brand-300 ring-1 ring-brand-500/40'
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
              className="rounded-sm p-1.5 text-gray-500 hover:bg-gray-800 hover:text-green-400 transition-colors disabled:opacity-50"
            >
              <Activity className={clsx('h-4 w-4', checkLoading && 'animate-pulse')} />
            </button>
          )}
          {onReachability && (
            <button
              onClick={onReachability}
              title="Internet check — open google.com through this node"
              disabled={reachLoading}
              className="rounded-sm p-1.5 text-gray-500 hover:bg-gray-800 hover:text-sky-400 transition-colors disabled:opacity-50"
            >
              <Globe className={clsx('h-4 w-4', reachLoading && 'animate-pulse')} />
            </button>
          )}
          {onSpeedtest && (
            <button
              onClick={onSpeedtest}
              title="Speed test"
              disabled={speedLoading}
              className="rounded-sm p-1.5 text-gray-500 hover:bg-gray-800 hover:text-yellow-400 transition-colors disabled:opacity-50"
            >
              <Gauge className={clsx('h-4 w-4', speedLoading && 'animate-pulse')} />
            </button>
          )}
          {onEdit && (
            <button
              onClick={onEdit}
              title="Edit"
              className="rounded-sm p-1.5 text-gray-500 hover:bg-gray-800 hover:text-gray-100 transition-colors"
            >
              <Pencil className="h-4 w-4" />
            </button>
          )}
          {onExportUri && (
            <button
              onClick={onExportUri}
              title="Copy share URL"
              className="rounded-sm p-1.5 text-gray-500 hover:bg-gray-800 hover:text-gray-100 transition-colors"
            >
              <Link2 className="h-4 w-4" />
            </button>
          )}
          {onDelete && (
            <button
              onClick={onDelete}
              title="Delete"
              className="rounded-sm p-1.5 text-gray-500 hover:bg-gray-800 hover:text-red-400 transition-colors"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
