import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { serversApi } from '@/api/client'
import type {
  ServerCreate, ServerUpdate,
  ServerDeploymentProtocol, ServerDeploymentUpsert,
} from '@/types'

// Server list — invalidated by every mutation in this file. We don't
// auto-refetch on a timer the way useNodes does because connection state
// changes only when the user clicks Test (no health-check daemon for SSH).

export function useServers() {
  return useQuery({
    queryKey: ['servers'],
    queryFn: () => serversApi.list(),
  })
}

export function useServer(id: number) {
  return useQuery({
    queryKey: ['servers', id],
    queryFn: () => serversApi.get(id),
    enabled: !!id,
  })
}

export function useCreateServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data: ServerCreate) => serversApi.create(data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['servers'] }),
  })
}

export function useUpdateServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: ServerUpdate }) =>
      serversApi.update(id, data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['servers'] }),
  })
}

export function useDeleteServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => serversApi.delete(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['servers'] }),
  })
}

export function useTestServer() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => serversApi.test(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['servers'] }),
  })
}

export function useTestAllServers() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => serversApi.testAll(),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['servers'] }),
  })
}

// ── Deployments ─────────────────────────────────────────────────────────────

export function useDeployments(serverId: number | null) {
  return useQuery({
    queryKey: ['servers', serverId, 'deployments'],
    queryFn: () => serversApi.listDeployments(serverId as number),
    enabled: !!serverId,
  })
}

export function useUpsertDeployment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      serverId, protocol, data,
    }: { serverId: number; protocol: ServerDeploymentProtocol; data: ServerDeploymentUpsert }) =>
      serversApi.upsertDeployment(serverId, protocol, data),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['servers', vars.serverId, 'deployments'] })
    },
  })
}

export function useDeleteDeployment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      serverId, protocol,
    }: { serverId: number; protocol: ServerDeploymentProtocol }) =>
      serversApi.deleteDeployment(serverId, protocol),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['servers', vars.serverId, 'deployments'] })
    },
  })
}

export function useCreateNodeFromDeployment() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      serverId, protocol,
    }: { serverId: number; protocol: ServerDeploymentProtocol }) =>
      serversApi.createNodeFromDeployment(serverId, protocol),
    onSuccess: (_data, vars) => {
      // Invalidate both: nodes list got a new entry, and the deployment
      // status flipped to "deployed" + got a last_node_id.
      qc.invalidateQueries({ queryKey: ['nodes'] })
      qc.invalidateQueries({ queryKey: ['servers', vars.serverId, 'deployments'] })
    },
  })
}
