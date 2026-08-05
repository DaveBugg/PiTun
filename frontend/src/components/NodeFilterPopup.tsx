import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Filter, X } from 'lucide-react'
import { clsx } from 'clsx'

import { subsApi } from '@/api/client'
import { useT } from '@/hooks/useT'
import type { Subscription } from '@/types'

/**
 * Multi-axis filter popover for the Nodes page (since v1.3.3). Lets
 * the operator narrow the listing by subscription / protocol / online
 * status / group when a single subscription pulls 1000+ rows and the
 * old protocol-chips-only filter isn't enough.
 *
 * Why a popover rather than always-visible chips: each axis would add
 * another row to an already-busy toolbar (search + protocol chips +
 * pagination controls). Hiding them behind one button keeps the
 * common "no filters set" case clean.
 *
 * State is fully controlled — parent owns the `value` and gets every
 * change immediately via `onChange`. Persistence (URL state /
 * localStorage) is the parent's call.
 */

export interface NodeFilterState {
  subscription_id?: number
  /** When true: only nodes with no subscription (`subscription_id IS NULL`).
   *  Mutually exclusive with `subscription_id` in the UI — selecting one
   *  clears the other so we don't pass both to the backend. */
  local?: boolean
  protocol?: string
  online?: boolean
  group?: string
}

export interface NodeFilterPopupProps {
  value: NodeFilterState
  onChange: (next: NodeFilterState) => void
  /**
   * Protocols available across the current Node set. Passed in so the
   * popover doesn't have to do its own listing (parent already loads
   * nodes via `useNodesPage`). Empty list → protocol axis hidden.
   */
  protocols: string[]
  /**
   * Optional list of distinct groups across the current Node set.
   * Same rationale as protocols. Empty list → group axis hidden.
   */
  groups?: string[]
}

