import { keepPreviousData, useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { nodesApi } from '@/api/client'
import type { NodeCreate, NodePageParams, NodeUpdate } from '@/types'

export function useNodes(params?: { enabled?: boolean; group?: string }) {
  return useQuery({
    queryKey: ['nodes', params],
    queryFn: () => nodesApi.list(params),
    refetchInterval: 60_000,
  })
}

/**
 * Paginated Nodes listing (since v1.3.3). Used by the Nodes page so
 * a subscription with 1000+ entries doesn't tank the UI. Separate from
 * `useNodes()` because that one is still needed by reorder / export /
 * circle-scheduler call sites that need the unbounded list.
 *
 * `placeholderData: keepPreviousData` keeps the previous page visible
 * while flipping pages — avoids the table collapsing to "Loading…"
 * mid-paginate, which looks especially janky with high latency.
 */
export function useNodesPage(params: NodePageParams) {
  return useQuery({
    queryKey: ['nodes', 'page', params],
    queryFn: () => nodesApi.listPage(params),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  })
}

export function useNode(id: number) {
  return useQuery({
    queryKey: ['nodes', id],
    queryFn: () => nodesApi.get(id),
    enabled: !!id,
  })
}

/**
 * Fetch the active Node row (or `undefined` when no active node is
 * set). Used by the Nodes page to pin the active node at the top of
 * the list, even when the current page/filter would hide it. Stable
 * key keeps the result cached across page navigations.
 */
export function useActiveNode(activeId: number | null | undefined) {
  return useQuery({
    queryKey: ['nodes', 'active', activeId],
    queryFn: () => nodesApi.get(activeId as number),
    enabled: activeId != null && activeId > 0,
    refetchInterval: 60_000,
    // Stale rows can show briefly while the query refetches — that's
    // better than blanking the pin between refreshes.
    placeholderData: (prev) => prev,
  })
}

export function useCreateNode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: NodeCreate) => nodesApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nodes'] }),
  })
}

export function useUpdateNode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: NodeUpdate }) => nodesApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nodes'] }),
  })
}

export function useDeleteNode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => nodesApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nodes'] }),
  })
}

export function useImportNodes() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ uris, subscriptionId, nameOverride }: { uris: string; subscriptionId?: number; nameOverride?: string }) =>
      nodesApi.import({ uris, name_override: nameOverride }, subscriptionId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nodes'] }),
  })
}

export function useCheckNodeHealth() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => nodesApi.checkHealth(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nodes'] }),
  })
}

export function useCheckAllNodes() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => nodesApi.checkAll(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['nodes'] }),
  })
}

export function useSpeedtest() {
  return useMutation({
    mutationFn: (id: number) => nodesApi.speedtest(id),
  })
}
