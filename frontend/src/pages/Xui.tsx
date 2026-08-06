import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Layers, Loader2, AlertTriangle, ExternalLink, Trash2, Plus, RefreshCw,
  ShieldCheck, ShieldAlert, Server as ServerIcon, KeyRound, Copy, Check,
  ChevronRight, ChevronDown, Upload, Shuffle, UploadCloud, QrCode, Radar,
} from 'lucide-react'

import { xuiApi, diagnosticsApi, type SniScanResult } from '@/api/client'
import { useT } from '@/hooks/useT'
import { useConfirm } from '@/components/ConfirmModal'
import { ModalShell } from '@/components/ModalShell'
import { ClientQrModal } from '@/components/ClientQrModal'
import { DirectToggle } from '@/components/DirectToggle'
import { copyToClipboard } from '@/lib/clipboard'
import type { InboundPreset, XuiClient, XuiInbound, XuiServer } from '@/types'

/**
 * x-ui panel management page (since v1.3.0-beta.7).
 *
 * Lists registered XuiServer rows, lets the user pick one, then shows
 * its inbounds + clients. Inbound creation flows through a preset
 * picker (the 6 wired-in templates from `app.core.xui_presets`).
 * Client lifecycle is per-inbound; "Export to Node" lands on the
 * existing Node table so the rest of PiTun's routing layer treats
 * x-ui clients like any other proxy outbound.
 *
 * This is the "single-server" UI — chains (Phase 6) get their own
 * page where multiple XuiServers are wired together end-to-end.
 */
export default function XuiPage() {
  const t = useT()
  const qc = useQueryClient()

  // Selected XuiServer id. Persists in URL hash so a deep-link to
  // `#/xui/5` lands on server 5 directly (mirrors the Servers page).
  const [selectedId, setSelectedId] = useState<number | null>(null)

  const { data: servers = [], isLoading: srvLoading, error: srvError } = useQuery<XuiServer[]>({
    queryKey: ['xui', 'servers'],
    queryFn: () => xuiApi.listServers(),
    refetchOnWindowFocus: false,
  })

  // Auto-select the first server when none picked.
  useEffect(() => {
    if (selectedId == null && servers.length > 0) {
      setSelectedId(servers[0].id)
    }
  }, [servers, selectedId])

  const selectedServer = servers.find((s) => s.id === selectedId) ?? null

  return (
    <div className="p-4 md:p-6">
      <header className="mb-5 flex flex-wrap items-center gap-3">
        <Layers className="h-6 w-6 text-brand-400" />
        <h1 className="text-2xl font-semibold text-gray-100">
          {t('X-ui panels', 'Панели X-ui')}
        </h1>
        <p className="text-sm text-gray-500 hidden md:block">
          {t(
            'Manage VLESS / Trojan / SOCKS inbounds on x-ui-pro / 3x-ui panels deployed via PiTun',
            'Управляйте VLESS / Trojan / SOCKS инбаундами на панелях x-ui-pro / 3x-ui, развёрнутых через PiTun',
          )}
        </p>
      </header>

      <div className="space-y-4">
      {srvLoading && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-6 flex items-center justify-center gap-2 text-sm text-gray-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t('Loading panels…', 'Загрузка панелей…')}
        </div>
      )}

      {srvError && (
        <div className="rounded-lg border border-red-200 dark:border-red-700/40 bg-red-50 dark:bg-red-900/20 px-3 py-3 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{t('Failed to load panels', 'Не удалось загрузить панели')}</span>
        </div>
      )}

      {!srvLoading && !srvError && servers.length === 0 && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-6 text-sm text-gray-400">
          <div className="flex items-start gap-2">
            <ServerIcon className="h-4 w-4 mt-0.5 text-gray-500" />
            <div>
              <div className="font-medium text-gray-200">
                {t('No x-ui panels registered yet', 'Пока нет зарегистрированных панелей x-ui')}
              </div>
              <p className="text-xs text-gray-500 mt-1 leading-snug">
                {t(
                  'Go to Servers → Deploy on a registered VPS → pick "x-ui" protocol. Once the install finishes, the panel registers itself here automatically.',
                  'Перейдите в Servers → Deploy на зарегистрированном VPS → выберите протокол "x-ui". После установки панель появится здесь автоматически.',
                )}
              </p>
            </div>
          </div>
        </div>
      )}

      {servers.length > 0 && (
        <div className="grid grid-cols-1 lg:grid-cols-[280px_1fr] gap-4">
          <ServerList
            servers={servers}
            selectedId={selectedId}
            onSelect={setSelectedId}
            onChanged={() => qc.invalidateQueries({ queryKey: ['xui', 'servers'] })}
          />
          {selectedServer && (
            <ServerDetail
              key={selectedServer.id}
              server={selectedServer}
            />
          )}
        </div>
      )}
      </div>
    </div>
  )
}


// ── Server list (left pane) ─────────────────────────────────────────────────

function ServerList({
  servers, selectedId, onSelect, onChanged,
}: {
  servers: XuiServer[]
  selectedId: number | null
  onSelect: (id: number) => void
  onChanged: () => void
}) {
  const t = useT()
  return (
    <aside className="rounded-xl border border-gray-800 bg-gray-950/60 overflow-hidden">
      <header className="px-3 py-2 border-b border-gray-800 text-[11px] text-gray-500 uppercase tracking-wider">
        {t('Registered panels', 'Зарегистрированные панели')}
      </header>
      <ul>
        {servers.map((s) => (
          <li key={s.id}>
            <button
              type="button"
              onClick={() => onSelect(s.id)}
              className={
                'w-full text-left px-3 py-2.5 flex items-start gap-2 transition-colors border-b border-gray-900 ' +
                (s.id === selectedId
                  ? 'bg-brand-50 dark:bg-brand-600/10 text-brand-700 dark:text-brand-200'
                  : 'text-gray-300 hover:bg-gray-900/40')
              }
            >
              <Layers className={
                'h-4 w-4 mt-0.5 shrink-0 ' +
                (s.id === selectedId ? 'text-brand-400' : 'text-gray-500')
              } />
              <div className="min-w-0 flex-1">
                <div className="font-medium truncate">{s.server_name}</div>
                <div className="text-[11px] text-gray-500 truncate">
                  {s.domain || s.server_host}
                </div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className={
                    'text-[10px] px-1.5 py-0.5 rounded-sm font-mono ' +
                    (s.mode === 'xui-pro'
                      ? 'bg-purple-100 text-purple-700 border border-purple-200 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-700/40'
                      : 'bg-gray-900 text-gray-400 border border-gray-700')
                  }>{s.mode}</span>
                  {s.last_check_error
                    ? <ShieldAlert className="h-3 w-3 text-red-600 dark:text-red-400" />
                    : <ShieldCheck className="h-3 w-3 text-emerald-600 dark:text-emerald-400" />}
                </div>
              </div>
            </button>
          </li>
        ))}
      </ul>
      {/* Refresh-all button at the bottom; per-panel probe is in the
          detail pane. */}
      <button
        type="button"
        onClick={onChanged}
        className="w-full px-3 py-2 text-xs text-gray-400 hover:text-gray-200 hover:bg-gray-900/60 transition-colors flex items-center justify-center gap-1.5 border-t border-gray-800"
      >
        <RefreshCw className="h-3.5 w-3.5" />
        {t('Refresh list', 'Обновить список')}
      </button>
    </aside>
  )
}


