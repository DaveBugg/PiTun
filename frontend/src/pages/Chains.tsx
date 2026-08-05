import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  GitBranch, Loader2, AlertTriangle, RefreshCw, Plus, Trash2, X, Copy, Check,
  Users, Upload, ChevronRight, Activity, CheckCircle2, XCircle, QrCode,
} from 'lucide-react'

import { xuiApi } from '@/api/client'
import { useT } from '@/hooks/useT'
import { useConfirm } from '@/components/ConfirmModal'
import { ModalShell } from '@/components/ModalShell'
import { ClientQrModal } from '@/components/ClientQrModal'
import { DirectToggle } from '@/components/DirectToggle'
import { copyToClipboard } from '@/lib/clipboard'
import type {
  ChainClientRead,
  ChainCreateChannelInput,
  ChainRead,
  XuiServer,
} from '@/types'

/**
 * Two-hop proxy chain management page (since v1.3.0-beta.7).
 *
 * Wires together two registered XuiServer panels — an EU "exit" and
 * a RU "relay" — into a single chain with N user-defined channels.
 * Each channel is a fully-isolated VLESS+Reality pipe; one chain
 * client → N panel-side clients (one per channel) → N exportable
 * Node rows.
 *
 * OPSEC: no hardcoded SNI / channel-name suggestions. Operator
 * picks their own masquerade targets per their threat model.
 */
export default function ChainsPage() {
  const t = useT()
  const qc = useQueryClient()

  const { data: chains = [], isLoading: chainsLoading, error: chainsError } =
    useQuery<ChainRead[]>({
      queryKey: ['xui', 'chains'],
      // 404 from /xui/chains means the backend predates beta.7 (the
      // endpoint doesn't exist at all). That's not a "load failed" —
      // it's a "you haven't deployed the new backend yet" situation,
      // and the right UX is the same EmptyState as "no chains yet".
      // Treat any non-200 EXCEPT 404 as a real error.
      queryFn: async () => {
        try {
          return await xuiApi.listChains()
        } catch (e: unknown) {
          const status = (e as { response?: { status?: number } })?.response?.status
          if (status === 404) return []
          throw e
        }
      },
      refetchOnWindowFocus: false,
      // Don't hammer a missing endpoint with the default 3 retries.
      retry: (failureCount, err: unknown) => {
        const status = (err as { response?: { status?: number } })?.response?.status
        if (status === 404 || status === 401 || status === 403) return false
        return failureCount < 2
      },
    })

  const { data: servers = [], isLoading: serversLoading } = useQuery<XuiServer[]>({
    queryKey: ['xui', 'servers'],
    queryFn: () => xuiApi.listServers(),
    staleTime: 30_000,
  })

  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [showCreate, setShowCreate] = useState(false)
  // Page-level "Direct connection" — every chain op (create, healthcheck,
  // add/delete client, export, delete) dials the panels directly (SO_MARK
  // bypass) when on; off = through the active node (default).
  const [direct, setDirect] = useState(false)

  useEffect(() => {
    if (selectedId == null && chains.length > 0) {
      setSelectedId(chains[0].id)
    }
  }, [chains, selectedId])

  const selected = chains.find((c) => c.id === selectedId) ?? null

  // Need ≥2 x-ui servers to build a chain — guard the "Create" button.
  const canCreate = servers.length >= 2

  return (
    <div className="p-4 md:p-6">
      <header className="mb-5 flex flex-wrap items-center gap-3">
        <GitBranch className="h-6 w-6 text-brand-400" />
        <h1 className="text-2xl font-semibold text-gray-100">
          {t('Chains', 'Цепочки')}
        </h1>
        <p className="text-sm text-gray-500 hidden md:block">
          {t(
            'Two-hop proxy chains across two x-ui panels — exit + relay',
            'Двухзвенные прокси-цепочки на двух x-ui панелях — exit + relay',
          )}
        </p>
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          <DirectToggle checked={direct} onChange={setDirect} className="px-1" />
          <button
            type="button"
            onClick={() => qc.invalidateQueries({ queryKey: ['xui', 'chains'] })}
            className="rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800 transition-colors flex items-center gap-1.5"
          >
            <RefreshCw className="h-4 w-4" />
            {t('Refresh', 'Обновить')}
          </button>
          <button
            type="button"
            onClick={() => setShowCreate(true)}
            disabled={!canCreate}
            title={canCreate ? '' : t(
              'Need at least 2 x-ui panels — go to Servers and deploy one more.',
              'Нужно минимум 2 панели x-ui — деплойни ещё одну на Servers.',
            )}
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors flex items-center gap-1.5"
          >
            <Plus className="h-4 w-4" />
            {t('Create chain', 'Создать цепочку')}
          </button>
        </div>
      </header>

      <div className="space-y-4">
        {(chainsLoading || serversLoading) && (
          <div className="rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-6 flex items-center justify-center gap-2 text-sm text-gray-500">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t('Loading…', 'Загрузка…')}
          </div>
        )}

        {chainsError && (
          <div className="rounded-lg border border-red-200 dark:border-red-700/40 bg-red-50 dark:bg-red-900/20 px-3 py-3 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
            <span>{t('Failed to load chains', 'Не удалось загрузить цепочки')}</span>
          </div>
        )}

        {!chainsLoading && !chainsError && chains.length === 0 && (
          <EmptyState canCreate={canCreate} onCreate={() => setShowCreate(true)} />
        )}

        {chains.length > 0 && (
          <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">
            <ChainList
              chains={chains}
              selectedId={selectedId}
              onSelect={setSelectedId}
            />
            {selected && (
              <ChainDetail key={selected.id} chain={selected} direct={direct} />
            )}
          </div>
        )}
      </div>

      {showCreate && (
        <CreateChainModal
          servers={servers}
          direct={direct}
          onClose={() => setShowCreate(false)}
          onCreated={(c) => {
            setShowCreate(false)
            setSelectedId(c.id)
            qc.invalidateQueries({ queryKey: ['xui', 'chains'] })
          }}
        />
      )}
    </div>
  )
}


