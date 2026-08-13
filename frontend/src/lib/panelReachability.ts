/**
 * Notice when the panel has stopped answering at THIS address, and remember
 * where else it can be reached.
 *
 * Switching to router mode makes the WAN port refuse new connections — that is
 * the point of it. But whoever flipped the switch is usually looking at the
 * panel on that very address, and the browser keeps the static bundle in its
 * HTTP cache, so the page carries on rendering while every request dies with
 * an empty response. The result is a working-looking panel emitting a stream
 * of console errors and no explanation anywhere on screen.
 *
 * So: while things work, remember the LAN address the box says it serves; when
 * requests start failing at the transport level, say where to go instead.
 * Only transport failures count — an HTTP error means the box answered, which
 * is a different problem entirely.
 */

const LAN_KEY = 'pitun_lan_panel_url'
const FAILURES_BEFORE_ALARM = 3

let consecutiveFailures = 0
const listeners = new Set<(unreachable: boolean) => void>()

function emit(unreachable: boolean) {
  for (const fn of listeners) fn(unreachable)
}

export function subscribeReachability(fn: (unreachable: boolean) => void) {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

/** Remember an address the panel is also reachable at (the LAN side). */
export function rememberLanPanel(ipv4: string | null | undefined) {
  if (!ipv4) return
  const url = `http://${ipv4}`
  if (url !== window.location.origin) localStorage.setItem(LAN_KEY, url)
}

export function lanPanelUrl(): string | null {
  const url = localStorage.getItem(LAN_KEY)
  return url && url !== window.location.origin ? url : null
}

export function noteRequestOutcome(reachedServer: boolean) {
  if (reachedServer) {
    if (consecutiveFailures > 0) {
      consecutiveFailures = 0
      emit(false)
    }
    return
  }
  consecutiveFailures += 1
  // One failed request is a blip — a reload mid-flight, a dropped wifi frame.
  // A run of them with nothing in between is the address going dark.
  if (consecutiveFailures === FAILURES_BEFORE_ALARM) emit(true)
}
