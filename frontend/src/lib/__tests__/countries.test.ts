/**
 * The regulatory domain is written straight into hostapd's `country_code`, and
 * a wrong one produces a radio with no usable channels rather than an error.
 * So the code must survive the round trip through the picker unchanged, and
 * the display helpers must never invent one.
 */
import { describe, it, expect } from 'vitest'

import { COUNTRY_CODES, countryFlag, countryName } from '@/lib/countries'

describe('countryFlag', () => {
  it('derives the flag from the code', () => {
    expect(countryFlag('NL')).toBe('🇳🇱')
    expect(countryFlag('de')).toBe('🇩🇪')
  })

  it('returns nothing for something that is not a country code', () => {
    for (const bad of ['', 'D', 'DEU', '12', 'D1']) {
      expect(countryFlag(bad)).toBe('')
    }
  })
})

describe('countryName', () => {
  it('localises the name', () => {
    expect(countryName('NL', 'en')).toBe('Netherlands')
    expect(countryName('DE', 'ru')).toBe('Германия')
  })

  it('falls back to the code rather than showing nothing', () => {
    // `ZZ` is not assigned; the code is what gets written to the radio, so it
    // is the honest thing to display.
    expect(countryName('ZZ', 'en')).toBe('ZZ')
    expect(countryName('', 'en')).toBe('')
  })
})

describe('COUNTRY_CODES', () => {
  it('are all well-formed and unique', () => {
    expect(new Set(COUNTRY_CODES).size).toBe(COUNTRY_CODES.length)
    for (const c of COUNTRY_CODES) expect(c).toMatch(/^[A-Z]{2}$/)
  })

  it('includes the ones our own docs and defaults use', () => {
    for (const c of ['NL', 'DE', 'RU', 'US', 'GB']) {
      expect(COUNTRY_CODES).toContain(c)
    }
  })
})
