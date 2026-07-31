import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { createServerTaskSocket, serverTasksApi } from '@/api/client'
import type { JobStatus, JobStreamFrame } from '@/types'

/** Listing — newest first. The page uses `server_id` + `status` filters
 * driven by URL query state. We poll while a `running` job is in the
 * page's filter set so the list visibly updates without a manual
 * refresh; that's done by the caller via `refetchInterval`. */
export function useServerTasks(params?: {
  server_id?: number
  status?: JobStatus
  limit?: number
  offset?: number
}) {
  return useQuery({
    queryKey: ['server-tasks', params ?? {}],
    queryFn: () => serverTasksApi.list(params),
    // Soft poll every 5s — cheap, and only kicks in while the page is
    // mounted. The WS stream gives sub-second freshness for the open
    // detail view; this catches "another tab started a deploy" cases.
    refetchInterval: 5000,
  })
}

/** Single-job detail (with log_tail + config). Poll while running so the
 * status flip lands within ~2s of finalization even if the WS dropped.
 *
 * The interval is derived from the DATA, not just the caller's flag: a
 * static `2000` kept hammering the backend for a job that finished hours
 * ago (each response carries the full log_tail), while callers that set
 * `polling:false` the moment a `done` frame arrived stopped polling
 * before the terminal row was ever fetched — leaving the modal showing a
 * `running` snapshot with no result and no error. */
export function useServerTask(jobId: string | null, opts?: { polling?: boolean }) {
  return useQuery({
    queryKey: ['server-tasks', jobId],
    queryFn: () => serverTasksApi.get(jobId as string),
    enabled: !!jobId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (status && status !== 'running') return false
      return opts?.polling ? 2000 : false
    },
  })
}

/** POST cancel — flips status to `cancelled` server-side; we invalidate
 * both the listing and the detail so the UI repaints. */
export function useCancelServerTask() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (jobId: string) => serverTasksApi.cancel(jobId),
    onSuccess: (_data, jobId) => {
      qc.invalidateQueries({ queryKey: ['server-tasks', jobId] })
      qc.invalidateQueries({ queryKey: ['server-tasks'] })
    },
  })
}


/** Subscribe to the live log WebSocket for one job. Returns a snapshot
 * of frames + a `done` flag so the caller can render and stop showing
 * the spinner.
 *
 * Lifecycle:
 *   1. Mount or jobId change → open WS
 *   2. Each text frame is JSON-parsed into a `JobStreamFrame`
 *   3. `done` frame flips `done` state and closes WS
 *   4. Unmount → ws.close()
 *
 * On reconnect (jobId change), the buffer is reset. We do NOT cap the
 * frame array client-side — the backend already caps backlog at ~2000
 * lines and the typical deploy emits a few hundred. If that ever
 * changes we can virtualize the log view.
 */
export function useServerTaskStream(jobId: string | null) {
  const [frames, setFrames] = useState<JobStreamFrame[]>([])
  const [done, setDone] = useState<JobStatus | 'unknown' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const wsRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!jobId) return

    setFrames([])
    setDone(null)
    setError(null)

    const ws = createServerTaskSocket(jobId)
    wsRef.current = ws

    ws.onmessage = (e) => {
      try {
        const frame = JSON.parse(e.data as string) as JobStreamFrame
        if (frame.event === 'done') {
          setDone(frame.status)
        } else {
          setFrames((prev) => [...prev, frame])
        }
      } catch {
        // Ignore malformed frames
      }
    }
    ws.onerror = () => {
      setError('WebSocket error')
    }
    ws.onclose = () => {
      // If we never got a `done` frame, surface as unknown so the UI
      // stops spinning. Backend always emits done before close in the
      // happy path; this is the network-drop fallback.
      setDone((prev) => prev ?? 'unknown')
    }

    return () => {
      try {
        ws.close()
      } catch {
        // ignore
      }
      wsRef.current = null
    }
  }, [jobId])

  return { frames, done, error }
}
