import * as React from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, Search } from 'lucide-react'
import { clsx } from 'clsx'
import { useT } from '@/hooks/useT'

export interface SearchableSelectOption {
  value: string
  label: string
  hint?: string
  disabled?: boolean
}

interface Props {
  options: SearchableSelectOption[]
  value: string
  onChange: (value: string) => void
  placeholder?: string
  searchPlaceholder?: string
  footer?: React.ReactNode
  disabled?: boolean
  className?: string
}

/**
 * Dropdown with a filter input for long option lists (node pickers).
 * Renders a button + absolute panel; Escape is stopped so a parent
 * ModalShell does not close together with the list.
 */
export function SearchableSelect({
  options,
  value,
  onChange,
  placeholder,
  searchPlaceholder,
  footer,
  disabled,
  className,
}: Props) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [highlight, setHighlight] = useState(0)
  const rootRef = useRef<HTMLDivElement | null>(null)
  const listRef = useRef<HTMLDivElement | null>(null)

  const selected = options.find((o) => o.value === value)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return options
    return options.filter(
      (o) =>
        o.label.toLowerCase().includes(q) ||
        (o.hint ?? '').toLowerCase().includes(q),
    )
  }, [options, query])

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

  useEffect(() => {
    setHighlight(0)
  }, [query])

  useEffect(() => {
    if (!open || !listRef.current) return
    listRef.current
      .querySelector(`[data-idx="${highlight}"]`)
      ?.scrollIntoView?.({ block: 'nearest' })
  }, [highlight, open])

  const pick = (v: string) => {
    onChange(v)
    setOpen(false)
  }

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) return
    if (e.key === 'Escape') {
      e.stopPropagation()
      setOpen(false)
    } else if (e.key === 'ArrowDown') {
      e.preventDefault()
      setHighlight((h) => Math.min(h + 1, filtered.length - 1))
    } else if (e.key === 'ArrowUp') {
      e.preventDefault()
      setHighlight((h) => Math.max(h - 1, 0))
    } else if (e.key === 'Enter') {
      e.preventDefault()
      const opt = filtered[highlight]
      if (opt && !opt.disabled) pick(opt.value)
    }
  }

  return (
    <div ref={rootRef} className={clsx('relative', className)} onKeyDown={onKeyDown}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          setOpen((v) => !v)
          setQuery('')
        }}
        className="w-full flex items-center justify-between gap-2 rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-left text-gray-100 focus:border-brand-500 focus:outline-hidden disabled:opacity-50"
      >
        <span className={clsx('truncate', !selected && 'text-gray-500')}>
          {selected ? selected.label : (placeholder ?? t('Select…', 'Выберите…'))}
        </span>
        <ChevronDown className={clsx('h-4 w-4 shrink-0 text-gray-500 transition-transform', open && 'rotate-180')} />
      </button>
      {open && (
        <div className="absolute z-40 mt-1 w-full rounded-lg border border-gray-700 bg-gray-900 shadow-lg overflow-hidden">
          <div className="relative border-b border-gray-800">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-gray-500" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={searchPlaceholder ?? t('Search…', 'Поиск…')}
              className="w-full bg-gray-900 pl-8 pr-3 py-2 text-sm text-gray-100 focus:outline-hidden"
            />
          </div>
          <div ref={listRef} className="max-h-60 overflow-y-auto">
            {filtered.length === 0 ? (
              <div className="px-3 py-3 text-sm text-gray-500 text-center">
                {t('Nothing matches', 'Ничего не найдено')}
              </div>
            ) : (
              filtered.map((o, idx) => (
                <button
                  key={o.value || '__empty__'}
                  type="button"
                  data-idx={idx}
                  disabled={o.disabled}
                  onClick={() => pick(o.value)}
                  onMouseEnter={() => setHighlight(idx)}
                  className={clsx(
                    'w-full text-left px-3 py-2 text-sm transition-colors disabled:opacity-50',
                    idx === highlight ? 'bg-gray-800' : '',
                    o.value === value
                      ? 'text-brand-600 dark:text-brand-300'
                      : 'text-gray-200',
                  )}
                >
                  <span className="block truncate">{o.label}</span>
                  {o.hint && (
                    <span className="block truncate text-[11px] text-gray-500">{o.hint}</span>
                  )}
                </button>
              ))
            )}
          </div>
          {footer && (
            <div className="border-t border-gray-800 px-3 py-2">{footer}</div>
          )}
        </div>
      )}
    </div>
  )
}
