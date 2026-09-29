/* Shared bits for the Organization Control Center tabs. */

export const STATUS_LABEL = {
  configured: 'Configured', partial: 'Partial', needs_setup: 'Needs Setup',
  not_ready: 'Not Ready', not_required: 'Not Required',
}

export function Pill({ status, label, tone }) {
  const cls = tone || status || 'neutral'
  return <span className={'occ-pill ' + cls}>{label || STATUS_LABEL[status] || status || '—'}</span>
}

export function fmtDate(v) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
}

export function ago(v) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return '—'
  const s = Math.max(0, (Date.now() - d.getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return Math.floor(s / 60) + ' min ago'
  if (s < 86400) return Math.floor(s / 3600) + ' h ago'
  if (s < 86400 * 30) return Math.floor(s / 86400) + ' d ago'
  return fmtDate(v)
}

/** A null count is "Not yet available", never zero. */
export function num(v) {
  return v === null || v === undefined ? 'Not yet available' : Number(v).toLocaleString()
}

export function humanAction(a) {
  return (a || '').replace(/^customer\./, '').replace(/[._]/g, ' ')
}
