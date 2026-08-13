import {
  CheckCircle2,
  ShieldAlert,
  Cable,
  Info,
  RefreshCw,
  Router as RouterIcon,
  Save,
  Wifi,
  XCircle,
} from 'lucide-react'
import { clsx } from 'clsx'

import OperatingModeSection from '@/components/OperatingModeSection'
import WanSection from '@/components/WanSection'
import WifiApSection from '@/components/WifiApSection'
import WanDiagnosticsSection from '@/components/WanDiagnosticsSection'
import { useSettingsDraft } from '@/hooks/useSettingsDraft'
import { networkApi } from '@/api/client'
import { rememberLanPanel } from '@/lib/panelReachability'
import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { useT } from '@/hooks/useT'

/**
 * Everything about how this box sits in the network, on one page.
 *
 * These settings used to live inside Settings → Network, next to ports and
 * health checks. They belong apart: they are the only settings that can take
 * the network down, they are read as a set ("what is my WAN, what is my LAN,
 * what is on the air"), and the page has to stay reachable while you reason
 * about them.
 *
 * The two deployments it has to serve are genuinely different questions, so
 * the page names them instead of leaving the operator to infer which fields
 * apply — see the intro block below.
 */
export default function RouterPage() {
  const t = useT()
  const {
    isLoading, draft, val, isChecked, set,
    hasChanges, save, saving, saved, error,
  } = useSettingsDraft()

  const lanIface = String(val('lan_interface') || '')
  const { data: ports } = useQuery({
    queryKey: ['network-interfaces'],
    queryFn: () => networkApi.interfaces(),
  })
  // Remember where the panel will still answer once the uplink stops
  // accepting connections — the switch is made from this page.
  useEffect(() => {
    const lan = ports?.items.find((i) => i.name === lanIface)
    rememberLanPanel(lan?.ipv4)
  }, [ports, lanIface])

  const mode = String(val('operating_mode') || 'gateway')
  const isRouter = mode === 'router'

  if (isLoading) return (
    <div className="p-6">
      <div className="h-8 w-48 rounded-sm bg-gray-800 animate-pulse mb-4" />
      <div className="space-y-4">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-40 rounded-xl bg-gray-800/50 animate-pulse" />
        ))}
      </div>
    </div>
  )

  const section = (
    icon: typeof RouterIcon, title: string, desc: string, children: React.ReactNode,
  ) => (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50">
      <div className="px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          {(() => { const I = icon; return <I className="h-4 w-4 text-brand-400" /> })()}
          {title}
        </h2>
        <p className="text-[11px] text-gray-500 mt-0.5">{desc}</p>
      </div>
      <div className="p-4">{children}</div>
    </div>
  )

  return (
    <div className="p-6 space-y-4 max-w-5xl">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-100">
            {t('Router', 'Роутер')}
          </h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {t(
              'How this box connects upstream and serves the network below it',
              'Как коробка подключается к вышестоящей сети и обслуживает сеть под собой',
            )}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {saved && (
            <span className="flex items-center gap-1 text-xs text-green-600 dark:text-green-400">
              <CheckCircle2 className="h-3.5 w-3.5" /> {t('Saved', 'Сохранено')}
            </span>
          )}
          <button
            onClick={save}
            disabled={!hasChanges || saving}
            className={clsx(
              'flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors',
              hasChanges
                ? 'bg-brand-600 text-white hover:bg-brand-500'
                : 'bg-gray-800 text-gray-500 cursor-not-allowed',
            )}
          >
            {saving
              ? <RefreshCw className="h-4 w-4 animate-spin" />
              : <Save className="h-4 w-4" />}
            {t('Save', 'Сохранить')}
          </button>
        </div>
      </div>

      {error && (
        <div className="flex items-center gap-2 rounded-lg bg-red-50 dark:bg-red-950/30 border border-red-200 dark:border-red-900/50 px-4 py-2.5 text-xs text-red-700 dark:text-red-300">
          <XCircle className="h-4 w-4 text-red-600 dark:text-red-400 shrink-0" />
          {error}
        </div>
      )}

      {/* Which of the two deployments this is decides which fields below
          matter. Stating both is cheaper than letting someone fill in PPPoE
          credentials on a box that sits behind an ISP router already doing
          the dialling. */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-4">
        <div className="flex items-center gap-2 mb-2">
          <Info className="h-4 w-4 text-brand-400" />
          <h2 className="text-sm font-semibold text-gray-200">
            {t('Which setup is this?', 'Какой это случай?')}
          </h2>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
          <div className={clsx(
            'rounded-lg border p-3',
            !isRouter ? 'border-brand-700/60 bg-brand-900/20' : 'border-gray-800',
          )}>
            <p className="font-medium text-gray-200 mb-1">
              {t('Behind an existing router', 'За существующим роутером')}
            </p>
            <p className="text-gray-500 leading-relaxed">
              {t(
                'Your ISP router keeps handing out addresses and doing NAT. PiTun sits on the same network and only proxies the devices you point at it. Nothing below the mode selector applies.',
                'Ваш роутер продолжает раздавать адреса и делать NAT. PiTun стоит в той же сети и проксирует только те устройства, которые вы на него направите. Всё ниже переключателя режима не применяется.',
              )}
            </p>
            <p className="text-[11px] text-gray-600 mt-1.5">
              {t('Mode: Gateway', 'Режим: шлюз')}
            </p>
          </div>
          <div className={clsx(
            'rounded-lg border p-3',
            isRouter ? 'border-brand-700/60 bg-brand-900/20' : 'border-gray-800',
          )}>
            <p className="font-medium text-gray-200 mb-1">
              {t('First in line, facing the ISP', 'Первый в цепочке, смотрит к провайдеру')}
            </p>
            <p className="text-gray-500 leading-relaxed">
              {t(
                'PiTun takes the uplink itself — DHCP, static, PPPoE or a tagged VLAN — hands out addresses on the LAN, does NAT, and can serve the WiFi. It needs two or more physical ports.',
                'PiTun сам берёт аплинк — DHCP, статика, PPPoE или VLAN с меткой, — раздаёт адреса в LAN, делает NAT и может раздавать Wi-Fi. Нужно два и более физических порта.',
              )}
            </p>
            <p className="text-[11px] text-gray-600 mt-1.5">
              {t('Mode: Router', 'Режим: роутер')}
            </p>
          </div>
        </div>
      </div>

      {section(
        RouterIcon,
        t('Operating mode', 'Режим работы'),
        t('Port roles, and the DHCP server when this box is the router',
          'Роли портов и DHCP-сервер, когда роутером выступает эта коробка'),
        <OperatingModeSection
          value={mode}
          onChange={(m) => set('operating_mode', m)}
          wan={String(val('wan_interface') || '')}
          lan={String(val('lan_interface') || '')}
          lanExtra={String(val('lan_extra_interfaces') || '')}
          onRoleChange={(role, iface) => set(role, iface)}
          dhcp={{
            enabled: isChecked('dhcp_enabled'),
            poolStart: String(val('dhcp_pool_start') || ''),
            poolEnd: String(val('dhcp_pool_end') || ''),
            leaseHours: String(val('dhcp_lease_hours') || 12),
          }}
          onDhcpChange={(k, v) => set(k, v)}
        />,
      )}

      {isRouter && (
        <>
          {section(
            Cable,
            t('Uplink (WAN)', 'Аплинк (WAN)'),
            t('How the box obtains its internet-facing address',
              'Как коробка получает адрес со стороны интернета'),
            <WanSection
              wan={String(val('wan_interface') || '')}
              values={{
                mode: String(val('wan_mode') || 'dhcp'),
                vlanId: String(val('wan_vlan_id') ?? 0),
                macClone: String(val('wan_mac_clone') || ''),
                address: String(val('wan_static_address') || ''),
                gateway: String(val('wan_static_gateway') || ''),
                dns: String(val('wan_static_dns') || ''),
                pppoeUser: String(val('wan_pppoe_user') || ''),
                // Write-only on the backend, same as the WiFi passphrase.
                pppoePassword: String(draft['wan_pppoe_password'] ?? ''),
              }}
              onChange={(k, v) => set(k, k === 'wan_vlan_id' ? Number(v) || 0 : v)}
            />,
          )}

          {section(
            Wifi,
            t('Wireless network', 'Беспроводная сеть'),
            t('Off until you switch it on. Network name and password are set here.',
              'Выключена, пока вы её не включите. Имя сети и пароль задаются здесь.'),
            <WifiApSection
              lan={String(val('lan_interface') || '')}
              wifi={{
                enabled: isChecked('wifi_enabled'),
                ssid: String(val('wifi_ssid') || ''),
                // Write-only on the backend: starts empty and only sends
                // when the operator actually types a new one.
                passphrase: String(draft['wifi_passphrase'] ?? ''),
                country: String(val('wifi_country') || ''),
                band: String(val('wifi_band') || '2.4'),
                channel: String(val('wifi_channel') ?? 0),
                security: String(val('wifi_security') || 'wpa2'),
                hidden: isChecked('wifi_hidden'),
              }}
              onChange={(k, v) => set(k, v)}
            />,
          )}

          <WanDiagnosticsSection />
        </>
      )}

        {section(
          ShieldAlert,
          t('Access from the uplink side', 'Доступ со стороны аплинка'),
          t('By default the uplink accepts nothing new — the panel and SSH are reachable from the LAN only',
            'По умолчанию аплинк не принимает новых соединений — панель и SSH доступны только из LAN'),
          <div className="space-y-3">
            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={isChecked('wan_admin_access')}
                onChange={(e) => set('wan_admin_access', e.target.checked)}
              />
              <div>
                <div className="text-[12px] text-gray-200">
                  {t('Allow the panel on the uplink (ports 80 and 443)',
                     'Разрешить панель со стороны аплинка (порты 80 и 443)')}
                </div>
                <div className="text-[11px] text-gray-500 mt-0.5">
                  {t(
                    "Useful when PiTun sits behind another router: that uplink is your own network, and it's where you already are. Refused outright if the uplink turns out to hold a public address — that would put the login page in front of the internet.",
                    'Полезно, когда PiTun стоит за другим роутером: этот аплинк — ваша же сеть, и вы уже находитесь в ней. Если у аплинка окажется публичный адрес, включение будет отклонено — это выставило бы страницу входа в интернет.',
                  )}
                </div>
              </div>
            </label>

            <label className="flex items-start gap-2 cursor-pointer">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={isChecked('wan_ssh_access')}
                onChange={(e) => set('wan_ssh_access', e.target.checked)}
              />
              <div>
                <div className="text-[12px] text-gray-200">
                  {t('Allow SSH on the uplink (port 22)',
                     'Разрешить SSH со стороны аплинка (порт 22)')}
                </div>
                <div className="text-[11px] text-gray-500 mt-0.5">
                  {t(
                    'Set this before switching, not after: once the uplink stops accepting connections, turning it on requires reaching the box some other way.',
                    'Включайте до переключения, а не после: когда аплинк перестанет принимать соединения, включить это можно будет только добравшись до коробки иначе.',
                  )}
                </div>
              </div>
            </label>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label htmlFor="wan_allow_tcp" className="block text-[11px] font-medium text-gray-400 mb-1">
                  {t('Additional TCP ports', 'Дополнительные TCP-порты')}
                </label>
                <input
                  id="wan_allow_tcp"
                  type="text"
                  value={String(val('wan_allow_tcp') || '')}
                  onChange={(e) => set('wan_allow_tcp', e.target.value)}
                  placeholder="22, 8443"
                  className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm font-mono text-gray-100 focus:outline-hidden focus:border-gray-600"
                />
              </div>
              <div>
                <label htmlFor="wan_allow_udp" className="block text-[11px] font-medium text-gray-400 mb-1">
                  {t('Additional UDP ports', 'Дополнительные UDP-порты')}
                </label>
                <input
                  id="wan_allow_udp"
                  type="text"
                  value={String(val('wan_allow_udp') || '')}
                  onChange={(e) => set('wan_allow_udp', e.target.value)}
                  placeholder="51820"
                  className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm font-mono text-gray-100 focus:outline-hidden focus:border-gray-600"
                />
              </div>
            </div>
            <p className="text-[10px] text-gray-600">
              {t('Same rule applies: nothing is opened on a public uplink.',
                 'Правило то же: на публичном аплинке ничего не открывается.')}
            </p>
          </div>,
        )}
    </div>
  )
}
