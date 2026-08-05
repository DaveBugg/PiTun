import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { clsx } from 'clsx'
import { Loader2, Play, Check, AlertTriangle } from 'lucide-react'

import { autocheckApi, subsApi } from '@/api/client'
import { useT } from '@/hooks/useT'
import { ModalShell } from '@/components/ModalShell'
import { apiErrorText } from '@/lib/apiError'
import type { AutoCheck, AutoCheckScope, Node, Subscription } from '@/types'

/**
 * Auto-checks config — a background loop periodically speed-tests the scoped
 * nodes so `speed_mbps` / `speed_tested_at` stay fresh (feeds NodeCircle
 * best/min_speed + the staleness colour). One global config; speed only.
 */
export function AutoCheckModal({ nodes, onClose }: { nodes: Node[]; onClose: () => void }) {
  const t = useT()
  const qc = useQueryClient()

  const { data: cfg, isLoading } = useQuery<AutoCheck>({
    queryKey: ['autocheck'],
    queryFn: () => autocheckApi.get(),
    refetchOnWindowFocus: false,
  })
  const { data: subs = [] } = useQuery<Subscription[]>({
    queryKey: ['subscriptions'],
    queryFn: () => subsApi.list(),
    staleTime: 30_000,
  })

  // Distinct non-empty node groups for the "group" scope picker.
  const groups = useMemo(
    () => Array.from(new Set(nodes.map((n) => n.group).filter((g): g is string => !!g))).sort(),
    [nodes],
  )

  if (isLoading || !cfg) {
    return (
      <ModalShell onClose={onClose} labelledBy="autocheck-title">
        <div className="w-full max-w-lg rounded-2xl bg-gray-950/95 border border-gray-800 p-6 m-4 flex items-center gap-2 text-sm text-gray-400">
          <Loader2 className="h-4 w-4 animate-spin" />
          {t('Loading…', 'Загрузка…')}
        </div>
      </ModalShell>
    )
  }
  return <AutoCheckForm cfg={cfg} nodes={nodes} subs={subs} groups={groups} qc={qc} onClose={onClose} />
}

