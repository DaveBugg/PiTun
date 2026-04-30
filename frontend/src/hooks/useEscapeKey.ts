import { useEffect } from 'react'

/**
 * Calls `onEscape()` whenever the user presses Esc while the component is
 * mounted. Used by every page-level modal to standardise close-on-Esc
 * behaviour without each modal hand-rolling its own keydown listener.
 *
 * Usage:
 *
 *     useEscapeKey(() => setOpen(false))
 *
 * Notes:
 * - The handler is registered once per call and stays in sync with the
 *   latest `onEscape` reference on every render — no stale-closure trap.
 * - Listener attaches to `document` so it fires regardless of which
 *   element has focus inside the modal.
 * - When `enabled` is false the listener is skipped entirely (useful for
 *   modals that share a parent component with non-modal state).
 */
export function useEscapeKey(onEscape: () => void, enabled = true): void {
  useEffect(() => {
    if (!enabled) return
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onEscape()
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [onEscape, enabled])
}
