import { useState } from 'react'
import {
  Download, Globe, RefreshCw, CheckCircle, XCircle, Layers,
  AlertTriangle, Loader2,
} from 'lucide-react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { geodataApi } from '@/api/client'
import { useSystemSettings, useUpdateSettings } from '@/hooks/useSystem'
import { GEO_PROFILES, detectActiveProfile, type GeoProfile } from '@/lib/geoProfiles'
import type { GeoUpdateProgress, GeoUpdateFileProgress } from '@/types'

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

  // Live progress poll. `useGeoUpdateProgress` polls 2 Hz while
  // active and stops automatically once `active` flips to false (one
  // last cycle captures the final state). When idle and never run,
  // returns the canonical zero-state so the UI can suppress the
  // progress panel without a separate "has any job ever existed"
  // signal.
  const progress = useGeoUpdateProgress()

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
    <div className="p-4 sm:p-6 space-y-6">
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
          disabled={updateGeo.isPending || progress?.active}
          className="flex items-center gap-2 rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors"
        >
          <RefreshCw className={progress?.active ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />
          {progress?.active ? 'Downloading…' : 'Download All'}
        </button>

        {updateGeo.isError && (
          <p className="text-xs text-red-400">Error: {String(updateGeo.error)}</p>
        )}

        {/* Live progress — visible from the moment a job is in flight
            until ~1s after it finishes (so the user sees the green
            "done" bar before it disappears). Hidden entirely when no
            job has been run since backend boot. */}
        {progress && (progress.active || Object.keys(progress.files).length > 0) && (
          <GeoProgressPanel progress={progress} />
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


// ── Live update progress ────────────────────────────────────────────────────
//
// `useGeoUpdateProgress` polls `GET /api/geodata/update/progress` at
// 2 Hz only while a job is in flight, then drops to 0 (manual refetch
// only) once `active` flips false. This keeps the LAN traffic at
// idle ≈ zero while still surfacing real progress during a download.
//
// We always do one immediate refetch on mount in case a job started
// from another tab — covers the "user has GeoData open in two
// windows and clicked Update in one" edge case.

function useGeoUpdateProgress(): GeoUpdateProgress | undefined {
  const { data } = useQuery({
    queryKey: ['geodata', 'progress'],
    queryFn: () => geodataApi.progress(),
    refetchInterval: (query) => {
      const last = query.state.data as GeoUpdateProgress | undefined
      // Poll while active. After finish, do one more cycle (~500ms)
      // so the UI captures the final 'done' state, then stop.
      if (last?.active) return 500
      return false
    },
    refetchOnWindowFocus: true,
  })
  return data
}

const FILE_LABELS: Record<string, string> = {
  geoip: 'geoip.dat',
  geosite: 'geosite.dat',
  mmdb: 'GeoLite2-Country.mmdb',
}

function GeoProgressPanel({ progress }: { progress: GeoUpdateProgress }) {
  const entries = Object.entries(progress.files)
  if (entries.length === 0) return null

  const allDone = entries.every(([, f]) => f.stage === 'done')
  const anyFailed = entries.some(([, f]) => f.stage === 'failed')

  return (
    <div className="rounded-lg border border-gray-800 bg-gray-950/40 p-4 space-y-3">
      <div className="flex items-center gap-2 text-xs">
        {progress.active ? (
          <>
            <Loader2 className="h-3.5 w-3.5 animate-spin text-brand-400" />
            <span className="text-brand-300">Update in progress…</span>
          </>
        ) : anyFailed ? (
          <>
            <AlertTriangle className="h-3.5 w-3.5 text-red-400" />
            <span className="text-red-300">Update finished with errors</span>
          </>
        ) : allDone ? (
          <>
            <CheckCircle className="h-3.5 w-3.5 text-green-400" />
            <span className="text-green-300">Update complete</span>
            {progress.tag_cache_refreshed && (
              <span className="text-[10px] text-gray-500">· tag cache reloaded into xray</span>
            )}
          </>
        ) : null}
      </div>
      <div className="space-y-2">
        {entries.map(([name, file]) => (
          <FileProgressRow key={name} name={name} file={file} />
        ))}
      </div>
    </div>
  )
}

function FileProgressRow({ name, file }: { name: string; file: GeoUpdateFileProgress }) {
  const label = FILE_LABELS[name] ?? name
  // Determinate bar when we know `bytes_total`; striped indeterminate
  // otherwise. `failed` and `done` get terminal colours.
  const pct = file.bytes_total
    ? Math.min(100, Math.round((file.bytes_downloaded / file.bytes_total) * 100))
    : null

  const stageColour = {
    queued: 'text-gray-500',
    downloading: 'text-brand-300',
    verifying: 'text-brand-300',
    applying: 'text-brand-300',
    done: 'text-green-400',
    failed: 'text-red-400',
  }[file.stage] || 'text-gray-400'

  const barColour = file.stage === 'failed'
    ? 'bg-red-500/70'
    : file.stage === 'done'
      ? 'bg-green-500/80'
      : 'bg-brand-500/80'

  return (
    <div>
      <div className="flex items-center gap-2 text-xs mb-1">
        <span className="font-mono text-gray-300 w-44 truncate">{label}</span>
        <span className={`uppercase tracking-wider text-[10px] ${stageColour}`}>
          {file.stage}
        </span>
        <span className="ml-auto font-mono text-[11px] text-gray-500">
          {formatSize(file.bytes_downloaded)}
          {file.bytes_total ? ` / ${formatSize(file.bytes_total)}` : ''}
          {pct !== null ? ` · ${pct}%` : ''}
        </span>
      </div>
      <div className="h-1.5 rounded bg-gray-800 overflow-hidden">
        {pct !== null ? (
          <div
            className={`h-full ${barColour} transition-all duration-300`}
            style={{ width: `${pct}%` }}
          />
        ) : file.stage === 'downloading' ? (
          // Indeterminate (no Content-Length): striped sliding bar.
          <div className="h-full w-1/3 bg-brand-500/60 animate-pulse" />
        ) : null}
      </div>
      {file.error && (
        <p className="mt-1 text-[11px] text-red-400 break-words">
          {file.error}
        </p>
      )}
      {file.source_url && file.stage === 'failed' && (
        <p className="mt-0.5 text-[10px] text-gray-600 font-mono break-all">
          source: {file.source_url}
        </p>
      )}
    </div>
  )
}
