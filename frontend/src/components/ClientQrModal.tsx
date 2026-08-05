import { useEffect, useRef, useState } from 'react'
import { QRCodeSVG } from 'qrcode.react'
import { Loader2, Copy, Check, Download, QrCode, X, AlertTriangle } from 'lucide-react'
import { ModalShell } from '@/components/ModalShell'
import { useT } from '@/hooks/useT'
import { copyToClipboard } from '@/lib/clipboard'

/**
 * Show a QR code for a single share URI. Used by the X-ui Panels and
 * Proxy Chains pages so the operator can scan a config straight into
 * a mobile VPN client without copy-pasting through some other channel.
 *
 * Two source modes:
 *  - **eager** — caller passes `uri` directly (used by chain clients,
 *    whose `vless_uri` is already part of the listing payload)
 *  - **lazy**  — caller passes a `fetchUri` async resolver (used by
 *    the X-ui inbound clients, where we hit `GET .../share-uri` on
 *    demand to avoid leaking secrets into every inbound listing)
 *
 * If the resolver returns `{ uri: null, reason: '...' }` the modal
 * renders the reason text instead of the QR — same UX as the
 * disabled-with-tooltip branch on the trigger button itself.
 */
export interface ClientQrModalProps {
  open: boolean
  onClose: () => void
  /** Heading shown above the QR (e.g. "VPN-VK · admin@home"). */
  title: string
  /** Eager URI (alternative to `fetchUri`). One of the two is required. */
  uri?: string | null
  /** Lazy URI resolver — called on open. */
  fetchUri?: () => Promise<{ uri: string | null; reason: string | null }>
  /** Optional subtitle shown under the title (e.g. inbound remark). */
  subtitle?: string
}

