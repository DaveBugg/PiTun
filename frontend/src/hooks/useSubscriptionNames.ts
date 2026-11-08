import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'

import { subsApi } from '@/api/client'

/** Subscription id → name, from the cached subscriptions list. */
export function useSubscriptionNames(): Map<number, string> {
  const { data = [] } = useQuery({
    queryKey: ['subscriptions'],
    queryFn: () => subsApi.list(),
  })
  return useMemo(() => new Map(data.map((s) => [s.id, s.name])), [data])
}
