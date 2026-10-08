/**
 * Proposal money, pure. Integer cents in, deterministic text out. No floats
 * carry a total: inputs are parsed from their decimal TEXT into cents, and
 * every derived figure is integer arithmetic on cents.
 */
export const MAX_CENTS = 100_000_000_000          // $1,000,000,000.00, matches the server
export const MAX_UNITS = 1_000_000
export const MAX_TERM_MONTHS = 120

/** Decimal text ("-500", "12.5", "1,250.00") -> integer cents, or null if unusable. */
export function parseCents(text) {
  if (typeof text === 'number') {
    if (!Number.isFinite(text)) return null
    text = String(text)
  }
  if (typeof text !== 'string') return null
  const t = text.trim().replace(/,/g, '')
  const m = /^([+-])?(\d+)(?:\.(\d{1,2}))?$/.exec(t)   // more than 2 decimals is refused, not rounded
  if (!m) return null
  const cents = Number(m[2]) * 100 + Number((m[3] || '').padEnd(2, '0') || 0)
  if (!Number.isSafeInteger(cents) || cents > MAX_CENTS) return null
  return m[1] === '-' ? -cents : cents
}

/** Integer cents -> "$1,234.56". Deterministic: fixed en-US grouping, always 2 decimals. */
export function formatCents(cents, currency = 'USD') {
  if (cents === null || cents === undefined || !Number.isInteger(cents)) return '—'
  const neg = cents < 0
  const abs = Math.abs(cents)
  const whole = String(Math.floor(abs / 100)).replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const frac = String(abs % 100).padStart(2, '0')
  const sym = (currency || 'USD') === 'USD' ? '$' : (currency + ' ')
  return (neg ? '-' : '') + sym + whole + '.' + frac
}

/** Validate a manager adjustment. -> { ok, cents, error } */
export function validateAdjustment(text) {
  const cents = parseCents(text)
  if (text === '' || text === null || text === undefined) {
    return { ok: false, cents: null, error: 'Enter an adjustment amount, for example -500.00.' }
  }
  if (cents === null) {
    return { ok: false, cents: null,
             error: 'Enter a plain dollar amount with at most two decimals (no NaN, units or symbols).' }
  }
  return { ok: true, cents, error: null }
}

/** Total after an adjustment, in cents; refuses a negative total like the server. */
export function totalAfter(baseCents, adjCents) {
  if (!Number.isInteger(baseCents) || !Number.isInteger(adjCents)) return { ok: false, cents: null, error: 'Not priced.' }
  const t = baseCents + adjCents
  return t < 0 ? { ok: false, cents: null, error: 'That adjustment would make the total negative.' }
               : { ok: true, cents: t, error: null }
}

/** Custom-rate form -> { ok, error, fields, monthlyCents, commitmentCents } */
export function validateCustomRate({ unit, min, term }) {
  const unitC = parseCents(String(unit ?? ''))
  if (unitC === null || unitC <= 0) {
    return { ok: false, error: 'Rate per unit must be a positive dollar amount with at most two decimals.' }
  }
  const minTxt = String(min === '' || min == null ? '1' : min).trim()
  if (!/^\d+$/.test(minTxt) || Number(minTxt) < 1 || Number(minTxt) > MAX_UNITS) {
    return { ok: false, error: 'Minimum units must be a whole number from 1 to ' + MAX_UNITS + '.' }
  }
  const termTxt = String(term == null ? '' : term).trim()
  if (termTxt !== '' && (!/^\d+$/.test(termTxt) || Number(termTxt) > MAX_TERM_MONTHS)) {
    return { ok: false, error: 'Term must be a whole number of months from 0 to ' + MAX_TERM_MONTHS + '.' }
  }
  const units = Number(minTxt)
  const months = termTxt === '' ? 0 : Number(termTxt)
  const monthlyCents = unitC * units
  if (!Number.isSafeInteger(monthlyCents) || monthlyCents > MAX_CENTS) {
    return { ok: false, error: 'That monthly total is larger than the allowed maximum.' }
  }
  return {
    ok: true, error: null, monthlyCents, commitmentCents: months > 0 ? monthlyCents * months : null,
    fields: { custom_unit_price: unitC / 100, custom_min_units: units,
              custom_term_months: months > 0 ? months : null },
  }
}

/**
 * Words for a refused call. Server text is shown (it is written for the rep),
 * trimmed and length-capped; the label says WHAT KIND of refusal it was.
 * Never returns a success-looking string.
 */
export function refusalMessage(err) {
  const raw = err && err.message ? String(err.message) : ''
  const text = raw.replace(/\s+/g, ' ').trim().slice(0, 300) || 'The server refused that change.'
  const status = err && err.status
  if (status === 409) return 'Not saved - out of date. ' + text
  if (status === 403) return 'Not saved - not authorized. ' + text
  if (status === 400 || status === 422) return 'Not saved - invalid. ' + text
  return 'Not saved. ' + text
}

/** 'conflict' lets the UI offer a reload; others just show the message. */
export function refusalKind(err) {
  const s = err && err.status
  return s === 409 ? 'conflict' : s === 403 ? 'authority' : (s === 400 || s === 422) ? 'invalid' : 'error'
}
