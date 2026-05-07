import { useEffect, useMemo, useRef, useState } from 'react'
import * as React from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import {
  Sparkles, Rocket, Loader2, Terminal, AlertTriangle, CheckCircle2,
  Ban, ExternalLink,
} from 'lucide-react'

import { serversApi } from '@/api/client'
import { ModalShell } from '@/components/ModalShell'
import { useT } from '@/hooks/useT'
import { useDeployments } from '@/hooks/useServers'
import {
  useCancelServerTask,
  useServerTask,
  useServerTaskStream,
} from '@/hooks/useServerTasks'
import type {
  DeployJobResult,
  Server,
  ServerDeploymentProtocol,
} from '@/types'

/**
 * Deploy modal — runs a remote install script over SSH and streams the
 * output live (since v1.3.0-beta.1, Phase 3).
 *
 * Two phases visible to the user:
 *   1. **Form** — domain / email / naive_user / naive_pass, defaults
 *      pulled from any existing ServerDeployment for prefill. "Run
 *      install" submits → `POST /servers/{id}/deploy` → 202 + job_id.
 *   2. **Streaming** — terminal panel rendering live stdout/stderr from
 *      the WS, status badge ticks once the WS closes with a `done`
 *      frame. On success with `result.node_id` we surface a "Go to
 *      node" link.
 *
 * Cancel is always available while the job is running. The remote
 * script keeps running on the VPS — see backend's `core.jobs.cancel`
 * docstring for the rationale; we just stop pumping its output to
 * this client. The deployment row still gets its terminal status if
 * the script eventually finishes.
 *
 * Sister to NaiveScriptModal (in Servers.tsx) which generates a
 * downloadable script for users who don't want to give PiTun their
 * SSH key. Both modals share the same form fields + save-deployment
 * flow so the badges / Create-node UX stay consistent.
 */
export function DeployModal({
  server,
  onClose,
}: {
  server: Server
  onClose: () => void
}) {
  const t = useT()

  // Pre-fill from existing deployment plan (if any).
  const { data: deployments = [] } = useDeployments(server.id)
  const existingNaive = deployments.find((d) => d.protocol === 'naive')

  const [domain, setDomain] = useState(existingNaive?.config.domain ?? '')
  const [email, setEmail] = useState(existingNaive?.config.email ?? '')
  const [naiveUser, setNaiveUser] = useState(
    existingNaive?.config.naive_user ?? 'pitun',
  )
  const [naivePass, setNaivePass] = useState(existingNaive?.config.naive_pass ?? '')

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)

  const buildParams = (): Record<string, unknown> | null => {
    if (!domain.trim() || !email.trim()) {
      setError(t('Domain and email are required', 'Domain и email обязательны'))
      return null
    }
    return {
      domain: domain.trim(),
      email: email.trim(),
      naive_user: naiveUser.trim() || undefined,
      // Empty pass → backend will auto-generate `secrets.token_urlsafe(24)`
      naive_pass: naivePass.trim() || undefined,
    }
  }

  const onStart = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')
    const params = buildParams()
    if (!params) return
    setSubmitting(true)
    try {
      const accepted = await serversApi.deploy(server.id, {
        protocol: 'naive',
        config: params,
      })
      setJobId(accepted.job_id)
    } catch (err: unknown) {
      // 409 SlotBusy or 400/500 — surface server message
      const msg = extractAxiosError(err)
      setError(msg)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <ModalShell onClose={onClose} labelledBy="deploy-modal-title">
      <div className="w-full max-w-3xl rounded-2xl bg-gray-950/95 border border-gray-800 p-6 m-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-start gap-3 mb-4">
          <div className="rounded-lg bg-brand-600/15 p-2 text-brand-400">
            <Rocket className="h-5 w-5" />
          </div>
          <div className="flex-1 min-w-0">
            <h2 id="deploy-modal-title" className="text-lg font-semibold text-gray-100">
              {t('Install NaiveProxy on', 'Установить NaiveProxy на')}{' '}
              <span className="text-brand-400">{server.name}</span>
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              {t(
                `Runs the install script over SSH on ${server.user}@${server.host}:${server.port}, streams output live, creates a Node on success.`,
                `Запустит установщик по SSH на ${server.user}@${server.host}:${server.port}, покажет вывод в реальном времени, создаст Node при успехе.`,
              )}
            </p>
          </div>
        </div>

        {!jobId && (
          <DeployForm
            domain={domain}
            email={email}
            naiveUser={naiveUser}
            naivePass={naivePass}
            error={error}
            submitting={submitting}
            setDomain={setDomain}
            setEmail={setEmail}
            setNaiveUser={setNaiveUser}
            setNaivePass={setNaivePass}
            onSubmit={onStart}
            onCancel={onClose}
          />
        )}

        {jobId && (
          <DeployRunning jobId={jobId} server={server} onClose={onClose} />
        )}
      </div>
    </ModalShell>
  )
}


