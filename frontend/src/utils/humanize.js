// Turn a stored key (contract_signed, awaiting-client) into a readable label
// ("Contract Signed"). Used where a screen would otherwise print the raw enum.
export function humanizeKey(value, fallback = '—') {
  if (value === null || value === undefined) return fallback
  const s = String(value).trim()
  if (!s) return fallback
  if (!/[_-]/.test(s) && s !== s.toLowerCase()) return s // already a label
  return s.split(/[_\-\s]+/).filter(Boolean)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1).toLowerCase()).join(' ')
}

// "2026-10-02T15:40:49Z" -> "Oct 2, 2026, 3:40 PM" in the viewer's locale.
// Anything that is not an ISO timestamp is returned unchanged.
const ISO_RE = /\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?\b/g
export function readableTimestamps(text, locale) {
  if (typeof text !== 'string' || !text) return text
  return text.replace(ISO_RE, (m) => {
    const d = new Date(m)
    if (Number.isNaN(d.getTime())) return m
    return d.toLocaleString(locale, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' })
  })
}

// Replace whole-word stored keys inside server text with their labels:
// "awaiting_client for 12 days" -> "Awaiting client for 12 days".
export function replaceKeys(text, labels) {
  if (typeof text !== 'string' || !text || !labels) return text
  return text.replace(/\b[a-z]+(?:_[a-z]+)+\b/g, (m) => (Object.prototype.hasOwnProperty.call(labels, m) ? labels[m] : m))
}
