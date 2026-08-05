import { useRef, useState } from 'react'
import { FileUp, Loader2, AlertCircle, AlertTriangle, Globe2, Tag, Plus, CheckCircle2 } from 'lucide-react'
import { clsx } from 'clsx'
import { isAxiosError } from 'axios'
import { useMutation } from '@tanstack/react-query'

import { ModalShell } from '@/components/ModalShell'
import { useT } from '@/hooks/useT'
import { routingApi } from '@/api/client'
import type {
  RoutingSet, RoutingPortRule, RoutingImportDestination,
  RoutingImportPreviewResult, RoutingImportResolution,
} from '@/types'

/**
 * Set-aware routing import (v1.4.3).
 *
 * Reads a native PiTun export envelope (or a legacy V2Ray JSON array /
 * raw rule array — auto-detected), flattens it to rules, lets the operator
 * pick a destination (Global / existing set / new set), then runs a
 * server-side PREVIEW to surface duplicates, in-file collisions, unusable
 * rules, and ACTION CONFLICTS (same match, different action). Conflicts are
 * resolved per-row (keep existing vs use imported) before commit so the
 * resulting ruleset stays unambiguous for xray.
 */

type Parsed = { rules: RoutingPortRule[]; note: string }

const RULE_TYPES = new Set(['mac', 'src_ip', 'dst_ip', 'domain', 'port', 'protocol', 'geoip', 'geosite'])

function flattenNative(env: any): RoutingPortRule[] {
  const out: RoutingPortRule[] = []
  for (const sc of env.scopes ?? []) {
    for (const r of sc.rules ?? []) out.push(coerceRule(r))
  }
  return out
}

function coerceRule(r: any): RoutingPortRule {
  return {
    name: String(r.name ?? ''),
    enabled: r.enabled !== false,
    rule_type: r.rule_type,
    match_value: String(r.match_value ?? ''),
    action: String(r.action ?? ''),
    order: Number(r.order ?? 100),
  }
}

/** Best-effort conversion of a legacy V2Ray JSON array (the old export). */
function convertV2Ray(arr: any[]): RoutingPortRule[] {
  const out: RoutingPortRule[] = []
  for (const e of arr) {
    const name = String(e.remarks ?? '')
    const enabled = e.enabled !== false
    let action = ''
    if (e.balancerTag) action = `balancer:${String(e.balancerTag).replace(/^balancer-/, '')}`
    else if (typeof e.outboundTag === 'string') {
      action = e.outboundTag.startsWith('node-') ? `node:${e.outboundTag.slice(5)}` : e.outboundTag
    }
    if (!action) continue
    const push = (rule_type: string, match_value: string) => {
      if (match_value && RULE_TYPES.has(rule_type)) {
        out.push({ name, enabled, rule_type: rule_type as RoutingPortRule['rule_type'], match_value, action, order: 100 })
      }
    }
    if (Array.isArray(e.domain)) {
      const geos: string[] = [], doms: string[] = []
      for (const d of e.domain) (String(d).startsWith('geosite:') ? geos : doms).push(String(d).replace(/^geosite:/, ''))
      push('domain', doms.join(',')); push('geosite', geos.join(','))
    }
    if (Array.isArray(e.ip)) {
      const geo: string[] = [], ips: string[] = []
      for (const x of e.ip) (String(x).startsWith('geoip:') ? geo : ips).push(String(x).replace(/^geoip:/, ''))
      push('dst_ip', ips.join(',')); push('geoip', geo.join(','))
    }
    if (Array.isArray(e.source)) push('src_ip', e.source.join(','))
    if (typeof e.port === 'string') push('port', e.port)
    else if (typeof e.port === 'number') push('port', String(e.port))
    if (Array.isArray(e.protocol)) push('protocol', e.protocol.join(','))
    if (e.attrs && typeof e.attrs.mac === 'string') push('mac', e.attrs.mac)
  }
  return out
}

function parseFile(text: string, t: (en: string, ru: string) => string): Parsed {
  let json: any
  try { json = JSON.parse(text) } catch { throw new Error(t('Not valid JSON.', 'Невалидный JSON.')) }

  if (json && typeof json === 'object' && !Array.isArray(json) && json.format === 'pitun-routing') {
    const rules = flattenNative(json).filter(r => RULE_TYPES.has(r.rule_type) && r.match_value && r.action)
    return { rules, note: t('PiTun native format', 'Нативный формат PiTun') }
  }
  if (Array.isArray(json)) {
    // Raw RoutingPortRule[] (native rule array) vs V2Ray entries.
    const looksNative = json.length > 0 && json.every(x => x && x.rule_type && 'match_value' in x && 'action' in x)
    if (looksNative) {
      const rules = json.map(coerceRule).filter(r => RULE_TYPES.has(r.rule_type) && r.match_value && r.action)
      return { rules, note: t('Rule array', 'Массив правил') }
    }
    return { rules: convertV2Ray(json), note: t('Legacy V2Ray format (converted)', 'Старый формат V2Ray (сконвертирован)') }
  }
  throw new Error(t('Unrecognized file format.', 'Неизвестный формат файла.'))
}

