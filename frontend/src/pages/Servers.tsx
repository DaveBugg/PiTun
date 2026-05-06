import { useState } from 'react'
import * as React from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  Cloud, Plus, Pencil, Trash2, Activity, ActivitySquare,
  Download, Clock, Wifi, WifiOff,
  HelpCircle, FileCode2, Terminal,
  Sparkles, Link2,
  FileDown, FileUp,
} from 'lucide-react'

import { serversApi, scriptsApi } from '@/api/client'
import { ServerForm } from '@/components/ServerForm'
import { ModalShell } from '@/components/ModalShell'
import { useConfirm } from '@/components/ConfirmModal'
import {
  useServers,
  useCreateServer,
  useUpdateServer,
  useDeleteServer,
  useTestServer,
  useTestAllServers,
  useDeployments,
  useUpsertDeployment,
  useCreateNodeFromDeployment,
} from '@/hooks/useServers'
import { useT } from '@/hooks/useT'
import type { Server, ServerCreate, ServerUpdate } from '@/types'

/**
 * Servers page — list of SSH-reachable VPS instances the user manages from
 * PiTun. Phase 1 supports CRUD + connection probe + downloadable naive
 * install bootstrap. Auto-deploy via SSH lands in Phase 2.
 *
 * The page is intentionally compact: a single table with row-level
 * actions, an "Add" button, and a "Test all" button. No bulk operations
 * yet — the typical user has 1-5 servers, not 50.
 */
