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
import { programCards } from '../program/programAccessModel.js'

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
  { key: 'demo',   label: 'Demo & Closing', stages: ['demo_build', 'demo_proposal', 'closing'],
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

// What each choice SUGGESTS. Every suggestion is shown in the form and can be
// changed or cleared before saving; nothing is recorded until the rep saves.
//   next / days — suggested next action and due date (null = no suggestion)
//   decide      — Not Interested asks the rep to choose: close as Lost, or a
//                 follow-up they schedule themselves. Never an automatic date.
//   books       — after saving, the real booking form opens. Choosing it does
//                 NOT mean a meeting exists; only the scheduler can say that.
export const CALL_OUTCOMES = [
  { key: 'connected',      label: 'Connected',      next: 'Follow up', days: 2 },
  { key: 'no_answer',      label: 'No Answer',      next: 'Try calling again', days: 1 },
  { key: 'voicemail',      label: 'Left Voicemail', next: 'Call back', days: 2 },
  { key: 'interested',     label: 'Interested',     next: 'Book a demo', days: 1 },
  { key: 'not_interested', label: 'Not Interested', next: null, days: null, decide: true },
  { key: 'book_meeting',   label: 'Book a Meeting', next: 'Book the meeting', days: 0, books: true },
]

// Not Interested → one of these, chosen explicitly.
export const NOT_INTERESTED_CHOICES = [
  { key: 'lost',  label: 'Close as Lost',
    hint: 'Ends this deal. It stays in your history; no follow-up is scheduled.' },
  { key: 'later', label: 'Follow up later',
    hint: 'Only if they said a later time might work. You pick the date.' },
  // Distinct from Not Interested: an explicit request for no further contact.
  // Recorded on the server (POST /sales/opportunities/{id}/do-not-contact),
  // which then blocks follow-ups, outreach logging, booking and every
  // prospect email for this phone/email — duplicates included.
  { key: 'dnc', label: 'Asked not to be contacted',
    hint: 'Do Not Contact: stops follow-ups, bookings and emails for this phone and email. A manager can correct it.' },
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

/** YYYY-MM-DD `days` from today — today in the team's zone when `tz` is given. */
export function ymdPlusDays(days, now = new Date(), tz) {
  const base = tz ? ymdIn(now, tz) : null
  let y, m, d
  if (base) { [y, m, d] = base.split('-').map(Number) } else { y = now.getFullYear(); m = now.getMonth() + 1; d = now.getDate() }
  const t = new Date(Date.UTC(y, m - 1, d + (Number(days) || 0)))
  return t.toISOString().slice(0, 10)
}

/**
 * A date-only due is stored on the 12:00 UTC anchor — the convention
 * NewProspect already uses and parts.isDateAnchor reads back as a calendar
 * date, so "due Oct 22" never shows as overdue on the evening of Oct 21.
 */
export function dueIsoFromYmd(ymd) {
  return /^\d{4}-\d{2}-\d{2}$/.test(ymd || '') ? ymd + 'T12:00:00' : null
}

// How each outcome reads on the deal's timeline. Book a Meeting records that
// they AGREED to meet — the booking itself is logged by the scheduler only
// when an appointment is actually created.
const SUMMARY = {
  connected: 'Connected',
  no_answer: 'No answer',
  voicemail: 'Left voicemail',
  interested: 'Interested',
  not_interested: 'Not interested',
  book_meeting: 'Agreed to a meeting (not booked yet)',
}

/**
 * The existing calls a recorded outcome makes:
 *   1. POST /sales/opportunities/{id}/notes   (timeline entry, event_type call_outcome)
 *   2. PATCH /sales/opportunities/{id}       (next action, due date, stage) — only
 *      when something about the deal changes.
 *
 * Not Interested needs `decision`: 'lost' closes the deal as Lost and clears the
 * next action and date (no future follow-up); 'later' REQUIRES a next action
 * and a date the rep chose. Throws with a plain message when something required
 * is missing, so the form can show it.
 */
export function buildOutcomeRequests({ stage, outcome, note, nextAction, dueYmd, decision }) {
  const o = outcomeByKey(outcome)
  if (!o) throw new Error('Pick what happened on the call.')
  const text = (note || '').trim()
  const next = (nextAction || '').trim()
  const due = dueIsoFromYmd(dueYmd)
  let label = SUMMARY[o.key]
  const patch = {}

  if (o.decide) {
    if (decision === 'lost') {
      label = 'Not interested — closed as Lost'
      patch.stage = LOST_STAGE
      patch.loss_reason = 'Not interested (call)'
      patch.next_action = null
      patch.next_action_due_at = null
    } else if (decision === 'dnc') {
      // The call result is written FIRST (outreach logging is refused once the
      // request is on file); the caller then records the Do Not Contact.
      label = 'Asked not to be contacted'
      patch.next_action = null
      patch.next_action_due_at = null
    } else if (decision === 'later') {
      if (!next) throw new Error('Add the next action for the later follow-up.')
      if (!due) throw new Error('Pick the date for the later follow-up.')
      label = 'Not interested now — follow up later'
      patch.next_action = next
      patch.next_action_due_at = due
    } else {
      throw new Error('Choose Close as Lost, Follow up later, or Asked not to be contacted.')
    }
  } else {
    if (next) patch.next_action = next
    if (due) patch.next_action_due_at = due
    const moved = stageAfterOutcome(stage, outcome)
    if (moved) patch.stage = moved
  }

  const full = 'Call: ' + label + (text ? ' — ' + text : '')
  const summary = full.slice(0, 250)
  // The summary is capped at 250 by the API; a longer note keeps its full text
  // in `detail` so nothing the rep typed is lost.
  const body = { event_type: 'call_outcome', summary, detail: summary.length < full.length ? text : null }
  return { note: body, patch: Object.keys(patch).length ? patch : null }
}

// ── follow-up list filters ─────────────────────────────────────────────────

/** YYYY-MM-DD of an instant in `tz` (falls back to the browser zone). */
function ymdIn(date, tz) {
  try {
    return new Intl.DateTimeFormat('en-CA', { timeZone: tz || undefined, year: 'numeric', month: '2-digit', day: '2-digit' })
      .format(date)
  } catch {
    const p = x => String(x).padStart(2, '0')
    return date.getFullYear() + '-' + p(date.getMonth() + 1) + '-' + p(date.getDate())
  }
}

/**
 * 'overdue' | 'today' | 'later' | 'none' — the SAME rule as the server
 * (app/services/workspace_time.py is_overdue / is_due_by_today), read in the
 * sales team's time zone so a badge, a list and My Day can never disagree:
 *   · a date-only due (stored on a 00:00 / 12:00 UTC anchor) is that calendar
 *     date: overdue once the team's day has moved past it;
 *   · any other due is a real instant: overdue once it has passed, otherwise
 *     due "today" when it falls on the team's current date.
 */
export function dueBucket(iso, now = new Date(), tz) {
  if (!iso) return 'none'
  const s = String(iso)
  const d = new Date(/[zZ]|[+-]\d{2}:?\d{2}$/.test(s) ? s : s + 'Z')
  if (isNaN(d)) return 'none'
  const anchor = d.getUTCMinutes() === 0 && d.getUTCSeconds() === 0 && d.getUTCMilliseconds() === 0 &&
    (d.getUTCHours() === 0 || d.getUTCHours() === 12)
  const today = ymdIn(now, tz)
  if (anchor) {
    const dueDay = d.toISOString().slice(0, 10)
    return dueDay < today ? 'overdue' : dueDay === today ? 'today' : 'later'
  }
  if (d < now) return 'overdue'
  return ymdIn(d, tz) <= today ? 'today' : 'later'
}

/** Open deals only, ordered: overdue first, then today, then by due date. */
export function followUpList(cards, filter = 'today', query = '', now = new Date(), tz) {
  const q = (query || '').trim().toLowerCase()
  const rank = { overdue: 0, today: 1, later: 2, none: 3 }
  return (cards || [])
    .filter(c => (c.status || 'open') === 'open')
    // Do Not Contact is never "due" — same rule as the server's My Day list.
    .filter(c => filter === 'all' || !c.do_not_contact)
    .map(c => ({ c, b: dueBucket(c.next_action_due_at, now, tz) }))
    .filter(({ b }) => filter === 'all' ? true
      : filter === 'overdue' ? b === 'overdue'
      : filter === 'today_only' ? b === 'today'
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

// ── typed time → booking time ──────────────────────────────────────────────

/**
 * The offset (minutes, local minus UTC) of `tz` at the given UTC instant,
 * read from the browser's own time-zone database.
 */
function zoneOffsetMinutes(utcMs, tz) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  }).formatToParts(new Date(utcMs))
  const v = {}
  parts.forEach(p => { v[p.type] = Number(p.value) })
  const asUtc = Date.UTC(v.year, v.month - 1, v.day, v.hour % 24, v.minute, v.second)
  return Math.round((asUtc - utcMs) / 60000)
}

/**
 * A wall-clock date + time typed in the sales team's time zone, as the naive
 * UTC string POST /sales/appointments expects ("2026-10-23T15:00:00").
 * Two passes so a time on a daylight-saving change day still resolves to the
 * offset that applies at that moment.
 */
export function wallToUtcNaive(ymd, hm, tz) {
  const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd || '')
  const t = /^(\d{1,2}):(\d{2})/.exec(hm || '')
  if (!d || !t) return null
  const wall = Date.UTC(+d[1], +d[2] - 1, +d[3], +t[1], +t[2])
  let utc = wall
  try {
    utc = wall - zoneOffsetMinutes(wall, tz || 'America/Chicago') * 60000
    utc = wall - zoneOffsetMinutes(utc, tz || 'America/Chicago') * 60000
  } catch {
    return null
  }
  return new Date(utc).toISOString().slice(0, 19)
}

/**
 * OTHER WORKSPACES (#300). How many places other than this Sales Workspace the
 * person may open from the program launcher (/workspaces) — counted with the
 * launcher's own card model over the server's /auth/my-contexts answer, so the
 * sidebar entry and the launcher can never disagree. The Back Office card is
 * where they already are, so it does not count. God Mode keeps its own return
 * bar and is never offered this (same rule as the old "Switch program" link).
 */
export function otherWorkspaceCount(ctx, role) {
  if (!ctx || role === 'god_admin') return 0
  return programCards(ctx).visible.filter(c => c.type !== 'back_office').length
}
