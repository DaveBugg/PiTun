import { useState } from 'react'
import type { Node, NodeCreate, Protocol, Transport, TlsMode } from '@/types'
import { InfoTip } from '@/components/InfoTip'
import { useT } from '@/hooks/useT'
import { useServers } from '@/hooks/useServers'

type FormData = Omit<NodeCreate, 'order'>

const PROTOCOLS: Protocol[] = ['vless', 'vmess', 'trojan', 'ss', 'wireguard', 'socks', 'hy2', 'naive']
const TRANSPORTS: Transport[] = ['tcp', 'ws', 'grpc', 'h2', 'xhttp', 'httpupgrade', 'kcp', 'quic']
const TLS_MODES: TlsMode[] = ['none', 'tls', 'reality']
const FINGERPRINTS = ['chrome', 'firefox', 'safari', 'ios', 'android', 'edge', '360', 'qq', 'random', 'randomized', 'randomizedalpn']

const DEFAULTS: FormData = {
  name: '',
  enabled: true,
  protocol: 'vless',
  address: '',
  port: 443,
  uuid: '',
  password: '',
  transport: 'tcp',
  tls: 'tls',
  sni: '',
  fingerprint: 'chrome',
  alpn: '',
  allow_insecure: false,
  ws_path: '/',
  ws_host: '',
  ws_headers: '',
  grpc_service: '',
  grpc_mode: 'gun',
  grpc_authority: '',
  http_path: '/',
  http_host: '',
  kcp_seed: '',
  kcp_header: 'none',
  reality_pbk: '',
  reality_sid: '',
  reality_spx: '',
  flow: '',
  wg_private_key: '',
  wg_public_key: '',
  wg_preshared_key: '',
  wg_endpoint: '',
  wg_mtu: 1420,
  wg_reserved: '',
  wg_local_address: '',
  hy2_obfs: '',
  hy2_obfs_password: '',
  naive_padding: true,
  group: '',
  note: '',
  subscription_id: undefined,
  server_id: undefined,
}

interface Props {
  initial?: Partial<Node>
  onSave: (data: NodeCreate) => void
  onCancel: () => void
  loading?: boolean
  nodes?: Node[]
}

