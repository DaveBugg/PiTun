/**
 * `copyToClipboard` is the single helper every "Copy" button funnels
 * through. Two paths to cover:
 *   1. Secure context → `navigator.clipboard.writeText` succeeds.
 *   2. Insecure context (or clipboard API rejects) → execCommand
 *      fallback path runs against a hidden textarea.
 *
 * The fallback path is what makes PiTun UI's copy buttons work on
 * plain HTTP LAN origins where the modern API is silently blocked.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { copyToClipboard } from '@/lib/clipboard'

describe('copyToClipboard', () => {
  let writeText: ReturnType<typeof vi.spyOn>
  let execCommand: ReturnType<typeof vi.spyOn>

  beforeEach(() => {
    writeText = vi.spyOn(navigator.clipboard, 'writeText').mockResolvedValue(undefined)
    // jsdom doesn't ship `document.execCommand` (removed when JSDOM
    // dropped legacy DOM features). Inject a stub so `vi.spyOn` has
    // something to wrap.
    if (typeof (document as any).execCommand !== 'function') {
      Object.defineProperty(document, 'execCommand', {
        value: () => false,
        configurable: true,
        writable: true,
      })
    }
    execCommand = vi.spyOn(document, 'execCommand').mockReturnValue(true)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('uses navigator.clipboard.writeText in secure contexts', async () => {
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true })
    const ok = await copyToClipboard('hello')
    expect(ok).toBe(true)
    expect(writeText).toHaveBeenCalledWith('hello')
    expect(execCommand).not.toHaveBeenCalled()
  })

  it('falls back to execCommand("copy") in insecure contexts', async () => {
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true })
    const ok = await copyToClipboard('plain http LAN')
    expect(ok).toBe(true)
    expect(writeText).not.toHaveBeenCalled()
    expect(execCommand).toHaveBeenCalledWith('copy')
  })

  it('falls back to execCommand when clipboard.writeText rejects', async () => {
    Object.defineProperty(window, 'isSecureContext', { value: true, configurable: true })
    writeText.mockRejectedValueOnce(new Error('NotAllowedError'))
    const ok = await copyToClipboard('via fallback')
    expect(ok).toBe(true)
    expect(writeText).toHaveBeenCalledOnce()
    expect(execCommand).toHaveBeenCalledWith('copy')
  })

  it('returns false for empty input without touching either API', async () => {
    const ok = await copyToClipboard('')
    expect(ok).toBe(false)
    expect(writeText).not.toHaveBeenCalled()
    expect(execCommand).not.toHaveBeenCalled()
  })

  it('cleans up the temporary textarea after the fallback runs', async () => {
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true })
    await copyToClipboard('x')
    // No stray <textarea> left in the DOM
    expect(document.querySelectorAll('textarea').length).toBe(0)
  })

  it('cleans up the textarea even when execCommand throws', async () => {
    Object.defineProperty(window, 'isSecureContext', { value: false, configurable: true })
    execCommand.mockImplementationOnce(() => { throw new Error('boom') })
    const ok = await copyToClipboard('x')
    expect(ok).toBe(false)
    expect(document.querySelectorAll('textarea').length).toBe(0)
  })
})
