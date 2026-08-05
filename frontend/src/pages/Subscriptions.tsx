import { useEffect, useRef, useState } from 'react'
import {
  Download, Fingerprint, Link, Plus, RefreshCw, Rss, Trash2, Upload, Zap,
} from 'lucide-react'
import { InfoTip } from '@/components/InfoTip'
import { clsx } from 'clsx'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { subsApi, uaTemplatesApi } from '@/api/client'
import { useConfirm } from '@/components/ConfirmModal'
import { ModalShell } from '@/components/ModalShell'
import { UserAgentTemplatesModal } from '@/components/UserAgentTemplatesModal'
import { useT } from '@/hooks/useT'
import { apiErrorText } from '@/lib/apiError'
import type { Subscription, SubscriptionCreate, UserAgentTemplate } from '@/types'

// Shown only while the template list loads, or if the table is empty. The
// backend has the matching fallback, so these still resolve to a real UA.
const FALLBACK_UA_KEYS = [
  'v2ray', 'clash', 'sing-box',
  'happ', 'happ-android', 'happ-windows', 'happ-macos',
  'streisand', 'chrome',
]

const INTERVAL_PRESETS = [
  { label: '1h', value: 3600 },
  { label: '6h', value: 21600 },
  { label: '12h', value: 43200 },
  { label: '24h', value: 86400 },
  { label: '3d', value: 259200 },
  { label: '7d', value: 604800 },
]

