import { useT } from '@/hooks/useT'
import * as React from 'react'
import { useEffect, useMemo, useState } from 'react'
import { Plus, Activity, Search, GripVertical, Gauge, FileDown, FileUp, Pin, ArrowDownNarrowWide, ArrowUpNarrowWide, Timer } from 'lucide-react'
import { clsx } from 'clsx'
import { useQueryClient, useMutation } from '@tanstack/react-query'
import {
  useNodes,
  useNodesPage,
  useActiveNode,
  useCreateNode,
  useUpdateNode,
  useDeleteNode,
  useCheckNodeHealth,
  useCheckAllNodes,
  useSpeedtestStream,
  useReachability,
  useSpeedtestAll,
  useAutocheckSweep,
  useSpeedResults,
  useSpeedPending,
} from '@/hooks/useNodes'
import { nodesApi } from '@/api/client'
import { useSystemStatus, useSetActiveNode } from '@/hooks/useSystem'
import { NodeCard } from '@/components/NodeCard'
import { NodeForm } from '@/components/NodeForm'
import { UriImport } from '@/components/UriImport'
import { NaiveSidecarPanel } from '@/components/NaiveSidecarPanel'
import { NodeFilterPopup, type NodeFilterState } from '@/components/NodeFilterPopup'
import { Pagination } from '@/components/Pagination'
import { useConfirm } from '@/components/ConfirmModal'
import { copyToClipboard } from '@/lib/clipboard'
import { ModalShell } from '@/components/ModalShell'
import { AutoCheckModal } from '@/components/AutoCheckModal'
import { apiErrorText } from '@/lib/apiError'
import type { Node, NodeCreate, NodePageParams } from '@/types'

type Modal = 'none' | 'add' | 'edit' | 'import'

// Canonical protocol set surfaced in the filter popup. Until we add a
// proper facets endpoint (1.3.4-backlog), hardcoding this keeps the
// chip strip stable regardless of which page the user is on.
const KNOWN_PROTOCOLS = ['vless', 'vmess', 'trojan', 'ss', 'wireguard', 'socks', 'hy2', 'naive']

// Page size persistence. localStorage so the choice survives reloads —
// session storage would reset on browser restart, which annoys users
// with a large subscription who picked 100/page for a reason.
const PAGE_SIZE_KEY = 'pitun.nodesPageSize'
const PAGE_SIZE_DEFAULT = 50
const PAGE_SIZE_OPTIONS = [10, 50, 100]

function loadPageSize(): number {
  try {
    const raw = localStorage.getItem(PAGE_SIZE_KEY)
    if (!raw) return PAGE_SIZE_DEFAULT
    const n = Number(raw)
    return PAGE_SIZE_OPTIONS.includes(n) ? n : PAGE_SIZE_DEFAULT
  } catch {
    return PAGE_SIZE_DEFAULT
  }
}

// Sort direction (tiebreaker on Node.id). Default 'desc' = newest
// first, which matches what a user expects after a subscription
// pulls 1k+ rows — see top of file rationale.
const DIRECTION_KEY = 'pitun.nodesDirection'
type SortDirection = 'asc' | 'desc'

function loadDirection(): SortDirection {
  try {
    const raw = localStorage.getItem(DIRECTION_KEY)
    return raw === 'asc' ? 'asc' : 'desc'
  } catch {
    return 'desc'
  }
}

