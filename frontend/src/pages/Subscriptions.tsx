import { useState } from 'react'
import { Plus, RefreshCw, Trash2, Rss, Link, Zap } from 'lucide-react'
import { InfoTip } from '@/components/InfoTip'
import { clsx } from 'clsx'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { subsApi } from '@/api/client'
import { useConfirm } from '@/components/ConfirmModal'
import { ModalShell } from '@/components/ModalShell'
import type { Subscription, SubscriptionCreate } from '@/types'

const UA_OPTIONS = [
  'v2ray', 'clash', 'sing-box',
  // Happ — distinct presets per OS. Stricter panels gate on the OS
  // segment and X-Device-Os header; pick the one your panel expects.
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
}: {
  initial?: Partial<Subscription>
  onSave: (d: SubscriptionCreate) => void
  onCancel: () => void
  loading?: boolean
}) {
  const [form, setForm] = useState({
    name: initial?.name ?? '',
    url: initial?.url ?? '',
    ua: initial?.ua ?? 'v2ray',
    custom_ua: initial?.custom_ua ?? '',
    filter_regex: initial?.filter_regex ?? '',
    auto_update: initial?.auto_update ?? true,
    update_interval: initial?.update_interval ?? 86400,
    enabled: initial?.enabled ?? true,
  })
  const set = <K extends keyof typeof form>(k: K, v: typeof form[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

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
          className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
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
          className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-none"
        />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
            User-Agent
            <InfoTip className="ml-0.5" text="User agent sent when fetching the subscription. Providers serve different formats based on UA. v2ray → base64 URI list, clash → YAML, sing-box → JSON config, happ/streisand → bypass some CDN protections, chrome → full browser UA for strict CDN." />
          </label>
          <select
            value={form.ua}
            onChange={(e) => set('ua', e.target.value)}
            disabled={!!form.custom_ua.trim()}
            className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none disabled:opacity-50"
          >
            {UA_OPTIONS.map((u) => <option key={u} value={u}>{u}</option>)}
          </select>
        </div>
        <div>
          <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
            Update Interval
            <InfoTip className="ml-0.5" text="How often to auto-update. The scheduler checks every minute and refreshes subscriptions when the interval has elapsed." />
          </label>
          <div className="flex rounded overflow-hidden border border-gray-700">
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
          className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-none"
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
          className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-none"
        />
      </div>
      <div className="flex gap-4">
        <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
          <input
            type="checkbox"
            checked={form.auto_update}
            onChange={(e) => set('auto_update', e.target.checked)}
            className="rounded border-gray-600 bg-gray-800 text-brand-500"
          />
          Auto-update
        </label>
        <label className="flex items-center gap-2 text-sm text-gray-300 cursor-pointer">
          <input
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => set('enabled', e.target.checked)}
            className="rounded border-gray-600 bg-gray-800 text-brand-500"
          />
          Enabled
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
  const qc = useQueryClient()
  const confirm = useConfirm()

  const { data: subs = [] } = useQuery({
    queryKey: ['subscriptions'],
    queryFn: () => subsApi.list(),
  })

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
    // Backend `refresh` blocks until the fetch+parse+upsert is fully
    // committed, so by the time this resolves the DB is consistent. The
    // previous `setTimeout(..., 2000)` was always either too early (fast
    // backend → stale UI for 1.8s) or too late (slow backend → invalidate
    // fired before data was ready).
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['subscriptions'] })
      qc.invalidateQueries({ queryKey: ['nodes'] })
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
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-100">Subscriptions</h1>
        <div className="flex items-center gap-2">
          {subs.length > 0 && (
            <button
              onClick={refreshAll}
              disabled={refresh.isPending}
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
              className="w-full rounded-lg bg-gray-800 border border-gray-700 pl-9 pr-3 py-2 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-none"
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
            return (
              <div key={sub.id} className={clsx(
                'rounded-xl border bg-gray-900 p-4 transition-colors',
                sub.enabled ? 'border-gray-800' : 'border-gray-800/50 opacity-60',
              )}>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-start gap-3 min-w-0">
                    <Rss className="h-5 w-5 text-brand-400 flex-shrink-0 mt-0.5" />
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-gray-100">{sub.name}</div>
                      <div className="text-xs text-gray-500 font-mono mt-0.5 truncate">{sub.url}</div>
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 text-xs text-gray-600">
                        <span className="text-gray-400 font-medium">{sub.node_count} nodes</span>
                        <span>UA: {sub.ua}</span>
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
                        <div className="mt-1.5 text-xs text-red-400 bg-red-950/30 border border-red-800/40 rounded px-2 py-1 font-mono">
                          Error: {sub.last_error}
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    <button
                      onClick={() => refresh.mutate(sub.id)}
                      disabled={refresh.isPending && refresh.variables === sub.id}
                      title="Refresh now"
                      className="rounded p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
                    >
                      <RefreshCw className={clsx('h-4 w-4', refresh.isPending && refresh.variables === sub.id && 'animate-spin')} />
                    </button>
                    <button
                      onClick={() => { setEditSub(sub); setModal('edit') }}
                      title="Edit"
                      className="rounded p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
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
                      className="rounded p-1.5 text-gray-500 hover:text-red-400 hover:bg-gray-800 transition-colors"
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
        <ModalShell onClose={() => setModal('none')} labelledBy="subscription-modal-title">
          <div className="w-full max-w-lg rounded-2xl bg-gray-950 border border-gray-800 p-6">
            <h2 id="subscription-modal-title" className="text-base font-semibold text-gray-100 mb-5">
              {modal === 'add' ? 'Add Subscription' : 'Edit Subscription'}
            </h2>
            <SubForm
              initial={editSub ?? undefined}
              onSave={handleSave}
              onCancel={() => setModal('none')}
              loading={create.isPending || update.isPending}
            />
          </div>
        </ModalShell>
      )}
    </div>
  )
}
