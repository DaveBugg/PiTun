import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { eventsApi } from '@/api/client'

/**
 * Recent Events list. Polls every 30 s — events are not real-time but a
 * 30-second cadence matches user expectations for "what just changed?"
 * without burdening the backend (the trim loop runs every 10 min, so
 * the table stays small even under burst).
 */
export function useEvents(params?: { limit?: number; category?: string; severity?: string }) {
  return useQuery({
    queryKey: ['events', params],
    queryFn: () => eventsApi.list(params),
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
}

export function useClearEvents() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: eventsApi.clear,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['events'] }),
  })
}
