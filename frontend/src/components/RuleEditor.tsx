import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { balancersApi, http } from '@/api/client'
import { useT } from '@/hooks/useT'
import type { RoutingRule, RoutingRuleCreate, RuleType } from '@/types'

interface GeoCategories {
  geosite: string[]
  geoip: string[]
}

const RULE_TYPES: RuleType[] = ['mac', 'src_ip', 'dst_ip', 'domain', 'port', 'protocol', 'geoip', 'geosite']
const ACTIONS = ['proxy', 'direct', 'block']

const RULE_TYPE_LABELS: Record<RuleType, string> = {
  mac: 'MAC Address',
  src_ip: 'Source IP/CIDR',
  dst_ip: 'Destination IP/CIDR',
  domain: 'Domain / Keyword',
  port: 'Port Range',
  protocol: 'Protocol',
  geoip: 'GeoIP',
  geosite: 'GeoSite',
}

const RULE_TYPE_LABELS_RU: Record<RuleType, string> = {
  mac: 'MAC-адрес',
  src_ip: 'IP/CIDR источника',
  dst_ip: 'IP/CIDR назначения',
  domain: 'Домен / Ключевое слово',
  port: 'Диапазон портов',
  protocol: 'Протокол',
  geoip: 'GeoIP',
  geosite: 'GeoSite',
}

const RULE_TYPE_HINTS: Record<RuleType, string> = {
  mac: 'aa:bb:cc:dd:ee:ff, ...',
  src_ip: '192.168.1.10, 192.168.2.0/24',
  dst_ip: '1.2.3.4/8, 5.6.7.8',
  domain: 'example.com, keyword:google',
  port: '80, 443, 8000-9000',
  protocol: 'tcp, udp',
  geoip: 'CN, US, RU (country codes)',
  geosite: 'google, youtube, netflix',
}

interface Props {
  initial?: Partial<RoutingRule>
  nodeOptions?: { id: number; name: string }[]
  onSave: (data: RoutingRuleCreate) => void
  onCancel: () => void
  loading?: boolean
}

