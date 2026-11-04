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
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ArrowUpCircle, CheckCircle2, Loader2, RefreshCw, AlertTriangle, Info, X,
} from 'lucide-react'
import { clsx } from 'clsx'

import { updateApi } from '@/api/client'
import { apiErrorText } from '@/lib/apiError'
import { useT } from '@/hooks/useT'
import { useConfirm } from '@/components/ConfirmModal'

const ACTIVE_STATES = ['queued', 'running']

// The status file keeps the last run's result for good. A success is news for
// half an hour; a failure stays until it is dismissed. Either can be closed,
// and a closed one stays closed (keyed by the run it describes).
const DONE_BANNER_MS = 30 * 60 * 1000
const DISMISS_KEY = 'pitun.update.dismissed'

// After "Update now" the host agent may need a few seconds before it writes its
// first status (it consumes the request, then resolves the network path), and
// for that moment the status file still shows the PREVIOUS run. Keep watching,
// and keep showing "starting", until the file changes or this long has passed.
const LAUNCH_GRACE_MS = 120_000
const LAUNCH_MAX_MS = 10 * 60_000
// The click is remembered for the tab's lifetime, so a reload or a remount in
// the middle of the update (the page is often refreshed while the backend
// restarts) still shows the update as running.
const LAUNCH_KEY = 'pitun.update.launch'

type Launch = { since: number; stamp: string }

function readLaunch(): Launch | null {
  try {
    const raw = sessionStorage.getItem(LAUNCH_KEY)
    if (!raw) return null
    const v = JSON.parse(raw)
    return typeof v?.since === 'number' && typeof v?.stamp === 'string' ? v : null
  } catch { return null }
}

function writeLaunch(v: Launch | null) {
  try {
    if (v) sessionStorage.setItem(LAUNCH_KEY, JSON.stringify(v))
    else sessionStorage.removeItem(LAUNCH_KEY)
  } catch { /* private mode */ }
}

function readDismissed(): string {
  try { return localStorage.getItem(DISMISS_KEY) ?? '' } catch { return '' }
}

