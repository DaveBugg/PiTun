import { useState } from 'react'
import * as React from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  Cloud, Plus, Pencil, Trash2, Activity, ActivitySquare,
  Download, Clock, Wifi, WifiOff,
  HelpCircle, FileCode2, Terminal,
  Sparkles, Link2,
  FileDown, FileUp,
  Rocket, ListChecks, Users, Trash,
  Layers, ExternalLink,
} from 'lucide-react'

import { serversApi, scriptsApi } from '@/api/client'
import { ServerForm } from '@/components/ServerForm'
import { ModalShell } from '@/components/ModalShell'
import { useConfirm } from '@/components/ConfirmModal'
import { DeployModal } from '@/components/DeployModal'
import { ManageClientsModal } from '@/components/ManageClientsModal'
import { TemplatePicker } from '@/components/TemplatePicker'
import { SshPortField } from '@/components/SshPortField'
import { UninstallModal } from '@/components/UninstallModal'
import { DirectToggle } from '@/components/DirectToggle'
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
  useDeploymentClients,
} from '@/hooks/useServers'
import { useT } from '@/hooks/useT'
import type { Server, ServerCreate, ServerUpdate, ServerDeployment, ServerDeploymentProtocol } from '@/types'

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

  // Page-level "Direct connection" — governs every server op launched
  // from this page (test, test-all, deploy, uninstall, WG clients).
  // Off = through the active node (default); on = SO_MARK bypass, for
  // reaching a box while the active node is down.
  const [direct, setDirect] = useState(false)

  const [showForm, setShowForm] = useState(false)
  const [editing, setEditing] = useState<Server | null>(null)
  // Naive-script modal can run in two modes:
  //   - server-bound (target = a specific Server, header tagged)
  //   - manual (no Server registered, generic header)
  // Both share the same form fields and Blob-download flow.
  const [scriptModal, setScriptModal] = useState<
    | { kind: 'server'; server: Server; protocol?: ServerDeploymentProtocol }
    | { kind: 'manual'; protocol?: ServerDeploymentProtocol }
    | null
  >(null)
  // Auto-deploy modal — runs install over SSH and streams the log.
  // (since v1.3.0-beta.1 — companion to the manual-script flow.)
  const [deployTarget, setDeployTarget] = useState<Server | null>(null)
  // Manage WireGuard clients (since v1.3.0-beta.4) — only available on
  // servers that have a WG ServerDeployment row.
  const [clientsTarget, setClientsTarget] = useState<Server | null>(null)
  // Uninstall modal (since v1.3.0-beta.6) — wipes server-side state
  // for one protocol. Tracks both target server AND protocol because
  // a server can have multiple deployments and the user must be
  // explicit about which one to nuke.
  const [uninstallTarget, setUninstallTarget] = useState<{
    server: Server; protocol: ServerDeploymentProtocol
  } | null>(null)

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
          {/* Server-tasks history — async deploy log, accessible only
              from this page (per the v1.3.0 design — not in the main
              sidebar). Show the link once the user has registered
              at least one server, otherwise it's just noise. */}
          {servers.length > 0 && (
            <Link
              to="/server-tasks"
              className="rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800 transition-colors flex items-center gap-1.5"
              title={t('Recent install / deploy jobs', 'Недавние задачи установки / деплоя')}
            >
              <ListChecks className="h-4 w-4" />
              {t('Tasks', 'Задачи')}
            </Link>
          )}
          {servers.length > 0 && (
            <DirectToggle checked={direct} onChange={setDirect} className="px-1" />
          )}
          <button
            onClick={() => testAll.mutate(direct)}
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
      <ManualScriptsSection onRunScript={(protocol) => setScriptModal({ kind: 'manual', protocol })} />

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

      {/* Table — horizontally scrollable on phones since the columns
          (status / name / address / auth / last-check / actions)
          don't fit in 360px without truncating to uselessness. The
          outer rounded card stays in place so the visual frame is
          intact; only the table itself slides. A future iteration
          could collapse to card-list under sm: but the scroll
          version at least makes everything reachable without
          information loss. */}
      {servers.length > 0 && (
        <div className="rounded-2xl border border-gray-800 bg-gray-900/30 overflow-x-auto">
          <table className="w-full min-w-[640px] text-sm">
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
                  testing={testServer.isPending && testServer.variables?.id === s.id}
                  onTest={() => testServer.mutate({ id: s.id, direct })}
                  onEdit={() => openEdit(s)}
                  onDelete={() => handleDelete(s)}
                  onShowScript={() => setScriptModal({ kind: 'server', server: s })}
                  onDeploy={() => setDeployTarget(s)}
                  onManageClients={() => setClientsTarget(s)}
                  onUninstall={(protocol) => setUninstallTarget({ server: s, protocol })}
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
        <ManualScriptModal
          mode={scriptModal}
          onClose={() => setScriptModal(null)}
        />
      )}

      {deployTarget && (
        <DeployModal
          server={deployTarget}
          initialDirect={direct}
          onClose={() => setDeployTarget(null)}
        />
      )}

      {clientsTarget && (
        <ManageClientsModal
          server={clientsTarget}
          direct={direct}
          onClose={() => setClientsTarget(null)}
        />
      )}

      {uninstallTarget && (
        <UninstallModal
          server={uninstallTarget.server}
          protocol={uninstallTarget.protocol}
          direct={direct}
          onClose={() => setUninstallTarget(null)}
          onRedeploy={() => {
            // After successful uninstall, jump straight into deploy
            // for the same server — typical "wipe → reinstall fresh"
            // flow during template / config testing.
            setDeployTarget(uninstallTarget.server)
            setUninstallTarget(null)
          }}
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

function ManualScriptsSection({
  onRunScript,
}: {
  onRunScript: (protocol: ServerDeploymentProtocol) => void
}) {
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
          onAction={() => onRunScript('naive')}
        />
        <ScriptCard
          icon={FileCode2}
          title="WireGuard"
          subtitle={t(
            'wg-quick + first peer on a fresh VPS',
            'wg-quick + первый клиент на чистом VPS',
          )}
          description={t(
            'Installs wireguard-tools, generates server keypair, enables wg-quick@wg0, and adds your first peer. Prints the wireguard:// URI + an INI conf block to scan / import.',
            'Установит wireguard-tools, сгенерирует ключи сервера, включит wg-quick@wg0 и добавит первого клиента. Напечатает wireguard:// URI и INI conf для импорта.',
          )}
          actionLabel={t('Configure & download', 'Настроить и скачать')}
          onAction={() => onRunScript('wireguard')}
        />
        {/* x-ui doesn't have a "configure & download .sh" path on
            purpose — the install flow (random fakesite, nginx, certbot,
            etc.) is too involved to script-and-forget. PiTun's
            SSH-driven Deploy on a registered Server handles it
            end-to-end. For users who want to install manually, link
            out to the upstream repos. */}
        <XuiUpstreamCard />
      </div>
    </section>
  )
}


function XuiUpstreamCard() {
  const t = useT()
  return (
    <div className="rounded-2xl border border-gray-800 bg-gray-900/40 p-4 flex flex-col">
      <div className="flex items-start gap-2 mb-2">
        <div className="rounded-lg bg-purple-50 dark:bg-purple-900/30 border border-purple-200 dark:border-purple-700/40 p-1.5 text-purple-700 dark:text-purple-300">
          <Layers className="h-4 w-4" />
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-gray-100">x-ui-pro / 3x-ui</div>
          <div className="text-[11px] text-gray-500">
            {t('panel-managed Xray inbounds', 'панель для управления Xray-инбаундами')}
          </div>
        </div>
      </div>
      <p className="text-xs text-gray-400 mb-3 leading-snug flex-1">
        {t(
          'No "configure & download" here — the install touches nginx, Let\'s Encrypt and a 268 MB fakesite bundle. Use PiTun → Servers → Deploy → "x-ui" on a registered VPS for the supported flow. If you want to install manually, the upstream repos below also work; once running, import a client URI from the panel into PiTun → Nodes.',
          'Тут нет "настроить и скачать" — установка трогает nginx, Let\'s Encrypt и архив фейк-сайтов на 268 МБ. Через PiTun → Servers → Deploy → "x-ui" на зарегистрированный VPS работает из коробки. Если хочется руками — установите через апстрим-репы ниже, потом импортируйте URI клиента из панели в PiTun → Nodes.',
        )}
      </p>
      <div className="flex flex-col gap-1.5 text-xs">
        <a
          href="https://github.com/GFW4Fun/x-ui-pro"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-brand-400 hover:text-brand-700 dark:hover:text-brand-300"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          GFW4Fun/x-ui-pro
          <span className="text-gray-500">— {t('nginx + fakesite + WARP/Tor', 'nginx + фейк-сайт + WARP/Tor')}</span>
        </a>
        <a
          href="https://github.com/MHSanaei/3x-ui"
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-brand-400 hover:text-brand-700 dark:hover:text-brand-300"
        >
          <ExternalLink className="h-3.5 w-3.5" />
          MHSanaei/3x-ui
          <span className="text-gray-500">— {t('bare panel, Reality-friendly', 'голая панель, удобна для Reality')}</span>
        </a>
      </div>
    </div>
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
        <div className="rounded-lg bg-brand-50 dark:bg-brand-600/15 p-2 text-brand-400">
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
  onDeploy: () => void
  onManageClients: () => void
  /** Open the uninstall modal for the chosen protocol. Only invoked
   * when at least one deployment exists. */
  onUninstall: (protocol: ServerDeploymentProtocol) => void
}

// onManageClients is wired through ServerRow → WireGuardBadge so the
// summary line on the row carries its own jump-to-modal affordance,
// keeping the icon column tidy.
function ServerRow({
  server, testing, onTest, onEdit, onDelete, onShowScript, onDeploy, onManageClients,
  onUninstall,
}: RowProps) {
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
  const wireguard = deployments.find((d) => d.protocol === 'wireguard')
  const xui = deployments.find((d) => d.protocol === 'xui')
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
        {(naive || wireguard || xui) && (
          <div className="mt-1 flex flex-col items-start gap-1">
            {naive && <DeploymentBadge deployment={naive} onCreateNode={handleCreateNode} pending={createNode.isPending} />}
            {wireguard && <WireGuardBadge serverId={server.id} onManageClients={onManageClients} />}
            {xui && <XuiBadge deployment={xui} />}
          </div>
        )}
      </td>
      <td className="px-4 py-3">
        <div className="text-gray-300 font-mono text-xs">
          {server.user}@{server.host}:{server.port}
        </div>
      </td>
      <td className="px-4 py-3">
        <span className="rounded-sm bg-gray-800 px-2 py-0.5 text-xs text-gray-400">
          {server.auth_type === 'password' ? t('password', 'пароль') : t('key', 'ключ')}
        </span>
      </td>
      <td className="px-4 py-3 text-xs text-gray-500">
        {server.last_check_error ? (
          <span className="text-red-600 dark:text-red-400" title={server.last_check_error}>
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
          {/* Auto-deploy via SSH (since v1.3.0-beta.1). Distinct from
              the Download button below — that emits a script the user
              runs manually; this one runs it for them and streams the
              log. We require the server's SSH probe to have succeeded
              at least once, otherwise the deploy will fail loudly on
              the first SFTP call anyway. Allow online OR unknown — a
              fresh server hasn't been tested yet, but the user can
              still try; offline is the only hard block. */}
          <IconBtn
            onClick={onDeploy}
            title={t('Install proxy over SSH', 'Установить прокси по SSH')}
            icon={Rocket}
            disabled={server.status === 'offline'}
          />
          {wireguard && (
            <IconBtn
              onClick={onManageClients}
              title={t('Manage WireGuard clients', 'Управление клиентами WireGuard')}
              icon={Users}
            />
          )}
          {/* Per-protocol "Wipe" buttons (since v1.3.0-beta.6) — one
              icon per ServerDeployment that exists. Distinct from the
              row-level Delete (Trash2) below: that drops the Server
              record from PiTun's DB; this one runs an SSH-driven
              uninstall script that wipes the actual VPS state.
              Hidden entirely when no deployment is set up so the
              icon strip stays compact for fresh servers. */}
          {naive && (
            <IconBtn
              onClick={() => onUninstall('naive')}
              title={t('Wipe NaiveProxy from VPS', 'Удалить NaiveProxy с VPS')}
              icon={Trash}
              danger
              disabled={server.status === 'offline'}
            />
          )}
          {wireguard && (
            <IconBtn
              onClick={() => onUninstall('wireguard')}
              title={t('Wipe WireGuard from VPS', 'Удалить WireGuard с VPS')}
              icon={Trash}
              danger
              disabled={server.status === 'offline'}
            />
          )}
          {xui && (
            <IconBtn
              onClick={() => onUninstall('xui')}
              title={t('Wipe x-ui from VPS', 'Удалить x-ui с VPS')}
              icon={Trash}
              danger
              disabled={server.status === 'offline'}
            />
          )}
          <IconBtn
            onClick={onShowScript}
            title={t('Get install script (naive / WireGuard)', 'Получить скрипт установки (naive / WireGuard)')}
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
      <span className="inline-flex items-center gap-1 text-green-600 dark:text-green-400 text-xs">
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
      <span className="inline-flex items-center gap-1 text-red-600 dark:text-red-400 text-xs">
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
      <div className="mt-1 inline-flex items-center gap-1.5 text-[11px] text-blue-600 dark:text-blue-400">
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
      <div className="mt-1 text-[11px] text-red-600 dark:text-red-400">
        {t('Naive setup failed', 'Установка Naive не удалась')}
      </div>
    )
  }

  // configured — has plan, no node yet, offer one-click creation
  return (
    <div className="inline-flex items-center gap-2 text-[11px]">
      <span className="text-gray-500">
        <Sparkles className="inline h-3 w-3 mr-1 text-yellow-500" />
        {t('Naive configured', 'Naive настроен')}
        {updated && <span className="text-gray-600"> · {updated}</span>}
      </span>
      <button
        onClick={onCreateNode}
        disabled={pending}
        className="rounded-sm bg-brand-50 dark:bg-brand-600/20 hover:bg-brand-50 text-brand-700 dark:bg-brand-600/30 dark:text-brand-300 px-1.5 py-0.5 text-[11px] font-medium disabled:opacity-50 transition-colors"
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


// WireGuard's parallel to DeploymentBadge. Multi-client by nature, so
// instead of "Create node" it links to the Clients modal where the
// admin can add / sync / export peers individually.
function WireGuardBadge({
  serverId, onManageClients,
}: {
  serverId: number
  onManageClients: () => void
}) {
  const t = useT()
  // Cheap query — same one ManageClientsModal uses, so it's cached.
  const { data } = useDeploymentClients(serverId)
  const total = data?.clients.length ?? 0
  const orphans = data?.clients.filter((c) => c.status === 'orphan').length ?? 0
  return (
    <div className="inline-flex items-center gap-2 text-[11px]">
      <span className="text-gray-500">
        <Sparkles className="inline h-3 w-3 mr-1 text-yellow-500" />
        {t('WireGuard configured', 'WireGuard настроен')}
        {data && <span className="text-gray-600"> · {t(`${total} client(s)`, `${total} клиент(ов)`)}</span>}
        {orphans > 0 && (
          <span className="text-yellow-600 dark:text-yellow-400"> · {t(`${orphans} orphan`, `${orphans} осиротевш.`)}</span>
        )}
      </span>
      <button
        onClick={onManageClients}
        className="rounded-sm bg-brand-50 dark:bg-brand-600/20 hover:bg-brand-50 text-brand-700 dark:bg-brand-600/30 dark:text-brand-300 px-1.5 py-0.5 text-[11px] font-medium transition-colors"
        title={t('Open Clients modal', 'Открыть модалку клиентов')}
      >
        {t('Clients →', 'Клиенты →')}
      </button>
    </div>
  )
}

// x-ui's parallel to WireGuardBadge. Multi-client like WG (one VPS
// hosts many vless / trojan / socks inbounds, each with N clients)
// but the management UI lives on its own page (`/xui`) rather than a
// modal — too much surface for the table view. We just surface the
// deployment status + a "Manage" jump-link.
function XuiBadge({ deployment }: { deployment: ServerDeployment }) {
  const t = useT()
  const cfg = (deployment.config ?? {}) as { domain?: string; mode?: string }
  const mode = cfg.mode || 'bare'
  return (
    <div className="inline-flex items-center gap-2 text-[11px]">
      <span className="text-gray-500">
        <Sparkles className="inline h-3 w-3 mr-1 text-yellow-500" />
        {t('x-ui configured', 'x-ui настроен')}
        <span className="text-gray-600 font-mono"> · {mode}</span>
        {cfg.domain && (
          <span className="text-gray-600"> · {cfg.domain}</span>
        )}
      </span>
      <Link
        to="/xui"
        className="rounded-sm bg-brand-50 dark:bg-brand-600/20 hover:bg-brand-50 text-brand-700 dark:bg-brand-600/30 dark:text-brand-300 px-1.5 py-0.5 text-[11px] font-medium transition-colors"
        title={t('Open x-ui page', 'Открыть страницу x-ui')}
      >
        {t('Manage →', 'Управление →')}
      </Link>
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
          ? 'text-gray-500 hover:bg-red-50 dark:hover:bg-red-900/30 hover:text-red-400'
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
  | { kind: 'server'; server: Server; protocol?: ServerDeploymentProtocol }
  | { kind: 'manual'; protocol?: ServerDeploymentProtocol }

/** Unified install-script modal (since v1.3.0-beta.5).
 *
 * Replaces the original NaiveScriptModal with a protocol toggle so naive
 * and wireguard share the same form-collect → save-deployment → download
 * pipeline. Each protocol has its own field set + builder + download
 * call, but the shell (mode handling, button row, save logic, error
 * surface) is shared.
 *
 * `mode.protocol` is just the *initial* selection — once the modal is
 * open the user can switch via the toggle. Form values for the
 * unselected protocol are kept in state so flipping back doesn't lose
 * user input. */
function ManualScriptModal({ mode, onClose }: { mode: ScriptModalMode; onClose: () => void }) {
  const t = useT()

  // Pre-fill from any existing deployment (per protocol) when this modal
  // is opened on a specific server. Manual mode has no deployments.
  const serverIdForFetch = mode.kind === 'server' ? mode.server.id : null
  const { data: deployments = [] } = useDeployments(serverIdForFetch)
  const existingNaive = deployments.find((d) => d.protocol === 'naive')
  const existingWg = deployments.find((d) => d.protocol === 'wireguard')

  // Default the toggle to whichever protocol was clicked; if absent,
  // pick whatever's already configured on this server, falling back
  // to naive (the older / more common path).
  const initialProtocol: ServerDeploymentProtocol =
    mode.protocol ??
    (existingWg ? 'wireguard' : 'naive')
  const [protocol, setProtocol] = useState<ServerDeploymentProtocol>(initialProtocol)

  // Naive-specific config view — narrow the union'd DeploymentConfig.
  const naiveCfg = (existingNaive?.config ?? {}) as {
    domain?: string; email?: string; naive_user?: string; naive_pass?: string
    template_id?: string; install_php?: boolean
  }
  const [domain, setDomain] = useState(naiveCfg.domain ?? '')
  const [email, setEmail] = useState(naiveCfg.email ?? '')
  const [naiveUser, setNaiveUser] = useState(naiveCfg.naive_user ?? 'pitun')
  const [naivePass, setNaivePass] = useState(naiveCfg.naive_pass ?? '')
  const [naiveTemplateId, setNaiveTemplateId] = useState<string | undefined>(naiveCfg.template_id)
  const [naiveInstallPhp, setNaiveInstallPhp] = useState<boolean>(!!naiveCfg.install_php)
  // SSH port move (since v1.3.0-beta.7) — surfaced uniformly across
  // naive + wg sections of this modal; one sshd per VPS so one input.
  const [sshPort, setSshPort] = useState<string>('')

  // WireGuard-specific. `client_name` is per-deploy and not stored on
  // ServerDeployment.config (the script picks it up from env), so it
  // always starts blank. The rest pre-fill from any saved deployment.
  const wgCfg = (existingWg?.config ?? {}) as import('@/types').WireGuardDeploymentConfig
  const [wgClientName, setWgClientName] = useState('')
  const [wgServerPort, setWgServerPort] = useState(wgCfg.server_port?.toString() ?? '51820')
  const [wgDns1, setWgDns1] = useState(wgCfg.dns_1 ?? '1.1.1.1')
  const [wgDns2, setWgDns2] = useState(wgCfg.dns_2 ?? '1.0.0.1')
  const [wgAllowedIps, setWgAllowedIps] = useState(wgCfg.allowed_ips ?? '0.0.0.0/0,::/0')

  const [downloading, setDownloading] = useState(false)
  const [error, setError] = useState('')

  const upsertDeployment = useUpsertDeployment()

  type NaiveParams = {
    domain: string; email: string; naive_user?: string; naive_pass: string
    template_id?: string; install_php?: boolean; ssh_port?: number
  }
  type WgParams = {
    client_name?: string
    server_port?: number
    dns_1?: string
    dns_2?: string
    allowed_ips?: string
    ssh_port?: number
  }

  /** Validate + assemble params for the active protocol. */
  const buildNaiveParams = (): NaiveParams | null => {
    if (!domain.trim() || !email.trim()) {
      setError(t('Domain and email are required', 'Domain и email обязательны'))
      return null
    }
    // Password left blank: reuse saved → else generate client-side so
    // the script + deployment row stay in sync.
    const finalPass = naivePass.trim() || naiveCfg.naive_pass || generateRandomPassword()
    return {
      domain: domain.trim(),
      email: email.trim(),
      naive_user: naiveUser.trim() || undefined,
      naive_pass: finalPass,
      template_id: naiveTemplateId,
      install_php: naiveInstallPhp || undefined,
      ssh_port: sshPort.trim() ? Number(sshPort.trim()) : undefined,
    }
  }

  const buildWgParams = (): WgParams | null => {
    const port = wgServerPort.trim() ? Number(wgServerPort.trim()) : undefined
    if (port !== undefined && (Number.isNaN(port) || port < 1 || port > 65535)) {
      setError(t('Server port must be 1–65535', 'Порт сервера должен быть 1–65535'))
      return null
    }
    return {
      client_name: wgClientName.trim() || undefined,
      server_port: port,
      dns_1: wgDns1.trim() || undefined,
      dns_2: wgDns2.trim() || undefined,
      allowed_ips: wgAllowedIps.trim() || undefined,
      ssh_port: sshPort.trim() ? Number(sshPort.trim()) : undefined,
    }
  }

  /** Persist the deployment plan to the Server (server mode only). */
  const persistNaive = async (params: NaiveParams) => {
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
          template_id: params.template_id,
          install_php: params.install_php,
        },
      },
    })
  }

  const persistWg = async (params: WgParams) => {
    if (mode.kind !== 'server') return
    // ServerDeployment.config_json keeps server-level state; client_name
    // is per-deploy and intentionally NOT persisted here (each manual
    // re-run can pick a different first peer). Mirrors how DeployModal
    // builds the WG config — see frontend/src/types/index.ts.
    await upsertDeployment.mutateAsync({
      serverId: mode.server.id,
      protocol: 'wireguard',
      data: {
        protocol: 'wireguard',
        config: {
          server_port: params.server_port,
          dns_1: params.dns_1,
          dns_2: params.dns_2,
          allowed_ips: params.allowed_ips,
        } as import('@/types').WireGuardDeploymentConfig,
      },
    })
  }

  /** Trigger the .sh download via Blob, server-bound or manual. */
  const downloadNaive = async (params: NaiveParams) => {
    if (mode.kind === 'server') {
      await serversApi.downloadNaiveInstallScript(mode.server.id, params)
    } else {
      await scriptsApi.downloadNaiveInstall(params)
    }
  }

  const downloadWg = async (params: WgParams) => {
    if (mode.kind === 'server') {
      await serversApi.downloadWireguardInstallScript(mode.server.id, params)
    } else {
      await scriptsApi.downloadWireguard(params)
    }
  }

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    setDownloading(true)
    try {
      if (protocol === 'naive') {
        const p = buildNaiveParams(); if (!p) return
        await persistNaive(p)
      } else {
        const p = buildWgParams(); if (!p) return
        await persistWg(p)
      }
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setDownloading(false)
    }
  }

  const handleSaveAndDownload = async () => {
    setError('')
    setDownloading(true)
    try {
      if (protocol === 'naive') {
        const p = buildNaiveParams(); if (!p) return
        await persistNaive(p)
        await downloadNaive(p)
      } else {
        const p = buildWgParams(); if (!p) return
        await persistWg(p)
        await downloadWg(p)
      }
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Download failed')
    } finally {
      setDownloading(false)
    }
  }

  const handleDownloadOnly = async () => {
    setError('')
    setDownloading(true)
    try {
      if (protocol === 'naive') {
        const p = buildNaiveParams(); if (!p) return
        await downloadNaive(p)
      } else {
        const p = buildWgParams(); if (!p) return
        await downloadWg(p)
      }
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Download failed')
    } finally {
      setDownloading(false)
    }
  }

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

  const onFormSubmit = mode.kind === 'server' ? handleSave : handleDownloadOnly

  return (
    <ModalShell onClose={onClose} labelledBy="manual-script-title">
      <form
        onSubmit={onFormSubmit}
        className="w-full max-w-lg rounded-2xl bg-gray-950/95 border border-gray-800 p-6 m-4 max-h-[90vh] overflow-y-auto"
      >
        <h2 id="manual-script-title" className="text-lg font-semibold text-gray-100 mb-1">
          {protocol === 'naive'
            ? t('NaiveProxy install script', 'Скрипт установки NaiveProxy')
            : t('WireGuard install script', 'Скрипт установки WireGuard')}
        </h2>
        <p className="text-xs text-gray-500 mb-4">{subtitle}</p>

        {/* Protocol toggle — same affordance as the Deploy modal so the
            user's mental model stays consistent. */}
        <div className="mb-4 grid grid-cols-2 gap-2">
          <button
            type="button"
            onClick={() => setProtocol('naive')}
            className={
              'rounded-lg border px-3 py-2 text-left transition-colors text-sm ' +
              (protocol === 'naive'
                ? 'border-brand-500/60 bg-brand-50 dark:bg-brand-600/10 text-brand-700 dark:text-brand-200'
                : 'border-gray-800 bg-gray-900/40 text-gray-400 hover:border-gray-700 hover:text-gray-200')
            }
          >
            <div className="font-medium">NaiveProxy</div>
            <div className="text-[11px] text-gray-500 mt-0.5">
              {t('HTTPS over TLS, single tunnel', 'HTTPS поверх TLS, один туннель')}
            </div>
          </button>
          <button
            type="button"
            onClick={() => setProtocol('wireguard')}
            className={
              'rounded-lg border px-3 py-2 text-left transition-colors text-sm ' +
              (protocol === 'wireguard'
                ? 'border-brand-500/60 bg-brand-50 dark:bg-brand-600/10 text-brand-700 dark:text-brand-200'
                : 'border-gray-800 bg-gray-900/40 text-gray-400 hover:border-gray-700 hover:text-gray-200')
            }
          >
            <div className="font-medium">WireGuard</div>
            <div className="text-[11px] text-gray-500 mt-0.5">
              {t('UDP, multi-client', 'UDP, много клиентов')}
            </div>
          </button>
        </div>

        {error && (
          <div className="mb-3 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 px-3 py-2 text-sm text-red-700 dark:text-red-300">
            {error}
          </div>
        )}

        {protocol === 'naive' ? (
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
            <FieldL
              label={t('Decoy site (cover page)', 'Обложка-приманка')}
              hint={t(
                'what unauthenticated visitors see at the proxy domain',
                'что увидит случайный посетитель на домене прокси',
              )}
            >
              <TemplatePicker
                value={naiveTemplateId}
                onChange={(id, requiresPhp) => {
                  setNaiveTemplateId(id)
                  // Picking a php-needing template auto-enables the
                  // toggle so the user sees the consequence; the
                  // backend forces it on regardless, but reflecting
                  // that state in the UI keeps the form honest.
                  if (requiresPhp) setNaiveInstallPhp(true)
                }}
              />
            </FieldL>
            <label className="flex items-start gap-2 cursor-pointer rounded-lg border border-gray-800 bg-gray-900/40 px-3 py-2 hover:border-gray-700">
              <input
                type="checkbox"
                checked={naiveInstallPhp}
                onChange={(e) => setNaiveInstallPhp(e.target.checked)}
                className="mt-0.5 h-3.5 w-3.5 rounded-sm border-gray-600 bg-gray-800 text-brand-500 focus:ring-brand-500"
              />
              <div className="min-w-0 flex-1">
                <div className="text-xs font-medium text-gray-200">
                  {t('Install hardened PHP-FPM', 'Установить ужесточённый PHP-FPM')}
                </div>
                <div className="text-[11px] text-gray-500 mt-0.5 leading-snug">
                  {t(
                    'Required for dynamic decoys (e.g. fake-2FA) that need real server-side roundtrips. Jail blocks exec/network/FS escapes. Skip if your decoy is pure HTML.',
                    'Нужно для динамических обложек (напр. фейковая 2FA), которым требуется реальный серверный обработчик. Jail блокирует exec/сеть/FS. Пропустите, если обложка — обычный HTML.',
                  )}
                </div>
              </div>
            </label>
          </div>
        ) : (
          <div className="space-y-3">
            <FieldL
              label={t('First client name', 'Имя первого клиента')}
              hint={t(
                'leave blank → "client1"; alphanumeric + _-',
                'оставьте пустым → "client1"; буквы/цифры/_/-',
              )}
            >
              <input
                type="text"
                value={wgClientName}
                onChange={(e) => setWgClientName(e.target.value)}
                placeholder="phone-1"
                className={inputCls}
                autoFocus
              />
            </FieldL>
            <FieldL
              label={t('Server port (UDP)', 'Порт сервера (UDP)')}
              hint={t('default 51820', 'по умолчанию 51820')}
            >
              <input
                type="number"
                min={1}
                max={65535}
                value={wgServerPort}
                onChange={(e) => setWgServerPort(e.target.value)}
                className={inputCls}
              />
            </FieldL>
            <div className="grid grid-cols-2 gap-3">
              <FieldL label={t('DNS 1', 'DNS 1')}>
                <input
                  type="text"
                  value={wgDns1}
                  onChange={(e) => setWgDns1(e.target.value)}
                  placeholder="1.1.1.1"
                  className={inputCls}
                />
              </FieldL>
              <FieldL label={t('DNS 2', 'DNS 2')}>
                <input
                  type="text"
                  value={wgDns2}
                  onChange={(e) => setWgDns2(e.target.value)}
                  placeholder="1.0.0.1"
                  className={inputCls}
                />
              </FieldL>
            </div>
            <FieldL
              label={t('AllowedIPs', 'AllowedIPs')}
              hint={t(
                'what client routes through tunnel — full tunnel by default',
                'что клиент маршрутизирует в туннель — по умолчанию весь трафик',
              )}
            >
              <input
                type="text"
                value={wgAllowedIps}
                onChange={(e) => setWgAllowedIps(e.target.value)}
                placeholder="0.0.0.0/0,::/0"
                className={inputCls}
              />
            </FieldL>
          </div>
        )}

        {/* SSH port move — common to both protocols, one sshd per VPS. */}
        <div className="mt-3">
          <FieldL
            label={t('SSH port (optional)', 'SSH-порт (опционально)')}
            hint={t(
              'leave blank to keep current — applied at install, PiTun remembers it',
              'оставьте пустым — текущий не трогаем; PiTun запомнит после установки',
            )}
          >
            <SshPortField
              value={sshPort}
              onChange={setSshPort}
              serverPort={mode.kind === 'server' ? mode.server.port : undefined}
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
              className="flex-1 rounded-lg border border-brand-400 bg-brand-50 dark:bg-brand-600/15 hover:bg-brand-600/25 text-brand-300 px-4 py-2 text-sm font-medium disabled:opacity-50 transition-colors"
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
  'w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden'

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
