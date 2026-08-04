import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { serversApi } from '@/api/client'
import type {
  ServerCreate, ServerUpdate,
  ServerDeploymentProtocol, ServerDeploymentUpsert,
  DeploymentClientCreate, ExportClientToNodeRequest,
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
    mutationFn: ({ id, direct = false }: { id: number; direct?: boolean }) =>
      serversApi.test(id, direct),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['servers'] }),
  })
}

export function useTestAllServers() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (direct: boolean = false) => serversApi.testAll(direct),
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


// ── WireGuard server-side clients (since v1.3.0-beta.4) ────────────────────
//
// One DeploymentClient row per peer. Mutations invalidate both the
// clients query and the deployments query (status badge counts may
// change), and on export-to-node also the nodes list.

export function useDeploymentClients(serverId: number | null) {
  return useQuery({
    queryKey: ['servers', serverId, 'wg-clients'],
    queryFn: () => serversApi.listClients(serverId as number),
    enabled: !!serverId,
  })
}

export function useAddClient() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ serverId, body, direct = false }:
      { serverId: number; body: DeploymentClientCreate; direct?: boolean }) =>
      serversApi.addClient(serverId, body, direct),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['servers', vars.serverId, 'wg-clients'] })
      qc.invalidateQueries({ queryKey: ['servers', vars.serverId, 'deployments'] })
    },
  })
}

export function useRemoveClient() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ serverId, name, direct = false }:
      { serverId: number; name: string; direct?: boolean }) =>
      serversApi.removeClient(serverId, name, direct),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['servers', vars.serverId, 'wg-clients'] })
      // Linked Nodes may have flipped to client_orphan — refresh nodes.
      qc.invalidateQueries({ queryKey: ['nodes'] })
    },
  })
}

export function useSyncClients() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ serverId, direct = false }: { serverId: number; direct?: boolean }) =>
      serversApi.syncClients(serverId, direct),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['servers', vars.serverId, 'wg-clients'] })
      // Sync may have orphaned Nodes that map to server-side-deleted peers.
      qc.invalidateQueries({ queryKey: ['nodes'] })
    },
  })
}

export function useExportClientToNode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      serverId, name, body, direct = false,
    }: { serverId: number; name: string; body?: ExportClientToNodeRequest; direct?: boolean }) =>
      serversApi.exportClientToNode(serverId, name, body ?? {}, direct),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['servers', vars.serverId, 'wg-clients'] })
      qc.invalidateQueries({ queryKey: ['nodes'] })
    },
  })
}
