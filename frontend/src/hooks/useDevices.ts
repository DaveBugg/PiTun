import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { devicesApi } from '@/api/client'
import type { DeviceUpdate, DeviceBulkUpdate } from '@/types'

export function useDevices(params?: { online_only?: boolean; policy?: string }) {
  return useQuery({
    queryKey: ['devices', params],
    queryFn: () => devicesApi.list(params),
    refetchInterval: 30_000,
  })
}

export function useUpdateDevice() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: DeviceUpdate }) => devicesApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['devices'] }),
  })
}

export function useDeleteDevice() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => devicesApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['devices'] }),
  })
}

export function useScanDevices() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => devicesApi.scan(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['devices'] }),
  })
}

export function useBulkPolicy() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: DeviceBulkUpdate) => devicesApi.bulkPolicy(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['devices'] }),
  })
}

export function useResetAllPolicies() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => devicesApi.resetAllPolicies(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['devices'] }),
  })
}
