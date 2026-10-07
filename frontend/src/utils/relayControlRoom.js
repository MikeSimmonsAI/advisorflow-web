// Pure helpers for the Relay Control Room. No React, no DOM: node-testable.

export const POLL_MS = 20000
export const LEASE_MINUTES = 30

let _seq = 0
// A unique query string per fetch so no proxy/browser cache can serve old state.
export function stateUrl(now = Date.now()) {
  _seq += 1
  return `/god/relay/state?_=${now}-${_seq}`
}

// The state the page shows when a fetch fails: never a stale active worker.
export function failedState(message) {
  return {
    available: false,
    reason: message || 'Relay status could not be loaded',
    worker: { state: 'unavailable', display: 'Relay status unavailable' },
    queued_behind: [],
    history: [],
    completed_today: 0,
    suggested_next: '',
  }
}

// Only an available payload with an active worker may be called Working.
export function isWorking(state) {
  const w = state && state.available ? state.worker : null
  return !!w && w.state === 'active' && w.display === 'Working'
}

export function headline(state) {
  if (!state || !state.available) return 'Relay status unavailable'
  const w = state.worker || {}
  if (w.state === 'idle') return w.display || 'Idle - no active Claude worker'
  return w.display || 'Unknown'
}

// Live elapsed: server minutes at generation time plus time since we received it.
export function liveElapsedMinutes(worker, generatedAt, nowMs = Date.now()) {
  if (!worker || worker.elapsed_min == null) return null
  const drift = generatedAt ? Math.max(0, Math.floor((nowMs - Date.parse(generatedAt)) / 60000)) : 0
  return worker.elapsed_min + (worker.state === 'active' || worker.state === 'queued' ? drift : 0)
}

export function formatCT(iso) {
  if (!iso) return 'unavailable'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return 'unavailable'
  return d.toLocaleString('en-US', { timeZone: 'America/Chicago', hour12: false,
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) + ' CT'
}

export function formatMinutes(m) {
  if (m == null) return 'unavailable'
  if (m < 60) return `${m} min`
  return `${Math.floor(m / 60)} h ${m % 60} min`
}

export function leaseText(worker) {
  const el = worker && worker.elapsed_min
  if (el == null) return `${LEASE_MINUTES}-minute lease`
  return `${LEASE_MINUTES}-minute lease (${Math.max(0, LEASE_MINUTES - el)} min left)`
}

export function actionsRunText(worker) {
  return worker && worker.actions_run_id ? String(worker.actions_run_id) : 'not mapped'
}