// ── Empty state ─────────────────────────────────────────────────────────────

function EmptyState({ canCreate, onCreate }: { canCreate: boolean; onCreate: () => void }) {
  const t = useT()
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 px-4 py-6">
      <div className="flex items-start gap-3">
        <GitBranch className="h-5 w-5 text-gray-500 mt-0.5" />
        <div className="flex-1">
          <div className="font-medium text-gray-200">
            {t('No chains yet', 'Цепочек пока нет')}
          </div>
          <p className="text-xs text-gray-500 mt-1 leading-snug">
            {canCreate
              ? t(
                  'Create a chain to wire two x-ui panels together. The exit panel takes the masked traffic out to the internet; the relay panel is what your clients dial.',
                  'Создайте цепочку — она свяжет две панели x-ui. Exit-панель выпускает замаскированный трафик в интернет, к relay-панели подключаются клиенты.',
                )
              : t(
                  'Chains need 2+ x-ui panels. Go to Servers → Deploy → "x-ui" on two VPS instances first.',
                  'Цепочкам нужно ≥2 панели x-ui. Сначала задеплой через Servers → Deploy → "x-ui" на два VPS.',
                )}
          </p>
          {canCreate && (
            <button
              type="button"
              onClick={onCreate}
              className="mt-3 rounded-lg bg-brand-600 hover:bg-brand-500 px-3 py-1.5 text-xs text-white font-medium inline-flex items-center gap-1.5"
            >
              <Plus className="h-3.5 w-3.5" />
              {t('Create chain', 'Создать цепочку')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}


// ── Chain list (left pane) ──────────────────────────────────────────────────

function ChainList({
  chains, selectedId, onSelect,
}: {
  chains: ChainRead[]
  selectedId: number | null
  onSelect: (id: number) => void
}) {
  const t = useT()
  return (
    <aside className="rounded-xl border border-gray-800 bg-gray-950/60 overflow-hidden">
      <header className="px-3 py-2 border-b border-gray-800 text-[11px] text-gray-500 uppercase tracking-wider">
        {t('Chains', 'Цепочки')}
      </header>
      <ul>
        {chains.map((c) => (
          <li key={c.id}>
            <button
              type="button"
              onClick={() => onSelect(c.id)}
              className={
                'w-full text-left px-3 py-2.5 flex items-start gap-2 transition-colors border-b border-gray-900 ' +
                (c.id === selectedId
                  ? 'bg-brand-50 dark:bg-brand-600/10 text-brand-700 dark:text-brand-200'
                  : 'text-gray-300 hover:bg-gray-900/40')
              }
            >
              <GitBranch className={
                'h-4 w-4 mt-0.5 shrink-0 ' +
                (c.id === selectedId ? 'text-brand-400' : 'text-gray-500')
              } />
              <div className="min-w-0 flex-1">
                <div className="font-medium truncate">{c.name}</div>
                <div className="text-[11px] text-gray-500 truncate">
                  {c.channels.length} {t('channels', 'каналов')}
                </div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <ChainStatusBadge status={c.status} />
                </div>
              </div>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  )
}

function ChainStatusBadge({ status }: { status: ChainRead['status'] }) {
  const cls = {
    deployed: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-700/40',
    pending: 'bg-yellow-50 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300 border-yellow-200 dark:border-yellow-700/40',
    degraded: 'bg-amber-50 dark:bg-amber-900/30 text-amber-700 dark:text-amber-300 border-amber-200 dark:border-amber-700/40',
    failed: 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 border-red-200 dark:border-red-700/40',
  }[status] ?? 'bg-gray-900 text-gray-400 border-gray-700'
  return (
    <span className={`text-[10px] px-1.5 py-0.5 rounded-sm font-mono border ${cls}`}>
      {status}
    </span>
  )
}


// ── Chain detail (right pane) ───────────────────────────────────────────────

function ChainDetail({ chain, direct }: { chain: ChainRead; direct: boolean }) {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()

  const { data: clients = [], isLoading: clientsLoading } = useQuery<ChainClientRead[]>({
    queryKey: ['xui', 'chains', chain.id, 'clients'],
    queryFn: () => xuiApi.listChainClients(chain.id),
    refetchOnWindowFocus: false,
  })

  const addClientMut = useMutation({
    mutationFn: () => xuiApi.addChainClient(chain.id, {}, direct),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['xui', 'chains', chain.id, 'clients'] })
    },
  })

  const delChainMut = useMutation({
    mutationFn: () => xuiApi.deleteChain(chain.id, direct),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['xui', 'chains'] })
    },
  })

  const delChannelMut = useMutation({
    mutationFn: (channelId: number) =>
      xuiApi.deleteChainChannel(chain.id, channelId, direct),
    onSuccess: () => {
      // Channel delete may also fold the chain away (last-channel
      // branch) — invalidate both lists so the UI reflects either
      // outcome without a manual refresh.
      qc.invalidateQueries({ queryKey: ['xui', 'chains'] })
      qc.invalidateQueries({ queryKey: ['xui', 'chains', chain.id, 'clients'] })
      qc.invalidateQueries({ queryKey: ['nodes'] })
    },
  })

  // Diagnostic sweep — reachability, xray state, inbound presence,
  // and the most-bitten check: relay's running config has the chain
  // outbound + matching routing rule. Result panel stays mounted
  // until the user closes or re-runs.
  const healthMut = useMutation({
    mutationFn: () => xuiApi.healthcheckChain(chain.id, direct),
  })

  return (
    <section className="rounded-xl border border-gray-800 bg-gray-950/60 p-4 space-y-3">
      <header className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-lg font-medium text-gray-100">{chain.name}</h2>
            <ChainStatusBadge status={chain.status} />
          </div>
          <div className="text-xs text-gray-500 mt-0.5 flex flex-wrap items-center gap-1.5">
            <span className="font-mono">{chain.relay_host || '?'}</span>
            <ChevronRight className="h-3 w-3 text-gray-600" />
            <span className="font-mono">{chain.exit_host || '?'}</span>
            <span className="text-gray-700 ml-1">·</span>
            <span>SNI cover: <span className="font-mono">{chain.exit_sni}</span></span>
          </div>
          {chain.last_error && (
            <div className="mt-2 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700/40 px-2 py-1 text-[11px] text-red-700 dark:text-red-300 flex items-start gap-1.5">
              <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
              <span>{chain.last_error}</span>
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => healthMut.mutate()}
            disabled={healthMut.isPending}
            className="rounded-lg border border-gray-700 hover:bg-brand-50 dark:hover:bg-brand-900/30 hover:border-brand-400/50 hover:text-brand-700 dark:hover:text-brand-300 px-2.5 py-1.5 text-xs text-gray-300 inline-flex items-center gap-1.5"
            title={t('Probe panels + running xray config for chain health', 'Проверить панели и running xray-config для здоровья цепочки')}
          >
            {healthMut.isPending
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Activity className="h-3.5 w-3.5" />}
            {t('Check health', 'Проверка')}
          </button>
          <button
            type="button"
            onClick={async () => {
              const ok = await confirm({
                title: t('Delete chain?', 'Удалить цепочку?'),
                body: t(
                  `Chain "${chain.name}" will be torn down on both panels (${chain.channels.length} inbounds on each). Existing clients stop working immediately.`,
                  `Цепочка "${chain.name}" будет снесена на обеих панелях (${chain.channels.length} инбаундов на каждой). Клиенты немедленно перестанут работать.`,
                ),
                confirmLabel: t('Delete', 'Удалить'),
                danger: true,
              })
              if (ok) delChainMut.mutate()
            }}
            disabled={delChainMut.isPending}
            className="rounded-lg border border-gray-700 hover:bg-red-50 dark:hover:bg-red-900/30 hover:border-red-700/40 hover:text-red-700 dark:hover:text-red-300 px-2.5 py-1.5 text-xs text-gray-300 inline-flex items-center gap-1.5"
          >
            {delChainMut.isPending
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Trash2 className="h-3.5 w-3.5" />}
            {t('Delete chain', 'Удалить цепочку')}
          </button>
        </div>
      </header>

      {/* Healthcheck result panel — shown after a probe runs.
          Each check carries its own colour so the bad rows pop. */}
      {(healthMut.data || healthMut.error) && (
        <div className={`rounded-lg border px-3 py-2 space-y-1 ${
          healthMut.data?.ok
            ? 'border-green-200 dark:border-green-700/40 bg-green-50 dark:bg-green-900/10'
            : 'border-yellow-200 dark:border-yellow-700/40 bg-yellow-50 dark:bg-yellow-900/10'
        }`}>
          <div className="flex items-center justify-between gap-2">
            <div className="text-[11px] font-medium uppercase tracking-wider">
              {healthMut.isPending
                ? t('Probing…', 'Проверка…')
                : healthMut.data?.ok
                  ? t('Healthy', 'Здорова')
                  : t('Issues found', 'Найдены проблемы')}
            </div>
            <button
              type="button"
              onClick={() => healthMut.reset()}
              className="text-gray-500 hover:text-gray-300 p-0.5"
              title={t('Dismiss', 'Скрыть')}
            >
              <X className="h-3 w-3" />
            </button>
          </div>
          {healthMut.error && (
            <div className="text-[11px] text-red-700 dark:text-red-300">
              {String((healthMut.error as Error).message)}
            </div>
          )}
          {healthMut.data?.checks.map((c, i) => (
            <div key={i} className="flex items-start gap-1.5 text-[11px]">
              {c.status === 'ok' ? (
                <CheckCircle2 className="h-3 w-3 mt-0.5 shrink-0 text-green-600 dark:text-green-400" />
              ) : c.status === 'warn' ? (
                <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0 text-yellow-600 dark:text-yellow-400" />
              ) : (
                <XCircle className="h-3 w-3 mt-0.5 shrink-0 text-red-600 dark:text-red-400" />
              )}
              <div className="min-w-0">
                <span className="text-gray-200">{c.name}</span>
                {c.detail && (
                  <span className="text-gray-500"> — <span className="font-mono">{c.detail}</span></span>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Channels */}
      <div className="space-y-2">
        <div className="text-[11px] uppercase tracking-wider text-gray-500">
          {t('Channels', 'Каналы')}
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {chain.channels.map((ch) => {
            const isDeletingThis =
              delChannelMut.isPending && delChannelMut.variables === ch.id
            const isLast = chain.channels.length === 1
            return (
              <div key={ch.id} className="rounded-lg border border-gray-800 bg-gray-900/40 p-3 flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="font-medium text-gray-100">{ch.name}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded-sm font-mono bg-gray-800 text-gray-400">
                      :{ch.relay_port} → :{ch.exit_port}
                    </span>
                  </div>
                  <div className="text-[11px] text-gray-500 mt-0.5 font-mono">
                    SNI: {ch.client_sni}
                  </div>
                </div>
                {/* Per-channel delete — drops both inbounds, closes
                    UFW ports, repushes the relay template without
                    this channel, and cascade-removes any exported
                    Nodes. The "last channel" case folds the whole
                    chain away (warns the operator first). */}
                <button
                  type="button"
                  onClick={async () => {
                    const ok = await confirm({
                      title: isLast
                        ? t('Delete the last channel? Chain will be removed.', 'Удалить последний канал? Цепочка тоже удалится.')
                        : t(`Delete channel "${ch.name}"?`, `Удалить канал «${ch.name}»?`),
                      body: isLast
                        ? t(
                            'This is the only channel left in this chain. Removing it tears down the whole chain — every client config and exported Node tied to this chain will stop working.',
                            'Это единственный канал в цепочке. После удаления вся цепочка снимется — все клиентские конфиги и экспортированные Nodes по этой цепочке перестанут работать.',
                          )
                        : t(
                            `Inbounds on both panels will be removed and any Nodes exported from "${ch.name}" will be cascade-deleted. Other channels in this chain stay untouched.`,
                            `Инбаунды на обеих панелях будут удалены, и Nodes, экспортированные из «${ch.name}», тоже снесутся каскадом. Остальные каналы цепочки не затронуты.`,
                          ),
                      confirmLabel: t('Delete', 'Удалить'),
                      danger: true,
                    })
                    if (ok) delChannelMut.mutate(ch.id)
                  }}
                  disabled={isDeletingThis}
                  title={t('Delete channel', 'Удалить канал')}
                  className="rounded-md border border-gray-700 hover:bg-red-50 dark:hover:bg-red-900/30 hover:border-red-700/40 hover:text-red-700 dark:hover:text-red-300 p-1 text-gray-500 disabled:opacity-50 shrink-0"
                >
                  {isDeletingThis
                    ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    : <Trash2 className="h-3.5 w-3.5" />}
                </button>
              </div>
            )
          })}
        </div>
      </div>

      {/* Clients */}
      <div className="space-y-2 pt-3 border-t border-gray-800">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <div className="text-[11px] uppercase tracking-wider text-gray-500 flex items-center gap-1.5">
            <Users className="h-3 w-3" />
            {t('Clients', 'Клиенты')}
            <span className="text-gray-600 ml-1">({clients.length})</span>
          </div>
          <button
            type="button"
            onClick={() => addClientMut.mutate()}
            disabled={addClientMut.isPending || chain.status !== 'deployed'}
            title={chain.status !== 'deployed'
              ? t(`Chain is ${chain.status} — wait until it's 'deployed'`, `Цепочка в статусе ${chain.status} — дождитесь 'deployed'`)
              : ''}
            className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 disabled:cursor-not-allowed px-3 py-1.5 text-xs text-white font-medium inline-flex items-center gap-1.5"
          >
            {addClientMut.isPending
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Plus className="h-3.5 w-3.5" />}
            {t('Add client', 'Добавить клиента')}
          </button>
        </div>

        {clientsLoading && (
          <div className="text-xs text-gray-500 flex items-center gap-1.5 py-2">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {t('Loading clients…', 'Загрузка клиентов…')}
          </div>
        )}

        {!clientsLoading && clients.length === 0 && (
          <div className="rounded-lg border border-dashed border-gray-700 px-4 py-6 text-center text-sm text-gray-500">
            {t('No clients yet — click "Add client".', 'Клиентов нет — нажмите "Добавить клиента".')}
          </div>
        )}

        {clients.map((cc) => (
          <ChainClientCard
            key={cc.id}
            chainId={chain.id}
            direct={direct}
            client={cc}
            onChanged={() => qc.invalidateQueries({
              queryKey: ['xui', 'chains', chain.id, 'clients'],
            })}
          />
        ))}
      </div>
    </section>
  )
}


// ── Chain client card ──────────────────────────────────────────────────────

function ChainClientCard({
  chainId, direct, client, onChanged,
}: {
  chainId: number
  direct: boolean
  client: ChainClientRead
  onChanged: () => void
}) {
  const t = useT()
  const confirm = useConfirm()
  const [expanded, setExpanded] = useState(false)
  const [copiedFor, setCopiedFor] = useState<number | null>(null)
  const [qrTarget, setQrTarget] = useState<{
    uri: string
    title: string
    subtitle: string
  } | null>(null)

  const delMut = useMutation({
    mutationFn: () => xuiApi.deleteChainClient(chainId, client.id, direct),
    onSuccess: onChanged,
  })
  const exportMut = useMutation({
    mutationFn: (channelIds: number[]) =>
      xuiApi.exportChainClientNodes(chainId, client.id, {
        channel_ids: channelIds,
      }, direct),
    onSuccess: onChanged,
  })

  const exportedCount = client.channels.filter((c) => c.exported_node_id != null).length

  const copy = async (uri: string, channelId: number) => {
    // `copyToClipboard` falls back to execCommand for insecure HTTP
    // contexts (PiTun UI is typically served over plain HTTP).
    if (await copyToClipboard(uri)) {
      setCopiedFor(channelId)
      setTimeout(() => setCopiedFor(null), 1500)
    }
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 overflow-hidden">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full text-left px-3 py-2.5 flex items-center gap-2 hover:bg-gray-900/60 transition-colors"
      >
        <Users className="h-4 w-4 text-gray-500 shrink-0" />
        <div className="min-w-0 flex-1">
          <div className="font-mono text-sm text-gray-100 truncate">{client.label}</div>
          <div className="text-[11px] text-gray-500 mt-0.5">
            {client.channels.length} {t('configs', 'конфигов')}
            {exportedCount > 0 && (
              <span className="ml-2">
                · {exportedCount} {t('exported to Nodes', 'экспортировано в Nodes')}
              </span>
            )}
          </div>
        </div>
        <ChevronRight className={
          'h-4 w-4 text-gray-500 shrink-0 transition-transform ' +
          (expanded ? 'rotate-90' : '')
        } />
      </button>

      {expanded && (
        <div className="border-t border-gray-800 p-3 space-y-2">
          {client.channels.map((cch) => (
            <div key={cch.id} className="space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-medium text-gray-200">{cch.channel_name}</span>
                {cch.exported_node_id != null && (
                  <span className="text-[10px] px-1.5 py-0.5 rounded-sm font-mono bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-700/40">
                    Node #{cch.exported_node_id}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1.5">
                <code className="flex-1 rounded-sm bg-gray-950 border border-gray-800 px-2 py-1 text-[11px] font-mono text-gray-300 break-all">
                  {cch.vless_uri}
                </code>
                <button
                  type="button"
                  onClick={() => setQrTarget({
                    uri: cch.vless_uri,
                    title: `${client.label} · ${cch.channel_name}`,
                    subtitle: t('Chain client config', 'Конфиг цепочки'),
                  })}
                  className="rounded-md border border-gray-700 hover:bg-brand-50 dark:hover:bg-brand-900/30 hover:border-brand-400/50 hover:text-brand-700 dark:hover:text-brand-300 p-1.5 text-gray-400 shrink-0"
                  title={t('Show QR code', 'Показать QR-код')}
                >
                  <QrCode className="h-3.5 w-3.5" />
                </button>
                <button
                  type="button"
                  onClick={() => copy(cch.vless_uri, cch.id)}
                  className="rounded-md border border-gray-700 hover:bg-gray-800 p-1.5 text-gray-400 hover:text-gray-200 shrink-0"
                  title={t('Copy VLESS URI', 'Копировать VLESS URI')}
                >
                  {copiedFor === cch.id
                    ? <Check className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" />
                    : <Copy className="h-3.5 w-3.5" />}
                </button>
              </div>
            </div>
          ))}
          <div className="flex items-center gap-2 pt-2 border-t border-gray-800">
            <button
              type="button"
              onClick={async () => {
                const ok = await confirm({
                  title: t('Delete chain client?', 'Удалить клиента цепочки?'),
                  body: t(
                    `All ${client.channels.length} per-channel configs for "${client.label}" will be removed from the relay panel.`,
                    `Все ${client.channels.length} конфигов для "${client.label}" будут удалены с relay-панели.`,
                  ),
                  confirmLabel: t('Delete', 'Удалить'),
                  danger: true,
                })
                if (ok) delMut.mutate()
              }}
              disabled={delMut.isPending}
              className="rounded-md border border-gray-700 hover:bg-red-50 dark:hover:bg-red-900/30 hover:border-red-700/40 hover:text-red-700 dark:hover:text-red-300 px-2 py-1 text-[11px] text-gray-400 inline-flex items-center gap-1"
            >
              {delMut.isPending
                ? <Loader2 className="h-3 w-3 animate-spin" />
                : <Trash2 className="h-3 w-3" />}
              {t('Delete', 'Удалить')}
            </button>
            <div className="flex-1" />
            <button
              type="button"
              onClick={() => exportMut.mutate(client.channels.map((c) => c.channel_id))}
              disabled={exportMut.isPending || exportedCount === client.channels.length}
              title={exportedCount === client.channels.length
                ? t('All channels already exported', 'Все каналы уже экспортированы')
                : ''}
              className="rounded-md bg-brand-600 hover:bg-brand-500 disabled:opacity-50 disabled:cursor-not-allowed px-2.5 py-1 text-[11px] text-white font-medium inline-flex items-center gap-1"
            >
              {exportMut.isPending
                ? <Loader2 className="h-3 w-3 animate-spin" />
                : <Upload className="h-3 w-3" />}
              {t('Export to Nodes', 'Экспортировать в Nodes')}
            </button>
          </div>
        </div>
      )}
      <ClientQrModal
        open={qrTarget !== null}
        onClose={() => setQrTarget(null)}
        title={qrTarget?.title || ''}
        subtitle={qrTarget?.subtitle}
        uri={qrTarget?.uri ?? null}
      />
    </div>
  )
}


// ── Create chain modal ──────────────────────────────────────────────────────

interface ChannelDraftRow extends ChainCreateChannelInput {
  /** Local stable id for React keys — not sent to backend. */
  _key: string
}

function makeEmptyChannel(): ChannelDraftRow {
  return {
    _key: Math.random().toString(36).slice(2),
    name: '',
    client_sni: '',
    exit_port: 0,
    relay_port: 0,
    exit_xhttp_path: '',
    relay_remark: '',
    exit_remark: '',
  }
}

function CreateChainModal({
  servers, direct, onClose, onCreated,
}: {
  servers: XuiServer[]
  direct: boolean
  onClose: () => void
  onCreated: (chain: ChainRead) => void
}) {
  const t = useT()
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [exitId, setExitId] = useState<number | null>(null)
  const [relayId, setRelayId] = useState<number | null>(null)
  const [exitSni, setExitSni] = useState('www.google.com')
  const [channels, setChannels] = useState<ChannelDraftRow[]>([makeEmptyChannel()])
  const [error, setError] = useState('')

  // Pick first two distinct panels as defaults so the form has a
  // valid starting state — user can override either dropdown.
  useEffect(() => {
    if (servers.length >= 2 && exitId == null && relayId == null) {
      setExitId(servers[0].id)
      setRelayId(servers[1].id)
    }
  }, [servers, exitId, relayId])

  const createMut = useMutation({
    mutationFn: () => xuiApi.createChain({
      name: name.trim(),
      exit_xui_server_id: exitId!,
      relay_xui_server_id: relayId!,
      exit_sni: exitSni.trim(),
      channels: channels.map((c) => ({
        name: c.name.trim(),
        client_sni: c.client_sni.trim(),
        exit_port: c.exit_port || 0,
        relay_port: c.relay_port || 0,
        exit_xhttp_path: c.exit_xhttp_path?.trim() || '',
        relay_remark: c.relay_remark?.trim() || '',
        exit_remark: c.exit_remark?.trim() || '',
      })),
    }, direct),
    onSuccess: onCreated,
    onError: (err: unknown) => {
      // A failed create still leaves a `failed` ProxyChain row behind
      // (deliberately — the UI must show it and offer a delete). With
      // refetchOnMount/refetchOnWindowFocus off, nothing would fetch it,
      // so the row stayed invisible and un-deletable.
      qc.invalidateQueries({ queryKey: ['xui', 'chains'] })
      let msg = 'Create failed'
      if (typeof err === 'object' && err !== null) {
        const e = err as { response?: { data?: { detail?: unknown } }, message?: string }
        const detail = e.response?.data?.detail
        if (typeof detail === 'string') msg = detail
        else if (e.message) msg = e.message
      }
      setError(msg)
    },
  })

  const validate = (): string | null => {
    if (!name.trim()) return t('Chain name is required', 'Имя цепочки обязательно')
    if (exitId == null) return t('Pick an exit panel', 'Выберите exit-панель')
    if (relayId == null) return t('Pick a relay panel', 'Выберите relay-панель')
    if (exitId === relayId) return t('Exit and relay must be different panels', 'Exit и relay — разные панели')
    if (!exitSni.trim()) return t('Exit SNI is required', 'Exit SNI обязателен')
    if (channels.length === 0) return t('At least one channel is required', 'Минимум один канал')
    const names = new Set<string>()
    for (const c of channels) {
      const n = c.name.trim()
      if (!n) return t('Channel name is required', 'Имя канала обязательно')
      if (!/^[a-zA-Z0-9_-]+$/.test(n)) return t(
        `Channel name "${n}" — letters, digits, _ and - only`,
        `Имя канала "${n}" — только буквы, цифры, _ и -`,
      )
      if (names.has(n)) return t(`Duplicate channel name: ${n}`, `Имя канала повторяется: ${n}`)
      names.add(n)
      if (!c.client_sni.trim()) return t(
        `Channel "${n}" — client SNI is required`,
        `Канал "${n}" — client SNI обязателен`,
      )
    }
    return null
  }

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const err = validate()
    if (err) { setError(err); return }
    setError('')
    createMut.mutate()
  }

  const updateChannel = (idx: number, patch: Partial<ChannelDraftRow>) => {
    setChannels((arr) => arr.map((c, i) => i === idx ? { ...c, ...patch } : c))
  }
  const addChannel = () => setChannels((arr) => [...arr, makeEmptyChannel()])
  const removeChannel = (idx: number) =>
    setChannels((arr) => arr.length > 1 ? arr.filter((_, i) => i !== idx) : arr)

  return (
    <ModalShell onClose={onClose} labelledBy="create-chain-title">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-3xl rounded-2xl bg-gray-950/95 border border-gray-800 p-6 m-4 max-h-[90vh] overflow-y-auto"
      >
        <h2 id="create-chain-title" className="text-lg font-semibold text-gray-100 mb-1">
          {t('Create chain', 'Создать цепочку')}
        </h2>
        <p className="text-xs text-gray-500 mb-4">
          {t(
            'Wires two registered x-ui panels into a two-hop pipe. Each channel is an independent VLESS+Reality tunnel with its own SNI cover.',
            'Связывает две зарегистрированные панели x-ui в двухзвенный туннель. Каждый канал — независимый VLESS+Reality с собственным SNI.',
          )}
        </p>

        {error && (
          <div className="mb-3 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/40 px-3 py-2 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {/* Identity */}
        <div className="space-y-3">
          <div>
            <label className="text-xs text-gray-400 block mb-1">
              {t('Chain name', 'Имя цепочки')}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-chain"
              className="w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
              autoFocus
            />
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto_1fr] gap-3 items-end">
            <div>
              <label className="text-xs text-gray-400 block mb-1">
                {t('Exit panel', 'Exit-панель')}
              </label>
              <PanelPicker
                servers={servers}
                value={exitId}
                onChange={(id) => {
                  // Picking the panel currently in the other slot
                  // auto-swaps them — saves the user from a 3-step
                  // dance when there are only 2 panels and they want
                  // to flip exit/relay.
                  if (id === relayId) {
                    setRelayId(exitId)
                  }
                  setExitId(id)
                }}
              />
            </div>
            <button
              type="button"
              onClick={() => {
                const a = exitId, b = relayId
                setExitId(b)
                setRelayId(a)
              }}
              disabled={exitId == null && relayId == null}
              title={t('Swap exit ↔ relay', 'Поменять exit ↔ relay местами')}
              className="rounded-lg border border-gray-700 hover:bg-gray-800 disabled:opacity-50 px-2 py-2 text-gray-300 hover:text-gray-100 text-sm transition-colors"
            >
              ⇄
            </button>
            <div>
              <label className="text-xs text-gray-400 block mb-1">
                {t('Relay panel', 'Relay-панель')}
              </label>
              <PanelPicker
                servers={servers}
                value={relayId}
                onChange={(id) => {
                  if (id === exitId) {
                    setExitId(relayId)
                  }
                  setRelayId(id)
                }}
              />
            </div>
          </div>

          <div>
            <label className="text-xs text-gray-400 block mb-1">
              {t('Exit SNI (cover for relay → exit hop)', 'Exit SNI (маскировка relay → exit)')}
            </label>
            <input
              type="text"
              value={exitSni}
              onChange={(e) => setExitSni(e.target.value)}
              placeholder="www.google.com"
              className="w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
            />
            <p className="text-[11px] text-gray-500 mt-1 leading-snug">
              {t(
                'A widely-reachable HTTPS host. The relay → exit hop will look like TLS to this domain.',
                'Универсально-достижимый HTTPS хост. Соединение relay → exit будет выглядеть как TLS к этому домену.',
              )}
            </p>
          </div>
        </div>

        {/* Channels */}
        <div className="mt-5 space-y-2">
          <div className="flex items-center justify-between">
            <div className="text-[11px] uppercase tracking-wider text-gray-500">
              {t('Channels', 'Каналы')}
            </div>
            <button
              type="button"
              onClick={addChannel}
              className="rounded-md border border-gray-700 hover:bg-gray-800 px-2.5 py-1 text-[11px] text-gray-300 inline-flex items-center gap-1"
            >
              <Plus className="h-3 w-3" />
              {t('Add channel', 'Добавить канал')}
            </button>
          </div>
          <div className="space-y-2">
            {channels.map((ch, idx) => (
              <ChannelDraftRowEdit
                key={ch._key}
                value={ch}
                onChange={(patch) => updateChannel(idx, patch)}
                onRemove={channels.length > 1 ? () => removeChannel(idx) : undefined}
                index={idx}
                exitMode={servers.find((s) => s.id === exitId)?.mode}
                relayMode={servers.find((s) => s.id === relayId)?.mode}
              />
            ))}
          </div>
        </div>

        <div className="flex gap-2 pt-5">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-400 hover:bg-gray-800"
          >
            {t('Cancel', 'Отмена')}
          </button>
          <div className="flex-1" />
          <button
            type="submit"
            disabled={createMut.isPending}
            className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-4 py-2 text-sm text-white font-medium inline-flex items-center gap-2"
          >
            {createMut.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {t('Create chain', 'Создать цепочку')}
          </button>
        </div>

        {createMut.isPending && (
          <p className="text-[11px] text-gray-500 mt-2 leading-snug">
            {t(
              'Orchestration takes 10-30 seconds (panel API calls + xrayTemplateConfig push + Xray restart on the relay).',
              'Оркестрация занимает 10-30 секунд (API панелей + push xrayTemplateConfig + рестарт Xray на relay).',
            )}
          </p>
        )}
      </form>
    </ModalShell>
  )
}

function PanelPicker({
  servers, value, onChange,
}: {
  servers: XuiServer[]
  value: number | null
  onChange: (id: number) => void
}) {
  // No `disabledId` prop — picking the panel that's currently in the
  // other slot is allowed and triggers an auto-swap in the parent.
  // That's much friendlier UX than the previous "disable the other
  // option" pattern, which dead-ended users with only 2 panels.
  return (
    <select
      value={value ?? ''}
      onChange={(e) => onChange(Number(e.target.value))}
      className="w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
    >
      <option value="" disabled>— pick a panel —</option>
      {servers.map((s) => (
        <option key={s.id} value={s.id}>
          {s.server_name} ({s.mode}) — {s.domain || s.server_host}
        </option>
      ))}
    </select>
  )
}

function ChannelDraftRowEdit({
  value, onChange, onRemove, index, exitMode, relayMode,
}: {
  value: ChannelDraftRow
  onChange: (patch: Partial<ChannelDraftRow>) => void
  onRemove?: () => void
  index: number
  exitMode?: string
  relayMode?: string
}) {
  const t = useT()
  // xui-pro panels run on a domain with Caddy/ACME → ports 80 + 443
  // are bound outside the panel's view. Surface this client-side so
  // the user doesn't get a 4xx from the backend port validator.
  const relayReserved = relayMode === 'xui-pro' && (value.relay_port === 80 || value.relay_port === 443)
  const exitReserved = exitMode === 'xui-pro' && (value.exit_port === 80 || value.exit_port === 443)
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/30 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="text-[11px] text-gray-500 font-mono">
          {t('Channel', 'Канал')} #{index + 1}
        </div>
        {onRemove && (
          <button
            type="button"
            onClick={onRemove}
            title={t('Remove channel', 'Удалить канал')}
            className="text-gray-500 hover:text-red-400 p-1"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
        <div>
          <label className="text-[11px] text-gray-500 block mb-0.5">
            {t('Name', 'Имя')}
          </label>
          <input
            type="text"
            value={value.name}
            onChange={(e) => onChange({ name: e.target.value })}
            placeholder="alpha"
            pattern="[A-Za-z0-9_-]+"
            className="w-full rounded-md bg-gray-900 border border-gray-800 px-2 py-1 text-xs text-gray-100 focus:border-brand-500 focus:outline-hidden"
          />
        </div>
        <div>
          <label className="text-[11px] text-gray-500 block mb-0.5">
            {t('Client SNI (operator-chosen)', 'Client SNI (на ваше усмотрение)')}
          </label>
          <input
            type="text"
            value={value.client_sni}
            onChange={(e) => onChange({ client_sni: e.target.value })}
            placeholder="example.com"
            className="w-full rounded-md bg-gray-900 border border-gray-800 px-2 py-1 text-xs text-gray-100 focus:border-brand-500 focus:outline-hidden"
          />
        </div>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        <div>
          <label className="text-[11px] text-gray-500 block mb-0.5">
            {t('Relay port', 'Relay-порт')}
          </label>
          <input
            type="number"
            min={0}
            max={65535}
            value={value.relay_port || ''}
            onChange={(e) => onChange({ relay_port: Number(e.target.value) || 0 })}
            placeholder={t('auto', 'авто')}
            className={`w-full rounded-md bg-gray-900 border px-2 py-1 text-xs text-gray-100 focus:outline-hidden ${relayReserved ? 'border-red-500/60 focus:border-red-500' : 'border-gray-800 focus:border-brand-500'}`}
          />
          {relayReserved && (
            <div className="text-[10px] text-red-600 dark:text-red-400 mt-0.5 leading-tight">
              {t('Reserved by panel HTTPS (Caddy / ACME)', 'Занят панелью HTTPS (Caddy / ACME)')}
            </div>
          )}
        </div>
        <div>
          <label className="text-[11px] text-gray-500 block mb-0.5">
            {t('Exit port', 'Exit-порт')}
          </label>
          <input
            type="number"
            min={0}
            max={65535}
            value={value.exit_port || ''}
            onChange={(e) => onChange({ exit_port: Number(e.target.value) || 0 })}
            placeholder={t('auto', 'авто')}
            className={`w-full rounded-md bg-gray-900 border px-2 py-1 text-xs text-gray-100 focus:outline-hidden ${exitReserved ? 'border-red-500/60 focus:border-red-500' : 'border-gray-800 focus:border-brand-500'}`}
          />
          {exitReserved && (
            <div className="text-[10px] text-red-600 dark:text-red-400 mt-0.5 leading-tight">
              {t('Reserved by panel HTTPS (Caddy / ACME)', 'Занят панелью HTTPS (Caddy / ACME)')}
            </div>
          )}
        </div>
        <div className="col-span-2">
          <label className="text-[11px] text-gray-500 block mb-0.5">
            {t('xhttp path (optional)', 'xhttp path (необязательно)')}
          </label>
          <input
            type="text"
            value={value.exit_xhttp_path ?? ''}
            onChange={(e) => onChange({ exit_xhttp_path: e.target.value })}
            placeholder={t(`auto: /api/v1/${value.name || '<name>'}`, `авто: /api/v1/${value.name || '<имя>'}`)}
            className="w-full rounded-md bg-gray-900 border border-gray-800 px-2 py-1 text-xs text-gray-100 focus:border-brand-500 focus:outline-hidden"
          />
        </div>
      </div>
    </div>
  )
}
