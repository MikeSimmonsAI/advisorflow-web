/* Pure state and wording logic for the buyer board — no React, no DOM, no
 * imports — so `node tests/frontend/wsBoardState.test.mjs` can exercise it.
 *
 * Three jobs:
 *   1. REQUEST ORDERING. A refresh that started earlier must never overwrite a
 *      board that a later request already delivered (slow network + a save that
 *      refreshes right after it). Each load takes a generation; only the newest
 *      generation may touch the state.
 *   2. NO DOUBLE SUBMIT. `createInFlight()` is a synchronous latch. Component
 *      `busy` state only flips after a render, so two fast clicks both see
 *      busy=false; the latch closes on the first.
 *   3. OPERATOR LANGUAGE. The server refuses with "label [code]" (selection
 *      rules) or {reason, code} (send path). The words are shown and the stable
 *      code stays visible for support, never the code alone.
 */

/* ── 1. load state ─────────────────────────────────────────────────────── */

export function initialBoardState() {
  return { board: null, error: null, loading: true, refreshing: false,
           latest: 0, applied: 0 }
}

/* Start a load. Returns the next state; the caller reads `state.latest` as the
 * generation token for this request. */
export function loadStarted(state) {
  return { ...state, latest: state.latest + 1, refreshing: state.board !== null,
           loading: state.board === null }
}

/* Settle a request. A response from any generation but the newest is dropped
 * whole — data or error — because the newest request is still the one that
 * will say what the board looks like. */
export function loadSucceeded(state, gen, board) {
  if (gen !== state.latest) return state
  return { ...state, board, error: null, loading: false, refreshing: false,
           applied: gen }
}

export function loadFailed(state, gen, message) {
  if (gen !== state.latest) return state
  // Rows already on screen are KEPT; the error is stated beside them.
  return { ...state, error: message || 'Something went wrong.', loading: false,
           refreshing: false }
}

/* What the screen should be: a blocking error only when there is nothing to
 * show, otherwise rows plus an explicit stale warning. */
export function boardView(state) {
  if (!state.board) return state.error ? 'error' : 'loading'
  return state.error ? 'stale' : 'ready'
}


/* ── 2. in-flight latch ────────────────────────────────────────────────── */

export function createInFlight() {
  let held = false
  return {
    get held() { return held },
    /* Run fn unless one is already running. Resolves {ran:false} when refused,
     * and always releases, even if fn throws. */
    async run(fn) {
      if (held) return { ran: false, value: undefined }
      held = true
      try {
        return { ran: true, value: await fn() }
      } finally {
        held = false
      }
    },
  }
}


/* ── 3. reason codes → operator language ───────────────────────────────── */

/* Codes the select / response routes can refuse with. Wording is the server's;
 * this map is only the fallback when a message arrives without its label. */
export const SELECTION_REASONS = {
  select_via_select_buyer:
    'Choose a buyer with Select, not by editing their response.',
  selected_row_is_locked:
    'This buyer is the current selection. Select a different buyer, or record '
    + 'that they passed, before changing their status.',
  buyer_opted_out: 'That buyer has opted out, so they cannot be selected.',
  buyer_inactive: 'That buyer is marked inactive, so they cannot be selected.',
  buyer_passed:
    'That buyer passed on this deal. Record a new response from them first.',
  buyer_not_on_deal: 'That buyer is not on this deal.',
}

/* Send-path codes (wholesale_disposition.SendRefused). */
export const SEND_REASONS = {
  recently_sent:
    'Sent moments ago. Wait a minute before resending so they do not get two copies.',
  already_sent: 'Already sent. Use Resend if you meant to send it again.',
  opted_out: 'This buyer has opted out of contact.',
  suppressed: 'This buyer is on the opt-out list.',
  no_address: 'There is no email address or phone number on file for this buyer.',
  not_enabled: 'Sending is switched off for this workspace. Nothing was sent.',
  bad_channel: 'Only email and text can be sent from here.',
  blocked: 'The send was blocked. Nothing was sent.',
  provider_error: 'The provider could not deliver it. Nothing reached the buyer.',
}

/* Split "label [code]" into its parts; a plain message has no code. */
export function parseRefusal(text) {
  const s = String(text == null ? '' : text).trim()
  const m = s.match(/^(.*?)\s*\[([a-z][a-z0-9_]*)\]\s*$/s)
  if (!m) return { label: s, code: null }
  return { label: m[1].trim(), code: m[2] }
}

/* The sentence a person reads, with the stable code kept as a trailing
 * reference ("Reference: buyer_passed") rather than as the message itself. */
export function describeRefusal(text, table = SELECTION_REASONS) {
  const { label, code } = parseRefusal(text)
  const words = label || (code && table[code]) || 'Something went wrong.'
  return code ? `${words} (reference: ${code})` : words
}

/* Outcome of POST /outreach/:id/resend → { ok, message, code }. */
export function resendOutcome(result) {
  if (result && result.sent) return { ok: true, message: 'Resent.', code: null }
  const code = (result && result.code) || null
  const words = (code && SEND_REASONS[code]) || (result && result.reason)
                || 'The deal sheet was not sent.'
  return { ok: false, code,
           message: code ? `${words} (reference: ${code})` : words }
}

/* Outcome of POST /deals/:id/select-buyer. `already_selected` is a success for
 * the buyer and a no-op for the deal; say so instead of calling it an error. */
export function selectOutcome(result, rows, outreachId) {
  if (result && result.already_selected) {
    return { ok: true, changed: false,
             message: 'That buyer was already selected. Nothing changed.' }
  }
  const replaced = (rows || []).some(
    (x) => x.is_selected && x.outreach_id !== outreachId)
  return {
    ok: true, changed: true,
    message: replaced
      ? 'Buyer selected. The previous selection was released and kept in the history.'
      : 'Buyer selected.',
  }
}


/* ── 4. what a row may offer ───────────────────────────────────────────── */

const ALL_RESPONSES = [
  'not_contacted', 'sent', 'delivered', 'opened', 'replied', 'interested',
  'needs_info', 'offer_submitted', 'passed', 'rejected', 'no_response',
]
/* Mirrors wholesale_selection.check_status_write for the CURRENT selection:
 * only an offer, or backing out, is accepted. 'selected' is never offered —
 * the generic response route refuses it (select_via_select_buyer). */
const SELECTED_RESPONSES = ['offer_submitted', 'passed', 'rejected']

export function isSelectedRow(row) {
  return Boolean(row && (row.is_selected || row.status === 'selected'))
}

export function allowedResponseStatuses(row) {
  return isSelectedRow(row) ? SELECTED_RESPONSES.slice() : ALL_RESPONSES.slice()
}

export function defaultResponseStatus(row) {
  const allowed = allowedResponseStatuses(row)
  if (row && allowed.includes(row.status)) return row.status
  return isSelectedRow(row) ? 'offer_submitted' : 'replied'
}

/* Select is offered only to a buyer the server will accept. */
export function selectDisabledReason(row) {
  if (!row) return 'No buyer.'
  if (row.do_not_contact) return 'This buyer has opted out of contact.'
  if (['passed', 'rejected'].includes(row.status)) {
    return 'This buyer passed. Record a new response first.'
  }
  return null
}

/* One selection per deal: the row that wins when a stale payload somehow
 * carries two flags is the one whose status agrees with the flag. */
export function currentSelection(rows) {
  const flagged = (rows || []).filter((r) => r.is_selected)
  return flagged.find((r) => r.status === 'selected') || flagged[0] || null
}
