import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle, Network as NetworkIcon, Loader2, RefreshCw,
  Activity, Globe, Wifi, Save, Undo2, CheckCircle2, XCircle, History,
  Plus, X, Trash2,
} from 'lucide-react'
// NetworkIcon stays for the "Default gateway" tile inside `StateCard`.

import { networkApi, type NetworkBackup, type NetworkProbeResult } from '@/api/client'
import { useT } from '@/hooks/useT'
import { useConfirm } from '@/components/ConfirmModal'
import { HostDnsHelp } from '@/components/HostDnsHelp'

// This host-DNS apply path is IPv4-only on the backend (it sets
// `ipv4.dns` / writes resolv.conf nameservers). The host's resolv.conf
// often also carries an IPv6 nameserver handed out by the router via
// RA (e.g. fdc4:...::1) — that's managed by IPv6 RA, not by this form,
// so we filter it out of the editable list rather than flag it red and
// block Apply on something the operator can't fix here.
const isIpv4 = (s: string) => /^(?:\d{1,3}\.){3}\d{1,3}$/.test(s.trim())

/**
 * Host-level network state + apply form (since v1.3.3).
 *
 * Rendered INSIDE the existing Settings → Network section, below the
 * xray-config fields (INTERFACE / LAN_CIDR / GATEWAY_IP / ROUTER_IP).
 * Those settings live in PiTun's own DB and drive xray's TPROXY
 * config — they don't touch the Linux host's networking. THIS block
 * is the other half: read the live host state (`ip route`, `ip addr`,
 * /etc/network/interfaces) and let the operator change the host's
 * default gateway + DNS without leaving the UI.
 *
 * IP/CIDR mutations are intentionally NOT exposed here — changing the
 * host IP drops the operator's SSH/web session and would need an
 * auto-rollback timer (not in scope for 1.3.3). Gateway + DNS only.
 */
