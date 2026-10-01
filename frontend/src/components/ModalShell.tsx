import { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { useEscapeKey } from '@/hooks/useEscapeKey'
import { useT } from '@/hooks/useT'

/**
 * Shared backdrop wrapper for every page-level modal in the app.
 *
 * What it provides:
 * - Fullscreen fixed backdrop with `bg-black/70` and a 1rem gutter
 * - A close ✕ pinned to the dialog's top-right corner
 * - Close on Esc (via `useEscapeKey`)
 * - `role="dialog"` + `aria-modal="true"` for assistive tech
 * - Optional `aria-labelledby` (pass the id of your modal's `<h2>`) so
 *   screen readers announce the dialog title automatically
 *
 * A backdrop click deliberately does NOT close: half these dialogs are
 * data-entry forms and a stray click outside threw the input away.
 *
 * Inner sizing/styling stays at the call site — the wrapper only owns
 * positioning and behaviour. Cards should carry no outer margin of
 * their own; the gutter lives here so the ✕ lands on the card corner.
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
   * and loses the state underneath.
   */
  closeOnEscape?: boolean
  /** Set false when the card draws its own ✕ in a header row. */
  showClose?: boolean
}

export function ModalShell({
  onClose, children, labelledBy, z = 50, closeOnEscape = true, showClose = true,
}: ModalShellProps) {
  const t = useT()
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
      className="fixed inset-0 flex items-center justify-center bg-black/70 p-4"
      style={{ zIndex: z }}
    >
      {/* `relative` anchors the ✕; it sits outside any `overflow-y-auto`
          on the card, so it stays put while the body scrolls. */}
      <div className="relative">
        {showClose && (
          <button
            type="button"
            onClick={onClose}
            aria-label={t('Close', 'Закрыть')}
            title={t('Close', 'Закрыть')}
            className="absolute top-2.5 right-2.5 z-10 rounded-md p-1 text-gray-500 hover:bg-gray-800 hover:text-gray-200 transition-colors"
          >
            <X className="h-4 w-4" />
          </button>
        )}
        {children}
      </div>
    </div>
  )

  if (typeof document === 'undefined') return node
  return createPortal(node, document.body)
}
