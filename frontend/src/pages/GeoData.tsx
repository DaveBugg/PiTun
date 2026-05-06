import { useState } from 'react'
import { Download, Globe, RefreshCw, CheckCircle, XCircle, Layers } from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { geodataApi } from '@/api/client'
import { useSystemSettings, useUpdateSettings } from '@/hooks/useSystem'
import { GEO_PROFILES, detectActiveProfile, type GeoProfile } from '@/lib/geoProfiles'

function formatSize(bytes?: number | null): string {
  if (!bytes) return '—'
  if (bytes > 1e6) return `${(bytes / 1e6).toFixed(1)} MB`
  return `${(bytes / 1e3).toFixed(0)} KB`
}

export function GeoData() {
  const qc = useQueryClient()
  const [customGeoipUrl, setCustomGeoipUrl] = useState('')
  const [customGeositeUrl, setCustomGeositeUrl] = useState('')
  const [customMmdbUrl, setCustomMmdbUrl] = useState('')

  const { data: geo } = useQuery({
    queryKey: ['geodata', 'status'],
    queryFn: () => geodataApi.status(),
    refetchInterval: 10_000,
  })

  const { data: sysSettings } = useSystemSettings()
  const updateSettings = useUpdateSettings()

  const updateGeo = useMutation({
    mutationFn: (payload: { geoip_url?: string; geosite_url?: string; mmdb_url?: string; type?: string }) =>
      geodataApi.update(payload),
    onSuccess: () => {
      setTimeout(() => qc.invalidateQueries({ queryKey: ['geodata'] }), 3000)
    },
  })

  const handleUpdateAll = () => {
    updateGeo.mutate({
      geoip_url: customGeoipUrl || undefined,
      geosite_url: customGeositeUrl || undefined,
      mmdb_url: customMmdbUrl || undefined,
      type: 'all',
    })
  }

  const handleUpdateSingle = (type: 'geoip' | 'geosite' | 'mmdb') => {
    updateGeo.mutate({
      geoip_url: type === 'geoip' ? (customGeoipUrl || undefined) : undefined,
      geosite_url: type === 'geosite' ? (customGeositeUrl || undefined) : undefined,
      mmdb_url: type === 'mmdb' ? (customMmdbUrl || undefined) : undefined,
      type,
    })
  }

  return (
    <div className="p-6 space-y-6">
      <h1 className="text-xl font-bold text-gray-100">GeoData</h1>

      {/* Status cards */}
      <div className="grid grid-cols-3 gap-4">
        {[
          {
            label: 'geoip.dat',
            exists: geo?.geoip_exists,
            size: geo?.geoip_size,
            mtime: geo?.geoip_mtime,
            type: 'geoip' as const,
          },
          {
            label: 'geosite.dat',
            exists: geo?.geosite_exists,
            size: geo?.geosite_size,
            mtime: geo?.geosite_mtime,
            type: 'geosite' as const,
          },
          {
            label: 'GeoLite2-Country.mmdb',
            exists: geo?.mmdb_exists,
            size: geo?.mmdb_size,
            mtime: geo?.mmdb_mtime,
            type: 'mmdb' as const,
          },
        ].map(({ label, exists, size, mtime, type }) => (
          <div key={label} className="rounded-xl border border-gray-800 bg-gray-900/30 p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-medium text-gray-200 font-mono truncate pr-1">{label}</span>
              {exists
                ? <CheckCircle className="h-4 w-4 text-green-400 flex-shrink-0" />
                : <XCircle className="h-4 w-4 text-red-400 flex-shrink-0" />
              }
            </div>
            <div className="text-xs text-gray-500 space-y-0.5">
              <div>Size: {formatSize(size)}</div>
              {mtime && (
                <div>Updated: {new Date(mtime).toLocaleString()}</div>
              )}
              {!exists && <div className="text-red-400">File not found</div>}
            </div>
            <button
              onClick={() => handleUpdateSingle(type)}
              disabled={updateGeo.isPending}
              className="mt-3 flex items-center gap-1 rounded px-2 py-1 text-xs bg-gray-700 text-gray-300 hover:bg-gray-600 disabled:opacity-50 transition-colors"
            >
              <RefreshCw className={updateGeo.isPending ? 'h-3 w-3 animate-spin' : 'h-3 w-3'} />
              Update
            </button>
          </div>
        ))}
      </div>

      {/* mmdb info note */}
      <div className="rounded-lg border border-blue-800/40 bg-blue-950/30 px-4 py-2.5 text-xs text-blue-300">
        GeoLite2 .mmdb is used for IP geolocation queries (requires xray version with mmdb support).
      </div>

      {/* Download section */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-5 space-y-4">
        <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
          <Download className="h-4 w-4" />
          Download / Update
        </h2>

        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">
              GeoIP URL
              <span className="ml-2 text-gray-600 font-normal">
                (leave empty to use default)
              </span>
            </label>
            <input
              value={customGeoipUrl}
              onChange={(e) => setCustomGeoipUrl(e.target.value)}
              placeholder={sysSettings?.geoip_url || 'https://…/geoip.dat'}
              className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">
              GeoSite URL
              <span className="ml-2 text-gray-600 font-normal">
                (leave empty to use default)
              </span>
            </label>
            <input
              value={customGeositeUrl}
              onChange={(e) => setCustomGeositeUrl(e.target.value)}
              placeholder={sysSettings?.geosite_url || 'https://…/geosite.dat'}
              className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">
              GeoLite2 MMDB URL
              <span className="ml-2 text-gray-600 font-normal">
                (leave empty to use default)
              </span>
            </label>
            <input
              value={customMmdbUrl}
              onChange={(e) => setCustomMmdbUrl(e.target.value)}
              placeholder={sysSettings?.geoip_mmdb_url || 'https://git.io/GeoLite2-Country.mmdb'}
              className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-none"
            />
          </div>
        </div>

        <button
          onClick={handleUpdateAll}
          disabled={updateGeo.isPending}
          className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors"
        >
          <RefreshCw className={updateGeo.isPending ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />
          {updateGeo.isPending ? 'Downloading…' : 'Download All'}
        </button>

        {updateGeo.isSuccess && (
          <p className="text-xs text-green-400">Download queued in background.</p>
        )}
        {updateGeo.isError && (
          <p className="text-xs text-red-400">Error: {String(updateGeo.error)}</p>
        )}
      </div>

      {/* Profile picker — switch between curated geo lists. */}
      <ProfilePicker sysSettings={sysSettings ?? null} />

      {/* Default URL settings */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-5 space-y-4">
        <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
          <Globe className="h-4 w-4" />
          Default URLs
        </h2>
        <div className="space-y-3">
          {([
            ['geoip_url', 'Default GeoIP URL'],
            ['geosite_url', 'Default GeoSite URL'],
            ['geoip_mmdb_url', 'Default GeoLite2 MMDB URL'],
          ] as const).map(([key, label]) => (
            <div key={key}>
              <label className="block text-xs font-medium text-gray-400 mb-1">
                {label}
              </label>
              <div className="flex gap-2">
                <input
                  defaultValue={sysSettings?.[key] ?? ''}
                  key={sysSettings?.[key]}
                  id={`input-${key}`}
                  className="flex-1 rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 font-mono focus:border-brand-500 focus:outline-none"
                />
                <button
                  onClick={() => {
                    const el = document.getElementById(`input-${key}`) as HTMLInputElement
                    updateSettings.mutate({ [key]: el.value })
                  }}
                  className="rounded px-3 py-1.5 text-xs bg-gray-700 text-gray-300 hover:bg-gray-600 transition-colors"
                >
                  Save
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ── Profile picker ──────────────────────────────────────────────────────────
//
// Switches between curated geo-data sources by bulk-updating the URL
// settings. After switching, the user must click "Update all" in the
// header above to actually re-download the data files. We could trigger
// download automatically here too — but that would mean a 67 MB pull
// without confirmation when switching to runetfreedom, which feels
// surprising. Better to let the user opt in.

function ProfilePicker({
  sysSettings,
}: {
  sysSettings: { geoip_url?: string; geosite_url?: string; geoip_mmdb_url?: string } | null
}) {
  const updateSettings = useUpdateSettings()
  const active = detectActiveProfile({
    geoip_url: sysSettings?.geoip_url,
    geosite_url: sysSettings?.geosite_url,
    geoip_mmdb_url: sysSettings?.geoip_mmdb_url,
  })

  const apply = (profile: GeoProfile) => {
    updateSettings.mutate({
      geoip_url: profile.urls.geoip_url,
      geosite_url: profile.urls.geosite_url,
      geoip_mmdb_url: profile.urls.geoip_mmdb_url,
    })
  }

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/30 p-5 space-y-3">
      <h2 className="text-sm font-medium text-gray-300 flex items-center gap-2">
        <Layers className="h-4 w-4" />
        Geo profile
        <span className="ml-auto text-[11px] font-normal text-gray-500">
          Active: <span className="text-gray-300">{active}</span>
        </span>
      </h2>
      <p className="text-xs text-gray-500">
        Switching profile updates the three URLs below. After switching, use
        "Update all" above to download the new dataset. Quick-Add presets in
        the Routing page automatically switch to use category names that
        exist in the active profile's geosite.
      </p>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {GEO_PROFILES.map((p) => {
          const isActive = active === p.id
          return (
            <div
              key={p.id}
              className={`rounded-lg border p-3 transition-colors ${
                isActive
                  ? 'border-brand-600 bg-brand-600/10'
                  : 'border-gray-800 bg-gray-900/40 hover:border-gray-700'
              }`}
            >
              <div className="flex items-start gap-2">
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-semibold text-gray-100 flex items-center gap-2">
                    {p.label}
                    <span className="text-[10px] uppercase tracking-wider text-gray-500">
                      {p.geositeSize}
                    </span>
                    {isActive && (
                      <span className="rounded bg-brand-600/30 text-brand-300 px-1.5 py-0.5 text-[10px] uppercase tracking-wider">
                        active
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-gray-400 mt-1 leading-relaxed">
                    {p.description}
                  </p>
                </div>
              </div>
              {!isActive && (
                <button
                  onClick={() => apply(p)}
                  disabled={updateSettings.isPending}
                  className="mt-3 w-full rounded bg-gray-800 hover:bg-gray-700 text-gray-200 text-xs font-medium px-3 py-1.5 disabled:opacity-50 transition-colors"
                >
                  {updateSettings.isPending ? 'Switching…' : 'Switch to this profile'}
                </button>
              )}
            </div>
          )
        })}
      </div>
      {active === 'custom' && (
        <p className="text-[11px] text-yellow-500/80">
          ⚠ Current URLs don't match any registered profile. Quick-Add presets
          fall back to common categories (`category-ru`, `tld-ru`, `cn`, etc.).
        </p>
      )}
    </div>
  )
}