// ── Form (pre-start) ────────────────────────────────────────────────────────

function DeployForm(props: {
  domain: string; email: string; naiveUser: string; naivePass: string
  error: string; submitting: boolean
  setDomain: (v: string) => void
  setEmail: (v: string) => void
  setNaiveUser: (v: string) => void
  setNaivePass: (v: string) => void
  onSubmit: (e: React.FormEvent) => void
  onCancel: () => void
}) {
  const t = useT()
  return (
    <form onSubmit={props.onSubmit}>
      {props.error && (
        <div className="mb-3 rounded-lg bg-red-900/30 border border-red-700/50 px-3 py-2 text-sm text-red-300 flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0" />
          <span>{props.error}</span>
        </div>
      )}

      <div className="space-y-3">
        <FieldL label={t('Domain', 'Домен')} hint={t('A-record points to the VPS', 'A-запись указывает на VPS')}>
          <input
            type="text"
            value={props.domain}
            onChange={(e) => props.setDomain(e.target.value)}
            placeholder="proxy.example.com"
            className={inputCls}
            required
            autoFocus
          />
        </FieldL>
        <FieldL label={t("Let's Encrypt email", 'Email для Let\'s Encrypt')}>
          <input
            type="email"
            value={props.email}
            onChange={(e) => props.setEmail(e.target.value)}
            placeholder="me@example.com"
            className={inputCls}
            required
          />
        </FieldL>
        <FieldL label={t('Naive username', 'Имя пользователя Naive')} hint={t('default "pitun"', 'по умолчанию "pitun"')}>
          <input
            type="text"
            value={props.naiveUser}
            onChange={(e) => props.setNaiveUser(e.target.value)}
            className={inputCls}
          />
        </FieldL>
        <FieldL
          label={t('Naive password', 'Пароль Naive')}
          hint={t(
            'leave blank — the server auto-generates one',
            'оставьте пустым — сервер сгенерирует автоматически',
          )}
        >
          <input
            type="text"
            value={props.naivePass}
            onChange={(e) => props.setNaivePass(e.target.value)}
            className={inputCls}
          />
        </FieldL>
      </div>

      <div className="rounded-lg border border-yellow-700/40 bg-yellow-900/10 px-3 py-2 mt-4 text-xs text-yellow-200 flex items-start gap-2">
        <Sparkles className="h-3.5 w-3.5 mt-0.5 flex-shrink-0 text-yellow-400" />
        <span>
          {t(
            'The install takes 2–5 minutes (apt update, Caddy build, Let\'s Encrypt cert). Output streams live below.',
            'Установка займёт 2–5 минут (apt update, сборка Caddy, сертификат Let\'s Encrypt). Вывод появится ниже в реальном времени.',
          )}
        </span>
      </div>

      <div className="flex gap-2 pt-5">
        <button
          type="button"
          onClick={props.onCancel}
          className="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-400 hover:bg-gray-800 transition-colors"
        >
          {t('Cancel', 'Отмена')}
        </button>
        <button
          type="submit"
          disabled={props.submitting}
          className="flex-1 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors flex items-center justify-center gap-1.5"
        >
          {props.submitting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" />
              {t('Starting…', 'Запуск…')}
            </>
          ) : (
            <>
              <Rocket className="h-4 w-4" />
              {t('Run install', 'Запустить установку')}
            </>
          )}
        </button>
      </div>
    </form>
  )
}


// ── Running view (post-start) ───────────────────────────────────────────────

