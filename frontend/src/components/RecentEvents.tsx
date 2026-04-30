import { useState } from 'react'
import {
  Bell, RefreshCw, Trash2, ChevronDown, ChevronRight,
  Shuffle, Map, Layers, Repeat, AlertTriangle, Activity, RotateCw, Rss,
} from 'lucide-react'
import { clsx } from 'clsx'
import { useEvents, useClearEvents } from '@/hooks/useEvents'
import { useConfirm } from '@/components/ConfirmModal'
import { useT } from '@/hooks/useT'
import { useAppStore } from '@/store'
import type { Event, EventSeverity } from '@/types'

/**
 * Recent Events card on Dashboard.
 *
 * Polls /api/events every 30s for the latest N events (default 8) and
 * renders them as a compact list. Each row shows: relative time + icon
 * + severity dot + localized title + chevron to expand details.
 *
 * Design notes:
 * - Backend stores ASCII English titles. We render an icon + locale-aware
 *   "category label" via CATEGORY_META so adding a new category on the
 *   backend doesn't require a frontend rebuild — unknown codes fall back
 *   to a generic Activity icon.
 * - "Clear" wipes everything via DELETE /api/events. Confirm modal first
 *   so the user doesn't lose context if they're investigating an issue.
 * - Empty state is benign — most installs won't see events for hours,
 *   the empty card serves as documentation that the feature exists.
 */

interface CategoryMeta {
  icon: React.ComponentType<{ className?: string }>
  labelEn: string
  labelRu: string
}

// Lookup is by category code. Prefix match ("failover.*" → failover meta)
// is handled in `metaFor()` so adding sub-categories on the backend
// (e.g. "failover.no_fallback") just falls through to the parent visuals.
const CATEGORY_META: Record<string, CategoryMeta> = {
  failover:        { icon: Shuffle,        labelEn: 'Failover',     labelRu: 'Failover'      },
  sidecar:         { icon: RotateCw,       labelEn: 'Sidecar',      labelRu: 'Sidecar'       },
  circle:          { icon: Repeat,         labelEn: 'Node Circle',  labelRu: 'Node Circle'   },
  geo:             { icon: Map,            labelEn: 'GeoData',      labelRu: 'GeoData'       },
  subscription:    { icon: Rss,            labelEn: 'Subscription', labelRu: 'Подписка'      },
  xray:            { icon: Layers,         labelEn: 'xray',         labelRu: 'xray'          },
}

function metaFor(category: string): CategoryMeta {
  const root = category.split('.')[0]
  return CATEGORY_META[root] ?? {
    icon: Activity, labelEn: category, labelRu: category,
  }
}

const SEVERITY_DOT: Record<EventSeverity, string> = {
  info:    'bg-brand-500',
  warning: 'bg-amber-500',
  error:   'bg-red-500',
}

const SEVERITY_TEXT: Record<EventSeverity, string> = {
  info:    'text-brand-400',
  warning: 'text-amber-400',
  error:   'text-red-400',
}

/** "5 min ago" / "2 h ago" / "3 d ago" / "just now". UTC-aware. */
function formatTimeAgo(iso: string, lang: 'en' | 'ru'): string {
  const ts = new Date(iso.endsWith('Z') ? iso : iso + 'Z').getTime()
  const sec = Math.max(1, Math.round((Date.now() - ts) / 1000))
  if (sec < 30)   return lang === 'ru' ? 'только что' : 'just now'
  if (sec < 90)   return lang === 'ru' ? 'минуту назад' : '1 min ago'
  if (sec < 3600) return lang === 'ru' ? `${Math.round(sec / 60)} мин назад` : `${Math.round(sec / 60)} min ago`
  if (sec < 86400) {
    const h = Math.round(sec / 3600)
    return lang === 'ru' ? `${h} ч назад` : `${h} h ago`
  }
  const d = Math.round(sec / 86400)
  return lang === 'ru' ? `${d} дн назад` : `${d} d ago`
}

