import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { balancersApi } from '@/api/client'
import type { RoutingRule, RoutingRuleCreate, RuleType } from '@/types'

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
  const { data: balancerGroups = [] } = useQuery({
    queryKey: ['balancers'],
    queryFn: () => balancersApi.list(),
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
      match_value: form.match_value,
      action: action as RoutingRuleCreate['action'],
      order: form.order,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">Name</label>
          <input
            value={form.name}
            onChange={(e) => set('name', e.target.value)}
            required
            autoFocus
            className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
            placeholder="Block China IPs"
          />
        </div>
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">Priority (lower = first)</label>
          <input
            type="number"
            value={form.order}
            onChange={(e) => set('order', Number(e.target.value))}
            className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
          />
        </div>
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1">Rule Type</label>
        <select
          value={form.rule_type}
          onChange={(e) => set('rule_type', e.target.value as RuleType)}
          className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
        >
          {RULE_TYPES.map((t) => (
            <option key={t} value={t}>{RULE_TYPE_LABELS[t]}</option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1">
          Match Value
          <span className="ml-2 font-normal text-gray-600">({RULE_TYPE_HINTS[form.rule_type]})</span>
        </label>
        <textarea
          value={form.match_value}
          onChange={(e) => set('match_value', e.target.value)}
          required
          rows={3}
          className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-none resize-none"
          placeholder={RULE_TYPE_HINTS[form.rule_type]}
        />
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1">Action</label>
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
          className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
        >
          {ACTIONS.map((a) => (
            <option key={a} value={a}>{a.charAt(0).toUpperCase() + a.slice(1)}</option>
          ))}
          {nodeOptions.length > 0 && (
            <option value="_node">Route to specific node…</option>
          )}
          {balancerGroups.length > 0 && (
            <option value="_balancer">Route via balancer group…</option>
          )}
        </select>
      </div>

      {isNodeAction && nodeOptions.length > 0 && (
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">Target Node</label>
          <select
            value={form.customNode}
            onChange={(e) => set('customNode', e.target.value)}
            className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
          >
            <option value="">Select node…</option>
            {nodeOptions.map((n) => (
              <option key={n.id} value={n.id}>{n.name}</option>
            ))}
          </select>
        </div>
      )}

      {isBalancerAction && balancerGroups.length > 0 && (
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">Target Balancer Group</label>
          <select
            value={form.customBalancer}
            onChange={(e) => set('customBalancer', e.target.value)}
            className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
          >
            <option value="">Select balancer group…</option>
            {balancerGroups.map((bg) => (
              <option key={bg.id} value={bg.id}>{bg.name} ({bg.node_ids.length} nodes, {bg.strategy})</option>
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
          className="rounded border-gray-600 bg-gray-800 text-brand-500"
        />
        <label htmlFor="rule-enabled" className="text-sm text-gray-300">Enabled</label>
      </div>

      <div className="flex justify-end gap-3 pt-2 border-t border-gray-800">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={loading}
          className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors"
        >
          {loading ? 'Saving…' : 'Save Rule'}
        </button>
      </div>
    </form>
  )
}
