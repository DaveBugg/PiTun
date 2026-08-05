import { useState } from 'react'
import * as React from 'react'
import {
  Users, Plus, RefreshCw, Trash2, Download, Network, Loader2,
  AlertTriangle, AlertCircle, CheckCircle2, ExternalLink,
} from 'lucide-react'

import { serversApi } from '@/api/client'
import { ModalShell } from '@/components/ModalShell'
import { useT } from '@/hooks/useT'
import { useConfirm } from '@/components/ConfirmModal'
import {
  useDeploymentClients,
  useAddClient,
  useRemoveClient,
  useSyncClients,
  useExportClientToNode,
} from '@/hooks/useServers'
import type {
  DeploymentClient,
  DeploymentClientStatus,
  Server,
} from '@/types'

/**
 * Manage WireGuard clients on one server. Sits on top of the
 * DeploymentClient layer (since v1.3.0-beta.4): one row per peer in
 * `wg0.conf`, with status `available` | `exported` | `orphan`.
 *
 *  - **Sync** re-lists peers from the VPS and reconciles. Existing-by-name
 *    rows are kept; new peers appear; rows whose name no longer exists
 *    server-side are flagged `orphan` (we DO NOT auto-delete because
 *    they may be linked to Nodes — see Nodes page for the orphan badge).
 *  - **Add** runs `setup-wireguard-server.sh add-client <name>` over SSH;
 *    backend persists the new keypair + INI conf into a DeploymentClient.
 *  - **Remove** wipes the peer server-side AND the DeploymentClient row;
 *    any Nodes that were exported from this client get `client_orphan=1`.
 *  - **Export to Node** creates a Node from this client's saved conf
 *    (only works while we still have the priv key, i.e. for clients we
 *    added through PiTun — synced-from-server clients lack the priv key
 *    and the export endpoint will refuse them with a clear 400).
 *  - **Conf** downloads the full INI for this peer (incl. priv key).
 *
 * Companion to DeployModal (which only adds the FIRST client during
 * install). All routes live under
 * `/api/servers/{id}/deployments/wireguard/clients/*`.
 */