export function NodeFilterPopup({ value, onChange, protocols, groups = [] }: NodeFilterPopupProps) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)

  // Subscriptions for the dropdown. The endpoint returns at most a
  // handful of rows (one per configured subscription), so fetching on
  // popup mount is cheap.
  const { data: subs = [] } = useQuery<Subscription[]>({
    queryKey: ['subscriptions'],
    queryFn: () => subsApi.list(),
    // Subscriptions change rarely; 5-minute stale window is fine.
    staleTime: 5 * 60_000,
  })

  // Click-outside dismiss — mirror the NodesJsonIO dropdown pattern.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const target = e.target as globalThis.Node | null
      if (rootRef.current && target && !rootRef.current.contains(target)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])

  // Activity badge — counts how many filter axes are set. Surfaced
  // on the trigger button so the operator can see at a glance that
  // their view is filtered without having to open the popup. The
  // `subscription_id`/`local` pair count as a single axis (they're
  // mutually exclusive on the source dropdown).
  const activeCount =
    ((value.subscription_id != null || value.local) ? 1 : 0) +
    (value.protocol ? 1 : 0) +
    (value.online != null ? 1 : 0) +
    (value.group ? 1 : 0)

  const clearAll = () => onChange({})

  const set = <K extends keyof NodeFilterState>(key: K, v: NodeFilterState[K]) =>
    onChange({ ...value, [key]: v })

  // Subscription dropdown encodes three modes in one <select>:
  //   ''       = all sources (no subscription filter)
  //   'local'  = subscription_id IS NULL (locally-added nodes only)
  //   <id>     = belongs to that subscription
  // Switching options clears the OTHER axis so we never send both.
  const sourceValue: string =
    value.local ? 'local'
    : value.subscription_id != null ? String(value.subscription_id)
    : ''

  const handleSourceChange = (raw: string) => {
    if (raw === '') {
      onChange({ ...value, subscription_id: undefined, local: undefined })
    } else if (raw === 'local') {
      onChange({ ...value, subscription_id: undefined, local: true })
    } else {
      onChange({ ...value, subscription_id: Number(raw), local: undefined })
    }
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className={clsx(
          'flex items-center gap-1.5 rounded-lg border px-2.5 py-2 text-sm transition-colors',
          activeCount > 0
            ? 'border-brand-700/60 bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-200 hover:bg-brand-50 dark:hover:bg-brand-900/50'
            : 'border-gray-800 bg-gray-900 text-gray-400 hover:text-gray-200 hover:border-gray-700',
        )}
        title={t('Filters', 'Фильтры')}
        aria-haspopup="dialog"
        aria-expanded={open}
      >
        <Filter className="h-4 w-4" />
        <span className="hidden sm:inline">{t('Filters', 'Фильтры')}</span>
        {activeCount > 0 && (
          <span className="rounded-full bg-brand-600 text-white text-[10px] leading-none px-1.5 py-0.5 font-semibold">
            {activeCount}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-1 w-[min(92vw,20rem)] rounded-lg border border-gray-700 bg-gray-950 shadow-xl z-50 p-3 space-y-3">
          {/* Header — title + clear-all */}
          <div className="flex items-center justify-between pb-2 border-b border-gray-800">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-gray-400">
              {t('Filter nodes', 'Фильтр нод')}
            </h3>
            {activeCount > 0 && (
              <button
                type="button"
                onClick={clearAll}
                className="text-[11px] text-brand-400 hover:text-brand-700 dark:hover:text-brand-300 inline-flex items-center gap-1"
              >
                <X className="h-3 w-3" />
                {t('Clear', 'Сбросить')}
              </button>
            )}
          </div>

          {/* Source — combined "subscription / local-only / all" axis.
              Single dropdown keeps the popup compact and reflects that
              `subscription_id` and `local=true` are mutually exclusive. */}
          <div className="space-y-1">
            <label className="text-[11px] uppercase tracking-wider text-gray-500">
              {t('Source', 'Источник')}
            </label>
            <select
              value={sourceValue}
              onChange={(e) => handleSourceChange(e.target.value)}
              className="w-full rounded-md border border-gray-800 bg-gray-900 px-2 py-1.5 text-sm text-gray-200 focus:border-brand-400 focus:outline-hidden"
            >
              <option value="">{t('All sources', 'Все источники')}</option>
              <option value="local">
                {t('Local only (no subscription)', 'Только локальные (без подписки)')}
              </option>
              {subs.length > 0 && (
                <option disabled>──────────</option>
              )}
              {subs.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name} {s.node_count ? `(${s.node_count})` : ''}
                </option>
              ))}
            </select>
          </div>

          {/* Protocol — only render if there's a choice to make */}
          {protocols.length > 0 && (
            <div className="space-y-1">
              <label className="text-[11px] uppercase tracking-wider text-gray-500">
                {t('Protocol', 'Протокол')}
              </label>
              <div className="flex flex-wrap gap-1">
                <FilterChip
                  active={!value.protocol}
                  onClick={() => set('protocol', undefined)}
                  label={t('All', 'Все')}
                />
                {protocols.map((p) => (
                  <FilterChip
                    key={p}
                    active={value.protocol === p}
                    onClick={() => set('protocol', p)}
                    label={p}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Online status */}
          <div className="space-y-1">
            <label className="text-[11px] uppercase tracking-wider text-gray-500">
              {t('Status', 'Статус')}
            </label>
            <div className="flex flex-wrap gap-1">
              <FilterChip
                active={value.online == null}
                onClick={() => set('online', undefined)}
                label={t('Any', 'Любой')}
              />
              <FilterChip
                active={value.online === true}
                onClick={() => set('online', true)}
                label={t('Online', 'Онлайн')}
              />
              <FilterChip
                active={value.online === false}
                onClick={() => set('online', false)}
                label={t('Offline', 'Офлайн')}
              />
            </div>
          </div>

          {/* Group — only if user actually uses the field */}
          {groups.length > 0 && (
            <div className="space-y-1">
              <label className="text-[11px] uppercase tracking-wider text-gray-500">
                {t('Group', 'Группа')}
              </label>
              <select
                value={value.group ?? ''}
                onChange={(e) => set('group', e.target.value === '' ? undefined : e.target.value)}
                className="w-full rounded-md border border-gray-800 bg-gray-900 px-2 py-1.5 text-sm text-gray-200 focus:border-brand-400 focus:outline-hidden"
              >
                <option value="">{t('All groups', 'Все группы')}</option>
                {groups.map((g) => (
                  <option key={g} value={g}>{g}</option>
                ))}
              </select>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function FilterChip({ active, onClick, label }: { active: boolean; onClick: () => void; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={clsx(
        'rounded-sm px-2 py-1 text-xs font-medium transition-colors',
        active
          ? 'bg-brand-600 text-white'
          : 'bg-gray-800 text-gray-400 hover:bg-gray-700',
      )}
    >
      {label}
    </button>
  )
}
