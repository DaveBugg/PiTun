import { ReactNode } from 'react'
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
}

export function ModalShell({ onClose, children, labelledBy, z = 50 }: ModalShellProps) {
  useEscapeKey(onClose)

  return (
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
}
