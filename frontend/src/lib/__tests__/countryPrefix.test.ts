/**
 * The backend stores the country as a flag emoji at the front of the node's
 * name. Windows has no flag glyphs, so Chrome there draws the pair as bare
 * letters flush against the name — `CH vless-reality-…` reads as one string.
 * The prefix is split out and shown as its own badge; the font we now carry
 * makes the glyph itself render the same everywhere.
 */
import { describe, it, expect } from 'vitest'

import { splitCountryPrefix } from '@/lib/countryPrefix'

describe('splitCountryPrefix', () => {
  it('separates the flag from the name', () => {
    const r = splitCountryPrefix('🇨🇭 vless-reality-vision-pi-3b574eee')
    expect(r.code).toBe('CH')
    expect(r.flag).toBe('🇨🇭')
    expect(r.name).toBe('vless-reality-vision-pi-3b574eee')
  })

  it('leaves a name without a flag entirely alone', () => {
    const r = splitCountryPrefix('vless-reality-vision-pi')
    expect(r).toEqual({ code: '', flag: '', name: 'vless-reality-vision-pi' })
  })

  it('does not mistake ordinary leading letters for a flag', () => {
    // "CH node" is two ASCII letters, not regional indicators — a node
    // legitimately named that must not lose them.
    const r = splitCountryPrefix('CH node')
    expect(r.code).toBe('')
    expect(r.name).toBe('CH node')
  })

  it('tolerates a missing separator rather than eating a character', () => {
    const r = splitCountryPrefix('🇳🇱node')
    expect(r.code).toBe('NL')
    expect(r.name).toBe('node')
  })

  it('handles an empty name', () => {
    expect(splitCountryPrefix('').name).toBe('')
  })

  it('keeps non-ASCII names intact', () => {
    const r = splitCountryPrefix('🇩🇪 узел-берлин')
    expect(r.code).toBe('DE')
    expect(r.name).toBe('узел-берлин')
  })
})
