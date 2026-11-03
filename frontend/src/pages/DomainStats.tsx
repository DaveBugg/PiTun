import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, ArrowDown, ArrowUp, Eraser, ListPlus, Loader2, Search, Trash2 } from 'lucide-react'
import { balancersApi, domainStatsApi, nodesApi, routingApi } from '@/api/client'
import { useConfirm } from '@/components/ConfirmModal'
import { ModalShell } from '@/components/ModalShell'
import { useRoutingSets } from '@/hooks/useRoutingSets'
import { apiErrorText } from '@/lib/apiError'
import { useT } from '@/hooks/useT'
import type { DomainRuleBody, DomainStatsQuery } from '@/types'

const PAGE = 100
type SortKey = NonNullable<DomainStatsQuery['sort']>

function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function ago(iso: string, t: (en: string, ru: string) => string) {
  const s = Math.max(0, (Date.now() - new Date(iso.endsWith('Z') || iso.includes('+') ? iso : iso + 'Z').getTime()) / 1000)
  if (s < 90) return t('just now', 'только что')
  if (s < 5400) return t(`${Math.round(s / 60)} min ago`, `${Math.round(s / 60)} мин назад`)
  if (s < 129600) return t(`${Math.round(s / 3600)} h ago`, `${Math.round(s / 3600)} ч назад`)
  return t(`${Math.round(s / 86400)} d ago`, `${Math.round(s / 86400)} дн назад`)
}

