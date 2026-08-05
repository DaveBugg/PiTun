/**
 * Geo-data profile registry.
 *
 * A "profile" bundles three things:
 *   1. A set of canonical URLs for the geoip / geosite / mmdb files
 *   2. A list of Quick-Add routing presets that use category names known
 *      to exist inside that profile's geosite.dat
 *   3. Display metadata (label, description, hints)
 *
 * Profiles are detected at runtime by URL-match: if the current Settings
 * URLs match a profile exactly, that profile is "active". User can switch
 * profiles from the GeoData page; switching just bulk-updates the URL
 * settings via the existing PATCH /system/settings endpoint, then the
 * geo files need to be re-downloaded.
 *
 * Why central?  Quick Add (Routing page) and the URL settings (GeoData
 * page) both need to know what's in each profile, and they need to stay
 * in sync — a Quick-Add preset that references `geosite:ru-blocked`
 * MUST only be visible when runetfreedom is active, because Loyalsoldier
 * doesn't have that category. Keeping the registry in one file makes
 * that invariant easy to maintain.
 */

import type { RuleType } from '@/types'

/** Single Quick-Add rule (one button-click can post multiple). */
export interface PresetRule {
  rule_type: RuleType
  match_value: string
  action: string
}

/** A Quick-Add preset emits one or more rules atomically. */
export interface QuickAddPreset {
  label: string
  /** Optional short hint shown in the Quick Add menu. */
  hint?: string
  rules: PresetRule[]
}

export interface GeoProfile {
  id: 'loyalsoldier' | 'runetfreedom' | 'v2fly'
  label: string
  description: string
  /** Russian translation of `description` (code/category names stay EN). */
  descriptionRu: string
  /** Approximate geosite size hint shown to the user. */
  geositeSize: string
  urls: {
    geoip_url: string
    geosite_url: string
    geoip_mmdb_url: string
  }
  presets: QuickAddPreset[]
}

// ── Common presets shared across profiles ───────────────────────────────────
//
// These use category names that exist in EVERY supported profile's
// geosite.dat. Verified against actual file dumps (see roadmap entry).