export default function HostNetworkSection() {
  const t = useT()
  const confirm = useConfirm()
  const qc = useQueryClient()

  const { data: state, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['network-state'],
    queryFn: () => networkApi.getState(),
    refetchInterval: 10_000,
  })

  const { data: backupsData } = useQuery({
    queryKey: ['network-backups'],
    queryFn: () => networkApi.listBackups(),
    refetchInterval: 30_000,
  })

  // Self-loop = the default route points at the box's own IP. Far more
  // severe than the amber advisories (it kills ALL outbound traffic), so
  // it gets red styling and a flag on the gateway card. Backend already
  // emits the explanatory warning text; this is the visual escalation.
  const selfLoop = !!(state && state.gateway && state.ip && state.gateway === state.ip)

  return (
    <div className="space-y-3">
      <div className="flex items-start justify-between gap-2">
        <p className="text-[11px] text-gray-500 max-w-xl">
          {t(
            'Live host network — distinct from the xray-config fields above. Apply rewrites /etc/network/interfaces and runtime routes. Gateway/DNS changes are safe — your SSH session survives. Every apply auto-creates a rollback backup.',
            'Реальная host-сеть — отдельно от xray-полей выше. Apply правит /etc/network/interfaces и runtime-маршруты. Смена gateway/DNS не разрывает SSH. На каждый apply сохраняется бэкап.',
          )}
        </p>
        <button
          type="button"
          onClick={() => refetch()}
          disabled={isFetching}
          className="shrink-0 inline-flex items-center gap-1.5 rounded-md border border-gray-700 bg-gray-900 hover:bg-gray-800 hover:border-brand-400/40 hover:text-brand-700 dark:hover:text-brand-300 px-2 py-1 text-[11px] text-gray-300 disabled:opacity-50"
          title={t('Refresh host state', 'Обновить host-состояние')}
        >
          {isFetching
            ? <Loader2 className="h-3 w-3 animate-spin" />
            : <RefreshCw className="h-3 w-3" />}
          {t('Refresh', 'Обновить')}
        </button>
      </div>

      {isLoading && (
        <div className="flex items-center justify-center py-12 text-gray-500 text-sm">
          <Loader2 className="h-5 w-5 animate-spin mr-2" />
          {t('Loading network state…', 'Загрузка сетевого состояния…')}
        </div>
      )}

      {isError && (
        <div className="flex items-start gap-2 rounded-md border border-red-200 dark:border-red-900/60 bg-red-50 dark:bg-red-900/20 p-3 text-[12px] text-red-800 dark:text-red-200">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <div>{t('Failed to read network state from backend.', 'Не удалось получить сетевое состояние с backend.')}</div>
        </div>
      )}

      {state && (
        <>
          {/* Warnings get top billing — these are the things that ruin
             your day if you ignore them. The "another PiTun upstream"
             one is the primary motivator for this whole page. */}
          {state.warnings.length > 0 && (
            <div className="space-y-2">
              {state.warnings.map((w, i) => {
                // The self-loop warning (backend emits it first) is red;
                // the rest stay amber advisories.
                const danger = selfLoop && i === 0
                return (
                  <div
                    key={i}
                    className={
                      'flex items-start gap-2 rounded-md border p-3 text-[12px] ' +
                      (danger
                        ? 'border-red-300 dark:border-red-900/60 bg-red-50 dark:bg-red-900/20 text-red-800 dark:text-red-200'
                        : 'border-amber-200 dark:border-amber-900/60 bg-amber-50 dark:bg-amber-900/20 text-amber-800 dark:text-amber-200')
                    }
                  >
                    <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
                    <div>{w}</div>
                  </div>
                )
              })}
            </div>
          )}

          <div className="grid sm:grid-cols-2 gap-3">
            <StateCard
              icon={<Wifi className="h-4 w-4 text-brand-400" />}
              label={t('Interface', 'Интерфейс')}
              value={state.interface || '—'}
              sub={t(
                `Network manager: ${state.manager}`,
                `Сетевой менеджер: ${state.manager}`,
              )}
            />
            <StateCard
              icon={<Activity className="h-4 w-4 text-brand-400" />}
              label={t('Mode', 'Режим')}
              value={state.mode.toUpperCase()}
              sub={
                state.mode === 'dhcp'
                  ? t('IP/gateway/DNS pushed by DHCP server', 'IP/шлюз/DNS получены от DHCP')
                  : state.mode === 'static'
                  ? t('Manually configured', 'Настроено вручную')
                  : t('Could not determine from manager config', 'Не удалось определить из конфигов менеджера')
              }
            />
            <StateCard
              icon={<NetworkIcon className="h-4 w-4 text-brand-400" />}
              label={t('Host IP', 'IP хоста')}
              value={state.ip
                ? `${state.ip}${state.cidr != null ? `/${state.cidr}` : ''}`
                : '—'}
              sub={t('LAN clients reach the PiTun web UI here.', 'LAN-клиенты заходят на веб-морду по этому адресу.')}
              mono
            />
            <StateCard
              icon={<Globe className={`h-4 w-4 ${selfLoop ? 'text-red-500' : 'text-brand-400'}`} />}
              label={t('Default gateway', 'Default gateway')}
              value={state.gateway || '—'}
              sub={selfLoop
                ? t(
                    'SELF-LOOP: this points at PiTun itself. Set it to your ISP router below.',
                    'SELF-LOOP: указывает на сам PiTun. Задайте адрес ISP-роутера ниже.',
                  )
                : t(
                    'Where this PiTun sends outbound traffic. Should point at your ISP router, not another PiTun.',
                    'Куда сам PiTun отправляет исходящий трафик. Должен указывать на ваш ISP-роутер, не на другой PiTun.',
                  )}
              mono
              danger={selfLoop}
            />
          </div>

          <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3">
            <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-2">
              {t('DNS servers', 'DNS-серверы')}
            </div>
            {state.dns.length === 0 ? (
              <div className="text-[12px] text-gray-500">
                {t('No nameservers in /etc/resolv.conf.', 'В /etc/resolv.conf нет nameserver-записей.')}
              </div>
            ) : (
              <ul className="space-y-1">
                {state.dns.map((dns, i) => (
                  <li key={i} className="font-mono text-[12px] text-gray-200">
                    {dns}
                  </li>
                ))}
              </ul>
            )}
          </div>

          <ApplyForm
            currentGateway={state.gateway}
            currentDns={state.dns}
            disabled={state.manager !== 'ifupdown' && state.manager !== 'networkmanager'}
            disabledReason={
              state.manager === 'unknown'
                ? t('Active network manager could not be detected.', 'Не удалось определить активный сетевой менеджер.')
                : t(
                    `Manager ${state.manager} not yet supported for apply — edit config files manually.`,
                    `Менеджер ${state.manager} ещё не поддерживается для apply — правьте конфиг руками.`,
                  )
            }
            onApplied={() => {
              qc.invalidateQueries({ queryKey: ['network-state'] })
              qc.invalidateQueries({ queryKey: ['network-backups'] })
            }}
          />

          <BackupsPanel
            backups={backupsData?.items || []}
            onRolledBack={async () => {
              qc.invalidateQueries({ queryKey: ['network-state'] })
              qc.invalidateQueries({ queryKey: ['network-backups'] })
            }}
            confirm={confirm}
          />
        </>
      )}
    </div>
  )
}