export function RuleEditor({ initial, nodeOptions = [], onSave, onCancel, loading }: Props) {
  const t = useT()
  const { data: balancerGroups = [] } = useQuery({
    queryKey: ['balancers'],
    queryFn: () => balancersApi.list(),
  })

  // Available geosite/geoip tags from the loaded `.dat` (since v1.2.7).
  // `staleTime: Infinity` because tags only change after a Geo update,
  // which is a manual user action — no need to refetch on focus or
  // poll. The endpoint returns empty arrays if the cache isn't ready,
  // and we treat that as "autocomplete unavailable" rather than
  // distinguish from genuine empty .dat (rare).
  const { data: geoCategories } = useQuery<GeoCategories>({
    queryKey: ['geo', 'categories'],
    queryFn: async () => {
      const r = await http.get<GeoCategories>('/geodata/categories')
      return r.data
    },
    staleTime: Infinity,
  })

  const initialAction = initial?.action ?? 'proxy'
  // Parse `node:<id>` / `balancer:<id>` defensively. `split(':')[1]` alone
  // returns '' for malformed `'node:'` (render-safe but the form silently
  // fails on submit) and picks up garbage for `'node:abc'`. Extract only a
  // positive integer; anything else → empty so the user sees no preselected
  // node/balancer and picks one explicitly.
  const parseRefId = (action: string, prefix: 'node' | 'balancer'): string => {
    const m = new RegExp(`^${prefix}:(\\d+)$`).exec(action)
    return m ? m[1] : ''
  }
  const initialCustomNode = parseRefId(initialAction, 'node')
  const initialCustomBalancer = parseRefId(initialAction, 'balancer')

  const [form, setForm] = useState({
    name: initial?.name ?? '',
    enabled: initial?.enabled ?? true,
    rule_type: initial?.rule_type ?? ('dst_ip' as RuleType),
    match_value: initial?.match_value ?? '',
    action: initialAction as string,
    order: initial?.order ?? 100,
    customNode: initialCustomNode,
    customBalancer: initialCustomBalancer,
  })

  const set = <K extends keyof typeof form>(k: K, v: typeof form[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  // `match_value` is the canonical form (comma-joined, no whitespace)
  // and is what we ship to the API. The textarea stores its own
  // "raw" state because normalising on every keystroke deletes the
  // trailing separator the operator just typed (`abc,` becomes
  // `abc` before they can type the second tag — no commas survive,
  // no newlines either). Canonicalisation happens at submit and
  // when GeoTagPicker appends a tag.
  const canonicalize = (raw: string): string =>
    raw
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .join(',')
  const toDisplay = (canonical: string): string =>
    canonical
      ? canonical.split(',').map((s) => s.trim()).filter(Boolean).join(',\n')
      : ''
  const [matchValueRaw, setMatchValueRaw] = useState<string>(
    () => toDisplay(initial?.match_value ?? ''),
  )

  const isNodeAction = form.action.startsWith('node:') || form.action === '_node'
  const isBalancerAction = form.action.startsWith('balancer:') || form.action === '_balancer'

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    let action = form.action
    if (isNodeAction) {
      if (!form.customNode) return // prevent submitting sentinel
      action = `node:${form.customNode}`
    } else if (isBalancerAction) {
      if (!form.customBalancer) return // prevent submitting sentinel
      action = `balancer:${form.customBalancer}`
    }
    onSave({
      name: form.name,
      enabled: form.enabled,
      rule_type: form.rule_type,
      // Canonicalise here — the textarea is the source of truth
      // while the dialog is open, but the API expects a flat
      // comma-joined string without trailing separators / spaces.
      match_value: canonicalize(matchValueRaw),
      action: action as RoutingRuleCreate['action'],
      order: form.order,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">{t('Name', 'Название')}</label>
          <input
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
            required
            autoFocus
            className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
            placeholder={t('Block China IPs', 'Блокировать IP Китая')}
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">{t('Priority (lower = first)', 'Приоритет (меньше = раньше)')}</label>
          <input
            type="number"
            value={form.order}
            onChange={(e) => set('order', Number(e.target.value))}
            className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
          />
        </div>
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1">{t('Rule Type', 'Тип правила')}</label>
        <select
          value={form.rule_type}
          onChange={(e) => set('rule_type', e.target.value as RuleType)}
          className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
        >
          {RULE_TYPES.map((rt) => (
            <option key={rt} value={rt}>{t(RULE_TYPE_LABELS[rt], RULE_TYPE_LABELS_RU[rt])}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1">
          {t('Match Value', 'Значение')}
          <span className="ml-2 font-normal text-gray-600">({RULE_TYPE_HINTS[form.rule_type]})</span>
        </label>
        <textarea
          value={matchValueRaw}
          onChange={(e) => setMatchValueRaw(e.target.value)}
          required
          rows={8}
          // `<datalist>` autocomplete only works on `<input>`, not
          // `<textarea>`. Comma-separated multi-tag entry is a textarea
          // here, so we keep the textarea for editing but show a
          // browse-the-tag-list helper underneath when the rule_type
          // is one we know how to autocomplete.
          //
          // `resize-y` lets the operator drag for taller views when
          // editing big geosite groups (50+ tags). Default 8 rows
          // already covers most cases without the dialog growing
          // beyond viewport.
          className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-hidden resize-y"
          placeholder={RULE_TYPE_HINTS[form.rule_type]}
        />
        {(form.rule_type === 'geosite' || form.rule_type === 'geoip') && geoCategories && (
          <GeoTagPicker
            available={form.rule_type === 'geosite' ? geoCategories.geosite : geoCategories.geoip}
            currentValue={canonicalize(matchValueRaw)}
            onAppend={(tag) => {
              // Append to the raw display state too — the textarea is
              // the source of truth while the dialog is open, the
              // form field is only computed at submit.
              setMatchValueRaw((cur) => {
                const trimmed = cur.replace(/[\s,]+$/, '')
                return trimmed ? `${trimmed},\n${tag}` : tag
              })
            }}
          />
        )}
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1">{t('Action', 'Действие')}</label>
        <select
          value={isNodeAction ? '_node' : isBalancerAction ? '_balancer' : form.action}
          onChange={(e) => {
            if (e.target.value === '_node') {
              set('action', '_node')
            } else if (e.target.value === '_balancer') {
              set('action', '_balancer')
            } else {
              set('action', e.target.value)
            }
          }}
          className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
        >
          {ACTIONS.map((a) => (
            <option key={a} value={a}>{a.charAt(0).toUpperCase() + a.slice(1)}</option>
          ))}
          {nodeOptions.length > 0 && (
            <option value="_node">{t('Route to specific node…', 'Направить на конкретную ноду…')}</option>
          )}
          {balancerGroups.length > 0 && (
            <option value="_balancer">{t('Route via balancer group…', 'Через группу балансировки…')}</option>
          )}
        </select>
      </div>

      {isNodeAction && nodeOptions.length > 0 && (
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">{t('Target Node', 'Целевая нода')}</label>
          <select
            value={form.customNode}
            onChange={(e) => set('customNode', e.target.value)}
            className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
          >
            <option value="">{t('Select node…', 'Выберите ноду…')}</option>
            {nodeOptions.map((n) => (
              <option key={n.id} value={n.id}>{n.name}</option>
            ))}
          </select>
        </div>
      )}

      {isBalancerAction && balancerGroups.length > 0 && (
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">{t('Target Balancer Group', 'Целевая группа балансировки')}</label>
          <select
            value={form.customBalancer}
            onChange={(e) => set('customBalancer', e.target.value)}
            className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
          >
            <option value="">{t('Select balancer group…', 'Выберите группу…')}</option>
            {balancerGroups.map((bg) => (
              <option key={bg.id} value={bg.id}>{bg.name} ({bg.node_ids.length} {t('nodes', 'нод')}, {bg.strategy})</option>
            ))}
          </select>
        </div>
      )}

      <div className="flex items-center gap-2">
        <input
          type="checkbox"
          id="rule-enabled"
          checked={form.enabled}
          onChange={(e) => set('enabled', e.target.checked)}
          className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
        />
        <label htmlFor="rule-enabled" className="text-sm text-gray-300">{t('Enabled', 'Включено')}</label>
      </div>

      <div className="flex justify-end gap-3 pt-2 border-t border-gray-800">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors"
        >
          {t('Cancel', 'Отмена')}
        </button>
        <button
          type="submit"
          disabled={loading}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors"
        >
          {loading ? t('Saving…', 'Сохранение…') : t('Save Rule', 'Сохранить правило')}
        </button>
      </div>
    </form>
  )
}


/**
 * GeoTagPicker — search + click-to-append helper for routing rules
 * referencing `geosite:*` or `geoip:*` tags. Lists all categories
 * present in the currently loaded `.dat` (parsed once at backend
 * startup; v1.2.7).
 *
 * Design notes:
 *   * Filter is a simple substring-includes — fast enough for ~1500
 *     tags (the v2fly geosite typical size); no need for fuzzy search.
 *   * Already-included tags are dimmed but still clickable (no harm
 *     appending a duplicate; xray dedups on its side, and the user
 *     might want to write `category-cn,category-cn-foo` patterns).
 *   * Collapsed by default to avoid blowing up the rule editor; the
 *     full list is rendered only when the user expands.
 */
function GeoTagPicker({
  available,
  currentValue,
  onAppend,
}: {
  available: string[]
  currentValue: string
  onAppend: (tag: string) => void
}) {
  const t = useT()
  const [filter, setFilter] = useState('')
  const [open, setOpen] = useState(false)

  if (available.length === 0) {
    return (
      <div className="mt-1 text-[11px] text-gray-600">
        {t('Tag autocomplete unavailable — the .dat cache is empty (Geo files not loaded).', 'Автодополнение тегов недоступно — кэш .dat пуст (Geo-файлы не загружены).')}
      </div>
    )
  }

  const currentTags = new Set(
    currentValue.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean),
  )
  const f = filter.trim().toLowerCase()
  const filtered = f
    ? available.filter((tag) => tag.includes(f))
    : available
  const display = filtered.slice(0, 200)
  const hidden = filtered.length - display.length

  return (
    <div className="mt-2 rounded-sm border border-gray-800 bg-gray-900/50">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-1.5 text-xs text-gray-400 hover:text-gray-200 transition-colors"
      >
        <span>
          {open ? '▼' : '▶'} {t('Browse available tags', 'Показать доступные теги')} ({available.length})
        </span>
        <span className="text-[11px] text-gray-600">
          {currentTags.size} {t('selected', 'выбрано')}
        </span>
      </button>
      {open && (
        <div className="px-3 pb-3 pt-1 space-y-2">
          <input
            type="text"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder={t('Filter tags…', 'Фильтр тегов…')}
            className="w-full rounded-sm bg-gray-800 border border-gray-700 px-2 py-1 text-xs text-gray-200 focus:border-brand-500 focus:outline-hidden"
          />
          <div className="flex flex-wrap gap-1 max-h-48 overflow-y-auto">
            {display.map((tag) => {
              const inUse = currentTags.has(tag)
              return (
                <button
                  type="button"
                  key={tag}
                  onClick={() => onAppend(tag)}
                  className={
                    inUse
                      ? 'rounded-sm border border-gray-700 bg-gray-800/50 px-2 py-0.5 text-[11px] text-gray-500 font-mono hover:bg-gray-700'
                      : 'rounded-sm border border-gray-700 bg-gray-800 px-2 py-0.5 text-[11px] text-gray-300 font-mono hover:bg-brand-700 hover:border-brand-400 hover:text-white'
                  }
                  title={inUse ? t('Already in this rule (click to append again)', 'Уже в правиле (клик — добавить снова)') : t('Click to append', 'Клик — добавить')}
                >
                  {tag}
                </button>
              )
            })}
            {hidden > 0 && (
              <span className="text-[11px] text-gray-600 self-center pl-2">
                +{hidden} {t('more — refine filter', 'ещё — уточните фильтр')}
              </span>
            )}
            {filtered.length === 0 && (
              <span className="text-[11px] text-gray-600">{t('No tags match the filter.', 'Нет тегов по фильтру.')}</span>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
