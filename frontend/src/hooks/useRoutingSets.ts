import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { routingSetsApi } from '@/api/client'
import type {
  RoutingSetCreate,
  RoutingSetUpdate,
  DeviceBulkSetAssign,
} from '@/types'

// Per-device-group routing sets (v1.4).
//
// All mutations invalidate `['routing-sets']`, `['routing', 'rules']`
// AND `['devices']` because:
//   * creating/deleting a set changes the rules tab list
//   * deleting a set with cascade=move-to-global flips routing_set_id
//     to null on dependent rules + devices
//   * bulk-assign moves device routing_set_id around
// Conservative invalidation is fine — the queries are small.

export function useRoutingSets() {
  return useQuery({
    queryKey: ['routing-sets'],
    queryFn: () => routingSetsApi.list(),
    // Sets rarely change once configured — no auto-refetch needed.
    // Mutations explicitly invalidate the cache below.
    staleTime: 60_000,
  })
}

export function useRoutingSetCapacity() {
  return useQuery({
    queryKey: ['routing-sets', 'capacity'],
    queryFn: () => routingSetsApi.capacity(),
    staleTime: 30_000,
  })
}

export function useCreateRoutingSet() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: RoutingSetCreate) => routingSetsApi.create(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['routing-sets'] })
    },
  })
}

export function useUpdateRoutingSet() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: RoutingSetUpdate }) =>
      routingSetsApi.update(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['routing-sets'] })
    },
  })
}

export function useDeleteRoutingSet() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, cascade }: { id: number; cascade?: 'move-to-global' }) =>
      routingSetsApi.delete(id, cascade ? { cascade } : undefined),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['routing-sets'] })
      qc.invalidateQueries({ queryKey: ['routing', 'rules'] })
      qc.invalidateQueries({ queryKey: ['devices'] })
    },
  })
}

export function useBulkAssignDevices() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: DeviceBulkSetAssign) =>
      routingSetsApi.bulkAssignDevices(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['devices'] })
      qc.invalidateQueries({ queryKey: ['routing-sets'] })
    },
  })
}
