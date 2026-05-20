import { useState, useEffect, useRef } from 'react'
import { Plus, Pencil, Trash2, ToggleLeft, ToggleRight, GripVertical, Upload, Zap, FileUp, FileDown, HelpCircle, AlertTriangle } from 'lucide-react'
import { clsx } from 'clsx'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { routingApi } from '@/api/client'
import { useNodes } from '@/hooks/useNodes'
import { RuleEditor } from '@/components/RuleEditor'
import { useConfirm } from '@/components/ConfirmModal'
import { ModalShell } from '@/components/ModalShell'
import { useT } from '@/hooks/useT'
import { useAppStore } from '@/store'
import { useSystemSettings } from '@/hooks/useSystem'
import {
  GEO_PROFILES, detectActiveProfile, getProfile,
  type QuickAddPreset,
} from '@/lib/geoProfiles'
import type { RoutingRule, RoutingRuleCreate, RuleType, BulkRuleCreate, V2RayRule } from '@/types'

type Tab = 'rules' | 'devices'
type Modal = 'none' | 'add' | 'edit' | 'bulk' | 'import-v2ray' | 'help'

const RULE_TYPE_COLORS: Record<RuleType, string> = {
  mac:      'bg-purple-900/60 text-purple-300',
  src_ip:   'bg-blue-900/60 text-blue-300',
  dst_ip:   'bg-cyan-900/60 text-cyan-300',
  domain:   'bg-green-900/60 text-green-300',
  port:     'bg-yellow-900/60 text-yellow-300',
  protocol: 'bg-orange-900/60 text-orange-300',
  geoip:    'bg-red-900/60 text-red-300',
  geosite:  'bg-pink-900/60 text-pink-300',
}

const ACTION_COLORS: Record<string, string> = {
  proxy:  'text-brand-400',
  direct: 'text-green-400',
  block:  'text-red-400',
}

const ACTION_BG_COLORS: Record<string, string> = {
  proxy:  'bg-brand-900/40 text-brand-300',
  direct: 'bg-green-900/40 text-green-300',
  block:  'bg-red-900/40 text-red-300',
}

// Quick-Add presets are now provided by the active geo profile (see
// `frontend/src/lib/geoProfiles.ts`). We pick the registry list at render
// time based on which profile is detected from current settings — this
// way runetfreedom-only presets (e.g. "Proxy blocked-in-RU sites") only
// show when runetfreedom is the active source. Falls back to Loyalsoldier
// presets when settings are 'custom'.