export function RecentEvents({ limit = 8 }: { limit?: number }) {
  const t = useT()
  const lang = useAppStore((s) => s.lang)
  const events = useEvents({ limit })
  const clear = useClearEvents()
  const confirm = useConfirm()
  const [expanded, setExpanded] = useState<Set<number>>(new Set())

  const toggleExpand = (id: number) => {
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleClear = async () => {
    const ok = await confirm({
      title: t('Clear all events?', 'Очистить все события?'),
      body: t(
        'Clears the entire Recent Events feed. New events will appear as they happen.',
        'Удаляет всю ленту событий. Новые события появятся по мере возникновения.'
      ),
      confirmLabel: t('Clear', 'Очистить'),
      danger: true,
    })
    if (ok) clear.mutate()
  }

  const items = events.data ?? []

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30">
      <div className="flex items-center justify-between border-b border-gray-800 px-4 py-2.5">
        <div className="flex items-center gap-2">
          <Bell className="h-4 w-4 text-gray-500" />
          <h3 className="text-sm font-semibold text-gray-200">
            {t('Recent Events', 'Лента событий')}
          </h3>
          {events.isFetching && <RefreshCw className="h-3 w-3 text-gray-600 animate-spin" />}
        </div>
        {items.length > 0 && (
          <button
            onClick={handleClear}
            disabled={clear.isPending}
            className="flex items-center gap-1 rounded px-2 py-1 text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors disabled:opacity-50"
            title={t('Clear all events', 'Очистить все')}
          >
            <Trash2 className="h-3 w-3" />
            {t('Clear', 'Очистить')}
          </button>
        )}
      </div>

      {events.isLoading ? (
        <div className="p-4 text-xs text-gray-500">{t('Loading…', 'Загрузка…')}</div>
      ) : items.length === 0 ? (
        <div className="p-4 text-xs text-gray-500">
          {t(
            'No events yet — failovers, sidecar restarts, geo updates and circle rotations show up here.',
            'Событий пока нет — здесь появляются failover, рестарты sidecar, обновления geo и ротация circle.'
          )}
        </div>
      ) : (
        <ul className="divide-y divide-gray-800/60">
          {items.map((ev) => (
            <EventRow
              key={ev.id}
              ev={ev}
              expanded={expanded.has(ev.id)}
              onToggle={() => toggleExpand(ev.id)}
              lang={lang}
            />
          ))}
        </ul>
      )}
    </div>
  )
}

function EventRow({
  ev, expanded, onToggle, lang,
}: { ev: Event; expanded: boolean; onToggle: () => void; lang: 'en' | 'ru' }) {
  const meta = metaFor(ev.category)
  const Icon = meta.icon
  const hasDetails = !!ev.details
  const sev = (['info', 'warning', 'error'] as const).includes(ev.severity as any)
    ? (ev.severity as EventSeverity)
    : 'info'

  return (
    <li>
      <button
        onClick={hasDetails ? onToggle : undefined}
        disabled={!hasDetails}
        className={clsx(
          'w-full flex items-center gap-3 px-4 py-2.5 text-left',
          hasDetails && 'hover:bg-gray-800/40 transition-colors cursor-pointer',
          !hasDetails && 'cursor-default',
        )}
      >
        <span className="text-[10px] text-gray-600 font-mono w-20 shrink-0">
          {formatTimeAgo(ev.timestamp, lang)}
        </span>
        <Icon className={clsx('h-4 w-4 shrink-0', SEVERITY_TEXT[sev])} />
        <span className={clsx('h-1.5 w-1.5 rounded-full shrink-0', SEVERITY_DOT[sev])} />
        <span className="flex-1 min-w-0 text-sm text-gray-300 truncate">{ev.title}</span>
        {hasDetails && (
          expanded
            ? <ChevronDown className="h-3.5 w-3.5 text-gray-600 shrink-0" />
            : <ChevronRight className="h-3.5 w-3.5 text-gray-600 shrink-0" />
        )}
        {sev === 'error' && !hasDetails && (
          <AlertTriangle className="h-3.5 w-3.5 text-red-500 shrink-0" />
        )}
      </button>
      {expanded && hasDetails && (
        <div className="px-4 pb-3 pl-[7.5rem] text-xs text-gray-500 font-mono whitespace-pre-wrap break-words">
          {ev.details}
        </div>
      )}
    </li>
  )
}