export function DomainStats() {
  const t = useT()
  const qc = useQueryClient()
  const confirm = useConfirm()

  const [search, setSearch] = useState('')
  const [q, setQ] = useState('')
  const [sort, setSort] = useState<SortKey>('hits')
  const [order, setOrder] = useState<'asc' | 'desc'>('desc')
  const [minHits, setMinHits] = useState(1)
  const [sinceDays, setSinceDays] = useState(0)
  const [offset, setOffset] = useState(0)
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [allMatching, setAllMatching] = useState(false)
  const [ruleOpen, setRuleOpen] = useState(false)
  const [cleanOpen, setCleanOpen] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)

  // Search is sent a moment after typing stops.
  useEffect(() => {
    const id = setTimeout(() => { setQ(search.trim()); setOffset(0) }, 300)
    return () => clearTimeout(id)
  }, [search])

  const params: DomainStatsQuery = {
    q: q || undefined, sort, order,
    min_hits: minHits > 1 ? minHits : undefined,
    since_days: sinceDays > 0 ? sinceDays : undefined,
    limit: PAGE, offset,
  }
  const { data, isLoading, error } = useQuery({
    queryKey: ['domain-stats', params],
    queryFn: () => domainStatsApi.list(params),
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  })

  const refresh = () => qc.invalidateQueries({ queryKey: ['domain-stats'] })
  const cfg = data?.config
  const [draft, setDraft] = useState<{ retention: string; maxRows: string } | null>(null)
  const retention = draft?.retention ?? String(cfg?.retention_days ?? 90)
  const maxRows = draft?.maxRows ?? String(cfg?.max_rows ?? 20000)

  const saveCfg = useMutation({
    mutationFn: (c: { enabled?: boolean; retention_days?: number; max_rows?: number }) => domainStatsApi.updateConfig(c),
    onSuccess: () => { setDraft(null); refresh() },
  })

  const items = data?.items ?? []
  const pageSelected = items.length > 0 && items.every((r) => picked.has(r.domain))
  const toggleAll = () => {
    const next = new Set(picked)
    if (pageSelected) items.forEach((r) => next.delete(r.domain))
    else items.forEach((r) => next.add(r.domain))
    setPicked(next)
    setAllMatching(false)
  }
  const toggle = (d: string) => {
    const next = new Set(picked)
    if (next.has(d)) next.delete(d); else next.add(d)
    setPicked(next)
    setAllMatching(false)
  }
  const selectedCount = allMatching ? (data?.total ?? 0) : picked.size

  const removeSelected = async () => {
    const ok = await confirm({
      title: t(`Remove ${selectedCount} domain(s) from the statistics?`, `Удалить ${selectedCount} доменов из статистики?`),
      body: t('Only the counters are removed — routing rules are not touched.', 'Удаляются только счётчики — правила маршрутизации не затрагиваются.'),
      confirmLabel: t('Remove', 'Удалить'),
      danger: true,
    })
    if (!ok) return
    if (allMatching) {
      setNotice(t('Narrow the search to remove by selection, or use “Clean up”.', 'Сузьте поиск, чтобы удалить выбранное, или используйте «Очистка».'))
      return
    }
    const r = await domainStatsApi.prune({ domains: [...picked] })
    setPicked(new Set())
    setNotice(t(`Removed ${r.removed}.`, `Удалено: ${r.removed}.`))
    refresh()
  }

  const sortHeader = (key: SortKey, label: string, right = false) => (
    <th className={`px-3 py-2 font-medium ${right ? 'text-right' : 'text-left'}`}>
      <button
        type="button"
        onClick={() => { if (sort === key) setOrder(order === 'desc' ? 'asc' : 'desc'); else { setSort(key); setOrder(key === 'domain' ? 'asc' : 'desc') } setOffset(0) }}
        className="inline-flex items-center gap-1 hover:text-gray-200"
      >
        {label}
        {sort === key && (order === 'desc' ? <ArrowDown className="h-3 w-3" /> : <ArrowUp className="h-3 w-3" />)}
      </button>
    </th>
  )

  return (
    <div className="p-4 sm:p-6 space-y-5">
      <div>
        <h1 className="text-xl font-bold text-gray-100">{t('Domains', 'Домены')}</h1>
        <p className="mt-1 text-sm text-gray-400 max-w-3xl">
          {t(
            'Which domains the devices on your network look up, how often and when, with the addresses they resolved to. Pick some of them and turn them into a routing rule — or add them to a rule you already have.',
            'Какие домены запрашивают устройства вашей сети, как часто и когда, и на какие адреса они разрешились. Выберите несколько и превратите в правило маршрутизации — или добавьте в уже существующее.',
          )}
        </p>
        <p className="mt-1 text-xs text-gray-500 max-w-3xl">
          {t(
            'Counters only (no per-device history), kept off by default because the list shows what your household browses. It is capped and ages out on its own, and you can clear it any time. The count is how often xray had to resolve a name: repeats answered from its own DNS cache are not logged, so very busy names are undercounted — it still ranks them and shows when they were last seen.',
            'Только счётчики (без истории по устройствам). По умолчанию выключено, потому что список показывает, что смотрят в вашей семье. Он ограничен и стареет сам, очистить можно в любой момент. Счётчик — сколько раз xray разрешал имя: повторы из собственного DNS-кэша xray не пишутся в лог, поэтому очень частые имена занижены, но порядок и время последнего обращения видны.',
          )}
        </p>
      </div>

      {data && !data.log_level_ok && (
        <div className="rounded-lg border border-amber-700/40 bg-amber-900/20 px-3 py-2 text-xs text-amber-300 flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>
            {t(
              'xray writes DNS lookups to its log only at level “info” or “debug”. Raise the log level in Settings, otherwise nothing is collected.',
              'xray пишет DNS-запросы в лог только на уровне «info» или «debug». Поднимите уровень логов в Настройках, иначе ничего не соберётся.',
            )}
          </span>
        </div>
      )}

      {/* collection + limits */}
      <section className="rounded-xl border border-gray-800 bg-gray-950/60 p-4 space-y-3">
        <div className="flex items-center gap-3 flex-wrap">
          <label className="inline-flex items-center gap-2 text-sm text-gray-200 cursor-pointer">
            <input
              type="checkbox"
              checked={!!cfg?.enabled}
              disabled={!cfg || saveCfg.isPending}
              onChange={(e) => saveCfg.mutate({ enabled: e.target.checked })}
              className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
            />
            {t('Collect domain statistics', 'Собирать статистику доменов')}
          </label>
          <div className="ml-auto flex items-center gap-2 flex-wrap text-xs text-gray-400">
            <label className="inline-flex items-center gap-1.5">
              {t('Keep for', 'Хранить')}
              <input
                type="number" min={0} max={3650} value={retention}
                onChange={(e) => setDraft({ retention: e.target.value, maxRows })}
                className="w-16 rounded-sm bg-gray-800 border border-gray-700 px-2 py-1 text-gray-100"
              />
              {t('days (0 = forever)', 'дн. (0 = бессрочно)')}
            </label>
            <label className="inline-flex items-center gap-1.5">
              {t('Max rows', 'Макс. строк')}
              <input
                type="number" min={100} max={1000000} value={maxRows}
                onChange={(e) => setDraft({ retention, maxRows: e.target.value })}
                className="w-24 rounded-sm bg-gray-800 border border-gray-700 px-2 py-1 text-gray-100"
              />
            </label>
            <button
              type="button"
              disabled={!draft || saveCfg.isPending}
              onClick={() => saveCfg.mutate({ retention_days: Number(retention), max_rows: Number(maxRows) })}
              className="rounded-lg bg-gray-800 px-3 py-1 text-gray-200 hover:bg-gray-700 disabled:opacity-40"
            >
              {t('Save limits', 'Сохранить лимиты')}
            </button>
          </div>
        </div>
        {saveCfg.isError && <p className="text-xs text-red-400">{apiErrorText(saveCfg.error, t('Could not save', 'Не удалось сохранить'))}</p>}
        {data && (
          <p className="text-xs text-gray-500">
            {t(
              `${data.summary.rows} domains · ${data.summary.total_hits} resolutions · ≈ ${fmtBytes(data.summary.approx_bytes)} on disk`,
              `${data.summary.rows} доменов · ${data.summary.total_hits} разрешений · ≈ ${fmtBytes(data.summary.approx_bytes)} на диске`,
            )}
            {data.summary.dropped > 0 && ` · ${t(`${data.summary.dropped} resolutions skipped under load`, `${data.summary.dropped} разрешений пропущено под нагрузкой`)}`}
          </p>
        )}
      </section>

      {/* search + filters */}
      <div className="flex items-center gap-2 flex-wrap">
        <div className="relative">
          <Search className="h-4 w-4 absolute left-2.5 top-2 text-gray-500" />
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t('Search a domain or an IP…', 'Поиск по домену или IP…')}
            className="w-64 rounded-lg bg-gray-800 border border-gray-700 pl-8 pr-3 py-1.5 text-sm text-gray-100"
          />
        </div>
        <label className="text-xs text-gray-400 inline-flex items-center gap-1.5">
          {t('Min resolutions', 'Мин. разрешений')}
          <input
            type="number" min={1} value={minHits}
            onChange={(e) => { setMinHits(Math.max(1, Number(e.target.value) || 1)); setOffset(0) }}
            className="w-16 rounded-sm bg-gray-800 border border-gray-700 px-2 py-1 text-gray-100"
          />
        </label>
        <select
          value={sinceDays}
          onChange={(e) => { setSinceDays(Number(e.target.value)); setOffset(0) }}
          className="rounded-lg bg-gray-800 border border-gray-700 px-2 py-1.5 text-xs text-gray-200"
          aria-label={t('Seen within', 'Виден за период')}
        >
          <option value={0}>{t('Any time', 'За всё время')}</option>
          <option value={1}>{t('Last 24 h', 'Последние 24 ч')}</option>
          <option value={7}>{t('Last 7 days', 'Последние 7 дней')}</option>
          <option value={30}>{t('Last 30 days', 'Последние 30 дней')}</option>
        </select>
        <button
          type="button"
          onClick={() => setCleanOpen(true)}
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-1.5 text-sm text-gray-300 hover:bg-gray-700"
        >
          <Eraser className="h-4 w-4" /> {t('Clean up…', 'Очистка…')}
        </button>
      </div>

      {/* selection bar */}
      {selectedCount > 0 && (
        <div className="rounded-lg border border-brand-700/40 bg-brand-900/20 px-3 py-2 flex items-center gap-3 flex-wrap text-sm">
          <span className="text-gray-200">
            {t(`${selectedCount} selected`, `Выбрано: ${selectedCount}`)}
          </span>
          {!allMatching && pageSelected && (data?.total ?? 0) > items.length && (
            <button type="button" className="text-xs underline text-brand-300" onClick={() => setAllMatching(true)}>
              {t(`Select all ${data?.total} matching`, `Выбрать все найденные (${data?.total})`)}
            </button>
          )}
          <button
            type="button"
            onClick={() => setRuleOpen(true)}
            className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-1 text-sm text-white hover:bg-brand-500"
          >
            <ListPlus className="h-4 w-4" /> {t('Create rule…', 'Создать правило…')}
          </button>
          <button
            type="button" onClick={removeSelected}
            className="inline-flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-1 text-sm text-gray-300 hover:bg-gray-700"
          >
            <Trash2 className="h-4 w-4" /> {t('Remove', 'Удалить')}
          </button>
          <button type="button" className="text-xs text-gray-400 underline" onClick={() => { setPicked(new Set()); setAllMatching(false) }}>
            {t('Clear selection', 'Снять выбор')}
          </button>
        </div>
      )}
      {notice && <p className="text-xs text-emerald-400">{notice}</p>}

      <div className="rounded-xl border border-gray-800 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="bg-gray-900/60 text-xs text-gray-400">
            <tr>
              <th className="px-3 py-2 w-8">
                <input type="checkbox" checked={pageSelected} onChange={toggleAll} aria-label={t('Select page', 'Выбрать страницу')} className="rounded-sm border-gray-600 bg-gray-800" />
              </th>
              {sortHeader('domain', t('Domain', 'Домен'))}
              {sortHeader('hits', t('Resolutions', 'Разрешений'), true)}
              {sortHeader('last_seen', t('Last seen', 'Последний раз'))}
              {sortHeader('first_seen', t('First seen', 'Впервые'))}
              <th className="px-3 py-2 text-left font-medium">{t('Addresses', 'Адреса')}</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {isLoading && (
              <tr><td colSpan={6} className="px-3 py-6 text-center text-gray-500"><Loader2 className="h-4 w-4 animate-spin inline" /></td></tr>
            )}
            {error && (
              <tr><td colSpan={6} className="px-3 py-6 text-center text-red-400">{apiErrorText(error, t('Could not load', 'Не удалось загрузить'))}</td></tr>
            )}
            {!isLoading && !error && items.length === 0 && (
              <tr>
                <td colSpan={6} className="px-3 py-8 text-center text-gray-500">
                  {q || minHits > 1 || sinceDays
                    ? t('Nothing matches.', 'Ничего не найдено.')
                    : cfg?.enabled
                      ? t('Nothing collected yet — it fills as xray resolves names for your devices.', 'Пока пусто — заполняется по мере того, как xray разрешает имена для ваших устройств.')
                      : t('Collection is off. Turn it on above to start.', 'Сбор выключен. Включите его выше, чтобы начать.')}
                </td>
              </tr>
            )}
            {items.map((r) => (
              <tr key={r.domain} className="hover:bg-gray-900/40">
                <td className="px-3 py-1.5">
                  <input type="checkbox" checked={picked.has(r.domain) || allMatching} onChange={() => toggle(r.domain)} aria-label={r.domain} className="rounded-sm border-gray-600 bg-gray-800" />
                </td>
                <td className="px-3 py-1.5 font-mono text-gray-100 break-all">{r.domain}</td>
                <td className="px-3 py-1.5 text-right tabular-nums text-gray-200">{r.hits}</td>
                <td className="px-3 py-1.5 text-gray-400 whitespace-nowrap">{ago(r.last_seen, t)}</td>
                <td className="px-3 py-1.5 text-gray-500 whitespace-nowrap">{ago(r.first_seen, t)}</td>
                <td className="px-3 py-1.5 font-mono text-xs text-gray-500">{r.ips.slice(0, 2).join(', ')}{r.ips.length > 2 ? ` +${r.ips.length - 2}` : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data && data.total > PAGE && (
        <div className="flex items-center gap-3 text-xs text-gray-400">
          <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} className="rounded-lg bg-gray-800 px-3 py-1 disabled:opacity-40">{t('Previous', 'Назад')}</button>
          <span>{offset + 1}–{Math.min(offset + PAGE, data.total)} / {data.total}</span>
          <button type="button" disabled={offset + PAGE >= data.total} onClick={() => setOffset(offset + PAGE)} className="rounded-lg bg-gray-800 px-3 py-1 disabled:opacity-40">{t('Next', 'Вперёд')}</button>
        </div>
      )}

      {ruleOpen && (
        <RuleModal
          count={selectedCount}
          body={allMatching
            ? { query: q, min_hits: minHits > 1 ? minHits : undefined, since_days: sinceDays > 0 ? sinceDays : undefined }
            : { domains: [...picked] }}
          onClose={() => setRuleOpen(false)}
          onDone={(msg) => { setRuleOpen(false); setNotice(msg); setPicked(new Set()); setAllMatching(false) }}
        />
      )}
      {cleanOpen && data && (
        <CleanModal
          rows={data.summary.rows}
          bytes={data.summary.approx_bytes}
          onClose={() => setCleanOpen(false)}
          onDone={(msg) => { setCleanOpen(false); setNotice(msg); setPicked(new Set()); refresh() }}
        />
      )}
    </div>
  )
}

