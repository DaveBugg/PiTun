import { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, Loader2, Terminal, CheckCircle2, Ban, ExternalLink, Trash2,
} from 'lucide-react'
import { Link } from 'react-router-dom'

import { http } from '@/api/client'
import { ModalShell } from '@/components/ModalShell'
import { useT } from '@/hooks/useT'
import { effectiveJobStatus } from '@/lib/jobStatus'
import {
  useCancelServerTask,
  useServerTask,
  useServerTaskStream,
} from '@/hooks/useServerTasks'
import type { DeployJobAccepted, DeployJobResult, Server, ServerDeploymentProtocol } from '@/types'

/**
 * Uninstall modal (since v1.3.0-beta.6).
 *
 * Mirrors DeployModal but without a form — uninstall is a one-click
 * destructive action gated by a confirm step. Two phases:
 *   1. **Confirm** — red banner explaining what gets wiped + a
 *      single "Wipe" button. Cancel just closes.
 *   2. **Streaming** — same `useServerTaskStream` infrastructure
 *      as deploy, renders live stdout/stderr in a terminal panel.
 *      On success, surfaces a "Re-deploy" affordance for the typical
 *      "wipe → reinstall fresh" testing flow.
 *
 * The remote script (`uninstall-<protocol>-server.sh`) is idempotent —
 * re-running on an already-clean system is a no-op. So if the user
 * cancels mid-stream, retrying later still works.
 */