export function ClientQrModal({ open, onClose, title, uri, fetchUri, subtitle }: ClientQrModalProps) {
  const t = useT()
  const [resolved, setResolved] = useState<{ uri: string | null; reason: string | null } | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [reveal, setReveal] = useState(false)
  const [copied, setCopied] = useState(false)
  const svgRef = useRef<SVGSVGElement | null>(null)

  // Pin the latest fetcher in a ref so the resolve-effect can call it
  // without listing `fetchUri` as a dep. The parent re-creates the
  // arrow function on every render (`qrTarget ? () => api(…) : undef`),
  // and depending on that ref makes the effect refire each render →
  // setResolved → render → loop. The `open` flag + the eager `uri`
  // value are the *real* triggers; everything else just needs to be
  // current when those flip.
  const fetchRef = useRef(fetchUri)
  useEffect(() => { fetchRef.current = fetchUri }, [fetchUri])

  // Resolve on open. Eager `uri` short-circuits the fetcher.
  // Deps intentionally limited to [open, uri] — see fetchRef comment.
  useEffect(() => {
    if (!open) return
    setReveal(false)
    setCopied(false)
    setError(null)
    if (uri !== undefined) {
      // Reason text is rendered from a sentinel ('no_uri') at render
      // time so we don't have to depend on `t` here (the translator
      // is a fresh closure each render too).
      setResolved({ uri: uri ?? null, reason: uri ? null : 'no_uri' })
      return
    }
    const fn = fetchRef.current
    if (!fn) {
      setResolved({ uri: null, reason: 'No URI source provided.' })
      return
    }
    let cancelled = false
    setLoading(true)
    fn()
      .then((r) => { if (!cancelled) setResolved(r) })
      .catch((e: Error) => { if (!cancelled) setError(String(e?.message || e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [open, uri])

  const finalUri = resolved?.uri ?? null
  // Localize the 'no_uri' sentinel at render time — keeps the resolve
  // effect free of `t` as a dependency.
  const displayReason = resolved?.reason === 'no_uri'
    ? t('No share URI available for this client.', 'Для этого клиента URI недоступен.')
    : resolved?.reason ?? null

  const handleCopy = async () => {
    if (!finalUri) return
    // `copyToClipboard` handles the insecure-HTTP fallback. PiTun UI is
    // typically served over plain HTTP on a LAN IP, where the secure-
    // context-only `navigator.clipboard` API silently rejects.
    if (await copyToClipboard(finalUri)) {
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  // Rasterise the SVG into a PNG blob and trigger a download. Keeps
  // the file small (~3 KB) and works offline. The .download attribute
  // on an anchor is universally supported in the Electron/Chrome
  // browser targets the UI runs in.
  const handleDownload = () => {
    if (!finalUri || !svgRef.current) return
    const svg = svgRef.current
    const xml = new XMLSerializer().serializeToString(svg)
    const blob = new Blob([xml], { type: 'image/svg+xml;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${title.replace(/[^a-zA-Z0-9_\-.]/g, '_')}.svg`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  if (!open) return null

  return (
    <ModalShell onClose={onClose} labelledBy="qr-modal-title">
      <div className="w-[min(92vw,28rem)] rounded-2xl bg-gray-950 border border-gray-800 shadow-2xl">
        <div className="flex items-start justify-between gap-3 p-4 border-b border-gray-800">
          <div className="min-w-0">
            <h2
              id="qr-modal-title"
              className="text-sm font-semibold text-gray-100 flex items-center gap-2"
            >
              <QrCode className="h-4 w-4 text-brand-400" />
              {title}
            </h2>
            {subtitle && (
              <div className="text-[11px] text-gray-500 mt-0.5 truncate">{subtitle}</div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-gray-500 hover:bg-gray-800 hover:text-gray-200"
            aria-label={t('Close', 'Закрыть')}
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-4 space-y-3">
          {loading && (
            <div className="flex items-center justify-center py-12 text-gray-500 text-sm">
              <Loader2 className="h-5 w-5 animate-spin mr-2" />
              {t('Loading…', 'Загрузка…')}
            </div>
          )}

          {error && !loading && (
            <div className="flex items-start gap-2 rounded-md border border-red-200 dark:border-red-900/60 bg-red-50 dark:bg-red-900/20 p-3 text-[12px] text-red-800 dark:text-red-200">
              <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
              <div>
                <div className="font-medium">{t('Failed to load URI', 'Не удалось получить URI')}</div>
                <div className="text-red-700 dark:text-red-300/80 mt-1">{error}</div>
              </div>
            </div>
          )}

          {!loading && !error && finalUri && (
            <>
              {/*
                * level="M" balances density vs error correction. URIs
                * tend to be ~150-300 chars — M (15% recovery) keeps
                * the lattice scannable from a phone held loosely
                * without inflating module count too much.
                */}
              <div className="flex items-center justify-center rounded-xl bg-white p-3">
                <QRCodeSVG
                  ref={svgRef}
                  value={finalUri}
                  size={256}
                  level="M"
                  includeMargin={false}
                />
              </div>
              <div className="space-y-1">
                <label className="text-[11px] uppercase tracking-wider text-gray-500">
                  {t('URI', 'URI')}
                </label>
                <div className="flex gap-1.5">
                  <input
                    readOnly
                    type={reveal ? 'text' : 'password'}
                    value={finalUri}
                    onFocus={(e) => e.currentTarget.select()}
                    className="flex-1 rounded-md border border-gray-800 bg-gray-900 px-2 py-1.5 text-[11px] font-mono text-gray-200 focus:border-brand-700 focus:outline-hidden"
                  />
                  <button
                    type="button"
                    onClick={() => setReveal((v) => !v)}
                    className="rounded-md border border-gray-700 bg-gray-900 px-2 py-1 text-[11px] text-gray-300 hover:border-brand-400/50 hover:text-brand-700 dark:hover:text-brand-300"
                    title={reveal ? t('Hide', 'Скрыть') : t('Reveal', 'Показать')}
                  >
                    {reveal ? t('Hide', 'Скрыть') : t('Reveal', 'Показать')}
                  </button>
                </div>
                <p className="text-[10px] text-gray-600">
                  {t(
                    'URI contains credentials (UUID or password). Hidden by default.',
                    'URI содержит креды (UUID или пароль). По умолчанию скрыт.',
                  )}
                </p>
              </div>

              <div className="flex items-center gap-2 pt-1">
                <button
                  type="button"
                  onClick={handleCopy}
                  className="inline-flex items-center gap-1.5 rounded-md border border-gray-700 bg-gray-900 px-2.5 py-1.5 text-[11px] text-gray-200 hover:border-brand-400/50 hover:text-brand-700 dark:hover:text-brand-300"
                >
                  {copied
                    ? <><Check className="h-3 w-3" />{t('Copied', 'Скопировано')}</>
                    : <><Copy className="h-3 w-3" />{t('Copy', 'Копировать')}</>}
                </button>
                <button
                  type="button"
                  onClick={handleDownload}
                  className="inline-flex items-center gap-1.5 rounded-md border border-gray-700 bg-gray-900 px-2.5 py-1.5 text-[11px] text-gray-200 hover:border-brand-400/50 hover:text-brand-700 dark:hover:text-brand-300"
                  title={t('Download QR as SVG', 'Скачать QR в SVG')}
                >
                  <Download className="h-3 w-3" />
                  {t('SVG', 'SVG')}
                </button>
              </div>
            </>
          )}

          {!loading && !error && !finalUri && displayReason && (
            <div className="flex items-start gap-2 rounded-md border border-amber-200 dark:border-amber-900/60 bg-amber-50 dark:bg-amber-900/20 p-3 text-[12px] text-amber-800 dark:text-amber-200">
              <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
              <div>
                <div className="font-medium">
                  {t('No QR available', 'QR недоступен')}
                </div>
                <div className="text-amber-700 dark:text-amber-300/80 mt-1">{displayReason}</div>
              </div>
            </div>
          )}
        </div>
      </div>
    </ModalShell>
  )
}
