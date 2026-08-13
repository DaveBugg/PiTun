/**
 * ISO-3166-1 alpha-2 codes, plus name and flag derivation.
 *
 * Only the codes are stored. Names come from `Intl.DisplayNames`, which every
 * browser we target ships and which already knows every UI language we offer —
 * carrying our own translated list would mean maintaining ~250 names twice and
 * would still be wrong for any language added later. Flags are the two
 * regional-indicator characters for the code, so there is no icon set to ship
 * either.
 *
 * Used for the WiFi regulatory domain, where the code is what hostapd wants
 * (`country_code=NL`) but the name is what a person knows.
 */

export const COUNTRY_CODES: readonly string[] = [
  'AD', 'AE', 'AF', 'AG', 'AI', 'AL', 'AM', 'AO', 'AQ', 'AR', 'AS', 'AT', 'AU',
  'AW', 'AX', 'AZ', 'BA', 'BB', 'BD', 'BE', 'BF', 'BG', 'BH', 'BI', 'BJ', 'BL',
  'BM', 'BN', 'BO', 'BQ', 'BR', 'BS', 'BT', 'BV', 'BW', 'BY', 'BZ', 'CA', 'CC',
  'CD', 'CF', 'CG', 'CH', 'CI', 'CK', 'CL', 'CM', 'CN', 'CO', 'CR', 'CU', 'CV',
  'CW', 'CX', 'CY', 'CZ', 'DE', 'DJ', 'DK', 'DM', 'DO', 'DZ', 'EC', 'EE', 'EG',
  'EH', 'ER', 'ES', 'ET', 'FI', 'FJ', 'FK', 'FM', 'FO', 'FR', 'GA', 'GB', 'GD',
  'GE', 'GF', 'GG', 'GH', 'GI', 'GL', 'GM', 'GN', 'GP', 'GQ', 'GR', 'GS', 'GT',
  'GU', 'GW', 'GY', 'HK', 'HM', 'HN', 'HR', 'HT', 'HU', 'ID', 'IE', 'IL', 'IM',
  'IN', 'IO', 'IQ', 'IR', 'IS', 'IT', 'JE', 'JM', 'JO', 'JP', 'KE', 'KG', 'KH',
  'KI', 'KM', 'KN', 'KP', 'KR', 'KW', 'KY', 'KZ', 'LA', 'LB', 'LC', 'LI', 'LK',
  'LR', 'LS', 'LT', 'LU', 'LV', 'LY', 'MA', 'MC', 'MD', 'ME', 'MF', 'MG', 'MH',
  'MK', 'ML', 'MM', 'MN', 'MO', 'MP', 'MQ', 'MR', 'MS', 'MT', 'MU', 'MV', 'MW',
  'MX', 'MY', 'MZ', 'NA', 'NC', 'NE', 'NF', 'NG', 'NI', 'NL', 'NO', 'NP', 'NR',
  'NU', 'NZ', 'OM', 'PA', 'PE', 'PF', 'PG', 'PH', 'PK', 'PL', 'PM', 'PN', 'PR',
  'PS', 'PT', 'PW', 'PY', 'QA', 'RE', 'RO', 'RS', 'RU', 'RW', 'SA', 'SB', 'SC',
  'SD', 'SE', 'SG', 'SH', 'SI', 'SJ', 'SK', 'SL', 'SM', 'SN', 'SO', 'SR', 'SS',
  'ST', 'SV', 'SX', 'SY', 'SZ', 'TC', 'TD', 'TF', 'TG', 'TH', 'TJ', 'TK', 'TL',
  'TM', 'TN', 'TO', 'TR', 'TT', 'TV', 'TW', 'TZ', 'UA', 'UG', 'UM', 'US', 'UY',
  'UZ', 'VA', 'VC', 'VE', 'VG', 'VI', 'VN', 'VU', 'WF', 'WS', 'YE', 'YT', 'ZA',
  'ZM', 'ZW',
]

const REGIONAL_INDICATOR_A = 0x1f1e6
const LETTER_A = 'A'.charCodeAt(0)

/** The flag emoji for a country code, or an empty string if it isn't one. */
export function countryFlag(code: string): string {
  const cc = (code || '').trim().toUpperCase()
  if (!/^[A-Z]{2}$/.test(cc)) return ''
  return String.fromCodePoint(
    ...[...cc].map((ch) => REGIONAL_INDICATOR_A + (ch.charCodeAt(0) - LETTER_A)),
  )
}

const KNOWN = new Set(COUNTRY_CODES)

/**
 * Localised country name, falling back to the code itself.
 *
 * Only codes we actually list are looked up: `Intl.DisplayNames` answers an
 * unassigned region with a localised "Unknown Region", which is worse than
 * saying nothing — the code is what gets written to the radio, so showing it
 * verbatim is the honest answer when we can't name it.
 */
export function countryName(code: string, lang: string): string {
  const cc = (code || '').trim().toUpperCase()
  if (!cc) return ''
  if (!KNOWN.has(cc)) return cc
  try {
    return new Intl.DisplayNames([lang || 'en'], { type: 'region' }).of(cc) || cc
  } catch {
    return cc
  }
}
