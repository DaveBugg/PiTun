import { useQuery } from '@tanstack/react-query'
import { Cable, Loader2, AlertTriangle } from 'lucide-react'
import { clsx } from 'clsx'

import { networkApi } from '@/api/client'
import { useT } from '@/hooks/useT'
import { useAppStore } from '@/store'
import { translateWifiDetail } from '@/lib/wifiDetail'

/**
 * Operating mode + physical NIC inventory.
 *
 * "Router" is only offered when the box actually has two or more real NICs —
 * it needs one facing the ISP and one facing the LAN. The backend enforces the
 * same rule; this is the explanation, not the gate.
 *
 * Saving a switch to router applies immediately: WAN firewall, NAT, DHCP and
 * the access point all come up together, guarded by the commit-confirm
 * watchdog. The warning below the selector says so, because the difference
 * between "recorded" and "the network just changed" is the whole risk here.
 */
export default function OperatingModeSection({
  value, onChange, wan, lan, lanExtra, onRoleChange, dhcp, onDhcpChange,
}: {
  value: string
  onChange: (mode: string) => void
  wan: string
  lan: string
  lanExtra: string
  onRoleChange: (
    role: 'wan_interface' | 'lan_interface' | 'lan_extra_interfaces',
    iface: string,
  ) => void
  dhcp: { enabled: boolean; poolStart: string; poolEnd: string; leaseHours: string }
  onDhcpChange: (key: string, value: string | boolean) => void
}) {
  const t = useT()
  const lang = useAppStore((s) => s.lang)
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['network-interfaces'],
    queryFn: () => networkApi.interfaces(),
    refetchInterval: 30_000,
  })

  const capable = data?.router_capable ?? false
  const nics = data?.items ?? []
  // "We could not ask" is not "the box has no ports". Rendering a failed
  // request as `0 ports` tells the operator something about their hardware
  // that we never established — and on a box running an older backend, where
  // this endpoint 404s, that reads as "your NICs vanished".
  const detectionFailed = isError && !isLoading
  const lanExtras = (lanExtra || '').split(',').map((s) => s.trim()).filter(Boolean)
  const lanIsWireless = nics.find((n) => n.name === lan)?.wireless ?? false
  const unclaimed = data?.unclaimed ?? []

  return (
    <div className="space-y-3">
      {/* NIC inventory */}
      <div>
        <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1.5">
          {t('Network ports', 'Сетевые порты')}
          {data && <span className="ml-1.5 text-gray-600 normal-case">({data.count})</span>}
        </div>
        {isLoading ? (
          <div className="flex items-center gap-2 text-[12px] text-gray-500">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            {t('Detecting…', 'Определяем…')}
          </div>
        ) : detectionFailed ? (
          <div className="flex items-start gap-2 rounded-md border border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-950/30 p-2 text-[11px] text-amber-800 dark:text-amber-300">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <div>
              {t(
                'Could not read the network ports from the backend, so nothing below reflects this hardware.',
                'Не удалось получить список сетевых портов от бэкенда — всё ниже не отражает это железо.',
              )}
              <div className="mt-0.5 opacity-80 font-mono">
                {(error as { response?: { status?: number } })?.response?.status === 404
                  ? t(
                      'The endpoint is missing — this box is running a backend older than router mode.',
                      'Эндпоинта нет — на этой машине бэкенд старее, чем режим роутера.',
                    )
                  : String((error as Error)?.message ?? '')}
              </div>
            </div>
          </div>
        ) : nics.length === 0 && unclaimed.length === 0 ? (
          <div className="text-[12px] text-gray-500">
            {t('No physical interfaces detected.', 'Физические интерфейсы не найдены.')}
          </div>
        ) : (
          <div className="space-y-1">
            {nics.map((n) => (
              <div
                key={n.name}
                className="flex items-center gap-2 rounded-md border border-gray-800 bg-gray-950/40 px-2.5 py-1.5 text-[12px]"
              >
                <Cable className={clsx('h-3.5 w-3.5 shrink-0', n.carrier ? 'text-emerald-500' : 'text-gray-600')} />
                <span className="font-mono text-gray-200">{n.name}</span>
                <span className="font-mono text-gray-600 text-[11px]">{n.mac}</span>
                <span className="flex-1" />
                {n.ipv4 && (
                  <span className="font-mono text-gray-400">
                    {n.ipv4}{n.cidr != null ? `/${n.cidr}` : ''}
                  </span>
                )}
                {n.is_default_route && (
                  <span className="rounded-sm border border-brand-200 bg-brand-50 text-brand-700 dark:border-brand-800/50 dark:bg-brand-900/30 dark:text-brand-300 px-1.5 py-0.5 text-[10px]">
                    {t('uplink', 'аплинк')}
                  </span>
                )}
                {n.wireless && (
                  <span
                    className={clsx(
                      'rounded-sm border px-1.5 py-0.5 text-[10px]',
                      n.ap_capable === true
                        ? 'border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-800/50 dark:bg-emerald-950/30 dark:text-emerald-300'
                        : n.ap_capable === false
                        ? 'border-gray-700 text-gray-500'
                        : 'border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-800/50 dark:bg-amber-950/30 dark:text-amber-300',
                    )}
                    title={translateWifiDetail(n.wifi_detail, lang)}
                  >
                    {n.ap_capable === true
                      ? t('WiFi · can serve', 'WiFi · может раздавать')
                      : n.ap_capable === false
                      ? t('WiFi · client only', 'WiFi · только клиент')
                      : t('WiFi · unknown', 'WiFi · неизвестно')}
                  </span>
                )}
                <span className={clsx('text-[10px]', n.carrier ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-600')}>
                  {n.carrier ? t('link up', 'линк есть') : t('no link', 'нет линка')}
                </span>
              </div>
            ))}
          </div>
        )}

        {/* Hardware on the bus that produced no interface. Saying "not found"
            about a card that is physically present sends people looking
            anywhere but at the driver. */}
        {unclaimed.length > 0 && (
          <div className="mt-2 flex items-start gap-2 rounded-md border border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-950/30 p-2 text-[11px] text-amber-800 dark:text-amber-300">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            <div className="space-y-1">
              <div>
                {t(
                  'A network card is present but produced no interface, so PiTun cannot use it.',
                  'Сетевая карта присутствует, но интерфейс не создан — PiTun её использовать не может.',
                )}
              </div>
              {unclaimed.map((u) => (
                <div key={u.slot} className="font-mono opacity-90">
                  {u.slot} · {u.vendor}:{u.device}
                  {u.kind === 'wireless' ? t(' · wireless', ' · беспроводная') : ''}
                  {' — '}
                  {u.reason === 'no_driver'
                    ? t('no driver is bound to it', 'к ней не привязан драйвер')
                    : t(
                        `driver ${u.driver} is loaded but the device never came up — usually missing firmware`,
                        `драйвер ${u.driver} загружен, но устройство не поднялось — как правило, нет прошивки`,
                      )}
                </div>
              ))}
              <div className="opacity-80">
                {t(
                  'On Debian, firmware for most adapters lives in the non-free-firmware component, which a slimmed-down image often has disabled.',
                  'В Debian прошивки большинства адаптеров лежат в компоненте non-free-firmware, который в урезанных образах часто отключён.',
                )}
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Mode choice */}
      <div>
        <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1.5">
          {t('Operating mode', 'Режим работы')}
        </div>
        <div className="space-y-2">
          <label className="flex items-start gap-2 rounded-lg border border-gray-800 bg-gray-950/40 p-2.5 cursor-pointer hover:bg-gray-900/60">
            <input
              type="radio"
              className="mt-0.5"
              checked={value !== 'router'}
              onChange={() => onChange('gateway')}
            />
            <div>
              <div className="text-[12px] text-gray-200">
                {t('Gateway — beside your router', 'Шлюз — рядом с роутером')}
                <span className="ml-2 text-[10px] text-gray-500">{t('default', 'по умолчанию')}</span>
              </div>
              <div className="text-[11px] text-gray-500 mt-0.5">
                {t(
                  'Devices point their gateway at PiTun; your existing router keeps doing DHCP and NAT. Works on any hardware, including a single port.',
                  'Устройства указывают шлюзом PiTun; DHCP и NAT остаются на вашем роутере. Работает на любом железе, в том числе с одним портом.',
                )}
              </div>
            </div>
          </label>

          <label
            className={clsx(
              'flex items-start gap-2 rounded-lg border p-2.5',
              capable
                ? 'border-gray-800 bg-gray-950/40 cursor-pointer hover:bg-gray-900/60'
                : 'border-gray-800 bg-gray-950/20 opacity-60 cursor-not-allowed',
            )}
          >
            <input
              type="radio"
              className="mt-0.5"
              disabled={!capable}
              title={detectionFailed
                ? t('Port detection failed — cannot tell whether this box can be a router.',
                    'Определить порты не удалось — неизвестно, может ли эта машина быть роутером.')
                : undefined}
              checked={value === 'router'}
              onChange={() => onChange('router')}
            />
            <div>
              <div className="text-[12px] text-gray-200">
                {t('Router — PiTun is the router', 'Роутер — PiTun и есть роутер')}
              </div>
              <div className="text-[11px] text-gray-500 mt-0.5">
                {t(
                  'PiTun owns the ISP uplink, hands out addresses on the LAN and does NAT. Needs two or more ports.',
                  'PiTun берёт на себя аплинк провайдера, раздаёт адреса в LAN и делает NAT. Нужно два и более порта.',
                )}
              </div>
            </div>
          </label>
        </div>

        {!capable && !isLoading && !detectionFailed && (
          <div className="mt-2 flex items-start gap-2 rounded-md border border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-950/30 p-2 text-[11px] text-amber-800 dark:text-amber-300">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            {t(
              `Router mode needs at least 2 physical ports — this box has ${data?.count ?? 0}.`,
              `Для режима роутера нужно минимум 2 физических порта — на этой машине ${data?.count ?? 0}.`,
            )}
          </div>
        )}

        {/* Port roles — only meaningful once router mode is chosen. The
           backend validates the same rules; this narrows the choices so the
           operator doesn't have to discover them by hitting an error. */}
        {value === 'router' && (
          <div className="mt-3 grid sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] font-medium text-gray-400 mb-1">
                {t('WAN port — faces the ISP', 'WAN-порт — в сторону провайдера')}
              </label>
              <select
                value={wan}
                onChange={(e) => onRoleChange('wan_interface', e.target.value)}
                className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:outline-hidden focus:border-gray-600"
              >
                <option value="">{t('— choose —', '— выберите —')}</option>
                {nics.filter((n) => n.name !== lan).map((n) => (
                  <option key={n.name} value={n.name}>
                    {n.name}{n.is_default_route ? t(' (current uplink)', ' (текущий аплинк)') : ''}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-gray-400 mb-1">
                {t('LAN port — faces your network', 'LAN-порт — в сторону сети')}
              </label>
              <select
                value={lan}
                onChange={(e) => onRoleChange('lan_interface', e.target.value)}
                className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:outline-hidden focus:border-gray-600"
              >
                <option value="">{t('— choose —', '— выберите —')}</option>
                {nics
                  .filter((n) => n.name !== wan)
                  // A client-only radio can't run an access point, so it can
                  // never serve the LAN — leave it out rather than let the
                  // operator pick it and get a 400.
                  .filter((n) => !n.wireless || n.ap_capable === true)
                  .map((n) => (
                    <option key={n.name} value={n.name}>
                      {n.name}{n.wireless ? t(' (WiFi AP)', ' (WiFi AP)') : ''}
                    </option>
                  ))}
              </select>
              {nics.some((n) => n.wireless && n.ap_capable !== true) && (
                <p className="mt-1 text-[10px] text-gray-600">
                  {t(
                    'Wireless adapters that cannot run an access point are not listed.',
                    'Беспроводные адаптеры без поддержки точки доступа не показаны.',
                  )}
                </p>
              )}

              {/* A LAN of several ports is bridged into one segment, so the
                  extra ones are picked here rather than being separate
                  networks. The primary keeps the address; the bridge takes it
                  over while router mode is on. */}
              {lan && nics.filter((n) => n.name !== wan && n.name !== lan
                                         && (!n.wireless || n.ap_capable === true)).length > 0 && (
                <div className="mt-2">
                  <div className="text-[11px] text-gray-500 mb-1">
                    {t('Also on the LAN (bridged with it)',
                       'Тоже в LAN (объединяются в один сегмент)')}
                  </div>
                  <div className="space-y-1">
                    {nics
                      .filter((n) => n.name !== wan && n.name !== lan)
                      .filter((n) => !n.wireless || n.ap_capable === true)
                      .map((n) => {
                        const on = lanExtras.includes(n.name)
                        // One radio only: a second access point needs its own
                        // SSID and channel, which this page doesn't configure.
                        const radioTaken =
                          n.wireless
                          && !on
                          && (lanIsWireless
                              || lanExtras.some((e) => nics.find((i) => i.name === e)?.wireless))
                        return (
                          <label
                            key={n.name}
                            className={clsx(
                              'flex items-center gap-2 text-[12px]',
                              radioTaken ? 'text-gray-600 cursor-not-allowed' : 'text-gray-300 cursor-pointer',
                            )}
                            title={radioTaken
                              ? t('Only one radio can serve the LAN',
                                  'LAN может обслуживать только одно радио')
                              : undefined}
                          >
                            <input
                              type="checkbox"
                              checked={on}
                              disabled={radioTaken}
                              onChange={(e) => {
                                const next = e.target.checked
                                  ? [...lanExtras, n.name]
                                  : lanExtras.filter((x) => x !== n.name)
                                onRoleChange('lan_extra_interfaces', next.join(','))
                              }}
                            />
                            {n.name}{n.wireless ? t(' (WiFi AP)', ' (WiFi AP)') : ''}
                          </label>
                        )
                      })}
                  </div>
                  {lanExtras.length > 0 && (
                    <p className="mt-1 text-[10px] text-gray-600">
                      {t(
                        `These ports and ${lan} become one network on a bridge, sharing the subnet and the DHCP pool. Their own addresses are removed while router mode is on.`,
                        `Эти порты и ${lan} станут одной сетью на мосту с общей подсетью и общим пулом DHCP. Их собственные адреса на время режима роутера снимаются.`,
                      )}
                    </p>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {value === 'router' && (
          <div className="mt-3 space-y-2">
            <label className="flex items-center gap-2 text-[12px] text-gray-300">
              <input
                type="checkbox"
                checked={dhcp.enabled}
                onChange={(e) => onDhcpChange('dhcp_enabled', e.target.checked)}
                className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
              />
              {t('Hand out addresses on the LAN (DHCP)', 'Раздавать адреса в LAN (DHCP)')}
            </label>
            {dhcp.enabled && (
              <div className="grid sm:grid-cols-3 gap-3">
                <div>
                  <label className="block text-[11px] font-medium text-gray-400 mb-1">
                    {t('Pool start', 'Начало пула')}
                  </label>
                  <input
                    type="text"
                    value={dhcp.poolStart}
                    onChange={(e) => onDhcpChange('dhcp_pool_start', e.target.value)}
                    placeholder={t('auto', 'авто')}
                    className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm font-mono text-gray-100 focus:outline-hidden focus:border-gray-600"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-400 mb-1">
                    {t('Pool end', 'Конец пула')}
                  </label>
                  <input
                    type="text"
                    value={dhcp.poolEnd}
                    onChange={(e) => onDhcpChange('dhcp_pool_end', e.target.value)}
                    placeholder={t('auto', 'авто')}
                    className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm font-mono text-gray-100 focus:outline-hidden focus:border-gray-600"
                  />
                </div>
                <div>
                  <label className="block text-[11px] font-medium text-gray-400 mb-1">
                    {t('Lease (hours)', 'Аренда (часы)')}
                  </label>
                  <input
                    type="number"
                    min={1}
                    value={dhcp.leaseHours}
                    onChange={(e) => onDhcpChange('dhcp_lease_hours', e.target.value)}
                    className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:outline-hidden focus:border-gray-600"
                  />
                </div>
              </div>
            )}
            {dhcp.enabled && !dhcp.poolStart && !dhcp.poolEnd && (
              <p className="text-[10px] text-gray-600">
                {t(
                  'Left empty, a range is chosen from the LAN subnet that cannot include the gateway address.',
                  'Если оставить пустым, диапазон подберётся из подсети LAN так, чтобы в него не попал адрес самой коробки.',
                )}
              </p>
            )}
          </div>
        )}

        {value === 'router' && (
          <div className="mt-2 flex items-start gap-2 rounded-md border border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-950/30 p-2 text-[11px] text-amber-800 dark:text-amber-300">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            {t(
              'Saving this reconfigures the network immediately: the WAN port stops accepting new connections from outside, and the LAN port starts serving DHCP. If it goes wrong, PiTun reverts to gateway on its own unless you confirm — but do this with physical access to the box, not over SSH from the network it is about to change.',
              'Сохранение сразу перестраивает сеть: WAN-порт перестанет принимать новые входящие соединения, а LAN-порт начнёт раздавать DHCP. Если что-то пойдёт не так, PiTun сам вернётся в режим шлюза, пока вы не подтвердите, — но делайте это с физическим доступом к коробке, а не по SSH из той самой сети, которую перестраиваете.',
            )}
          </div>
        )}
      </div>
    </div>
  )
}
