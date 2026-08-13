import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { systemApi } from '@/api/client'
import type { SystemSettings } from '@/types'

/**
 * Shared edit buffer over `/system/settings`.
 *
 * Settings and Router are two pages onto one settings table, so they have to
 * agree on what "unsaved" means: both read the same query, hold edits locally
 * until an explicit save, and highlight fields that differ from the stored
 * value. Duplicating that per page is how the two drift — one forgetting to
 * clear its draft after a save, or coercing an integer field differently from
 * the other.
 */

// Mirrors `SystemSettings` but keeps string-indexed access: the union gives
// autocomplete for known keys while leaving an escape hatch for settings not
// yet in the canonical type.
export type SettingValue = string | number | boolean | undefined
export type PartialSettings =
  Partial<Record<keyof SystemSettings, SettingValue>> & Record<string, SettingValue>

export function useSettingsDraft(intFields: readonly string[] = []) {
  const qc = useQueryClient()
  const { data: settings, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: systemApi.getSettings,
    staleTime: 60_000,
  })

  const [draft, setDraft] = useState<PartialSettings>({})
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')

  const mutation = useMutation({
    // The form holds ad-hoc string keys with string values; they're coerced
    // in `save` below. Cast at the boundary.
    mutationFn: (patch: PartialSettings) =>
      systemApi.updateSettings(patch as Partial<SystemSettings>),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] })
      // Both pages read this; a router-mode change alters what the other
      // one should be showing.
      qc.invalidateQueries({ queryKey: ['system', 'settings'] })
      setSaved(true)
      setError('')
      setDraft({})
      setTimeout(() => setSaved(false), 2000)
    },
    onError: (err: any) => {
      setError(err?.response?.data?.detail || err?.message || 'Failed to save settings')
    },
  })

  const val = (key: string): SettingValue => {
    if (draft[key] !== undefined) return draft[key]
    const v = (settings as any)?.[key]
    return v ?? ''
  }

  const isChecked = (key: string): boolean => {
    const v = val(key)
    if (typeof v === 'boolean') return v
    if (typeof v === 'string') return v.toLowerCase() === 'true'
    return Boolean(v)
  }

  const set = (key: string, value: SettingValue) =>
    setDraft((d) => ({ ...d, [key]: value }))

  const hasChanges = Object.keys(draft).length > 0

  const save = () => {
    if (!hasChanges) return
    setError('')
    const patch: PartialSettings = {}
    for (const [k, v] of Object.entries(draft)) {
      if (intFields.includes(k)) {
        const n = parseInt(String(v ?? ''))
        if (isNaN(n) || n < 1) {
          setError(`Invalid value for ${k}: ${v}`)
          return
        }
        patch[k] = n
      } else {
        patch[k] = v
      }
    }
    mutation.mutate(patch)
  }

  return {
    settings, isLoading, draft, val, isChecked, set,
    hasChanges, save, saving: mutation.isPending, saved, error, setError,
  }
}