export function UpdateSection() {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [checkError, setCheckError] = useState<string | null>(null)
  const [dismissed, setDismissed] = useState(readDismissed)

  // Set when an update is requested: when, and the status stamp it was requested over.
  const launch = useRef<Launch | null>(readLaunch())
  const [, tick] = useState(0)
  // No status at all (the backend is down mid-restart) and an unreadable one
  // ("unknown") both mean "cannot tell", not "nothing is running": keep the
  // update on screen for as long as an update can plausibly take. Otherwise the
  // launch holds until the status file shows something other than the old run.
  const stillLaunching = (d: { state: string; updated_at?: string | null } | undefined) => {
    const l = launch.current
    if (l === null) return false
    const age = Date.now() - l.since
    if (d === undefined || d.state === 'unknown') return age < LAUNCH_MAX_MS
    return age < LAUNCH_GRACE_MS && (d.updated_at ?? '') === l.stamp
  }

  const status = useQuery({
    queryKey: ['system', 'update', 'status'],
    queryFn: () => updateApi.status(),
    // Poll only while something is in flight. A finished update leaves a
    // terminal state on disk, so there is nothing to watch afterwards.
    refetchInterval: (q) =>
      ACTIVE_STATES.includes(q.state.data?.state ?? '')
        || q.state.data?.request_pending === true
        || stillLaunching(q.state.data)
        ? 2000
        : false,
    // Mid-update the backend is down; keep the last known state rather
    // than blanking the panel, and don't count that as an error.
    retry: false,
  })

  // A request the host agent has not picked up yet counts as in flight too:
  // the status file still holds the previous run until it starts.
  const launching = stillLaunching(status.data)
  // An unreadable status mid-update keeps showing the last step we did see.
  const lastActive = useRef<NonNullable<typeof status.data> | undefined>(undefined)
  if (status.data && ACTIVE_STATES.includes(status.data.state)) lastActive.current = status.data
  const view = status.data?.state === 'unknown' && lastActive.current ? lastActive.current : status.data
  const busy =
    ACTIVE_STATES.includes(status.data?.state ?? '')
    || status.data?.request_pending === true
    || launching
  // The backend goes away during its own restart. While busy, a failed
  // poll means "restarting", not "broken".
  const restarting = busy && status.isError

  // The run answered with something that is not "in progress" (the agent found
  // nothing to do, or it already finished): the launch is over. Say so instead
  // of letting the progress box vanish without a word.
  const [launchNote, setLaunchNote] = useState<string | null>(null)
  useEffect(() => {
    const l = launch.current
    const d = status.data
    if (!l || !d) return
    const answered = (d.updated_at ?? '') !== l.stamp
    const terminal = !ACTIVE_STATES.includes(d.state) && d.state !== 'unknown' && d.request_pending !== true
    if (answered && terminal) {
      launch.current = null
      writeLaunch(null)
      if (d.state === 'idle' || d.state === 'available') setLaunchNote(d.message || null)
    }
  }, [status.data])

  const check = useMutation({
    mutationFn: () => updateApi.check(),
    onSuccess: () => setCheckError(null),
    onError: (err) => setCheckError(apiErrorText(err, t('Check failed', 'Проверка не удалась'))),
  })

  const start = useMutation({
    // Pin the version the user was shown: the agent would otherwise look the
    // latest release up again and could disagree with the check just made.
    mutationFn: (force: boolean) => updateApi.start({ force, version: force ? undefined : check.data?.latest ?? undefined }),
    onSuccess: (data) => {
      setLaunchNote(null)
      launch.current = { since: Date.now(), stamp: data.updated_at ?? '' }
      writeLaunch(launch.current)
      qc.setQueryData(['system', 'update', 'status'], data)
      tick((n) => n + 1)
    },
  })

  // Once an update finishes, the reported version changed — drop the
  // cached version data so the sidebar and About stop showing the old one.
  useEffect(() => {
    if (status.data?.state === 'done') {
      qc.invalidateQueries({ queryKey: ['system'] })
    }
  }, [status.data?.state, qc])

  // Which finished run the banner would describe, and whether it is still news.
  const runKey = `${status.data?.state}:${status.data?.to ?? ''}:${status.data?.updated_at ?? ''}`
  const finishedAt = status.data?.updated_at ? Date.parse(status.data.updated_at) : NaN
  const fresh = Number.isFinite(finishedAt) && Date.now() - finishedAt < DONE_BANNER_MS
  const showDone = !busy && status.data?.state === 'done' && fresh && dismissed !== runKey
  const showFailed = !busy && status.data?.state === 'failed' && dismissed !== runKey
  const dismiss = () => {
    setDismissed(runKey)
    try { localStorage.setItem(DISMISS_KEY, runKey) } catch { /* private mode */ }
  }

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
        <div className="rounded-lg border border-brand-800/50 bg-brand-900/30 p-3 space-y-2">
          <div className="flex items-center gap-2 text-sm text-brand-700 dark:text-brand-200">
            <Loader2 className="h-4 w-4 animate-spin" />
            <span>
              {restarting
                ? t('Restarting services…', 'Перезапуск сервисов…')
                : launching && !ACTIVE_STATES.includes(view?.state ?? '')
                  ? t('Starting the update…', 'Запуск обновления…')
                  : (view?.message || t('Updating…', 'Обновление…'))}
            </span>
            <span className="ml-auto font-mono text-xs text-brand-300">
              {launching && !ACTIVE_STATES.includes(view?.state ?? '') ? 0 : (view?.pct ?? 0)}%
            </span>
          </div>
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-gray-800">
            <div
              className="h-full rounded-full bg-brand-500 transition-all duration-500"
              style={{ width: `${launching && !ACTIVE_STATES.includes(view?.state ?? '') ? 0 : Math.min(100, Math.max(0, view?.pct ?? 0))}%` }}
            />
          </div>
          {view?.request_pending && view?.state === 'queued' && (
            <p className="text-[11px] text-gray-400 flex items-start gap-1.5">
              <Info className="mt-0.5 h-3 w-3 shrink-0" />
              {t(
                'Waiting for the host update agent. If this does not move, the agent is not installed — run scripts/pitun-update.sh --install-agent on the box.',
                'Ожидание агента обновления на хосте. Если ничего не меняется, агент не установлен — запустите scripts/pitun-update.sh --install-agent на коробке.',
              )}
            </p>
          )}
        </div>
      )}

      {!busy && launchNote && (
        <div className="rounded-lg border border-gray-700 bg-gray-800/40 px-3 py-2 text-sm text-gray-300 flex items-start gap-2">
          <Info className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{launchNote}</span>
        </div>
      )}

      {start.isError && (
        <div className="rounded-lg border border-red-800/50 bg-red-950/30 px-3 py-2 text-sm text-red-200 flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{apiErrorText(start.error, t('Could not start the update', 'Не удалось запустить обновление'))}</span>
        </div>
      )}

      {/* ── Terminal state from the last run ─────────────────────── */}
      {showDone && (
        <div className="rounded-lg border border-green-200 dark:border-green-800/50 bg-green-50 dark:bg-green-950/30 px-3 py-2 text-sm text-green-800 dark:text-green-200 flex items-start gap-2">
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {t('Updated to ', 'Обновлено до ')}
            <span className="font-mono">{status.data?.to}</span>
            {status.data?.from ? ` (${t('was', 'было')} ${status.data?.from})` : ''}
          </span>
          <button type="button" onClick={dismiss} aria-label={t('Dismiss', 'Закрыть')} className="ml-auto text-green-700/70 hover:text-green-600 dark:text-green-300/70 dark:hover:text-green-200">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}
      {showFailed && (
        <div className="rounded-lg border border-red-200 dark:border-red-900/50 bg-red-50 dark:bg-red-950/30 px-3 py-2 text-sm text-red-800 dark:text-red-200 flex items-start gap-2">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>
            {t('Update failed: ', 'Обновление не удалось: ')}
            {status.data?.message || t('see the agent log on the box', 'смотрите лог агента на коробке')}
            {' — '}
            {t(
              'the pre-update database snapshot is kept under /opt/pitun/data-backup-pre-*.db.',
              'снапшот базы, снятый перед обновлением, лежит в /opt/pitun/data-backup-pre-*.db.',
            )}
          </span>
          <button type="button" onClick={dismiss} aria-label={t('Dismiss', 'Закрыть')} className="ml-auto shrink-0 text-red-700/70 hover:text-red-600 dark:text-red-300/70 dark:hover:text-red-200">
            <X className="h-4 w-4" />
          </button>
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