export function ManageClientsModal({
  server,
  direct = false,
  onClose,
}: {
  server: Server
  /** Route the WG-client SSH ops directly (SO_MARK bypass) instead of
   * through the active node. Inherited from the Servers page toggle. */
  direct?: boolean
  onClose: () => void
}) {
  const t = useT()
  const confirm = useConfirm()

  const { data, isLoading, error, refetch, isRefetching } = useDeploymentClients(server.id)
  const clients = data?.clients ?? []

  const addClient = useAddClient()
  const removeClient = useRemoveClient()
  const syncClients = useSyncClients()
  const exportToNode = useExportClientToNode()

  const [newName, setNewName] = useState('')
  const [actionError, setActionError] = useState<string>('')
  const [lastSync, setLastSync] = useState<{
    added: string[]; unchanged: string[]; orphaned: string[]
  } | null>(null)

  const onAdd = async (e: React.FormEvent) => {
    e.preventDefault()
    setActionError('')
    if (!newName.trim()) {
      setActionError(t('Name is required', 'Имя обязательно'))
      return
    }
    try {
      await addClient.mutateAsync({ serverId: server.id, body: { name: newName.trim() }, direct })
      setNewName('')
    } catch (err: unknown) {
      setActionError(extractAxiosError(err))
    }
  }

  const onSync = async () => {
    setActionError('')
    setLastSync(null)
    try {
      const r = await syncClients.mutateAsync({ serverId: server.id, direct })
      setLastSync(r)
    } catch (err: unknown) {
      setActionError(extractAxiosError(err))
    }
  }

  const onRemove = async (c: DeploymentClient) => {
    const exportedCount = c.exported_node_ids.length
    const ok = await confirm({
      title: t('Remove client?', 'Удалить клиента?'),
      body:
        t(
          `"${c.name}" will be removed from wg0.conf on the server and from PiTun.`,
          `"${c.name}" будет удалён из wg0.conf на сервере и из PiTun.`,
        ) +
        (exportedCount > 0
          ? ' ' +
            t(
              `${exportedCount} linked node(s) will be flagged as orphan (not deleted).`,
              `${exportedCount} связанных нод(ы) будут помечены как orphan (не удалены).`,
            )
          : ''),
      confirmLabel: t('Remove', 'Удалить'),
      danger: true,
    })
    if (!ok) return
    setActionError('')
    try {
      await removeClient.mutateAsync({ serverId: server.id, name: c.name, direct })
    } catch (err: unknown) {
      setActionError(extractAxiosError(err))
    }
  }

  const onExport = async (c: DeploymentClient) => {
    setActionError('')
    try {
      // Pass an empty body — backend defaults node_name=client_name and
      // enabled=true. Future iteration can offer a small picker form.
      await exportToNode.mutateAsync({ serverId: server.id, name: c.name, body: {}, direct })
    } catch (err: unknown) {
      setActionError(extractAxiosError(err))
    }
  }

  const onDownloadConf = async (c: DeploymentClient) => {
    setActionError('')
    try {
      const conf = await serversApi.getClientConf(server.id, c.name, direct)
      // Trigger a browser download — `<server>-<client>.conf` is the
      // typical naming used by wg-quick / mobile apps.
      const blob = new Blob([conf.wg_conf], { type: 'text/plain;charset=utf-8' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${server.name}-${c.name}.conf`
      a.click()
      URL.revokeObjectURL(url)
    } catch (err: unknown) {
      setActionError(extractAxiosError(err))
    }
  }

  return (
    <ModalShell onClose={onClose} labelledBy="manage-clients-title">
      <div className="w-full max-w-3xl rounded-2xl bg-gray-950/95 border border-gray-800 p-6 m-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-start gap-3 mb-4">
          <div className="rounded-lg bg-brand-50 dark:bg-brand-600/15 p-2 text-brand-400">
            <Users className="h-5 w-5" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 id="manage-clients-title" className="text-lg font-semibold text-gray-100">
              {t('WireGuard clients on', 'WireGuard клиенты на')}{' '}
              <span className="text-brand-400">{server.name}</span>
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {t(
                'One row per peer in wg0.conf. Use Sync to re-list from the VPS.',
                'Одна строка на peer в wg0.conf. Используйте Sync для обновления с VPS.',
              )}
            </p>
          </div>
          <button
            type="button"
            onClick={onSync}
            disabled={syncClients.isPending}
            className="rounded-lg border border-gray-700 hover:bg-gray-800 px-3 py-1.5 text-xs text-gray-300 inline-flex items-center gap-1.5 disabled:opacity-50 transition-colors"
            title={t('Reconcile with the server', 'Синхронизировать с сервером')}
          >
            {syncClients.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            {t('Sync', 'Синхронизировать')}
          </button>
        </div>

        {actionError && (
          <div className="mb-3 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 px-3 py-2 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
            <span>{actionError}</span>
          </div>
        )}

        {lastSync && (
          <div className="mb-3 rounded-lg border border-emerald-200 dark:border-emerald-700/40 bg-emerald-50 dark:bg-emerald-900/10 px-3 py-2 text-xs text-emerald-700 dark:text-emerald-300 flex items-start gap-2">
            <CheckCircle2 className="h-4 w-4 mt-0.5" />
            <div>
              {t('Sync result: ', 'Результат синхронизации: ')}
              <span className="font-mono">+{lastSync.added.length}</span>
              {' '}{t('added', 'добавлено')}
              {' · '}
              <span className="font-mono">{lastSync.unchanged.length}</span>
              {' '}{t('unchanged', 'без изменений')}
              {' · '}
              <span className="font-mono text-yellow-700 dark:text-yellow-300">{lastSync.orphaned.length}</span>
              {' '}{t('orphaned', 'осиротевших')}
            </div>
          </div>
        )}

        {/* Add new client */}
        <form onSubmit={onAdd} className="mb-4 flex items-end gap-2">
          <div className="flex-1">
            <label className="block">
              <div className="mb-1.5 text-xs text-gray-500">
                {t('New client name', 'Имя нового клиента')}
                <span className="text-gray-600">
                  {' '}— {t('alphanumeric + _- · added on the server immediately', 'буквы/цифры/_/- · сразу добавляется на сервер')}
                </span>
              </div>
              <input
                type="text"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="phone-2"
                className="w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
              />
            </label>
          </div>
          <button
            type="submit"
            disabled={addClient.isPending}
            className="rounded-lg bg-brand-600 hover:bg-brand-500 text-white px-3 py-2 text-sm font-medium flex items-center gap-1.5 disabled:opacity-50 transition-colors"
          >
            {addClient.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                {t('Adding…', 'Добавление…')}
              </>
            ) : (
              <>
                <Plus className="h-4 w-4" />
                {t('Add', 'Добавить')}
              </>
            )}
          </button>
        </form>

        {/* Client list */}
        {isLoading ? (
          <div className="rounded-lg border border-gray-800 bg-gray-900/30 px-3 py-8 text-center text-sm text-gray-500 flex items-center justify-center gap-2">
            <Loader2 className="h-4 w-4 animate-spin" />
            {t('Loading clients…', 'Загрузка клиентов…')}
          </div>
        ) : error ? (
          <div className="rounded-lg border border-red-200 dark:border-red-700/40 bg-red-50 dark:bg-red-900/10 px-3 py-3 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 mt-0.5" />
            <div>
              <div className="font-medium">{t('Failed to load clients', 'Не удалось загрузить клиентов')}</div>
              <div className="text-xs text-red-700 dark:text-red-300/80 mt-0.5 wrap-break-word font-mono">
                {(error as Error).message}
              </div>
              <button
                type="button"
                onClick={() => refetch()}
                disabled={isRefetching}
                className="mt-2 rounded-sm border border-red-200 dark:border-red-700/50 px-2 py-0.5 text-xs hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-50"
              >
                {t('Retry', 'Повторить')}
              </button>
            </div>
          </div>
        ) : clients.length === 0 ? (
          <div className="rounded-lg border border-gray-800 bg-gray-900/30 px-3 py-8 text-center text-sm text-gray-500">
            <Network className="h-6 w-6 mx-auto mb-2 text-gray-600" />
            <div>{t('No clients yet', 'Клиентов пока нет')}</div>
            <div className="text-xs text-gray-600 mt-1">
              {t(
                'Add one above, or run Sync if you added peers manually on the VPS.',
                'Добавьте выше или нажмите Sync, если добавляли peer-ов вручную на VPS.',
              )}
            </div>
          </div>
        ) : (
          <div className="rounded-lg border border-gray-800 bg-gray-900/30 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wider text-gray-500 bg-gray-900/60">
                <tr>
                  <th className="px-3 py-2 text-left">{t('Name', 'Имя')}</th>
                  <th className="px-3 py-2 text-left">{t('Status', 'Статус')}</th>
                  <th className="px-3 py-2 text-left">{t('Address', 'Адрес')}</th>
                  <th className="px-3 py-2"></th>
                </tr>
              </thead>
              <tbody>
                {clients.map((c) => (
                  <ClientRow
                    key={c.id}
                    client={c}
                    busy={
                      removeClient.isPending && removeClient.variables?.name === c.name
                    }
                    exporting={
                      exportToNode.isPending && exportToNode.variables?.name === c.name
                    }
                    onRemove={() => onRemove(c)}
                    onExport={() => onExport(c)}
                    onDownload={() => onDownloadConf(c)}
                  />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Footer */}
        <div className="flex justify-end pt-4">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-gray-700 hover:bg-gray-800 px-3 py-1.5 text-sm text-gray-300 transition-colors"
          >
            {t('Close', 'Закрыть')}
          </button>
        </div>
      </div>
    </ModalShell>
  )
}


// ── Row ─────────────────────────────────────────────────────────────────────

function ClientRow({
  client,
  busy,
  exporting,
  onRemove,
  onExport,
  onDownload,
}: {
  client: DeploymentClient
  busy: boolean
  exporting: boolean
  onRemove: () => void
  onExport: () => void
  onDownload: () => void
}) {
  const t = useT()
  const exportedCount = client.exported_node_ids.length
  const isOrphan = client.status === 'orphan'

  return (
    <tr className="border-t border-gray-800/60 hover:bg-gray-900/40">
      <td className="px-3 py-2">
        <div className="font-medium text-gray-100 font-mono">{client.name}</div>
        {exportedCount > 0 && (
          <div className="text-[11px] text-gray-500 mt-0.5">
            {t(`Linked to ${exportedCount} node(s)`, `Связан с ${exportedCount} нод(ами)`)}
          </div>
        )}
      </td>
      <td className="px-3 py-2">
        <ClientStatusBadge status={client.status} />
      </td>
      <td className="px-3 py-2 text-xs text-gray-400 font-mono">
        {client.wg_local_address || <span className="text-gray-600">—</span>}
      </td>
      <td className="px-3 py-2">
        <div className="flex justify-end gap-1">
          <IconBtn
            onClick={onDownload}
            title={t('Download .conf', 'Скачать .conf')}
            icon={Download}
            disabled={isOrphan}
          />
          <IconBtn
            onClick={onExport}
            title={
              isOrphan
                ? t('Cannot export — server-side client is gone', 'Нельзя экспортировать — клиент удалён на сервере')
                : t('Export to Node', 'Экспортировать как Node')
            }
            icon={ExternalLink}
            disabled={isOrphan || exporting}
            spinning={exporting}
          />
          <IconBtn
            onClick={onRemove}
            title={t('Remove client', 'Удалить клиента')}
            icon={Trash2}
            danger
            disabled={busy}
            spinning={busy}
          />
        </div>
      </td>
    </tr>
  )
}


function ClientStatusBadge({ status }: { status: DeploymentClientStatus }) {
  const t = useT()
  if (status === 'orphan') {
    return (
      <span className="inline-flex items-center gap-1 rounded-sm bg-yellow-50 dark:bg-yellow-900/30 border border-yellow-200 dark:border-yellow-700/40 px-2 py-0.5 text-xs text-yellow-700 dark:text-yellow-300">
        <AlertCircle className="h-3 w-3" />
        {t('orphan', 'осиротел')}
      </span>
    )
  }
  if (status === 'exported') {
    return (
      <span className="inline-flex items-center gap-1 rounded-sm bg-emerald-50 dark:bg-emerald-900/30 border border-emerald-200 dark:border-emerald-700/40 px-2 py-0.5 text-xs text-emerald-700 dark:text-emerald-300">
        <CheckCircle2 className="h-3 w-3" />
        {t('exported', 'экспортирован')}
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-sm bg-gray-800 px-2 py-0.5 text-xs text-gray-400">
      {t('available', 'доступен')}
    </span>
  )
}


function IconBtn({
  onClick, icon: Icon, title, disabled, danger, spinning,
}: {
  onClick: () => void
  icon: React.ComponentType<{ className?: string }>
  title: string
  disabled?: boolean
  danger?: boolean
  spinning?: boolean
}) {
  const cls = danger
    ? 'p-1.5 rounded-sm text-gray-500 hover:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 disabled:opacity-30 disabled:hover:bg-transparent transition-colors'
    : 'p-1.5 rounded-sm text-gray-500 hover:text-brand-400 hover:bg-gray-800 disabled:opacity-30 disabled:hover:bg-transparent transition-colors'
  return (
    <button type="button" onClick={onClick} title={title} disabled={disabled} className={cls}>
      {spinning ? (
        <Loader2 className="h-4 w-4 animate-spin" />
      ) : (
        <Icon className="h-4 w-4" />
      )}
    </button>
  )
}


function extractAxiosError(err: unknown): string {
  if (typeof err === 'object' && err !== null) {
    const e = err as { response?: { data?: { detail?: unknown } }, message?: string }
    const detail = e.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      return detail
        .map((d: { msg?: string; loc?: unknown }) => (d?.msg ?? '') + (d?.loc ? ' (' + JSON.stringify(d.loc) + ')' : ''))
        .filter(Boolean)
        .join('; ')
    }
    if (e.message) return e.message
  }
  return 'Request failed'
}