// ── create / extend a routing rule from the selection ───────────────────────

function RuleModal({ count, body, onClose, onDone }: {
  count: number
  body: Pick<DomainRuleBody, 'domains' | 'query' | 'min_hits' | 'since_days'>
  onClose: () => void
  onDone: (message: string) => void
}) {
  const t = useT()
  const [use, setUse] = useState<'domains' | 'ips'>('domains')
  const [useParent, setUseParent] = useState(false)
  const [mode, setMode] = useState<'new' | 'append'>('new')
  const [name, setName] = useState('')
  const [action, setAction] = useState('proxy')
  const [setId, setSetId] = useState('')
  const [ruleId, setRuleId] = useState('')

  const { data: rules = [] } = useQuery({ queryKey: ['routing', 'rules'], queryFn: () => routingApi.listRules() })
  const { data: nodes = [] } = useQuery({ queryKey: ['nodes', 'all-for-rule'], queryFn: () => nodesApi.list() })
  const { data: balancers = [] } = useQuery({ queryKey: ['balancers'], queryFn: () => balancersApi.list() })
  const { data: sets = [] } = useRoutingSets()

  const wantedType = use === 'domains' ? 'domain' : 'dst_ip'
  const candidates = useMemo(() => rules.filter((r) => r.rule_type === wantedType), [rules, wantedType])
  useEffect(() => { setRuleId('') }, [wantedType])

  const create = useMutation({
    mutationFn: () => domainStatsApi.createRules({
      ...body, use, use_parent: use === 'domains' ? useParent : undefined, mode,
      rule_id: mode === 'append' ? Number(ruleId) : undefined,
      name: name.trim() || undefined, action,
      routing_set_id: setId ? Number(setId) : null,
    }),
    onSuccess: (r) => onDone(
      r.mode === 'append'
        ? t(`Added ${r.added} to the rule (${r.already_present} were already there).`, `Добавлено в правило: ${r.added} (уже были: ${r.already_present}).`)
        : t(`Rule created with ${r.added} entries.`, `Правило создано, записей: ${r.added}.`),
    ),
  })

  const field = 'w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100'
  return (
    <ModalShell onClose={onClose} labelledBy="dom-rule-title">
      <div className="w-full max-w-lg rounded-2xl bg-gray-950/95 border border-gray-800 p-6 space-y-4">
        <h2 id="dom-rule-title" className="text-lg font-semibold text-gray-100 pr-8">
          {t(`Create a rule from ${count} domain(s)`, `Правило из доменов: ${count}`)}
        </h2>

        <div className="grid grid-cols-2 gap-2 text-sm">
          {(['domains', 'ips'] as const).map((u) => (
            <button
              key={u} type="button" onClick={() => setUse(u)}
              className={`rounded-lg border px-3 py-2 ${use === u ? 'border-brand-500 bg-brand-900/30 text-gray-100' : 'border-gray-700 text-gray-400'}`}
            >
              {u === 'domains' ? t('Match the domains', 'По доменам') : t('Match the IP addresses', 'По IP-адресам')}
            </button>
          ))}
        </div>
        {use === 'domains' ? (
          <label className="flex items-center gap-2 text-xs text-gray-300">
            <input type="checkbox" checked={useParent} onChange={(e) => setUseParent(e.target.checked)} className="rounded-sm border-gray-600 bg-gray-800" />
            {t('Use the parent domain (video.example.com → example.com, covers subdomains)', 'Брать родительский домен (video.example.com → example.com, охватывает поддомены)')}
          </label>
        ) : (
          <p className="text-xs text-gray-500">{t('Uses the addresses recorded for these domains. Domains with no recorded address are skipped.', 'Берутся адреса, записанные для этих доменов. Домены без записанного адреса пропускаются.')}</p>
        )}

        <div className="grid grid-cols-2 gap-2 text-sm">
          {(['new', 'append'] as const).map((m) => (
            <button
              key={m} type="button" onClick={() => setMode(m)}
              className={`rounded-lg border px-3 py-2 ${mode === m ? 'border-brand-500 bg-brand-900/30 text-gray-100' : 'border-gray-700 text-gray-400'}`}
            >
              {m === 'new' ? t('New rule', 'Новое правило') : t('Add to an existing rule', 'В существующее правило')}
            </button>
          ))}
        </div>

        {mode === 'new' ? (
          <div className="space-y-3">
            <label className="block text-xs text-gray-400">{t('Name (optional)', 'Название (необязательно)')}
              <input className={field} value={name} onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="block text-xs text-gray-400">{t('Send through', 'Направить')}
              <select className={field} value={action} onChange={(e) => setAction(e.target.value)}>
                <option value="proxy">{t('Proxy (active node)', 'Прокси (активная нода)')}</option>
                <option value="direct">{t('Direct', 'Напрямую')}</option>
                <option value="block">{t('Block', 'Блокировать')}</option>
                {nodes.length > 0 && (
                  <optgroup label={t('A specific node', 'Конкретная нода')}>
                    {nodes.map((n) => <option key={n.id} value={`node:${n.id}`}>{n.name}</option>)}
                  </optgroup>
                )}
                {balancers.length > 0 && (
                  <optgroup label={t('A balancer group', 'Группа балансировки')}>
                    {balancers.map((b) => <option key={b.id} value={`balancer:${b.id}`}>{b.name}</option>)}
                  </optgroup>
                )}
              </select>
            </label>
            {sets.length > 0 && (
              <label className="block text-xs text-gray-400">{t('Applies to', 'Действует для')}
                <select className={field} value={setId} onChange={(e) => setSetId(e.target.value)}>
                  <option value="">{t('Every device (global)', 'Всех устройств (глобально)')}</option>
                  {sets.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              </label>
            )}
          </div>
        ) : (
          <label className="block text-xs text-gray-400">{t('Rule', 'Правило')}
            <select className={field} value={ruleId} onChange={(e) => setRuleId(e.target.value)}>
              <option value="">{t('Pick a rule…', 'Выберите правило…')}</option>
              {candidates.map((r) => <option key={r.id} value={r.id}>{r.name} · {r.action}</option>)}
            </select>
            {candidates.length === 0 && (
              <span className="text-gray-500">{t(`No ${wantedType} rules yet — create a new one instead.`, `Правил типа ${wantedType} пока нет — создайте новое.`)}</span>
            )}
          </label>
        )}

        {create.isError && <p className="text-xs text-red-400">{apiErrorText(create.error, t('Could not create the rule', 'Не удалось создать правило'))}</p>}

        <div className="flex justify-end gap-2 pt-2 border-t border-gray-800">
          <button type="button" onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:bg-gray-800">{t('Cancel', 'Отмена')}</button>
          <button
            type="button"
            disabled={create.isPending || (mode === 'append' && !ruleId)}
            onClick={() => create.mutate()}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50"
          >
            {create.isPending ? t('Saving…', 'Сохраняю…') : mode === 'new' ? t('Create rule', 'Создать правило') : t('Add', 'Добавить')}
          </button>
        </div>
      </div>
    </ModalShell>
  )
}

// ── cleaning ────────────────────────────────────────────────────────────────

function CleanModal({ rows, bytes, onClose, onDone }: {
  rows: number
  bytes: number
  onClose: () => void
  onDone: (message: string) => void
}) {
  const t = useT()
  const confirm = useConfirm()
  const [kind, setKind] = useState<'age' | 'top' | 'size' | 'all'>('age')
  const [days, setDays] = useState('30')
  const [top, setTop] = useState('1000')
  const [kb, setKb] = useState(String(Math.max(64, Math.round(bytes / 2048))))

  const run = useMutation({
    mutationFn: () => {
      if (kind === 'age') return domainStatsApi.prune({ older_than_days: Number(days) })
      if (kind === 'top') return domainStatsApi.prune({ keep_top: Number(top) })
      if (kind === 'size') return domainStatsApi.prune({ max_bytes: Number(kb) * 1024 })
      return domainStatsApi.prune({ everything: true })
    },
    onSuccess: (r) => onDone(t(`Removed ${r.removed} domain(s).`, `Удалено доменов: ${r.removed}.`)),
  })

  const go = async () => {
    if (kind === 'all') {
      const ok = await confirm({
        title: t('Clear all domain statistics?', 'Очистить всю статистику доменов?'),
        body: t(`${rows} domains and their counters will be removed.`, `Будут удалены домены (${rows}) и их счётчики.`),
        confirmLabel: t('Clear everything', 'Очистить всё'),
        danger: true,
      })
      if (!ok) return
    }
    run.mutate()
  }

  const opt = (k: typeof kind, label: string, control?: React.ReactNode) => (
    <label className={`flex items-center gap-3 rounded-lg border px-3 py-2 text-sm cursor-pointer ${kind === k ? 'border-brand-500 bg-brand-900/20' : 'border-gray-700'}`}>
      <input type="radio" name="clean" checked={kind === k} onChange={() => setKind(k)} />
      <span className="text-gray-200 flex-1">{label}</span>
      {control}
    </label>
  )
  const num = 'w-24 rounded-sm bg-gray-800 border border-gray-700 px-2 py-1 text-sm text-gray-100'

  return (
    <ModalShell onClose={onClose} labelledBy="dom-clean-title">
      <div className="w-full max-w-md rounded-2xl bg-gray-950/95 border border-gray-800 p-6 space-y-4">
        <h2 id="dom-clean-title" className="text-lg font-semibold text-gray-100 pr-8">{t('Clean up', 'Очистка')}</h2>
        <p className="text-xs text-gray-500">{t(`Now ${rows} domains, ≈ ${fmtBytes(bytes)}. Routing rules are never touched.`, `Сейчас ${rows} доменов, ≈ ${fmtBytes(bytes)}. Правила маршрутизации не затрагиваются.`)}</p>
        <div className="space-y-2">
          {opt('age', t('Remove domains not seen for more than (days)', 'Удалить домены, не встречавшиеся дольше (дней)'),
            <input className={num} type="number" min={1} value={days} onChange={(e) => setDays(e.target.value)} onFocus={() => setKind('age')} />)}
          {opt('top', t('Keep only the busiest (domains)', 'Оставить только самые частые (доменов)'),
            <input className={num} type="number" min={0} value={top} onChange={(e) => setTop(e.target.value)} onFocus={() => setKind('top')} />)}
          {opt('size', t('Shrink to about (KB)', 'Сжать примерно до (КБ)'),
            <input className={num} type="number" min={1} value={kb} onChange={(e) => setKb(e.target.value)} onFocus={() => setKind('size')} />)}
          {opt('all', t('Clear everything', 'Очистить всё'))}
        </div>
        {run.isError && <p className="text-xs text-red-400">{apiErrorText(run.error, t('Could not clean up', 'Не удалось очистить'))}</p>}
        <div className="flex justify-end gap-2 pt-2 border-t border-gray-800">
          <button type="button" onClick={onClose} className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:bg-gray-800">{t('Cancel', 'Отмена')}</button>
          <button type="button" disabled={run.isPending} onClick={go} className="rounded-lg bg-red-700 px-4 py-2 text-sm font-medium text-white hover:bg-red-600 disabled:opacity-50">
            {run.isPending ? t('Working…', 'Работаю…') : t('Remove', 'Удалить')}
          </button>
        </div>
      </div>
    </ModalShell>
  )
}
