/**
 * Local invoice draft helpers, pure (no React, no network). Money is integer
 * cents end to end: user text is parsed to cents, server *_cents fields are
 * formatted, and nothing here computes an authoritative total.
 */
export const MAX_UNIT_CENTS = 1_000_000_000      // matches app/services/invoice_draft.py
export const MAX_QTY = 1_000_000
export const MAX_ADJUST_CENTS = 10_000_000_000_000

/** Non-negative decimal text ("12.5", "1,250.00") -> integer cents, or null. */
export function parseMoneyText(text) {
  if (typeof text !== 'string') return null
  const m = /^(\d+)(?:\.(\d{1,2}))?$/.exec(text.trim().replace(/,/g, ''))   // sub-cent refused, not rounded
  if (!m) return null
  const cents = Number(m[1]) * 100 + Number((m[2] || '').padEnd(2, '0') || 0)
  return Number.isSafeInteger(cents) ? cents : null
}

/** Integer cents -> "$1,234.56"; anything not an integer -> an em dash. */
export function formatCents(cents) {
  if (!Number.isInteger(cents)) return '—'
  const abs = Math.abs(cents)
  const whole = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  return (cents < 0 ? '-' : '') + '$' + whole + '.' + String(abs % 100).padStart(2, '0')
}

/** Line form text -> { ok, error, line: {description, quantity, unit_price_cents} } */
export function validateLine({ description, quantity, unitPrice }) {
  const desc = typeof description === 'string' ? description.trim() : ''
  if (!desc) return { ok: false, error: 'Enter a description.' }
  if (desc.length > 200) return { ok: false, error: 'Description is too long (max 200).' }
  const q = String(quantity ?? '').trim()
  if (!/^\d+$/.test(q) || Number(q) < 1 || Number(q) > MAX_QTY) {
    return { ok: false, error: 'Quantity must be a whole number from 1 to ' + MAX_QTY + '.' }
  }
  const cents = parseMoneyText(String(unitPrice ?? ''))
  if (cents === null) {
    return { ok: false, error: 'Unit price must be a plain non-negative dollar amount with at most two decimals.' }
  }
  if (cents > MAX_UNIT_CENTS) return { ok: false, error: 'Unit price is above the allowed maximum.' }
  return { ok: true, error: null, line: { description: desc, quantity: Number(q), unit_price_cents: cents } }
}

/** Discount / operator-entered tax text -> { ok, error, cents } (blank means 0). */
export function validateAdjustmentAmount(text, label) {
  const t = String(text ?? '').trim()
  if (t === '') return { ok: true, error: null, cents: 0 }
  const cents = parseMoneyText(t)
  if (cents === null) return { ok: false, error: label + ' must be a plain non-negative dollar amount with at most two decimals.' }
  if (cents > MAX_ADJUST_CENTS) return { ok: false, error: label + ' is above the allowed maximum.' }
  return { ok: true, error: null, cents }
}

/** New-draft form -> { ok, error, body } */
export function validateNewDraft({ customerName, memo }) {
  const name = String(customerName ?? '').trim()
  if (!name) return { ok: false, error: 'Enter a customer name.' }
  if (name.length > 200) return { ok: false, error: 'Customer name is too long (max 200).' }
  if (String(memo ?? '').length > 2000) return { ok: false, error: 'Memo is too long (max 2000).' }
  return { ok: true, error: null, body: { customer_name: name, memo: String(memo ?? '').trim() } }
}

/** Void needs a reason; other transitions do not. */
export function validateTransition(to, reason) {
  const r = String(reason ?? '').trim()
  if (to === 'void' && !r) return { ok: false, error: 'A reason is required to void an invoice.' }
  if (r.length > 500) return { ok: false, error: 'Reason is too long (max 500).' }
  return { ok: true, error: null, reason: r }
}

/**
 * Which controls to show, from the SERVER's view only. Transitions come from
 * allowed_transitions, editing from `editable`. There is no send / finalize /
 * pay control by design.
 */
export function controlsFor(draft) {
  if (!draft) return { canEdit: false, transitions: [], blocker: null }
  const transitions = Array.isArray(draft.allowed_transitions) ? draft.allowed_transitions : []
  return {
    canEdit: draft.editable === true,
    transitions: transitions.filter(t => t === 'approval_ready' || t === 'draft' || t === 'void'),
    blocker: draft.state === 'draft' ? (draft.refusal_reason || null) : null,
  }
}

export const TRANSITION_LABEL = {
  approval_ready: 'Mark approval-ready',
  draft: 'Return to draft',
  void: 'Void invoice',
}

export const STATE_LABEL = { draft: 'Draft', approval_ready: 'Approval-ready', void: 'Void' }

export const NOT_SENT_NOTICE =
  'Approval-ready is an internal local status only. The invoice is not sent, finalized, charged, or created with any payment provider.'

export const TAX_NOTICE = 'Tax is an operator-entered amount. It is not calculated or attested.'

/** Stable per-action key: the same logical action (same token) gives the same key, so a retry replays. */
export function idempotencyKey(action, draftId, version, token) {
  return ['inv', action, draftId || 'new', version ?? 0, token].join(':').slice(0, 120)
}

/** Words for a refused or failed call. Never returns anything success-looking. */
export function refusalMessage(err) {
  const raw = err && err.message ? String(err.message) : ''
  const text = raw.replace(/\s+/g, ' ').trim().slice(0, 300) || 'The server refused that change.'
  const s = err && err.status
  if (s === 409) return 'Not saved - out of date. ' + text + ' The latest version has been reloaded.'
  if (s === 403) return 'Not saved - not authorized. ' + text
  if (s === 404) return 'Not saved - invoice not found. ' + text
  if (s === 400 || s === 422) return 'Not saved - invalid. ' + text
  if (s === 503) return 'Not saved - invoice storage is unavailable or not set up. ' + text
  return 'Not saved. ' + text
}

export function refusalKind(err) {
  const s = err && err.status
  return s === 409 ? 'conflict' : s === 403 ? 'authority' : s === 404 ? 'missing'
    : s === 503 ? 'storage' : (s === 400 || s === 422) ? 'invalid' : 'error'
}

/** Audit event -> { when, who, what, detail } for display; tolerant of bad JSON. */
export function describeEvent(ev) {
  let detail = ''
  try {
    const d = JSON.parse(ev.detail || '{}')
    detail = Object.keys(d).sort().map(k => {
      const v = d[k]
      return k + ': ' + (k.endsWith('_cents') && Number.isInteger(v) ? formatCents(v) : String(v))
    }).join(', ')
  } catch { detail = String(ev.detail || '').slice(0, 200) }
  return { when: ev.at, who: ev.actor, what: String(ev.action || '').replace(/_/g, ' '), detail }
}