export function Nodes() {
  const t = useT()
  const confirm = useConfirm()
  const [modal, setModal] = useState<Modal>('none')
  const [showAutocheck, setShowAutocheck] = useState(false)
  const [editNode, setEditNode] = useState<Node | null>(null)
  const [search, setSearch] = useState('')
  const [filters, setFilters] = useState<NodeFilterState>({})
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState<number>(loadPageSize)
  const [direction, setDirection] = useState<SortDirection>(loadDirection)

  // Reset to first page whenever the result set changes shape — any
  // filter / search / page-size flip can invalidate the current
  // offset (e.g. were on page 12 of 24, then filter brings total down
  // to 3 → page 12 is empty).
  useEffect(() => {
    setPage(1)
  }, [
    search,
    filters.subscription_id, filters.local, filters.protocol,
    filters.online, filters.group, pageSize,
  ])

  // Persist page-size choice. Storage write fires only on actual change
  // (the loader pulls on mount, the setter writes on flip).
  useEffect(() => {
    try { localStorage.setItem(PAGE_SIZE_KEY, String(pageSize)) } catch { /* quota / private mode */ }
  }, [pageSize])

  // Sort direction also persisted across reloads.
  useEffect(() => {
    try { localStorage.setItem(DIRECTION_KEY, direction) } catch { /* same */ }
  }, [direction])

  const pageParams: NodePageParams = useMemo(() => ({
    limit: pageSize,
    offset: (page - 1) * pageSize,
    search: search || undefined,
    subscription_id: filters.subscription_id,
    local: filters.local,
    protocol: filters.protocol,
    online: filters.online,
    group: filters.group,
    direction,
  }), [page, pageSize, search, filters, direction])

  const { data: pageData, isLoading } = useNodesPage(pageParams)
  const nodes = pageData?.items ?? []
  const total = pageData?.total ?? 0

  // Drag-to-reorder gate. Reorder runs **within the currently visible
  // subset** — the user drags nodes they can see, and the backend
  // updates the `order` field on those rows only. Nodes outside the
  // view keep their existing order (interleaving may shift slightly
  // but the relative ordering of the dragged set is preserved).
  //
  // Conditions:
  //   * At least 2 visible nodes (1 row has nothing to reorder against).
  //   * Visible count ≤ 100 — scrolling 100 cards trying to drag is
  //     already painful UX; beyond that we point users at the Edit
  //     modal's explicit `order` field.
  //
  // We DON'T gate on filters or pagination anymore — `total <= 100`
  // was an overly conservative rule that made a 1256-node subscription
  // un-reorderable even when the user had narrowed the view to 20 of
  // them via filters.
  const canDrag = nodes.length >= 2 && nodes.length <= 100
  // Keep the unbounded list around for the chain-via dropdown in
  // NodeForm — that needs to reference Nodes on other pages. The
  // reorder handler no longer relies on it (uses visible subset).
  const { data: allNodesForReorder = [] } = useNodes()

  const { data: status } = useSystemStatus()
  // Active node — fetched independently so it survives pagination
  // and filtering. If the operator filters out the subscription that
  // owns the active node, we still want it pinned at the top so they
  // never lose sight of which Node traffic is flowing through.
  const { data: activeNode } = useActiveNode(status?.active_node_id ?? null)
  const showActivePin =
    activeNode != null && !nodes.some((n) => n.id === activeNode.id)

  const createNode = useCreateNode()
  const updateNode = useUpdateNode()
  const deleteNode = useDeleteNode()
  const checkHealth = useCheckNodeHealth()
  const checkAll = useCheckAllNodes()
  const speedStream = useSpeedtestStream()
  const reachability = useReachability()
  const setActive = useSetActiveNode()

  // Results and in-flight ids come from the query cache so they survive
  // pagination, filtering and leaving the page mid-test.
  const { data: speedResults } = useSpeedResults()
  const { data: speedPending } = useSpeedPending()
  const [dragId, setDragId] = useState<number | null>(null)
  const qc = useQueryClient()

  const speedAll = useSpeedtestAll()
  // True while a background sweep (auto-check or "Speed All") runs — drives
  // the button spinner + refreshes node rows live.
  const sweeping = useAutocheckSweep()

  // Reorder via useMutation so it follows the same pattern as other CRUD
  // operations (toast hooks, optimistic update later, error surface via
  // mutation.error). Previously was a bare `.then(invalidateQueries)` that
  // silently swallowed failures.
  const reorderNodes = useMutation({
    mutationFn: (ids: number[]) => nodesApi.reorder(ids),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nodes'] }),
  })

  const handleSave = (data: NodeCreate) => {
    if (editNode) {
      updateNode.mutate({ id: editNode.id, data }, { onSuccess: () => setModal('none') })
    } else {
      createNode.mutate(data, { onSuccess: () => setModal('none') })
    }
  }

  // Streaming test: writes live progress into the query cache itself, so it
  // ticks in place and survives leaving the page.
  const handleSpeedtest = (node: Node) => {
    if (speedPending.includes(node.id)) return
    speedStream.run(node.id)
  }

  const handleReachability = (node: Node) => {
    if (speedPending.includes(node.id)) return
    reachability.run(node.id)
  }

  const handleExportUri = async (node: Node) => {
    try {
      const uri = await nodesApi.uri(node.id)
      await copyToClipboard(uri)
    } catch {
      /* protocol without a URL form (e.g. wireguard) — nothing to copy */
    }
  }

  const handleDrop = (targetId: number) => {
    if (dragId === null || dragId === targetId) return
    // Reorder runs on the **visible subset** — we send only the IDs
    // currently rendered, in their new order. The backend rewrites
    // `order` (steps of 10) on those rows; other rows keep their
    // existing values. Relative ordering of the dragged set is what
    // the user actually wants — minor interleaving with non-visible
    // rows is acceptable. See note on `canDrag` above.
    const ids = nodes.map((n) => n.id)
    const fromIndex = ids.indexOf(dragId)
    const toIndex = ids.indexOf(targetId)
    if (fromIndex === -1 || toIndex === -1) return
    ids.splice(fromIndex, 1)
    ids.splice(toIndex, 0, dragId)
    setDragId(null)
    reorderNodes.mutate(ids)
  }

  // Guard rail: Test-All / Speed-All operate on every ENABLED Node
  // in the database — with 1k+ subscription rows that's hours of
  // sequential xray spawns. Threshold of 50 mirrors the default page
  // size; above that we ask the operator to confirm with the actual
  // count surfaced. Backend tracks "enabled" so `total` from the
  // current paginated view is a close enough proxy without firing a
  // second query.
  const BULK_CONFIRM_THRESHOLD = 50

  const runTestAll = async () => {
    if (total > BULK_CONFIRM_THRESHOLD) {
      const ok = await confirm({
        title: t(`Test all ${total} nodes?`, `Проверить все ${total} нод?`),
        body: t(
          `Health-check fires sequentially through every enabled node. With ${total} nodes this can take ${Math.ceil(total * 2 / 60)}+ minutes. Active traffic isn't affected.`,
          `Проверка идёт последовательно по всем включённым нодам. Для ${total} нод это займёт ${Math.ceil(total * 2 / 60)}+ минут. Активный трафик не затрагивается.`,
        ),
        confirmLabel: t('Run health check', 'Запустить проверку'),
      })
      if (!ok) return
    }
    checkAll.mutate()
  }

  const runSpeedAll = async () => {
    if (total > BULK_CONFIRM_THRESHOLD) {
      const ok = await confirm({
        title: t(`Speed-test all ${total} nodes?`, `Замерить скорость всех ${total} нод?`),
        body: t(
          `Runs the same background sweep as Auto-checks — one node at a time (~10–30s each); results appear live on the cards. With ${total} nodes the full sweep can take a while and saturate the uplink, but it keeps running in the background even if you leave the page.`,
          `Запускает тот же фоновый проход, что и Автопроверки — по одной ноде (~10–30с каждая); результаты появляются на карточках вживую. Для ${total} нод полный проход может занять время и загрузить канал, но он продолжится в фоне, даже если уйти со страницы.`,
        ),
        confirmLabel: t('Run speed test', 'Запустить тест'),
        danger: true,
      })
      if (!ok) return
    }
    speedAll.mutate()
  }

  return (
    <div className="p-4 sm:p-6 space-y-5">
      {/* Header — title on its own line on phones; action buttons
          wrap to multiple rows below it (same pattern as the Servers
          page so the discovery affordance stays consistent — a
          horizontally-scrollable strip wasn't obvious enough). On
          wider screens everything collapses back to one row via
          flex-wrap's natural single-line behaviour. */}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <h1 className="text-xl font-bold text-gray-100">{t('Nodes', 'Ноды')}</h1>
        <div className="flex items-center gap-2 flex-wrap">
          <button
            onClick={runTestAll}
            disabled={checkAll.isPending}
            className="flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
          >
            <Activity className={clsx('h-4 w-4', checkAll.isPending && 'animate-pulse')} />
            {t('Test All', 'Проверить все')}
          </button>
          <button
            onClick={runSpeedAll}
            disabled={speedAll.isPending || sweeping}
            className="flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
          >
            <Gauge className={clsx('h-4 w-4', (speedAll.isPending || sweeping) && 'animate-spin')} />
            {(speedAll.isPending || sweeping) ? t('Testing…', 'Проверка…') : t('Speed All', 'Скорость всех')}
          </button>
          <button
            onClick={() => setShowAutocheck(true)}
            className="flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-300 hover:bg-gray-700 transition-colors"
            title={t('Auto speed-checks — schedule background speed tests', 'Автопроверки скорости — фоновые тесты по расписанию')}
          >
            <Timer className="h-4 w-4" />
            {t('Auto-checks', 'Автопроверки')}
          </button>
          {/* Unified Import — paste URIs OR drop a PiTun JSON bundle;
              the modal auto-detects which path to take. */}
          <button
            onClick={() => setModal('import')}
            className="flex items-center gap-1.5 rounded-lg bg-gray-700 px-3 py-2 text-sm text-gray-200 hover:bg-gray-600 transition-colors"
            title={t('Import nodes — paste URIs or upload a PiTun JSON bundle; format is auto-detected', 'Импорт нод — вставьте URI или загрузите PiTun JSON; формат определяется автоматически')}
          >
            <FileUp className="h-4 w-4" />
            {t('Import', 'Импорт')}
          </button>
          {/* Export dropdown — pick between URI-list (.txt) and
              full-fidelity JSON bundle (.json). */}
          <NodesJsonIO />
          <button
            onClick={() => { setEditNode(null); setModal('add') }}
            className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors"
          >
            <Plus className="h-4 w-4" />
            {t('Add Node', 'Добавить ноду')}
          </button>
        </div>
      </div>

      {/* Filters row — search input + FilterPopup (covers subscription /
          protocol / status / group). The protocol-chips-strip we used
          to have here is folded into the popup. */}
      <div className="flex items-center gap-3 flex-wrap">
        <div className="relative flex-1 min-w-0 sm:min-w-48 w-full sm:w-auto">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-500" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('Search nodes…', 'Поиск нод…')}
            className="w-full rounded-lg bg-gray-900 border border-gray-800 pl-9 pr-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
          />
        </div>
        <NodeFilterPopup
          value={filters}
          onChange={setFilters}
          protocols={KNOWN_PROTOCOLS}
        />
        {/* Sort-direction toggle. Single button that flips between
            'newest first' (default — natural for subscription pulls)
            and 'oldest first'. The `Node.order` column stays primary
            so drag-to-reorder placement still wins regardless of
            which direction is selected. Choice persists to
            localStorage. */}
        <button
          type="button"
          onClick={() => setDirection((d) => (d === 'desc' ? 'asc' : 'desc'))}
          className="flex items-center gap-1.5 rounded-lg border border-gray-800 bg-gray-900 px-2.5 py-2 text-sm text-gray-400 hover:text-gray-200 hover:border-gray-700 transition-colors"
          title={direction === 'desc' ? t('Newest IDs first — click to reverse', 'Сначала новые ID — клик, чтобы развернуть') : t('Oldest IDs first — click to reverse', 'Сначала старые ID — клик, чтобы развернуть')}
          aria-label={direction === 'desc' ? t('Sort newest first', 'Сначала новые') : t('Sort oldest first', 'Сначала старые')}
        >
          {direction === 'desc'
            ? <ArrowDownNarrowWide className="h-4 w-4" />
            : <ArrowUpNarrowWide className="h-4 w-4" />}
          <span className="hidden sm:inline text-xs">
            {direction === 'desc' ? t('Newest', 'Новые') : t('Oldest', 'Старые')}
          </span>
        </button>
      </div>

      {/* Speed All runs synchronously and the reverse proxy cuts the
          request at 120s, so on a large node set it WILL fail. Without
          this banner the spinner just stopped and the operator was left
          wondering whether anything happened. */}
      {speedAll.isError && (
        <div className="rounded-lg border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-950/30 px-3 py-2 text-xs text-red-700 dark:text-red-300">
          Speed All failed: {apiErrorText(speedAll.error, 'request timed out')}.
          Results for individual nodes are still available via the per-node
          speed test.
        </div>
      )}

      {/* Pinned active node — shown only when the active node is NOT
          already in the current page (filters/pagination would hide
          it). Surfaces "which Node am I currently routing through"
          regardless of how the operator narrowed the list. */}
      {showActivePin && activeNode && (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-brand-400">
            <Pin className="h-3 w-3" />
            <span>{t('Active node (pinned)', 'Активная нода (закреплена)')}</span>
          </div>
          <div>
            <div className="flex items-start">
              {/* No drag handle / grip column — the pinned card sits
                  outside the reorderable list, and the card's own active
                  border is the single highlight (no outer ring). */}
              <div className="flex-1 min-w-0">
                <NodeCard
                  node={activeNode}
                  isActive={true}
                  onEdit={() => { setEditNode(activeNode); setModal('edit') }}
                  onDelete={async () => {
                    const ok = await confirm({
                      title: `Delete "${activeNode.name}"?`,
                      body: t('This node is currently active. Routing rules pointing at it will be left dangling.', 'Эта нода сейчас активна. Правила маршрутизации на неё останутся «висячими».'),
                      confirmLabel: t('Delete', 'Удалить'),
                      danger: true,
                    })
                    if (ok) deleteNode.mutate(activeNode.id)
                  }}
                  onCheck={() => checkHealth.mutate(activeNode.id)}
                  onSpeedtest={() => handleSpeedtest(activeNode)}
                  onReachability={() => handleReachability(activeNode)}
                  onExportUri={() => handleExportUri(activeNode)}
                  onSelect={() => setActive.mutate(activeNode.id)}
                  checkLoading={checkHealth.isPending && checkHealth.variables === activeNode.id}
                  speedLoading={speedPending.includes(activeNode.id)}
                  reachLoading={speedPending.includes(activeNode.id)}
                />
                {/* The pinned card is only rendered when the active node
                    is NOT in the list, so without this row its speed-test
                    result had nowhere to appear at all. */}
                {speedResults[activeNode.id] && (
                  <div className="text-xs text-gray-500 mt-1 pl-4 font-mono">
                    Speed: {speedResults[activeNode.id]}
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Node list */}
      {isLoading && !pageData ? (
        <div className="text-center py-12 text-gray-500">Loading…</div>
      ) : nodes.length === 0 ? (
        <div className="text-center py-12 text-gray-500">
          {total === 0 && !search && Object.values(filters).every((v) => v == null || v === '')
            ? 'No nodes yet. Add or import nodes to get started.'
            : 'No nodes match the current filter.'}
        </div>
      ) : (
        <div className="grid gap-3">
          {nodes.map((node) => (
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
              {/* Drag handle desktop-only — HTML5 DnD doesn't fire on
                  touch events, so showing the grip on mobile would
                  imply broken functionality. Mobile users reorder
                  via the Edit modal's `order` field for now;
                  full touch DnD is on the post-1.3.0 backlog.

                  Drag is also gated by filters/pagination state since
                  v1.3.3 — reordering a paginated subset would scramble
                  off-page rows. The tooltip below tells the user why
                  the handle is muted when those conditions aren't met. */}
              <div
                className={clsx('mt-3 shrink-0 hidden sm:block', canDrag ? 'cursor-grab text-gray-600 hover:text-gray-400' : 'text-gray-800 cursor-not-allowed')}
                title={canDrag
                  ? 'Drag to reorder (within visible set)'
                  : nodes.length > 100
                    ? 'Reorder disabled: 100+ visible nodes. Filter or use the Edit modal order field.'
                    : 'Reorder needs at least 2 visible nodes.'}
              >
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
                      body: t('Routing rules pointing at this node will be left dangling — fix them after deletion.', 'Правила маршрутизации на эту ноду останутся «висячими» — поправьте их после удаления.'),
                      confirmLabel: t('Delete', 'Удалить'),
                      danger: true,
                    })
                    if (ok) deleteNode.mutate(node.id)
                  }}
                  onCheck={() => checkHealth.mutate(node.id)}
                  onSpeedtest={() => handleSpeedtest(node)}
                  onReachability={() => handleReachability(node)}
                  onExportUri={() => handleExportUri(node)}
                  onSelect={() => setActive.mutate(node.id)}
                  checkLoading={checkHealth.isPending && checkHealth.variables === node.id}
                  speedLoading={speedPending.includes(node.id)}
                  reachLoading={speedPending.includes(node.id)}
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

      {/* Pagination — always rendered when there's any result so the
          page-size picker stays accessible even on a 5-node install.
          The strip + prev/next collapse when there's only one page. */}
      {(total > 0) && (
        <Pagination
          page={page}
          pageSize={pageSize}
          total={total}
          pageSizeOptions={PAGE_SIZE_OPTIONS}
          onPageChange={setPage}
          onPageSizeChange={setPageSize}
          itemLabel="nodes"
        />
      )}

      {/* Add / Edit modal */}
      {(modal === 'add' || modal === 'edit') && (
        <Modal title={modal === 'add' ? 'Add Node' : 'Edit Node'} onClose={() => setModal('none')}>
          <NodeForm
            initial={editNode ?? undefined}
            onSave={handleSave}
            onCancel={() => setModal('none')}
            loading={createNode.isPending || updateNode.isPending}
            // Pass the unbounded list so the chain-via dropdown can
            // reference Nodes on other pages. The 1k-row query is
            // cached at the page level (refetched once per minute).
            nodes={allNodesForReorder}
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

      {/* Auto-checks config */}
      {showAutocheck && (
        <AutoCheckModal nodes={allNodesForReorder} onClose={() => setShowAutocheck(false)} />
      )}
    </div>
  )
}

function Modal({ title, children, onClose }: { title: string; children: React.ReactNode; onClose: () => void }) {
  return (
    <ModalShell onClose={onClose} labelledBy="nodes-modal-title">
      {/* `w-full` here would resolve against ModalShell's
          stopPropagation wrapper, which has no width and lets the
          flex parent shrink the child to its content's natural
          width — observed as a ~360px-wide Import dialog on a 1080p
          screen. `w-[min(92vw,42rem)]` clamps to a sane max but
          guarantees at least 92% of the viewport on phones. */}
      <div className="w-[min(92vw,42rem)] max-h-[90vh] overflow-y-auto rounded-2xl bg-gray-950 border border-gray-800 p-6">
        <h2 id="nodes-modal-title" className="text-base font-semibold text-gray-100 mb-5">{title}</h2>
        {children}
      </div>
    </ModalShell>
  )
}


// ── Export dropdown (URIs / JSON) ───────────────────────────────────────────
//
// Single "Export" button that opens a tiny popover with two choices:
//   * Export as URI list (.txt) — one `vless://…` per line, the
//     shareable format every v2rayN-compatible client understands.
//   * Export as JSON bundle (.json) — full-fidelity round-trip with
//     protocol-specific fields (WG / Hysteria / Naive padding…).
//
// Import is unified into the existing UriImport modal (with auto-
// detection of pasted/uploaded format), so this file no longer
// owns an import button — see Nodes.tsx for the wire-up.

function NodesJsonIO() {
  const [open, setOpen] = React.useState(false)
  const menuRef = React.useRef<HTMLDivElement | null>(null)

  // Click-outside dismiss. Mounting a global listener while the menu
  // is open is cheaper than a portaled overlay and matches what the
  // other dropdowns in the project do.
  React.useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      // The unqualified `Node` collides with the project's own
      // Node ORM type imported from `@/types` — qualify against
      // `globalThis.Node` so TS picks the DOM definition.
      const target = e.target as globalThis.Node | null
      if (menuRef.current && target && !menuRef.current.contains(target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  const runExport = async (which: 'uris' | 'json') => {
    setOpen(false)
    try {
      if (which === 'uris') await nodesApi.exportURIs()
      else await nodesApi.exportJSON()
    } catch (err: unknown) {
      alert('Export failed: ' + (err instanceof Error ? err.message : String(err)))
    }
  }

  return (
    <div ref={menuRef} className="relative">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-lg bg-gray-700 px-3 py-2 text-sm text-gray-200 hover:bg-gray-600 transition-colors"
        title="Download nodes as a file (.txt URIs or .json bundle)"
      >
        <FileDown className="h-4 w-4" />
        Export
      </button>
      {open && (
        <div className="absolute right-0 mt-1 w-64 rounded-lg border border-gray-700 bg-gray-900 shadow-lg z-50 overflow-hidden">
          <button
            onClick={() => runExport('uris')}
            className="w-full text-left px-3 py-2 hover:bg-gray-800 text-sm text-gray-200 border-b border-gray-800"
          >
            <div className="font-medium">URI list (.txt)</div>
            <div className="text-[11px] text-gray-500 mt-0.5">
              Shareable: one vless://… per line. WG nodes skipped.
            </div>
          </button>
          <button
            onClick={() => runExport('json')}
            className="w-full text-left px-3 py-2 hover:bg-gray-800 text-sm text-gray-200"
          >
            <div className="font-medium">JSON bundle (.json)</div>
            <div className="text-[11px] text-gray-500 mt-0.5">
              Full-fidelity backup — every field, including WG / Hysteria.
            </div>
          </button>
        </div>
      )}
    </div>
  )
}
