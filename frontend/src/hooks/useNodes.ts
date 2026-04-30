import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { nodesApi } from '@/api/client'
import type { NodeCreate, NodeUpdate } from '@/types'

export function useNodes(params?: { enabled?: boolean; group?: string }) {
  return useQuery({
    queryKey: ['nodes', params],
    queryFn: () => nodesApi.list(params),
    refetchInterval: 60_000,
  })
}

export function useNode(id: number) {
  return useQuery({
    queryKey: ['nodes', id],
    queryFn: () => nodesApi.get(id),
    enabled: !!id,
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
    mutationFn: ({ uris, subscriptionId }: { uris: string; subscriptionId?: number }) =>
      nodesApi.import({ uris }, subscriptionId),
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
