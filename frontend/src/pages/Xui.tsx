import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Layers, Loader2, AlertTriangle, ExternalLink, Trash2, Plus, RefreshCw,
  ShieldCheck, ShieldAlert, Server as ServerIcon, KeyRound, Copy, Check,
} from 'lucide-react'

import { xuiApi } from '@/api/client'
import { useT } from '@/hooks/useT'
import { useConfirm } from '@/components/ConfirmModal'
import { ModalShell } from '@/components/ModalShell'
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
    <div className="space-y-4">
      <header className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-xl font-semibold text-gray-100 flex items-center gap-2">
            <Layers className="h-5 w-5 text-brand-400" />
            {t('x-ui panels', 'Панели x-ui')}
          </h1>
          <p className="text-xs text-gray-500 mt-0.5">
            {t(
              'Manage VLESS / Trojan / SOCKS inbounds on x-ui-pro / 3x-ui panels deployed via PiTun.',
              'Управление VLESS / Trojan / SOCKS инбаундами на панелях x-ui-pro / 3x-ui, развёрнутых через PiTun.',
            )}
          </p>
        </div>
      </header>

      {srvLoading && (
        <div className="rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-6 flex items-center justify-center gap-2 text-sm text-gray-500">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t('Loading panels…', 'Загрузка панелей…')}
        </div>
      )}

      {srvError && (
        <div className="rounded-lg border border-red-700/40 bg-red-900/20 px-3 py-3 text-sm text-red-300 flex items-start gap-2">
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
                  ? 'bg-brand-600/10 text-brand-200'
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
                    'text-[10px] px-1.5 py-0.5 rounded font-mono ' +
                    (s.mode === 'xui-pro'
                      ? 'bg-purple-900/30 text-purple-300 border border-purple-700/40'
                      : 'bg-gray-900 text-gray-400 border border-gray-700')
                  }>{s.mode}</span>
                  {s.last_check_error
                    ? <ShieldAlert className="h-3 w-3 text-red-400" />
                    : <ShieldCheck className="h-3 w-3 text-emerald-400" />}
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

  const { data: inbounds = [], isLoading, error, refetch } = useQuery<XuiInbound[]>({
    queryKey: ['xui', 'inbounds', server.id],
    queryFn: () => xuiApi.listInbounds(server.id),
    refetchOnWindowFocus: false,
  })

  const probeMut = useMutation({
    mutationFn: () => xuiApi.probeServer(server.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['xui', 'servers'] })
    },
  })

  const delInboundMut = useMutation({
    mutationFn: (id: number) => xuiApi.deleteInbound(server.id, id),
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
              className="text-brand-400 hover:text-brand-300 inline-flex items-center gap-1"
            >
              {t('Open panel', 'Открыть панель')}
              <ExternalLink className="h-3 w-3" />
            </a>
            <span className="text-gray-700">·</span>
            <span className="font-mono">{server.panel_user}</span>
          </div>
          {server.last_check_error && (
            <div className="mt-2 rounded-md bg-red-900/20 border border-red-700/40 px-2 py-1 text-[11px] text-red-300 flex items-start gap-1.5">
              <AlertTriangle className="h-3 w-3 mt-0.5 shrink-0" />
              <span>{server.last_check_error}</span>
            </div>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => probeMut.mutate()}
            disabled={probeMut.isPending}
            className="rounded-lg border border-gray-700 hover:bg-gray-800 px-2.5 py-1.5 text-xs text-gray-300 inline-flex items-center gap-1.5"
            title={t('Re-test the Bearer token', 'Проверить Bearer-токен')}
          >
            {probeMut.isPending
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <ShieldCheck className="h-3.5 w-3.5" />}
            {t('Probe', 'Проверить')}
          </button>
          <button
            type="button"
            onClick={() => refetch()}
            className="rounded-lg border border-gray-700 hover:bg-gray-800 px-2.5 py-1.5 text-xs text-gray-300 inline-flex items-center gap-1.5"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            {t('Refresh', 'Обновить')}
          </button>
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
        <div className="rounded-lg border border-red-700/40 bg-red-900/20 px-3 py-2 text-sm text-red-300">
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
          onClose={() => setShowAddClientFor(null)}
          onCreated={() => {
            setShowAddClientFor(null)
            qc.invalidateQueries({ queryKey: ['xui', 'inbounds', server.id] })
          }}
        />
      )}
    </section>
  )
}


// ── Inbound card ────────────────────────────────────────────────────────────