export function Routing() {
  const t = useT()
  const lang = useAppStore((s) => s.lang)
  const confirm = useConfirm()
  const [tab, setTab] = useState<Tab>('rules')
  const [modal, setModal] = useState<Modal>('none')
  const [editRule, setEditRule] = useState<RoutingRule | null>(null)
  const [dragId, setDragId] = useState<number | null>(null)
  const [expandedRules, setExpandedRules] = useState<Set<number>>(new Set())
  const [viewMode, setViewMode] = useState<'priority' | 'action'>('priority')
  const [showPresets, setShowPresets] = useState(false)

  // Bulk import state
  const [bulkRuleType, setBulkRuleType] = useState<RuleType>('domain')
  const [bulkAction, setBulkAction] = useState<string>('proxy')
  const [bulkValues, setBulkValues] = useState('')

  // V2Ray import state
  const [v2rayRules, setV2rayRules] = useState<V2RayRule[]>([])
  const [v2rayMode, setV2rayMode] = useState<'as_is' | 'invert'>('as_is')
  const [v2rayClear, setV2rayClear] = useState(false)
  const [v2rayFileName, setV2rayFileName] = useState('')

  // Multi-select for batch delete
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set())
  const toggleSelect = (id: number) => setSelectedIds(prev => {
    const next = new Set(prev)
    next.has(id) ? next.delete(id) : next.add(id)
    return next
  })
  const selectAll = () => setSelectedIds(new Set(rules.map(r => r.id)))
  const selectNone = () => setSelectedIds(new Set())

  const presetsRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const qc = useQueryClient()

  const { data: rules = [] } = useQuery({
    queryKey: ['routing', 'rules'],
    queryFn: () => routingApi.listRules(),
  })
  const { data: devices = [] } = useQuery({
    queryKey: ['routing', 'devices'],
    queryFn: () => routingApi.listDevices(),
    enabled: tab === 'devices',
    refetchInterval: 30_000,
  })
  const { data: nodes = [] } = useNodes()
  const { data: sysSettings } = useSystemSettings()

  // Quick-Add presets come from the geo profile that matches the
  // current GeoData URLs. If the user has customised URLs (no profile
  // match), fall back to Loyalsoldier's presets — those only reference
  // categories that are also present upstream (`cn`, `category-ru`,
  // `tld-ru`, `category-ads-all`, etc.) so the fallback is safe.
  const activeProfileId = detectActiveProfile({
    geoip_url: sysSettings?.geoip_url,
    geosite_url: sysSettings?.geosite_url,
    geoip_mmdb_url: sysSettings?.geoip_mmdb_url,
  })
  const activeProfile =
    activeProfileId === 'custom'
      ? GEO_PROFILES[0]  // Loyalsoldier — universally compatible defaults
      : (getProfile(activeProfileId) ?? GEO_PROFILES[0])
  const PRESETS = activeProfile.presets

  const createRule = useMutation({
    mutationFn: (data: RoutingRuleCreate) => routingApi.createRule(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['routing'] }); setModal('none') },
  })
  const updateRule = useMutation({
    mutationFn: ({ id, data }: { id: number; data: Partial<RoutingRuleCreate> }) =>
      routingApi.updateRule(id, data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['routing'] }); setModal('none') },
  })
  const deleteRule = useMutation({
    mutationFn: (id: number) => routingApi.deleteRule(id),
    onSuccess: (_data, deletedId) => {
      qc.invalidateQueries({ queryKey: ['routing'] })
      setSelectedIds(prev => { const n = new Set(prev); n.delete(deletedId); return n })
    },
  })
  const deleteAll = useMutation({
    mutationFn: () => routingApi.deleteAllRules(),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['routing'] }); setSelectedIds(new Set()) },
  })
  const deleteBatch = useMutation({
    mutationFn: (ids: number[]) => routingApi.deleteBatchRules(ids),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['routing'] }); setSelectedIds(new Set()) },
  })
  const bulkCreate = useMutation({
    mutationFn: (data: BulkRuleCreate) => routingApi.bulkCreate(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['routing'] })
      setModal('none')
      setBulkValues('')
    },
  })
  const v2rayImport = useMutation({
    mutationFn: (data: { rules: V2RayRule[]; mode: 'as_is' | 'invert'; clear_existing: boolean }) =>
      routingApi.importV2ray(data),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ['routing'] })
      setModal('none')
      setV2rayRules([])
      setV2rayFileName('')
      if (fileInputRef.current) fileInputRef.current.value = ''
      alert(`Imported ${result.imported} rules, skipped ${result.skipped}`)
    },
  })

  const handleV2rayFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    setV2rayFileName(file.name)
    const reader = new FileReader()
    reader.onload = (ev) => {
      try {
        const data = JSON.parse(ev.target?.result as string)
        setV2rayRules(Array.isArray(data) ? data : data.rules || [])
      } catch {
        alert('Invalid JSON file')
      }
    }
    reader.readAsText(file)
  }

  const nodeOptions = nodes.map((n) => ({ id: n.id, name: n.name }))

  // Close presets dropdown on outside click
  useEffect(() => {
    if (!showPresets) return
    const handler = (e: MouseEvent) => {
      if (presetsRef.current && !presetsRef.current.contains(e.target as HTMLElement)) {
        setShowPresets(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showPresets])

  const handleRuleDrop = (targetId: number) => {
    if (dragId === null || dragId === targetId) return
    const ids = rules.map((r) => r.id)
    const fromIndex = ids.indexOf(dragId)
    const toIndex = ids.indexOf(targetId)
    ids.splice(fromIndex, 1)
    ids.splice(toIndex, 0, dragId)
    setDragId(null)
    routingApi.reorderRules(ids).then(() => qc.invalidateQueries({ queryKey: ['routing', 'rules'] }))
  }

  const handleSave = (data: RoutingRuleCreate) => {
    if (editRule) {
      updateRule.mutate({ id: editRule.id, data })
    } else {
      createRule.mutate(data)
    }
  }

  // Export all current rules as a V2Ray-style JSON array — same schema we
  // read on import. Useful as a backup, or to share a curated ruleset with
  // another PiTun instance / a Shadowrocket config. The generated file
  // round-trips through the "Import JSON" dialog.
  //
  // Field mapping:
  //   name         → remarks
  //   enabled      → enabled
  //   action       → outboundTag ("proxy"|"direct"|"block") or balancerTag
  //                  (node:<id> actions are not in v2ray's vocabulary; they
  //                   export as-is and will be skipped by a stock importer)
  //   rule_type    → which payload field is populated:
  //     domain|geosite → domain: [match_value...]
  //     dst_ip|geoip   → ip:     [match_value...]
  //     src_ip         → source: [match_value...]
  //     port           → port:   "match_value"
  //     protocol       → protocol: [match_value...]
  //     mac            → attrs:  {mac: "..."}   (PiTun-specific, ignored by stock v2ray)
  const handleExport = () => {
    const exported = rules.map((r) => {
      const values = r.match_value.split(',').map(v => v.trim()).filter(Boolean)
      const entry: Record<string, unknown> = {
        type: 'field',
        remarks: r.name,
        enabled: r.enabled,
      }
      // Map action
      if (r.action.startsWith('balancer:')) {
        entry.balancerTag = `balancer-${r.action.split(':', 2)[1]}`
      } else if (r.action.startsWith('node:')) {
        entry.outboundTag = `node-${r.action.split(':', 2)[1]}`
      } else {
        entry.outboundTag = r.action
      }
      // Map payload per rule_type
      switch (r.rule_type) {
        case 'domain':
        case 'geosite':
          entry.domain = values.map(v => r.rule_type === 'geosite' ? `geosite:${v}` : v)
          break
        case 'dst_ip':
          entry.ip = values
          break
        case 'geoip':
          entry.ip = values.map(v => `geoip:${v}`)
          break
        case 'src_ip':
          entry.source = values
          break
        case 'port':
          entry.port = r.match_value
          break
        case 'protocol':
          entry.protocol = values
          break
        case 'mac':
          entry.attrs = { mac: r.match_value }
          break
      }
      return entry
    })

    const json = JSON.stringify(exported, null, 2)
    const blob = new Blob([json], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    const date = new Date().toISOString().slice(0, 10)
    a.download = `pitun-routing-rules-${date}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const handlePreset = async (preset: QuickAddPreset) => {
    setShowPresets(false)
    // A preset can emit multiple rules (e.g. "Bypass RU sites" lays
    // down BOTH a geosite rule for category-ru/tld-ru AND a domain
    // rule for *.su / *.рф). Post sequentially — POST /routing/rules
    // is fast enough that fanning out via Promise.all isn't worth the
    // complexity, and serial keeps the order predictable.
    const baseOrder = rules.length
    for (let i = 0; i < preset.rules.length; i++) {
      const r = preset.rules[i]
      const ruleName =
        preset.rules.length === 1
          ? preset.label
          : `${preset.label} (${i + 1}/${preset.rules.length})`
      await createRule.mutateAsync({
        name: ruleName,
        enabled: true,
        rule_type: r.rule_type,
        match_value: r.match_value,
        action: r.action as RoutingRuleCreate['action'],
        order: baseOrder + i,
      })
    }
  }

  const handleBulkImport = () => {
    bulkCreate.mutate({
      rule_type: bulkRuleType,
      action: bulkAction,
      values: bulkValues,
    })
  }

  const bulkLineCount = bulkValues.split('\n').filter((l) => l.trim()).length

  const toggleExpanded = (id: number) => {
    setExpandedRules((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const renderMatchValue = (rule: RoutingRule) => {
    const parts = rule.match_value.split(',')
    const isExpanded = expandedRules.has(rule.id)

    if (isExpanded) {
      return (
        <span
          className="text-xs text-gray-400 font-mono cursor-pointer"
          onClick={() => toggleExpanded(rule.id)}
        >
          {parts.map((v, i) => (
            <span key={i}>
              {v.trim()}
              {i < parts.length - 1 && <br />}
            </span>
          ))}
        </span>
      )
    }

    if (parts.length <= 3) {
      return (
        <span className="text-xs text-gray-500 font-mono truncate max-w-xs">
          {parts.map((v) => v.trim()).join(', ')}
        </span>
      )
    }

    return (
      <span
        className="text-xs text-gray-500 font-mono cursor-pointer flex items-center gap-1.5"
        onClick={() => toggleExpanded(rule.id)}
      >
        <span className="truncate">{parts.slice(0, 2).map((v) => v.trim()).join(', ')}</span>
        <span className="shrink-0 rounded bg-gray-800 px-1.5 py-0.5 text-[10px] text-gray-400">
          +{parts.length - 2} more
        </span>
      </span>
    )
  }

  // Group rules by action for "By Action" view
  const groupedRules = () => {
    const groups: Record<string, RoutingRule[]> = {}
    for (const rule of rules) {
      const key = rule.action
      if (!groups[key]) groups[key] = []
      groups[key].push(rule)
    }
    // Sort within each group by order
    for (const key of Object.keys(groups)) {
      groups[key].sort((a, b) => a.order - b.order)
    }
    return groups
  }

  const renderRuleRow = (rule: RoutingRule, isDndEnabled: boolean) => (
    <div
      key={rule.id}
      draggable={isDndEnabled}
      onDragStart={isDndEnabled ? () => setDragId(rule.id) : undefined}
      // Always clear dragId on drag end — `drop` only fires on a
      // successful drop, but `dragend` covers cancel/escape/miss too.
      // Without this, a started-but-not-completed drag leaves the row
      // at opacity-50 until the next drag starts.
      onDragEnd={isDndEnabled ? () => setDragId(null) : undefined}
      onDragOver={isDndEnabled ? (e) => e.preventDefault() : undefined}
      onDrop={isDndEnabled ? () => handleRuleDrop(rule.id) : undefined}
      // Tighter horizontal gap on mobile so the checkbox + grip
      // don't eat half the row's width on a 360-wide phone.
      className={clsx(
        'flex items-center gap-1 sm:gap-2',
        isDndEnabled && dragId === rule.id && 'opacity-50',
      )}
    >
      <input
        type="checkbox"
        checked={selectedIds.has(rule.id)}
        onChange={() => toggleSelect(rule.id)}
        className="rounded border-gray-600 bg-gray-700 shrink-0 ml-1 sm:ml-0"
      />
      {isDndEnabled && (
        <div className="cursor-grab text-gray-600 hover:text-gray-400 shrink-0 hidden sm:block">
          <GripVertical className="h-4 w-4" />
        </div>
      )}
      <div
        className={clsx(
          // Tighter padding on mobile so the rule body doesn't push
          // the inline action buttons off the right edge of the card.
          'flex-1 min-w-0 rounded-xl border p-3 sm:p-4 transition-colors',
          rule.enabled
            ? 'border-gray-800 bg-gray-900'
            : 'border-gray-800/50 bg-gray-900/50 opacity-60',
        )}
      >
        {/* Mobile: stack rule meta + actions row vertically (each
            line wraps cleanly inside the card). Tablet+: original
            single-row inline layout. */}
        <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3 sm:flex-wrap">
          <span
            className={clsx(
              'rounded px-2 py-0.5 text-xs font-mono font-medium',
              RULE_TYPE_COLORS[rule.rule_type] ?? 'bg-gray-700 text-gray-300',
            )}
          >
            {rule.rule_type}
          </span>
          <span className="flex-1 text-sm text-gray-200 font-medium truncate">{rule.name}</span>
          {renderMatchValue(rule)}
          <span className={clsx('text-sm font-medium', ACTION_COLORS[rule.action] ?? 'text-gray-400')}>
            → {rule.action}
          </span>
          <div className="flex items-center gap-1 ml-auto">
            <button
              onClick={() =>
                updateRule.mutate({ id: rule.id, data: { enabled: !rule.enabled } })
              }
              title={rule.enabled ? 'Disable' : 'Enable'}
              className="rounded p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
            >
              {rule.enabled
                ? <ToggleRight className="h-4 w-4 text-brand-400" />
                : <ToggleLeft className="h-4 w-4" />}
            </button>
            <button
              onClick={() => { setEditRule(rule); setModal('edit') }}
              className="rounded p-1.5 text-gray-500 hover:text-gray-200 hover:bg-gray-800 transition-colors"
            >
              <Pencil className="h-4 w-4" />
            </button>
            <button
              onClick={async () => {
                const ok = await confirm({
                  title: `Delete rule "${rule.name}"?`,
                  confirmLabel: 'Delete',
                  danger: true,
                })
                if (ok) deleteRule.mutate(rule.id)
              }}
              className="rounded p-1.5 text-gray-500 hover:text-red-400 hover:bg-gray-800 transition-colors"
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  )

  // Routing rules ONLY apply when proxy mode is `rules`. In `global`
  // mode every connection is force-routed through the active node
  // regardless of what the operator typed here; in `bypass` mode
  // every connection goes direct. The audit on `config_gen.py:824` in
  // 1.3.4 verified the rules-loop only runs in the `rules` branch —
  // so on the other two modes the page renders an active edit
  // surface that has no effect. Show a banner so the operator sees
  // the gotcha before they save changes that won't take effect.
  const proxyMode = sysSettings?.mode
  const rulesIgnored = proxyMode != null && proxyMode !== 'rules'

  return (
    <div className="p-4 sm:p-6 space-y-5">
      {rulesIgnored && tab === 'rules' && (
        <div className="rounded-lg border border-amber-900/60 bg-amber-900/15 px-4 py-3 text-amber-200 text-sm flex items-start gap-3">
          <AlertTriangle className="h-5 w-5 shrink-0 mt-0.5" />
          <div>
            <div className="font-semibold mb-0.5">
              Routing rules are disabled in {proxyMode === 'global' ? '"Global"' : '"Bypass / Direct"'} mode
            </div>
            <div className="text-amber-300/80 text-[13px]">
              {proxyMode === 'global'
                ? 'All traffic is force-routed through the active node. Rules below are still saved but won\'t take effect until you switch Proxy Mode to "Rules" on the Dashboard. The `Bypass local networks` rule in particular has NO effect in Global — use the "Bypass private CIDRs" toggle on the Dashboard instead.'
                : 'All traffic goes direct (no proxy). Rules below are still saved but won\'t take effect until you switch Proxy Mode to "Rules" on the Dashboard.'}
            </div>
          </div>
        </div>
      )}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <h1 className="text-xl font-bold text-gray-100">Routing</h1>
        {tab === 'rules' && (
          // Action buttons wrap to multiple rows on phones — same
          // pattern as the Servers + Nodes pages so the discovery
          // affordance stays consistent (a horizontal-scroll strip
          // wasn't obvious enough that the row was scrollable).
          <div className="flex items-center gap-2 flex-wrap">
            {/* Quick Add Presets */}
            <div className="relative" ref={presetsRef}>
              <button
                onClick={() => setShowPresets(!showPresets)}
                className="flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm font-medium text-gray-300 hover:bg-gray-700 transition-colors"
              >
                <Zap className="h-4 w-4" />
                Quick Add
              </button>
              {showPresets && (
                <div className="absolute right-0 top-full mt-1 z-30 w-80 rounded-xl border border-gray-700 bg-gray-900 shadow-xl overflow-hidden">
                  {/* Profile indicator at top of dropdown — gives the
                      user context on which category set is in use, plus
                      a quick hop to the Geo profile picker if they want
                      to switch (e.g. they expected runetfreedom-only
                      "blocked-in-RU" preset and don't see it). */}
                  <div className="border-b border-gray-800 bg-gray-950/60 px-3 py-2 text-[11px] text-gray-500 flex items-center justify-between">
                    <span>
                      Profile:{' '}
                      <span className="text-gray-300">{activeProfile.label}</span>
                      {activeProfileId === 'custom' && (
                        <span className="text-yellow-500/80"> (custom URLs — using Loyalsoldier presets)</span>
                      )}
                    </span>
                    <a
                      href="/geodata"
                      className="text-brand-400 hover:text-brand-300"
                      title="Switch geo profile in GeoData"
                    >
                      change
                    </a>
                  </div>
                  {PRESETS.map((preset) => {
                    // Action badge shows the FIRST rule's action — for
                    // multi-rule presets every rule typically shares the
                    // same action ("Bypass RU sites" → both rules direct).
                    const primaryAction = preset.rules[0]?.action ?? 'proxy'
                    return (
                      <button
                        key={preset.label}
                        onClick={() => handlePreset(preset)}
                        className="block w-full px-3 py-2.5 text-sm text-gray-300 hover:bg-gray-800 transition-colors text-left border-b border-gray-800 last:border-b-0"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span>{preset.label}</span>
                          <span className={clsx('rounded px-1.5 py-0.5 text-[10px] font-medium', ACTION_BG_COLORS[primaryAction] ?? 'bg-gray-700 text-gray-300')}>
                            {primaryAction}
                            {preset.rules.length > 1 && (
                              <span className="opacity-70"> ×{preset.rules.length}</span>
                            )}
                          </span>
                        </div>
                        {preset.hint && (
                          <div className="mt-0.5 text-[11px] text-gray-500">{preset.hint}</div>
                        )}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
            {/* Bulk Import */}
            <button
              onClick={() => setModal('bulk')}
              className="flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm font-medium text-gray-300 hover:bg-gray-700 transition-colors"
            >
              <Upload className="h-4 w-4" />
              Bulk
            </button>
            {/* V2Ray Import */}
            <button
              onClick={() => setModal('import-v2ray')}
              className="flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm font-medium text-gray-300 hover:bg-gray-700 transition-colors"
            >
              <FileUp className="h-4 w-4" />
              Import JSON
            </button>
            {/* V2Ray Export — downloads current rules as a JSON array in
                the same shape as the Import dialog expects (round-trip). */}
            <button
              onClick={handleExport}
              disabled={rules.length === 0}
              title={rules.length === 0 ? 'No rules to export' : 'Export rules as V2Ray JSON'}
              className="flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm font-medium text-gray-300 hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              <FileDown className="h-4 w-4" />
              Export JSON
            </button>
            {/* Help */}
            <button
              onClick={() => setModal('help')}
              title="Rule syntax help"
              className="flex items-center gap-1.5 rounded-lg border border-gray-700 bg-gray-800 px-3 py-2 text-sm font-medium text-gray-300 hover:bg-gray-700 transition-colors"
            >
              <HelpCircle className="h-4 w-4" />
              Help
            </button>
            {/* Add Rule */}
            <button
              onClick={() => { setEditRule(null); setModal('add') }}
              className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors"
            >
              <Plus className="h-4 w-4" />
              Add Rule
            </button>
          </div>
        )}
      </div>

      {/* Batch actions bar */}
      {tab === 'rules' && rules.length > 0 && (
        <div className="flex items-center gap-3 flex-wrap">
          <label className="flex items-center gap-1.5 cursor-pointer text-xs text-gray-500">
            <input
              type="checkbox"
              checked={selectedIds.size === rules.length && rules.length > 0}
              onChange={() => selectedIds.size === rules.length ? selectNone() : selectAll()}
              className="rounded border-gray-600 bg-gray-700"
            />
            {selectedIds.size > 0 ? `${selectedIds.size} selected` : 'Select all'}
          </label>
          {selectedIds.size > 0 && (
            <button
              onClick={async () => {
                const ok = await confirm({
                  title: `Delete ${selectedIds.size} selected rules?`,
                  confirmLabel: 'Delete',
                  danger: true,
                })
                if (ok) deleteBatch.mutate([...selectedIds])
              }}
              disabled={deleteBatch.isPending}
              className="flex items-center gap-1 rounded-lg bg-red-900/50 border border-red-700/50 px-2.5 py-1 text-xs text-red-300 hover:bg-red-800/50 transition-colors"
            >
              <Trash2 className="h-3 w-3" />
              Delete selected
            </button>
          )}
          <button
            onClick={async () => {
              const ok = await confirm({
                title: `Delete ALL ${rules.length} routing rules?`,
                body: 'Cannot be undone. Routing falls back to default behaviour until you add rules again.',
                confirmLabel: 'Delete all',
                danger: true,
              })
              if (ok) deleteAll.mutate()
            }}
            disabled={deleteAll.isPending}
            className="flex items-center gap-1 rounded-lg border border-gray-700 px-2.5 py-1 text-xs text-gray-500 hover:text-red-400 hover:border-red-700/50 transition-colors ml-auto"
          >
            <Trash2 className="h-3 w-3" />
            Delete all ({rules.length})
          </button>
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-gray-800">
        {([['rules', 'Rules'], ['devices', 'Devices (ARP)']] as const).map(([t, label]) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={clsx(
              'flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 -mb-px transition-colors',
              tab === t
                ? 'border-brand-500 text-brand-400'
                : 'border-transparent text-gray-500 hover:text-gray-300',
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Rules tab */}
      {tab === 'rules' && (
        <div className="space-y-2">
          {/* Self-healing inbox: rules disabled by config_gen because
              they referenced missing geosite/geoip tags (since v1.2.7).
              Banner stays until the admin acts on each entry or
              dismisses the whole list. */}
          <AutoDisabledBanner />

          {/* View toggle */}
          {rules.length > 0 && (
            <div className="flex justify-end">
              <div className="inline-flex rounded-lg border border-gray-700 overflow-hidden">
                <button
                  onClick={() => setViewMode('priority')}
                  className={clsx(
                    'px-3 py-1.5 text-xs font-medium transition-colors',
                    viewMode === 'priority'
                      ? 'bg-gray-700 text-gray-100'
                      : 'bg-gray-900 text-gray-500 hover:text-gray-300',
                  )}
                >
                  By Priority
                </button>
                <button
                  onClick={() => setViewMode('action')}
                  className={clsx(
                    'px-3 py-1.5 text-xs font-medium transition-colors',
                    viewMode === 'action'
                      ? 'bg-gray-700 text-gray-100'
                      : 'bg-gray-900 text-gray-500 hover:text-gray-300',
                  )}
                >
                  By Action
                </button>
              </div>
            </div>
          )}

          {rules.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              No routing rules. Add rules to control traffic flow.
            </div>
          ) : viewMode === 'priority' ? (
            rules.map((rule) => renderRuleRow(rule, true))
          ) : (
            Object.entries(groupedRules()).map(([action, groupRules]) => (
              <div key={action} className="space-y-2">
                <div className="flex items-center gap-2 pt-3 pb-1">
                  <span className={clsx('text-sm font-semibold', ACTION_COLORS[action] ?? 'text-gray-400')}>
                    → {action}
                  </span>
                  <span className="text-xs text-gray-600">
                    ({groupRules.length} {groupRules.length === 1 ? 'rule' : 'rules'})
                  </span>
                </div>
                {groupRules.map((rule) => renderRuleRow(rule, false))}
              </div>
            ))
          )}
        </div>
      )}

      {/* Devices tab */}
      {tab === 'devices' && (
        <div className="space-y-2">
          <p className="text-xs text-gray-500">
            Devices from ARP table. Assign routing action by creating a MAC or Source IP rule.
          </p>
          {devices.length === 0 ? (
            <div className="text-center py-8 text-gray-500">No devices in ARP table</div>
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-800 text-left text-xs text-gray-500">
                  <th className="pb-2 font-medium">IP</th>
                  <th className="pb-2 font-medium">MAC</th>
                  <th className="pb-2 font-medium">Rule Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/50">
                {devices.map((d) => (
                  <tr key={d.mac} className="hover:bg-gray-900/50">
                    <td className="py-2.5 font-mono text-gray-300">{d.ip}</td>
                    <td className="py-2.5 font-mono text-gray-400">{d.mac}</td>
                    <td className="py-2.5">
                      {d.rule_action ? (
                        <span className={clsx('text-sm font-medium', ACTION_COLORS[d.rule_action] ?? 'text-gray-400')}>
                          {d.rule_action}
                        </span>
                      ) : (
                        <span className="text-gray-600 text-xs">default</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {/* Add / Edit Modal */}
      {(modal === 'add' || modal === 'edit') && (
        <ModalShell onClose={() => setModal('none')} labelledBy="rule-modal-title">
          {/* `w-full` collapses under ModalShell's stopPropagation
              wrapper (flex shrinks to natural width), giving a
              skinny ~360px dialog. `w-[min(92vw,48rem)]` keeps a
              sane desktop max (768px) but always at least 92% of
              the viewport on phones. */}
          <div className="w-[min(92vw,48rem)] max-h-[90vh] overflow-y-auto rounded-2xl bg-gray-950 border border-gray-800 p-6">
            <h2 id="rule-modal-title" className="text-base font-semibold text-gray-100 mb-5">
              {modal === 'add' ? 'Add Routing Rule' : 'Edit Routing Rule'}
            </h2>
            <RuleEditor
              initial={editRule ?? undefined}
              nodeOptions={nodeOptions}
              onSave={handleSave}
              onCancel={() => setModal('none')}
              loading={createRule.isPending || updateRule.isPending}
            />
          </div>
        </ModalShell>
      )}

      {/* Bulk Import Modal */}
      {modal === 'bulk' && (
        <ModalShell onClose={() => setModal('none')} labelledBy="bulk-modal-title">
          <div className="w-full max-w-xl max-h-[90vh] overflow-y-auto rounded-2xl bg-gray-950 border border-gray-800 p-6">
            <h2 id="bulk-modal-title" className="text-base font-semibold text-gray-100 mb-5">Bulk Import Rules</h2>
            <div className="space-y-4">
              {/* Rule type */}
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1.5">Rule Type</label>
                <select
                  value={bulkRuleType}
                  onChange={(e) => setBulkRuleType(e.target.value as RuleType)}
                  className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200 focus:border-brand-500 focus:outline-none"
                >
                  {(['mac', 'src_ip', 'dst_ip', 'domain', 'port', 'protocol', 'geoip', 'geosite'] as RuleType[]).map((t) => (
                    <option key={t} value={t}>{t}</option>
                  ))}
                </select>
              </div>
              {/* Action */}
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1.5">Action</label>
                <select
                  value={bulkAction}
                  onChange={(e) => setBulkAction(e.target.value)}
                  className="w-full rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200 focus:border-brand-500 focus:outline-none"
                >
                  <option value="proxy">proxy</option>
                  <option value="direct">direct</option>
                  <option value="block">block</option>
                </select>
              </div>
              {/* Values textarea */}
              <div>
                <label className="block text-sm font-medium text-gray-400 mb-1.5">Values</label>
                <textarea
                  value={bulkValues}
                  onChange={(e) => setBulkValues(e.target.value)}
                  placeholder={'Paste values, one per line\nExamples:\nnetflix.com\nyoutube.com\nspotify.com'}
                  className="w-full h-48 rounded-lg border border-gray-700 bg-gray-900 px-3 py-2 text-sm text-gray-200 font-mono placeholder-gray-600 focus:border-brand-500 focus:outline-none resize-none"
                />
              </div>
              {/* Preview */}
              <p className="text-xs text-gray-500">
                Will create 1 rule with {bulkLineCount} {bulkLineCount === 1 ? 'value' : 'values'}
              </p>
              {/* Actions */}
              <div className="flex justify-end gap-3 pt-2">
                <button
                  onClick={() => setModal('none')}
                  className="rounded-lg border border-gray-700 bg-gray-800 px-4 py-2 text-sm font-medium text-gray-300 hover:bg-gray-700 transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={handleBulkImport}
                  disabled={bulkLineCount === 0 || bulkCreate.isPending}
                  className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <Upload className="h-4 w-4" />
                  {bulkCreate.isPending ? 'Importing...' : 'Import'}
                </button>
              </div>
            </div>
          </div>
        </ModalShell>
      )}

      {/* V2Ray JSON Import Modal */}
      {modal === 'import-v2ray' && (
        <ModalShell onClose={() => setModal('none')} labelledBy="v2ray-modal-title">
          <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-2xl bg-gray-950 border border-gray-800 p-6">
            <h2 id="v2ray-modal-title" className="text-base font-semibold text-gray-100 mb-5">Import V2Ray / Shadowrocket Rules</h2>
            <div className="space-y-4">
              {/* File upload */}
              <div>
                <label className="block text-xs text-gray-500 mb-1.5">JSON File (V2RayN routing format)</label>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".json"
                  onChange={handleV2rayFile}
                  className="w-full text-sm text-gray-400 file:mr-3 file:rounded-lg file:border-0 file:bg-gray-800 file:px-3 file:py-2 file:text-sm file:text-gray-300 hover:file:bg-gray-700"
                />
                {v2rayFileName && (
                  <p className="text-xs text-gray-500 mt-1">{v2rayFileName} — {v2rayRules.length} rules found</p>
                )}
              </div>

              {/* Mode selector */}
              <div>
                <label className="block text-xs text-gray-500 mb-1.5">Import Mode</label>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    onClick={() => setV2rayMode('as_is')}
                    className={clsx(
                      'rounded-lg border p-3 text-left text-xs transition-all',
                      v2rayMode === 'as_is'
                        ? 'border-brand-600 bg-brand-900/20 text-brand-300'
                        : 'border-gray-700 bg-gray-900 text-gray-400 hover:border-gray-600',
                    )}
                  >
                    <div className="font-medium text-sm mb-0.5">As Is (Whitelist)</div>
                    <div className="opacity-70">proxy→proxy, direct→direct. Only listed domains through VPN</div>
                  </button>
                  <button
                    onClick={() => setV2rayMode('invert')}
                    className={clsx(
                      'rounded-lg border p-3 text-left text-xs transition-all',
                      v2rayMode === 'invert'
                        ? 'border-brand-600 bg-brand-900/20 text-brand-300'
                        : 'border-gray-700 bg-gray-900 text-gray-400 hover:border-gray-600',
                    )}
                  >
                    <div className="font-medium text-sm mb-0.5">Inverted (Blacklist)</div>
                    <div className="opacity-70">proxy↔direct swapped. Everything through VPN except listed</div>
                  </button>
                </div>
              </div>

              {/* Clear existing */}
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={v2rayClear}
                  onChange={(e) => setV2rayClear(e.target.checked)}
                  className="rounded border-gray-600 bg-gray-700 text-red-600"
                />
                <span className="text-xs text-gray-300">Clear all existing rules before import</span>
              </label>

              {/* Preview */}
              {v2rayRules.length > 0 && (
                <div className="rounded-lg border border-gray-800 bg-gray-900/30 max-h-48 overflow-y-auto p-3">
                  <p className="text-xs text-gray-500 mb-2">Preview ({v2rayRules.length} rules):</p>
                  {v2rayRules.map((r, i) => {
                    const tag = r.outboundTag || '?'
                    const displayTag = v2rayMode === 'invert'
                      ? (tag === 'proxy' ? 'direct' : tag === 'direct' ? 'proxy' : tag)
                      : tag
                    const domains = r.domain?.length || 0
                    const ips = r.ip?.length || 0
                    const port = r.port ? 1 : 0
                    return (
                      <div key={i} className="flex items-center gap-2 text-xs py-0.5">
                        <span className={clsx(
                          'rounded px-1.5 py-0.5 font-mono text-[10px]',
                          displayTag === 'proxy' ? 'bg-brand-900/40 text-brand-300' :
                          displayTag === 'direct' ? 'bg-green-900/40 text-green-300' :
                          'bg-red-900/40 text-red-300'
                        )}>
                          {displayTag}
                        </span>
                        <span className="text-gray-400 truncate">
                          {r.remarks || `Rule #${i+1}`}
                          <span className="text-gray-600 ml-1">
                            ({domains > 0 ? `${domains} domains` : ''}{ips > 0 ? `${domains > 0 ? ', ' : ''}${ips} IPs` : ''}{port > 0 ? 'port' : ''})
                          </span>
                        </span>
                      </div>
                    )
                  })}
                </div>
              )}

              {/* Actions */}
              <div className="flex justify-end gap-3 pt-2">
                <button
                  onClick={() => { setModal('none'); setV2rayRules([]); setV2rayFileName('') }}
                  className="rounded-lg border border-gray-700 bg-gray-800 px-4 py-2 text-sm font-medium text-gray-300 hover:bg-gray-700 transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={() => v2rayImport.mutate({ rules: v2rayRules, mode: v2rayMode, clear_existing: v2rayClear })}
                  disabled={v2rayRules.length === 0 || v2rayImport.isPending}
                  className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                >
                  <FileUp className="h-4 w-4" />
                  {v2rayImport.isPending ? 'Importing...' : `Import ${v2rayRules.length} rules`}
                </button>
              </div>
            </div>
          </div>
        </ModalShell>
      )}

      {/* Help Modal */}
      {modal === 'help' && (
        <ModalShell onClose={() => setModal('none')} labelledBy="help-modal-title">
          <div className="w-full max-w-3xl max-h-[90vh] overflow-y-auto rounded-2xl bg-gray-950 border border-gray-800 p-6">
            <div className="flex items-center justify-between mb-5">
              <h2 id="help-modal-title" className="text-base font-semibold text-gray-100">{t('Routing rules — syntax & examples', 'Правила маршрутизации — синтаксис и примеры')}</h2>
              <button
                onClick={() => setModal('none')}
                className="rounded-lg px-3 py-1 text-xs text-gray-400 hover:text-gray-100 hover:bg-gray-800"
              >
                {t('Close', 'Закрыть')}
              </button>
            </div>

            <div className="space-y-6 text-sm text-gray-300">
              <section>
                <h3 className="text-gray-100 font-semibold mb-2">{t('General', 'Общее')}</h3>
                <ul className="list-disc pl-5 space-y-1 text-gray-400">
                  {lang === 'ru' ? (
                    <li>Правила оцениваются <span className="text-gray-200">сверху вниз</span>. Первое совпадение побеждает. Переупорядочивайте через drag-маркер или изменяя <span className="font-mono">Priority</span> (меньше = раньше).</li>
                  ) : (
                    <li>Rules are evaluated <span className="text-gray-200">top to bottom</span>. First match wins. Reorder via drag handle or by changing <span className="font-mono">Priority</span> (lower = earlier).</li>
                  )}
                  <li><span className="font-mono">Match Value</span> {t('can contain multiple items separated by comma or new line.', 'может содержать несколько значений, разделённых запятой или переносом строки.')}</li>
                  {lang === 'ru' ? (
                    <li>Action <span className="text-brand-400 font-medium">proxy</span> отправляет трафик через VPN, <span className="text-green-400 font-medium">direct</span> обходит VPN, <span className="text-red-400 font-medium">block</span> отбрасывает.</li>
                  ) : (
                    <li>Action <span className="text-brand-400 font-medium">proxy</span> sends traffic through VPN, <span className="text-green-400 font-medium">direct</span> bypasses VPN, <span className="text-red-400 font-medium">block</span> drops it.</li>
                  )}
                  <li>{t('You can also route to a specific node or balancer group.', 'Можно также направить на конкретную ноду или группу балансировки.')}</li>
                </ul>
              </section>

              <section>
                <h3 className="text-gray-100 font-semibold mb-2">{t('Domain rule — prefixes', 'Правила domain — префиксы')}</h3>
                <p className="text-gray-400 mb-3">
                  {lang === 'ru'
                    ? <>Для правил <span className="font-mono bg-green-900/40 text-green-300 px-1.5 py-0.5 rounded">domain</span> можно добавлять префикс к каждому значению. Префиксы определяют <em>способ</em> совпадения (семантика xray).</>
                    : <>For <span className="font-mono bg-green-900/40 text-green-300 px-1.5 py-0.5 rounded">domain</span> rules you can prefix each value. Prefixes control <em>how</em> the domain is matched (xray semantics).</>}
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-gray-800 text-left text-gray-500">
                        <th className="pb-2 pr-3 font-medium">Prefix</th>
                        <th className="pb-2 pr-3 font-medium">{t('Meaning', 'Значение')}</th>
                        <th className="pb-2 pr-3 font-medium">{t('Example', 'Пример')}</th>
                        <th className="pb-2 font-medium">{t('Matches', 'Совпадает')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-800/60 align-top">
                      <tr>
                        <td className="py-2 pr-3 font-mono text-brand-300">full:</td>
                        <td className="py-2 pr-3 text-gray-300">{t('Exact match only (no subdomains)', 'Только точное совпадение (без поддоменов)')}</td>
                        <td className="py-2 pr-3 font-mono text-gray-400">full:google.com</td>
                        <td className="py-2 text-gray-400"><span className="text-green-400">google.com</span> <span className="text-red-400">× www.google.com</span></td>
                      </tr>
                      <tr>
                        <td className="py-2 pr-3 font-mono text-brand-300">domain:</td>
                        <td className="py-2 pr-3 text-gray-300">{t('Domain + all subdomains', 'Домен + все поддомены')}</td>
                        <td className="py-2 pr-3 font-mono text-gray-400">domain:google.com</td>
                        <td className="py-2 text-gray-400"><span className="text-green-400">google.com, mail.google.com, a.b.google.com</span></td>
                      </tr>
                      <tr>
                        <td className="py-2 pr-3 font-mono text-gray-500">({t('no prefix', 'без префикса')})</td>
                        <td className="py-2 pr-3 text-gray-300">{lang === 'ru' ? <>То же что <span className="font-mono">domain:</span> — совпадение по поддоменам</> : <>Same as <span className="font-mono">domain:</span> — subdomain match</>}</td>
                        <td className="py-2 pr-3 font-mono text-gray-400">netflix.com</td>
                        <td className="py-2 text-gray-400"><span className="text-green-400">netflix.com + *.netflix.com</span></td>
                      </tr>
                      <tr>
                        <td className="py-2 pr-3 font-mono text-brand-300">keyword:</td>
                        <td className="py-2 pr-3 text-gray-300">{t('Substring anywhere in the domain', 'Подстрока в любом месте домена')}</td>
                        <td className="py-2 pr-3 font-mono text-gray-400">keyword:google</td>
                        <td className="py-2 text-gray-400"><span className="text-green-400">google.com, googleusercontent.com, fakegoogle.net</span></td>
                      </tr>
                      <tr>
                        <td className="py-2 pr-3 font-mono text-brand-300">regexp:</td>
                        <td className="py-2 pr-3 text-gray-300">{t('Regular expression (Go regex syntax)', 'Регулярное выражение (синтаксис Go regex)')}</td>
                        <td className="py-2 pr-3 font-mono text-gray-400">regexp:.*\.ru$</td>
                        <td className="py-2 text-gray-400"><span className="text-green-400">{t('any domain ending in .ru', 'любой домен, оканчивающийся на .ru')}</span></td>
                      </tr>
                      <tr>
                        <td className="py-2 pr-3 font-mono text-brand-300">geosite:</td>
                        <td className="py-2 pr-3 text-gray-300">{lang === 'ru' ? <>Категория из <span className="font-mono">geosite.dat</span></> : <>Category from <span className="font-mono">geosite.dat</span></>}</td>
                        <td className="py-2 pr-3 font-mono text-gray-400">geosite:cn</td>
                        <td className="py-2 text-gray-400"><span className="text-green-400">{t('all Chinese domains from geosite DB', 'все китайские домены из geosite DB')}</span></td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div className="mt-3 rounded-lg border border-yellow-800/60 bg-yellow-900/20 p-3 text-xs text-yellow-200/90">
                  <p className="font-semibold mb-1">{t('Rule of thumb — when use what:', 'Правило выбора — когда что использовать:')}</p>
                  <ul className="list-disc pl-5 space-y-0.5">
                    {lang === 'ru' ? (<>
                      <li>Нужен один конкретный хост и ничего больше — <span className="font-mono">full:host.example.com</span></li>
                      <li>Нужен сервис и его CDN поддомены — <span className="font-mono">domain:example.com</span> (или просто <span className="font-mono">example.com</span>)</li>
                      <li>Нужно поймать <em>все</em> варианты с словом (ads, tracker…) — <span className="font-mono">keyword:tracker</span></li>
                      <li>Нужен встроенный список (cn, ru, category-ads-all…) — <span className="font-mono">geosite:&lt;tag&gt;</span> или используйте тип правила <span className="font-mono bg-pink-900/60 text-pink-300 px-1 rounded">geosite</span></li>
                    </>) : (<>
                      <li>You want one specific host and nothing else — <span className="font-mono">full:host.example.com</span></li>
                      <li>You want a service and its CDN subdomains — <span className="font-mono">domain:example.com</span> (or simply <span className="font-mono">example.com</span>)</li>
                      <li>You want to catch <em>all</em> variants containing a word (ads, tracker…) — <span className="font-mono">keyword:tracker</span></li>
                      <li>You want a built-in list (cn, ru, category-ads-all, netflix, google…) — <span className="font-mono">geosite:&lt;tag&gt;</span> or use the dedicated <span className="font-mono bg-pink-900/60 text-pink-300 px-1 rounded">geosite</span> rule type</li>
                    </>)}
                  </ul>
                </div>
              </section>

              <section>
                <h3 className="text-gray-100 font-semibold mb-2">{t('All rule types', 'Все типы правил')}</h3>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-gray-800 text-left text-gray-500">
                        <th className="pb-2 pr-3 font-medium">Type</th>
                        <th className="pb-2 pr-3 font-medium">{t('What it matches', 'Что совпадает')}</th>
                        <th className="pb-2 font-medium">{t('Example value', 'Пример значения')}</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-800/60 align-top">
                      <tr>
                        <td className="py-2 pr-3"><span className="font-mono bg-purple-900/60 text-purple-300 px-1.5 py-0.5 rounded">mac</span></td>
                        <td className="py-2 pr-3 text-gray-400">{t('Source device MAC address (applied via nftables, not xray)', 'MAC-адрес устройства-источника (применяется через nftables, не xray)')}</td>
                        <td className="py-2 font-mono text-gray-400">aa:bb:cc:dd:ee:ff</td>
                      </tr>
                      <tr>
                        <td className="py-2 pr-3"><span className="font-mono bg-blue-900/60 text-blue-300 px-1.5 py-0.5 rounded">src_ip</span></td>
                        <td className="py-2 pr-3 text-gray-400">{t('Source IP / CIDR (who initiates the connection)', 'IP / CIDR источника (кто инициирует соединение)')}</td>
                        <td className="py-2 font-mono text-gray-400">192.168.1.10, 192.168.2.0/24</td>
                      </tr>
                      <tr>
                        <td className="py-2 pr-3"><span className="font-mono bg-cyan-900/60 text-cyan-300 px-1.5 py-0.5 rounded">dst_ip</span></td>
                        <td className="py-2 pr-3 text-gray-400">{t('Destination IP / CIDR', 'IP / CIDR назначения')}</td>
                        <td className="py-2 font-mono text-gray-400">1.1.1.1, 10.0.0.0/8</td>
                      </tr>
                      <tr>
                        <td className="py-2 pr-3"><span className="font-mono bg-green-900/60 text-green-300 px-1.5 py-0.5 rounded">domain</span></td>
                        <td className="py-2 pr-3 text-gray-400">{t('Destination domain (see prefixes above)', 'Домен назначения (см. префиксы выше)')}</td>
                        <td className="py-2 font-mono text-gray-400">netflix.com, full:api.foo.com, keyword:ads</td>
                      </tr>
                      <tr>
                        <td className="py-2 pr-3"><span className="font-mono bg-yellow-900/60 text-yellow-300 px-1.5 py-0.5 rounded">port</span></td>
                        <td className="py-2 pr-3 text-gray-400">{t('Destination port / range', 'Порт / диапазон назначения')}</td>
                        <td className="py-2 font-mono text-gray-400">80, 443, 8000-9000</td>
                      </tr>
                      <tr>
                        <td className="py-2 pr-3"><span className="font-mono bg-orange-900/60 text-orange-300 px-1.5 py-0.5 rounded">protocol</span></td>
                        <td className="py-2 pr-3 text-gray-400">{t('L4 / L7 protocol detected by xray sniffer', 'L4 / L7 протокол, определённый xray sniffer')}</td>
                        <td className="py-2 font-mono text-gray-400">tcp, udp, http, tls, bittorrent</td>
                      </tr>
                      <tr>
                        <td className="py-2 pr-3"><span className="font-mono bg-red-900/60 text-red-300 px-1.5 py-0.5 rounded">geoip</span></td>
                        <td className="py-2 pr-3 text-gray-400">{lang === 'ru' ? <>Код страны из <span className="font-mono">geoip.dat</span></> : <>Country code from <span className="font-mono">geoip.dat</span></>}</td>
                        <td className="py-2 font-mono text-gray-400">CN, RU, US, private</td>
                      </tr>
                      <tr>
                        <td className="py-2 pr-3"><span className="font-mono bg-pink-900/60 text-pink-300 px-1.5 py-0.5 rounded">geosite</span></td>
                        <td className="py-2 pr-3 text-gray-400">{lang === 'ru' ? <>Категория из <span className="font-mono">geosite.dat</span> (префикс не нужен)</> : <>Category from <span className="font-mono">geosite.dat</span> (no prefix needed)</>}</td>
                        <td className="py-2 font-mono text-gray-400">cn, google, netflix, category-ads-all</td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </section>

              <section>
                <h3 className="text-gray-100 font-semibold mb-2">{t('Tips', 'Советы')}</h3>
                <ul className="list-disc pl-5 space-y-1 text-gray-400">
                  {lang === 'ru' ? (<>
                    <li>Используйте <span className="font-mono bg-gray-800 px-1 rounded">Bulk</span> для вставки множества значений одного типа/действия сразу (по одному на строку).</li>
                    <li>Используйте <span className="font-mono bg-gray-800 px-1 rounded">Import JSON</span> для файлов маршрутизации V2RayN / Shadowrocket. Режим <em>Inverted</em> меняет proxy ↔ direct (белый список в чёрный).</li>
                    <li>Отключайте правило вместо удаления при отладке — переключатель справа.</li>
                    <li>Ставьте более специфичные правила <em>выше</em> общих (напр. <span className="font-mono">full:api.x.com → direct</span> выше <span className="font-mono">domain:x.com → proxy</span>).</li>
                  </>) : (<>
                    <li>Use <span className="font-mono bg-gray-800 px-1 rounded">Bulk</span> to paste many values of the same type/action at once (one per line).</li>
                    <li>Use <span className="font-mono bg-gray-800 px-1 rounded">Import JSON</span> for V2RayN / Shadowrocket routing files. <em>Inverted</em> mode swaps proxy ↔ direct (turn whitelist into blacklist).</li>
                    <li>Disable a rule instead of deleting it while debugging — toggle the switch on the right.</li>
                    <li>Put more specific rules <em>above</em> more generic ones (e.g. <span className="font-mono">full:api.x.com → direct</span> above <span className="font-mono">domain:x.com → proxy</span>).</li>
                  </>)}
                </ul>
              </section>
            </div>
          </div>
        </ModalShell>
      )}
    </div>
  )
}


