// Small helpers shared by the Contacts page and its drawer. Display only:
// nothing here decides anything about a contact, it only names what the
// server already said.

export const RECORD_CLASS_OPTIONS = [
  { value: '', label: 'All classifications' },
  { value: 'contact', label: 'Contact' },
  { value: 'previous_customer', label: 'Previous customer' },
  { value: 'customer', label: 'Customer' },
  { value: 'renewal', label: 'Renewal' },
  { value: 'lead', label: 'Lead' },
  { value: 'partner', label: 'Partner' },
  { value: 'vendor', label: 'Vendor' },
  { value: 'employee', label: 'Employee' },
  { value: 'other', label: 'Other' },
]

const CLASS_TONE = {
  previous_customer: 'purple',
  customer: 'green',
  renewal: 'amber',
  lead: 'blue',
  contact: 'neutral',
}

export function humanize(value) {
  return String(value || '')
    .split(/[_\-\s]+/).filter(Boolean)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ')
}

export function classTone(recordClass) {
  return CLASS_TONE[recordClass] || 'neutral'
}

const EMAIL_TONE = {
  ready: 'green', pending: 'amber', review: 'amber',
  invalid: 'red', hard_bounce: 'red', unsubscribed: 'red', suppressed: 'red',
  no_email: 'neutral-dim',
}
const SMS_TONE = {
  ready: 'green', pending_validation: 'amber', review: 'amber',
  suppressed: 'red', dnc: 'red', opted_out: 'red', invalid: 'red',
  landline: 'neutral', no_phone: 'neutral-dim',
}
export function emailTone(s) { return EMAIL_TONE[s] || 'neutral' }
export function smsTone(s) { return SMS_TONE[s] || 'neutral' }

export function personName(c) {
  if (!c) return ''
  const full = (c.full_name || '').trim()
  if (full) return full
  return [c.first_name, c.last_name].filter(Boolean).join(' ').trim()
}

/** Name for display: the person, else the company, else an em dash. */
export function displayName(c) {
  return personName(c) || (c && c.company) || '—'
}

export function initials(c) {
  const src = personName(c) || (c && c.company) || ''
  const parts = src.split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  return (parts[0][0] + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase()
}

export function fmtNum(n) {
  return typeof n === 'number' ? n.toLocaleString() : n
}

export function fmtDate(v) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return String(v)
  return d.toLocaleString()
}

/** The JSON-ish blobs (custom_fields, vertical_fields...) may arrive as an
 *  object or as a serialised string. Normalise to [[key, value]] pairs. */
export function fieldPairs(blob) {
  let obj = blob
  if (typeof obj === 'string') {
    try { obj = JSON.parse(obj) } catch (e) { return obj.trim() ? [['value', obj]] : [] }
  }
  if (!obj || typeof obj !== 'object') return []
  if (Array.isArray(obj)) return obj.map((v, i) => [String(i + 1), v])
  return Object.entries(obj).filter(([, v]) => v !== null && v !== undefined && v !== '')
}

export function fieldValue(v) {
  if (v === true) return 'Yes'
  if (v === false) return 'No'
  if (v && typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
