/* Pure disposition-handoff and appointment helpers for the deal room — no
 * React, no DOM, no imports — so `node tests/frontend/wsDispositionState.test.mjs`
 * runs it.
 *
 *   1. SEND INPUT. Asking price is parsed once ("$250,000" is fine, "abc" is an
 *      error — never silently NaN→null), and nothing is sendable without a
 *      ticked buyer and an enabled channel. Each problem names its field.
 *   2. SEND OUTCOME. Only what the server reported per buyer: sent (with a
 *      provider reference when supplied) or not sent (with its reason/code).
 *      A send is never described as accepted, contracted or funded.
 *   3. STALE RESULT. A preview/outcome belongs to the inputs that produced it;
 *      when those change, it is no longer shown as current.
 *   4. APPOINTMENT. Status and time must agree; a status shows its time only
 *      when the status can have one. Unknown statuses are shown as unknown.
 */

export function parseMoney(value) {
  if (value === null || value === undefined) return { ok: true, value: null }
  const s = String(value).replace(/[$,\s]/g, '')
  if (s === '') return { ok: true, value: null }
  if (!/^\d+(\.\d{1,2})?$/.test(s)) return { ok: false, value: null }
  const n = Number(s)
  return Number.isFinite(n) ? { ok: true, value: n } : { ok: false, value: null }
}

/* → { ok, errors: {fieldId: text}, firstInvalid, body }. Field ids are the
 * element ids the caller can focus. */
export function validateSend({ selected, askingPrice, channel, channels }) {
  const errors = {}
  const price = parseMoney(askingPrice)
  if (!price.ok) errors['dp-asking'] = 'Enter the asking price as a number, like 250000.'
  const list = Array.isArray(channels) ? channels : []
  const ch = list.find((c) => c.channel === channel)
  if (!channel) errors['dp-channel'] = 'Pick a channel.'
  else if (ch && ch.enabled === false) {
    errors['dp-channel'] = ch.detail || 'This channel is not enabled for this workspace.'
  }
  if (!Array.isArray(selected) || !selected.length) {
    errors['dp-buyers'] = 'Tick at least one buyer first.'
  }
  const order = ['dp-buyers', 'dp-asking', 'dp-channel']
  const firstInvalid = order.find((k) => errors[k]) || null
  return {
    ok: !firstInvalid, errors, firstInvalid,
    body: { buyer_ids: selected, asking_price: price.value, channel },
  }
}

export function sendInputKey({ selected, askingPrice, channel }) {
  return JSON.stringify([[...(selected || [])].map(String).sort(),
                         String(askingPrice ?? '').trim(), channel || ''])
}

/* Summary from the server's per-buyer rows, counted from the rows themselves
 * rather than trusting a total that might disagree. */
export function summarizeOutcome(outcome) {
  const rows = Array.isArray(outcome?.results) ? outcome.results : []
  const sent = rows.filter((r) => r && r.sent).length
  const notSent = rows.length - sent
  const kind = !rows.length ? 'none' : sent === 0 ? 'none-sent'
    : notSent === 0 ? 'all-sent' : 'partial'
  const text = !rows.length
    ? 'The server returned no per-buyer results, so nothing is confirmed as sent.'
    : `${sent} sent, ${notSent} not sent.`
  return { sent, notSent, kind, text, tone: kind === 'all-sent' ? 'good' : 'warn' }
}

/* Is a shown preview/outcome still about the current inputs? */
export function isCurrentResult(resultKey, currentKey) {
  return resultKey != null && resultKey === currentKey
}

const APPT_WITH_TIME = ['scheduled', 'completed', 'no_show']
const APPT_LABELS = {
  none: 'No appointment', requested: 'Requested — not yet scheduled',
  scheduled: 'Scheduled', completed: 'Completed',
  no_show: 'Missed (no-show)', cancelled: 'Cancelled',
}

export function appointmentLabel(status) {
  if (!status) return 'No appointment recorded'
  return APPT_LABELS[status] || 'Unknown status'
}

export function appointmentCanHaveTime(status) {
  return APPT_WITH_TIME.includes(status)
}

/* Display: a time is shown only beside a status that can have one. */
export function appointmentSummary(status, at, fmt) {
  const label = appointmentLabel(status)
  const when = at ? (fmt ? fmt(at) : at) : ''
  if (when && appointmentCanHaveTime(status)) return `${label} · ${when}`
  if (when && status === 'cancelled') return `${label} (was ${when})`
  return label
}

/* Seller-editor appointment fields: status + datetime-local text. */
export function validateAppointment({ status, at }) {
  const errors = {}
  const raw = String(at ?? '').trim()
  let iso = null
  if (raw) {
    const d = new Date(raw)
    if (Number.isNaN(d.getTime())) errors.appointment_at = 'Enter a valid date and time.'
    else iso = d.toISOString().slice(0, 19)
  }
  if (!errors.appointment_at) {
    if (status === 'scheduled' && !raw) {
      errors.appointment_at = 'A scheduled appointment needs a date and time.'
    } else if (status === 'none' && raw) {
      errors.appointment_at = 'Clear the time, or pick a status that has one.'
    } else if (status === 'requested' && raw) {
      errors.appointment_at = 'A requested appointment has no time yet — clear it, or choose Scheduled.'
    }
  }
  const firstInvalid = errors.appointment_at ? 'appointment_at' : null
  return { ok: !firstInvalid, errors, firstInvalid, iso }
}