/**
 * AutoDisabledBanner — sits above the rules table on the Routing page,
 * surfaces routing rules that the v1.2.7 self-heal pass disabled
 * because they referenced a `geosite:X` or `geoip:X` tag absent from
 * the loaded `.dat`.
 *
 * Each row offers three actions:
 *   • Re-enable: flips the rule back on; if the tag is still missing
 *     the next config write will self-disable it again. User accepts
 *     that risk.
 *   • Delete: permanently removes the rule.
 *   • (per-banner) Dismiss all: clears the inbox without touching
 *     rules. The yellow banner just disappears; rules stay disabled
 *     in the DB.
 *
 * Hidden when the inbox is empty — the banner doesn't render its own
 * negative state.
 */
function AutoDisabledBanner() {
  const t = useT()
  const qc = useQueryClient()

  const { data } = useQuery({
    queryKey: ['routing', 'auto-disabled'],
    queryFn: () => routingApi.listAutoDisabled(),
  })

  const reEnable = useMutation({
    mutationFn: (ruleId: number) => routingApi.reEnableAutoDisabled(ruleId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['routing'] })
    },
  })

  const remove = useMutation({
    mutationFn: (ruleId: number) => routingApi.deleteAutoDisabled(ruleId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['routing'] })
    },
  })

  const dismissAll = useMutation({
    mutationFn: () => routingApi.dismissAutoDisabledAll(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['routing', 'auto-disabled'] })
    },
  })

  const items = data?.items ?? []
  if (items.length === 0) return null

  return (
    <div
      role="alert"
      className="rounded-lg border border-yellow-700/50 bg-yellow-950/30 px-4 py-3 text-sm"
    >
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-start gap-2 min-w-0">
          <span className="text-yellow-300 text-lg leading-none mt-0.5">⚠</span>
          <div>
            <div className="font-semibold text-yellow-100">
              {items.length}{' '}
              {t(
                items.length === 1 ? 'rule auto-disabled' : 'rules auto-disabled',
                items.length === 1 ? 'правило отключено автоматически' : 'правил отключено автоматически',
              )}
            </div>
            <div className="text-xs text-yellow-300/80 mt-0.5">
              {t(
                'Xray rejected the generated config because these rules reference geosite/geoip tags missing from your current .dat. Switch to a profile that includes them, re-enable individually, or delete.',
                'Xray отклонил конфиг — эти правила ссылаются на geosite/geoip теги отсутствующие в вашем .dat. Переключите Geo профиль либо включите/удалите вручную.',
              )}
            </div>
          </div>
        </div>
        <button
          type="button"
          onClick={() => dismissAll.mutate()}
          disabled={dismissAll.isPending}
          className="text-xs text-yellow-400 hover:text-yellow-200 underline disabled:opacity-50 flex-shrink-0"
        >
          {t('Dismiss all', 'Скрыть все')}
        </button>
      </div>
      <ul className="divide-y divide-yellow-900/40 mt-2">
        {items.map((it) => (
          <li
            key={`${it.rule_id}-${it.disabled_at ?? ''}`}
            className="py-2 flex items-center justify-between gap-3 text-xs"
          >
            <div className="min-w-0 flex-1">
              <div className="text-yellow-100 font-medium truncate">
                {it.name || `Rule #${it.rule_id}`}
              </div>
              <div className="text-yellow-300/70 font-mono mt-0.5 truncate">
                {it.rule_type}: {it.match_value}
              </div>
              <div className="text-yellow-400/60 mt-0.5">
                {t('Missing:', 'Отсутствует:')}{' '}
                <span className="font-mono">
                  {it.missing_kind}:{it.missing_tag}
                </span>
              </div>
            </div>
            <div className="flex gap-2 flex-shrink-0">
              <button
                type="button"
                onClick={() => reEnable.mutate(it.rule_id)}
                disabled={reEnable.isPending}
                className="rounded border border-yellow-700/60 bg-yellow-900/30 px-2 py-1 text-yellow-200 hover:bg-yellow-800/50 disabled:opacity-50"
                title={t(
                  'Re-enable this rule. If the tag is still missing it will be auto-disabled again on the next config write.',
                  'Включить правило обратно. Если тег всё ещё отсутствует — на следующей записи конфига будет снова отключено.',
                )}
              >
                {t('Re-enable', 'Включить')}
              </button>
              <button
                type="button"
                onClick={() => remove.mutate(it.rule_id)}
                disabled={remove.isPending}
                className="rounded border border-red-800/50 bg-red-950/30 px-2 py-1 text-red-300 hover:bg-red-900/40 disabled:opacity-50"
              >
                {t('Delete', 'Удалить')}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