function AutoCheckForm({
  cfg, nodes, subs, groups, qc, onClose,
}: {
  cfg: AutoCheck
  nodes: Node[]
  subs: Subscription[]
  groups: string[]
  qc: ReturnType<typeof useQueryClient>
  onClose: () => void
}) {
  const t = useT()
  const [enabled, setEnabled] = useState(cfg.enabled)
  const [scopeKind, setScopeKind] = useState<AutoCheckScope>(cfg.scope_kind)
  const [interval, setIntervalStr] = useState(String(cfg.interval_minutes))
  const [subId, setSubId] = useState<string>(cfg.scope_kind === 'subscription' ? cfg.scope_value : '')
  const [group, setGroup] = useState<string>(cfg.scope_kind === 'group' ? cfg.scope_value : '')
  const [nodeIds, setNodeIds] = useState<Set<number>>(() => {
    if (cfg.scope_kind !== 'nodes') return new Set()
    try { return new Set(JSON.parse(cfg.scope_value || '[]')) } catch { return new Set() }
  })
  const [error, setError] = useState('')

  const scopeValue = (): string => {
    if (scopeKind === 'subscription') return subId
    if (scopeKind === 'group') return group
    if (scopeKind === 'nodes') return JSON.stringify(Array.from(nodeIds))
    return ''
  }

  const saveMut = useMutation({
    mutationFn: () =>
      autocheckApi.update({
        enabled,
        interval_minutes: Math.max(1, parseInt(interval, 10) || 1),
        scope_kind: scopeKind,
        scope_value: scopeValue(),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['autocheck'] })
      onClose()
    },
    onError: (e) => setError(apiErrorText(e, t('Save failed', 'Не удалось сохранить'))),
  })

  const runMut = useMutation({
    mutationFn: () => autocheckApi.run(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['autocheck'] }),
  })

  const toggleNode = (id: number) =>
    setNodeIds((prev) => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  const mins = Math.max(1, parseInt(interval, 10) || 1)
  const inputCls = 'w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden'

  return (
    <ModalShell onClose={onClose} labelledBy="autocheck-title">
      <div className="w-full max-w-lg rounded-2xl bg-gray-950/95 border border-gray-800 p-6 m-4 max-h-[90vh] overflow-y-auto space-y-4">
        <div>
          <h2 id="autocheck-title" className="text-lg font-semibold text-gray-100">
            {t('Auto speed-checks', 'Автопроверки скорости')}
          </h2>
          <p className="text-xs text-gray-500 mt-0.5">
            {t(
              'Periodically speed-tests the scoped nodes in the background so ratings stay fresh.',
              'Периодически замеряет скорость выбранных нод в фоне, чтобы оценки не устаревали.',
            )}
          </p>
        </div>

        {error && (
          <div className="rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/40 px-3 py-2 text-sm text-red-700 dark:text-red-300 flex items-start gap-2">
            <AlertTriangle className="h-3.5 w-3.5 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {/* Enable */}
        <label className="flex items-center justify-between gap-3 cursor-pointer">
          <span className="text-sm text-gray-200">{t('Enabled', 'Включено')}</span>
          <button
            type="button"
            role="switch"
            aria-checked={enabled}
            onClick={() => setEnabled((v) => !v)}
            className={clsx(
              'relative shrink-0 rounded-full transition-colors ring-1 h-5 w-9',
              enabled ? 'bg-brand-600 ring-brand-500/50' : 'bg-gray-700 ring-gray-600/50',
            )}
          >
            <span className={clsx('absolute top-0.5 left-0.5 h-4 w-4 rounded-full bg-white transition-transform', enabled && 'translate-x-4')} />
          </button>
        </label>

        {/* Interval */}
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">
            {t('Interval (minutes)', 'Интервал (минуты)')}
            <span className="text-gray-600 ml-1.5">
              {mins >= 60 ? `≈ ${(mins / 60).toFixed(mins % 60 ? 1 : 0)}h` : ''}
            </span>
          </label>
          <input
            type="number" inputMode="numeric" min={1}
            value={interval}
            onChange={(e) => setIntervalStr(e.target.value)}
            className={inputCls}
          />
        </div>

        {/* Scope */}
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">{t('Scope', 'Область')}</label>
          <select value={scopeKind} onChange={(e) => setScopeKind(e.target.value as AutoCheckScope)} className={inputCls}>
            <option value="all">{t('All nodes', 'Все ноды')}</option>
            <option value="subscription">{t('One subscription', 'Одна подписка')}</option>
            <option value="group">{t('One group', 'Одна группа')}</option>
            <option value="nodes">{t('Specific nodes', 'Выбранные ноды')}</option>
          </select>
        </div>

        {scopeKind === 'subscription' && (
          <select value={subId} onChange={(e) => setSubId(e.target.value)} className={inputCls}>
            <option value="">{t('— pick a subscription —', '— выберите подписку —')}</option>
            {subs.map((s) => <option key={s.id} value={String(s.id)}>{s.name}</option>)}
          </select>
        )}

        {scopeKind === 'group' && (
          <select value={group} onChange={(e) => setGroup(e.target.value)} className={inputCls}>
            <option value="">{t('— pick a group —', '— выберите группу —')}</option>
            {groups.map((g) => <option key={g} value={g}>{g}</option>)}
          </select>
        )}

        {scopeKind === 'nodes' && (
          <div className="max-h-48 overflow-y-auto rounded-sm border border-gray-800 bg-gray-900/40 p-2 space-y-1">
            {nodes.length === 0 && <div className="text-xs text-gray-500 px-1">{t('No nodes', 'Нет нод')}</div>}
            {nodes.map((n) => (
              <label key={n.id} className="flex items-center gap-2 text-sm text-gray-300 px-1 py-0.5 cursor-pointer hover:bg-gray-800/60 rounded-sm">
                <input type="checkbox" checked={nodeIds.has(n.id)} onChange={() => toggleNode(n.id)} className="accent-brand-500" />
                <span className="truncate">{n.name}</span>
              </label>
            ))}
          </div>
        )}

        {/* Status + run-now */}
        <div className="flex items-center justify-between gap-3 pt-1 text-xs text-gray-500">
          <span>
            {cfg.is_sweeping
              ? t('Sweeping now…', 'Идёт прогон…')
              : cfg.last_sweep
                ? t(`Last sweep: ${relTime(cfg.last_sweep)}`, `Последний прогон: ${relTime(cfg.last_sweep)}`)
                : t('Never run', 'Ещё не запускалось')}
          </span>
          <button
            type="button"
            onClick={() => runMut.mutate()}
            disabled={runMut.isPending || cfg.is_sweeping}
            className="rounded-lg border border-gray-700 px-2.5 py-1 text-xs text-gray-300 hover:bg-gray-800 disabled:opacity-50 inline-flex items-center gap-1.5"
            title={t('Run a sweep now', 'Прогнать сейчас')}
          >
            <Play className="h-3 w-3" />
            {runMut.data?.status === 'started' ? t('Started', 'Запущено') : t('Run now', 'Прогнать')}
          </button>
        </div>

        <div className="flex justify-end gap-2 pt-2">
          <button type="button" onClick={onClose} className="rounded-lg border border-gray-700 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-800">
            {t('Cancel', 'Отмена')}
          </button>
          <button
            type="button"
            onClick={() => { setError(''); saveMut.mutate() }}
            disabled={saveMut.isPending}
            className="rounded-lg bg-brand-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 inline-flex items-center gap-1.5"
          >
            {saveMut.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
            {t('Save', 'Сохранить')}
          </button>
        </div>
      </div>
    </ModalShell>
  )
}

function relTime(iso: string): string {
  const d = new Date(iso.endsWith('Z') ? iso : iso + 'Z')
  const diffMin = Math.floor((Date.now() - d.getTime()) / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const h = Math.floor(diffMin / 60)
  if (h < 24) return `${h}h ago`
  return d.toLocaleString()
}