// ── Subcomponents ────────────────────────────────────────────────────────

function StateCard({
  icon, label, value, sub, mono, danger,
}: {
  icon: React.ReactNode
  label: string
  value: string
  sub?: string
  mono?: boolean
  danger?: boolean
}) {
  return (
    <div className={
      'rounded-lg border p-3 ' +
      (danger
        ? 'border-red-300 dark:border-red-900/60 bg-red-50 dark:bg-red-900/15'
        : 'border-gray-800 bg-gray-900/40')
    }>
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-gray-500 mb-1">
        {icon}
        {label}
      </div>
      <div className={
        `text-sm ${mono ? 'font-mono ' : ''}` +
        (danger ? 'text-red-700 dark:text-red-300' : 'text-gray-100')
      }>
        {value}
      </div>
      {sub && (
        <div className={'text-[10px] mt-1 ' + (danger ? 'text-red-600 dark:text-red-400' : 'text-gray-600')}>
          {sub}
        </div>
      )}
    </div>
  )
}

function ApplyForm({
  currentGateway, currentDns, disabled, disabledReason, onApplied,
}: {
  currentGateway: string | null
  currentDns: string[]
  disabled: boolean
  disabledReason: string
  onApplied: () => void
}) {
  const t = useT()
  const [gateway, setGateway] = useState('')
  const [dnsList, setDnsList] = useState<string[]>([])
  const [probe, setProbe] = useState<NetworkProbeResult | null>(null)
  const [probing, setProbing] = useState(false)
  const [applyOk, setApplyOk] = useState<string | null>(null)
  const [applyErr, setApplyErr] = useState<string | null>(null)

  // IPv4 nameservers from current state — the editable list is IPv4-only
  // (see isIpv4 note). Any router-RA IPv6 nameserver is excluded here so
  // it neither shows red nor blocks Apply.
  const currentDnsV4 = currentDns.filter(isIpv4)

  // Initial fill from current state — but only ONCE per current-state
  // change. If the user has started typing, don't clobber.
  useEffect(() => {
    if (gateway === '' && currentGateway) setGateway(currentGateway)
    if (dnsList.length === 0 && currentDnsV4.length > 0) setDnsList([...currentDnsV4])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentGateway, currentDnsV4.join(',')])

  const probeMut = useMutation({
    mutationFn: (ip: string) => networkApi.probe(ip),
    onMutate: () => { setProbing(true); setProbe(null) },
    onSettled: () => setProbing(false),
    onSuccess: (data) => setProbe(data),
  })

  const applyMut = useMutation({
    mutationFn: (body: { gateway?: string; dns?: string[] }) => networkApi.apply(body),
    onMutate: () => { setApplyOk(null); setApplyErr(null) },
    onSuccess: (data) => {
      setApplyOk(t(
        `Applied. Backup ${data.backup.id} saved — use Revert in the Backups list to roll back.`,
        `Применено. Бэкап ${data.backup.id} сохранён — кнопка Revert в списке бэкапов откатит обратно.`,
      ))
      onApplied()
    },
    onError: (e: Error & { response?: { data?: { detail?: string } } }) => {
      setApplyErr(e.response?.data?.detail || e.message || 'Apply failed')
    },
  })

  const gatewayChanged = gateway && gateway !== currentGateway
  // Compare against the IPv4-filtered current list so the form isn't
  // marked "changed" just because we dropped a router-RA IPv6 entry.
  const dnsChanged = JSON.stringify(dnsList) !== JSON.stringify(currentDnsV4)
  const hasChanges = Boolean(gatewayChanged || dnsChanged)

  // IPv4 syntactic check — backend re-validates, this is just UX.
  const validIp = isIpv4
  const gatewayValid = !gateway || validIp(gateway)
  const dnsAllValid = dnsList.every(validIp)

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-[11px] uppercase tracking-wider text-gray-500">
            {t('Change gateway / DNS', 'Изменить gateway / DNS')}
          </span>
          <HostDnsHelp highlight="network" />
        </div>
        {disabled && (
          <span className="text-[10px] text-amber-700 dark:text-amber-300">{disabledReason}</span>
        )}
      </div>

      {/* Gateway */}
      <div className="space-y-1">
        <label className="text-[11px] text-gray-400 font-medium">
          {t('Default gateway', 'Default gateway')}
          <span className="text-gray-600 ml-2">{t('(IPv4)', '(IPv4)')}</span>
        </label>
        <div className="flex gap-1.5">
          <input
            type="text"
            value={gateway}
            onChange={(e) => { setGateway(e.target.value); setProbe(null) }}
            disabled={disabled}
            placeholder={currentGateway || '192.168.1.1'}
            className={
              'flex-1 rounded-md border bg-gray-900 px-2 py-1.5 text-[12px] font-mono text-gray-100 focus:outline-hidden ' +
              (!gatewayValid
                ? 'border-red-200 dark:border-red-700/60 focus:border-red-600'
                : 'border-gray-700 focus:border-brand-700')
            }
          />
          <button
            type="button"
            onClick={() => gateway && gatewayValid && probeMut.mutate(gateway)}
            disabled={disabled || !gateway || !gatewayValid || probing}
            className="rounded-md border border-gray-700 hover:border-brand-400/50 hover:text-brand-700 dark:hover:text-brand-300 hover:bg-brand-50 dark:hover:bg-brand-500/12 px-2.5 py-1 text-[11px] text-gray-300 disabled:opacity-50 inline-flex items-center gap-1.5"
            title={t('Ping the candidate gateway from the host', 'Пинг кандидата с хоста')}
          >
            {probing
              ? <Loader2 className="h-3 w-3 animate-spin" />
              : <Activity className="h-3 w-3" />}
            {t('Test', 'Проверить')}
          </button>
        </div>
        {!gatewayValid && (
          <div className="text-[10px] text-red-700 dark:text-red-300">
            {t('Not a valid IPv4 address.', 'Невалидный IPv4-адрес.')}
          </div>
        )}
        {probe && (
          <div className={
            'flex items-start gap-1.5 rounded-md border px-2 py-1 text-[11px] ' +
            (probe.reachable
              ? 'border-emerald-200 dark:border-emerald-700/40 bg-emerald-50 dark:bg-emerald-900/15 text-emerald-800 dark:text-emerald-200'
              : 'border-red-200 dark:border-red-700/40 bg-red-50 dark:bg-red-900/15 text-red-800 dark:text-red-200')
          }>
            {probe.reachable
              ? <CheckCircle2 className="h-3.5 w-3.5 shrink-0 mt-0.5" />
              : <XCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />}
            {probe.detail}
          </div>
        )}
      </div>

      {/* DNS list */}
      <div className="space-y-1">
        <label className="text-[11px] text-gray-400 font-medium">
          {t('DNS servers', 'DNS-серверы')}
        </label>
        <p className="text-[10px] text-gray-600 -mt-0.5">
          {t(
            "The box's PRIMARY DNS — tried first for its OWN lookups (subscriptions, geo, panels, health checks). Add extra fallbacks on the DNS page.",
            'ОСНОВНОЙ DNS машины — пробуется первым для её СОБСТВЕННЫХ запросов (подписки, гео, панели, health-проверки). Доп. fallback добавляй на странице DNS.',
          )}
        </p>
        {dnsList.map((dns, i) => (
          <div key={i} className="flex gap-1.5">
            <input
              type="text"
              value={dns}
              onChange={(e) => {
                const copy = [...dnsList]
                copy[i] = e.target.value
                setDnsList(copy)
              }}
              disabled={disabled}
              placeholder="1.1.1.1"
              className={
                'flex-1 rounded-md border bg-gray-900 px-2 py-1.5 text-[12px] font-mono text-gray-100 focus:outline-hidden ' +
                (dns && !validIp(dns)
                  ? 'border-red-200 dark:border-red-700/60 focus:border-red-600'
                  : 'border-gray-700 focus:border-brand-700')
              }
            />
            <button
              type="button"
              onClick={() => setDnsList(dnsList.filter((_, j) => j !== i))}
              disabled={disabled}
              className="rounded-md border border-gray-700 hover:border-red-700/50 hover:text-red-700 dark:hover:text-red-300 px-2 py-1 text-gray-500"
              title={t('Remove', 'Удалить')}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        ))}
        <button
          type="button"
          onClick={() => setDnsList([...dnsList, ''])}
          disabled={disabled}
          className="rounded-md border border-gray-700 hover:border-brand-400/50 hover:text-brand-700 dark:hover:text-brand-300 hover:bg-brand-50 dark:hover:bg-brand-500/12 px-2 py-1 text-[11px] text-gray-400 inline-flex items-center gap-1.5"
        >
          <Plus className="h-3 w-3" />
          {t('Add DNS server', 'Добавить DNS-сервер')}
        </button>
      </div>

      {/* Result banners */}
      {applyOk && (
        <div className="flex items-start gap-2 rounded-md border border-emerald-200 dark:border-emerald-700/40 bg-emerald-50 dark:bg-emerald-900/20 p-2 text-[12px] text-emerald-800 dark:text-emerald-200">
          <CheckCircle2 className="h-4 w-4 shrink-0 mt-0.5" />
          <div>{applyOk}</div>
        </div>
      )}
      {applyErr && (
        <div className="flex items-start gap-2 rounded-md border border-red-200 dark:border-red-700/40 bg-red-50 dark:bg-red-900/20 p-2 text-[12px] text-red-800 dark:text-red-200">
          <XCircle className="h-4 w-4 shrink-0 mt-0.5" />
          <div>{applyErr}</div>
        </div>
      )}

      {/* Submit */}
      <div className="flex items-center gap-2 pt-1 border-t border-gray-800">
        <button
          type="button"
          onClick={() => {
            const body: { gateway?: string; dns?: string[] } = {}
            if (gatewayChanged) body.gateway = gateway
            if (dnsChanged) body.dns = dnsList.filter((d) => d.trim())
            applyMut.mutate(body)
          }}
          disabled={disabled || !hasChanges || !gatewayValid || !dnsAllValid || applyMut.isPending}
          className="inline-flex items-center gap-1.5 rounded-md bg-brand-600 hover:bg-brand-500 disabled:opacity-50 disabled:cursor-not-allowed px-3 py-1.5 text-[12px] text-white font-medium"
        >
          {applyMut.isPending
            ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
            : <Save className="h-3.5 w-3.5" />}
          {t('Apply', 'Применить')}
        </button>
        {hasChanges && (
          <span className="text-[10px] text-gray-500">
            {t(
              'A backup of the current state is created before applying.',
              'Перед применением создаётся бэкап текущего состояния.',
            )}
          </span>
        )}
      </div>
    </div>
  )
}

