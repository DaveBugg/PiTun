/**
 * Split the country prefix off a node name.
 *
 * The backend prefixes imported and deployed node names with a flag emoji —
 * two regional-indicator characters. Windows has no flag glyphs at all, so
 * Chrome there draws the pair as the bare letters "CH", which then sit flush
 * against the name and read as part of it: `CH vless-reality-…`.
 *
 * So the prefix is pulled out and rendered as its own badge. The badge shows
 * the flag where the platform can draw one and the letters where it can't —
 * both are legible, and either way the name starts where the name starts.
 */

const RI_BASE = 0x1f1e6      // regional indicator 'A'
const RI_LAST = 0x1f1ff      // regional indicator 'Z'

export interface CountryPrefix {
  /** ISO-3166 alpha-2, e.g. "CH". Empty when the name carries no prefix. */
  code: string
  /** The flag emoji itself, for platforms that render it. */
  flag: string
  /** The name with the prefix and its separating space removed. */
  name: string
}

export function splitCountryPrefix(raw: string): CountryPrefix {
  const name = raw ?? ''
  const chars = [...name]
  const a = chars[0]?.codePointAt(0)
  const b = chars[1]?.codePointAt(0)

  const isRI = (c?: number) => c !== undefined && c >= RI_BASE && c <= RI_LAST
  if (!isRI(a) || !isRI(b)) return { code: '', flag: '', name }

  const code =
    String.fromCharCode(65 + (a! - RI_BASE)) +
    String.fromCharCode(65 + (b! - RI_BASE))

  // The backend writes "<flag> <name>"; drop the separator too, but tolerate
  // its absence rather than eating a character of the name.
  const rest = chars.slice(2).join('')
  return {
    code,
    flag: chars.slice(0, 2).join(''),
    name: rest.startsWith(' ') ? rest.slice(1) : rest,
  }
}
