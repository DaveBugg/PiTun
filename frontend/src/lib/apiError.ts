import { isAxiosError } from 'axios'

/**
 * Pull a human-readable message out of an API failure.
 *
 * FastAPI answers with two different `detail` shapes and both reach the
 * UI: a plain string for the `HTTPException`s we raise by hand (those are
 * written for operators and worth showing verbatim), and an array of
 * per-field objects for pydantic 422s. Without the array branch a
 * validation error renders as the generic fallback and the operator has
 * no idea which field is wrong.
 */
export function apiErrorText(err: unknown, fallback: string): string {
  if (isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === 'string') return detail
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
