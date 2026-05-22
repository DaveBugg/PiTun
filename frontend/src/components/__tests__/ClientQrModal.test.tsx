/**
 * ClientQrModal: shows a share URI as QR + lets the operator copy it.
 *
 * The copy button used to silently fail on plain-HTTP LAN origins
 * (where `navigator.clipboard` is gated behind `isSecureContext`).
 * Coverage here verifies the button is wired to `copyToClipboard` and
 * that the UI flips to "Copied" on success — the helper itself has
 * unit coverage in `lib/__tests__/clipboard.test.ts` that exercises
 * both the secure-context and insecure-fallback branches.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

// Stub the helper at module level so this test doesn't have to deal
// with jsdom's quirky `isSecureContext` / `document.execCommand`
// behaviour — that's already covered in the lib unit test.
vi.mock('@/lib/clipboard', () => ({
  copyToClipboard: vi.fn(),
}))

import { ClientQrModal } from '@/components/ClientQrModal'
import { copyToClipboard } from '@/lib/clipboard'

describe('<ClientQrModal>', () => {
  beforeEach(() => {
    vi.mocked(copyToClipboard).mockReset()
  })

  it('routes copy button clicks through copyToClipboard helper', async () => {
    vi.mocked(copyToClipboard).mockResolvedValue(true)
    const user = userEvent.setup()
    const uri = 'vless://abc@host:443?type=tcp#test'

    render(<ClientQrModal open onClose={() => {}} title="Test" uri={uri} />)

    // Anchor on URI input rendering so we know `finalUri` is bound on
    // the click handler before we trigger it.
    await screen.findByDisplayValue(uri)
    await user.click(screen.getByRole('button', { name: /(copy|копировать)/i }))

    await waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith(uri))
    // UI flips to "Copied" / "Скопировано"
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /(copied|скопировано)/i })).toBeInTheDocument()
    })
  })

  it('does NOT flip to Copied when the helper reports failure', async () => {
    // If the helper returns false (both clipboard API and execCommand
    // fallback failed), the UI must NOT show a misleading success
    // indicator — the user needs to know to copy manually.
    vi.mocked(copyToClipboard).mockResolvedValue(false)
    const user = userEvent.setup()
    const uri = 'vless://abc@host:443?type=tcp#test'

    render(<ClientQrModal open onClose={() => {}} title="Test" uri={uri} />)
    await screen.findByDisplayValue(uri)
    await user.click(screen.getByRole('button', { name: /(copy|копировать)/i }))

    await waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith(uri))
    // Stays on "Copy" — never flipped to "Copied"
    expect(screen.queryByRole('button', { name: /(copied|скопировано)/i })).toBeNull()
  })

  it('does not render a Copy button when there is no URI', async () => {
    // `uri={null}` is the explicit-no-URI branch ("no_uri" reason) —
    // the modal renders only the reason text, no QR / no copy button.
    render(<ClientQrModal open onClose={() => {}} title="Test" uri={null} />)

    expect(screen.queryByRole('button', { name: /(copy|копировать)/i })).toBeNull()
    expect(copyToClipboard).not.toHaveBeenCalled()
  })
})