// ── Server detail (right pane) ──────────────────────────────────────────────

function ServerDetail({ server }: { server: XuiServer }) {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()

  // "Direct connection" — when on, panel operations dial the panel OFF the
  // active node's tunnel (SO_MARK bypass). Default off = through the active
  // node, like all other backend egress. Handy to reach a panel while the
  // active node is down. Off by default so we never silently leave the VPN.
  const [direct, setDirect] = useState(false)

  const { data: inbounds = [], isLoading, error, refetch } = useQuery<XuiInbound[]>({
    queryKey: ['xui', 'inbounds', server.id],
    queryFn: () => xuiApi.listInbounds(server.id),
    refetchOnWindowFocus: false,
  })

  // Healthcheck modal — fires the new multi-layer probe (panel API +
  // xray state + SSH-checked nginx/ufw/cert/disk) when the user
  // clicks "Проверить". Reuses the chain-style "checks list" shape
  // so the UI is uniform across X-ui and Chains. The old cheap
  // Bearer-only `probeServer` endpoint still exists for callers that
  // just want a token re-test (sidebar list-refresh, etc.).
  const [showHealth, setShowHealth] = useState(false)
  const healthMut = useMutation({
    mutationFn: () => xuiApi.healthcheckServer(server.id, direct),
  })

  // Reconcile the panel ↔ PiTun cache. Refresh-button does the local
  // re-fetch; this one calls a server endpoint that writes back to
  // the `XuiClient` cache table — picks up hand-added / hand-deleted
  // clients the operator made via the panel UI and cascades any
  // Node rows whose backing client vanished.
  const syncMut = useMutation({
    mutationFn: () => xuiApi.syncServer(server.id, direct),
    onSuccess: (summary) => {
      qc.invalidateQueries({ queryKey: ['xui', 'inbounds', server.id] })
      qc.invalidateQueries({ queryKey: ['nodes'] })
      // ServerDetail remounts on `key={server.id}`, so switching panels
      // mid-sync used to discard the summary — including the fact that
      // orphan Nodes were cascade-deleted. Park it per panel instead.
      qc.setQueryData(['xui', 'sync-result', server.id], summary)
    },
  })

  // Last sync summary for THIS panel, surviving a panel switch.
  const { data: syncSummary } = useQuery<Awaited<ReturnType<typeof xuiApi.syncServer>> | null>({
    queryKey: ['xui', 'sync-result', server.id],
    queryFn: async () => null,
    initialData: null,
    staleTime: Infinity,
    gcTime: Infinity,
  })

  // Fakesite rotation — xui-pro only. The "rotate" button just calls
  // the backend (random template from the bundled archive); upload
  // is gated by a hidden <input type=file> wired to a button.
  const rotateMut = useMutation({
    mutationFn: () => xuiApi.rotateFakesite(server.id, direct),
  })
  const uploadMut = useMutation({
    mutationFn: (zip: File) => xuiApi.uploadFakesite(server.id, zip, direct),
  })
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const isXuiPro = server.mode === 'xui-pro'

  const delInboundMut = useMutation({
    mutationFn: (id: number) => xuiApi.deleteInbound(server.id, id, direct),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['xui', 'inbounds', server.id] })
    },
  })

  const [showAddInbound, setShowAddInbound] = useState(false)
  const [showAddClientFor, setShowAddClientFor] = useState<number | null>(null)

  const panelUrl =
    (server.mode === 'xui-pro' && server.domain
      ? `https://${server.domain}:${server.panel_port}${server.panel_basepath}/`
      : `https://${server.server_host}:${server.panel_port}${server.panel_basepath}/`)

  return (
    <section className="rounded-xl border border-gray-800 bg-gray-950/60 p-4 space-y-3">
      <header className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h2 className="text-lg font-medium text-gray-100">{server.server_name}</h2>
          <div className="text-xs text-gray-500 mt-0.5 flex items-center gap-2 flex-wrap">
            <span>{server.domain || server.server_host}</span>
            <span className="text-gray-700">·</span>
            <a
              href={panelUrl}
              target="_blank"
              rel="noreferrer"
              className="text-brand-400 hover:text-brand-700 dark:hover:text-brand-300 inline-flex items-center gap-1"
            >
              {t('Open panel', 'Открыть панель')}
              <ExternalLink className="h-3 w-3" />
            </a>
            <span className="text-gray-700">·</span>
            <span className="font-mono">{server.panel_user}</span>
          </div>
          {server.last_check_error && (
            <div className="mt-2 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700/40 px-2 py-1 text-[11px] text-red-700 dark:text-red-300 flex items-start gap-1.5">
              <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
              <span>{server.last_check_error}</span>
            </div>
          )}
          {/* Inline result for the fakesite rotate/upload mutations.
              Lives next to the header so the operator sees what just
              happened without an extra modal. */}
          {rotateMut.data && (
            <div className="mt-2 rounded-md bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-700/40 px-2 py-1 text-[11px] text-green-700 dark:text-green-300 flex items-start gap-1.5">
              <Check className="h-3 w-3 mt-0.5 shrink-0" />
              <span>
                {t(`Fakesite rotated → ${rotateMut.data.name || '(unknown)'}`, `Фейк-сайт обновлён → ${rotateMut.data.name || '(неизвестно)'}`)}
              </span>
            </div>
          )}
          {rotateMut.error && (
            <div className="mt-2 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700/40 px-2 py-1 text-[11px] text-red-700 dark:text-red-300 flex items-start gap-1.5">
              <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
              <span>
                {t('Rotation failed', 'Ошибка ротации')}: {
                  ((rotateMut.error as { response?: { data?: { detail?: string } } }).response?.data?.detail
                    || (rotateMut.error as Error).message
                    || 'unknown')
                }
              </span>
            </div>
          )}
          {uploadMut.data && (
            <div className="mt-2 rounded-md bg-green-50 dark:bg-green-900/20 border border-green-200 dark:border-green-700/40 px-2 py-1 text-[11px] text-green-700 dark:text-green-300 flex items-start gap-1.5">
              <Check className="h-3 w-3 mt-0.5 shrink-0" />
              <span>{t('Fakesite uploaded', 'Фейк-сайт загружен')}</span>
            </div>
          )}
          {syncSummary && (
            <div className="mt-2 rounded-md bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-700/40 px-2 py-1 text-[11px] text-blue-800 dark:text-blue-200 flex items-start gap-1.5">
              <Check className="h-3 w-3 mt-0.5 shrink-0" />
              <span>
                {t(
                  `Sync: +${syncSummary.added} added · ${syncSummary.updated} updated · -${syncSummary.removed} removed${syncSummary.orphan_nodes_removed ? ` · ${syncSummary.orphan_nodes_removed} orphan Node(s) cleaned` : ''}${syncSummary.chain_skipped ? ` · ${syncSummary.chain_skipped} chain inbound(s) skipped` : ''}`,
                  `Синхронизация: +${syncSummary.added} добавлено · ${syncSummary.updated} обновлено · -${syncSummary.removed} удалено${syncSummary.orphan_nodes_removed ? ` · ${syncSummary.orphan_nodes_removed} осиротевших Node убрано` : ''}${syncSummary.chain_skipped ? ` · ${syncSummary.chain_skipped} chain-инбаунда пропущено` : ''}`,
                )}
              </span>
            </div>
          )}
          {syncMut.error && (
            <div className="mt-2 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700/40 px-2 py-1 text-[11px] text-red-700 dark:text-red-300 flex items-start gap-1.5">
              <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
              <span>
                {t('Sync failed', 'Ошибка синхронизации')}: {
                  ((syncMut.error as { response?: { data?: { detail?: string } } }).response?.data?.detail
                    || (syncMut.error as Error).message
                    || 'unknown')
                }
              </span>
            </div>
          )}
          {uploadMut.error && (
            <div className="mt-2 rounded-md bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700/40 px-2 py-1 text-[11px] text-red-700 dark:text-red-300 flex items-start gap-1.5">
              <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
              <span>
                {t('Upload failed', 'Ошибка загрузки')}: {
                  ((uploadMut.error as { response?: { data?: { detail?: string } } }).response?.data?.detail
                    || (uploadMut.error as Error).message
                    || 'unknown')
                }
              </span>
            </div>
          )}
        </div>
        {/* Toolbar wraps on narrow viewports — by beta.7 we have 5
            buttons (Check / Refresh / Rotate fakesite / Upload zip /
            Add inbound) which together overflow a 360px mobile
            screen. `flex-wrap` reflows onto multiple lines, and
            `justify-end` keeps everything against the right edge so
            the primary "Add inbound" CTA stays where the operator
            expects it on desktop. */}
        <div className="flex items-center justify-end gap-2 flex-wrap">
          <button
            type="button"
            onClick={() => {
              setShowHealth(true)
              healthMut.mutate()
            }}
            disabled={healthMut.isPending}
            className="rounded-lg border border-gray-700 hover:bg-gray-800 px-2.5 py-1.5 text-xs text-gray-300 inline-flex items-center gap-1.5"
            title={t(
              'Run a deep health-check (panel API, xray, nginx, UFW, TLS cert, disk)',
              'Глубокая проверка: API панели, xray, nginx, UFW, TLS-сертификат, диск',
            )}
          >
            {healthMut.isPending
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <ShieldCheck className="h-3.5 w-3.5" />}
            {t('Check', 'Проверить')}
          </button>
          <button
            type="button"
            onClick={() => refetch()}
            className="rounded-lg border border-gray-700 hover:bg-gray-800 px-2.5 py-1.5 text-xs text-gray-300 inline-flex items-center gap-1.5"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            {t('Refresh', 'Обновить')}
          </button>
          {/* Sync = server-side reconcile of XuiClient cache ↔ panel.
              Distinct from Refresh (which just re-fetches the list
              the UI renders). Use after you've added/removed clients
              directly in the panel UI to keep PiTun's view honest. */}
          <button
            type="button"
            onClick={() => syncMut.mutate()}
            disabled={syncMut.isPending}
            className="rounded-lg border border-gray-700 hover:bg-gray-800 px-2.5 py-1.5 text-xs text-gray-300 inline-flex items-center gap-1.5 disabled:opacity-50"
            title={t(
              'Reconcile the PiTun client cache with what is actually on the panel right now',
              'Сверить локальный кеш PiTun с текущим состоянием панели',
            )}
          >
            {syncMut.isPending
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <RefreshCw className="h-3.5 w-3.5" />}
            {t('Sync', 'Синхронизация')}
          </button>
          {/* Direct-connection toggle: dial the panel off the active node's
              tunnel (SO_MARK bypass). Off = through the active node. Applies
              to every panel op on this server (probe, sync, add-client,
              create-inbound, fakesite) via the ?direct= flag. */}
          <DirectToggle checked={direct} onChange={setDirect} className="px-1" />
          {/* Fakesite controls — xui-pro only. Bare panels run no
              nginx fronting, so there's no /var/www/html to rotate. */}
          {isXuiPro && (
            <>
              <button
                type="button"
                onClick={() => rotateMut.mutate()}
                disabled={rotateMut.isPending || uploadMut.isPending}
                className="rounded-lg border border-gray-700 hover:bg-gray-800 px-2.5 py-1.5 text-xs text-gray-300 inline-flex items-center gap-1.5 disabled:opacity-50"
                title={t(
                  'Pick a random fakesite template from the bundled archive and swap it into nginx',
                  'Выбрать случайный фейк-сайт из встроенного архива и переключить nginx на него',
                )}
              >
                {rotateMut.isPending
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  : <Shuffle className="h-3.5 w-3.5" />}
                {t('Rotate fakesite', 'Ротация фейк-сайта')}
              </button>
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                disabled={rotateMut.isPending || uploadMut.isPending}
                className="rounded-lg border border-gray-700 hover:bg-gray-800 px-2.5 py-1.5 text-xs text-gray-300 inline-flex items-center gap-1.5 disabled:opacity-50"
                title={t(
                  'Upload your own fakesite as a .zip (must contain index.html, ≤100 MB)',
                  'Загрузить свой фейк-сайт .zip (нужен index.html, до 100 MB)',
                )}
              >
                {uploadMut.isPending
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  : <UploadCloud className="h-3.5 w-3.5" />}
                {t('Upload zip', 'Загрузить ZIP')}
              </button>
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip,application/zip,application/x-zip-compressed"
                className="hidden"
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) uploadMut.mutate(f)
                  // Reset so picking the same file twice still fires.
                  e.target.value = ''
                }}
              />
            </>
          )}
          <button
            type="button"
            onClick={() => setShowAddInbound(true)}
            className="rounded-lg bg-brand-600 hover:bg-brand-500 px-3 py-1.5 text-xs text-white font-medium inline-flex items-center gap-1.5"
          >
            <Plus className="h-3.5 w-3.5" />
            {t('Add inbound', 'Добавить инбаунд')}
          </button>
        </div>
      </header>

      {isLoading && (
        <div className="text-sm text-gray-500 flex items-center gap-2 py-4">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t('Loading inbounds…', 'Загрузка инбаундов…')}
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-red-200 dark:border-red-700/40 bg-red-50 dark:bg-red-900/20 px-3 py-2 text-sm text-red-700 dark:text-red-300">
          {t('Failed to load inbounds from panel', 'Не удалось получить инбаунды с панели')}
        </div>
      )}

      {!isLoading && !error && inbounds.length === 0 && (
        <div className="rounded-lg border border-dashed border-gray-700 px-4 py-6 text-center text-sm text-gray-500">
          {t('No inbounds yet — click "Add inbound" to create one.', 'Инбаундов пока нет — нажмите "Добавить инбаунд".')}
        </div>
      )}

      {inbounds.map((ib) => (
        <InboundCard
          key={ib.id}
          serverId={server.id}
          direct={direct}
          inbound={ib}
          onAddClient={() => setShowAddClientFor(ib.id)}
          onDelete={async () => {
            const ok = await confirm({
              title: t('Delete inbound?', 'Удалить инбаунд?'),
              body: t(
                `Inbound "${ib.remark}" (port ${ib.port}, ${ib.protocol}) will be removed from the panel. Existing clients on it stop working immediately.`,
                `Инбаунд "${ib.remark}" (порт ${ib.port}, ${ib.protocol}) будет удалён с панели. Клиенты немедленно перестанут работать.`,
              ),
              confirmLabel: t('Delete', 'Удалить'),
              danger: true,
            })
            if (ok) delInboundMut.mutate(ib.id)
          }}
          removing={delInboundMut.isPending && delInboundMut.variables === ib.id}
        />
      ))}

      {showAddInbound && (
        <AddInboundModal
          server={server}
          direct={direct}
          onClose={() => setShowAddInbound(false)}
          onCreated={() => {
            setShowAddInbound(false)
            qc.invalidateQueries({ queryKey: ['xui', 'inbounds', server.id] })
          }}
        />
      )}

      {showAddClientFor !== null && (
        <AddClientModal
          serverId={server.id}
          inboundId={showAddClientFor}
          direct={direct}
          onClose={() => setShowAddClientFor(null)}
          onCreated={() => {
            setShowAddClientFor(null)
            qc.invalidateQueries({ queryKey: ['xui', 'inbounds', server.id] })
          }}
        />
      )}

      {showHealth && (
        <HealthCheckModal
          serverName={server.server_name}
          loading={healthMut.isPending}
          error={healthMut.error}
          result={healthMut.data}
          onClose={() => setShowHealth(false)}
          onRetry={() => healthMut.mutate()}
        />
      )}
    </section>
  )
}