export function UninstallModal({
  server,
  protocol,
  direct = false,
  onClose,
  onRedeploy,
}: {
  server: Server
  protocol: ServerDeploymentProtocol
  /** Route the SSH uninstall directly (SO_MARK bypass) instead of
   * through the active node. Inherited from the Servers page toggle. */
  direct?: boolean
  onClose: () => void
  /** Optional: called when the user clicks "Re-deploy now" after a
   * successful uninstall. Parent typically opens the DeployModal. */
  onRedeploy?: () => void
}) {
  const t = useT()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)

  const onStart = async () => {
    setError('')
    setSubmitting(true)
    try {
      const accepted = await http
        .post<DeployJobAccepted>(`/servers/${server.id}/uninstall/${protocol}`, null, {
          params: { direct },
        })
        .then((r) => r.data)
      setJobId(accepted.job_id)
    } catch (err: unknown) {
      setError(extractAxiosError(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ModalShell onClose={onClose} labelledBy="uninstall-modal-title">
      <div className="w-full max-w-3xl rounded-2xl bg-gray-950/95 border border-gray-800 p-6 m-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-start gap-3 mb-4">
          <div className="rounded-lg bg-red-600/15 p-2 text-red-600 dark:text-red-400">
            <Trash2 className="h-5 w-5" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 id="uninstall-modal-title" className="text-lg font-semibold text-gray-100">
              {t('Uninstall', 'Удалить')}{' '}
              <span className="text-red-700 dark:text-red-300">{
                protocol === 'naive' ? 'NaiveProxy'
                  : protocol === 'wireguard' ? 'WireGuard'
                  : 'x-ui'
              }</span>{' '}
              {t('from', 'с')}{' '}
              <span className="text-brand-400">{server.name}</span>
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {t(
                `Runs the uninstaller over SSH on ${server.user}@${server.host}:${server.port}, streams output live.`,
                `Запустит деинсталлятор по SSH на ${server.user}@${server.host}:${server.port}, покажет вывод в реальном времени.`,
              )}
            </p>
          </div>
        </div>

        {!jobId && (
          <ConfirmView
            protocol={protocol}
            error={error}
            submitting={submitting}
            onSubmit={onStart}
            onCancel={onClose}
          />
        )}

        {jobId && (
          <UninstallRunning
            jobId={jobId}
            server={server}
            protocol={protocol}
            onClose={onClose}
            onRedeploy={onRedeploy}
          />
        )}
      </div>
    </ModalShell>
  )
}


function ConfirmView(props: {
  protocol: ServerDeploymentProtocol
  error: string
  submitting: boolean
  onSubmit: () => void
  onCancel: () => void
}) {
  const t = useT()
  // Per-protocol "what gets wiped" rundown. Kept short — the
  // uninstall scripts' own banner prints the long form when it runs.
  const wipeList = props.protocol === 'naive'
    ? [
        t('Caddy package + /etc/caddy/ + /var/log/caddy/', 'Пакет Caddy + /etc/caddy/ + /var/log/caddy/'),
        t('Decoy site under /var/www/html/', 'Сайт-приманка в /var/www/html/'),
        t('PiTun-side ServerDeployment row (config metadata)', 'Запись ServerDeployment в PiTun (метаданные конфига)'),
      ]
    : props.protocol === 'xui'
    ? [
        t('x-ui systemd unit + binary + /etc/x-ui/', 'systemd-юнит x-ui + бинарь + /etc/x-ui/'),
        t('nginx (purged) + /var/www/html/ (xui-pro mode only)', 'nginx (полное удаление) + /var/www/html/ (только в xui-pro режиме)'),
        t('certbot + tor + warp-plus (if installed by xui-pro)', 'certbot + tor + warp-plus (если ставились x-ui-pro)'),
        t('PiTun-side XuiServer + XuiClient rows (cascade-delete)', 'Записи XuiServer + XuiClient в PiTun (каскадное удаление)'),
        t('Linked Node(s) kept; chain inbounds removed via panel API', 'Связанные Node-ы остаются; chain-инбаунды удаляются через API'),
      ]
    : [
        t('wg-quick@wg0 (stop + disable) + iptables NAT rules', 'wg-quick@wg0 (стоп + disable) + iptables NAT'),
        t('/etc/wireguard/ (server keys + peer configs)', '/etc/wireguard/ (ключи сервера + конфиги пиров)'),
        t('PiTun-side DeploymentClient rows (cascade-delete)', 'Записи DeploymentClient в PiTun (каскадное удаление)'),
        t('Linked Node(s) flagged as `orphan` (kept)', 'Связанные Node-ы помечаются как `orphan` (не удаляются)'),
      ]
  return (
    <div>
      {props.error && (
        <div className="mb-3 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 px-3 py-2 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{props.error}</span>
        </div>
      )}

      <div className="rounded-xl border border-red-200 dark:border-red-700/50 bg-red-50 dark:bg-red-900/15 px-4 py-3 mb-4">
        <div className="flex items-start gap-2 text-sm text-red-800 dark:text-red-200 mb-2">
          <AlertTriangle className="h-4 w-4 mt-0.5 shrink-0 text-red-600 dark:text-red-400" />
          <strong>{t('This will wipe:', 'Будет удалено:')}</strong>
        </div>
        <ul className="text-xs text-red-800 dark:text-red-200/90 space-y-1 ml-6 list-disc list-outside">
          {wipeList.map((line) => <li key={line}>{line}</li>)}
        </ul>
        <p className="mt-3 text-[11px] text-red-700 dark:text-red-300/70 ml-6">
          {t(
            'SSH hardening / firewall rules from earlier installs are NOT touched. Idempotent — safe to re-run on partially-cleaned systems.',
            'Изменения SSH-hardening и firewall, сделанные ранее, не затрагиваются. Идемпотентно — безопасно перезапускать на частично-очищенной системе.',
          )}
        </p>
      </div>

      <div className="flex gap-2 pt-2">
        <button
          type="button"
          onClick={props.onCancel}
          className="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-400 hover:bg-gray-800 transition-colors"
        >
          {t('Cancel', 'Отмена')}
        </button>
        <button
          type="button"
          onClick={props.onSubmit}
          disabled={props.submitting}
          className="flex-1 rounded-lg bg-red-600 px-4 py-2 text-sm font-medium text-white hover:bg-red-500 disabled:opacity-50 transition-colors flex items-center justify-center gap-1.5"
        >
          {props.submitting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              {t('Starting…', 'Запуск…')}
            </>
          ) : (
            <>
              <Trash2 className="h-4 w-4" />
              {t('Wipe', 'Удалить')}
            </>
          )}
        </button>
      </div>
    </div>
  )
}


function UninstallRunning({
  jobId, server, protocol, onClose, onRedeploy,
}: {
  jobId: string
  server: Server
  protocol: ServerDeploymentProtocol
  onClose: () => void
  onRedeploy?: () => void
}) {
  const t = useT()
  const qc = useQueryClient()
  const { frames, done, error: wsError } = useServerTaskStream(jobId)
  // Poll until the persisted row is terminal — a dropped socket
  // (`done === 'unknown'`) must not switch off the fallback channel,
  // and the row is committed microseconds before the `done` frame.
  // See DeployModal for the full rationale.
  const { data: jobRow } = useServerTask(jobId, { polling: true })
  const rowTerminal = jobRow != null && jobRow.status !== 'running'
  const isRunning = !rowTerminal && (done === null || done === 'unknown')

  const cancel = useCancelServerTask()
  const onCancel = () => cancel.mutate(jobId)

  // Refresh side-effects on completion: server deployments list,
  // nodes (orphan badges may have flipped for WG), wg-clients
  // cache (was wiped server-side).
  useEffect(() => {
    if (!rowTerminal) return
    qc.invalidateQueries({ queryKey: ['nodes'] })
    qc.invalidateQueries({ queryKey: ['servers'] })
    qc.invalidateQueries({ queryKey: ['servers', server.id, 'deployments'] })
    qc.invalidateQueries({ queryKey: ['servers', server.id, 'wg-clients'] })
  }, [rowTerminal, qc, server.id])

  const result = useMemo<DeployJobResult | null>(() => {
    const r = jobRow?.result
    return r ? (r as DeployJobResult) : null
  }, [jobRow])

  // Persisted row wins once terminal, read through the shared
  // projection (a non-zero exit finalizes the job as `succeeded`).
  const finalStatus = rowTerminal && jobRow
    ? effectiveJobStatus(jobRow)
    : (done && done !== 'unknown' ? done : 'running')

  return (
    <div>
      <StatusBanner status={finalStatus} result={result} error={jobRow?.error || wsError} protocol={protocol} />

      <div className="mt-3 rounded-xl border border-gray-800 bg-black/60 overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-gray-800/60 bg-gray-900/50 text-xs text-gray-500">
          <Terminal className="h-3.5 w-3.5" />
          <span className="font-mono">job {jobId.slice(0, 12)}…</span>
          {isRunning && <Loader2 className="h-3 w-3 animate-spin text-red-600 dark:text-red-400 ml-1" />}
          <Link
            to={`/server-tasks?server_id=${server.id}`}
            className="ml-auto text-[11px] text-gray-500 hover:text-brand-400 inline-flex items-center gap-1"
          >
            {t('All tasks', 'Все задачи')} <ExternalLink className="h-3 w-3" />
          </Link>
        </div>
        <LogPanel frames={frames} fallbackTail={jobRow?.log_tail ?? null} />
      </div>

      <div className="flex gap-2 pt-4">
        {isRunning ? (
          <button
            type="button"
            onClick={onCancel}
            disabled={cancel.isPending}
            className="rounded-lg border border-red-200 dark:border-red-800/60 bg-red-50 dark:bg-red-900/20 hover:bg-red-50 dark:hover:bg-red-900/30 text-red-700 dark:text-red-300 px-3 py-1.5 text-sm flex items-center gap-1.5 disabled:opacity-50 transition-colors"
            title={t(
              'Cancel local stream — remote script keeps running on the VPS',
              'Отменить локальный поток — скрипт продолжит работу на VPS',
            )}
          >
            <Ban className="h-4 w-4" />
            {cancel.isPending ? t('Cancelling…', 'Отмена…') : t('Cancel', 'Отменить')}
          </button>
        ) : finalStatus === 'succeeded' && onRedeploy ? (
          <button
            type="button"
            onClick={() => { onClose(); onRedeploy() }}
            className="rounded-lg bg-brand-600 hover:bg-brand-500 text-white px-3 py-1.5 text-sm font-medium flex items-center gap-1.5 transition-colors"
          >
            {t('Re-deploy', 'Установить заново')}
          </button>
        ) : null}
        <div className="flex-1" />
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800 transition-colors"
        >
          {isRunning ? t('Hide (keeps running)', 'Скрыть (продолжит работу)') : t('Close', 'Закрыть')}
        </button>
      </div>
    </div>
  )
}


function StatusBanner({
  status, result, error, protocol,
}: {
  status: string
  result: DeployJobResult | null
  error: string | null | undefined
  protocol: ServerDeploymentProtocol
}) {
  const t = useT()
  if (status === 'running') {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-700/40 bg-red-50 dark:bg-red-900/10 px-3 py-2 text-sm text-red-700 dark:text-red-300 flex items-center gap-2">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>{t('Running uninstaller…', 'Идёт удаление…')}</span>
      </div>
    )
  }
  if (status === 'cancelled') {
    return (
      <div className="rounded-lg border border-yellow-200 dark:border-yellow-700/40 bg-yellow-50 dark:bg-yellow-900/10 px-3 py-2 text-sm text-yellow-700 dark:text-yellow-300 flex items-start gap-2">
        <Ban className="h-4 w-4 mt-0.5" />
        <div>
          <div className="font-medium">{t('Cancelled', 'Отменено')}</div>
          <div className="text-xs text-yellow-700 dark:text-yellow-300/80 mt-0.5">
            {t(
              'The uninstall script may still be running on the VPS. Re-run when ready, or check the server manually.',
              'Скрипт удаления может всё ещё работать на VPS. Повторите запуск или проверьте сервер вручную.',
            )}
          </div>
        </div>
      </div>
    )
  }
  if (status === 'failed') {
    return (
      <div className="rounded-lg border border-red-200 dark:border-red-700/40 bg-red-50 dark:bg-red-900/10 px-3 py-2 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
        <AlertTriangle className="h-4 w-4 mt-0.5" />
        <div className="min-w-0">
          <div className="font-medium">{t('Uninstall failed', 'Удаление не удалось')}</div>
          {error && <div className="text-xs text-red-700 dark:text-red-300/80 mt-0.5 wrap-break-word font-mono">{error}</div>}
        </div>
      </div>
    )
  }
  return (
    <div className="rounded-lg border border-emerald-200 dark:border-emerald-700/40 bg-emerald-50 dark:bg-emerald-900/10 px-3 py-2 text-sm text-emerald-700 dark:text-emerald-300 flex items-start gap-2">
      <CheckCircle2 className="h-4 w-4 mt-0.5" />
      <div className="min-w-0">
        <div className="font-medium">
          {protocol === 'naive' ? t('NaiveProxy uninstalled', 'NaiveProxy удалён')
            : protocol === 'xui' ? t('x-ui uninstalled', 'x-ui удалён')
            : t('WireGuard uninstalled', 'WireGuard удалён')}
        </div>
        {result?.duration_sec ? (
          <div className="text-xs text-emerald-700 dark:text-emerald-300/80 mt-0.5">
            {t('Took ', 'За ')}
            <span className="font-mono">{Math.round(result.duration_sec)}s</span>
            {t('. The server is ready for a fresh install.', '. Сервер готов к свежей установке.')}
          </div>
        ) : null}
      </div>
    </div>
  )
}