export function NodeForm({ initial, onSave, onCancel, loading, nodes = [] }: Props) {
  const t = useT()
  const [form, setForm] = useState<FormData>({ ...DEFAULTS, ...initial })
  const [chainNodeId, setChainNodeId] = useState(initial?.chain_node_id?.toString() ?? '')
  const [serverId, setServerId] = useState(initial?.server_id?.toString() ?? '')
  // Servers list is small (1-5 typical) and the form is short-lived, so
  // fetching here on mount keeps the API surface simple — we don't have
  // to thread the list down through Nodes page → form.
  const { data: servers = [] } = useServers()

  const set = <K extends keyof FormData>(key: K, value: FormData[K]) =>
    setForm((f) => ({ ...f, [key]: value }))

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    onSave({
      ...form,
      order: initial?.order ?? 0,
      // Send an explicit `null` (not `undefined`) when cleared — `undefined`
      // is dropped by JSON.stringify, so the field never reaches the PATCH
      // body and the backend's `model_dump(exclude_unset=True)` leaves the
      // old value in place (you could set a chain but never remove it).
      chain_node_id: chainNodeId ? Number(chainNodeId) : null,
      server_id: serverId ? Number(serverId) : null,
    })
  }

  // ── render helpers (plain functions, NOT components) ─────────────
  // Called as {inp('name')} not <Input field="name" />.
  // This keeps element types stable across re-renders so React
  // preserves DOM nodes and input focus is never lost.

  const inp = (field: keyof FormData, type = 'text', placeholder?: string, autoFocus = false) => (
    <input
      type={type}
      value={String(form[field] ?? '')}
      onChange={(e) => set(field, (type === 'number' ? Number(e.target.value) : e.target.value) as FormData[typeof field])}
      placeholder={placeholder}
      autoFocus={autoFocus}
      className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
    />
  )

  const sel = (field: keyof FormData, options: string[]) => (
    <select
      value={String(form[field] ?? '')}
      onChange={(e) => set(field, e.target.value as FormData[typeof field])}
      className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
    >
      {options.map((o) => <option key={o} value={o}>{o}</option>)}
    </select>
  )

  const fld = (label: React.ReactNode, children: React.ReactNode, hint?: string) => (
    <div>
      <label className="block text-xs font-medium text-gray-400 mb-1">{label}</label>
      {children}
      {hint && <p className="mt-1 text-xs text-gray-600">{hint}</p>}
    </div>
  )

  const showWs = form.transport === 'ws'
  const showGrpc = form.transport === 'grpc'
  const showHttp = ['h2', 'xhttp', 'httpupgrade'].includes(form.transport)
  const showKcp = form.transport === 'kcp'
  const showReality = form.tls === 'reality'
  const showTls = form.tls !== 'none'
  const isWg = form.protocol === 'wireguard'
  const isHy2 = form.protocol === 'hy2'
  const isNaive = form.protocol === 'naive'
  const hasUuid = ['vless', 'vmess', 'socks', 'naive'].includes(form.protocol)
  const hasPass = ['trojan', 'ss', 'socks', 'hy2', 'naive'].includes(form.protocol)

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {/* Basic */}
      <section className="space-y-3">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Basic</h3>
        <div className="grid grid-cols-2 gap-3">
          {fld('Name', inp('name', 'text', 'My Node', true))}
          {fld('Protocol', sel('protocol', PROTOCOLS))}
          {fld('Address', inp('address', 'text', 'example.com'))}
          {fld('Port', inp('port', 'number'))}
        </div>
        <div className="flex items-center gap-2">
          <input
            type="checkbox"
            id="enabled"
            checked={form.enabled}
            onChange={(e) => set('enabled', e.target.checked)}
            className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
          />
          <label htmlFor="enabled" className="text-sm text-gray-300">Enabled</label>
        </div>
      </section>

      {/* Auth */}
      {!isWg && (
        <section className="space-y-3">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Auth</h3>
          <div className="grid grid-cols-1 gap-3">
            {hasUuid && fld(isNaive ? 'Username' : 'UUID', inp('uuid', 'text', isNaive ? 'user' : 'xxxxxxxx-xxxx-...'))}
            {hasPass && fld('Password', inp('password', 'password'))}
            {form.protocol === 'vless' && fld('Flow', inp('flow', 'text', 'xtls-rprx-vision'), 'e.g. xtls-rprx-vision')}
            {form.protocol === 'vless' && (form.tls === 'tls' || form.tls === 'reality') && !form.flow && (
              <div className="rounded-lg bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-700/40 px-3 py-2 text-xs text-yellow-700 dark:text-yellow-300">
                ⚠ VLESS without flow is deprecated. Set flow to 'xtls-rprx-vision' for better performance.
              </div>
            )}
          </div>
        </section>
      )}

      {/* WireGuard */}
      {isWg && (
        <section className="space-y-3">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">WireGuard</h3>
          <div className="grid grid-cols-1 gap-3">
            {fld('Private Key', inp('wg_private_key'))}
            {fld('Public Key (peer)', inp('wg_public_key'))}
            {fld('Pre-shared Key', inp('wg_preshared_key'))}
            {fld('Local Address', inp('wg_local_address', 'text', '10.0.0.2/32'), 'e.g. 10.0.0.2/32')}
            <div className="grid grid-cols-2 gap-3">
              {fld('MTU', inp('wg_mtu', 'number'))}
              {fld('Reserved (JSON)', inp('wg_reserved', 'text', '[0,0,0]'), '[0,0,0]')}
            </div>
          </div>
        </section>
      )}

      {/* NaiveProxy */}
      {isNaive && (
        <section className="space-y-3">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider flex items-center gap-1.5">
            NaiveProxy
            <InfoTip text={t(
              'NaiveProxy masquerades as Chrome traffic to a Caddy+forwardproxy server. Address must be a real domain (not an IP) with a valid TLS certificate — this is how traffic looks indistinguishable from normal HTTPS. A sidecar container will be spawned automatically on this device to handle the connection.',
              'NaiveProxy маскирует трафик под Chrome к серверу Caddy+forwardproxy. Адрес должен быть реальным доменом (не IP) с валидным TLS-сертификатом — именно так трафик неотличим от обычного HTTPS. На устройстве автоматически запустится sidecar-контейнер для обработки соединения.'
            )} />
          </h3>
          <div className="grid grid-cols-2 gap-3">
            {fld('SNI', inp('sni', 'text', 'example.com'), t('Optional — defaults to Address', 'Опционально — по умолчанию совпадает с Address'))}
            <div className="flex items-center gap-2 mt-5">
              <input
                type="checkbox"
                id="naive_padding"
                checked={form.naive_padding ?? true}
                onChange={(e) => set('naive_padding', e.target.checked)}
                className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
              />
              <label htmlFor="naive_padding" className="text-sm text-gray-300">
                {t('Enable padding', 'Включить padding')}
              </label>
            </div>
          </div>
          {form.address && /^\d{1,3}(\.\d{1,3}){3}$/.test(form.address) && (
            <div className="rounded-lg bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-700/40 px-3 py-2 text-xs text-yellow-700 dark:text-yellow-300">
              ⚠ {t(
                'Address looks like an IP. NaiveProxy requires a real domain with a valid TLS certificate.',
                'Адрес похож на IP. NaiveProxy требует реальный домен с валидным TLS-сертификатом.'
              )}
            </div>
          )}
        </section>
      )}

      {/* Transport (non-WG, non-Hy2, non-Naive) */}
      {!isWg && !isHy2 && !isNaive && (
        <section className="space-y-3">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Transport</h3>
          <div className="grid grid-cols-2 gap-3">
            {fld('Transport', sel('transport', TRANSPORTS))}
            {fld('TLS', sel('tls', TLS_MODES))}
          </div>

          {showTls && (
            <div className="grid grid-cols-2 gap-3">
              {fld('SNI', inp('sni', 'text', 'example.com'))}
              {fld('Fingerprint', sel('fingerprint', FINGERPRINTS))}
              {fld('ALPN', inp('alpn', 'text', 'h2,http/1.1'), 'comma-separated')}
              <div className="flex items-center gap-2 mt-5">
                <input
                  type="checkbox"
                  id="insecure"
                  checked={form.allow_insecure}
                  onChange={(e) => set('allow_insecure', e.target.checked)}
                  className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
                />
                <label htmlFor="insecure" className="text-sm text-gray-300">Allow insecure</label>
              </div>
              {form.allow_insecure && (
                <div className="rounded-lg bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-700/40 px-3 py-2 text-xs text-yellow-700 dark:text-yellow-300">
                  ⚠ allowInsecure will be disabled by xray-core after 2026-06-01. Use pinnedPeerCertSha256 instead.
                </div>
              )}
            </div>
          )}

          {showReality && (
            <div className="grid grid-cols-2 gap-3">
              {fld('Reality Public Key', inp('reality_pbk'))}
              {fld('Short ID', inp('reality_sid'))}
              {fld('Spider X', inp('reality_spx'))}
            </div>
          )}

          {showWs && (
            <div className="grid grid-cols-2 gap-3">
              {fld('WS Path', inp('ws_path', 'text', '/'))}
              {fld('WS Host', inp('ws_host'))}
            </div>
          )}

          {showGrpc && (
            <div className="space-y-3">
              {fld('gRPC Service Name', inp('grpc_service'))}
              <div className="grid grid-cols-2 gap-3">
                {fld(
                  'Mode',
                  sel('grpc_mode', ['gun', 'multi']),
                  'multi = H2 multi-stream (recommended behind nginx).',
                )}
                {fld(
                  'Authority',
                  inp('grpc_authority'),
                  ':authority HTTP/2 header — nginx routes by this. Leave empty to use destination host.',
                )}
              </div>
            </div>
          )}

          {showHttp && (
            <div className="grid grid-cols-2 gap-3">
              {fld('Path', inp('http_path', 'text', '/'))}
              {fld('Host', inp('http_host'))}
            </div>
          )}

          {showKcp && (
            <div className="grid grid-cols-2 gap-3">
              {fld('Seed', inp('kcp_seed'))}
              {fld('Header type', inp('kcp_header', 'text', 'none'))}
            </div>
          )}
        </section>
      )}

      {/* Hysteria2 extras */}
      {isHy2 && (
        <section className="space-y-3">
          <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Hysteria2</h3>
          <div className="grid grid-cols-2 gap-3">
            {fld('SNI', inp('sni'))}
            {fld('Obfs type', inp('hy2_obfs'), 'e.g. salamander')}
            {fld('Obfs password', inp('hy2_obfs_password'))}
            <div className="flex items-center gap-2 mt-5">
              <input
                type="checkbox"
                id="insecure2"
                checked={form.allow_insecure}
                onChange={(e) => set('allow_insecure', e.target.checked)}
                className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
              />
              <label htmlFor="insecure2" className="text-sm text-gray-300">Allow insecure</label>
            </div>
            {form.allow_insecure && (
              <div className="rounded-lg bg-yellow-50 dark:bg-yellow-900/20 border border-yellow-200 dark:border-yellow-700/40 px-3 py-2 text-xs text-yellow-700 dark:text-yellow-300">
                ⚠ allowInsecure will be disabled by xray-core after 2026-06-01. Use pinnedPeerCertSha256 instead.
              </div>
            )}
          </div>
        </section>
      )}

      {/* Meta */}
      <section className="space-y-3">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider">Meta</h3>
        <div className="grid grid-cols-2 gap-3">
          {fld('Group', inp('group', 'text', 'default'))}
          {fld('Note', inp('note'))}
        </div>
        {/* Optional link to a Server (the VPS hosting this node). Pure
            metadata — does not affect routing. Empty option is shown
            even when no servers exist so the field doesn't look broken. */}
        {fld(
          <span className="flex items-center gap-1.5">
            {t('Linked server', 'Привязанный сервер')}
            <InfoTip text={t(
              'Optional: link this node to one of the VPS instances on the Servers tab. Purely informational — helps you remember which physical machine hosts this node.',
              'Опционально: привязать ноду к одному из серверов из вкладки Servers. Информационно — чтобы знать на какой машине крутится нода.',
            )} />
          </span>,
          <select
            value={serverId}
            onChange={(e) => setServerId(e.target.value)}
            className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
          >
            <option value="">{t('— none —', '— нет —')}</option>
            {servers.map((s) => (
              <option key={s.id} value={s.id.toString()}>
                {s.name} ({s.host})
              </option>
            ))}
          </select>,
        )}
      </section>

      {/* Chain tunnel */}
      <section className="space-y-3">
        <h3 className="text-xs font-semibold text-gray-500 uppercase tracking-wider flex items-center gap-1.5">
          Chain via (double tunnel)
          <InfoTip text="Advanced: route this node's traffic through another node as outer transport. Creates a double tunnel — the chain node is the outer (visible) layer, this node is the inner layer. Note: WireGuard nodes can't be a relay (xray can't tunnel through WireGuard) — they only work as the chain's exit, so they're not listed here." />
        </h3>
        <select
          value={chainNodeId}
          onChange={(e) => setChainNodeId(e.target.value)}
          className="w-full rounded-sm bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-hidden"
        >
          <option value="">None (single tunnel)</option>
          {nodes
            // WireGuard can't be a relay — xray can't tunnel traffic
            // THROUGH a WG outbound (0 bytes); it only works as the exit
            // hop. So exclude WG nodes from the "chain through" list.
            .filter((n) => n.id !== (initial as Node | undefined)?.id && n.protocol !== 'wireguard')
            .map((n) => (
              <option key={n.id} value={n.id.toString()}>
                {n.name} ({n.protocol} · {n.address})
              </option>
            ))}
        </select>
        {chainNodeId && (() => {
          const chainNode = nodes.find(n => n.id === Number(chainNodeId))
          return chainNode ? (
            <div className="rounded-xl border border-blue-200 dark:border-blue-800/50 bg-blue-50 dark:bg-blue-950/30 p-4 space-y-3 text-xs">
              <div className="flex items-center gap-2 text-blue-700 dark:text-blue-300 font-semibold">
                <span>🔗</span>
                <span>Double Tunnel Active</span>
              </div>
              <div className="space-y-1 text-gray-400 font-mono text-[11px] leading-5">
                <div className="text-gray-300">Traffic path:</div>
                <div className="pl-2 border-l-2 border-gray-700 space-y-1">
                  <div>Your device</div>
                  <div className="text-blue-600 dark:text-blue-400">↓ inner: encrypted by <span className="text-gray-200">[this node]</span></div>
                  <div className="text-yellow-600 dark:text-yellow-400">↓ outer: tunneled via <span className="text-gray-200">{chainNode.name}</span></div>
                  <div>Server <span className="text-gray-200">{chainNode.address}</span> → decrypts outer</div>
                  <div>Server <span className="text-gray-200">[this node address]</span> → decrypts inner</div>
                  <div className="text-green-600 dark:text-green-400">↓ Internet (clean traffic)</div>
                </div>
              </div>
              <div className="rounded-lg bg-gray-900 border border-gray-700 px-3 py-2 space-y-1">
                <div className="text-gray-300 font-medium">How to activate:</div>
                <div className="text-gray-400">Set <span className="text-white font-medium">[this node]</span> as Active Node on the Dashboard. The chain node <span className="text-yellow-700 dark:text-yellow-300">{chainNode.name}</span> is used automatically as outer transport — you do not set it as active node.</div>
              </div>
              <div className="text-gray-600">⚠ Both servers must be independently reachable from RPi. Test connectivity via Nodes → health check before using.</div>
            </div>
          ) : null
        })()}
      </section>

      {/* Actions */}
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
          {loading ? 'Saving…' : 'Save Node'}
        </button>
      </div>
    </form>
  )
}
