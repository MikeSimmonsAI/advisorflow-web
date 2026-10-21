/**
 * Simple Sales Workspace — the plain rules behind the six redesigned screens
 * (Lane F, issue #281). No React and no network here, so every rule below is
 * unit-tested directly (frontend/tests/salesSimple.test.mjs).
 *
 * NOTHING HERE CHANGES WHAT A STAGE MEANS. The simple four-column pipeline is
 * a way of LOOKING at the real lifecycle stages from app/models/sales_models.py
 * — every deal keeps its real stage, the real stage is always shown on the
 * card, and changing it still goes through PATCH /sales/opportunities/{id}.
 */

// ── the simple pipeline ────────────────────────────────────────────────────

// Real stages, in lifecycle order (sales_models.OPPORTUNITY_STAGES).
export const REAL_STAGES = [
  'prospect', 'contacted', 'discovery', 'demo_build', 'demo_proposal',
  'closing', 'won', 'onboarding', 'live',
]
export const LOST_STAGE = 'lost'

export const STAGE_LABELS = {
  prospect: 'Prospect',
  contacted: 'Contacted / Qualified',
  discovery: 'Discovery',
  demo_build: 'Demo Build',
  demo_proposal: 'Demo / Proposal',
  closing: 'Closing',
  won: 'Won',
  onboarding: 'Onboarding',
  live: 'Live / Completed',
  lost: 'Lost',
}

// The four columns the approved design shows. Every real stage belongs to
// exactly one of them; `lost` and any stage this file has never heard of are
// kept in their own buckets so no deal can fall off the board.
export const SIMPLE_COLUMNS = [
  { key: 'new',    label: 'New Leads',      stages: ['prospect'],
    hint: 'Not reached yet' },
  { key: 'follow', label: 'Follow Up',      stages: ['contacted', 'discovery'],
    hint: 'Talking, learning what they need' },
  { key: 'demo',   label: 'Demo Scheduled', stages: ['demo_build', 'demo_proposal', 'closing'],
    hint: 'Demo, proposal or closing' },
  { key: 'won',    label: 'Won',            stages: ['won', 'onboarding', 'live'],
    hint: 'Sold — onboarding takes over' },
]

/** 'new' | 'follow' | 'demo' | 'won' | 'lost' | 'other' */
export function simpleColumnFor(stage) {
  if (stage === LOST_STAGE) return 'lost'
  const col = SIMPLE_COLUMNS.find(c => c.stages.includes(stage))
  return col ? col.key : 'other'
}

/** The stage a deal lands on when moved INTO a column (the column's first). */
export function entryStageFor(columnKey) {
  const col = SIMPLE_COLUMNS.find(c => c.key === columnKey)
  return col ? col.stages[0] : null
}

/**
 * Flatten the /sales/opportunities response ({stages:[{opportunities}], lost})
 * into the simple columns. Returns {new, follow, demo, won, lost, other}.
 * Deals are never dropped: an unknown stage goes to `other`.
 */
export function groupForSimpleBoard(resp) {
  const out = { new: [], follow: [], demo: [], won: [], lost: [], other: [] }
  const seen = new Set()
  const add = o => {
    if (!o || seen.has(o.id)) return
    seen.add(o.id)
    out[simpleColumnFor(o.stage)].push(o)
  }
  ;(resp?.stages || []).forEach(s => (s.opportunities || []).forEach(add))
  ;(resp?.lost || []).forEach(add)
  return out
}

// ── call / text ────────────────────────────────────────────────────────────
//
// Calls and texts go through the rep's own phone (tel: / sms:), exactly as the
// existing calendar card does. No provider API is called from these screens,
// so nothing can be sent to a customer by a test or by accident.

export function cleanPhone(phone) {
  if (!phone) return ''
  const s = String(phone).trim()
  const plus = s.startsWith('+') ? '+' : ''
  const digits = s.replace(/[^\d]/g, '')
  return digits.length >= 7 ? plus + digits : ''
}

export function telHref(phone) {
  const p = cleanPhone(phone)
  return p ? 'tel:' + p : null
}

export function smsHref(phone) {
  const p = cleanPhone(phone)
  return p ? 'sms:' + p : null
}

// ── "What happened?" after a call ──────────────────────────────────────────

export const CALL_OUTCOMES = [
  { key: 'connected',      label: 'Connected',      tone: 'blue',
    next: 'Follow up', days: 2 },
  { key: 'no_answer',      label: 'No Answer',      tone: 'gray',
    next: 'Try calling again', days: 1 },
  { key: 'voicemail',      label: 'Left Voicemail', tone: 'gray',
    next: 'Call back', days: 2 },
  { key: 'interested',     label: 'Interested',     tone: 'green',
    next: 'Book a demo', days: 1 },
  { key: 'not_interested', label: 'Not Interested', tone: 'red',
    next: 'Check back in', days: 90 },
  { key: 'book_meeting',   label: 'Book a Meeting', tone: 'teal',
    next: 'Demo booked — prepare', days: 1, books: true },
]