export function Servers() {
  const t = useT()
  const confirm = useConfirm()

  const { data: servers = [], isLoading } = useServers()
  const createServer = useCreateServer()
  const updateServer = useUpdateServer()
  const deleteServer = useDeleteServer()
  const testServer = useTestServer()
  const testAll = useTestAllServers()

  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Server | null>(null)
  // Naive-script modal can run in two modes:
  //   - server-bound (target = a specific Server, header tagged)
  //   - manual (no Server registered, generic header)
  // Both share the same form fields and Blob-download flow.
  const [scriptModal, setScriptModal] = useState<
    | { kind: 'server'; server: Server }
    | { kind: 'manual' }
    | null
  >(null)

  const openAdd = () => {
    setEditing(null)
    setShowForm(true)
  }

  const openEdit = (s: Server) => {
    setEditing(s)
    setShowForm(true)
  }

  const closeForm = () => {
    setShowForm(false)
    setEditing(null)
  }

  const handleSubmit = async (data: ServerCreate | ServerUpdate) => {
    if (editing) {
      await updateServer.mutateAsync({ id: editing.id, data })
    } else {
      await createServer.mutateAsync(data as ServerCreate)
    }
  }

  const handleDelete = async (s: Server) => {
    const ok = await confirm({
      title: t('Delete server?', 'Удалить сервер?'),
      body: t(
        `"${s.name}" will be removed from PiTun. Linked nodes will lose their server reference but will keep working.`,
        `"${s.name}" будет удалён из PiTun. Привязанные ноды потеряют ссылку на сервер, но продолжат работать.`,
      ),
      confirmLabel: t('Delete', 'Удалить'),
      danger: true,
    })
    if (ok) deleteServer.mutate(s.id)
  }

  return (
    <div className="p-4 md:p-6">
      <header className="mb-5 flex flex-wrap items-center gap-3">
        <Cloud className="h-6 w-6 text-brand-400" />
        <h1 className="text-2xl font-semibold text-gray-100">
          {t('Servers', 'Серверы')}
        </h1>
        <p className="text-sm text-gray-500 hidden md:block">
          {t(
            'VPS instances you manage from PiTun — store SSH access + deploy scripts',
            'Ваши VPS — храним доступ по SSH и разворачиваем скрипты установки',
          )}
        </p>
        <div className="ml-auto flex gap-2 flex-wrap">
          <button
            onClick={() => testAll.mutate()}
            disabled={testAll.isPending || servers.length === 0}
            className="rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800 disabled:opacity-50 transition-colors flex items-center gap-1.5"
            title={t('Test SSH connection on every server', 'Проверить SSH-соединение со всеми серверами')}
          >
            <ActivitySquare className="h-4 w-4" />
            {testAll.isPending ? t('Pinging…', 'Проверка…') : t('Test all', 'Проверить все')}
          </button>
          <ServersJsonIO />
          <button
            onClick={openAdd}
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-500 transition-colors flex items-center gap-1.5"
          >
            <Plus className="h-4 w-4" />
            {t('Add server', 'Добавить')}
          </button>
        </div>
      </header>

      {/* Manual scripts — always visible, not gated on server presence.
          The user might want to grab the install bootstrap before
          registering anything (the typical first-time flow). One card
          per available script; today there's only naive, future cards
          for WG / Hy2 will land here. */}
      <ManualScriptsSection onRunNaive={() => setScriptModal({ kind: 'manual' })} />

      {/* Empty state */}
      {!isLoading && servers.length === 0 && (
        <div className="rounded-2xl border border-dashed border-gray-800 bg-gray-900/30 p-10 text-center">
          <Cloud className="h-10 w-10 text-gray-600 mx-auto mb-3" />
          <h2 className="text-lg font-medium text-gray-300">
            {t('No servers yet', 'Серверов пока нет')}
          </h2>
          <p className="mt-1 text-sm text-gray-500 max-w-md mx-auto">
            {t(
              'Add your VPS to keep its SSH access in one place and install NaiveProxy / WireGuard with one command.',
              'Добавьте VPS, чтобы держать SSH-доступ в одном месте и устанавливать NaiveProxy / WireGuard одной командой.',
            )}
          </p>
          <button
            onClick={openAdd}
            className="mt-4 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 inline-flex items-center gap-2"
          >
            <Plus className="h-4 w-4" />
            {t('Add the first server', 'Добавить первый сервер')}
          </button>
        </div>
      )}

      {/* Table */}
      {servers.length > 0 && (
        <div className="rounded-2xl border border-gray-800 bg-gray-900/30 overflow-hidden">
          <table className="w-full text-sm">
            <thead className="text-xs uppercase tracking-wider text-gray-500 bg-gray-900/60">
              <tr>
                <th className="px-4 py-3 text-left">{t('Status', 'Статус')}</th>
                <th className="px-4 py-3 text-left">{t('Name', 'Название')}</th>
                <th className="px-4 py-3 text-left">{t('Address', 'Адрес')}</th>
                <th className="px-4 py-3 text-left">{t('Auth', 'Авторизация')}</th>
                <th className="px-4 py-3 text-left">{t('Last check', 'Последняя проверка')}</th>
                <th className="px-4 py-3"></th>
              </tr>
            </thead>
            <tbody>
              {servers.map((s) => (
                <ServerRow
                  key={s.id}
                  server={s}
                  testing={testServer.isPending && testServer.variables === s.id}
                  onTest={() => testServer.mutate(s.id)}
                  onEdit={() => openEdit(s)}
                  onDelete={() => handleDelete(s)}
                  onShowScript={() => setScriptModal({ kind: 'server', server: s })}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}

      {showForm && (
        <ServerForm
          initial={editing}
          onClose={closeForm}
          onSubmit={handleSubmit}
        />
      )}

      {scriptModal && (
        <NaiveScriptModal
          mode={scriptModal}
          onClose={() => setScriptModal(null)}
        />
      )}
    </div>
  )
}

// ── Manual scripts section ──────────────────────────────────────────────────
//
// Cards block above the servers table. Always visible — even when there
// are no servers yet — so the typical "buy VPS, get script, run it"
// flow doesn't require a server registration first.

function ManualScriptsSection({ onRunNaive }: { onRunNaive: () => void }) {
  const t = useT()
  return (
    <section className="mb-5">
      <h2 className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-gray-500">
        <Terminal className="h-3.5 w-3.5" />
        {t('Manual scripts', 'Скрипты для ручной установки')}
        <span className="ml-2 normal-case text-[11px] text-gray-600 tracking-normal">
          {t(
            '— download and run on your VPS as root',
            '— скачайте и запустите на VPS под root',
          )}
        </span>
      </h2>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <ScriptCard
          icon={FileCode2}
          title="NaiveProxy"
          subtitle={t(
            'Caddy + forward_proxy on a fresh VPS',
            'Caddy + forward_proxy на чистом VPS',
          )}
          description={t(
            'Issues a Let\'s Encrypt cert, sets up the forward-proxy plugin, prints the naive+https:// URI to import into Nodes.',
            'Получит сертификат Let\'s Encrypt, настроит forward-proxy, напечатает naive+https:// URI для импорта в Nodes.',
          )}
          actionLabel={t('Configure & download', 'Настроить и скачать')}
          onAction={onRunNaive}
        />
      </div>
    </section>
  )
}

function ScriptCard({
  icon: Icon,
  title,
  subtitle,
  description,
  actionLabel,
  onAction,
}: {
  icon: typeof FileCode2
  title: string
  subtitle: string
  description: string
  actionLabel: string
  onAction: () => void
}) {
  return (
    <div className="rounded-2xl border border-gray-800 bg-gray-900/40 p-4 flex flex-col">
      <div className="flex items-start gap-3">
        <div className="rounded-lg bg-brand-600/15 p-2 text-brand-400">
          <Icon className="h-5 w-5" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-semibold text-gray-100">{title}</div>
          <div className="text-xs text-gray-500 mt-0.5">{subtitle}</div>
        </div>
      </div>
      <p className="mt-3 text-xs text-gray-400 leading-relaxed flex-1">
        {description}
      </p>
      <button
        onClick={onAction}
        className="mt-3 inline-flex items-center justify-center gap-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-200 text-xs font-medium px-3 py-2 transition-colors"
      >
        <Download className="h-3.5 w-3.5" />
        {actionLabel}
      </button>
    </div>
  )
}

// ── Row ─────────────────────────────────────────────────────────────────────

interface RowProps {
  server: Server
  testing: boolean
  onTest: () => void
  onEdit: () => void
  onDelete: () => void
  onShowScript: () => void
}

function ServerRow({ server, testing, onTest, onEdit, onDelete, onShowScript }: RowProps) {
  const t = useT()
  const lastCheck = server.last_check
    ? new Date(server.last_check).toLocaleString()
    : null

  // Pull this server's deployments so we can show a "naive configured"
  // badge + "Create node" action right on the row. One query per row is
  // fine — the list is short (typical user has 1-5 servers) and the
  // payload is tiny (1 row per protocol per server).
  const { data: deployments = [] } = useDeployments(server.id)
  const naive = deployments.find((d) => d.protocol === 'naive')
  const createNode = useCreateNodeFromDeployment()

  const handleCreateNode = () => {
    createNode.mutate({ serverId: server.id, protocol: 'naive' })
  }

  return (
    <tr className="border-t border-gray-800/60 hover:bg-gray-900/40">
      <td className="px-4 py-3">
        <StatusBadge status={server.status} latency={server.latency_ms ?? null} />
      </td>
      <td className="px-4 py-3">
        <div className="font-medium text-gray-100">{server.name}</div>
        {server.description && (
          <div className="text-xs text-gray-500 mt-0.5 line-clamp-1">{server.description}</div>
        )}
        {naive && <DeploymentBadge deployment={naive} onCreateNode={handleCreateNode} pending={createNode.isPending} />}
      </td>
      <td className="px-4 py-3">
        <div className="text-gray-300 font-mono text-xs">
          {server.user}@{server.host}:{server.port}
        </div>
      </td>
      <td className="px-4 py-3">
        <span className="rounded bg-gray-800 px-2 py-0.5 text-xs text-gray-400">
          {server.auth_type === 'password' ? t('password', 'пароль') : t('key', 'ключ')}
        </span>
      </td>
      <td className="px-4 py-3 text-xs text-gray-500">
        {server.last_check_error ? (
          <span className="text-red-400" title={server.last_check_error}>
            {server.last_check_error.slice(0, 40)}{server.last_check_error.length > 40 ? '…' : ''}
          </span>
        ) : lastCheck ? (
          <span className="inline-flex items-center gap-1">
            <Clock className="h-3 w-3" />
            {lastCheck}
          </span>
        ) : (
          <span className="text-gray-600">—</span>
        )}
      </td>
      <td className="px-4 py-3">
        <div className="flex justify-end gap-1">
          <IconBtn
            onClick={onTest}
            title={t('Test SSH connection', 'Проверить SSH')}
            disabled={testing}
            icon={Activity}
            spinning={testing}
          />
          <IconBtn
            onClick={onShowScript}
            title={t('Get NaiveProxy install script', 'Получить скрипт установки NaiveProxy')}
            icon={Download}
          />
          <IconBtn
            onClick={onEdit}
            title={t('Edit', 'Редактировать')}
            icon={Pencil}
          />
          <IconBtn
            onClick={onDelete}
            title={t('Delete', 'Удалить')}
            icon={Trash2}
            danger
          />
        </div>
      </td>
    </tr>
  )
}

function StatusBadge({ status, latency }: { status: string; latency: number | null }) {
  const t = useT()
  if (status === 'online') {
    return (
      <span className="inline-flex items-center gap-1 text-green-400 text-xs">
        <Wifi className="h-3.5 w-3.5" />
        <span>{t('online', 'онлайн')}</span>
        {latency !== null && (
          <span className="text-gray-500">({latency}ms)</span>
        )}
      </span>
    )
  }
  if (status === 'offline') {
    return (
      <span className="inline-flex items-center gap-1 text-red-400 text-xs">
        <WifiOff className="h-3.5 w-3.5" />
        <span>{t('offline', 'офлайн')}</span>
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 text-gray-500 text-xs">
      <HelpCircle className="h-3.5 w-3.5" />
      <span>{t('unknown', 'неизвестно')}</span>
    </span>
  )
}

// ── Deployment status badge under the server name ──────────────────────────
//
// Shown when a deployment plan exists for this server. Three visual states:
//   1. Has plan, no node yet → "Naive configured · Create node →" (clickable)
//   2. Has plan + linked node → "Naive deployed · linked to Node #X" (info only)
//   3. Status === 'failed' → "Naive setup failed" (red, info only)

function DeploymentBadge({
  deployment,
  onCreateNode,
  pending,
}: {
  deployment: import('@/types').ServerDeployment
  onCreateNode: () => void
  pending: boolean
}) {
  const t = useT()
  const updated = deployment.updated_at
    ? new Date(deployment.updated_at).toLocaleDateString()
    : null

  if (deployment.last_node_id) {
    return (
      <div className="mt-1 inline-flex items-center gap-1.5 text-[11px] text-blue-400">
        <Link2 className="h-3 w-3" />
        <span>
          {t('Naive deployed', 'Naive развернут')} ·{' '}
          {t('linked to Node', 'привязан к Node')} #{deployment.last_node_id}
        </span>
      </div>
    )
  }

  if (deployment.status === 'failed') {
    return (
      <div className="mt-1 text-[11px] text-red-400">
        {t('Naive setup failed', 'Установка Naive не удалась')}
      </div>
    )
  }

  // configured — has plan, no node yet, offer one-click creation
  return (
    <div className="mt-1 inline-flex items-center gap-2 text-[11px]">
      <span className="text-gray-500">
        <Sparkles className="inline h-3 w-3 mr-1 text-yellow-500" />
        {t('Naive configured', 'Naive настроен')}
        {updated && <span className="text-gray-600"> · {updated}</span>}
      </span>
      <button
        onClick={onCreateNode}
        disabled={pending}
        className="rounded bg-brand-600/20 hover:bg-brand-600/30 text-brand-300 px-1.5 py-0.5 text-[11px] font-medium disabled:opacity-50 transition-colors"
        title={t(
          'Create a Node from this deployment (use after running the script on the VPS)',
          'Создать Node из этого deployment’а (когда скрипт уже выполнен на VPS)',
        )}
      >
        {pending ? t('Creating…', 'Создание…') : t('Create node →', 'Создать Node →')}
      </button>
    </div>
  )
}

function IconBtn({
  onClick,
  title,
  icon: Icon,
  disabled,
  danger,
  spinning,
}: {
  onClick: () => void
  title: string
  icon: typeof Activity
  disabled?: boolean
  danger?: boolean
  spinning?: boolean
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={`rounded p-1.5 transition-colors disabled:opacity-50 ${
        danger
          ? 'text-gray-500 hover:bg-red-900/30 hover:text-red-400'
          : 'text-gray-500 hover:bg-gray-800 hover:text-gray-200'
      }`}
    >
      <Icon className={`h-4 w-4 ${spinning ? 'animate-spin' : ''}`} />
    </button>
  )
}

// ── Naive install script modal ──────────────────────────────────────────────
//
// Asks the user for `domain` + `email`, then triggers a download of the
// pre-filled bash bootstrap. The actual script generation happens on the
// backend (server-side jinja-style template) — we just collect the
// parameters and call `serversApi.downloadNaiveInstallScript`.

type ScriptModalMode =
  | { kind: 'server'; server: Server }
  | { kind: 'manual' }

function NaiveScriptModal({ mode, onClose }: { mode: ScriptModalMode; onClose: () => void }) {
  const t = useT()

  // Pre-fill from existing deployment when this modal is opened on a
  // specific server. For manual mode there's no deployment to fetch.
  const serverIdForFetch = mode.kind === 'server' ? mode.server.id : null
  const { data: deployments = [] } = useDeployments(serverIdForFetch)
  const existingNaive = deployments.find((d) => d.protocol === 'naive')

  const [domain, setDomain] = useState(existingNaive?.config.domain ?? '')
  const [email, setEmail] = useState(existingNaive?.config.email ?? '')
  const [naiveUser, setNaiveUser] = useState(
    existingNaive?.config.naive_user ?? 'pitun',
  )
  const [naivePass, setNaivePass] = useState(existingNaive?.config.naive_pass ?? '')
  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState('')

  const upsertDeployment = useUpsertDeployment()

  /** Validate + assemble params used by both Save and Save&Download. */
  const buildParams = (): { domain: string; email: string; naive_user?: string; naive_pass: string } | null => {
    if (!domain.trim() || !email.trim()) {
      setError(t('Domain and email are required', 'Domain и email обязательны'))
      return null
    }
    // If password left blank: reuse saved one (deployment update without
    // password change), or generate a new one client-side. We always
    // know the value so the saved deployment matches what the script
    // would print on the VPS.
    const finalPass =
      naivePass.trim() ||
      existingNaive?.config.naive_pass ||
      generateRandomPassword()
    return {
      domain: domain.trim(),
      email: email.trim(),
      naive_user: naiveUser.trim() || undefined,
      naive_pass: finalPass,
    }
  }

  /** Persist the deployment plan on a Server. Manual mode is a no-op. */
  const persist = async (params: NonNullable<ReturnType<typeof buildParams>>) => {
    if (mode.kind !== 'server') return
    await upsertDeployment.mutateAsync({
      serverId: mode.server.id,
      protocol: 'naive',
      data: {
        protocol: 'naive',
        config: {
          domain: params.domain,
          email: params.email,
          naive_user: params.naive_user,
          naive_pass: params.naive_pass,
        },
      },
    })
  }

  /** Trigger the .sh download via Blob, server-bound or manual. */
  const download = async (params: NonNullable<ReturnType<typeof buildParams>>) => {
    if (mode.kind === 'server') {
      await serversApi.downloadNaiveInstallScript(mode.server.id, params)
    } else {
      await scriptsApi.downloadNaiveInstall(params)
    }
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    const params = buildParams()
    if (!params) return
    setDownloading(true)
    try {
      await persist(params)
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setDownloading(false)
    }
  }

  const handleSaveAndDownload = async () => {
    setError('')
    const params = buildParams()
    if (!params) return
    setDownloading(true)
    try {
      await persist(params)
      await download(params)
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Download failed')
    } finally {
      setDownloading(false)
    }
  }

  // Manual mode: only download makes sense (no Server to attach to).
  const handleDownloadOnly = async () => {
    setError('')
    const params = buildParams()
    if (!params) return
    setDownloading(true)
    try {
      await download(params)
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Download failed')
    } finally {
      setDownloading(false)
    }
  }

  // Subtitle shows the target server when in server-bound mode; manual
  // mode gets a generic "no server registered" line so the user knows
  // they're getting the standalone version.
  const subtitle =
    mode.kind === 'server'
      ? t(
          `Generates a pre-filled bash bootstrap for ${mode.server.name}. Download it, then run on the VPS as root.`,
          `Скачайте готовый bash-скрипт для ${mode.server.name} и запустите на VPS под root.`,
        )
      : t(
          'Download a self-contained installer; run it on any fresh VPS as root.',
          'Самодостаточный установщик — запустите на любом чистом VPS под root.',
        )

  // Default form submit (Enter in any input) hits the primary action:
  // "Save" in server mode, "Download" in manual mode. Reach the
  // "Save & download" path explicitly with its own button.
  const onFormSubmit = mode.kind === 'server' ? handleSave : handleDownloadOnly

  return (
    <ModalShell onClose={onClose} labelledBy="naive-script-title">
      <form
        onSubmit={onFormSubmit}
        className="w-full max-w-lg rounded-2xl bg-gray-950/95 border border-gray-800 p-6 m-4"
      >
        <h2 id="naive-script-title" className="text-lg font-semibold text-gray-100 mb-1">
          {t('NaiveProxy install script', 'Скрипт установки NaiveProxy')}
        </h2>
        <p className="text-xs text-gray-500 mb-4">{subtitle}</p>

        {error && (
          <div className="mb-3 rounded-lg bg-red-900/30 border border-red-700/50 px-3 py-2 text-sm text-red-300">
            {error}
          </div>
        )}

        <div className="space-y-3">
          <FieldL label={t('Domain', 'Домен')} hint={t('A-record points to the VPS', 'A-запись указывает на VPS')}>
            <input
              type="text"
              value={domain}
              onChange={(e) => setDomain(e.target.value)}
              placeholder="proxy.example.com"
              className={inputCls}
              required
              autoFocus
            />
          </FieldL>
          <FieldL label={t("Let's Encrypt email", 'Email для Let\'s Encrypt')}>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="me@example.com"
              className={inputCls}
              required
            />
          </FieldL>
          <FieldL label={t('Naive username', 'Имя пользователя Naive')} hint={t('default "pitun"', 'по умолчанию "pitun"')}>
            <input
              type="text"
              value={naiveUser}
              onChange={(e) => setNaiveUser(e.target.value)}
              className={inputCls}
            />
          </FieldL>
          <FieldL
            label={t('Naive password', 'Пароль Naive')}
            hint={t(
              'leave blank — auto-generated; saved with the deployment',
              'оставьте пустым — сгенерируется автоматически и сохранится',
            )}
          >
            <input
              type="text"
              value={naivePass}
              onChange={(e) => setNaivePass(e.target.value)}
              className={inputCls}
            />
          </FieldL>
        </div>

        {/* Footer button row.
            Server mode: 3 buttons — Cancel, Save, Save & download.
              "Save" persists the deployment plan (so the row gets the
              "Naive configured" badge and Create-Node becomes possible)
              without producing the .sh — handy when the user already
              downloaded earlier and just wants to update domain/email.
            Manual mode: 2 buttons — Cancel, Download. Nothing to save
              because there's no Server to attach the plan to.            */}
        <div className="flex gap-2 pt-5">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-400 hover:bg-gray-800 transition-colors"
          >
            {t('Cancel', 'Отмена')}
          </button>

          {mode.kind === 'server' && (
            <button
              type="submit"
              disabled={downloading}
              className="flex-1 rounded-lg border border-brand-600 bg-brand-600/15 hover:bg-brand-600/25 text-brand-300 px-4 py-2 text-sm font-medium disabled:opacity-50 transition-colors"
              title={t(
                'Save the deployment plan without downloading the script',
                'Сохранить deployment без скачивания скрипта',
              )}
            >
              {downloading ? t('Saving…', 'Сохранение…') : t('Save', 'Сохранить')}
            </button>
          )}

          <button
            type="button"
            onClick={mode.kind === 'server' ? handleSaveAndDownload : handleDownloadOnly}
            disabled={downloading}
            className="flex-1 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors flex items-center justify-center gap-1.5"
          >
            <Download className="h-4 w-4" />
            {downloading
              ? t('Generating…', 'Генерация…')
              : mode.kind === 'server'
              ? t('Save & download .sh', 'Сохранить и скачать .sh')
              : t('Download .sh', 'Скачать .sh')}
          </button>
        </div>
      </form>
    </ModalShell>
  )
}

const inputCls =
  'w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none'

/**
 * Generate a 24-byte URL-safe random password (32 chars). Mirrors the
 * backend's `secrets.token_urlsafe(24)` shape so user-side and
 * server-side auto-gen are interchangeable. Generating client-side lets
 * us PUT the same value into the saved deployment AND into the
 * downloaded script in one round-trip.
 */
function generateRandomPassword(): string {
  const bytes = new Uint8Array(24)
  crypto.getRandomValues(bytes)
  // base64url: replace + with -, / with _, strip =
  let b64 = btoa(String.fromCharCode(...bytes))
  return b64.replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
}

function FieldL({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <div className="mb-1.5 flex items-center gap-2 text-xs">
        <span className="text-gray-500">{label}</span>
        {hint && <span className="text-gray-600">— {hint}</span>}
      </div>
      {children}
    </label>
  )
}


// ── JSON backup / restore ───────────────────────────────────────────────────
//
// Mirror of NodesJsonIO with one extra concern: SSH credentials. The
// export endpoint accepts `?include_secrets=true`, but we ask the user
// explicitly via a confirm dialog before opting in — leaking a JSON
// file with plaintext SSH passwords/keys would be much worse than
// leaking a list of node configs. Default = strip secrets.

function ServersJsonIO() {
  const t = useT()
  const fileRef = React.useRef<HTMLInputElement | null>(null)
  const qc = useQueryClient()
  const confirm = useConfirm()

  const handleExport = async () => {
    const includeSecrets = await confirm({
      title: t('Export servers — include secrets?', 'Экспорт серверов — включить секреты?'),
      body: (
        <>
          <p className="mb-2 text-sm text-gray-300">
            {t(
              'Should the export include SSH passwords / private keys?',
              'Включать ли в экспорт SSH-пароли и приватные ключи?',
            )}
          </p>
          <ul className="text-xs text-gray-400 space-y-1 list-disc list-inside">
            <li>
              <b className="text-gray-200">{t('Cancel:', 'Отмена:')}</b>{' '}
              {t('exclude secrets — re-enter them after import.', 'без секретов — придётся ввести заново.')}
            </li>
            <li>
              <b className="text-gray-200">OK:</b>{' '}
              {t(
                'include secrets in plaintext — round-trip backup, treat the file like a password vault.',
                'секреты в plaintext — полный backup, относись к файлу как к хранилищу паролей.',
              )}
            </li>
          </ul>
        </>
      ),
      confirmLabel: t('Include secrets', 'Включить'),
      cancelLabel: t('No secrets', 'Без секретов'),
      danger: true,
    })

    try {
      await serversApi.exportJSON(includeSecrets)
    } catch (err: unknown) {
      alert('Export failed: ' + (err instanceof Error ? err.message : String(err)))
    }
  }

  const handlePickFile = () => fileRef.current?.click()

  const handleFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    let bundle: unknown
    try {
      const text = await file.text()
      bundle = JSON.parse(text)
    } catch {
      alert('Invalid JSON file')
      return
    }

    const replace = await confirm({
      title: t('Import servers', 'Импорт серверов'),
      body: (
        <>
          <p className="mb-2 text-sm text-gray-300">
            {t('How should this bundle be applied?', 'Как применить этот файл?')}
          </p>
          <ul className="text-xs text-gray-400 space-y-1 list-disc list-inside">
            <li><b className="text-gray-200">Cancel:</b> {t('abort.', 'отменить.')}</li>
            <li><b className="text-gray-200">OK (Replace):</b> {t('wipe + restore.', 'удалить всё, восстановить.')}</li>
          </ul>
          <p className="mt-3 text-xs text-yellow-500/90">
            {t(
              'Tip: re-run with default replace=off to merge (duplicates by name+host+port skip).',
              'Подсказка: запустить ещё раз без replace для merge (дубли по name+host+port пропустятся).',
            )}
          </p>
        </>
      ),
      confirmLabel: t('Replace all', 'Заменить всё'),
      cancelLabel: 'Cancel',
      danger: true,
    })

    try {
      const result = await serversApi.importJSON(bundle, replace)
      qc.invalidateQueries({ queryKey: ['servers'] })
      const errSuffix = result.errors?.length
        ? `\nErrors:\n${result.errors.slice(0, 5).join('\n')}`
        : ''
      const secretsNote = !result.has_secrets && result.imported > 0
        ? `\n${t('Note: bundle had no secrets — credentials are blank, edit each server to set them.', 'Внимание: в файле нет секретов — заполни их вручную.')}`
        : ''
      alert(
        `Imported: ${result.imported}, skipped: ${result.skipped}${errSuffix}${secretsNote}`,
      )
    } catch (err: unknown) {
      alert('Import failed: ' + (err instanceof Error ? err.message : String(err)))
    }
  }

  return (
    <>
      <input
        ref={fileRef}
        type="file"
        accept="application/json,.json"
        onChange={handleFile}
        className="hidden"
      />
      <button
        onClick={handleExport}
        className="rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800 transition-colors flex items-center gap-1.5"
        title={t('Download all servers as a JSON backup', 'Скачать все серверы как JSON-бэкап')}
      >
        <FileDown className="h-4 w-4" />
        {t('Export', 'Экспорт')}
      </button>
      <button
        onClick={handlePickFile}
        className="rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800 transition-colors flex items-center gap-1.5"
        title={t('Restore servers from a JSON backup', 'Восстановить серверы из JSON-бэкапа')}
      >
        <FileUp className="h-4 w-4" />
        {t('Import', 'Импорт')}
      </button>
    </>
  )
}
