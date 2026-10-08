// Pure helpers for the Relay Control Room. No React, no DOM: node-testable.

export const POLL_MS = 20000
export const LEASE_MINUTES = 30

let _seq = 0
// A unique query string per fetch so no proxy/browser cache can serve old state.
export function stateUrl(now = Date.now()) {
  _seq += 1
  // SCI staging: /god/relay/state is the older relay snapshot; the evidence-only
  // worker state lives at /god/relay/worker (same scripts/relay/relay_state.py contract).
  return `/god/relay/worker?_=${now}-${_seq}`
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

// A 200 is not proof of a usable state: a proxy can return an HTML page, `null`, or a
// payload missing its worker. Anything that is not a well-formed state is a FAILURE,
// never a success, so the page cannot crash or show a banner built from garbage.
export function normalizeState(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return failedState('Relay status response was not a valid state')
  }
  if (raw.available === false) return failedState(raw.reason)
  const w = raw.worker
  if (raw.available !== true || !w || typeof w !== 'object' || typeof w.state !== 'string') {
    return failedState('Relay status response was incomplete')
  }
  return {
    ...raw,
    queued_behind: Array.isArray(raw.queued_behind) ? raw.queued_behind : [],
    history: Array.isArray(raw.history) ? raw.history : [],
    completed_today: Number.isFinite(raw.completed_today) ? raw.completed_today : 0,
    suggested_next: typeof raw.suggested_next === 'string' ? raw.suggested_next : '',
  }
}

// How old the last SUCCESSFUL check is. Failed attempts never refresh this.
export const STALE_AFTER_MS = POLL_MS * 3
export function isCheckStale(lastOkMs, nowMs = Date.now()) {
  return lastOkMs == null || nowMs - lastOkMs > STALE_AFTER_MS
}

// UP NEXT: only work the relay itself reports as queued (a directive posted, not yet
// accepted). Nothing is invented: no queue records means an empty list, said plainly.
// Owner is unassigned until a worker accepts it; a posted directive auto-starts.
export function upNext(state) {
  if (!state || !state.available) return []
  const cards = []
  if (state.worker && state.worker.state === 'queued') cards.push(state.worker)
  for (const q of state.queued_behind || []) if (q && q.state === 'queued') cards.push(q)
  return cards.map(c => ({
    relay_run_id: c.relay_run_id, project: c.project || '', status: c.display || 'Queued',
    owner: c.owner || 'unassigned (set when a worker accepts)',
    last_update_at: c.last_update_at || null,
    start: c.start_mode === 'approval' ? 'needs approval' : 'auto-start',
    stale: c.health === 'STALE',
  }))
}

// Actionable blockers: terminal BLOCKED/APPROVAL_REQUIRED runs and stuck cards, each with
// the reason the relay reported. Kept apart from recommendations.
export function blockers(state) {
  if (!state || !state.available) return []
  const out = []
  const w = state.worker || {}
  if (w.health === 'STALE/HUNG' || w.health === 'STALE' || w.state === 'mismatch') {
    out.push({ relay_run_id: w.relay_run_id, kind: w.health || w.state, reason: w.health_detail || w.health || '' })
  }
  for (const h of state.history || []) {
    if (h.state === 'terminal' && (h.result === 'BLOCKED' || h.result === 'APPROVAL_REQUIRED')) {
      out.push({ relay_run_id: h.relay_run_id, kind: h.result, reason: h.blocked_reason || 'no reason reported' })
    } else if (h.state === 'mismatch') {
      out.push({ relay_run_id: h.relay_run_id, kind: 'RELAY_STATE_MISMATCH', reason: h.health || '' })
    }
  }
  return out
}

// A recommendation is advice from a finished/active run. It is NOT queued work.
export function recommendation(state) {
  return state && state.available && typeof state.suggested_next === 'string' ? state.suggested_next : ''
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

// Lease status from the server-provided expiry, evaluated against the live clock so the
// card flips to STALE/HUNG even between polls. Terminal cards never go stale.
export function leaseStatus(worker, nowMs = Date.now()) {
  if (!worker || !worker.lease_expires_at || (worker.state !== 'active' && worker.state !== 'queued')) {
    return { expired: false, minutesLeft: null }
  }
  const left = Math.ceil((Date.parse(worker.lease_expires_at) - nowMs) / 60000)
  return { expired: left <= 0, minutesLeft: Math.max(0, left) }
}

// A card still claiming Working whose lease has lapsed on the live clock must say STALE/HUNG.
export function isLapsed(worker, nowMs = Date.now()) {
  return !!worker && worker.state === 'active' && worker.display === 'Working' && leaseStatus(worker, nowMs).expired
}

export const FETCH_TIMEOUT_MS = 15000

// Poll + manual refresh controller (injected fetch/timers, so node-testable).
// Every call performs a NEW request (unique URL, never joins an older one); a manual
// refresh is never blocked by a poll in flight or a hung request; only the newest request
// may write state, so a slow older reply cannot overwrite a newer one.
export function createRefresher({ fetchState, onLoading, onState, onError, timeoutMs = FETCH_TIMEOUT_MS,
  setTimer = setTimeout, clearTimer = clearTimeout }) {
  let latest = 0
  let newestGenerated = 0   // generated_at (ms) of the newest state accepted so far
  return async function refresh(manual = false) {
    const mine = ++latest
    onLoading(true, manual)
    let timer
    const timeout = new Promise((_, rej) => {
      timer = setTimer(() => rej(new Error('Timed out waiting for relay status')), timeoutMs)
    })
    try {
      const s = normalizeState(await Promise.race([fetchState(stateUrl(), { cache: 'no-store' }), timeout]))
      if (mine === latest) {
        const gen = s.available ? Date.parse(s.generated_at) : NaN
        if (!s.available) onError(s)
        else if (Number.isFinite(gen) && gen < newestGenerated) {
          onError(failedState('Relay returned a status older than one already received'))
        } else {
          if (Number.isFinite(gen)) newestGenerated = gen
          onState(s)
        }
      }
    } catch (e) {
      if (mine === latest) onError(failedState(e && e.message))
    } finally {
      clearTimer(timer)
      if (mine === latest) onLoading(false, manual)
    }
  }
}

export function actionsRunText(worker) {
  return worker && worker.actions_run_id ? String(worker.actions_run_id) : 'not mapped'
}

// The CT calendar day (YYYY-MM-DD) of an ISO time, or '' when unknown.
export function ctDay(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleDateString('en-CA', { timeZone: 'America/Chicago' })
}

// Today's relay run OUTCOMES, kept apart. A COMPLETED run means the run reported
// completion (source/tests) - it is NOT a verified release. Blocked and approval
// runs are counted separately and never added to "completed". Superseded/mismatch
// cards are neither.
export function runOutcomesToday(state, nowMs = Date.now()) {
  const out = { completed: 0, blocked: 0, approval: 0, other: 0 }
  if (!state || !state.available) return out
  const today = ctDay(new Date(nowMs).toISOString())
  for (const h of state.history || []) {
    if (!h || ctDay(h.last_update_at) !== today) continue
    if (h.state === 'terminal' && h.result === 'COMPLETED') out.completed += 1
    else if (h.state === 'terminal' && h.result === 'BLOCKED') out.blocked += 1
    else if (h.state === 'terminal' && h.result === 'APPROVAL_REQUIRED') out.approval += 1
    else out.other += 1
  }
  return out
}

// The most recent finished run - shown apart from the live worker so "last completed"
// can never be read as "working now".
export function lastFinished(state) {
  if (!state || !state.available) return null
  return (state.history || []).find(h => h && h.state === 'terminal') || null
}
