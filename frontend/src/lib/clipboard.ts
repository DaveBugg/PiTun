// Clipboard helper that works in both secure and insecure contexts.
//
// `navigator.clipboard.writeText` is the modern way to copy, but it
// requires a **secure context** (HTTPS or localhost). The PiTun UI is
// typically accessed over plain HTTP on a LAN IP
// (`http://192.168.x.x/`), where `window.isSecureContext === false` and
// the call silently rejects with `NotAllowedError`. We fall back to the
// deprecated-but-still-universal `execCommand('copy')` path in that
// case so the "Copy" buttons across the app actually work.
//
// Returns `true` if the copy succeeded, `false` otherwise. Callers are
// expected to surface success / failure to the user (e.g. flip a
// "Copied" indicator).
export async function copyToClipboard(text: string): Promise<boolean> {
  if (!text) return false

  // Primary path: secure context + Clipboard API.
  if (window.isSecureContext && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // fall through to execCommand fallback
    }
  }

  // Fallback for insecure HTTP contexts. Position offscreen so the
  // selection flash doesn't reflow layout or steal visible focus.
  const ta = document.createElement('textarea')
  ta.value = text
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.top = '0'
  ta.style.left = '0'
  ta.style.opacity = '0'
  ta.style.pointerEvents = 'none'
  document.body.appendChild(ta)
  try {
    ta.focus()
    ta.select()
    ta.setSelectionRange(0, text.length)
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(ta)
  }
}
