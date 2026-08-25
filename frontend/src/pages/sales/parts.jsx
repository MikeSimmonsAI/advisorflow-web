/**
 * Shared presentational pieces for the Sales Workspace.
 *
 * NotBuilt is the important one. When a section depends on the scheduling
 * engine that does not exist yet, it renders NotBuilt rather than an empty
 * list — "no appointments today" and "appointments do not exist yet" look
 * identical on screen and mean completely different things. The API sends
 * {available:false, reason} for exactly these, and we surface the reason.
 */

export function Chip({ tone, children }) {
  return <span className={'sw-chip' + (tone ? ' sw-' + tone : '')}>{children}</span>
}

export function Card({ title, sub, right, children, bodyless }) {
  return (
    <div className="sw-card">
      {(title || right) && (
        <div className="sw-card-h">
          <div>
            {title && <h3>{title}</h3>}
            {sub && <small>{sub}</small>}
          </div>
          {right && <><div className="sw-spacer" />{right}</>}
        </div>
      )}
      {bodyless ? children : <div className="sw-card-b">{children}</div>}
    </div>
  )
}

export function Metric({ label, value, sub, attn }) {
  return (
    <div className={'sw-metric' + (attn ? ' sw-attn' : '')}>
      <span>{label}</span>
      <b>{value}</b>
      {sub && <small>{sub}</small>}
    </div>
  )
}

export function Empty({ title, children }) {
  return (
    <div className="sw-empty">
      <b>{title}</b>
      {children && <p>{children}</p>}
    </div>
  )
}

/**
 * A capability that genuinely does not exist yet. `block` is the
 * {available:false, reason} object the API returns.
 */
export function NotBuilt({ label, block }) {
  const reason = (block && block.reason)
    || 'This part of the workspace has not been built yet.'
  return (
    <div className="sw-notbuilt">
      <b>{label || 'NOT BUILT YET'}</b>
      <p>{reason}</p>
    </div>
  )
}

export function Info({ label, value }) {
  return (
    <div className="sw-info">
      <span>{label}</span>
      <b>{value === null || value === undefined || value === '' ? '—' : value}</b>
    </div>
  )
}

export function ErrorBar({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="sw-err">
      {String(error)}
      {onRetry && <> <button className="sw-tiny" style={{ marginLeft: 8 }} onClick={onRetry}>Retry</button></>}
    </div>
  )
}

// ── time formatting ─────────────────────────────────────────────────────────
//
// The API sends naive datetimes with NO timezone suffix: instants are naive
// UTC ("2026-08-26T14:00:00") and `*_local` fields are the already-resolved
// wall clock in the team's timezone ("2026-08-26T09:00:00").
//
// `new Date("2026-08-26T09:00:00")` interprets that as the BROWSER's local
// time. For an instant that is simply wrong, and for a resolved wall clock it
// is right only by luck when the viewer happens to sit in the team's zone —
// which is exactly the bug that made a 9am Chicago meeting render as 2pm.
//
// So: never hand a naive string to Date and hope. `parseNaive` rebuilds the
// components into a Date used purely as a formatting vehicle, so the wall clock
// the server resolved is the wall clock displayed, wherever the viewer is.

export function parseNaive(iso) {
  if (!iso) return null
  const m = String(iso).match(/^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})/)
  if (!m) {
    const d = new Date(iso)
    return isNaN(d) ? null : d
  }
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]),
                  Number(m[4]), Number(m[5]))
}

/** "9:00 AM" from a resolved local wall clock. */
export function wallTime(iso) {
  const d = parseNaive(iso)
  return d ? d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' }) : ''
}

/** "Wednesday, Aug 26" from a resolved local wall clock. */
export function wallDay(iso) {
  const d = parseNaive(iso)
  return d ? d.toLocaleDateString(undefined,
    { weekday: 'long', month: 'short', day: 'numeric' }) : ''
}

/** "Wed, Aug 26, 9:00 AM" from a resolved local wall clock. */
export function wallDateTime(iso) {
  const d = parseNaive(iso)
  return d ? d.toLocaleString(undefined, {
    weekday: 'short', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit',
  }) : ''
}

// ── formatting ──────────────────────────────────────────────────────────────

export function money(v) {
  if (v === null || v === undefined) return null
  return '$' + Number(v).toLocaleString(undefined, {
    minimumFractionDigits: 0, maximumFractionDigits: 0,
  })
}

export function shortDate(iso) {
  if (!iso) return null
  const d = new Date(iso)
  if (isNaN(d)) return null
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export function dateTime(iso) {
  if (!iso) return null
  const d = new Date(iso)
  if (isNaN(d)) return null
  return d.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}

/** "Overdue", "Today", "in 3d" — a due date the rep can act on at a glance. */
export function dueLabel(iso) {
  if (!iso) return { text: null, tone: null }
  const d = new Date(iso)
  if (isNaN(d)) return { text: null, tone: null }
  const now = new Date()
  const days = Math.floor((d - now) / 86400000)
  if (d < now) return { text: 'Overdue', tone: 'red' }
  if (days === 0) return { text: 'Today', tone: 'amber' }
  if (days === 1) return { text: 'Tomorrow', tone: 'amber' }
  return { text: 'in ' + days + 'd', tone: null }
}

export function initials(name) {
  if (!name) return '?'
  return name.trim().split(/\s+/).slice(0, 2).map(p => p[0]).join('').toUpperCase()
}