function DeployRunning({
  jobId,
  server,
  onClose,
}: {
  jobId: string
  server: Server
  onClose: () => void
}) {
  const t = useT()
  const qc = useQueryClient()
  const { frames, done, error: wsError } = useServerTaskStream(jobId)
  // Poll the detail row while the WS is still open — once `done` is set
  // we stop polling and use the final detail snapshot for the result
  // banner (node_id, parsed_uri, error).
  const isRunning = done === null
  const { data: jobRow } = useServerTask(jobId, { polling: isRunning })

  const cancel = useCancelServerTask()
  const onCancel = () => cancel.mutate(jobId)

  // After finalize, refresh side-effects: nodes list (new naive node)
  // + servers (new ServerDeployment).
  useEffect(() => {
    if (done && done !== 'unknown') {
      qc.invalidateQueries({ queryKey: ['nodes'] })
      qc.invalidateQueries({ queryKey: ['servers'] })
      qc.invalidateQueries({ queryKey: ['servers', server.id, 'deployments'] })
    }
  }, [done, qc, server.id])

  const result = useMemo<DeployJobResult | null>(() => {
    const r = jobRow?.result
    if (!r) return null
    return r as DeployJobResult
  }, [jobRow])

  const finalStatus = jobRow?.status ?? (done === null ? 'running' : done)

  return (
    <div>
      {/* Status banner */}
      <StatusBanner
        status={finalStatus}
        result={result}
        error={jobRow?.error || wsError}
      />

      {/* Live log terminal */}
      <div className="mt-3 rounded-xl border border-gray-800 bg-black/60 overflow-hidden">
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-gray-800/60 bg-gray-900/50 text-xs text-gray-500">
          <Terminal className="h-3.5 w-3.5" />
          <span className="font-mono">job {jobId.slice(0, 12)}…</span>
          {isRunning && (
            <Loader2 className="h-3 w-3 animate-spin text-brand-400 ml-1" />
          )}
          <Link
            to={`/server-tasks?server_id=${server.id}`}
            className="ml-auto text-[11px] text-gray-500 hover:text-brand-400 inline-flex items-center gap-1"
            title={t('Open in Server-Tasks page', 'Открыть на странице Server-Tasks')}
          >
            {t('All tasks', 'Все задачи')}
            <ExternalLink className="h-3 w-3" />
          </Link>
        </div>
        <LogPanel frames={frames} fallbackTail={jobRow?.log_tail ?? null} />
      </div>

      {/* Footer actions */}
      <div className="flex gap-2 pt-4">
        {isRunning ? (
          <button
            type="button"
            onClick={onCancel}
            disabled={cancel.isPending}
            className="rounded-lg border border-red-800/60 bg-red-900/20 hover:bg-red-900/30 text-red-300 px-3 py-1.5 text-sm flex items-center gap-1.5 disabled:opacity-50 transition-colors"
            title={t(
              'Cancel local stream — remote script keeps running on the VPS',
              'Отменить локальный поток — скрипт продолжит работу на VPS',
            )}
          >
            <Ban className="h-4 w-4" />
            {cancel.isPending ? t('Cancelling…', 'Отмена…') : t('Cancel', 'Отменить')}
          </button>
        ) : (
          // Finalized — offer "Open node" if we got one, or just Close
          result?.node_id ? (
            <Link
              to={`/nodes`}
              onClick={onClose}
              className="rounded-lg bg-brand-600 hover:bg-brand-500 text-white px-3 py-1.5 text-sm font-medium flex items-center gap-1.5 transition-colors"
            >
              {t('Open Nodes', 'Открыть Nodes')}
              <ExternalLink className="h-4 w-4" />
            </Link>
          ) : null
        )}

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


// ── Helpers ─────────────────────────────────────────────────────────────────

function StatusBanner({
  status,
  result,
  error,
}: {
  status: string
  result: DeployJobResult | null
  error: string | null | undefined
}) {
  const t = useT()
  if (status === 'running') {
    return (
      <div className="rounded-lg border border-brand-700/40 bg-brand-900/10 px-3 py-2 text-sm text-brand-300 flex items-center gap-2">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span>{t('Running install…', 'Идёт установка…')}</span>
      </div>
    )
  }
  if (status === 'cancelled') {
    return (
      <div className="rounded-lg border border-yellow-700/40 bg-yellow-900/10 px-3 py-2 text-sm text-yellow-300 flex items-start gap-2">
        <Ban className="h-4 w-4 mt-0.5" />
        <div>
          <div className="font-medium">{t('Cancelled', 'Отменено')}</div>
          <div className="text-xs text-yellow-300/80 mt-0.5">
            {t(
              'The remote script may still be running on the VPS. Re-run when ready, or check the server manually.',
              'Скрипт может всё ещё выполняться на VPS. Повторите запуск или проверьте сервер вручную.',
            )}
          </div>
        </div>
      </div>
    )
  }
  if (status === 'failed') {
    return (
      <div className="rounded-lg border border-red-700/40 bg-red-900/10 px-3 py-2 text-sm text-red-300 flex items-start gap-2">
        <AlertTriangle className="h-4 w-4 mt-0.5" />
        <div className="min-w-0">
          <div className="font-medium">{t('Install failed', 'Установка не удалась')}</div>
          {error && (
            <div className="text-xs text-red-300/80 mt-0.5 break-words font-mono">{error}</div>
          )}
        </div>
      </div>
    )
  }
  // succeeded
  if (result?.status === 'deployed_no_uri') {
    return (
      <div className="rounded-lg border border-yellow-700/40 bg-yellow-900/10 px-3 py-2 text-sm text-yellow-300 flex items-start gap-2">
        <AlertTriangle className="h-4 w-4 mt-0.5" />
        <div>
          <div className="font-medium">{t('Script ran but no URI was emitted', 'Скрипт отработал, но URI не выдан')}</div>
          <div className="text-xs text-yellow-300/80 mt-0.5">
            {t(
              'Check the log below — you may need to add the Node manually.',
              'Проверьте лог ниже — возможно, придётся добавить Node вручную.',
            )}
          </div>
        </div>
      </div>
    )
  }
  if (result?.status === 'failed') {
    return (
      <div className="rounded-lg border border-red-700/40 bg-red-900/10 px-3 py-2 text-sm text-red-300 flex items-start gap-2">
        <AlertTriangle className="h-4 w-4 mt-0.5" />
        <div>
          <div className="font-medium">{t('Install failed', 'Установка не удалась')}</div>
          {result?.error && (
            <div className="text-xs text-red-300/80 mt-0.5 break-words font-mono">{result.error}</div>
          )}
        </div>
      </div>
    )
  }
  return (
    <div className="rounded-lg border border-emerald-700/40 bg-emerald-900/10 px-3 py-2 text-sm text-emerald-300 flex items-start gap-2">
      <CheckCircle2 className="h-4 w-4 mt-0.5" />
      <div className="min-w-0">
        <div className="font-medium">{t('Install succeeded', 'Установка прошла успешно')}</div>
        {result?.node_id != null && (
          <div className="text-xs text-emerald-300/80 mt-0.5">
            {t('Node created: ', 'Создана нода: ')}
            <span className="font-mono">#{result.node_id}</span>
            {result.duration_sec ? (
              <span className="text-emerald-300/60"> · {Math.round(result.duration_sec)}s</span>
            ) : null}
          </div>
        )}
      </div>
    </div>
  )
}


function LogPanel({
  frames,
  fallbackTail,
}: {
  frames: ReturnType<typeof useServerTaskStream>['frames']
  fallbackTail: string | null
}) {
  const ref = useRef<HTMLDivElement | null>(null)

  // Auto-scroll to bottom on new lines (the standard log-viewer UX).
  // Skip if user has scrolled up — implemented via a near-bottom check:
  // if they're within 60px of bottom, follow; otherwise stay put.
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [frames.length])

  const usingFallback = frames.length === 0 && fallbackTail
  const lines = usingFallback
    ? fallbackTail.split('\n').map((line, i) => ({
        kind: 'stdout' as const,
        line,
        idx: i,
      }))
    : frames.map((f, i) => ({
        kind: f.event === 'log' ? f.kind : 'stdout',
        line: f.event === 'log' ? f.line : '',
        idx: i,
      }))

  return (
    <div
      ref={ref}
      className="font-mono text-[11px] leading-snug max-h-72 overflow-y-auto p-3"
      // Keep the typical terminal vibe — soft scrollbar, monospace.
      style={{ scrollbarWidth: 'thin' }}
    >
      {lines.length === 0 ? (
        <div className="text-gray-600">…</div>
      ) : (
        lines.map((l) => (
          <div
            key={l.idx}
            className={l.kind === 'stderr' ? 'text-red-400' : 'text-gray-300'}
          >
            {l.line || ' '}
          </div>
        ))
      )}
    </div>
  )
}


// ── Misc ────────────────────────────────────────────────────────────────────

const inputCls =
  'w-full rounded-lg bg-gray-900 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:border-brand-500 focus:outline-none'

function FieldL({
  label, hint, children,
}: { label: string; hint?: string; children: React.ReactNode }) {
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

/** Best-effort message extraction from an axios error so we surface
 * whatever the backend's HTTPException.detail said (e.g. "Deploy
 * already running on server_id=7 protocol='naive'"). Falls back to
 * a generic "Failed to start". */
function extractAxiosError(err: unknown): string {
  if (typeof err === 'object' && err !== null) {
    // axios error shape — `response.data.detail` is FastAPI's standard.
    const e = err as { response?: { data?: { detail?: unknown } }, message?: string }
    const detail = e.response?.data?.detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      // 422 from Pydantic — array of {loc, msg, ...}
      return detail
        .map((d: { msg?: string; loc?: unknown }) => (d?.msg ?? '') + (d?.loc ? ' (' + JSON.stringify(d.loc) + ')' : ''))
        .filter(Boolean)
        .join('; ')
    }
    if (e.message) return e.message
  }
  return 'Failed to start deploy'
}

// Suppress "imported but unused" during the (rare) case where a type
// alias goes through without instantiation in this file.
export type { ServerDeploymentProtocol as _ServerDeploymentProtocol }
