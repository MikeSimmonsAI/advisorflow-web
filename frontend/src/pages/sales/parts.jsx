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
