import { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useEscapeKey } from '@/hooks/useEscapeKey'

/**
 * Shared backdrop wrapper for every page-level modal in the app.
 *
 * What it provides:
 * - Fullscreen fixed backdrop with `bg-black/70`
 * - Close on Esc (via `useEscapeKey`)
 * - Close on backdrop click; clicks inside the child do not propagate
 * - `role="dialog"` + `aria-modal="true"` for assistive tech
 * - Optional `aria-labelledby` (pass the id of your modal's `<h2>`) so
 *   screen readers announce the dialog title automatically
 *
 * Inner sizing/styling stays at the call site — the wrapper only owns
 * positioning and behaviour. Typical use:
 *
 *     <ModalShell onClose={() => setOpen(false)} labelledBy="my-modal-title">
 *       <div className="w-full max-w-lg rounded-2xl bg-gray-950 …">
 *         <h2 id="my-modal-title" …>…</h2>
 *         …
 *       </div>
 *     </ModalShell>
 *
 * Sister to <ConfirmModal>, which has its own copy of these behaviours
 * because its rendering is fully controlled by <ConfirmProvider>.
 */
interface ModalShellProps {
  onClose: () => void
  children: ReactNode
  /** Element id of the modal's heading — wires up aria-labelledby. */
  labelledBy?: string
  /** Override z-index (default 50). Bump if you nest dialogs. */
  z?: number
  /**
   * Set false while a dialog is open ON TOP of this one. `useEscapeKey`
   * listens on `document`, so otherwise one Esc collapses the whole stack
   * and loses the state underneath. Backdrop clicks don't need this — the
   * child's backdrop covers the parent's.
   */
  closeOnEscape?: boolean
}

export function ModalShell({
  onClose, children, labelledBy, z = 50, closeOnEscape = true,
}: ModalShellProps) {
  useEscapeKey(onClose, closeOnEscape)

  // Portal to document.body so the modal isn't a child of any
  // page-level wrapper. Without this, callers that render the modal
  // inline as a sibling inside a `space-y-N` container get a
  // tailwind-injected `margin-top` on the dialog node — the margin
  // is technically ignored for positioning by `position: fixed +
  // inset:0`, but in some browsers (mobile Chrome / Safari) the
  // dialog's own backdrop `bg-black/70` gets a visible top strip
  // where the body background bleeds through, which looks like a
  // partial overlay. Portal'ing breaks the parent chain entirely
  // and the issue can't recur. SSR-safe via the `typeof document`
  // guard (frontend currently is SPA-only, but the guard costs
  // nothing).
  const node = (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby={labelledBy}
      className="fixed inset-0 flex items-center justify-center bg-black/70"
      style={{ zIndex: z }}
      onClick={onClose}
    >
      <div onClick={(e) => e.stopPropagation()}>{children}</div>
    </div>
  )

  if (typeof document === 'undefined') return node
  return createPortal(node, document.body)
}
