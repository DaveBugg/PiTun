// Shared "hide unreachable nodes" preference — one key drives the Nodes
// page quick filter and the chain-via picker in NodeForm.
export const HIDE_UNAVAILABLE_KEY = 'pitun.nodesHideUnavailable'

export function loadHideUnavailable(): boolean {
  try {
    return localStorage.getItem(HIDE_UNAVAILABLE_KEY) === '1'
  } catch {
    return false
  }
}

export function saveHideUnavailable(v: boolean): void {
  try {
    localStorage.setItem(HIDE_UNAVAILABLE_KEY, v ? '1' : '0')
  } catch {
    /* quota / private mode */
  }
}
