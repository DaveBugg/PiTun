/**
 * VersionPopover: renders a sidebar trigger that opens a popover with a
 * full version dump from /api/system/versions. Key behaviours:
 *   - lazy-fetches (no network call until the user clicks)
 *   - click-outside closes
 *   - Copy button writes a formatted dump to the clipboard
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('@/api/client', () => ({
  systemApi: {
    versions: vi.fn(),
  },
}))

import { VersionPopover } from '@/components/VersionPopover'
import { systemApi } from '@/api/client'

// jsdom ships a read-only Clipboard stub on `navigator.clipboard` (can't
// replace the whole object with defineProperty). Instead we spyOn the
// `writeText` method each test — `restoreMocks: true` in vitest.config
// takes care of cleanup.
let clipboardWrite: ReturnType<typeof vi.spyOn>

function wrap(ui: ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
}

const FIXTURE = {
  pitun: { backend: '1.0.1', naive_image: '1.0.1' },
  runtime: { xray: '26.3.27', python: '3.11.14' },
  third_party: { nginx: 'nginx:1.25-alpine', socket_proxy: undefined },
  host: {
    kernel: '6.12.47+rpt-rpi-v8',
    os: 'Debian GNU/Linux 12 (bookworm)',
    docker: '27.3.1',
    arch: 'aarch64',
  },
  data: {
    alembic_rev: '007_add_devices',
    geoip_mtime: '2026-04-18T12:33:00+00:00',
    geosite_mtime: undefined,
  },
}

describe('<VersionPopover>', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(systemApi.versions).mockResolvedValue(FIXTURE)
    clipboardWrite = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined)
  })

  it('renders a trigger showing the short version', () => {
    render(wrap(<VersionPopover shortVersion="1.0.1" />))
    expect(screen.getByRole('button', { name: /show all system versions/i })).toHaveTextContent('PiTun 1.0.1')
  })

  it('does NOT fetch until clicked (lazy)', async () => {
    render(wrap(<VersionPopover shortVersion="1.0.1" />))
    // Give React Query a moment — shouldn't fire yet
    await new Promise(r => setTimeout(r, 20))
    expect(systemApi.versions).not.toHaveBeenCalled()
  })

  it('opens on click, fetches, and renders the full snapshot', async () => {
    const user = userEvent.setup()
    render(wrap(<VersionPopover shortVersion="1.0.1" />))

    await user.click(screen.getByRole('button', { name: /show all system versions/i }))
    expect(systemApi.versions).toHaveBeenCalledTimes(1)

    // Values from the fixture appear
    await waitFor(() => {
      expect(screen.getByText('26.3.27')).toBeInTheDocument()
    })
    expect(screen.getByText('Debian GNU/Linux 12 (bookworm)')).toBeInTheDocument()
    expect(screen.getByText('007_add_devices')).toBeInTheDocument()
    // Missing fields render as em-dash
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThan(0)   // socket_proxy + geosite_mtime
  })

  it('copies a formatted dump to the clipboard when Copy is clicked', async () => {
    const user = userEvent.setup()
    render(wrap(<VersionPopover shortVersion="1.0.1" />))

    await user.click(screen.getByRole('button', { name: /show all system versions/i }))
    await waitFor(() => expect(screen.getByText('26.3.27')).toBeInTheDocument())
    await user.click(screen.getByTitle(/copy to clipboard/i))

    expect(clipboardWrite).toHaveBeenCalledOnce()
    const text = clipboardWrite.mock.calls[0][0]
    expect(text).toContain('backend')
    expect(text).toContain('1.0.1')
    expect(text).toContain('xray')
    expect(text).toContain('26.3.27')
  })
})
