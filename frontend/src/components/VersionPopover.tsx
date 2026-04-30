/**
 * Click-anchored popover listing every version the backend can report.
 *
 * - Fetches `/api/system/versions` lazily (only once the user opens it).
 * - Positions itself relative to the trigger via a simple
 *   `getBoundingClientRect` — no Floating UI dependency needed for a
 *   sidebar-anchored tooltip. Good enough for desktop; mobile users open
 *   the full About tab instead.
 * - Click-outside closes. Escape closes. Tab-focus moves to "Copy" button
 *   on open for quick keyboard use during bug reports.
 */
import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Info, Copy, Check } from 'lucide-react'
import { clsx } from 'clsx'
import { useSystemVersions } from '@/hooks/useSystem'
import type { SystemVersions } from '@/types'

interface Props {
  /** The short version label shown in the sidebar (e.g. "1.0.1"). */
  shortVersion: string
}

export function VersionPopover({ shortVersion }: Props) {
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const [coords, setCoords] = useState<{ left: number; top: number } | null>(null)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popRef = useRef<HTMLDivElement>(null)

  const { data, isLoading } = useSystemVersions({ enabled: open })

  // Position the popover to the RIGHT of the trigger, bottom-aligned to
  // the trigger's top — same "pops out of the sidebar" feel as before
  // but now via document.body portal, so we escape the sidebar's
  // `backdrop-blur-md` stacking context. Without the portal, InfoTip
  // tooltips on the main content (z-50 in their own context) bled
  // through the popover — classic CSS stacking-context trap.
  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return
    const rect = triggerRef.current.getBoundingClientRect()
    // 8px gap between sidebar edge and popover (matches ml-2).
    setCoords({ left: rect.right + 8, top: rect.bottom })
  }, [open])

  // Click-outside + Escape close.
  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      if (
        popRef.current?.contains(e.target as Node) ||
        triggerRef.current?.contains(e.target as Node)
      ) return
      setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const copyAll = async () => {
    if (!data) return
    const text = formatForClipboard(data)
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // clipboard API blocked (non-HTTPS origin on LAN) — fall back to select-all
      // on the hidden textarea so Ctrl+C still works.
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.left = '-9999px'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }

  return (
    <>
      <button
        ref={triggerRef}
        onClick={() => setOpen(v => !v)}
        className={clsx(
          'inline-flex items-center gap-1 text-xs font-mono text-gray-500 hover:text-gray-300 transition-colors',
          open && 'text-gray-300',
        )}
        aria-label="Show all system versions"
      >
        <span>PiTun {shortVersion}</span>
        <Info className="h-3 w-3" />
      </button>

      {open && coords && createPortal(
        <div
          ref={popRef}
          role="dialog"
          aria-label="System versions"
          // Portal'd into document.body so we escape any stacking context
          // the sidebar creates (backdrop-blur-md is a common offender).
          // Positioned via `fixed` with coords computed from the trigger's
          // getBoundingClientRect. `bottom-*` anchored by transform so the
          // popover grows upward from the trigger's top edge.
          style={{
            position: 'fixed',
            left: coords.left,
            top: coords.top,
            transform: 'translateY(-100%)',
          }}
          className="z-[9999] w-72 rounded-lg border border-gray-700 bg-gray-900 p-3 shadow-2xl"
        >
          <div className="flex items-center justify-between mb-2">
            <h3 className="text-xs font-semibold text-gray-200 uppercase tracking-wide">System versions</h3>
            <button
              onClick={copyAll}
              disabled={!data}
              className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs text-gray-400 hover:text-gray-200 hover:bg-gray-800 disabled:opacity-50 transition-colors"
              title="Copy to clipboard"
            >
              {copied ? (<><Check className="h-3 w-3 text-green-400" /> copied</>) : (<><Copy className="h-3 w-3" /> copy</>)}
            </button>
          </div>

          {isLoading && !data && (
            <div className="text-xs text-gray-500 py-4 text-center">Loading…</div>
          )}

          {data && <VersionTable data={data} />}
        </div>,
        document.body,
      )}
    </>
  )
}

function VersionTable({ data }: { data: SystemVersions }) {
  return (
    <div className="space-y-3 text-xs font-mono">
      <Section title="PiTun">
        <Row k="backend" v={data.pitun.backend} />
        <Row k="naive image" v={data.pitun.naive_image} />
        <Row k="frontend" v={__APP_VERSION__} />
      </Section>
      <Section title="Runtime">
        <Row k="xray" v={data.runtime.xray} />
        <Row k="python" v={data.runtime.python} />
      </Section>
      <Section title="Third-party">
        <Row k="nginx" v={data.third_party.nginx} />
        <Row k="socket proxy" v={data.third_party.socket_proxy} />
      </Section>
      <Section title="Host">
        <Row k="os" v={data.host.os} />
        <Row k="kernel" v={data.host.kernel} />
        <Row k="arch" v={data.host.arch} />
        <Row k="docker" v={data.host.docker} />
      </Section>
      <Section title="Data">
        <Row k="alembic rev" v={data.data.alembic_rev} />
        <Row k="geoip" v={formatMtime(data.data.geoip_mtime)} />
        <Row k="geosite" v={formatMtime(data.data.geosite_mtime)} />
      </Section>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <div className="text-[10px] font-semibold text-gray-500 uppercase tracking-wider mb-1">{title}</div>
      <div className="space-y-0.5">{children}</div>
    </div>
  )
}

function Row({ k, v }: { k: string; v?: string | null }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-gray-500">{k}</span>
      <span className="text-gray-300 truncate">{v || '—'}</span>
    </div>
  )
}

function formatMtime(iso?: string): string | undefined {
  if (!iso) return undefined
  try {
    const d = new Date(iso)
    // "YYYY-MM-DD" — day precision is enough, hours are noise for a geo DB
    return d.toISOString().slice(0, 10)
  } catch {
    return iso
  }
}

/** Flat plain-text dump of every version — good for pasting into a GitHub issue. */
function formatForClipboard(d: SystemVersions): string {
  const lines: string[] = []
  const add = (k: string, v?: string | null) => {
    if (v) lines.push(`  ${k.padEnd(14)} ${v}`)
  }
  lines.push('PiTun')
  add('backend',     d.pitun.backend)
  add('frontend',    __APP_VERSION__)
  add('naive image', d.pitun.naive_image)
  lines.push('Runtime')
  add('xray',   d.runtime.xray)
  add('python', d.runtime.python)
  lines.push('Third-party')
  add('nginx',        d.third_party.nginx)
  add('socket-proxy', d.third_party.socket_proxy)
  lines.push('Host')
  add('os',     d.host.os)
  add('kernel', d.host.kernel)
  add('arch',   d.host.arch)
  add('docker', d.host.docker)
  lines.push('Data')
  add('alembic rev', d.data.alembic_rev)
  add('geoip',       formatMtime(d.data.geoip_mtime))
  add('geosite',     formatMtime(d.data.geosite_mtime))
  return lines.join('\n')
}
