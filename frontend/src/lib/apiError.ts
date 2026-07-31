import { isAxiosError } from 'axios'

/**
 * Pull a human-readable message out of an API failure.
 *
 * FastAPI uses two `detail` shapes and both reach the UI: a plain string
 * from a hand-raised `HTTPException`, and an array of per-field objects
 * from a pydantic 422. Without the array branch a validation error
 * renders as the generic fallback, naming no field.
 */
export function apiErrorText(err: unknown, fallback: string): string {
  if (isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
    // Structured detail: several endpoints raise
    // `HTTPException(detail={"error": ..., "hint": ...})` — the 409 from
    // the subscription-refresh mutex and the 503 from a busy xray lock
    // both do. Rendered as the generic fallback, the hint (the only part
    // that tells the operator what to do) was thrown away.
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const d = detail as { error?: unknown; message?: unknown; hint?: unknown }
      const head =
        typeof d.error === 'string' ? d.error
          : typeof d.message === 'string' ? d.message : ''
      const hint = typeof d.hint === 'string' ? d.hint : ''
      if (head && hint) return `${head} — ${hint}`
      if (head) return head
      if (hint) return hint
    }
    if (Array.isArray(detail) && detail.length > 0) {
      const first = detail[0] as { loc?: unknown[]; msg?: unknown }
      const loc = Array.isArray(first?.loc)
        ? first.loc.filter((p) => p !== 'body' && typeof p !== 'number')
        : []
      const msg = typeof first?.msg === 'string' ? first.msg : ''
      if (msg) return loc.length > 0 ? `${loc.join('.')}: ${msg}` : msg
    }
  }
  if (err instanceof Error && err.message) return err.message
  return fallback
}
