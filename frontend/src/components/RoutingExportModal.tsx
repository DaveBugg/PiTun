import { useMemo, useState } from 'react'
import { FileDown, Layers, Globe2, Tag } from 'lucide-react'
import { clsx } from 'clsx'

import { ModalShell } from '@/components/ModalShell'
import { useT } from '@/hooks/useT'
import type {
  RoutingRule, RoutingSet, RoutingPortRule, RoutingExportScope, RoutingExportEnvelope,
} from '@/types'

/**
 * Set-aware routing export (v1.4.3).
 *
 * Pick which SCOPES to export — Global and/or each routing set — then
 * download them either merged into one file or as separate per-scope
 * files. Output is the native PiTun envelope (preserves every rule field
 * incl. mac/geosite/node:/balancer:, which the legacy V2Ray export drops).
 * Re-imported via the Import dialog, which picks a destination per file.
 */
function toPortRule(r: RoutingRule): RoutingPortRule {
  return {
    name: r.name,
    enabled: r.enabled,
    rule_type: r.rule_type,
    match_value: r.match_value,
    action: r.action,
    order: r.order,
  }
}

function download(envelope: RoutingExportEnvelope, filename: string) {
  const blob = new Blob([JSON.stringify(envelope, null, 2)], { type: 'application/json' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

function slug(s: string): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'set'
}

export function RoutingExportModal({
  rules,
  routingSets,
  onClose,
}: {
  rules: RoutingRule[]
  routingSets: RoutingSet[]
  onClose: () => void
}) {
  const t = useT()

  // Build the selectable scopes: Global first, then each set (in display order).
  const scopes = useMemo(() => {
    const sorted = [...routingSets].sort((a, b) => a.order - b.order || a.id - b.id)
    const out: { id: string; label: string; isGlobal: boolean; set?: RoutingSet; rules: RoutingRule[] }[] = [
      { id: 'global', label: t('Global', 'Глобальные'), isGlobal: true, rules: rules.filter(r => r.routing_set_id === null) },
    ]
    for (const s of sorted) {
      out.push({ id: `set:${s.id}`, label: s.name, isGlobal: false, set: s, rules: rules.filter(r => r.routing_set_id === s.id) })
    }
    return out
  }, [rules, routingSets, t])

  // Default: select every scope that actually has rules.
  const [selected, setSelected] = useState<Set<string>>(
    () => new Set(scopes.filter(s => s.rules.length > 0).map(s => s.id)),
  )

  const toggle = (id: string) =>
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  const chosen = scopes.filter(s => selected.has(s.id))
  const totalRules = chosen.reduce((n, s) => n + s.rules.length, 0)
  const date = new Date().toISOString().slice(0, 10)

  const scopeToEnvScope = (s: typeof scopes[number]): RoutingExportScope =>
    s.isGlobal
      ? { kind: 'global', rules: s.rules.map(toPortRule) }
      : { kind: 'set', set_name: s.set!.name, set_description: s.set!.description ?? null, rules: s.rules.map(toPortRule) }

  const exportMerged = () => {
    const envelope: RoutingExportEnvelope = {
      format: 'pitun-routing', version: 1, exported_at: new Date().toISOString(),
      scopes: chosen.map(scopeToEnvScope),
    }
    download(envelope, `pitun-routing-${date}.json`)
    onClose()
  }

  const exportSeparate = () => {
    for (const s of chosen) {
      const envelope: RoutingExportEnvelope = {
        format: 'pitun-routing', version: 1, exported_at: new Date().toISOString(),
        scopes: [scopeToEnvScope(s)],
      }
      const tag = s.isGlobal ? 'global' : slug(s.set!.name)
      download(envelope, `pitun-routing-${tag}-${date}.json`)
    }
    onClose()
  }

  const titleId = 'routing-export-modal-title'

  return (
    <ModalShell onClose={onClose} labelledBy={titleId}>
      <div className="w-full max-w-md rounded-2xl bg-gray-950 border border-gray-800 shadow-xl">
        <div className="p-6 space-y-4">
          <div className="flex items-center gap-2">
            <FileDown className="h-5 w-5 text-purple-600 dark:text-purple-400" />
            <h2 id={titleId} className="text-lg font-semibold text-white">
              {t('Export routing rules', 'Экспорт правил роутинга')}
            </h2>
          </div>

          <p className="text-[13px] text-gray-400">
            {t(
              'Pick which scopes to export. Native PiTun format — preserves every rule field and the set names.',
              'Выбери области для экспорта. Нативный формат PiTun — сохраняет все поля правил и имена сетов.',
            )}
          </p>

          <div className="space-y-1.5 max-h-72 overflow-y-auto pr-1">
            {scopes.map(s => {
              const isSel = selected.has(s.id)
              const empty = s.rules.length === 0
              return (
                <label
                  key={s.id}
                  className={clsx(
                    'flex items-center gap-3 rounded-lg border px-3 py-2 cursor-pointer transition-colors',
                    isSel ? 'border-purple-600/60 bg-purple-50 dark:bg-purple-900/15' : 'border-gray-800 bg-gray-900/40 hover:border-gray-700',
                    empty && 'opacity-60',
                  )}
                >
                  <input
                    type="checkbox"
                    checked={isSel}
                    onChange={() => toggle(s.id)}
                    className="h-4 w-4 accent-purple-500"
                  />
                  {s.isGlobal
                    ? <Globe2 className="h-4 w-4 text-gray-400 shrink-0" />
                    : <Tag className="h-4 w-4 text-purple-700 dark:text-purple-300 shrink-0" />}
                  <span className="text-sm text-gray-200 truncate flex-1">{s.label}</span>
                  <span className="text-xs text-gray-500 shrink-0">
                    {s.rules.length} {t('rules', 'правил')}
                  </span>
                </label>
              )
            })}
          </div>

          <div className="flex items-center justify-between text-xs text-gray-500">
            <button
              type="button"
              onClick={() => setSelected(new Set(scopes.map(s => s.id)))}
              className="hover:text-gray-300"
            >
              {t('Select all', 'Выбрать все')}
            </button>
            <span>{t('Selected', 'Выбрано')}: {chosen.length} {t('scopes', 'обл.')} · {totalRules} {t('rules', 'правил')}</span>
            <button
              type="button"
              onClick={() => setSelected(new Set())}
              className="hover:text-gray-300"
            >
              {t('Clear', 'Снять')}
            </button>
          </div>

          <div className="flex flex-col gap-2 pt-2 border-t border-gray-800">
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm"
              >
                {t('Cancel', 'Отмена')}
              </button>
              <button
                type="button"
                onClick={exportSeparate}
                disabled={chosen.length === 0}
                title={t('One file per selected scope', 'По одному файлу на область')}
                className="px-3 py-2 rounded-lg border border-gray-700 bg-gray-800 hover:bg-gray-700 text-gray-200 text-sm disabled:opacity-50 inline-flex items-center gap-1.5"
              >
                <Layers className="h-4 w-4" />
                {t('Separate files', 'По отдельности')}
              </button>
              <button
                type="button"
                onClick={exportMerged}
                disabled={chosen.length === 0}
                title={t('All selected scopes in one file', 'Все области в одном файле')}
                className="px-3 py-2 rounded-lg bg-purple-600 hover:bg-purple-500 text-white text-sm disabled:opacity-50 inline-flex items-center gap-1.5"
              >
                <FileDown className="h-4 w-4" />
                {t('One file', 'Одним файлом')}
              </button>
            </div>
          </div>
        </div>
      </div>
    </ModalShell>
  )
}
