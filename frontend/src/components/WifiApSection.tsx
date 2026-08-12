import { useQuery } from '@tanstack/react-query'
import { Wifi, AlertTriangle } from 'lucide-react'

import { networkApi } from '@/api/client'
import { useT } from '@/hooks/useT'

/**
 * WiFi access point settings (router mode, phase 1b).
 *
 * Only shown when the chosen LAN port is a radio that can actually run an AP —
 * hostapd cannot turn a client-only adapter into an access point, and finding
 * that out at start time means the working setup is already dismantled.
 *
 * The passphrase is write-only on the backend: it is never returned, so this
 * field starts empty and only sends a value when the operator types one.
 */
export default function WifiApSection({
  lan, wifi, onChange,
}: {
  lan: string
  wifi: {
    enabled: boolean
    ssid: string
    passphrase: string
    country: string
    band: string
    channel: string
    security: string
    hidden: boolean
  }
  onChange: (key: string, value: string | boolean | number) => void
}) {
  const t = useT()
  const { data } = useQuery({
    queryKey: ['network-interfaces'],
    queryFn: () => networkApi.interfaces(),
  })

  const port = data?.items.find((i) => i.name === lan)
  if (!lan || !port?.wireless) return null

  if (port.ap_capable !== true) {
    return (
      <div className="mt-3 flex items-start gap-2 rounded-md border border-amber-200 dark:border-amber-800/50 bg-amber-50 dark:bg-amber-950/30 p-2 text-[11px] text-amber-800 dark:text-amber-300">
        <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
        <div>
          {port.ap_capable === false
            ? t(
                `${lan} is a client-only adapter — it can join networks but cannot create one. Use a wired LAN port, or a WiFi adapter that supports AP mode.`,
                `${lan} — адаптер только для подключения: он умеет входить в сети, но не создавать их. Используйте проводной LAN-порт или Wi-Fi-адаптер с поддержкой режима точки доступа.`,
              )
            : t(
                `Can't tell whether ${lan} supports access-point mode: ${port.wifi_detail}`,
                `Не удалось определить, поддерживает ли ${lan} режим точки доступа: ${port.wifi_detail}`,
              )}
        </div>
      </div>
    )
  }

  const channels = wifi.band === '5'
    ? [0, 36, 40, 44, 48, 149, 153, 157, 161]
    : [0, 1, 6, 11]

  return (
    <div className="mt-3 space-y-2">
      <label className="flex items-center gap-2 text-[12px] text-gray-300">
        <input
          type="checkbox"
          checked={wifi.enabled}
          onChange={(e) => onChange('wifi_enabled', e.target.checked)}
          className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
        />
        <Wifi className="h-3.5 w-3.5 text-brand-400" />
        {t('Serve WiFi from this box', 'Раздавать Wi-Fi с этой машины')}
      </label>

      {wifi.enabled && (
        <>
          <div className="grid sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-[11px] font-medium text-gray-400 mb-1">
                {t('Network name (SSID)', 'Имя сети (SSID)')}
              </label>
              <input
                type="text"
                value={wifi.ssid}
                onChange={(e) => onChange('wifi_ssid', e.target.value)}
                maxLength={32}
                className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:outline-hidden focus:border-gray-600"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-gray-400 mb-1">
                {t('Password', 'Пароль')}
              </label>
              <input
                type="password"
                value={wifi.passphrase}
                onChange={(e) => onChange('wifi_passphrase', e.target.value)}
                placeholder={t('unchanged', 'без изменений')}
                autoComplete="new-password"
                className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:outline-hidden focus:border-gray-600"
              />
              <p className="mt-1 text-[10px] text-gray-600">
                {t(
                  '8-63 characters — that is the WPA standard, not our rule. Leave empty to keep the current password.',
                  '8–63 символа — это требование стандарта WPA, а не наше. Оставьте пустым, чтобы не менять текущий пароль.',
                )}
              </p>
            </div>
          </div>

          <div className="grid sm:grid-cols-4 gap-3">
            <div>
              <label className="block text-[11px] font-medium text-gray-400 mb-1">
                {t('Country', 'Страна')}
              </label>
              <input
                type="text"
                value={wifi.country}
                onChange={(e) => onChange('wifi_country', e.target.value.toUpperCase())}
                maxLength={2}
                placeholder="DE"
                className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm font-mono text-gray-100 focus:outline-hidden focus:border-gray-600"
              />
            </div>
            <div>
              <label className="block text-[11px] font-medium text-gray-400 mb-1">
                {t('Band', 'Диапазон')}
              </label>
              <select
                value={wifi.band}
                onChange={(e) => { onChange('wifi_band', e.target.value); onChange('wifi_channel', 0) }}
                className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:outline-hidden focus:border-gray-600"
              >
                <option value="2.4">2.4 GHz</option>
                <option value="5">5 GHz</option>
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-gray-400 mb-1">
                {t('Channel', 'Канал')}
              </label>
              <select
                value={wifi.channel}
                onChange={(e) => onChange('wifi_channel', Number(e.target.value))}
                className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:outline-hidden focus:border-gray-600"
              >
                {channels.map((c) => (
                  <option key={c} value={c}>{c === 0 ? t('auto', 'авто') : c}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-[11px] font-medium text-gray-400 mb-1">
                {t('Security', 'Защита')}
              </label>
              <select
                value={wifi.security}
                onChange={(e) => onChange('wifi_security', e.target.value)}
                className="w-full rounded-lg bg-gray-950 border border-gray-800 px-3 py-2 text-sm text-gray-100 focus:outline-hidden focus:border-gray-600"
              >
                <option value="wpa2">WPA2</option>
                <option value="wpa2wpa3">WPA2 + WPA3</option>
              </select>
            </div>
          </div>

          <p className="text-[10px] text-gray-600">
            {t(
              'The country code decides which channels and power levels are legal — the radio will not come up correctly without a correct one.',
              'Код страны определяет, какие каналы и мощности разрешены — без правильного значения радио не поднимется как надо.',
            )}
          </p>

          <label className="flex items-center gap-2 text-[12px] text-gray-300">
            <input
              type="checkbox"
              checked={wifi.hidden}
              onChange={(e) => onChange('wifi_hidden', e.target.checked)}
              className="rounded-sm border-gray-600 bg-gray-800 text-brand-500"
            />
            {t('Hide the network name', 'Скрыть имя сети')}
          </label>

          <div className="flex items-start gap-2 rounded-md border border-gray-800 bg-gray-950/40 p-2 text-[11px] text-gray-500">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
            {t(
              'A built-in radio is not a replacement for a dedicated access point: expect fewer clients and shorter range than a real router.',
              'Встроенное радио не заменяет полноценную точку доступа: клиентов поместится меньше, а радиус будет короче, чем у настоящего роутера.',
            )}
          </div>
        </>
      )}
    </div>
  )
}
