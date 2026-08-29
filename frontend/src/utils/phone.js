// One phone formatter for the whole app.
//
// The STORED value is canonical E.164 (+14695537417) and nothing here changes
// it - these functions are for display only. Every surface that showed a raw
// +14695537417 to a human now calls formatPhone, so the leads table, the lead
// header, the client record and the conversation view read the same way.
//
// The backend has the same rule in app/routers/compose_router.py::_fmt_phone,
// so a number rendered by the server and one rendered by the page match.

/**
 * +14695537417 -> "+1 (469) 553-7417"
 * 4695537417   -> "(469) 553-7417"
 * Anything else is returned unchanged, because a number we cannot parse is
 * more useful shown as-is than silently mangled.
 */
export function formatPhone(value) {
  if (!value) return ''
  const raw = String(value).trim()
  const digits = raw.replace(/\D/g, '')
  if (digits.length === 11 && digits[0] === '1') {
    return `+1 (${digits.slice(1, 4)}) ${digits.slice(4, 7)}-${digits.slice(7)}`
  }
  if (digits.length === 10) {
    return `(${digits.slice(0, 3)}) ${digits.slice(3, 6)}-${digits.slice(6)}`
  }
  return raw
}

/** The href for a tel: link. Always the canonical value, never the pretty one. */
export function telHref(value) {
  if (!value) return ''
  const raw = String(value).trim()
  const digits = raw.replace(/\D/g, '')
  if (raw.startsWith('+')) return `tel:${raw}`
  if (digits.length === 10) return `tel:+1${digits}`
  if (digits.length === 11 && digits[0] === '1') return `tel:+${digits}`
  return `tel:${raw}`
}

export default formatPhone