export function RoutingImportModal({
  routingSets,
  onClose,
  onImported,
}: {
  routingSets: RoutingSet[]
  onClose: () => void
  onImported: () => void
}) {
  const t = useT()
  const fileRef = useRef<HTMLInputElement>(null)

  const [parsed, setParsed] = useState<Parsed | null>(null)
  const [fileName, setFileName] = useState('')
  const [error, setError] = useState<string | null>(null)

  const [destKind, setDestKind] = useState<'global' | 'set'>('global')
  const [setMode, setSetMode] = useState<'existing' | 'new'>(routingSets.length > 0 ? 'existing' : 'new')
  const [setId, setSetId] = useState<number | null>(routingSets[0]?.id ?? null)
  const [newName, setNewName] = useState('')
  const [newDesc, setNewDesc] = useState('')

  const [preview, setPreview] = useState<RoutingImportPreviewResult | null>(null)
  const [resolutions, setResolutions] = useState<Record<string, 'keep' | 'replace'>>({})
  const [done, setDone] = useState<string | null>(null)

  // Any destination change invalidates a computed preview.
  const resetPreview = () => { setPreview(null); setResolutions({}); setDone(null) }

  const onFile = async (f: File) => {
    setError(null); resetPreview(); setFileName(f.name)
    try {
      const p = parseFile(await f.text(), t)
      if (p.rules.length === 0) throw new Error(t('No usable rules found in the file.', 'В файле нет пригодных правил.'))
      setParsed(p)
    } catch (e) {
      setParsed(null)
      setError(e instanceof Error ? e.message : t('Failed to read file.', 'Не удалось прочитать файл.'))
    }
  }

  const buildDest = (): RoutingImportDestination => {
    if (destKind === 'global') return { kind: 'global' }
    if (setMode === 'existing') return { kind: 'set', set_id: setId }
    return { kind: 'set', new_set_name: newName.trim(), new_set_description: newDesc.trim() || null }
  }

  const destValid = destKind === 'global'
    || (setMode === 'existing' && setId != null)
    || (setMode === 'new' && newName.trim().length > 0)

  const errMsg = (e: unknown) => {
    if (isAxiosError(e)) { const d = e.response?.data?.detail; if (typeof d === 'string') return d }
    return e instanceof Error ? e.message : t('Request failed', 'Ошибка запроса')
  }

  const previewMut = useMutation({
    mutationFn: () => routingApi.importPreview({ rules: parsed!.rules, destination: buildDest() }),
    onMutate: () => setError(null),
    onSuccess: (res) => {
      setPreview(res)
      const init: Record<string, 'keep' | 'replace'> = {}
      for (const c of res.conflicts) init[c.key] = 'keep'
      setResolutions(init)
    },
    onError: (e) => setError(errMsg(e)),
  })

  const commitMut = useMutation({
    mutationFn: () => routingApi.importCommit({
      rules: parsed!.rules,
      destination: buildDest(),
      resolutions: Object.entries(resolutions).map(([key, choice]) => ({ key, choice })) as RoutingImportResolution[],
      default_conflict_choice: 'keep',
    }),
    onMutate: () => setError(null),
    onSuccess: (res) => {
      onImported()
      setDone(t(
        `Imported into ${res.set_name ?? 'Global'}: +${res.added} added, ${res.replaced} replaced, ${res.skipped_identical} identical skipped, ${res.skipped_conflicts} conflicts skipped, ${res.invalid_skipped} unusable.`,
        `Импортировано в ${res.set_name ?? 'Глобальные'}: +${res.added} добавлено, ${res.replaced} заменено, ${res.skipped_identical} идентичных пропущено, ${res.skipped_conflicts} конфликтов пропущено, ${res.invalid_skipped} непригодных.`,
      ))
    },
    onError: (e) => setError(errMsg(e)),
  })

  const titleId = 'routing-import-modal-title'
  const conflicts = preview?.conflicts ?? []

  return (
    <ModalShell onClose={onClose} labelledBy={titleId}>
      <div className="w-full max-w-lg rounded-2xl bg-gray-950 border border-gray-800 shadow-xl max-h-[88vh] overflow-y-auto">
        <div className="p-6 space-y-4">
          <div className="flex items-center gap-2">
            <FileUp className="h-5 w-5 text-purple-600 dark:text-purple-400" />
            <h2 id={titleId} className="text-lg font-semibold text-white">
              {t('Import routing rules', 'Импорт правил роутинга')}
            </h2>
          </div>

          {/* 1. File */}
          <div>
            <input
              ref={fileRef} type="file" accept="application/json,.json" className="hidden"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) onFile(f); e.currentTarget.value = '' }}
            />
            <button
              type="button"
              onClick={() => fileRef.current?.click()}
              className="w-full rounded-lg border border-dashed border-gray-700 bg-gray-900/40 hover:border-purple-600/60 px-3 py-3 text-sm text-gray-300 inline-flex items-center justify-center gap-2"
            >
              <FileUp className="h-4 w-4" />
              {fileName ? t('Choose a different file', 'Выбрать другой файл') : t('Choose export file', 'Выбрать файл экспорта')}
            </button>
            {parsed && (
              <p className="mt-1.5 text-xs text-gray-500">
                <span className="text-gray-300">{fileName}</span> — {parsed.rules.length} {t('rules', 'правил')} · {parsed.note}
              </p>
            )}
          </div>

          {/* 2. Destination */}
          {parsed && (
            <div className="space-y-2">
              <div className="text-xs uppercase tracking-wider text-gray-500">{t('Import into', 'Импортировать в')}</div>

              <label className={clsx('flex items-center gap-2.5 rounded-lg border px-3 py-2 cursor-pointer',
                destKind === 'global' ? 'border-purple-600/60 bg-purple-50 dark:bg-purple-900/15' : 'border-gray-800 bg-gray-900/40')}>
                <input type="radio" checked={destKind === 'global'} onChange={() => { setDestKind('global'); resetPreview() }} className="accent-purple-500" />
                <Globe2 className="h-4 w-4 text-gray-400" />
                <span className="text-sm text-gray-200">{t('Global (main rules)', 'Глобальные (основные)')}</span>
              </label>

              <label className={clsx('flex items-center gap-2.5 rounded-lg border px-3 py-2 cursor-pointer',
                destKind === 'set' ? 'border-purple-600/60 bg-purple-50 dark:bg-purple-900/15' : 'border-gray-800 bg-gray-900/40')}>
                <input type="radio" checked={destKind === 'set'} onChange={() => { setDestKind('set'); resetPreview() }} className="accent-purple-500" />
                <Tag className="h-4 w-4 text-purple-700 dark:text-purple-300" />
                <span className="text-sm text-gray-200">{t('A routing set', 'В набор (set)')}</span>
              </label>

              {destKind === 'set' && (
                <div className="ml-7 space-y-2">
                  <div className="flex gap-2 text-xs">
                    <button type="button" onClick={() => { setSetMode('existing'); resetPreview() }}
                      disabled={routingSets.length === 0}
                      className={clsx('px-2 py-1 rounded-md border', setMode === 'existing' ? 'border-purple-600 text-purple-700 dark:text-purple-300' : 'border-gray-700 text-gray-400', 'disabled:opacity-40')}>
                      {t('Existing', 'Существующий')}
                    </button>
                    <button type="button" onClick={() => { setSetMode('new'); resetPreview() }}
                      className={clsx('px-2 py-1 rounded-md border inline-flex items-center gap-1', setMode === 'new' ? 'border-purple-600 text-purple-700 dark:text-purple-300' : 'border-gray-700 text-gray-400')}>
                      <Plus className="h-3 w-3" /> {t('New set', 'Новый сет')}
                    </button>
                  </div>

                  {setMode === 'existing' ? (
                    <select
                      value={setId ?? ''}
                      onChange={(e) => { setSetId(Number(e.target.value) || null); resetPreview() }}
                      className="w-full rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-white"
                    >
                      {routingSets.length === 0 && <option value="">{t('No sets yet', 'Сетов пока нет')}</option>}
                      {routingSets.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                    </select>
                  ) : (
                    <div className="space-y-2">
                      <input
                        type="text" value={newName} maxLength={64}
                        onChange={(e) => { setNewName(e.target.value); resetPreview() }}
                        placeholder={t('New set name', 'Имя нового сета')}
                        className="w-full rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-white"
                      />
                      <input
                        type="text" value={newDesc}
                        onChange={(e) => setNewDesc(e.target.value)}
                        placeholder={t('Description (optional)', 'Описание (необязательно)')}
                        className="w-full rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 text-sm text-white"
                      />
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* 3. Preview summary */}
          {preview && (
            <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3 text-xs space-y-1">
              <div className="text-gray-300 font-medium">
                {t('Preview', 'Предпросмотр')} → <span className="text-purple-700 dark:text-purple-300">{preview.destination_label}</span>
                {!preview.destination_exists && <span className="text-gray-500"> ({t('will be created', 'будет создан')})</span>}
              </div>
              <div className="text-gray-400">
                <span className="text-emerald-600 dark:text-emerald-400">+{preview.will_add}</span> {t('new', 'новых')} ·{' '}
                {preview.identical_skipped} {t('identical', 'идентичных')} ·{' '}
                {preview.collapsed_duplicates} {t('file-dupes', 'дублей в файле')} ·{' '}
                {preview.invalid_skipped} {t('unusable', 'непригодных')} ·{' '}
                <span className={conflicts.length ? 'text-amber-600 dark:text-amber-400' : ''}>{conflicts.length} {t('conflicts', 'конфликтов')}</span>
              </div>
              {preview.invalid_skipped > 0 && (
                <div className="text-gray-500">
                  {t('Unusable = action points at a node/balancer or geo tag missing on this box.',
                     'Непригодные = action ссылается на отсутствующий узел/балансер или geo-тег на этой машине.')}
                </div>
              )}
            </div>
          )}

          {/* 3b. Conflicts to resolve */}
          {conflicts.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-300">
                <AlertTriangle className="h-4 w-4" />
                {t('Resolve conflicts (same match, different action)', 'Разреши конфликты (тот же match, другой action)')}
              </div>
              <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1">
                {conflicts.map(c => {
                  const choice = resolutions[c.key] ?? 'keep'
                  return (
                    <div key={c.key} className="rounded-lg border border-amber-200 dark:border-amber-800/40 bg-amber-50 dark:bg-amber-900/10 p-2">
                      <div className="text-xs text-gray-300 font-mono truncate">
                        {c.rule_type} = {c.match_value}
                      </div>
                      <div className="text-[11px] text-gray-500 mb-1.5">
                        {t('existing', 'было')}: <span className="text-gray-300">{c.existing_action}</span>
                        {'  ·  '}{t('imported', 'импорт')}: <span className="text-gray-300">{c.incoming_action}</span>
                      </div>
                      <div className="flex gap-1.5">
                        <button type="button" onClick={() => setResolutions(p => ({ ...p, [c.key]: 'keep' }))}
                          className={clsx('px-2 py-0.5 rounded-md text-[11px] border', choice === 'keep' ? 'border-purple-600 text-purple-700 dark:text-purple-300 bg-purple-50 dark:bg-purple-900/20' : 'border-gray-700 text-gray-400')}>
                          {t('Keep existing', 'Оставить')} ({c.existing_action})
                        </button>
                        <button type="button" onClick={() => setResolutions(p => ({ ...p, [c.key]: 'replace' }))}
                          className={clsx('px-2 py-0.5 rounded-md text-[11px] border', choice === 'replace' ? 'border-purple-600 text-purple-700 dark:text-purple-300 bg-purple-50 dark:bg-purple-900/20' : 'border-gray-700 text-gray-400')}>
                          {t('Use imported', 'Импорт')} ({c.incoming_action})
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {error && (
            <div className="flex items-start gap-2 rounded-lg bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700/50 p-3 text-sm text-red-700 dark:text-red-300">
              <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}
          {done && (
            <div className="flex items-start gap-2 rounded-lg bg-emerald-50 dark:bg-emerald-900/25 border border-emerald-200 dark:border-emerald-700/50 p-3 text-sm text-emerald-800 dark:text-emerald-200">
              <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0" />
              <span>{done}</span>
            </div>
          )}

          {/* Actions */}
          <div className="flex justify-end gap-2 pt-2 border-t border-gray-800">
            <button type="button" onClick={onClose}
              className="px-4 py-2 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm">
              {done ? t('Close', 'Закрыть') : t('Cancel', 'Отмена')}
            </button>
            {!done && (preview ? (
              <button
                type="button"
                onClick={() => commitMut.mutate()}
                disabled={commitMut.isPending}
                className="px-4 py-2 rounded-lg bg-purple-600 hover:bg-purple-500 text-white text-sm disabled:opacity-50 inline-flex items-center gap-2"
              >
                {commitMut.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {t('Import', 'Импортировать')}
              </button>
            ) : (
              <button
                type="button"
                onClick={() => previewMut.mutate()}
                disabled={!parsed || !destValid || previewMut.isPending}
                className="px-4 py-2 rounded-lg bg-purple-600 hover:bg-purple-500 text-white text-sm disabled:opacity-50 inline-flex items-center gap-2"
              >
                {previewMut.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                {t('Analyze', 'Проверить')}
              </button>
            ))}
          </div>
        </div>
      </div>
    </ModalShell>
  )
}
