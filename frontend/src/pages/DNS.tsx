import React, { useState, useEffect } from 'react'
import {
  Globe,
  Plus,
  Trash2,
  Pencil,
  CheckCircle,
  XCircle,
  Search,
  Save,
  X,
  Database,
  RefreshCw,
  AlertTriangle,
} from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { dnsApi, systemApi } from '@/api/client'
import { InfoTip } from '@/components/InfoTip'
import { useT } from '@/hooks/useT'
import type { DnsRule, DnsRuleCreate, DnsSettings, DnsTestResult, DnsQueryLog, DnsQueryStats, SystemSettings } from '@/types'

// ── helpers ───────────────────────────────────────────────────────────────────

function Toggle({
  checked,
  onChange,
  label,
}: {
  checked: boolean
  onChange: (v: boolean) => void
  label: React.ReactNode
}) {
  return (
    <label className="flex items-center gap-2 cursor-pointer select-none">
      <div
        onClick={() => onChange(!checked)}
        className={`relative w-9 h-5 rounded-full transition-colors ${
          checked ? 'bg-brand-600' : 'bg-gray-700'
        }`}
      >
        <span
          className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${
            checked ? 'translate-x-4' : 'translate-x-0'
          }`}
        />
      </div>
      <span className="text-sm text-gray-300">{label}</span>
    </label>
  )
}

function Input({
  label,
  value,
  onChange,
  placeholder,
  className = '',
}: {
  label?: React.ReactNode
  value: string
  onChange: (v: string) => void
  placeholder?: string
  className?: string
}) {
  return (
    <div className={className}>
      {label && (
        <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">{label}</label>
      )}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-none"
      />
    </div>
  )
}

// ── DNS Settings section ──────────────────────────────────────────────────────

function DnsSettingsSection({
  settings,
  onSave,
  saving,
}: {
  settings: DnsSettings
  onSave: (data: Partial<DnsSettings>) => void
  saving: boolean
}) {
  const t = useT()
  const [form, setForm] = useState<DnsSettings>({ ...settings })

  useEffect(() => {
    setForm({ ...settings })
  }, [settings])

  const set = <K extends keyof DnsSettings>(k: K, v: DnsSettings[K]) =>
    setForm((prev) => ({ ...prev, [k]: v }))

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-5 space-y-5">
      <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
        <Globe className="h-4 w-4 text-brand-400" />
        DNS Mode &amp; Servers
      </h2>

      {/* Mode */}
      <div>
        <label className="flex items-center gap-1 text-xs font-medium text-gray-400 mb-1">
          DNS Mode
          <InfoTip className="ml-0.5" text={t(
            'How xray resolves DNS. plain — standard UDP DNS. doh — DNS-over-HTTPS (encrypted, harder to intercept). dot — DNS-over-TCP on port 53 (NOT encrypted: xray-core does not support native DoT, so this mode falls back to plaintext TCP). fakedns — synthetic IPs for routing. Affects the main upstream DNS server.',
            'Способ резолвинга DNS в xray. plain — обычный UDP DNS. doh — DNS-over-HTTPS (зашифровано, труднее перехватить). dot — DNS-over-TCP на порту 53 (БЕЗ шифрования: xray-core не поддерживает нативный DoT, режим падает до plaintext TCP). fakedns — синтетические IP для маршрутизации. Влияет на основной upstream DNS-сервер.',
          )} />
        </label>
        <select
          value={form.dns_mode}
          onChange={(e) => set('dns_mode', e.target.value)}
          className="rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
        >
          <option value="plain">Plain DNS</option>
          <option value="doh">DNS-over-HTTPS (DoH)</option>
          <option value="dot">DNS-over-TCP (not encrypted)</option>
          <option value="fakedns">FakeDNS</option>
        </select>
      </div>

      {/* Servers */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Input
          label={<>Primary DNS <InfoTip className="ml-0.5" text={t('Primary DNS server for all queries. Examples: 8.8.8.8 (Google), 1.1.1.1 (Cloudflare), https://dns.google/dns-query (DoH).', 'Основной DNS-сервер для всех запросов. Примеры: 8.8.8.8 (Google), 1.1.1.1 (Cloudflare), https://dns.google/dns-query (DoH).')} /></>}
          value={form.dns_upstream}
          onChange={(v) => set('dns_upstream', v)}
          placeholder="1.1.1.1"
        />
        <Input
          label={<>Secondary DNS <InfoTip className="ml-0.5" text={t('Fallback DNS server used when the primary is unavailable.', 'Резервный DNS-сервер, используется когда основной недоступен.')} /></>}
          value={form.dns_upstream_secondary ?? ''}
          onChange={(v) => set('dns_upstream_secondary', v)}
          placeholder="8.8.8.8"
        />
        <Input
          label={<>Fallback DNS <InfoTip className="ml-0.5" text={t('Used as last resort if all other servers fail.', 'Используется как последний вариант если все остальные серверы недоступны.')} /></>}
          value={form.dns_fallback ?? ''}
          onChange={(v) => set('dns_fallback', v)}
          placeholder="114.114.114.114"
        />
      </div>

      {/* Toggles */}
      <div className="space-y-2.5">
        <Toggle
          checked={form.bypass_cn_dns}
          onChange={(v) => set('bypass_cn_dns', v)}
          label={<>Bypass CN — use 114.114.114.114 for CN domains (geosite:cn) <InfoTip className="ml-0.5" text={t('Send DNS queries for Chinese domains (geosite:cn) to a Chinese DNS server (114.114.114.114). Ensures correct IP resolution for CN domains and avoids DNS pollution.', 'Отправлять DNS-запросы для китайских доменов (geosite:cn) на китайский DNS (114.114.114.114). Обеспечивает корректный резолвинг CN-доменов и устраняет DNS-загрязнение.')} /></>}
        />
        <Toggle
          checked={form.bypass_ru_dns}
          onChange={(v) => set('bypass_ru_dns', v)}
          label={<>Bypass RU — use 77.88.8.8 (Yandex DNS) for RU domains (geosite:ru) <InfoTip className="ml-0.5" text={t('Send DNS queries for Russian domains (geosite:ru) to Yandex DNS (77.88.8.8). Ensures correct resolution for RU domains.', 'Отправлять DNS-запросы для российских доменов (geosite:ru) на Yandex DNS (77.88.8.8). Обеспечивает корректный резолвинг RU-доменов.')} /></>}
        />
        <Toggle
          checked={form.fakedns_enabled}
          onChange={(v) => set('fakedns_enabled', v)}
          label={<>Enable FakeDNS <InfoTip className="ml-0.5" text={t('xray assigns fake IP addresses (e.g. 198.18.x.x) to domain names before the real connection. This enables accurate domain-based routing even for apps that resolve DNS early. Required for correct domain routing in TUN mode.', 'xray назначает фейковые IP-адреса (напр. 198.18.x.x) доменам до реального соединения. Это обеспечивает точную маршрутизацию по домену даже для приложений, которые резолвят DNS заранее. Обязательно для корректной маршрутизации по домену в режиме TUN.')} /></>}
        />
        {form.fakedns_enabled && (
          <div className="ml-11 grid grid-cols-2 gap-3">
            <Input
              label={<>FakeDNS Pool <InfoTip className="ml-0.5" text={t('IP range used for fake addresses. Must not overlap with real IPs on your network. Default 198.18.0.0/15 is a reserved range safe to use.', 'Диапазон IP для фейковых адресов. Не должен пересекаться с реальными IP в вашей сети. По умолчанию 198.18.0.0/15 — зарезервированный диапазон, безопасен для использования.')} /></>}
              value={form.fakedns_pool}
              onChange={(v) => set('fakedns_pool', v)}
              placeholder="198.18.0.0/15"
            />
            <Input
              label={<>Pool Size <InfoTip className="ml-0.5" text={t('Maximum number of domains that can be assigned fake IPs simultaneously.', 'Максимальное количество доменов, которым одновременно могут быть назначены фейковые IP.')} /></>}
              value={String(form.fakedns_pool_size)}
              onChange={(v) => set('fakedns_pool_size', parseInt(v) || 65535)}
              placeholder="65535"
            />
          </div>
        )}
        <Toggle
          checked={form.dns_disable_fallback}
          onChange={(v) => set('dns_disable_fallback', v)}
          label={<>Disable DNS fallback <InfoTip className="ml-0.5" text={t(
            "Controls what happens when a DNS query doesn't match any DNS rule:\n\n" +
            "ON (recommended): Strict routing — each domain uses ONLY its configured server. " +
            "Unmatched domains use only plain servers (Primary/Secondary/Fallback). " +
            "Rule-specific servers (e.g. 94.140.14.14 for YouTube) are never used for other domains.\n\n" +
            "OFF: Permissive — for unmatched domains, ALL servers are queried simultaneously including rule-specific ones. " +
            "Fastest response wins, but rule servers appear in the log for domains they shouldn't handle.\n\n" +
            "Examples with ON:\n" +
            "• youtube.com → 94.140.14.14 (YouTube rule) only\n" +
            "• vk.com → 77.88.8.8 (Bypass RU) only\n" +
            "• apple.com → DoT 8.8.8.8 → DoT 8.8.4.4 → 1.1.1.1 (plain servers, no rule match)\n\n" +
            "Examples with OFF:\n" +
            "• apple.com → queried on ALL servers simultaneously; 94.140.14.14 may appear in the log even though no YouTube rule matched",
            "Определяет что происходит когда DNS-запрос не совпадает ни с одним правилом DNS:\n\n" +
            "ON (рекомендуется): Строгая маршрутизация — каждый сервер используется ТОЛЬКО для своих доменов. " +
            "Несовпавшие домены используют только plain-серверы (Primary/Secondary/Fallback). " +
            "Rule-specific серверы (напр. 94.140.14.14 для YouTube) никогда не используются для других доменов.\n\n" +
            "OFF: Разрешающий режим — для несовпавших доменов опрашиваются ВСЕ серверы одновременно, включая rule-specific. " +
            "Побеждает быстрейший ответ, но rule-серверы появляются в логе для чужих доменов.\n\n" +
            "Примеры с ON:\n" +
            "• youtube.com → 94.140.14.14 (правило YouTube) только\n" +
            "• vk.com → 77.88.8.8 (Bypass RU) только\n" +
            "• apple.com → DoT 8.8.8.8 → DoT 8.8.4.4 → 1.1.1.1 (plain-серверы, правило не совпало)\n\n" +
            "Примеры с OFF:\n" +
            "• apple.com → запрашивается на ВСЕХ серверах одновременно; 94.140.14.14 может появиться в логе хотя правило YouTube не совпало"
          )} /></>}
        />
        <Toggle
          checked={form.dns_sniffing}
          onChange={(v) => set('dns_sniffing', v)}
          label={<>Enable traffic sniffing (for domain-based routing) <InfoTip className="ml-0.5" text={t('xray inspects incoming connections to detect the target domain name from TLS SNI and HTTP Host headers. Enables domain-based routing rules to work correctly for transparent proxy traffic.', 'xray анализирует входящие соединения для определения целевого домена из TLS SNI и HTTP Host заголовков. Позволяет правилам маршрутизации по домену корректно работать для трафика прозрачного прокси.')} /></>}
        />
      </div>

      <button
        onClick={() => onSave(form)}
        disabled={saving}
        className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors"
      >
        <Save className="h-4 w-4" />
        {saving ? 'Saving…' : 'Save Settings'}
      </button>
    </div>
  )
}

// ── DNS Rules section ─────────────────────────────────────────────────────────

const emptyRule: DnsRuleCreate = {
  name: '',
  enabled: true,
  domain_match: '',
  dns_server: '',
  dns_type: 'plain',
  order: 100,
}

function RuleForm({
  initial,
  onSubmit,
  onCancel,
  submitting,
}: {
  initial: DnsRuleCreate
  onSubmit: (r: DnsRuleCreate) => void
  onCancel: () => void
  submitting: boolean
}) {
  const [form, setForm] = useState<DnsRuleCreate>({ ...initial })
  const set = <K extends keyof DnsRuleCreate>(k: K, v: DnsRuleCreate[K]) =>
    setForm((prev) => ({ ...prev, [k]: v }))

  return (
    <div className="rounded-lg border border-gray-700 bg-gray-850 p-4 space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <Input
          label="Name (optional)"
          value={form.name}
          onChange={(v) => set('name', v)}
          placeholder="e.g. China DNS"
        />
        <Input
          label="Domain Match"
          value={form.domain_match}
          onChange={(v) => set('domain_match', v)}
          placeholder="geosite:cn, netflix.com, keyword:ads"
        />
      </div>
      <div className="grid grid-cols-3 gap-3">
        <Input
          label="DNS Server"
          value={form.dns_server}
          onChange={(v) => set('dns_server', v)}
          placeholder="114.114.114.114"
          className="col-span-2"
        />
        <div>
          <label className="block text-xs font-medium text-gray-400 mb-1">Type</label>
          <select
            value={form.dns_type}
            onChange={(e) => set('dns_type', e.target.value as DnsRuleCreate['dns_type'])}
            className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none"
          >
            <option value="plain">Plain</option>
            <option value="doh">DoH</option>
            <option value="dot">TCP (not encrypted)</option>
          </select>
        </div>
      </div>
      <div className="flex items-center gap-3 pt-1">
        <Toggle
          checked={form.enabled}
          onChange={(v) => set('enabled', v)}
          label="Enabled"
        />
        <div className="ml-auto flex gap-2">
          <button
            onClick={onCancel}
            className="flex items-center gap-1 rounded px-3 py-1.5 text-xs bg-gray-700 text-gray-300 hover:bg-gray-600 transition-colors"
          >
            <X className="h-3.5 w-3.5" />
            Cancel
          </button>
          <button
            onClick={() => onSubmit(form)}
            disabled={submitting || !form.domain_match || !form.dns_server}
            className="flex items-center gap-1 rounded px-3 py-1.5 text-xs bg-brand-600 text-white hover:bg-brand-500 disabled:opacity-50 transition-colors"
          >
            <Save className="h-3.5 w-3.5" />
            {submitting ? 'Saving…' : 'Save Rule'}
          </button>
        </div>
      </div>
    </div>
  )
}

function DnsRulesSection({
  rules,
  onAdd,
  onUpdate,
  onDelete,
  adding,
  updating,
}: {
  rules: DnsRule[]
  onAdd: (r: DnsRuleCreate) => void
  onUpdate: (id: number, r: DnsRuleCreate) => void
  onDelete: (id: number) => void
  adding: boolean
  updating: boolean
}) {
  const [showAdd, setShowAdd] = useState(false)
  const [editId, setEditId] = useState<number | null>(null)

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-200">DNS Rules</h2>
        <button
          onClick={() => { setShowAdd(true); setEditId(null) }}
          className="flex items-center gap-1 rounded-lg px-3 py-1.5 text-xs bg-brand-600 text-white hover:bg-brand-500 transition-colors"
        >
          <Plus className="h-3.5 w-3.5" />
          Add Rule
        </button>
      </div>

      {showAdd && (
        <RuleForm
          initial={emptyRule}
          onSubmit={(r) => { onAdd(r); setShowAdd(false) }}
          onCancel={() => setShowAdd(false)}
          submitting={adding}
        />
      )}

      {rules.length === 0 && !showAdd && (
        <p className="text-xs text-gray-500 py-4 text-center">
          No DNS rules configured. Rules allow routing specific domains to custom DNS servers.
        </p>
      )}

      {rules.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs text-gray-300">
            <thead>
              <tr className="border-b border-gray-800 text-gray-500">
                <th className="py-2 pr-4 text-left font-medium">Domain Match</th>
                <th className="py-2 pr-4 text-left font-medium">DNS Server</th>
                <th className="py-2 pr-4 text-left font-medium">Type</th>
                <th className="py-2 pr-4 text-left font-medium">Enabled</th>
                <th className="py-2 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((rule) =>
                editId === rule.id ? (
                  <tr key={rule.id}>
                    <td colSpan={5} className="py-2">
                      <RuleForm
                        initial={rule}
                        onSubmit={(r) => { onUpdate(rule.id, r); setEditId(null) }}
                        onCancel={() => setEditId(null)}
                        submitting={updating}
                      />
                    </td>
                  </tr>
                ) : (
                  <tr key={rule.id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="py-2 pr-4 font-mono">{rule.domain_match}</td>
                    <td className="py-2 pr-4 font-mono">{rule.dns_server}</td>
                    <td className="py-2 pr-4">
                      <span className="rounded-full px-2 py-0.5 text-xs bg-gray-700 text-gray-300">
                        {rule.dns_type}
                      </span>
                    </td>
                    <td className="py-2 pr-4">
                      {rule.enabled
                        ? <CheckCircle className="h-3.5 w-3.5 text-green-400" />
                        : <XCircle className="h-3.5 w-3.5 text-gray-600" />
                      }
                    </td>
                    <td className="py-2 text-right">
                      <div className="flex items-center justify-end gap-1">
                        <button
                          onClick={() => setEditId(rule.id)}
                          className="rounded p-1 text-gray-500 hover:text-gray-200 hover:bg-gray-700 transition-colors"
                          title="Edit"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => onDelete(rule.id)}
                          className="rounded p-1 text-gray-500 hover:text-red-400 hover:bg-gray-700 transition-colors"
                          title="Delete"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ── DNS Test section ──────────────────────────────────────────────────────────

function XrayServerLabel({ server }: { server: string }) {
  const isXrayFallback = server.startsWith('xray:')
  const isCache = server === 'cache'
  const isFakeDns = server === 'fakedns'

  if (isXrayFallback)
    return (
      <span className="font-mono text-gray-500" title="DNS query log not available (requires log_level=debug)">
        {server} <span className="text-gray-600">(log unavailable)</span>
      </span>
    )
  if (isCache)
    return <span className="font-mono text-gray-400">cache hit</span>
  if (isFakeDns)
    return <span className="font-mono text-purple-400">fakedns</span>
  return (
    <span className="inline-flex items-center gap-1.5">
      <CheckCircle className="h-3.5 w-3.5 text-green-400 flex-shrink-0" />
      <span className="font-mono text-green-400">{server}</span>
    </span>
  )
}

function DnsTestSection() {
  const t = useT()
  const [domain, setDomain] = useState('')
  const [server, setServer] = useState('')
  const [viaXray, setViaXray] = useState(false)
  const [result, setResult] = useState<DnsTestResult | null>(null)

  const testMutation = useMutation({
    mutationFn: () =>
      viaXray ? dnsApi.testViaXray(domain) : dnsApi.test(domain, server || undefined),
    onSuccess: (data) => setResult(data),
  })

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-5 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Search className="h-4 w-4 text-brand-400" />
          DNS Test Tool
        </h2>
        <Toggle
          checked={viaXray}
          onChange={(v) => { setViaXray(v); setResult(null) }}
          label={
            <span className="flex items-center gap-1.5">
              {viaXray
                ? <><span className="text-brand-400 font-medium">Via xray</span><InfoTip text={t('Resolves through xray\'s DNS inbound — all DNS rules apply exactly as for real device traffic. Result appears in DNS Query Log.', 'Резолвинг через DNS inbound xray — все правила DNS применяются как для реального трафика устройств. Результат отображается в DNS Query Log.')} /></>
                : <><span className="text-gray-400">Direct</span><InfoTip text={t('Resolves directly to the specified DNS server, bypassing xray rules. Useful for testing raw server connectivity.', 'Резолвинг напрямую к указанному DNS-серверу, минуя правила xray. Полезно для проверки прямой доступности сервера.')} /></>
              }
            </span>
          }
        />
      </div>

      <div className="flex gap-3 items-end">
        <Input
          label="Domain"
          value={domain}
          onChange={setDomain}
          placeholder="google.com"
          className="flex-1"
        />
        {!viaXray && (
          <Input
            label="Server (optional)"
            value={server}
            onChange={setServer}
            placeholder="1.1.1.1 or https://dns.google/dns-query"
            className="flex-1"
          />
        )}
        {viaXray && (
          <div className="flex-1 flex items-end pb-0.5">
            <p className="text-xs text-gray-500">
              Query goes to xray DNS inbound → DNS rules applied → appears in Query Log
            </p>
          </div>
        )}
        <button
          onClick={() => testMutation.mutate()}
          disabled={!domain || testMutation.isPending}
          className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors mb-0.5"
        >
          {testMutation.isPending ? 'Testing…' : 'Test'}
        </button>
      </div>

      {result && (
        <div className="rounded-lg border border-gray-700 bg-gray-800/50 p-4 text-xs space-y-2">
          {result.error ? (
            <div className="flex items-center gap-2 text-red-400">
              <XCircle className="h-4 w-4" />
              <span>{result.error}</span>
            </div>
          ) : (
            <>
              <div className="flex items-center gap-2 text-green-400">
                <CheckCircle className="h-4 w-4" />
                <span className="font-mono">{result.resolved_ips.join(', ')}</span>
              </div>
              <div className="text-gray-500 space-x-4">
                <span>Latency: <span className="text-gray-300">{result.latency_ms}ms</span></span>
                <span>Server: {viaXray
                  ? <XrayServerLabel server={result.server_used} />
                  : <span className="text-gray-300 font-mono">{result.server_used}</span>
                }</span>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── DNS Query Log section ─────────────────────────────────────────────────────

function DnsQueryLogSection({
  settings,
  logLevel,
}: {
  settings: SystemSettings | undefined
  logLevel: string | undefined
}) {
  const t = useT()
  const qc = useQueryClient()
  const [domainFilter, setDomainFilter] = useState('')
  const [showCacheOnly, setShowCacheOnly] = useState(false)
  const [offset, setOffset] = useState(0)
  const LIMIT = 50

  const enabled = settings?.dns_query_log_enabled ?? false

  const { data: stats, refetch: refetchStats } = useQuery<DnsQueryStats>({
    queryKey: ['dns', 'query-stats'],
    queryFn: () => dnsApi.getQueryStats(),
    enabled: enabled,
    refetchInterval: enabled ? 5000 : false,
  })

  const { data: logs = [], refetch: refetchLogs } = useQuery<DnsQueryLog[]>({
    queryKey: ['dns', 'queries', domainFilter, showCacheOnly, offset],
    queryFn: () => dnsApi.getQueryLogs({
      domain: domainFilter || undefined,
      limit: LIMIT,
      offset,
      cache_only: showCacheOnly || undefined,
    }),
    enabled: enabled,
    refetchInterval: enabled ? 5000 : false,
  })

  const toggleEnabled = useMutation({
    mutationFn: (val: boolean) => systemApi.updateSettings({ dns_query_log_enabled: val }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['system', 'settings'] })
    },
  })

  const clearLogs = useMutation({
    mutationFn: () => dnsApi.clearQueryLogs(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['dns', 'queries'] })
      qc.invalidateQueries({ queryKey: ['dns', 'query-stats'] })
    },
  })

  const showDebugWarning = enabled && logLevel && logLevel !== 'debug'

  const rowColor = (entry: DnsQueryLog): string => {
    if (entry.server_used === 'fakedns') return 'text-purple-300'
    if (entry.cache_hit) return 'text-gray-500'
    return 'text-gray-200'
  }

  const formatTime = (ts: string): string => {
    const d = new Date(ts)
    return d.toLocaleTimeString()
  }

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-5 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Database className="h-4 w-4 text-brand-400" />
          DNS Query Log
        </h2>
        <div className="flex items-center gap-3">
          <Toggle
            checked={enabled}
            onChange={(v) => toggleEnabled.mutate(v)}
            label={<>Enable <InfoTip className="ml-0.5" text={t('Record all DNS queries processed by xray to the database. Allows you to see which domains devices are querying, cache hit rates, and top domains. Requires log_level=debug in xray settings.', 'Записывать все DNS-запросы, обработанные xray, в базу данных. Позволяет видеть какие домены запрашивают устройства, статистику кэша и топ доменов. Требует log_level=debug в настройках xray.')} /></>}
          />
          <button
            onClick={() => clearLogs.mutate()}
            disabled={clearLogs.isPending || !enabled}
            className="flex items-center gap-1 rounded px-2.5 py-1.5 text-xs bg-gray-700 text-gray-300 hover:bg-red-900/40 hover:text-red-300 disabled:opacity-40 transition-colors"
          >
            <Trash2 className="h-3.5 w-3.5" />
            Clear
          </button>
          <button
            onClick={() => { refetchLogs(); refetchStats() }}
            disabled={!enabled}
            className="flex items-center gap-1 rounded px-2.5 py-1.5 text-xs bg-gray-700 text-gray-300 hover:bg-gray-600 disabled:opacity-40 transition-colors"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
        </div>
      </div>

      {/* Debug level warning */}
      {showDebugWarning && (
        <div className="flex items-center gap-2 rounded-lg bg-yellow-900/20 border border-yellow-700/40 px-3 py-2 text-xs text-yellow-300">
          <AlertTriangle className="h-3.5 w-3.5 flex-shrink-0" />
          DNS query logging requires log_level=debug. Current level: <span className="font-mono font-bold ml-1">{logLevel}</span>.
          Change it in System → Settings.
        </div>
      )}

      {!enabled && (
        <p className="text-xs text-gray-500 text-center py-4">
          Enable DNS Query Logging to capture and inspect DNS resolution events.
          Requires xray log_level=debug.
        </p>
      )}

      {enabled && (
        <>
          {/* Stats row */}
          {stats && (
            <div className="rounded-lg bg-gray-800/60 px-4 py-3 space-y-1.5 text-xs">
              <div className="flex items-center flex-wrap gap-x-4 gap-y-1 text-gray-400">
                <span>
                  Total: <span className="text-gray-200 font-mono">{stats.total_queries.toLocaleString()}</span>
                </span>
                <span>
                  Unique domains: <span className="text-gray-200 font-mono">{stats.unique_domains.toLocaleString()}</span>
                </span>
                <span>
                  Cache hit rate: <span className="text-gray-200 font-mono">{(stats.cache_hit_rate * 100).toFixed(1)}%</span>
                </span>
                <span>
                  Last hour: <span className="text-gray-200 font-mono">{stats.queries_last_hour}</span>
                </span>
              </div>
              {stats.top_domains.length > 0 && (
                <div className="text-gray-500 truncate">
                  Top: {stats.top_domains.map((d) => `${d.domain}(${d.count})`).join(' · ')}
                </div>
              )}
            </div>
          )}

          {/* Filters */}
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex-1 min-w-40">
              <input
                value={domainFilter}
                onChange={(e) => { setDomainFilter(e.target.value); setOffset(0) }}
                placeholder="Filter domain…"
                className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-xs text-gray-100 font-mono focus:border-brand-500 focus:outline-none"
              />
            </div>
            <Toggle
              checked={showCacheOnly}
              onChange={(v) => { setShowCacheOnly(v); setOffset(0) }}
              label="Cache hits only"
            />
          </div>

          {/* Table */}
          {logs.length === 0 ? (
            <p className="text-xs text-gray-600 text-center py-4">No DNS queries logged yet.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-gray-800 text-gray-500">
                    <th className="py-2 pr-3 text-left font-medium">Time</th>
                    <th className="py-2 pr-3 text-left font-medium">Domain</th>
                    <th className="py-2 pr-3 text-left font-medium">Type</th>
                    <th className="py-2 pr-3 text-left font-medium">IPs</th>
                    <th className="py-2 text-left font-medium">Server</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map((entry) => (
                    <tr
                      key={entry.id}
                      className={`border-b border-gray-800/40 hover:bg-gray-800/20 ${rowColor(entry)}`}
                    >
                      <td className="py-1.5 pr-3 font-mono whitespace-nowrap">{formatTime(entry.timestamp)}</td>
                      <td className="py-1.5 pr-3 font-mono max-w-48 truncate">{entry.domain}</td>
                      <td className="py-1.5 pr-3">
                        <span className="rounded px-1.5 py-0.5 bg-gray-800 text-gray-400">{entry.query_type}</span>
                      </td>
                      <td className="py-1.5 pr-3 font-mono max-w-40 truncate">
                        {entry.resolved_ips.join(', ') || '—'}
                      </td>
                      <td className="py-1.5 font-mono">
                        <span className={
                          entry.server_used === 'cache'
                            ? 'text-gray-500'
                            : entry.server_used === 'fakedns'
                            ? 'text-purple-400'
                            : 'text-gray-300'
                        }>
                          {entry.server_used}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {(logs.length === LIMIT || offset > 0) && (
            <div className="flex items-center justify-center gap-3 pt-1">
              {offset > 0 && (
                <button
                  onClick={() => setOffset((o) => Math.max(0, o - LIMIT))}
                  className="rounded px-3 py-1.5 text-xs bg-gray-700 text-gray-300 hover:bg-gray-600 transition-colors"
                >
                  ← Previous
                </button>
              )}
              {logs.length === LIMIT && (
                <button
                  onClick={() => setOffset((o) => o + LIMIT)}
                  className="rounded px-3 py-1.5 text-xs bg-gray-700 text-gray-300 hover:bg-gray-600 transition-colors"
                >
                  Load More →
                </button>
              )}
            </div>
          )}
        </>
      )}
    </div>
  )
}


// ── Main page ─────────────────────────────────────────────────────────────────

export function DNS() {
  const qc = useQueryClient()

  const { data: dnsSettings, isLoading: settingsLoading } = useQuery({
    queryKey: ['dns', 'settings'],
    queryFn: () => dnsApi.getSettings(),
  })

  const { data: rules = [], isLoading: rulesLoading } = useQuery({
    queryKey: ['dns', 'rules'],
    queryFn: () => dnsApi.getRules(),
  })

  const { data: sysSettings } = useQuery<SystemSettings>({
    queryKey: ['system', 'settings'],
    queryFn: () => systemApi.getSettings(),
  })

  const saveSettings = useMutation({
    mutationFn: (data: Partial<DnsSettings>) => dnsApi.updateSettings(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dns', 'settings'] }),
  })

  const createRule = useMutation({
    mutationFn: (data: DnsRuleCreate) => dnsApi.createRule(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dns', 'rules'] }),
  })

  const updateRule = useMutation({
    mutationFn: ({ id, data }: { id: number; data: DnsRuleCreate }) =>
      dnsApi.updateRule(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dns', 'rules'] }),
  })

  const deleteRule = useMutation({
    mutationFn: (id: number) => dnsApi.deleteRule(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dns', 'rules'] }),
  })

  if (settingsLoading || rulesLoading) {
    return (
      <div className="p-6 text-gray-500 text-sm">Loading DNS configuration…</div>
    )
  }

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-xl font-bold text-gray-100">DNS Management</h1>

      {dnsSettings && (
        <DnsSettingsSection
          settings={dnsSettings}
          onSave={(data) => saveSettings.mutate(data)}
          saving={saveSettings.isPending}
        />
      )}

      <DnsRulesSection
        rules={rules}
        onAdd={(r) => createRule.mutate(r)}
        onUpdate={(id, r) => updateRule.mutate({ id, data: r })}
        onDelete={(id) => deleteRule.mutate(id)}
        adding={createRule.isPending}
        updating={updateRule.isPending}
      />

      <DnsTestSection />

      <DnsQueryLogSection
        settings={sysSettings}
        logLevel={sysSettings?.log_level}
      />
    </div>
  )
}
