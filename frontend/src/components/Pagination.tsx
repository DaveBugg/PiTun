import { ChevronLeft, ChevronRight } from 'lucide-react'
import { clsx } from 'clsx'

import { useT } from '@/hooks/useT'

/**
 * Generic pagination control (since v1.3.3). Used by the Nodes page;
 * extracted as a separate component because once the device list /
 * DNS log views grow the same control will fit them.
 *
 * Renders three things in one toolbar:
 *  1. Page-size selector (10 / 50 / 100). Caller is responsible for
 *     persisting the choice (typically to localStorage).
 *  2. "Showing X-Y of Z" range label so the user knows where they are.
 *  3. Prev / Next buttons + a sparse page-number strip.
 *
 * Why a sparse strip rather than every page button: a subscription
 * with 1256 nodes paginated at 10/page is 126 pages — listing them
 * all in a row destroys the toolbar layout. Show first / last /
 * current ± 1 and gap markers.
 */

export interface PaginationProps {
  /** 1-based current page. */
  page: number
  /** Items per page. Caller controls the choice and persists it. */
  pageSize: number
  /** Total item count (across all pages). */
  total: number
  /** Available page-size options surfaced in the dropdown. */
  pageSizeOptions?: number[]
  onPageChange: (page: number) => void
  onPageSizeChange: (size: number) => void
  /** Override the noun shown in the range label (default: "items"). */
  itemLabel?: string
}

export function Pagination({
  page,
  pageSize,
  total,
  pageSizeOptions = [10, 50, 100],
  onPageChange,
  onPageSizeChange,
  itemLabel,
}: PaginationProps) {
  const t = useT()
  const lastPage = Math.max(1, Math.ceil(total / pageSize))
  const clamped = Math.min(Math.max(1, page), lastPage)
  const from = total === 0 ? 0 : (clamped - 1) * pageSize + 1
  const to = Math.min(clamped * pageSize, total)

  const noun = itemLabel ?? t('items', 'элементов')

  // Build a sparse page list: first, last, current ± 1, with `null`
  // sentinels for ellipsis gaps. Mirrors what GitHub / Linear do.
  const pages = buildPageStrip(clamped, lastPage)

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 py-2">
      <div className="flex items-center gap-3 text-xs text-gray-500">
        <span>
          {t('Show', 'Показывать')}
        </span>
        <select
          value={pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
          className="rounded-md border border-gray-800 bg-gray-900 px-2 py-1 text-xs text-gray-200 focus:border-brand-400 focus:outline-hidden"
        >
          {pageSizeOptions.map((opt) => (
            <option key={opt} value={opt}>{opt}</option>
          ))}
        </select>
        {total > 0 && (
          <span>
            {t(`Showing ${from}–${to} of ${total} ${noun}`,
               `Показано ${from}–${to} из ${total} ${noun}`)}
          </span>
        )}
        {total === 0 && (
          <span>{t('No items', 'Нет элементов')}</span>
        )}
      </div>

      {lastPage > 1 && (
        <div className="flex items-center gap-1">
          <PageButton
            disabled={clamped <= 1}
            onClick={() => onPageChange(clamped - 1)}
            ariaLabel={t('Previous page', 'Предыдущая страница')}
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </PageButton>
          {pages.map((p, i) =>
            p == null ? (
              <span key={`gap-${i}`} className="px-1 text-gray-600 text-xs select-none">…</span>
            ) : (
              <PageButton
                key={p}
                active={p === clamped}
                onClick={() => onPageChange(p)}
              >
                {p}
              </PageButton>
            ),
          )}
          <PageButton
            disabled={clamped >= lastPage}
            onClick={() => onPageChange(clamped + 1)}
            ariaLabel={t('Next page', 'Следующая страница')}
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </PageButton>
        </div>
      )}
    </div>
  )
}

function PageButton({
  children, onClick, active, disabled, ariaLabel,
}: {
  children: React.ReactNode
  onClick: () => void
  active?: boolean
  disabled?: boolean
  ariaLabel?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-current={active ? 'page' : undefined}
      className={clsx(
        'min-w-7 rounded-md px-2 py-1 text-xs transition-colors',
        active
          ? 'bg-brand-600 text-white font-medium'
          : 'bg-gray-900 border border-gray-800 text-gray-400 hover:text-gray-200 hover:border-gray-700',
        'disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:text-gray-400 disabled:hover:border-gray-800',
      )}
    >
      {children}
    </button>
  )
}

/**
 * Build the sparse page strip with ellipsis sentinels.
 *
 * Examples (current page in **bold**):
 *   total=3, current=1 → [**1**, 2, 3]
 *   total=10, current=1 → [**1**, 2, 3, null, 10]
 *   total=10, current=5 → [1, null, 4, **5**, 6, null, 10]
 *   total=10, current=10 → [1, null, 8, 9, **10**]
 */
function buildPageStrip(current: number, last: number): Array<number | null> {
  if (last <= 7) {
    return Array.from({ length: last }, (_, i) => i + 1)
  }
  const out: Array<number | null> = [1]
  const start = Math.max(2, current - 1)
  const end = Math.min(last - 1, current + 1)
  if (start > 2) out.push(null)
  for (let p = start; p <= end; p++) out.push(p)
  if (end < last - 1) out.push(null)
  out.push(last)
  return out
}
