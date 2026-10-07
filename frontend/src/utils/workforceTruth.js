/**
 * AI Workforce operator truth helpers — pure, dependency-free.
 *
 * Nothing here invents state. A lifecycle label is shown only for a state the
 * server evidenced; anything else is "Unknown". Tested by
 * tests/workforce_truth.test.mjs (node --test) and
 * tests/test_ai_workforce_truth_recovery.py (stdlib unittest).
 */

const LIFECYCLE = {
  queued: 'Queued', accepted: 'Accepted', running: 'Running', working: 'Running',
  paused: 'Paused', blocked: 'Blocked', completed: 'Completed',
  failed: 'Failed', cancelled: 'Cancelled', canceled: 'Cancelled',
  skipped: 'Skipped', stale: 'Stale / hung', hung: 'Stale / hung',
}
const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'canceled', 'skipped'])
const ACTIVE = new Set(['queued', 'accepted', 'running', 'working'])

export function normalizeState (raw) {
  const k = typeof raw === 'string' ? raw.trim().toLowerCase() : ''
  return LIFECYCLE[k] ? k : 'unknown'
}
export const isTerminal = raw => TERMINAL.has(normalizeState(raw))
export const isActive = raw => ACTIVE.has(normalizeState(raw))

/** Label for a state; unevidenced states are "Unknown", never "Idle"/"Working". */
export function lifecycleLabel (raw) {
  return LIFECYCLE[normalizeState(raw)] || 'Unknown'
}

/** Only a finite number the server sent is a percentage; never computed here. */
export function evidencedPercent (v) {
  return typeof v === 'number' && Number.isFinite(v) && v >= 0 && v <= 100 ? v : null
}

/**
 * Split runs into the one current active run and history.
 * Newest trusted active run wins; terminal and superseded runs go to history.
 */
export function selectActiveRun (runs) {
  const list = Array.isArray(runs) ? runs.filter(r => r && r.id != null) : []
  const ts = r => {
    const t = Date.parse(r.updated_at || r.started_at || '')
    return Number.isNaN(t) ? 0 : t
  }
  const active = list.filter(r => isActive(r.state) && !r.superseded_by)
    .sort((a, b) => ts(b) - ts(a))
  const current = active[0] || null
  const history = list.filter(r => r !== current).sort((a, b) => ts(b) - ts(a))
  return { current, history }
}

/** Display state for a worker: an active run beats any stale "idle" claim. */
export function workerDisplayState (worker, current) {
  if (current && isActive(current.state)) return normalizeState(current.state)
  return normalizeState(worker && worker.state)
}

/** Latest-wins sequencer: only the newest started request may apply. */
export function createSequencer () {
  let latest = 0
  return {
    begin () { latest += 1; return latest },
    isCurrent (token) { return token === latest },
  }
}

/** Synchronous same-action lock; other actions stay usable. */
export function createActionGuard () {
  const held = new Set()
  return {
    tryAcquire (key) { if (held.has(key)) return false; held.add(key); return true },
    release (key) { held.delete(key) },
    isHeld (key) { return held.has(key) },
  }
}

/** Failed refresh keeps last-good data and flags it possibly out of date. */
export function applyRefresh (prev, outcome) {
  if (outcome && outcome.ok) {
    return { data: outcome.data, stale: false, error: '', lastGoodAt: outcome.at || null }
  }
  const p = prev || { data: null, lastGoodAt: null }
  return {
    data: p.data,
    stale: p.data != null,
    lastGoodAt: p.lastGoodAt || null,
    error: (outcome && outcome.message) || 'Could not refresh.',
  }
}

/** Support code is shown only when the server supplied one. */
export function supportCode (err) {
  const c = err && (err.support_code || (err.detail && err.detail.support_code))
  return typeof c === 'string' && c.trim() ? c.trim() : null
}

const EVIDENCE = {
  committed: 'Committed code', tested: 'Executed tests', deployed: 'Deployment proof',
  blocked: 'Blocked', run_record: 'Persisted run record',
  unverified: 'Unverified claim',
}
/** Evidence kinds rendered distinctly; anything else is an unverified claim. */
export function evidenceLabel (kind) {
  return EVIDENCE[typeof kind === 'string' ? kind.toLowerCase() : ''] || EVIDENCE.unverified
}

const SECRET_RE = /(sk|ghp|gho|pk|rk|xox[bap])[-_][A-Za-z0-9_-]{8,}|bearer\s+[A-Za-z0-9._-]{12,}|authorization\s*[:=]\s*(?:bearer\s+)?\S+|(api[_-]?key|secret|token|password)\s*[:=]\s*\S+/gi
/** Redact credential-shaped text before it is rendered. */
export function redact (text) {
  return typeof text === 'string' ? text.replace(SECRET_RE, '[redacted]') : ''
}

/** Run-evidence polling cadence. "Live" is only claimed while fresh. */
export const POLL_INTERVAL_MS = 20000
export const POLL_STALE_AFTER_MS = POLL_INTERVAL_MS * 3

/** lastOkAt = ms timestamp of the last SUCCESSFUL refresh (null = never). */
export function pollFreshness (lastOkAt, nowMs = Date.now()) {
  if (typeof lastOkAt !== 'number' || Number.isNaN(lastOkAt)) return { live: false, ageSeconds: null }
  const age = Math.max(0, nowMs - lastOkAt)
  return { live: age <= POLL_STALE_AFTER_MS, ageSeconds: Math.floor(age / 1000) }
}

/** Heartbeat age in whole seconds, or null when none was recorded. */
export function heartbeatAgeSeconds (iso, nowMs = Date.now()) {
  const t = Date.parse(iso || '')
  return Number.isNaN(t) ? null : Math.max(0, Math.floor((nowMs - t) / 1000))
}

/** Overlap guard: tryStart() is false while a refresh is in flight. */
export function createRefreshGate () {
  let busy = false
  return {
    tryStart () { if (busy) return false; busy = true; return true },
    done () { busy = false },
    get busy () { return busy },
  }
}

/**
 * The dark-launch line. "Nothing can reach anybody" is a claim about zero, so
 * it needs a server-evidenced zero: a missing/failed/non-numeric count is
 * Unknown, never a green zero.
 */
export function darkLaunchStatus (dark) {
  const n = dark && dark.employees_that_could_execute
  if (typeof n !== 'number' || !Number.isFinite(n) || n < 0) {
    return { known: false, couldExecute: null, tone: 'off', label: 'Unknown — status not loaded' }
  }
  return n === 0
    ? { known: true, couldExecute: 0, tone: 'ok', label: 'Dark — nothing can reach anybody' }
    : { known: true, couldExecute: n, tone: 'warn', label: `${n} could reach people` }
}
