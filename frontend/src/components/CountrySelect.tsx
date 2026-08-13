import { useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronDown, Search } from 'lucide-react'
import { clsx } from 'clsx'

import { useT } from '@/hooks/useT'
import { useAppStore } from '@/store'
import { COUNTRY_CODES, countryFlag, countryName } from '@/lib/countries'

/**
 * ISO-3166 country picker, searchable by name or code.
 *
 * The regulatory domain decides which channels and power levels the radio may
 * use, and getting it wrong produces silence rather than an error — hostapd
 * comes up with no usable channels. A two-letter text box asks the operator to
 * recall that "DE" is Germany and, worse, accepts any two letters at all.
 *
 * Names come from the browser's own `Intl.DisplayNames`, so they are localised
 * without shipping a translation table, and flags are derived from the code
 * itself (regional indicator characters). No dependency, no list to maintain.
 */
export function CountrySelect({
  value, onChange, id,
}: {
  value: string
  onChange: (code: string) => void
  id?: string
}) {
  const t = useT()
  const lang = useAppStore((s) => s.lang)
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const boxRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)

  const options = useMemo(() => {
    const all = COUNTRY_CODES.map((code) => ({
      code, name: countryName(code, lang), flag: countryFlag(code),
    }))
    all.sort((a, b) => a.name.localeCompare(b.name, lang))
    const q = query.trim().toLowerCase()
    if (!q) return all
    return all.filter(
      (o) => o.name.toLowerCase().includes(q) || o.code.toLowerCase().startsWith(q),
    )
  }, [lang, query])

  useEffect(() => {
    if (!open) return
    searchRef.current?.focus()
    const onDocClick = (e: MouseEvent) => {
      if (!boxRef.current?.contains(e.target as Node)) setOpen(false)
    }
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onEsc)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onEsc)
    }
  }, [open])

  const pick = (code: string) => {
    onChange(code)
    setOpen(false)
    setQuery('')
  }

  return (
    <div className="relative" ref={boxRef}>
      <button
        id={id}
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 hover:border-gray-700 focus:outline-hidden focus:border-gray-600"
      >
        {value ? (
          <>
            <span aria-hidden>{countryFlag(value)}</span>
            <span className="truncate">{countryName(value, lang)}</span>
            <span className="font-mono text-[11px] text-gray-500">{value}</span>
          </>
        ) : (
          <span className="text-gray-500">
            {t('Select a country…', 'Выберите страну…')}
          </span>
        )}
        <span className="flex-1" />
        <ChevronDown className="h-3.5 w-3.5 shrink-0 text-gray-500" />
      </button>

      {open && (
        <div className="absolute z-30 mt-1 w-full rounded-lg border border-gray-800 bg-gray-950 shadow-lg">
          <div className="flex items-center gap-2 border-b border-gray-800 px-2.5 py-2">
            <Search className="h-3.5 w-3.5 shrink-0 text-gray-500" />
            <input
              ref={searchRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={t('Search by name or code', 'Поиск по названию или коду')}
              className="w-full bg-transparent text-sm text-gray-100 placeholder:text-gray-600 focus:outline-hidden"
            />
          </div>
          <div className="max-h-56 overflow-y-auto py-1">
            {options.length === 0 ? (
              <div className="px-3 py-2 text-xs text-gray-500">
                {t('Nothing matches', 'Ничего не найдено')}
              </div>
            ) : (
              options.map((o) => (
                <button
                  key={o.code}
                  type="button"
                  onClick={() => pick(o.code)}
                  className={clsx(
                    'flex w-full items-center gap-2 px-3 py-1.5 text-left text-sm hover:bg-gray-900',
                    o.code === value ? 'text-brand-300' : 'text-gray-200',
                  )}
                >
                  <span aria-hidden>{o.flag}</span>
                  <span className="truncate">{o.name}</span>
                  <span className="flex-1" />
                  <span className="font-mono text-[11px] text-gray-500">{o.code}</span>
                  {o.code === value && <Check className="h-3.5 w-3.5 shrink-0" />}
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
