/**
 * Translate the AP-capability explanations that `network_config.wifi_capabilities`
 * returns.
 *
 * The backend produces one English sentence per outcome. Sending a code instead
 * would be the tidier split, but these strings are already stored in nothing and
 * read by no other consumer, so mapping the known ones here — and passing
 * anything unrecognised through verbatim — keeps the backend free to add cases
 * without the UI hiding them behind a generic fallback. `iw failed: …` is the
 * open-ended one, and its driver text is worth showing as-is.
 */
export function translateWifiDetail(
  detail: string | undefined | null,
  lang: string,
): string {
  if (!detail) return ''
  if (lang !== 'ru') return detail

  const exact: Record<string, string> = {
    'not a wireless interface': 'не беспроводной интерфейс',
    'no phy80211 for this interface — cannot query the driver':
      'у интерфейса нет phy80211 — драйвер опросить нельзя',
    'adapter supports AP mode': 'адаптер поддерживает режим точки доступа',
    'adapter is client-only — it cannot serve WiFi':
      'адаптер только клиентский — раздавать Wi-Fi не может',
    '`iw` is not installed — install it to check whether this adapter supports AP mode':
      'не установлен `iw` — поставьте его, чтобы проверить поддержку режима точки доступа',
  }
  if (exact[detail]) return exact[detail]

  const failed = detail.match(/^iw failed: (.*)$/s)
  if (failed) return `iw завершился ошибкой: ${failed[1]}`

  return detail
}
