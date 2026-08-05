import { useEffect, useRef } from 'react'
import { keepPreviousData, useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { nodesApi, speedtestStream, autocheckApi } from '@/api/client'
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

/**
 * Speed-test results and in-flight ids live in the query cache, NOT in
 * page state.
 *
 * A test takes 30–80s server-side and the backend persists nothing. With
 * the result held in `Nodes.tsx` state and written from per-`mutate()`
 * callbacks, three things went wrong: navigating away unmounted the page
 * (v5 drops per-mutate callbacks of an unmounted observer, so the result
 * vanished), starting a second test detached the first one's callbacks
 * (its row was stuck on "testing…" forever), and `mutation.variables`
 * only ever described the LAST call, so the spinner and the disabled
 * button tracked the wrong node.
 *
 * Cache entries survive unmount, so a test started on the Nodes page is
 * still there after a round trip to the Dashboard, and every node gets
 * its own pending flag.
 */
export type SpeedResults = Record<number, string>

const SPEED_RESULTS_KEY = ['speedtest', 'results']
const SPEED_PENDING_KEY = ['speedtest', 'pending']

export function useSpeedResults() {
  return useQuery<SpeedResults>({
    queryKey: SPEED_RESULTS_KEY,
    queryFn: async () => ({}),
    initialData: {},
    staleTime: Infinity,
    gcTime: Infinity,
  })
}

export function useSpeedPending() {
  return useQuery<number[]>({
    queryKey: SPEED_PENDING_KEY,
    queryFn: async () => [],
    initialData: [],
    staleTime: Infinity,
    gcTime: Infinity,
  })
}

function formatSpeed(r: { download_mbps?: number | null; error?: string | null }): string {
  return r.download_mbps != null ? `${r.download_mbps} Mbps` : r.error || 'failed'
}

export function useSpeedtest() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: number) => nodesApi.speedtest(id),
    // Hook-level callbacks (unlike per-mutate ones) fire for every
    // mutation even if the component that started it is gone.
    onMutate: (id) => {
      qc.setQueryData<number[]>(SPEED_PENDING_KEY, (p = []) =>
        p.includes(id) ? p : [...p, id],
      )
      qc.setQueryData<SpeedResults>(SPEED_RESULTS_KEY, (r = {}) => ({
        ...r,
        [id]: 'testing…',
      }))
    },
    onSuccess: (res, id) => {
      qc.setQueryData<SpeedResults>(SPEED_RESULTS_KEY, (r = {}) => ({
        ...r,
        [id]: formatSpeed(res),
      }))
    },
    onError: (_err, id) => {
      qc.setQueryData<SpeedResults>(SPEED_RESULTS_KEY, (r = {}) => ({
        ...r,
        [id]: 'error',
      }))
    },
    onSettled: (_res, _err, id) => {
      qc.setQueryData<number[]>(SPEED_PENDING_KEY, (p = []) =>
        p.filter((x) => x !== id),
      )
    },
  })
}

/**
 * Live streaming speed test. Writes progress into the SAME cache the
 * per-node card reads, so the number ticks up in place ("Cachefly · 45.2
 * Mbps") and — because the cache lives outside the component — the run
 * survives navigating away and pagination, exactly like the one-shot test.
 * Not a mutation: the stream is a long-lived fetch we drive by hand.
 */
export function useSpeedtestStream() {
  const qc = useQueryClient()
  const setResult = (id: number, text: string) =>
    qc.setQueryData<SpeedResults>(SPEED_RESULTS_KEY, (r = {}) => ({ ...r, [id]: text }))
  const setPending = (id: number, on: boolean) =>
    qc.setQueryData<number[]>(SPEED_PENDING_KEY, (p = []) =>
      on ? (p.includes(id) ? p : [...p, id]) : p.filter((x) => x !== id),
    )

  const run = (id: number) => {
    setPending(id, true)
    setResult(id, 'testing…')
    return speedtestStream(id, (e) => {
      if (e.phase === 'connecting') {
        setResult(id, e.host === 'starting xray' ? 'starting…' : `via ${e.host}…`)
      } else if (e.phase === 'progress') {
        setResult(id, `${e.host} · ${e.mbps} Mbps`)
      } else if (e.phase === 'done') {
        const max = e.mbps_max != null && e.mbps_max !== e.mbps ? ` ↑${e.mbps_max}` : ''
        setResult(id, `${e.mbps} Mbps${max} (${e.host})`)
      } else if (e.phase === 'error') {
        setResult(id, `error: ${(e.error || 'failed').slice(0, 40)}`)
      }
    })
      .catch(() => setResult(id, 'error'))
      .finally(() => {
        setPending(id, false)
        // Server persists the post-warmup average as the stream closes, so
        // by now node.speed_mbps/speed_tested_at are fresh — refetch so the
        // node card's speed badge (and its 6h staleness colour) updates.
        qc.invalidateQueries({ queryKey: ['nodes'] })
      })
  }

  return { run }
}

/**
 * "Does the internet work through this node" check. Reuses the same status
 * line + pending flag as the speed test (they're mutually exclusive on a
 * node), so it survives navigation the same way and needs no new render.
 */
export function useReachability() {
  const qc = useQueryClient()
  const setResult = (id: number, text: string) =>
    qc.setQueryData<SpeedResults>(SPEED_RESULTS_KEY, (r = {}) => ({ ...r, [id]: text }))
  const setPending = (id: number, on: boolean) =>
    qc.setQueryData<number[]>(SPEED_PENDING_KEY, (p = []) =>
      on ? (p.includes(id) ? p : [...p, id]) : p.filter((x) => x !== id),
    )

  const run = (id: number) => {
    setPending(id, true)
    setResult(id, 'checking internet…')
    nodesApi.reachability(id)
      .then((r) => setResult(
        id,
        r.ok
          ? `internet ok · ${r.latency_ms}ms`
          : `internet ✗ · ${(r.detail || 'unreachable').slice(0, 40)}`,
      ))
      .catch(() => setResult(id, 'internet ✗'))
      .finally(() => setPending(id, false))
  }

  return { run }
}

/**
 * "Speed All" — kicks off the SAME background sweep the auto-check uses
 * (sequential, staleness-guarded, no request timeout), forcing scope "all".
 * Returns immediately, so a big node set can't 504 the way the old
 * synchronous speedtest-all did. Results land on the node rows as the sweep
 * progresses; `useAutocheckSweep` polls the status and refreshes the list.
 * The manual run also stamps `last_sweep`, so the next scheduled sweep is
 * pushed out an interval and the two never collide.
 */
export function useSpeedtestAll() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => autocheckApi.run('all', true),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['autocheck'] }),
  })
}

/**
 * Poll the auto-check status while a sweep is running so the UI reflects
 * progress: refreshes the node list on each tick (speeds appear live) and
 * once more when the sweep finishes. Returns whether a sweep is in flight
 * (drives the "Speed All" spinner). Idle → no polling.
 */
export function useAutocheckSweep(): boolean {
  const qc = useQueryClient()
  const status = useQuery({
    queryKey: ['autocheck'],
    queryFn: () => autocheckApi.get(),
    refetchInterval: (q) => (q.state.data?.is_sweeping ? 3000 : false),
  })
  const sweeping = !!status.data?.is_sweeping
  const wasSweeping = useRef(false)
  useEffect(() => {
    if (sweeping || wasSweeping.current) {
      qc.invalidateQueries({ queryKey: ['nodes'] })
    }
    wasSweeping.current = sweeping
  }, [sweeping, status.dataUpdatedAt, qc])
  return sweeping
}