// ── Health-check modal ─────────────────────────────────────────────────────

function HealthCheckModal({
  serverName, loading, error, result, onClose, onRetry,
}: {
  serverName: string
  loading: boolean
  error: unknown
  result: { ok: boolean; checks: Array<{ name: string; status: 'ok' | 'warn' | 'fail'; detail?: string | null }> } | undefined
  onClose: () => void
  onRetry: () => void
}) {
  const t = useT()
  const errMsg = error
    ? ((error as { response?: { data?: { detail?: unknown } }, message?: string }).response?.data?.detail
       ?? (error as Error).message
       ?? 'unknown error')
    : null

  const statusPill = (s: 'ok' | 'warn' | 'fail') => {
    if (s === 'ok') return 'bg-green-50 dark:bg-green-900/30 text-green-700 dark:text-green-300 border-green-200 dark:border-green-700/40'
    if (s === 'warn') return 'bg-yellow-50 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300 border-yellow-200 dark:border-yellow-700/40'
    return 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 border-red-200 dark:border-red-700/40'
  }

  return (
    <ModalShell onClose={onClose} labelledBy="xui-health-title">
      <div className="w-full max-w-xl rounded-2xl bg-gray-950/95 border border-gray-800 p-6 m-4 max-h-[90vh] overflow-y-auto">
        <h2 id="xui-health-title" className="text-lg font-semibold text-gray-100 mb-1">
          {t('Server health-check', 'Проверка сервера')}
        </h2>
        <p className="text-xs text-gray-500 mb-4">{serverName}</p>

        {loading && (
          <div className="text-sm text-gray-400 inline-flex items-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t('Running probes…', 'Запуск проверок…')}
          </div>
        )}

        {!loading && errMsg && (
          <div className="rounded-lg bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-700/40 px-3 py-2 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5" />
            <span>{String(errMsg)}</span>
          </div>
        )}

        {!loading && !errMsg && result && (
          <div className="space-y-2">
            <div className={
              'rounded-lg border px-3 py-2 text-sm flex items-center gap-2 ' +
              (result.ok
                ? 'bg-green-50 dark:bg-green-900/20 border-green-200 dark:border-green-700/40 text-green-700 dark:text-green-300'
                : 'bg-yellow-50 dark:bg-yellow-900/20 border-yellow-200 dark:border-yellow-700/40 text-yellow-800 dark:text-yellow-200')
            }>
              {result.ok ? <Check className="h-4 w-4" /> : <AlertTriangle className="h-4 w-4" />}
              <span>
                {result.ok
                  ? t('All checks passed', 'Все проверки прошли')
                  : t('Some checks need attention', 'Некоторые проверки требуют внимания')}
              </span>
            </div>
            <ul className="space-y-1.5">
              {result.checks.map((c, i) => (
                <li
                  key={`${c.name}-${i}`}
                  className="rounded-md border border-gray-800 bg-gray-900/40 px-3 py-2 flex items-start justify-between gap-3"
                >
                  <div className="min-w-0">
                    <div className="text-sm text-gray-200">{c.name}</div>
                    {c.detail && (
                      <div className="text-[11px] text-gray-500 mt-0.5 break-all">
                        {c.detail}
                      </div>
                    )}
                  </div>
                  <span className={
                    'shrink-0 text-[10px] px-1.5 py-0.5 rounded-sm border uppercase font-mono ' +
                    statusPill(c.status)
                  }>
                    {c.status}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="flex justify-end gap-2 mt-5">
          <button
            type="button"
            onClick={onRetry}
            disabled={loading}
            className="rounded-lg border border-gray-700 hover:bg-gray-800 px-3 py-1.5 text-xs text-gray-300 inline-flex items-center gap-1.5 disabled:opacity-50"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            {t('Re-run', 'Перезапустить')}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-brand-600 hover:bg-brand-500 px-3 py-1.5 text-xs text-white font-medium"
          >
            {t('Close', 'Закрыть')}
          </button>
        </div>
      </div>
    </ModalShell>
  )
}


// ── Inbound card ────────────────────────────────────────────────────────────

function InboundCard({
  serverId, direct, inbound, onAddClient, onDelete, removing,
}: {
  serverId: number
  direct: boolean
  inbound: XuiInbound
  onAddClient: () => void
  onDelete: () => void
  removing: boolean
}) {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [expanded, setExpanded] = useState(false)

  // Per-client QR modal. Holding the client identity + label here
  // keeps each row's fetch lazy — we only hit `GET .../share-uri`
  // when the user actually clicks the QR button.
  const [qrTarget, setQrTarget] = useState<{
    clientId: string
    title: string
    subtitle: string
  } | null>(null)

  // Parse the panel's JSON-in-JSON settings into a typed client list.
  // Each protocol stores its identity field slightly differently
  // (vless/vmess → id, trojan → password, ss → password, socks → user)
  // but the panel docs guarantee an `email` field per client across
  // every protocol — we surface that as the row label.
  type ParsedClient = {
    id?: string         // vless / vmess UUID
    password?: string   // trojan / ss
    user?: string       // socks
    email?: string
    flow?: string
    enable?: boolean
  }
  // 3x-ui v3.1.0 changed `settings` / `streamSettings` / `sniffing`
  // wire shape from JSON-encoded-string to nested object. v3.0.x
  // panels still ship them as strings. Handle both shapes — if it's
  // already an object, use as-is; if it's a string, JSON.parse it.
  const _loadField = (v: unknown): Record<string, unknown> => {
    if (v && typeof v === 'object') return v as Record<string, unknown>
    if (typeof v === 'string' && v) {
      try { return JSON.parse(v) as Record<string, unknown> } catch { /* fall through */ }
    }
    return {}
  }

  let clients: ParsedClient[] = []
  try {
    const s = _loadField(inbound.settings)
    const arr = (s.clients as ParsedClient[] | undefined) ?? (s.accounts as ParsedClient[] | undefined) ?? []
    clients = Array.isArray(arr) ? arr : []
  } catch { /* ignore */ }

  // Parse streamSettings once to surface transport + security tags.
  // Same shape-shift quirk as `settings` above.
  let inboundNetwork = ''
  let inboundSecurity = ''
  try {
    const ss = _loadField(inbound.streamSettings)
    const net = String(ss.network || '').toLowerCase()
    inboundNetwork = net === 'splithttp' ? 'xhttp' : net
    inboundSecurity = String(ss.security || '').toLowerCase()
    // xui-pro domain-mode inbounds emit `security: "none"` but tunnel
    // through nginx on :443 with TLS — the giveaway is an
    // `externalProxy` entry with `forceTls: "tls"`. Surface that as
    // a TLS badge so the user sees the effective wire shape.
    if (inboundSecurity === 'none' && Array.isArray(ss.externalProxy)) {
      const ep = ss.externalProxy[0]
      if (ep && typeof ep === 'object' && String(ep.forceTls || '').toLowerCase() === 'tls') {
        inboundSecurity = 'tls'
      }
    }
  } catch { /* ignore */ }

  // Chain-managed inbound? The chain orchestrator tags every inbound
  // it creates as `chain-<chain_id>-<channel>-<role>`. Those rows
  // need to stay read-only on the panel page — clients on them are
  // managed exclusively through the Chains tab, and a panel-side
  // delete would orphan the chain DB state.
  const isChainManaged = (inbound.tag || '').startsWith('chain-')

  // Map of UUID/email → exported Node id (backend-attached).
  const exports = inbound._pitun_exports || {}

  const exportMut = useMutation({
    mutationFn: (clientId: string) =>
      xuiApi.exportInboundClientToNode(serverId, inbound.id, clientId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['nodes'] })
    },
  })
  const delClientMut = useMutation({
    mutationFn: (clientUuid: string) =>
      xuiApi.deleteClient(serverId, inbound.id, clientUuid, direct),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['xui', 'inbounds', serverId] })
    },
  })

  // Pick the "natural id" we ship to the export endpoint: UUID for
  // vless/vmess, email for trojan/ss/socks (everything has email).
  const clientNaturalId = (c: ParsedClient): string =>
    c.id || c.email || c.password || c.user || ''

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3 space-y-2">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="min-w-0 flex-1 text-left flex items-start gap-2 hover:opacity-80 transition-opacity"
        >
          {expanded
            ? <ChevronDown className="h-3.5 w-3.5 text-gray-500 mt-1 shrink-0" />
            : <ChevronRight className="h-3.5 w-3.5 text-gray-500 mt-1 shrink-0" />}
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium text-gray-100">{inbound.remark || `inbound-${inbound.id}`}</span>
              {/* Order: port first (concrete identifying info) →
                  protocol → transport → security → state badges.
                  Palette (shared with NodeCard so the two lists feel
                  the same): protocol=blue, transport=green,
                  reality=purple, tls=orange, chain=red. */}
              <span className="text-[11px] text-gray-500 font-mono">:{inbound.port}</span>
              <span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-blue-100 text-blue-700 border border-blue-200 dark:bg-blue-900/30 dark:text-blue-300 dark:border-blue-700/40 uppercase font-mono">
                {inbound.protocol}
              </span>
              {inboundNetwork && inboundNetwork !== 'tcp' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-green-100 text-green-700 border border-green-200 dark:bg-green-900/30 dark:text-green-300 dark:border-green-700/40 uppercase font-mono">
                  {inboundNetwork}
                </span>
              )}
              {inboundSecurity === 'reality' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-purple-100 text-purple-700 border border-purple-200 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-700/40 uppercase font-mono">
                  reality
                </span>
              )}
              {inboundSecurity === 'tls' && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-orange-100 text-orange-700 border border-orange-200 dark:bg-orange-900/30 dark:text-orange-300 dark:border-orange-700/40 uppercase font-mono">
                  tls
                </span>
              )}
              {!inbound.enable && (
                <span className="text-[10px] px-1.5 py-0.5 rounded-sm bg-yellow-50 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300 border border-yellow-200 dark:border-yellow-700/40">
                  disabled
                </span>
              )}
              {isChainManaged && (
                <span
                  className="text-[10px] px-1.5 py-0.5 rounded-sm bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 border border-red-200 dark:border-red-700/40 font-mono uppercase"
                  title={t(
                    'This inbound is part of a chain. Manage its clients on the Chains tab.',
                    'Этот инбаунд принадлежит цепочке. Управление клиентами — на вкладке «Цепочки».',
                  )}
                >
                  chain
                </span>
              )}
            </div>
            <div className="text-[11px] text-gray-500 mt-0.5">
              {t(`${clients.length} client${clients.length === 1 ? '' : 's'}`, `${clients.length} клиент${clients.length === 1 ? '' : 'ов'}`)}
            </div>
          </div>
        </button>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={onAddClient}
            disabled={isChainManaged}
            title={isChainManaged
              ? t(
                  'Chain-managed inbound — add clients from the Chains tab.',
                  'Инбаунд цепочки — клиенты добавляются на вкладке «Цепочки».',
                )
              : undefined}
            className="rounded-md border border-gray-700 hover:bg-gray-800 disabled:hover:bg-transparent disabled:opacity-50 disabled:cursor-not-allowed px-2 py-1 text-[11px] text-gray-300 inline-flex items-center gap-1"
          >
            <Plus className="h-3 w-3" />
            {t('Add client', 'Добавить клиента')}
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={removing || isChainManaged}
            title={isChainManaged
              ? t(
                  'Chain-managed inbound — delete the chain from the Chains tab to remove it.',
                  'Инбаунд цепочки — удалите цепочку на вкладке «Цепочки», чтобы убрать его.',
                )
              : t('Delete inbound', 'Удалить инбаунд')}
            className="rounded-md border border-gray-700 hover:bg-red-50 dark:hover:bg-red-900/30 hover:border-red-700/40 hover:text-red-700 dark:hover:text-red-300 disabled:hover:bg-transparent disabled:hover:border-gray-700 disabled:hover:text-gray-500 disabled:opacity-50 disabled:cursor-not-allowed p-1 text-gray-500"
          >
            {removing
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Trash2 className="h-3.5 w-3.5" />}
          </button>
        </div>
      </div>

      {/* Client list — expanded view. The panel returns clients
          inline inside the inbound's `settings`, so this is free
          data with no extra round-trip. */}
      {expanded && (
        <div className="pl-6 space-y-1.5 pt-1 border-t border-gray-800/60">
          {clients.length === 0 && (
            <div className="text-[11px] text-gray-500 py-1">
              {t('No clients yet — use "Add client" above.', 'Клиентов нет — используйте «Добавить клиента» выше.')}
            </div>
          )}
          {clients.map((c, idx) => {
            const naturalId = clientNaturalId(c)
            const isExporting = exportMut.isPending && exportMut.variables === naturalId
            const isDeleting = delClientMut.isPending && delClientMut.variables === naturalId
            const lastExport = exportMut.data && exportMut.variables === naturalId ? exportMut.data : null
            return (
              // Persistent export badge — backend attaches a map of
              // client-id → node-id on every inbound, so the badge
              // survives reloads. lastExport (from the local mutation)
              // wins when present so users see the fresh result
              // immediately after clicking Export.
              <div key={naturalId || idx} className="flex items-center justify-between gap-2 text-[11px] py-1">
                <div className="min-w-0 flex items-center gap-2">
                  <KeyRound className="h-3 w-3 text-gray-500 shrink-0" />
                  <div className="min-w-0 truncate">
                    <span className="text-gray-200 font-medium">{c.email || (c.id ? c.id.slice(0, 8) : t('(unnamed)', '(без имени)'))}</span>
                    {c.id && (
                      <span className="text-gray-500 font-mono ml-1.5">{c.id.slice(0, 8)}…</span>
                    )}
                    {c.flow && (
                      <span className="text-gray-600 ml-1.5">· {c.flow}</span>
                    )}
                    {c.enable === false && (
                      <span className="text-yellow-700 dark:text-yellow-300 ml-1.5">· disabled</span>
                    )}
                  </div>
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  {naturalId && (
                    <button
                      type="button"
                      onClick={() => setQrTarget({
                        clientId: naturalId,
                        title: c.email || naturalId.slice(0, 8),
                        subtitle: inbound.remark || `inbound #${inbound.id}`,
                      })}
                      title={t(
                        'Show QR code (scan into mobile client)',
                        'Показать QR-код (для сканирования в мобильный клиент)',
                      )}
                      className="rounded-md border border-gray-700 hover:bg-brand-50 dark:hover:bg-brand-900/30 hover:border-brand-400/50 hover:text-brand-700 dark:hover:text-brand-300 p-1 text-gray-400"
                    >
                      <QrCode className="h-3 w-3" />
                    </button>
                  )}
                  {(() => {
                    // Fresh export from the local mutation wins over
                    // the cached badge from the inbound payload.
                    const exportedNodeId =
                      (lastExport && lastExport.node_id) ??
                      exports[naturalId] ??
                      (c.email ? exports[c.email] : undefined)
                    if (exportedNodeId !== undefined) {
                      return (
                        <span
                          className="rounded-md border border-green-200 dark:border-green-700/50 bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-300 px-1.5 py-0.5 text-[10px] inline-flex items-center gap-1"
                          title={t(
                            `Already exported as Node #${exportedNodeId}`,
                            `Уже экспортировано как Node #${exportedNodeId}`,
                          )}
                        >
                          <Check className="h-3 w-3" />
                          Node #{exportedNodeId}
                        </span>
                      )
                    }
                    // Chain-managed inbounds get their clients
                    // exported through the Chains tab — that path
                    // wires the resulting Node into the chain's
                    // ChainClientChannel.exported_node_id link so
                    // cascade-delete on chain teardown works. The
                    // generic per-inbound export endpoint here would
                    // create a bare Node with no such link, leaving
                    // an orphan if the chain is later removed.
                    if (isChainManaged) {
                      return (
                        <span
                          className="text-[10px] text-gray-500 italic px-1.5 py-0.5"
                          title={t(
                            'Chain client — export from the Chains tab.',
                            'Клиент цепочки — экспорт со вкладки «Цепочки».',
                          )}
                        >
                          {t('via Chains', 'через Цепочки')}
                        </span>
                      )
                    }
                    return (
                      <button
                        type="button"
                        onClick={() => naturalId && exportMut.mutate(naturalId)}
                        disabled={!naturalId || isExporting}
                        title={t('Export this client as a Node', 'Экспортировать этого клиента в Nodes')}
                        className="rounded-md border border-gray-700 hover:bg-brand-50 dark:hover:bg-brand-900/30 hover:border-brand-400/50 hover:text-brand-700 dark:hover:text-brand-300 px-1.5 py-0.5 text-[10px] text-gray-300 inline-flex items-center gap-1 disabled:opacity-50"
                      >
                        {isExporting
                          ? <Loader2 className="h-3 w-3 animate-spin" />
                          : <Upload className="h-3 w-3" />}
                        {t('Export to Nodes', 'Экспорт в Nodes')}
                      </button>
                    )
                  })()}
                  <button
                    type="button"
                    onClick={async () => {
                      if (!naturalId) return
                      const ok = await confirm({
                        title: t('Delete client?', 'Удалить клиента?'),
                        body: t(
                          `Client "${c.email || naturalId}" will be removed from the panel. Any traffic using its credentials stops immediately.`,
                          `Клиент «${c.email || naturalId}» будет удалён с панели. Трафик через его credentials немедленно прервётся.`,
                        ),
                        confirmLabel: t('Delete', 'Удалить'),
                        danger: true,
                      })
                      if (ok) delClientMut.mutate(naturalId)
                    }}
                    disabled={!naturalId || isDeleting || isChainManaged}
                    title={isChainManaged
                      ? t(
                          'Chain client — manage via the Chains tab.',
                          'Клиент цепочки — управление на вкладке «Цепочки».',
                        )
                      : t('Delete client', 'Удалить клиента')}
                    className="rounded-md border border-gray-700 hover:bg-red-50 dark:hover:bg-red-900/30 hover:border-red-700/40 hover:text-red-700 dark:hover:text-red-300 disabled:hover:bg-transparent disabled:hover:border-gray-700 disabled:hover:text-gray-500 disabled:opacity-50 disabled:cursor-not-allowed p-1 text-gray-500"
                  >
                    {isDeleting
                      ? <Loader2 className="h-3 w-3 animate-spin" />
                      : <Trash2 className="h-3 w-3" />}
                  </button>
                </div>
              </div>
            )
          })}
          {exportMut.error && (
            <div className="text-[11px] text-red-700 dark:text-red-300 pt-1">
              {t('Export failed', 'Ошибка экспорта')}: {String((exportMut.error as Error).message)}
            </div>
          )}
        </div>
      )}
      <ClientQrModal
        open={qrTarget !== null}
        onClose={() => setQrTarget(null)}
        title={qrTarget?.title || ''}
        subtitle={qrTarget?.subtitle}
        fetchUri={qrTarget
          ? () => xuiApi.getInboundClientShareUri(serverId, inbound.id, qrTarget.clientId)
          : undefined}
      />
    </div>
  )
}


