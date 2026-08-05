import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, X } from 'lucide-react'

import { apiErrorText } from '@/lib/apiError'

/**
 * Last-resort surface for mutation failures.
 *
 * Most system mutations (`start`/`stop`/`restart`, mode switch, active
 * node, settings PATCH) were fire-and-forget `.mutate()` calls with no
 * `onError` anywhere, and the QueryClient had no global handler either.
 * The backend deliberately builds messages for these — a 400 that
 * explains why an `inbound_mode` was rolled back, a 503 with a "wait and
 * retry" hint when the xray lock is busy — and every one of them was
 * dropped on the floor: the spinner just stopped and nothing changed on
 * screen, so the button looked broken.
 *
 * Subscribing to the MutationCache catches those without touching ~40
 * call sites. Mutations that render their own error UI stay unaffected:
 * this is additive, and a duplicate toast is far better than silence.
 */
export function MutationErrorToast() {
  const qc = useQueryClient()
  const [message, setMessage] = useState<string | null>(null)

  useEffect(() => {
    const cache = qc.getMutationCache()
    return cache.subscribe((event) => {
      // React on the ERROR ACTION, not on "some mutation in the cache
      // currently holds an error". A failed mutation stays in the cache
      // until it is garbage-collected, and every later cache event
      // (another mutation starting, succeeding, an observer detaching)
      // re-emits `updated` for it — which resurfaced one stale failure
      // on every subsequent click, so an unrelated action looked like it
      // had failed too.
      if (event.type !== 'updated' || event.action?.type !== 'error') return
      const error = event.action.error ?? event.mutation?.state.error
      if (!error) return
      setMessage(apiErrorText(error, 'Request failed'))
    })
  }, [qc])

  useEffect(() => {
    if (!message) return
    const id = setTimeout(() => setMessage(null), 8000)
    return () => clearTimeout(id)
  }, [message])

  if (!message) return null

  return (
    <div
      role="alert"
      className="fixed bottom-4 right-4 z-100 max-w-md rounded-lg border border-red-200 dark:border-red-800/60 bg-red-50 dark:bg-red-950/90 px-4 py-3 text-sm text-red-100 shadow-lg backdrop-blur-sm"
    >
      <div className="flex items-start gap-2">
        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-600 dark:text-red-400" />
        <span className="min-w-0 wrap-break-word">{message}</span>
        <button
          type="button"
          onClick={() => setMessage(null)}
          aria-label="Dismiss error"
          className="-mr-1 -mt-1 shrink-0 rounded-sm p-1 text-red-700 dark:text-red-300 hover:bg-red-50 dark:hover:bg-red-900/60 hover:text-red-100"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>
    </div>
  )
}
