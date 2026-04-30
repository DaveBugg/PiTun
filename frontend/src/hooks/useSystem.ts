import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { systemApi } from '@/api/client'
import type { ProxyMode, SystemSettings } from '@/types'

export function useSystemStatus() {
  return useQuery({
    queryKey: ['system', 'status'],
    queryFn: () => systemApi.status(),
    refetchInterval: 5_000,
  })
}

export function useSystemSettings() {
  return useQuery({
    queryKey: ['system', 'settings'],
    queryFn: () => systemApi.getSettings(),
  })
}

/**
 * Full version snapshot (PiTun backend/frontend, xray, host, docker, etc).
 *
 * `enabled` defaults false so the heavy introspection endpoint isn't hit
 * on every page load — pass `{ enabled: open }` from the popover and it'll
 * fetch once on first open, then cache for staleTime.
 */
export function useSystemVersions(opts?: { enabled?: boolean }) {
  return useQuery({
    queryKey: ['system', 'versions'],
    queryFn: () => systemApi.versions(),
    enabled: opts?.enabled ?? false,
    staleTime: 60_000,   // versions don't change between renders
  })
}

export function useStartProxy() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => systemApi.start(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['system'] }),
  })
}

export function useStopProxy() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => systemApi.stop(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['system'] }),
  })
}

export function useRestartProxy() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => systemApi.restart(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['system'] }),
  })
}

export function useSetMode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (mode: ProxyMode) => systemApi.setMode(mode),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['system'] }),
  })
}

export function useSetActiveNode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (nodeId: number) => systemApi.setActiveNode(nodeId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['system'] }),
  })
}

export function useUpdateSettings() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: Partial<SystemSettings>) => systemApi.updateSettings(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['system', 'settings'] }),
  })
}