const COMMON_PRESETS: QuickAddPreset[] = [
  {
    label: 'Bypass RU sites',
    hint: 'Russian sites + .ru/.su/.рф TLDs go direct',
    rules: [
      // category-ru: curated Russian-resident sites (Yandex, Mail.ru,
      // Sber, banks, gov.ru). tld-ru: catch-all for *.ru.
      { rule_type: 'geosite', match_value: 'category-ru,tld-ru', action: 'direct' },
      // .su (Soviet leftover, still active for some sites) and .рф
      // (Russian Cyrillic TLD, punycode xn--p1ai) aren't covered by any
      // tld-* category — handle as plain domain matchers, which xray
      // resolves natively without geosite.dat.
      { rule_type: 'domain', match_value: 'su,xn--p1ai', action: 'direct' },
    ],
  },
  {
    label: 'Bypass CN sites',
    hint: 'Mainland China sites go direct',
    rules: [{ rule_type: 'geosite', match_value: 'cn', action: 'direct' }],
  },
  {
    // `category-ads-all` is the union of `category-ads`, `category-ads-ir`
    // and several others — typically also covers analytics/telemetry
    // (Microsoft Vortex, Google Analytics, Mozilla telemetry endpoints,
    // etc.) since most v2fly contributors tag those alongside ads. There's
    // no dedicated `category-telemetry` in either Loyalsoldier or
    // runetfreedom, so a separate "Block telemetry" preset would fail
    // with "code not found in geosite.dat: CATEGORY-TELEMETRY". Single
    // ads-and-trackers preset covers both intents in practice.
    label: 'Block ads & trackers',
    hint: 'Ads + analytics/telemetry (category-ads-all)',
    rules: [{ rule_type: 'geosite', match_value: 'category-ads-all', action: 'block' }],
  },
  {
    // Comprehensive streaming services preset. Each service needs ALL its
    // ecosystem domains (CDN, API, image hosts, URL shorteners) — a bare
    // `youtube.com` won't catch `googlevideo.com` (CDN that actually
    // serves the video) and the user gets a 404 / hang. Entries use
    // explicit prefixes:
    //   domain:X — suffix match (X and *.X)
    //   full:X   — exact match (only X, no subdomains)
    // Backend auto-prefixes bare entries with `domain:` since 1.2.3 but
    // we keep prefixes explicit here for clarity and forward-compat.
    //
    // Coverage per service (rough mapping):
    //   YouTube       — main + shorteners + CDN + thumbnails + API
    //   Netflix       — main + assets + image/video CDNs + recs
    //   Twitch        — main + ttvnw/jtvnw video CDN + extensions
    //   Disney+       — main + Disney Streaming Services CDN + BAMTech backend
    //   Hulu          — main + stream + image
    //   HBO Max / Max — main + new "max.com" rebrand + CDN
    //   Spotify       — main + Spotify CDN + image CDN + URL shortener
    //   Amazon Prime  — main + amazonvideo + Akamai-aiv CDN
    //   Crunchyroll   — main + VRV (sister property)
    label: 'Proxy streaming',
    hint: 'YouTube/Netflix/Twitch/Disney+/Hulu/Max/Spotify/Prime/Crunchyroll + their CDNs',
    rules: [{
      rule_type: 'domain',
      match_value: [
        // YouTube
        'domain:youtube.com', 'domain:youtu.be', 'domain:yt.be',
        'domain:googlevideo.com', 'domain:ytimg.com',
        'domain:youtube-nocookie.com', 'domain:ggpht.com',
        'domain:youtubekids.com',
        'full:youtubei.googleapis.com',
        'full:youtube.googleapis.com',
        'full:yt-video-upload.l.google.com',
        // Netflix
        'domain:netflix.com', 'domain:netflix.net',
        'domain:nflxext.com', 'domain:nflximg.com', 'domain:nflximg.net',
        'domain:nflxvideo.net', 'domain:nflxso.net', 'domain:nflxsearch.net',
        // Twitch
        'domain:twitch.tv', 'domain:ttvnw.net', 'domain:jtvnw.net',
        'domain:live-video.net', 'domain:twitchcdn.net', 'domain:ext-twitch.tv',
        // Disney+
        'domain:disneyplus.com', 'domain:disney-plus.net',
        'domain:dssott.com', 'domain:bamgrid.com',
        'domain:disney.io', 'domain:disney.demdex.net',
        // Hulu
        'domain:hulu.com', 'domain:hulustream.com', 'domain:huluim.com',
        // HBO Max / Max
        'domain:hbomax.com', 'domain:hbo.com', 'domain:max.com',
        'domain:hbomaxcdn.com',
        // Spotify
        'domain:spotify.com', 'domain:scdn.co', 'domain:spotifycdn.com',
        'domain:pscdn.co', 'domain:spoti.fi',
        // Amazon Prime Video
        'domain:primevideo.com', 'domain:amazonvideo.com',
        'domain:aiv-cdn.net', 'domain:amazon-video.com',
        // Apple TV+ (limited — Apple uses shared infra; full: for the entry host)
        'full:tv.apple.com',
        // Crunchyroll
        'domain:crunchyroll.com', 'domain:vrv.co',
      ].join(','),
      action: 'proxy',
    }],
  },
  {
    label: 'Bypass local networks',
    hint: 'RFC1918 ranges go direct (LAN only, never through proxy)',
    rules: [{
      rule_type: 'dst_ip',
      match_value: '192.168.0.0/16,10.0.0.0/8,172.16.0.0/12',
      action: 'direct',
    }],
  },
]

// ── Profile registry ────────────────────────────────────────────────────────