export function outcomeByKey(key) {
  return CALL_OUTCOMES.find(o => o.key === key) || null
}

/**
 * A real conversation on a deal nobody had reached yet moves it from
 * Prospect to Contacted — the same step the existing stage picker offers.
 * Anything further along is left exactly where it is.
 */
export function stageAfterOutcome(currentStage, outcomeKey) {
  const talked = ['connected', 'interested', 'book_meeting']
  if (currentStage === 'prospect' && talked.includes(outcomeKey)) return 'contacted'
  return null
}

/** YYYY-MM-DD `days` from `now`, in the viewer's calendar. */
export function ymdPlusDays(days, now = new Date()) {
  const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() + (Number(days) || 0))
  const p = x => String(x).padStart(2, '0')
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
}

/**
 * A date-only due is stored on the 12:00 UTC anchor — the convention
 * NewProspect already uses and parts.isDateAnchor reads back as a calendar
 * date, so "due Oct 22" never shows as overdue on the evening of Oct 21.
 */
export function dueIsoFromYmd(ymd) {
  return /^\d{4}-\d{2}-\d{2}$/.test(ymd || '') ? ymd + 'T12:00:00' : null
}

/**
 * The two existing calls a recorded outcome makes:
 *   1. POST /sales/opportunities/{id}/notes   (timeline entry, event_type call_outcome)
 *   2. PATCH /sales/opportunities/{id}       (next action + due, and the
 *      Prospect → Contacted step when a conversation actually happened)
 * Returns {note, patch}. `patch` is null when nothing about the deal changes.
 */
export function buildOutcomeRequests({ stage, outcome, note, nextAction, dueYmd }) {
  const o = outcomeByKey(outcome)
  if (!o) throw new Error('Pick what happened on the call.')
  const text = (note || '').trim()
  const summary = ('Call: ' + o.label + (text ? ' — ' + text : '')).slice(0, 250)
  // The summary is capped at 250 by the API; a longer note keeps its full text
  // in `detail` so nothing the rep typed is lost.
  const body = {
    event_type: 'call_outcome',
    summary,
    detail: summary.length < ('Call: ' + o.label + (text ? ' — ' + text : '')).length ? text : null,
  }
  const patch = {}
  const next = (nextAction || '').trim()
  if (next) patch.next_action = next
  const due = dueIsoFromYmd(dueYmd)
  if (due) patch.next_action_due_at = due
  const moved = stageAfterOutcome(stage, outcome)
  if (moved) patch.stage = moved
  return { note: body, patch: Object.keys(patch).length ? patch : null }
}

// ── follow-up list filters ─────────────────────────────────────────────────

/**
 * Bucket a due date as 'overdue' | 'today' | 'later' | 'none', reading a
 * 00:00/12:00 UTC anchor as the calendar date it was written as (mirrors
 * parts.dueLabel and app/services/workspace_time.is_date_anchor).
 */
export function dueBucket(iso, now = new Date()) {
  if (!iso) return 'none'
  const s = String(iso)
  const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + 'Z')
  if (isNaN(d)) return 'none'
  const anchor = d.getUTCMinutes() === 0 && d.getUTCSeconds() === 0 &&
    (d.getUTCHours() === 0 || d.getUTCHours() === 12)
  const due = anchor ? new Date(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()) : d
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const dueDay = new Date(due.getFullYear(), due.getMonth(), due.getDate())
  if (dueDay < today) return 'overdue'
  if (dueDay.getTime() === today.getTime()) return (!anchor && due < now) ? 'overdue' : 'today'
  return 'later'
}

/** Open deals only, ordered: overdue first, then today, then by due date. */
export function followUpList(cards, filter = 'today', query = '', now = new Date()) {
  const q = (query || '').trim().toLowerCase()
  const rank = { overdue: 0, today: 1, later: 2, none: 3 }
  return (cards || [])
    .filter(c => (c.status || 'open') === 'open')
    .map(c => ({ c, b: dueBucket(c.next_action_due_at, now) }))
    .filter(({ b }) => filter === 'all' ? true
      : filter === 'overdue' ? b === 'overdue'
      : b === 'overdue' || b === 'today')
    .filter(({ c }) => !q || [c.company_name, c.contact_name, c.phone, c.email, c.next_action]
      .filter(Boolean).some(v => String(v).toLowerCase().includes(q)))
    .sort((a, b) => (rank[a.b] - rank[b.b]) ||
      String(a.c.next_action_due_at || '9999').localeCompare(String(b.c.next_action_due_at || '9999')))
    .map(({ c, b }) => ({ ...c, due_bucket: b }))
}

/** "Good morning" / "Good afternoon" / "Good evening". */
export function greeting(now = new Date()) {
  const h = now.getHours()
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
}

/** First name for the greeting; never invents one. */
export function firstName(full) {
  return (full || '').trim().split(/\s+/)[0] || ''
}