function InboundCard({
  inbound, onAddClient, onDelete, removing,
}: {
  inbound: XuiInbound
  onAddClient: () => void
  onDelete: () => void
  removing: boolean
}) {
  const t = useT()
  // Parse the panel's JSON-in-JSON settings to count clients.
  let clientCount = 0
  try {
    const s = JSON.parse(inbound.settings)
    const arr = s.clients ?? s.accounts ?? []
    clientCount = Array.isArray(arr) ? arr.length : 0
  } catch { /* ignore */ }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3 space-y-2">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-gray-100">{inbound.remark || `inbound-${inbound.id}`}</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded font-mono bg-gray-800 text-gray-400 uppercase">
              {inbound.protocol}
            </span>
            <span className="text-[11px] text-gray-500 font-mono">:{inbound.port}</span>
            {!inbound.enable && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-yellow-900/30 text-yellow-300 border border-yellow-700/40">
                disabled
              </span>
            )}
          </div>
          <div className="text-[11px] text-gray-500 mt-0.5">
            {t(`${clientCount} client${clientCount === 1 ? '' : 's'}`, `${clientCount} клиент${clientCount === 1 ? '' : 'ов'}`)}
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={onAddClient}
            className="rounded-md border border-gray-700 hover:bg-gray-800 px-2 py-1 text-[11px] text-gray-300 inline-flex items-center gap-1"
          >
            <Plus className="h-3 w-3" />
            {t('Add client', 'Добавить клиента')}
          </button>
          <button
            type="button"
            onClick={onDelete}
            disabled={removing}
            title={t('Delete inbound', 'Удалить инбаунд')}
            className="rounded-md border border-gray-700 hover:bg-red-900/30 hover:border-red-700/40 hover:text-red-300 p-1 text-gray-500 disabled:opacity-50"
          >
            {removing
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Trash2 className="h-3.5 w-3.5" />}
          </button>
        </div>
      </div>
    </div>
  )
}


// ── Add inbound modal ──────────────────────────────────────────────────────

function AddInboundModal({
  server, onClose, onCreated,
}: {
  server: XuiServer
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

  const preset = presets.find((p) => p.id === presetId) ?? null

  // Pre-populate defaults when picking a preset.
  useEffect(() => {
    if (!preset) return
    const next: Record<string, string> = {}
    for (const f of preset.fields) {
      next[f.name] = f.default ?? ''
    }
    setValues(next)
    setError('')
  }, [preset])

  const createMut = useMutation({
    mutationFn: () => {
      if (!preset) throw new Error('no preset')
      return xuiApi.createInbound(server.id, {
        preset_id: preset.id,
        values: { ...values },
      })
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
          <div className="mb-3 rounded-lg bg-red-900/30 border border-red-700/40 px-3 py-2 text-sm text-red-300 flex items-start gap-2">
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
            {presets.map((p) => {
              const blockedByDomain = p.needs_domain && server.mode === 'bare'
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => !blockedByDomain && setPresetId(p.id)}
                  disabled={blockedByDomain}
                  title={blockedByDomain
                    ? t('Needs a domain. Redeploy panel in xui-pro mode.', 'Нужен домен. Переустановите панель в режиме xui-pro.')
                    : undefined
                  }
                  className={
                    'rounded-lg border px-3 py-2 text-left transition-colors ' +
                    (blockedByDomain
                      ? 'border-gray-800 bg-gray-900/20 text-gray-600 cursor-not-allowed opacity-50'
                      : (p.id === presetId
                        ? 'border-brand-500/60 bg-brand-600/10 text-brand-200'
                        : 'border-gray-800 bg-gray-900/40 text-gray-400 hover:border-gray-700 hover:text-gray-200'))
                  }
                >
                  <div className="text-sm font-medium flex items-center gap-1.5 flex-wrap">
                    <span>{p.label}</span>
                    {p.supports_reality && (
                      <span className="text-[10px] px-1 py-0.5 rounded bg-purple-900/30 text-purple-300 border border-purple-700/40 font-mono">
                        reality
                      </span>
                    )}
                    {p.needs_domain && (
                      <span className="text-[10px] px-1 py-0.5 rounded bg-blue-900/30 text-blue-300 border border-blue-700/40 font-mono">
                        domain
                      </span>
                    )}
                  </div>
                  <div className="text-[11px] text-gray-500 mt-0.5 leading-snug">
                    {p.description}
                  </div>
                </button>
              )
            })}
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
                  className="w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
                />
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
  serverId, inboundId, onClose, onCreated,
}: {
  serverId: number
  inboundId: number
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
      }),
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

  const copy = (txt: string) => {
    navigator.clipboard.writeText(txt).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    })
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
          <div className="mb-3 rounded-lg bg-red-900/30 border border-red-700/40 px-3 py-2 text-sm text-red-300 flex items-start gap-2">
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
                className="w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
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
            <div className="rounded-lg border border-emerald-700/40 bg-emerald-900/10 px-3 py-2 text-sm text-emerald-200 flex items-start gap-2">
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
                  <code className="flex-1 rounded bg-gray-900 border border-gray-800 px-2 py-1 text-xs font-mono text-gray-200 break-all">
                    {created.client_uuid}
                  </code>
                  <button
                    type="button"
                    onClick={() => copy(created.client_uuid)}
                    className="rounded-md border border-gray-700 hover:bg-gray-800 p-1.5 text-gray-400 hover:text-gray-200"
                    title={t('Copy UUID', 'Копировать UUID')}
                  >
                    {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
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
