/**
 * AI Workforce operator ledger helpers — pure, dependency-free.
 * Tested by tests/workforce_ledger.test.mjs (node --test) and
 * tests/test_ai_workforce_operator_ledger.py.
 *
 * Nothing here invents state: an unrecognised state is "Unknown", an
 * unavailable fact renders as "Unavailable" (never 0 / passed), and "Done" is
 * claimed only when every delivery phase is evidenced as passed.
 */

export const LEDGER_STATES = ['queued', 'running', 'blocked', 'review_required', 'completed', 'failed', 'cancelled']
const LABELS = {
  queued: 'Queued', running: 'Running', blocked: 'Blocked',
  review_required: 'Review needed', completed: 'Completed',
  failed: 'Failed', cancelled: 'Cancelled',
}
const PHASE_LABELS = {
  source_complete: 'Source complete', tests_complete: 'Tests complete',
  deployed: 'Deployed', live_verified: 'Live verified',
}

export const stateLabel = s => LABELS[s] || 'Unknown'

/** Display value for a fact: unavailable / missing is never rendered as a value. */
export function factText (fact) {
  if (fact == null || fact.status === 'unavailable') return 'Unavailable'
  if (fact.status === 'passed') return 'Passed'
  if (typeof fact === 'string' && fact) return fact
  return 'Unavailable'
}

/** Four separate phase rows; "done" only when all four passed. */
export function phaseRows (entry) {
  const phases = (entry && entry.phases) || {}
  const rows = Object.keys(PHASE_LABELS).map(k => ({
    key: k, label: PHASE_LABELS[k], text: factText(phases[k]),
    passed: !!phases[k] && phases[k].status === 'passed',
  }))
  return { rows, done: rows.every(r => r.passed) }
}

/** The four sections the page shows; every entry lands in at most one. */
export function sections (items) {
  const list = Array.isArray(items) ? items : []
  const by = (...s) => list.filter(i => s.includes(i.state))
  return {
    active: by('running', 'queued', 'blocked'),
    review: by('review_required'),
    recent: by('completed', 'failed', 'cancelled'),
    unknown: list.filter(i => !LEDGER_STATES.includes(i.state)),
  }
}

/** Client-side filter; facets are computed from the unfiltered list. */
export function filterItems (items, { state = '', kind = '', employeeId = '' } = {}) {
  return (items || []).filter(i =>
    (!state || i.state === state) && (!kind || i.kind === kind) &&
    (!employeeId || (i.worker && i.worker.employee_id === employeeId)))
}

/** A review decision is offered only when the server said it is available. */
export function canDecide (entry) {
  return !!(entry && entry.kind === 'work_item' && entry.version &&
    entry.decisions && entry.decisions.review && entry.decisions.review.available)
}

/** Request body for a review decision; null when it must not be sent. */
export function reviewPayload (entry, decision, note) {
  if (!canDecide(entry) || !decision) return null
  return { decision, note: note || undefined, expected_version: entry.version }
}

/** Success is claimed only for a 2xx with a state echoed back. */
export function reviewOutcome (resp) {
  if (resp && typeof resp.state === 'string') {
    return { ok: true, replayed: !!resp.replayed, state: resp.state }
  }
  return { ok: false, replayed: false, state: null }
}
