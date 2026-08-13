/**
 * `wifi_detail` is the only part of the port list written by the backend, so
 * it is the one string a Russian operator would otherwise read in English.
 * The mapping has to stay lossless: an explanation we don't recognise is
 * still the most useful thing on screen when a radio won't serve, so it must
 * pass through rather than collapse into a generic fallback.
 */
import { describe, it, expect } from 'vitest'

import { translateWifiDetail } from '@/lib/wifiDetail'

describe('translateWifiDetail', () => {
  it('leaves English alone', () => {
    expect(translateWifiDetail('adapter supports AP mode', 'en'))
      .toBe('adapter supports AP mode')
  })

  it('translates the outcomes the capability probe reports', () => {
    expect(translateWifiDetail('adapter supports AP mode', 'ru'))
      .toBe('адаптер поддерживает режим точки доступа')
    expect(translateWifiDetail('adapter is client-only — it cannot serve WiFi', 'ru'))
      .toContain('только клиентский')
    expect(translateWifiDetail('not a wireless interface', 'ru'))
      .toBe('не беспроводной интерфейс')
  })

  it('keeps the driver text when iw itself failed', () => {
    const out = translateWifiDetail('iw failed: nl80211 not found', 'ru')
    expect(out).toContain('nl80211 not found')
  })

  it('passes an unrecognised explanation through instead of hiding it', () => {
    const novel = 'radio is soft-blocked by rfkill'
    expect(translateWifiDetail(novel, 'ru')).toBe(novel)
  })

  it('survives a missing value', () => {
    expect(translateWifiDetail(undefined, 'ru')).toBe('')
    expect(translateWifiDetail(null, 'en')).toBe('')
  })
})