export const GEO_PROFILES: GeoProfile[] = [
  {
    id: 'loyalsoldier',
    label: 'Loyalsoldier',
    description:
      'Community-standard CN-focused list with a decent RU base. Adds `geosite:gfw`, `geoip:telegram`, and other shortcuts on top of v2fly upstream. Good baseline for users routing both RU and CN traffic.',
    descriptionRu:
      'Стандартный community-список с уклоном в CN и неплохой базой по RU. Добавляет `geosite:gfw`, `geoip:telegram` и другие шорткаты поверх upstream v2fly. Хорошая база для маршрутизации и RU, и CN трафика.',
    geositeSize: '~10 MB',
    urls: {
      geoip_url:
        'https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat',
      geosite_url:
        'https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat',
      geoip_mmdb_url: 'https://git.io/GeoLite2-Country.mmdb',
    },
    presets: COMMON_PRESETS,
  },
  {
    id: 'v2fly',
    label: 'v2fly upstream',
    description:
      'Pure upstream from the v2fly maintainers — the canonical source that both Loyalsoldier and runetfreedom build on. Smallest download (~2 MB geosite). Same `category-ru`, `tld-ru`, `cn`, `category-ads-all` names work, but without the curated extras (no `gfw`, no `geoip:telegram`).',
    descriptionRu:
      'Чистый upstream от мейнтейнеров v2fly — канонический источник, на котором строятся и Loyalsoldier, и runetfreedom. Самая маленькая загрузка (~2 MB geosite). Работают те же `category-ru`, `tld-ru`, `cn`, `category-ads-all`, но без кураторских дополнений (нет `gfw`, нет `geoip:telegram`).',
    geositeSize: '~2 MB',
    urls: {
      geoip_url:
        'https://github.com/v2fly/geoip/releases/latest/download/geoip.dat',
      geosite_url:
        'https://github.com/v2fly/domain-list-community/releases/latest/download/dlc.dat',
      geoip_mmdb_url: 'https://git.io/GeoLite2-Country.mmdb',
    },
    presets: COMMON_PRESETS,
  },
  {
    id: 'runetfreedom',
    label: 'runetfreedom (RU-focused)',
    description:
      'Russia-focused list with extensive RU coverage and daily updates. Includes blocked-in-RU site lists (`ru-blocked`, `ru-blocked-all`) and Russian telecom categories (`mts-ru`, `t2-ru`, `tbank-ru`). Larger download (~67 MB geosite, 6× Loyalsoldier).',
    descriptionRu:
      'Список с уклоном в Россию: обширное покрытие RU и ежедневные обновления. Включает списки заблокированных в РФ сайтов (`ru-blocked`, `ru-blocked-all`) и категории телекомов (`mts-ru`, `t2-ru`, `tbank-ru`). Большая загрузка (~67 MB geosite, в 6× больше Loyalsoldier).',
    geositeSize: '~67 MB',
    urls: {
      geoip_url:
        'https://github.com/runetfreedom/russia-v2ray-rules-dat/releases/latest/download/geoip.dat',
      geosite_url:
        'https://github.com/runetfreedom/russia-v2ray-rules-dat/releases/latest/download/geosite.dat',
      geoip_mmdb_url: 'https://git.io/GeoLite2-Country.mmdb',
    },
    presets: [
      ...COMMON_PRESETS,
      // runetfreedom-specific presets — these reference categories that
      // ONLY exist in runetfreedom's geosite.dat. Putting an analogous
      // preset on Loyalsoldier would fail with "code not found".
      {
        label: 'Proxy blocked-in-RU sites',
        hint: 'Sites blocked by RKN — route through proxy to access',
        rules: [{
          rule_type: 'geosite',
          match_value: 'ru-blocked,ru-blocked-all',
          action: 'proxy',
        }],
      },
      {
        label: 'Bypass RU-only sites',
        hint: 'Sites accessible only from RU (banks, e-gov) — must go direct',
        rules: [{
          rule_type: 'geosite',
          match_value: 'ru-available-only-inside',
          action: 'direct',
        }],
      },
    ],
  },
]

/**
 * Resolve which profile (if any) the current settings match. Returns
 * `'custom'` if URLs don't fit any registered profile (user has
 * manually edited the fields).
 */
export function detectActiveProfile(current: {
  geoip_url?: string | null
  geosite_url?: string | null
  geoip_mmdb_url?: string | null
}): GeoProfile['id'] | 'custom' {
  for (const profile of GEO_PROFILES) {
    if (
      profile.urls.geoip_url === current.geoip_url &&
      profile.urls.geosite_url === current.geosite_url &&
      profile.urls.geoip_mmdb_url === current.geoip_mmdb_url
    ) {
      return profile.id
    }
  }
  return 'custom'
}

/** Lookup helper. Returns undefined for `'custom'`. */
export function getProfile(id: GeoProfile['id'] | 'custom'): GeoProfile | undefined {
  if (id === 'custom') return undefined
  return GEO_PROFILES.find((p) => p.id === id)
}