function SubForm({
  initial,
  onSave,
  onCancel,
  loading,
  templates,
  onManageTemplates,
}: {
  initial?: Partial<Subscription>
  onSave: (d: SubscriptionCreate) => void
  onCancel: () => void
  loading?: boolean
  /** UA templates from the API — drives the dropdown. */
  templates: UserAgentTemplate[]
  onManageTemplates: () => void
}) {
  const t = useT()
  const [form, setForm] = useState({
    name: initial?.name ?? '',
    url: initial?.url ?? '',
    ua: initial?.ua ?? 'v2ray',
    custom_ua: initial?.custom_ua ?? '',
    filter_regex: initial?.filter_regex ?? '',
    auto_update: initial?.auto_update ?? true,
    update_interval: initial?.update_interval ?? 86400,
    enabled: initial?.enabled ?? true,
    rotate_hwid: initial?.rotate_hwid ?? false,
  })
  const set = <K extends keyof typeof form>(k: K, v: typeof form[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  const uaOptions = templates.length > 0
    ? templates.map((tpl) => ({
        key: tpl.key,
        label: tpl.name === tpl.key ? tpl.key : `${tpl.name} · ${tpl.key}`,
      }))
    : FALLBACK_UA_KEYS.map((k) => ({ key: k, label: k }))
  const selectedTemplate = templates.find((tpl) => tpl.key === form.ua)
  const selectedHeaderCount = Object.keys(selectedTemplate?.headers ?? {}).length

  return (
    <form
      onSubmit={(e) => { e.preventDefault(); onSave(form) }}
      className="space-y-4"
    >
      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1">Name</label>
        <input
          value={form.name}
          onChange={(e) => set('name', e.target.value)}
          required
          autoFocus
          placeholder="My Subscription"
          className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
        />
      </div>
      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1">URL</label>
        <input
          value={form.url}
          onChange={(e) => set('url', e.target.value)}
          required
          type="url"
          placeholder="https://provider.com/sub?token=…"
          className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-hidden"
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
            User-Agent
            <InfoTip className="ml-0.5" text="User agent sent when fetching the subscription. Providers serve different formats based on UA. v2ray → base64 URI list, clash → YAML, sing-box → JSON config, happ/streisand → bypass some CDN protections, chrome → full browser UA for strict CDN. Manage the list with the 'UA templates' button on this page — a template can also carry extra request headers." />
          </label>
          <select
            value={form.ua}
            onChange={(e) => set('ua', e.target.value)}
            disabled={!!form.custom_ua.trim()}
            className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden disabled:opacity-50"
          >
            {uaOptions.map((o) => (
              <option key={o.key} value={o.key}>{o.label}</option>
            ))}
            {/* The subscription points at a key that no longer has a
                template (deleted or renamed outside this form). Keep it
                selectable so opening Edit doesn't silently re-point the
                subscription at whatever happens to be first. */}
            {form.ua && !uaOptions.some((o) => o.key === form.ua) && (
              <option value={form.ua}>
                {t(`${form.ua} (missing template)`, `${form.ua} (шаблон удалён)`)}
              </option>
            )}
          </select>
          <div className="mt-1 flex items-start justify-between gap-2">
            <span className="text-[11px] text-gray-600 font-mono truncate" title={selectedTemplate?.user_agent}>
              {selectedTemplate?.user_agent ?? ''}
            </span>
            <button
              type="button"
              onClick={onManageTemplates}
              className="text-[11px] text-brand-400 hover:text-brand-700 dark:hover:text-brand-300 whitespace-nowrap"
            >
              {t('Manage…', 'Настроить…')}
            </button>
          </div>
          {selectedHeaderCount > 0 && (
            <p className="mt-0.5 text-[11px] text-brand-400">
              {t(
                `+${selectedHeaderCount} custom header(s)`,
                `+${selectedHeaderCount} свой(их) заголовк(ов)`,
              )}
            </p>
          )}
        </div>
        <div>
          <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
            Update Interval
            <InfoTip className="ml-0.5" text="How often to auto-update. The scheduler checks every minute and refreshes subscriptions when the interval has elapsed." />
          </label>
          <div className="flex rounded-sm overflow-hidden border border-gray-700">
            {INTERVAL_PRESETS.map((p) => (
              <button
                key={p.value}
                type="button"
                onClick={() => set('update_interval', p.value)}
                className={clsx(
                  'flex-1 px-1.5 py-1.5 text-xs font-medium transition-colors',
                  form.update_interval === p.value
                    ? 'bg-brand-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:text-gray-200',
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
        </div>
      </div>
      <div>
        <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
          Custom User-Agent (override, optional)
          <InfoTip className="ml-0.5" text="Override the User-Agent picked from the dropdown above. Paste the exact UA string the panel docs require — useful for non-standard panels that gate on a fingerprint we don't ship a preset for. When set, the dropdown is ignored. Leave empty for normal use." />
        </label>
        <input
          value={form.custom_ua}
          onChange={(e) => set('custom_ua', e.target.value)}
          placeholder="e.g. Happ/2.7.0/ios/17.4/iPhone15,2"
          className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-hidden"
        />
      </div>
      <div>
        <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
          Name Filter (regex, optional)
          <InfoTip className="ml-0.5" text="Optional regex to filter imported nodes by name. Example: 'HK|SG' keeps only nodes with HK or SG in their name. Leave empty to import all nodes." />
        </label>
        <input
          value={form.filter_regex}
          onChange={(e) => set('filter_regex', e.target.value)}
          placeholder="HK|SG|US"
          className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-hidden"
        />
      </div>
      <div className="flex flex-wrap gap-4">
        <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
          <input
            type="checkbox"
            checked={form.auto_update}
            onChange={(e) => set('auto_update', e.target.checked)}
            className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
          />
          Auto-update
        </label>
        <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => set('enabled', e.target.checked)}
            className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
          />
          Enabled
        </label>
        <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
          <input
            type="checkbox"
            checked={form.rotate_hwid}
            onChange={(e) => set('rotate_hwid', e.target.checked)}
            className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
          />
          Rotate HWID
          <InfoTip className="ml-0.5" text="When enabled, each refresh generates a fresh random X-Hwid header instead of the stable machine-id-derived one. Most panels device-bind on first HWID, so leave OFF by default. Turn ON only if a panel starts returning degraded payloads (e.g. placeholder 'proxy' dummies) to the stable HWID — a fresh UUID often gets past the throttle." />
        </label>
      </div>
      <div className="flex justify-end gap-3 pt-2 border-t border-gray-800">
        <button type="button" onClick={onCancel} className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors">
          Cancel
        </button>
        <button type="submit" disabled={loading} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors">
          {loading ? 'Saving…' : 'Save'}
        </button>
      </div>
    </form>
  )
}

export function Subscriptions() {
  const [modal, setModal] = useState<'none' | 'add' | 'edit'>('none')
  const [editSub, setEditSub] = useState<Subscription | null>(null)
  const [quickUrl, setQuickUrl] = useState('')
  const [templatesOpen, setTemplatesOpen] = useState(false)
  const [uaNotice, setUaNotice] = useState<string | null>(null)
  const importRef = useRef<HTMLInputElement>(null)
  const qc = useQueryClient()
  const confirm = useConfirm()
  const t = useT()

  // `refreshing` holds the ids whose background refresh we're waiting on.
  // The endpoint answers 202 and does the fetch AFTER the response, so a
  // one-shot invalidate on success only ever re-read the OLD rows — and
  // with refetchOnMount/refetchOnWindowFocus off, nothing refreshed them
  // afterwards. We poll until `last_updated` moves (or we give up).
  const [refreshing, setRefreshing] = useState<Record<number, string | null>>({})

  const { data: subs = [] } = useQuery({
    queryKey: ['subscriptions'],
    queryFn: () => subsApi.list(),
    refetchInterval: Object.keys(refreshing).length > 0 ? 2000 : false,
  })

  // Drop ids whose row has moved on — `last_updated` changed, so the
  // background job finished.
  useEffect(() => {
    if (Object.keys(refreshing).length === 0) return
    setRefreshing((prev) => {
      const next = { ...prev }
      let changed = false
      for (const sub of subs) {
        if (!(sub.id in next)) continue
        if ((sub.last_updated ?? null) !== next[sub.id]) {
          delete next[sub.id]
          changed = true
        }
      }
      return changed ? next : prev
    })
  }, [subs, refreshing])

  // Same key as the templates modal, so a save there repaints this list.
  const { data: uaTemplates = [] } = useQuery({
    queryKey: ['ua-templates'],
    queryFn: () => uaTemplatesApi.list(),
  })

  const importTemplates = useMutation({
    mutationFn: ({ bundle, overwrite }: { bundle: unknown; overwrite: boolean }) =>
      uaTemplatesApi.importJSON(bundle, { overwrite }),
    onSuccess: (r) => {
      qc.invalidateQueries({ queryKey: ['ua-templates'] })
      const parts = [
        t(`${r.imported} added`, `добавлено: ${r.imported}`),
        t(`${r.updated} updated`, `обновлено: ${r.updated}`),
        t(`${r.skipped} skipped`, `пропущено: ${r.skipped}`),
      ]
      if (r.errors.length > 0) {
        parts.push(t(`${r.errors.length} failed`, `с ошибкой: ${r.errors.length}`))
      }
      setUaNotice(`${t('UA templates', 'Шаблоны UA')}: ${parts.join(' · ')}`)
    },
  })

  const handleExportTemplates = async () => {
    setUaNotice(null)
    try {
      await uaTemplatesApi.exportJSON()
    } catch (err) {
      setUaNotice(
        t('Export failed: ', 'Ошибка экспорта: ') +
        (err instanceof Error ? err.message : String(err)),
      )
    }
  }

  const handleImportTemplates = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    // Reset immediately so re-picking the same file fires `onChange` again.
    e.target.value = ''
    if (!file) return
    setUaNotice(null)

    let bundle: unknown
    try {
      bundle = JSON.parse(await file.text())
    } catch {
      setUaNotice(t('Not a valid JSON file.', 'Файл не является корректным JSON.'))
      return
    }

    // Only ask about overwriting when the bundle actually collides —
    // a prompt with no consequence just trains people to click through.
    const bundleKeys: string[] = Array.isArray((bundle as { templates?: unknown })?.templates)
      ? ((bundle as { templates: unknown[] }).templates
          .map((e) => (e as { key?: unknown })?.key)
          .filter((k): k is string => typeof k === 'string'))
      : []
    const existing = new Set(uaTemplates.map((tpl) => tpl.key))
    const collisions = bundleKeys.filter((k) => existing.has(k))

    let overwrite = false
    if (collisions.length > 0) {
      overwrite = await confirm({
        title: t(
          `${collisions.length} template(s) already exist`,
          `${collisions.length} шаблон(ов) уже существует`,
        ),
        body: t(
          `${collisions.join(', ')} — overwrite with the file's version? Their subscriptions stay attached either way. Choosing "Keep mine" imports only the new templates.`,
          `${collisions.join(', ')} — перезаписать версией из файла? Подписки останутся привязанными в любом случае. «Оставить свои» — импортировать только новые шаблоны.`,
        ),
        confirmLabel: t('Overwrite', 'Перезаписать'),
        cancelLabel: t('Keep mine', 'Оставить свои'),
      })
    }

    try {
      await importTemplates.mutateAsync({ bundle, overwrite })
    } catch (err) {
      setUaNotice(
        t('Import failed: ', 'Ошибка импорта: ') +
        apiErrorText(err, t('unknown error', 'неизвестная ошибка')),
      )
    }
  }

  const create = useMutation({
    mutationFn: (d: SubscriptionCreate) => subsApi.create(d),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['subscriptions'] })
      qc.invalidateQueries({ queryKey: ['nodes'] })
      setModal('none')
      setQuickUrl('')
    },
  })
  const update = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<SubscriptionCreate> }) =>
      subsApi.update(id, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['subscriptions'] }); setModal('none') },
  })
  const del = useMutation({
    mutationFn: (id: number) => subsApi.delete(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['subscriptions'] })
      qc.invalidateQueries({ queryKey: ['nodes'] })
    },
  })
  const refresh = useMutation({
    mutationFn: (id: number) => subsApi.refresh(id),
    // The endpoint returns 202 and runs the fetch in a background task,
    // so nothing is committed yet when this resolves. Remember the row's
    // pre-refresh `last_updated` and poll the list until it moves; the
    // effect above clears the marker and stops the polling.
    onMutate: (id) => {
      const before = subs.find((s) => s.id === id)?.last_updated ?? null
      setRefreshing((prev) => ({ ...prev, [id]: before }))
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['subscriptions'] })
      qc.invalidateQueries({ queryKey: ['nodes'] })
    },
    onError: (_err, id) => {
      // 409 from the in-flight mutex, or a plain network failure —
      // either way we're not waiting on this one. The global toast
      // renders the message (including the backend's hint).
      setRefreshing((prev) => {
        const next = { ...prev }
        delete next[id]
        return next
      })
    },
  })

  const handleQuickAdd = () => {
    const url = quickUrl.trim()
    if (!url) return
    try {
      const parsed = new URL(url)
      const name = parsed.hostname.replace(/^(www|sub|api)\./, '').split('.')[0] || 'Subscription'
      create.mutate({
        name,
        url,
        ua: 'v2ray',
        filter_regex: '',
        auto_update: true,
        update_interval: 86400,
        enabled: true,
      })
    } catch {
      // If URL is invalid, open full form with URL pre-filled
      setEditSub(null)
      setModal('add')
    }
  }

  const handleSave = (data: SubscriptionCreate) => {
    if (editSub) {
      update.mutate({ id: editSub.id, data })
    } else {
      create.mutate(data)
    }
  }

  const refreshAll = () => {
    subs.forEach((s) => {
      if (s.enabled) refresh.mutate(s.id)
    })
  }

  return (
    <div className="p-4 sm:p-6 space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-xl font-bold text-gray-100">Subscriptions</h1>
        <div className="flex items-center gap-2">
          {/* UA templates: manage, export, import. */}
          <div className="flex items-center rounded-lg bg-gray-800 overflow-hidden">
            <button
              onClick={() => setTemplatesOpen(true)}
              className="flex items-center gap-1.5 px-3 py-2 text-sm text-gray-300 hover:bg-gray-700 transition-colors"
            >
              <Fingerprint className="h-4 w-4" />
              <span className="hidden sm:inline">{t('UA templates', 'Шаблоны UA')}</span>
              <span className="sm:hidden">UA</span>
            </button>
            <span className="h-5 w-px bg-gray-700" aria-hidden="true" />
            <button
              onClick={handleExportTemplates}
              title={t('Export UA templates to a JSON file', 'Экспорт шаблонов UA в JSON-файл')}
              aria-label={t('Export UA templates', 'Экспорт шаблонов UA')}
              className="px-2.5 py-2 text-gray-400 hover:bg-gray-700 hover:text-gray-200 transition-colors"
            >
              <Download className="h-4 w-4" />
            </button>
            <button
              onClick={() => importRef.current?.click()}
              disabled={importTemplates.isPending}
              title={t('Import UA templates from a JSON file', 'Импорт шаблонов UA из JSON-файла')}
              aria-label={t('Import UA templates', 'Импорт шаблонов UA')}
              className="px-2.5 py-2 text-gray-400 hover:bg-gray-700 hover:text-gray-200 transition-colors disabled:opacity-50"
            >
              <Upload className="h-4 w-4" />
            </button>
            <input
              ref={importRef}
              type="file"
              accept=".json,application/json"
              onChange={handleImportTemplates}
              className="hidden"
            />
          </div>

          {subs.length > 0 && (
            <button
              onClick={refreshAll}
              disabled={refresh.isPending || Object.keys(refreshing).length > 0}
              className="flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-300 hover:bg-gray-700 transition-colors disabled:opacity-50"
            >
              <RefreshCw className={clsx('h-4 w-4', refresh.isPending && 'animate-spin')} />
              Refresh All
            </button>
          )}
          <button
            onClick={() => { setEditSub(null); setModal('add') }}
            className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors"
          >
            <Plus className="h-4 w-4" />
            Add
          </button>
        </div>
      </div>

      {uaNotice && (
        <div
          role="status"
          className="flex items-start justify-between gap-3 rounded-lg border border-gray-800 bg-gray-900/50 px-3 py-2 text-xs text-gray-300"
        >
          <span>{uaNotice}</span>
          <button
            onClick={() => setUaNotice(null)}
            aria-label={t('Dismiss', 'Закрыть')}
            className="text-gray-500 hover:text-gray-200"
          >
            ×
          </button>
        </div>
      )}

      {/* Quick Add bar */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-3">
        <label className="flex items-center gap-1.5 text-xs font-medium text-gray-500 mb-2">
          <Zap className="h-3.5 w-3.5" />
          Quick Add — paste subscription URL
        </label>
        <div className="flex gap-2">
          <div className="relative flex-1">
            <Link className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-600" />
            <input
              value={quickUrl}
              onChange={(e) => setQuickUrl(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleQuickAdd() } }}
              placeholder="https://provider.com/sub?token=abc123"
              className="w-full rounded-lg bg-gray-800 border border-gray-700 pl-9 pr-3 py-2 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-hidden"
            />
          </div>
          <button
            onClick={handleQuickAdd}
            disabled={!quickUrl.trim() || create.isPending}
            className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors whitespace-nowrap"
          >
            {create.isPending ? 'Adding…' : 'Add'}
          </button>
        </div>
        <p className="text-[11px] text-gray-600 mt-1.5">
          Auto-update: 24h · UA: v2ray · Press Enter or click Add. Edit settings later on the subscription card.
        </p>
      </div>

      {subs.length === 0 ? (
        <div className="text-center py-12 text-gray-500">No subscriptions yet. Paste a URL above or click Add.</div>
      ) : (
        <div className="space-y-3">
          {subs.map((sub) => {
            const intervalLabel = INTERVAL_PRESETS.find(p => p.value === sub.update_interval)?.label
              ?? `${Math.round(sub.update_interval / 3600)}h`
            const tpl = uaTemplates.find((x) => x.key === sub.ua)
            const tplHeaderCount = Object.keys(tpl?.headers ?? {}).length
            return (
              <div key={sub.id} className={clsx(
                'rounded-xl border bg-gray-900 p-4 transition-colors',
                sub.enabled ? 'border-gray-800' : 'border-gray-800/50 opacity-60',
              )}>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-3 min-w-0">
                    <Rss className="h-5 w-5 text-brand-400 shrink-0 mt-0.5" />
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-gray-100">{sub.name}</div>
                      <div className="text-xs text-gray-500 font-mono mt-0.5 truncate">{sub.url}</div>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 text-xs text-gray-600">
                        <span className="text-gray-400 font-medium">{sub.node_count} nodes</span>
                        {/* Resolved UA on hover: the key alone doesn't say
                            what goes on the wire once a template is edited. */}
                        <span title={sub.custom_ua?.trim() || tpl?.user_agent || undefined}>
                          UA: {sub.ua}
                          {tplHeaderCount > 0 && (
                            <span className="ml-1 text-brand-400">+{tplHeaderCount}h</span>
                          )}
                          {sub.custom_ua?.trim() && (
                            <span className="ml-1 text-yellow-600">
                              {t('(overridden)', '(переопределён)')}
                            </span>
                          )}
                        </span>
                        {sub.auto_update && (
                          <span className="text-green-600">auto: {intervalLabel}</span>
                        )}
                        {sub.filter_regex && (
                          <span className="text-yellow-600">filter: /{sub.filter_regex}/</span>
                        )}
                        {sub.last_updated && (
                          <span>Updated: {new Date(sub.last_updated).toLocaleString([], {
                            day: '2-digit', month: '2-digit', year: '2-digit',
                            hour: '2-digit', minute: '2-digit',
                          })}</span>
                        )}
                        {!sub.enabled && (
                          <span className="text-yellow-600 font-medium">Disabled</span>
                        )}
                      </div>
                      {sub.last_error && (
                        <div className="mt-1.5 text-xs text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-800/40 rounded-sm px-2 py-1 font-mono">
                          Error: {sub.last_error}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <button
                      onClick={() => refresh.mutate(sub.id)}
                      disabled={sub.id in refreshing}
                      title="Refresh now"
                      className="rounded-sm p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
                    >
                      <RefreshCw className={clsx('h-4 w-4', sub.id in refreshing && 'animate-spin')} />
                    </button>
                    <button
                      onClick={() => { setEditSub(sub); setModal('edit') }}
                      title="Edit"
                      className="rounded-sm p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
                    >
                      <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15.232 5.232l3.536 3.536m-2.036-5.036a2.5 2.5 0 113.536 3.536L6.5 21.036H3v-3.572L16.732 3.732z" />
                      </svg>
                    </button>
                    <button
                      onClick={async () => {
                        const ok = await confirm({
                          title: `Delete subscription "${sub.name}"?`,
                          body: `Will also remove its ${sub.node_count} imported nodes. Cannot be undone.`,
                          confirmLabel: 'Delete',
                          danger: true,
                        })
                        if (ok) del.mutate(sub.id)
                      }}
                      title="Delete with nodes"
                      className="rounded-sm p-1.5 text-gray-500 hover:text-red-400 hover:bg-gray-800 transition-colors"
                    >
                      <Trash2 className="h-4 w-4" />
                    </button>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {(modal !== 'none') && (
        <ModalShell
          onClose={() => setModal('none')}
          labelledBy="subscription-modal-title"
          // The templates dialog stacks on top of this one; without this
          // a single Esc would collapse both and discard the half-filled
          // subscription form.
          closeOnEscape={!templatesOpen}
        >
          <div className="w-full max-w-lg rounded-2xl bg-gray-950 border border-gray-800 p-6">
            <h2 id="subscription-modal-title" className="text-base font-semibold text-gray-100 mb-5">
              {modal === 'add' ? 'Add Subscription' : 'Edit Subscription'}
            </h2>
            <SubForm
              initial={editSub ?? undefined}
              onSave={handleSave}
              onCancel={() => setModal('none')}
              loading={create.isPending || update.isPending}
              templates={uaTemplates}
              onManageTemplates={() => setTemplatesOpen(true)}
            />
          </div>
        </ModalShell>
      )}

      {/* Opens over the subscription form so the operator can edit the
          catalogue and come back to a still-filled form. */}
      {templatesOpen && (
        <UserAgentTemplatesModal onClose={() => setTemplatesOpen(false)} />
      )}
    </div>
  )
}
