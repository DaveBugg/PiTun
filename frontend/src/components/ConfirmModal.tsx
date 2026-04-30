/**
 * Themed replacement for `window.confirm()` — promise-based API + provider
 * so call sites read like the native browser one but get our styling,
 * Esc/Enter handling, and theme inheritance.
 *
 * Setup once in main.tsx:
 *
 *     <ConfirmProvider>
 *       <App />
 *     </ConfirmProvider>
 *
 * Use anywhere:
 *
 *     const confirm = useConfirm()
 *     onClick={async () => {
 *       const ok = await confirm({
 *         title: 'Delete rule?',
 *         body: `Rule "${rule.name}" will be permanently deleted.`,
 *         confirmLabel: 'Delete',
 *         danger: true,
 *       })
 *       if (ok) deleteRule.mutate(rule.id)
 *     }}
 *
 * The dialog mounts at the provider, so individual call sites don't need
 * their own modal markup or state. Only one dialog can be open at a time
 * — a second `confirm(...)` call before the first resolves will queue
 * (rejected previous resolves with false).
 */
import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from 'react'
import { AlertTriangle } from 'lucide-react'
import { clsx } from 'clsx'

// ── Stand-alone modal — exported for callers that prefer controlled
//    rendering (state-driven open/close) over the promise hook.
interface ConfirmModalProps {
  title: string
  body?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmModal({
  title,
  body,
  confirmLabel = 'Confirm',
  cancelLabel = 'Cancel',
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmModalProps) {
  const confirmBtnRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    confirmBtnRef.current?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onCancel])

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="confirm-modal-title"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70"
      onClick={onCancel}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-md mx-4 rounded-xl bg-gray-950 border border-gray-800 p-5 shadow-2xl"
      >
        <div className="flex items-start gap-3">
          {danger && (
            <div className="flex h-9 w-9 items-center justify-center rounded-full bg-red-900/30 shrink-0">
              <AlertTriangle className="h-5 w-5 text-red-400" />
            </div>
          )}
          <div className="flex-1 min-w-0">
            <h2 id="confirm-modal-title" className="text-base font-semibold text-gray-100">
              {title}
            </h2>
            {body && <div className="mt-2 text-sm text-gray-400">{body}</div>}
          </div>
        </div>

        <div className="mt-5 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded-lg px-3 py-1.5 text-sm text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors"
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmBtnRef}
            onClick={onConfirm}
            className={clsx(
              'rounded-lg px-3 py-1.5 text-sm font-medium text-white transition-colors',
              danger
                ? 'bg-red-700 hover:bg-red-600'
                : 'bg-brand-600 hover:bg-brand-500',
            )}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}


// ── Promise hook + provider for the typical "if (confirm(…)) doX()" flow.

export interface ConfirmOptions {
  title: string
  body?: ReactNode
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
}

type Pending = {
  opts: ConfirmOptions
  resolve: (v: boolean) => void
}

const ConfirmContext = createContext<((opts: ConfirmOptions) => Promise<boolean>) | null>(null)

export function ConfirmProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null)

  const confirm = useCallback((opts: ConfirmOptions) => {
    return new Promise<boolean>((resolve) => {
      // If a second confirm() fires while one is open, resolve the
      // previous as false and replace it. Stops the unlikely double-modal
      // case from getting stuck.
      setPending((prev) => {
        if (prev) prev.resolve(false)
        return { opts, resolve }
      })
    })
  }, [])

  const close = (result: boolean) => {
    if (!pending) return
    pending.resolve(result)
    setPending(null)
  }

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      {pending && (
        <ConfirmModal
          {...pending.opts}
          onConfirm={() => close(true)}
          onCancel={() => close(false)}
        />
      )}
    </ConfirmContext.Provider>
  )
}

export function useConfirm() {
  const ctx = useContext(ConfirmContext)
  if (!ctx) {
    throw new Error('useConfirm() must be called inside <ConfirmProvider>')
  }
  return ctx
}
