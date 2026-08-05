import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import {
  ListChecks, Loader2, CheckCircle2, XCircle, Ban, ArrowLeft,
  Clock, Server as ServerIcon, ChevronRight, Terminal,
  RefreshCw,
} from 'lucide-react'

import { useT } from '@/hooks/useT'
import { effectiveJobStatus } from '@/lib/jobStatus'
import { useServers } from '@/hooks/useServers'
import {
  useCancelServerTask,
  useServerTask,
  useServerTaskStream,
  useServerTasks,
} from '@/hooks/useServerTasks'
import type { JobStatus, JobSummary, DeployJobResult } from '@/types'


/**
 * `/server-tasks` page — recent install / deploy jobs (since v1.3.0-beta.1).
 *
 * UX:
 *   - Filters in URL query params (?server_id=<id>&status=<...>) so a
 *     refresh / share link preserves the view.
 *   - Master/detail layout: list on the left, detail panel on the right
 *     when a job is selected (selection stored in URL too: ?id=<job_id>).
 *   - Live log panel for the selected running job, polling-fallback
 *     for the log_tail of finalized jobs.
 *
 * Discoverability: this page is NOT in the main sidebar (per the v1.3.0
 * design — server-tasks are a Servers-page concern, not a top-level
 * concept). The only entry point is the "Tasks" link on the Servers
 * page header. Direct URL `/server-tasks` works for sharing / back
 * navigation.
 */