// ── Add inbound modal ──────────────────────────────────────────────────────

function AddInboundModal({
  server, direct, onClose, onCreated,
}: {
  server: XuiServer
  direct: boolean
  onClose: () => void
  onCreated: () => void
}) {
  const t = useT()
  const { data: presets = [], isLoading } = useQuery<InboundPreset[]>({
    queryKey: ['xui', 'presets'],
    queryFn: () => xuiApi.listPresets(),
    staleTime: 60_000,
  })

  const [presetId, setPresetId] = useState<string | null>(null)
  const [values, setValues] = useState<Record<string, string>>({})
  const [error, setError] = useState('')

  // REALITY dest / SNI scan — probe whatever's in the `sni` field (a domain
  // OR a bare IP) THROUGH THE ACTIVE NODE and surface what turns up: TLS 1.3 /
  // HTTP-2 suitability plus the certificate domain the endpoint presents, so
  // the operator can pick a working serverName. Mirrors 3x-ui's reality-sni
  // scan, but routed through the active node like every other server op.
  const [sniScan, setSniScan] = useState<{ loading: boolean; res: SniScanResult | null; err: string }>(
    { loading: false, res: null, err: '' },
  )
  const scanSni = async (fieldName: string) => {
    const target = (values[fieldName] || '').trim()
    if (!target) return
    setSniScan({ loading: true, res: null, err: '' })
    try {
      setSniScan({ loading: false, res: await diagnosticsApi.sniScan(target), err: '' })
    } catch {
      setSniScan({ loading: false, res: null, err: t('scan failed', 'ошибка сканирования') })
    }
  }

  const preset = presets.find((p) => p.id === presetId) ?? null

  // Pre-populate defaults when picking a preset. Fields with a
  // `default_from` hint pull from a runtime panel attribute instead
  // of the static `default` — e.g. trojan-grpc's `authority` should
  // default to the selected panel's domain so nginx routes work
  // without manual edits.
  useEffect(() => {
    if (!preset) return
    const next: Record<string, string> = {}
    for (const f of preset.fields) {
      if (f.default_from === 'panel_domain') {
        next[f.name] = server.domain ?? ''
      } else {
        next[f.name] = f.default ?? ''
      }
    }
    setValues(next)
    setError('')
  }, [preset, server.domain])

  const createMut = useMutation({
    mutationFn: () => {
      if (!preset) throw new Error('no preset')
      return xuiApi.createInbound(server.id, {
        preset_id: preset.id,
        values: { ...values },
      }, direct)
    },
    onSuccess: onCreated,
    onError: (err: unknown) => {
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

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    if (!preset) {
      setError(t('Pick a preset first', 'Сначала выберите пресет'))
      return
    }
    for (const f of preset.fields) {
      if (f.required && !values[f.name]?.trim() && !f.default) {
        setError(t(`Field "${f.label}" is required`, `Поле "${f.label}" обязательно`))
        return
      }
    }
    createMut.mutate()
  }

  return (
    <ModalShell onClose={onClose} labelledBy="add-inbound-title">
      <form
        onSubmit={onSubmit}
        className="w-full max-w-2xl rounded-2xl bg-gray-950/95 border border-gray-800 p-6 m-4 max-h-[90vh] overflow-y-auto"
      >
        <h2 id="add-inbound-title" className="text-lg font-semibold text-gray-100 mb-1">
          {t('Add inbound', 'Добавить инбаунд')}
        </h2>
        <p className="text-xs text-gray-500 mb-4">
          {t(
            `Server: ${server.server_name} · mode: ${server.mode}`,
            `Сервер: ${server.server_name} · режим: ${server.mode}`,
          )}
        </p>

        {error && (
          <div className="mb-3 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/40 px-3 py-2 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-2">
          {t('Preset', 'Пресет')}
        </div>
        {isLoading ? (
          <div className="text-xs text-gray-500 flex items-center gap-1.5">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {t('Loading presets…', 'Загрузка пресетов…')}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 mb-4">
            {/* On bare panels (no domain, no nginx fronting :443) the
                domain-mode presets fundamentally can't work — skip
                rendering them entirely so the picker stays focused
                on the actually-installable subset. */}
            {presets
              .filter((p) => !(p.needs_domain && server.mode === 'bare'))
              .map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => setPresetId(p.id)}
                  className={
                    'rounded-lg border px-3 py-2 text-left transition-colors ' +
                    (p.id === presetId
                      ? 'border-brand-500/60 bg-brand-50 dark:bg-brand-600/10 text-brand-700 dark:text-brand-200'
                      : 'border-gray-800 bg-gray-900/40 text-gray-400 hover:border-gray-700 hover:text-gray-200')
                  }
                >
                  <div className="text-sm font-medium flex items-center gap-1.5 flex-wrap">
                    <span>{p.label}</span>
                    {p.supports_reality && (
                      <span className="text-[10px] px-1 py-0.5 rounded-sm bg-purple-100 text-purple-700 border border-purple-200 dark:bg-purple-900/30 dark:text-purple-300 dark:border-purple-700/40 font-mono">
                        reality
                      </span>
                    )}
                    {p.needs_domain && (
                      <span className="text-[10px] px-1 py-0.5 rounded-sm bg-blue-100 text-blue-700 border border-blue-200 dark:bg-blue-900/30 dark:text-blue-300 dark:border-blue-700/40 font-mono">
                        domain
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-gray-500 mt-0.5 leading-snug">
                    {p.description}
                  </div>
                </button>
              ))}
          </div>
        )}

        {preset && (
          <div className="space-y-3 border-t border-gray-800 pt-4">
            <div className="text-[11px] uppercase tracking-wider text-gray-500">
              {t('Preset fields', 'Поля пресета')}
            </div>
            {preset.fields.map((f) => (
              <div key={f.name}>
                <label className="text-xs text-gray-400 block mb-1">
                  {f.label}
                  {!f.required && (
                    <span className="text-gray-600 ml-1">({t('optional', 'необязательно')})</span>
                  )}
                </label>
                <input
                  type={f.type === 'int' ? 'number' : 'text'}
                  value={values[f.name] ?? ''}
                  onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
                  placeholder={f.placeholder || (f.default ?? '')}
                  className="w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
                />
                {f.type === 'sni' && (
                  <div className="mt-1.5 space-y-1">
                    <button
                      type="button"
                      onClick={() => scanSni(f.name)}
                      disabled={sniScan.loading || !values[f.name]?.trim()}
                      className="rounded-sm bg-gray-800 px-2 py-1 text-[11px] text-gray-300 hover:bg-gray-700 disabled:opacity-50 inline-flex items-center gap-1.5"
                    >
                      {sniScan.loading
                        ? <Loader2 className="h-3 w-3 animate-spin" />
                        : <Radar className="h-3 w-3" />}
                      {sniScan.loading
                        ? t('Scanning…', 'Сканирую…')
                        : t('Scan (via active node)', 'Сканировать (через активную ноду)')}
                    </button>
                    {(sniScan.res || sniScan.err) && (
                      <div className={
                        'text-[11px] ' +
                        (sniScan.err
                          ? 'text-red-600 dark:text-red-400'
                          : sniScan.res!.ok
                          ? 'text-green-600 dark:text-green-400'
                          : 'text-amber-600 dark:text-amber-400')
                      }>
                        {sniScan.err || (
                          <>
                            {sniScan.res!.ok ? '✓ ' : '• '}{sniScan.res!.detail}
                            {sniScan.res!.tls13 != null && ` · TLS1.3 ${sniScan.res!.tls13 ? '✓' : '✗'}`}
                            {sniScan.res!.http2 != null && ` · h2 ${sniScan.res!.http2 ? '✓' : '✗'}`}
                            {(sniScan.res!.cert_name || sniScan.res!.cert_subject) && (
                              <> · {t('cert', 'серт')}:{' '}
                                <span className="text-gray-300">
                                  {sniScan.res!.cert_name || sniScan.res!.cert_subject}
                                </span>
                              </>
                            )}
                            {` · via ${sniScan.res!.via}`}
                          </>
                        )}
                      </div>
                    )}
                  </div>
                )}
                {f.help && (
                  <p className="text-[11px] text-gray-500 mt-1 leading-snug">{f.help}</p>
                )}
              </div>
            ))}
          </div>
        )}

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
            disabled={!preset || createMut.isPending}
            className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-4 py-2 text-sm text-white font-medium inline-flex items-center gap-2"
          >
            {createMut.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {t('Create inbound', 'Создать инбаунд')}
          </button>
        </div>
      </form>
    </ModalShell>
  )
}


// ── Add client modal ───────────────────────────────────────────────────────

function AddClientModal({
  serverId, inboundId, direct, onClose, onCreated,
}: {
  serverId: number
  inboundId: number
  direct: boolean
  onClose: () => void
  onCreated: () => void
}) {
  const t = useT()
  const [label, setLabel] = useState('')
  const [created, setCreated] = useState<XuiClient | null>(null)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')

  const addMut = useMutation({
    mutationFn: () =>
      xuiApi.addClient(serverId, inboundId, {
        label: label.trim() || undefined,
      }, direct),
    onSuccess: (data) => setCreated(data),
    onError: (err: unknown) => {
      let msg = 'Add client failed'
      if (typeof err === 'object' && err !== null) {
        const e = err as { response?: { data?: { detail?: unknown } }, message?: string }
        const detail = e.response?.data?.detail
        if (typeof detail === 'string') msg = detail
        else if (e.message) msg = e.message
      }
      setError(msg)
    },
  })

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    addMut.mutate()
  }

  const copy = async (txt: string) => {
    // Falls back to execCommand on insecure HTTP — see lib/clipboard.ts.
    if (await copyToClipboard(txt)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  return (
    <ModalShell onClose={onClose} labelledBy="add-client-title">
      <div className="w-full max-w-lg rounded-2xl bg-gray-950/95 border border-gray-800 p-6 m-4 max-h-[90vh] overflow-y-auto">
        <h2 id="add-client-title" className="text-lg font-semibold text-gray-100 mb-1">
          {t('Add client', 'Добавить клиента')}
        </h2>
        <p className="text-xs text-gray-500 mb-4">
          {t(`Inbound id: ${inboundId}`, `ID инбаунда: ${inboundId}`)}
        </p>

        {error && (
          <div className="mb-3 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/40 px-3 py-2 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {!created ? (
          <form onSubmit={onSubmit} className="space-y-3">
            <div>
              <label className="text-xs text-gray-400 block mb-1">
                {t('Label (optional)', 'Метка (необязательно)')}
              </label>
              <input
                type="text"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder={t('auto-generated: pi-XXXXXXXX', 'автоматически: pi-XXXXXXXX')}
                className="w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
              />
              <p className="text-[11px] text-gray-500 mt-1 leading-snug">
                {t(
                  'Stored in the panel\'s "email" field. The `pi-` prefix lets PiTun identify managed clients on /sync.',
                  'Сохраняется в поле "email" панели. Префикс `pi-` помогает PiTun отличать свои клиенты при синхронизации.',
                )}
              </p>
            </div>

            <div className="flex gap-2 pt-3">
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
                disabled={addMut.isPending}
                className="rounded-lg bg-brand-600 hover:bg-brand-500 disabled:opacity-50 px-4 py-2 text-sm text-white font-medium inline-flex items-center gap-2"
              >
                {addMut.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                {t('Add client', 'Добавить')}
              </button>
            </div>
          </form>
        ) : (
          <div className="space-y-3">
            <div className="rounded-lg border border-emerald-200 dark:border-emerald-700/40 bg-emerald-50 dark:bg-emerald-900/10 px-3 py-2 text-sm text-emerald-800 dark:text-emerald-200 flex items-start gap-2">
              <ShieldCheck className="h-4 w-4 mt-0.5" />
              <span>{t('Client created', 'Клиент создан')}</span>
            </div>
            <div>
              <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1">
                {t('Label', 'Метка')}
              </div>
              <div className="font-mono text-sm text-gray-200">{created.label}</div>
            </div>
            {created.client_uuid && (
              <div>
                <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1">UUID</div>
                <div className="flex items-center gap-2">
                  <code className="flex-1 rounded-sm bg-gray-900 border border-gray-800 px-2 py-1 text-xs font-mono text-gray-200 break-all">
                    {created.client_uuid}
                  </code>
                  <button
                    type="button"
                    onClick={() => copy(created.client_uuid)}
                    className="rounded-md border border-gray-700 hover:bg-gray-800 p-1.5 text-gray-400 hover:text-gray-200"
                    title={t('Copy UUID', 'Копировать UUID')}
                  >
                    {copied ? <Check className="h-3.5 w-3.5 text-emerald-600 dark:text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
                  </button>
                </div>
              </div>
            )}
            <div className="flex gap-2 pt-3">
              <div className="flex-1" />
              <button
                type="button"
                onClick={() => { setCreated(null); onCreated() }}
                className="rounded-lg bg-brand-600 hover:bg-brand-500 px-4 py-2 text-sm text-white font-medium inline-flex items-center gap-2"
              >
                <KeyRound className="h-3.5 w-3.5" />
                {t('Done', 'Готово')}
              </button>
            </div>
          </div>
        )}
      </div>
    </ModalShell>
  )
}