function LogPanel({
  frames, fallbackTail,
}: {
  frames: ReturnType<typeof useServerTaskStream>['frames']
  fallbackTail: string | null
}) {
  const ref = useRef<HTMLDivElement | null>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [frames.length])

  const usingFallback = frames.length === 0 && fallbackTail
  const lines = usingFallback
    ? fallbackTail.split('\n').map((line, i) => ({ kind: 'stdout' as const, line, idx: i }))
    : frames.map((f, i) => ({
        kind: f.event === 'log' ? f.kind : 'stdout',
        line: f.event === 'log' ? f.line : '',
        idx: i,
      }))

  return (
    <div
      ref={ref}
      className="font-mono text-[11px] leading-snug max-h-72 overflow-y-auto p-3"
      style={{ scrollbarWidth: 'thin' }}
    >
      {lines.length === 0 ? (
        <div className="text-gray-600">…</div>
      ) : (
        lines.map((l) => (
          <div key={l.idx} className={l.kind === 'stderr' ? 'text-red-600 dark:text-red-400' : 'text-gray-300'}>
            {l.line || ' '}
          </div>
        ))
      )}
    </div>
  )
}


function extractAxiosError(err: unknown): string {
  if (typeof err === 'object' && err !== null) {
    const e = err as { response?: { data?: { detail?: unknown } }, message?: string }
    const detail = e.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (e.message) return e.message
  }
  return 'Failed to start uninstall'
}
