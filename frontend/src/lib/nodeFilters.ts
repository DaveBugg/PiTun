// Shared "hide unreachable nodes" preference — one key drives the Nodes
// page quick filter and the node pickers (chain-via, endpoints).
// Pickers default to hiding; the Nodes page defaults to showing.
export const HIDE_UNAVAILABLE_KEY = 'pitun.nodesHideUnavailable'

export function loadHideUnavailable(defaultValue = false): boolean {
  try {
    const raw = localStorage.getItem(HIDE_UNAVAILABLE_KEY)
    if (raw === null) return defaultValue
    return raw === '1'
  } catch {
    return defaultValue
  }
}

export function saveHideUnavailable(v: boolean): void {
  try {
    localStorage.setItem(HIDE_UNAVAILABLE_KEY, v ? '1' : '0')
  } catch {
    /* quota / private mode */
  }
}
