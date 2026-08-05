/**
 * Update panel — check GitHub, then hand the apply to the host agent.
 *
 * Two things shape this UI and are worth stating, because they look like
 * bugs otherwise:
 *
 * 1. **The backend cannot report its own update.** Applying one restarts
 *    this very container, so progress is polled from a file the host
 *    agent writes. Expect the poll to fail for ~30-60s mid-update; that
 *    is the update working, not a crash, so a dropped request while
 *    `running` is rendered as "restarting", never as an error.
 * 2. **"Could not check" ≠ "up to date".** The box TPROXYs its own
 *    traffic, so a dead tunnel takes GitHub with it. `network_path`
 *    tells the operator which route answered, and an unreachable check
 *    says so instead of quietly claiming the latest version.
 */
import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowUpCircle, CheckCircle2, Loader2, RefreshCw, AlertTriangle, Info,
} from 'lucide-react'
import { clsx } from 'clsx'

import { updateApi } from '@/api/client'
import { apiErrorText } from '@/lib/apiError'
import { useT } from '@/hooks/useT'
import { useConfirm } from '@/components/ConfirmModal'

const ACTIVE_STATES = ['queued', 'running']

export function UpdateSection() {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [checkError, setCheckError] = useState<string | null>(null)

  const status = useQuery({
    queryKey: ['system', 'update', 'status'],
    queryFn: () => updateApi.status(),
    // Poll only while something is in flight. A finished update leaves a
    // terminal state on disk, so there is nothing to watch afterwards.
    refetchInterval: (q) =>
      ACTIVE_STATES.includes(q.state.data?.state ?? '') ? 2000 : false,
    // Mid-update the backend is down; keep the last known state rather
    // than blanking the panel, and don't count that as an error.
    retry: false,
  })

  const busy = ACTIVE_STATES.includes(status.data?.state ?? '')
  // The backend goes away during its own restart. While busy, a failed
  // poll means "restarting", not "broken".
  const restarting = busy && status.isError

  const check = useMutation({
    mutationFn: () => updateApi.check(),
    onSuccess: () => setCheckError(null),
    onError: (err) => setCheckError(apiErrorText(err, t('Check failed', 'Проверка не удалась'))),
  })

  const start = useMutation({
    mutationFn: (force: boolean) => updateApi.start({ force }),
    onSuccess: (data) => {
      qc.setQueryData(['system', 'update', 'status'], data)
    },
  })

  // Once an update finishes, the reported version changed — drop the
  // cached version data so the sidebar and About stop showing the old one.
  useEffect(() => {
    if (status.data?.state === 'done') {
      qc.invalidateQueries({ queryKey: ['system'] })
    }
  }, [status.data?.state, qc])

  const result = check.data
  const available = result?.update_available === true
  const unreachable = result != null && result.latest == null

  // Installing a build older than `update_ui_since` takes this very
  // panel away with it. The box is still updatable afterwards — but only
  // from a shell on it, which is not something to discover later.
  const losesUpdateUi = result?.target_lacks_update_ui === true
  const downgradeWarning = t(
    `Warning: ${result?.latest} predates ${result?.update_ui_since ?? '1.4.8'} and has no in-UI updater. After installing it, Settings → Updates disappears and you can only update from a shell on the box: /opt/pitun/scripts/pitun-update.sh --force`,
    `Внимание: ${result?.latest} старше ${result?.update_ui_since ?? '1.4.8'} и не содержит обновления из UI. После установки раздел «Обновления» исчезнет, и обновиться можно будет только из консоли на коробке: /opt/pitun/scripts/pitun-update.sh --force`,
  )

  const onUpdate = async (force: boolean) => {
    const base = t(
      'The backend and frontend containers restart during the update, so the UI will be unavailable for about a minute. A database snapshot is taken first and kept under /opt/pitun/data-backup-pre-*.db — restoring it, if ever needed, is a manual step.',
      'Во время обновления контейнеры backend и frontend перезапускаются, и UI будет недоступен около минуты. Перед началом снимается снапшот базы в /opt/pitun/data-backup-pre-*.db — восстановление из него, если понадобится, выполняется вручную.',
    )
    const ok = await confirm({
      title: force
        ? t('Re-install the current version?', 'Переустановить текущую версию?')
        : t(`Update to ${result?.latest}?`, `Обновить до ${result?.latest}?`),
      body: losesUpdateUi ? `${downgradeWarning}

${base}` : base,
      confirmLabel: t('Update', 'Обновить'),
      danger: losesUpdateUi,
    })
    if (ok) start.mutate(force)
  }

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-5 space-y-4">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <ArrowUpCircle className="h-4 w-4 text-brand-400" />
          {t('Updates', 'Обновления')}
        </h2>
        <button
          type="button"
          onClick={() => check.mutate()}
          disabled={check.isPending || busy}
          className="flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={clsx('h-4 w-4', check.isPending && 'animate-spin')} />
          {t('Check for updates', 'Проверить обновления')}
        </button>
      </div>

      {/* ── In-flight update ─────────────────────────────────────── */}
      {busy && (
        <div className="rounded-lg border border-brand-800/50 bg-brand-950/30 p-3 space-y-2">
          <div className="flex items-center gap-2 text-sm text-brand-700 dark:text-brand-200">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>
              {restarting
                ? t('Restarting services…', 'Перезапуск сервисов…')
                : (status.data?.message || t('Updating…', 'Обновление…'))}
            </span>
            <span className="ml-auto font-mono text-xs text-brand-300">
              {status.data?.pct ?? 0}%
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-800">
            <div
              className="h-full rounded-full bg-brand-500 transition-all duration-500"
              style={{ width: `${Math.min(100, Math.max(0, status.data?.pct ?? 0))}%` }}
            />
          </div>
          {status.data?.request_pending && status.data?.state === 'queued' && (
            <p className="text-[11px] text-gray-400 flex items-start gap-1.5">
              <Info className="mt-0.5 h-3 w-3 shrink-0" />
              {t(
                'Waiting for the host update agent. If this does not move, the agent is not installed — run scripts/pitun-update.sh --install-timer on the box.',
                'Ожидание агента обновления на хосте. Если ничего не меняется, агент не установлен — запустите scripts/pitun-update.sh --install-timer на коробке.',
              )}
            </p>
          )}
        </div>
      )}

      {/* ── Terminal state from the last run ─────────────────────── */}
      {!busy && status.data?.state === 'done' && (
        <div className="rounded-lg border border-green-200 dark:border-green-800/50 bg-green-50 dark:bg-green-950/30 px-3 py-2 text-sm text-green-800 dark:text-green-200 flex items-start gap-2">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {t('Updated to ', 'Обновлено до ')}
            <span className="font-mono">{status.data.to}</span>
            {status.data.from ? ` (${t('was', 'было')} ${status.data.from})` : ''}
          </span>
        </div>
      )}
      {!busy && status.data?.state === 'failed' && (
        <div className="rounded-lg border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-950/30 px-3 py-2 text-sm text-red-800 dark:text-red-200 flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {t('Update failed: ', 'Обновление не удалось: ')}
            {status.data.message || t('see the agent log on the box', 'смотрите лог агента на коробке')}
            {' — '}
            {t(
              'the pre-update database snapshot is kept under /opt/pitun/data-backup-pre-*.db.',
              'снапшот базы, снятый перед обновлением, лежит в /opt/pitun/data-backup-pre-*.db.',
            )}
          </span>
        </div>
      )}

      {/* ── Check result ─────────────────────────────────────────── */}
      {checkError && (
        <div className="rounded-lg border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-950/30 px-3 py-2 text-xs text-red-700 dark:text-red-300">
          {checkError}
        </div>
      )}

      {result && !checkError && (
        <div className="space-y-2 text-sm">
          <div className="flex items-center gap-2 text-gray-300">
            <span className="text-gray-500">{t('Installed', 'Установлено')}:</span>
            <span className="font-mono">{result.current}</span>
            {result.latest && (
              <>
                <span className="text-gray-600">→</span>
                <span className="text-gray-500">{t('latest', 'последняя')}:</span>
                <span className="font-mono">{result.latest}</span>
              </>
            )}
          </div>

          {unreachable ? (
            <div className="rounded-lg border border-yellow-200 dark:border-yellow-900/50 bg-yellow-50 dark:bg-yellow-950/20 px-3 py-2 text-xs text-yellow-800 dark:text-yellow-200 flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>
                {result.error || t('GitHub is unreachable.', 'GitHub недоступен.')}
              </span>
            </div>
          ) : available ? (
            <div className="flex items-center gap-3 flex-wrap">
              <button
                type="button"
                onClick={() => onUpdate(false)}
                disabled={busy || start.isPending}
                className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors disabled:opacity-50"
              >
                <ArrowUpCircle className="h-4 w-4" />
                {t('Update now', 'Обновить сейчас')}
              </button>
              <span className="text-xs text-gray-500">
                {t('Fetched via', 'Получено через')} {result.network_path}
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-xs text-gray-400">
              <CheckCircle2 className="h-3.5 w-3.5 text-green-500" />
              {t('You are on the latest version.', 'Установлена последняя версия.')}
              <button
                type="button"
                onClick={() => onUpdate(true)}
                disabled={busy || start.isPending}
                className="ml-2 underline decoration-dotted hover:text-gray-200 disabled:opacity-50"
                title={t(
                  'Re-download and re-apply the same version — use if an install left something broken',
                  'Скачать и применить ту же версию заново — если установка что-то повредила',
                )}
              >
                {t('re-install', 'переустановить')}
              </button>
            </div>
          )}

          {losesUpdateUi && (
            <div className="rounded-lg border border-yellow-200 dark:border-yellow-900/50 bg-yellow-50 dark:bg-yellow-950/20 px-3 py-2 text-xs text-yellow-800 dark:text-yellow-200 flex items-start gap-2">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              <span>{downgradeWarning}</span>
            </div>
          )}

          {result.notes && available && (
            <details className="text-xs text-gray-400">
              <summary className="cursor-pointer hover:text-gray-200">
                {t('Release notes', 'Что нового')}
              </summary>
              <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap rounded-sm bg-black/40 p-2 font-mono text-[11px] leading-relaxed">
                {result.notes}
              </pre>
            </details>
          )}
        </div>
      )}

      {!result && !busy && !checkError && (
        <p className="text-xs text-gray-500">
          {t(
            'Checks GitHub releases. The request goes through the active node when one is up, so a throttled or blocked direct route is not a problem.',
            'Проверяет релизы на GitHub. Запрос идёт через активную ноду, если она поднята, — поэтому заблокированный или замедленный прямой маршрут не помеха.',
          )}
        </p>
      )}
    </div>
  )
}

export default UpdateSection