export function ServerTasks() {
  const t = useT()
  const [params, setParams] = useSearchParams()

  // URL-driven filter state — keep the page bookmarkable.
  const serverIdParam = params.get('server_id')
  const statusParam = params.get('status') as JobStatus | null
  const selectedId = params.get('id')

  const serverIdFilter = serverIdParam ? Number(serverIdParam) : undefined
  const statusFilter = statusParam ?? undefined

  const { data: servers = [] } = useServers()
  const { data: list, isLoading, refetch } = useServerTasks({
    server_id: serverIdFilter,
    status: statusFilter,
    limit: 100,
  })
  const jobs = list?.jobs ?? []

  // Auto-select the newest job once the list lands, if no `id` is set.
  useEffect(() => {
    if (!selectedId && jobs.length > 0) {
      const next = new URLSearchParams(params)
      next.set('id', jobs[0].id)
      setParams(next, { replace: true })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobs.length])

  const setFilter = (key: 'server_id' | 'status', value: string | null) => {
    const next = new URLSearchParams(params)
    if (value === null || value === '') next.delete(key)
    else next.set(key, value)
    next.delete('id')  // reset selection when filter changes
    setParams(next, { replace: true })
  }

  const selectJob = (id: string) => {
    const next = new URLSearchParams(params)
    next.set('id', id)
    setParams(next, { replace: true })
  }

  return (
    <div className="p-4 md:p-6">
      <header className="mb-5 flex flex-wrap items-center gap-3">
        <Link
          to="/servers"
          className="rounded-lg p-1.5 text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors"
          title={t('Back to Servers', 'Назад к серверам')}
        >
          <ArrowLeft className="h-5 w-5" />
        </Link>
        <ListChecks className="h-6 w-6 text-brand-400" />
        <h1 className="text-2xl font-semibold text-gray-100">
          {t('Server tasks', 'Задачи на серверах')}
        </h1>
        <p className="text-sm text-gray-500 hidden md:block">
          {t(
            'Async install / deploy jobs — last 30 days',
            'Асинхронные задачи установки и деплоя за последние 30 дней',
          )}
        </p>
        <button
          onClick={() => refetch()}
          className="ml-auto rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800 transition-colors flex items-center gap-1.5"
          title={t('Refresh list', 'Обновить список')}
        >
          <RefreshCw className="h-4 w-4" />
          {t('Refresh', 'Обновить')}
        </button>
      </header>

      {/* Filter strip */}
      <div className="mb-4 flex flex-wrap gap-2 items-center text-xs">
        <span className="text-gray-500">{t('Server:', 'Сервер:')}</span>
        <FilterPill
          label={t('All', 'Все')}
          active={!serverIdFilter}
          onClick={() => setFilter('server_id', null)}
        />
        {servers.map((s) => (
          <FilterPill
            key={s.id}
            label={s.name}
            active={serverIdFilter === s.id}
            onClick={() => setFilter('server_id', String(s.id))}
          />
        ))}

        <span className="text-gray-500 ml-3">{t('Status:', 'Статус:')}</span>
        <FilterPill
          label={t('All', 'Все')}
          active={!statusFilter}
          onClick={() => setFilter('status', null)}
        />
        {(['running', 'succeeded', 'failed', 'cancelled'] as JobStatus[]).map((s) => (
          <FilterPill
            key={s}
            label={s}
            active={statusFilter === s}
            onClick={() => setFilter('status', s)}
          />
        ))}
      </div>

      {/* Master/detail */}
      <div className="grid gap-4 lg:grid-cols-[minmax(320px,420px)_1fr]">
        <JobListPanel
          jobs={jobs}
          loading={isLoading}
          selectedId={selectedId}
          onSelect={selectJob}
        />
        <JobDetailPanel jobId={selectedId} />
      </div>
    </div>
  )
}


// ── Filter pill ─────────────────────────────────────────────────────────────

function FilterPill({
  label, active, onClick,
}: {
  label: string; active: boolean; onClick: () => void
}) {
  return (
    <button
      onClick={onClick}
      className={
        'rounded-full px-2.5 py-1 transition-colors ' +
        (active
          ? 'bg-brand-50 dark:bg-brand-600/30 text-brand-700 dark:text-brand-200 border border-brand-500/50'
          : 'border border-gray-800 text-gray-400 hover:bg-gray-800/60')
      }
    >
      {label}
    </button>
  )
}


// ── Master list ─────────────────────────────────────────────────────────────

function JobListPanel({
  jobs, loading, selectedId, onSelect,
}: {
  jobs: JobSummary[]
  loading: boolean
  selectedId: string | null
  onSelect: (id: string) => void
}) {
  const t = useT()
  if (loading) {
    return (
      <div className="rounded-2xl border border-gray-800 bg-gray-900/30 p-6 text-center text-sm text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin inline-block mr-2" />
        {t('Loading…', 'Загрузка…')}
      </div>
    )
  }
  if (jobs.length === 0) {
    return (
      <div className="rounded-2xl border border-dashed border-gray-800 bg-gray-900/30 p-8 text-center">
        <Terminal className="h-8 w-8 text-gray-600 mx-auto mb-2" />
        <p className="text-sm text-gray-400">
          {t('No tasks yet', 'Задач ещё нет')}
        </p>
        <p className="mt-1 text-xs text-gray-600">
          {t(
            'Run an install on a server to create your first task.',
            'Запустите установку на сервере, чтобы создать первую задачу.',
          )}
        </p>
      </div>
    )
  }
  return (
    <div className="rounded-2xl border border-gray-800 bg-gray-900/30 overflow-hidden">
      <ul className="divide-y divide-gray-800/60">
        {jobs.map((j) => (
          <JobListItem
            key={j.id}
            job={j}
            selected={selectedId === j.id}
            onClick={() => onSelect(j.id)}
          />
        ))}
      </ul>
    </div>
  )
}


function JobListItem({
  job, selected, onClick,
}: {
  job: JobSummary; selected: boolean; onClick: () => void
}) {
  const startedAt = useMemo(() => {
    try {
      return new Date(job.started_at).toLocaleString()
    } catch {
      return job.started_at
    }
  }, [job.started_at])
  const durationLabel = useMemo(() => {
    if (!job.finished_at) return null
    try {
      const a = new Date(job.started_at).getTime()
      const b = new Date(job.finished_at).getTime()
      const sec = Math.max(0, Math.round((b - a) / 1000))
      if (sec < 60) return `${sec}s`
      return `${Math.floor(sec / 60)}m ${sec % 60}s`
    } catch {
      return null
    }
  }, [job.started_at, job.finished_at])

  return (
    <li>
      <button
        onClick={onClick}
        className={
          'w-full text-left px-4 py-3 flex items-center gap-3 transition-colors ' +
          (selected ? 'bg-brand-900/15' : 'hover:bg-gray-900/50')
        }
      >
        <StatusIcon status={effectiveJobStatus(job)} />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-gray-100 truncate">
              {job.target_name || `target #${job.target_id ?? '?'}`}
            </span>
            <span className="text-[11px] text-gray-500 font-mono">
              {job.protocol}
            </span>
          </div>
          <div className="mt-0.5 text-[11px] text-gray-500 flex items-center gap-2">
            <Clock className="h-3 w-3" />
            <span>{startedAt}</span>
            {durationLabel && (
              <>
                <span className="text-gray-700">·</span>
                <span>{durationLabel}</span>
              </>
            )}
          </div>
        </div>
        <ChevronRight className="h-4 w-4 text-gray-600 shrink-0" />
      </button>
    </li>
  )
}


// ── Detail panel ────────────────────────────────────────────────────────────

function JobDetailPanel({ jobId }: { jobId: string | null }) {
  const t = useT()
  const { data: job, isLoading } = useServerTask(jobId, {
    polling: true,  // cheap; we'll see it stop at terminal status
  })
  // Open WS only for running jobs to avoid useless reconnects.
  const isRunning = job?.status === 'running'
  const stream = useServerTaskStream(isRunning ? jobId : null)
  const cancel = useCancelServerTask()

  if (!jobId) {
    return (
      <div className="rounded-2xl border border-gray-800 bg-gray-900/20 p-10 text-center text-sm text-gray-500">
        {t('Select a task to view details', 'Выберите задачу для просмотра деталей')}
      </div>
    )
  }
  if (isLoading || !job) {
    return (
      <div className="rounded-2xl border border-gray-800 bg-gray-900/20 p-10 text-center text-sm text-gray-500">
        <Loader2 className="h-4 w-4 animate-spin inline-block mr-2" />
        {t('Loading…', 'Загрузка…')}
      </div>
    )
  }

  const result = (job.result ?? null) as DeployJobResult | null

  return (
    <div className="rounded-2xl border border-gray-800 bg-gray-900/30 overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-gray-800/60 flex items-start gap-3">
        <StatusIcon status={effectiveJobStatus(job)} large />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h2 className="text-base font-semibold text-gray-100 truncate">
              {job.kind} · {job.target_name || `#${job.target_id ?? '?'}`}
            </h2>
            <span className="text-xs text-gray-500 font-mono">{job.protocol}</span>
            <StatusPill status={effectiveJobStatus(job)} />
          </div>
          <div className="mt-1 text-[11px] text-gray-500 font-mono break-all">
            id {job.id}
          </div>
        </div>
        {isRunning && (
          <button
            onClick={() => cancel.mutate(job.id)}
            disabled={cancel.isPending}
            className="rounded-lg border border-red-200 dark:border-red-800/60 bg-red-50 dark:bg-red-900/20 hover:bg-red-50 dark:hover:bg-red-900/30 text-red-700 dark:text-red-300 px-3 py-1.5 text-xs flex items-center gap-1.5 disabled:opacity-50 transition-colors shrink-0"
          >
            <Ban className="h-3.5 w-3.5" />
            {cancel.isPending ? t('Cancelling…', 'Отмена…') : t('Cancel', 'Отменить')}
          </button>
        )}
      </div>

      {/* Result summary */}
      <div className="px-4 py-3 border-b border-gray-800/60 grid gap-2 sm:grid-cols-2 text-xs">
        <DetailKV label={t('Started', 'Запущено')} value={fmtDate(job.started_at)} />
        <DetailKV label={t('Finished', 'Завершено')} value={fmtDate(job.finished_at)} />
        {job.target_id != null && (
          <DetailKV
            label={t('Target server', 'Целевой сервер')}
            value={
              <Link
                to="/servers"
                className="text-brand-400 hover:text-brand-700 dark:hover:text-brand-300 inline-flex items-center gap-1"
              >
                <ServerIcon className="h-3 w-3" />
                #{job.target_id}
              </Link>
            }
          />
        )}
        {result && typeof result === 'object' && 'node_id' in result && result.node_id != null && (
          <DetailKV
            label={t('Created node', 'Создана нода')}
            value={
              <Link
                to="/nodes"
                className="text-brand-400 hover:text-brand-700 dark:hover:text-brand-300 inline-flex items-center gap-1 font-mono"
              >
                #{result.node_id}
              </Link>
            }
          />
        )}
        {job.error && (
          <div className="sm:col-span-2 rounded-lg bg-red-50 dark:bg-red-900/15 border border-red-200 dark:border-red-700/40 px-2 py-1.5 text-red-700 dark:text-red-300 text-[11px] font-mono wrap-break-word">
            {job.error}
          </div>
        )}
        {result?.status === 'deployed_no_uri' && (
          <div className="sm:col-span-2 rounded-lg bg-yellow-50 dark:bg-yellow-900/15 border border-yellow-200 dark:border-yellow-700/40 px-2 py-1.5 text-yellow-800 dark:text-yellow-200 text-[11px]">
            {t(
              'Script ran but no URI was emitted — add the Node manually.',
              'Скрипт отработал, но URI не выдан — добавьте Node вручную.',
            )}
          </div>
        )}
      </div>

      {/* Log */}
      <div className="bg-black/60">
        <div className="flex items-center gap-2 px-3 py-1.5 border-b border-gray-800/60 bg-gray-900/50 text-xs text-gray-500">
          <Terminal className="h-3.5 w-3.5" />
          <span>{t('Log', 'Лог')}</span>
          {isRunning && (
            <Loader2 className="h-3 w-3 animate-spin text-brand-400 ml-1" />
          )}
        </div>
        <DetailLog
          frames={stream.frames}
          fallbackTail={job.log_tail}
        />
      </div>
    </div>
  )
}


function DetailLog({
  frames, fallbackTail,
}: {
  frames: ReturnType<typeof useServerTaskStream>['frames']
  fallbackTail: string | null
}) {
  // Same auto-scroll-on-new-line behaviour as DeployModal.LogPanel.
  const [autoScroll, setAutoScroll] = useState(true)

  const usingFallback = frames.length === 0 && fallbackTail
  const lines = usingFallback
    ? fallbackTail.split('\n').map((line, i) => ({
        kind: 'stdout' as const, line, idx: i,
      }))
    : frames.map((f, i) => ({
        kind: f.event === 'log' ? f.kind : 'stdout',
        line: f.event === 'log' ? f.line : '',
        idx: i,
      }))

  return (
    <div
      onScroll={(e) => {
        const el = e.currentTarget
        const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60
        setAutoScroll(nearBottom)
      }}
      ref={(el) => {
        if (el && autoScroll) el.scrollTop = el.scrollHeight
      }}
      className="font-mono text-[11px] leading-snug max-h-96 overflow-y-auto p-3"
      style={{ scrollbarWidth: 'thin' }}
    >
      {lines.length === 0 ? (
        <div className="text-slate-500">…</div>
      ) : (
        lines.map((l) => (
          <div
            key={l.idx}
            // Fixed-dark terminal (bg-black/60) → light text in BOTH themes
            // (slate/red don't invert like our gray tokens).
            className={l.kind === 'stderr' ? 'text-red-400' : 'text-slate-300'}
          >
            {l.line || ' '}
          </div>
        ))
      )}
    </div>
  )
}


// ── Tiny presentational helpers ─────────────────────────────────────────────

function StatusIcon({ status, large }: { status: JobStatus; large?: boolean }) {
  const cls = large ? 'h-5 w-5' : 'h-4 w-4'
  if (status === 'running') return <Loader2 className={cls + ' animate-spin text-brand-400 shrink-0'} />
  if (status === 'succeeded') return <CheckCircle2 className={cls + ' text-emerald-600 dark:text-emerald-400 shrink-0'} />
  if (status === 'failed') return <XCircle className={cls + ' text-red-600 dark:text-red-400 shrink-0'} />
  if (status === 'cancelled') return <Ban className={cls + ' text-yellow-600 dark:text-yellow-400 shrink-0'} />
  return <ListChecks className={cls + ' text-gray-500 shrink-0'} />
}

function StatusPill({ status }: { status: JobStatus }) {
  const styles: Record<JobStatus, string> = {
    running: 'bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-200 border-brand-700/50',
    succeeded: 'bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-700/50',
    failed: 'bg-red-50 dark:bg-red-900/30 text-red-700 dark:text-red-300 border-red-200 dark:border-red-700/50',
    cancelled: 'bg-yellow-50 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-300 border-yellow-200 dark:border-yellow-700/50',
  }
  return (
    <span className={
      'rounded-full px-2 py-0.5 text-[10px] uppercase tracking-wider font-medium border ' +
      styles[status]
    }>
      {status}
    </span>
  )
}

function DetailKV({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-gray-500">{label}:</span>
      <span className="text-gray-300">{value || '—'}</span>
    </div>
  )
}

function fmtDate(iso: string | null): string {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString()
  } catch {
    return iso
  }
}
