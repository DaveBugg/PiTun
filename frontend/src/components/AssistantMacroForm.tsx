import { useState } from 'react'
import { X } from 'lucide-react'

import type { AssistantMacro, AssistantMacroParam } from '@/api/client'
import { useT } from '@/hooks/useT'

/** Human names of the macros (the quick-actions menu and form titles). */
export const MACRO_LABEL: Record<string, { en: string; ru: string }> = {
  route_device: { en: 'Route a device through a node', ru: 'Устройство через ноду' },
  build_circle: { en: 'Circle from a subscription', ru: 'Циркл из подписки' },
  add_site_rule: { en: 'Rule for a site', ru: 'Правило для сайта' },
  deploy_server: { en: 'Install x-ui on a VPS', ru: 'Поставить x-ui на VPS' },
  issue_nodes: { en: 'Issue nodes from x-ui', ru: 'Выпустить ноды с x-ui' },
  diagnose: { en: 'Find out what does not work', ru: 'Найти, что не работает' },
}

const INPUT = 'w-full rounded-md bg-gray-950 border border-gray-800 px-2 py-1 text-xs text-gray-100 focus:border-brand-500 focus:outline-hidden'

function toText(v: unknown): string {
  if (Array.isArray(v)) return v.join(', ')
  if (v === undefined || v === null) return ''
  return String(v)
}

function fromText(p: AssistantMacroParam, raw: string): unknown {
  const v = raw.trim()
  if (p.type === 'array') return v ? v.split(/[,\s]+/).filter(Boolean) : []
  if (p.type === 'integer' || p.type === 'number') {
    if (v === '') return undefined
    const n = Number(v.replace(',', '.'))
    return Number.isFinite(n) ? (p.type === 'integer' ? Math.round(n) : n) : undefined
  }
  return v
}

/**
 * A macro as a form: the fallback when a small model cannot fill the call,
 * and the quick-actions menu. Prefilled with what the model understood.
 */
export function MacroForm({ macro, initial, reason, busy, onSubmit, onClose }: {
  macro: AssistantMacro
  initial: Record<string, unknown>
  reason?: string
  busy: boolean
  onSubmit: (args: Record<string, unknown>) => void
  onClose: () => void
}) {
  const t = useT()
  const props = macro.parameters.properties
  const required = new Set(macro.parameters.required ?? [])
  const [text, setText] = useState<Record<string, string>>(
    () => Object.fromEntries(Object.keys(props).map((k) => [k, toText(initial[k])])))
  const [flags, setFlags] = useState<Record<string, boolean>>(
    () => Object.fromEntries(Object.entries(props).filter(([, p]) => p.type === 'boolean')
      .map(([k]) => [k, initial[k] === true || initial[k] === 'true'])))
  const missing = [...required].filter((k) => !(text[k] ?? '').trim())
  const badNumbers = Object.entries(props).filter(([k, p]) => (p.type === 'integer' || p.type === 'number')
    && (text[k] ?? '').trim() !== '' && !Number.isFinite(Number((text[k] ?? '').trim().replace(',', '.')))).map(([k]) => k)
  const label = MACRO_LABEL[macro.name] ? t(MACRO_LABEL[macro.name].en, MACRO_LABEL[macro.name].ru) : macro.name

  const submit = () => {
    const args: Record<string, unknown> = {}
    for (const [k, p] of Object.entries(props)) {
      if (p.type === 'boolean') { if (flags[k]) args[k] = true; continue }
      const v = fromText(p, text[k] ?? '')
      if (v !== undefined && v !== '' && !(Array.isArray(v) && v.length === 0)) args[k] = v
    }
    onSubmit(args)
  }

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); if (!missing.length && !badNumbers.length) submit() }}
      className="rounded-lg border border-brand-800 bg-brand-900/20 p-3 text-xs space-y-2"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="text-sm font-medium text-gray-100">{label}</div>
          {reason && <div className="text-[11px] text-gray-500">{t('Fill in what the model could not:', 'Заполните то, что модель не смогла:')} {reason}</div>}
        </div>
        <button type="button" onClick={onClose} aria-label={t('Close', 'Закрыть')} className="text-gray-500 hover:text-gray-300">
          <X className="h-4 w-4" />
        </button>
      </div>
      {Object.entries(props).map(([k, p]) => (
        <label key={k} className="block space-y-0.5">
          <span className="text-gray-400">{k.replace(/_/g, ' ')}{required.has(k) && <span className="text-red-400"> *</span>}</span>
          {p.type === 'boolean' ? (
            <input type="checkbox" checked={!!flags[k]} onChange={(e) => setFlags({ ...flags, [k]: e.target.checked })}
                   className="ml-2 align-middle" />
          ) : p.enum ? (
            <select value={text[k] ?? ''} onChange={(e) => setText({ ...text, [k]: e.target.value })} className={INPUT}>
              <option value="">—</option>
              {p.enum.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
          ) : (
            <input value={text[k] ?? ''} onChange={(e) => setText({ ...text, [k]: e.target.value })}
                   inputMode={p.type === 'integer' || p.type === 'number' ? 'numeric' : undefined}
                   placeholder={p.description ?? ''} className={INPUT} />
          )}
        </label>
      ))}
      {badNumbers.length > 0 && (
        <div className="text-[11px] text-red-400">{t('Not a number:', 'Не число:')} {badNumbers.join(', ')}</div>
      )}
      <button type="submit" disabled={busy || missing.length > 0 || badNumbers.length > 0}
              className="rounded-md bg-brand-600 px-3 py-1.5 text-xs text-white hover:bg-brand-500 disabled:opacity-50">
        {macro.name === 'diagnose' ? t('Check', 'Проверить') : t('Prepare the plan', 'Подготовить план')}
      </button>
    </form>
  )
}