function BackupsPanel({
  backups, onRolledBack, confirm,
}: {
  backups: NetworkBackup[]
  onRolledBack: () => void
  confirm: ReturnType<typeof useConfirm>
}) {
  const t = useT()
  const [rollingBack, setRollingBack] = useState<string | null>(null)
  const [deleting, setDeleting] = useState<string | null>(null)
  const [clearingAll, setClearingAll] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  // Helper to extract a backend `detail` field or fall back to .message
  // — keeps the error-render branches in the JSX identical for
  // rollback / delete / clear-all.
  const showError = (e: unknown) => {
    const x = e as Error & { response?: { data?: { detail?: string } } }
    setErr(x.response?.data?.detail || x.message || 'Operation failed')
  }

  if (backups.length === 0) {
    return (
      <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-gray-500 mb-1">
          <History className="h-4 w-4 text-brand-400" />
          {t('Backups', 'Бэкапы')}
        </div>
        <div className="text-[12px] text-gray-500">
          {t(
            'No backups yet. One will be created automatically on every Apply.',
            'Бэкапов пока нет. Создаются автоматически при каждом Apply.',
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900/40 p-3 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-wider text-gray-500">
          <History className="h-4 w-4 text-brand-400" />
          {t('Backups', 'Бэкапы')}
          <span className="text-gray-600">({backups.length})</span>
          {/* Hint about the rolling-window cap (MAX_BACKUPS=10 in
             core/network_apply.py). Helps the operator understand why
             very old backups disappear on their own. */}
          <span className="text-gray-700 normal-case tracking-normal">
            {t('— last 10 kept', '— хранятся последние 10')}
          </span>
        </div>
        {/* Bulk delete only when there are at least two — single-item
           list is faster to clear via the per-row trash icon. */}
        {backups.length > 1 && (
          <button
            type="button"
            onClick={async () => {
              setErr(null)
              const ok = await confirm({
                title: t('Clear all backups?', 'Удалить все бэкапы?'),
                body: t(
                  `${backups.length} backup(s) will be permanently deleted. This does NOT change current network state — only purges rollback history.`,
                  `${backups.length} бэкап(ов) будут удалены безвозвратно. Текущее сетевое состояние не меняется — чистится только история откатов.`,
                ),
                confirmLabel: t('Clear all', 'Удалить все'),
                danger: true,
              })
              if (!ok) return
              setClearingAll(true)
              try {
                await networkApi.clearBackups()
                onRolledBack()  // same query-invalidate path as a successful revert
              } catch (e) {
                showError(e)
              } finally {
                setClearingAll(false)
              }
            }}
            disabled={clearingAll}
            className="shrink-0 inline-flex items-center gap-1 rounded-md border border-gray-700 hover:border-red-700/50 hover:text-red-700 dark:hover:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/20 px-2 py-1 text-[10px] text-gray-400 disabled:opacity-50"
            title={t('Delete every backup', 'Удалить все бэкапы')}
          >
            {clearingAll
              ? <Loader2 className="h-3 w-3 animate-spin" />
              : <Trash2 className="h-3 w-3" />}
            {t('Clear all', 'Очистить')}
          </button>
        )}
      </div>
      {err && (
        <div className="flex items-start gap-2 rounded-md border border-red-200 dark:border-red-700/40 bg-red-50 dark:bg-red-900/20 p-2 text-[12px] text-red-800 dark:text-red-200">
          <XCircle className="h-4 w-4 shrink-0 mt-0.5" />
          <div>{err}</div>
        </div>
      )}
      <div className="space-y-1.5">
        {backups.map((b, i) => {
          const isNewest = i === 0
          const isRolling = rollingBack === b.id
          const isDeleting = deleting === b.id
          return (
            <div
              key={b.id}
              className="flex items-start justify-between gap-2 rounded-md border border-gray-800 bg-gray-950/40 px-2.5 py-1.5"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-[12px] text-gray-100 font-mono">{b.id}</span>
                  {isNewest && (
                    <span className="text-[9px] uppercase tracking-wider text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/30 border border-emerald-200 dark:border-emerald-700/40 px-1 py-0.5 rounded-sm">
                      {t('newest', 'свежий')}
                    </span>
                  )}
                </div>
                <div className="text-[10px] text-gray-600 mt-0.5">
                  {new Date(b.created_at).toLocaleString()} · {b.manager} · {b.interface}
                </div>
                <div className="text-[10px] text-gray-500 mt-0.5 font-mono">
                  gw={b.live_state.gateway || '—'},{' '}
                  dns=[{(b.live_state.dns || []).join(', ') || '—'}]
                </div>
              </div>
              <div className="shrink-0 flex items-center gap-1">
                <button
                  type="button"
                  onClick={async () => {
                    setErr(null)
                    const ok = await confirm({
                      title: t('Roll back to this backup?', 'Откатиться к этому бэкапу?'),
                      body: t(
                        `Restore gateway ${b.live_state.gateway || '—'} and DNS [${(b.live_state.dns || []).join(', ') || '—'}]. The current network config will be replaced by this snapshot.`,
                        `Восстановить gateway ${b.live_state.gateway || '—'} и DNS [${(b.live_state.dns || []).join(', ') || '—'}]. Текущая конфигурация заменится этим снэпшотом.`,
                      ),
                      confirmLabel: t('Roll back', 'Откатить'),
                      danger: true,
                    })
                    if (!ok) return
                    setRollingBack(b.id)
                    try {
                      await networkApi.rollback(b.id)
                      onRolledBack()
                    } catch (e) {
                      showError(e)
                    } finally {
                      setRollingBack(null)
                    }
                  }}
                  disabled={isRolling || isDeleting}
                  className="inline-flex items-center gap-1 rounded-md border border-gray-700 hover:border-amber-700/50 hover:text-amber-700 dark:hover:text-amber-300 hover:bg-amber-50 dark:hover:bg-amber-900/20 px-2 py-1 text-[11px] text-gray-300 disabled:opacity-50"
                >
                  {isRolling
                    ? <Loader2 className="h-3 w-3 animate-spin" />
                    : <Undo2 className="h-3 w-3" />}
                  {t('Revert', 'Откатить')}
                </button>
                {/* Per-row delete. Skipped on newest backup IF it's the
                   only safety net — deleting your last rollback point
                   while you might still want it is a footgun. With 2+
                   backups, delete on the newest is fine because the
                   one below it is still a viable rollback target. */}
                <button
                  type="button"
                  onClick={async () => {
                    setErr(null)
                    const ok = await confirm({
                      title: t('Delete this backup?', 'Удалить этот бэкап?'),
                      body: t(
                        `Backup ${b.id} will be deleted. The current network state is NOT affected — only this rollback point is removed.`,
                        `Бэкап ${b.id} будет удалён. Текущее сетевое состояние не меняется — удаляется только эта точка отката.`,
                      ),
                      confirmLabel: t('Delete', 'Удалить'),
                      danger: true,
                    })
                    if (!ok) return
                    setDeleting(b.id)
                    try {
                      await networkApi.deleteBackup(b.id)
                      onRolledBack()
                    } catch (e) {
                      showError(e)
                    } finally {
                      setDeleting(null)
                    }
                  }}
                  disabled={isRolling || isDeleting || (isNewest && backups.length === 1)}
                  className="rounded-md border border-gray-700 hover:border-red-700/50 hover:text-red-700 dark:hover:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/20 p-1 text-gray-500 disabled:opacity-30 disabled:cursor-not-allowed"
                  title={isNewest && backups.length === 1
                    ? t('Cannot delete your only backup', 'Нельзя удалить единственный бэкап')
                    : t('Delete this backup', 'Удалить этот бэкап')}
                >
                  {isDeleting
                    ? <Loader2 className="h-3 w-3 animate-spin" />
                    : <Trash2 className="h-3 w-3" />}
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
