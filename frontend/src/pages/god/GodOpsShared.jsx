/**
 * Shared pieces for the Checkpoint 6 God Mode screens.
 *
 * Everything here is presentational. NO business logic lives in this file and
 * none of it decides what the owner is allowed to do - the server decides that,
 * and these components render whatever it returns. A permission enforced in
 * React is a permission that is not enforced.
 */
import './GodOps.css'

export function money(v, currency) {
  if (v === null || v === undefined) return '—'
  const n = Number(v)
  if (Number.isNaN(n)) return '—'
  return new Intl.NumberFormat(undefined, {
    style: 'currency', currency: currency || 'USD',
    maximumFractionDigits: n % 1 === 0 ? 0 : 2,
  }).format(n)
}

export function when(v, opts) {
  if (!v) return '—'
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleDateString(undefined,
    opts || { month: 'short', day: 'numeric', year: 'numeric' })
}

export function whenExact(v) {
  if (!v) return '—'
  const d = new Date(v)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString()
}

/** Days until (positive) or since (negative) a date. Null when there is none. */
export function daysTo(v) {
  if (!v) return null
  const d = new Date(v)
  if (Number.isNaN(d.getTime())) return null
  return Math.round((d.getTime() - Date.now()) / 86400000)
}

const STATUS_TONE = {
  live: 'live', blocked: 'blocked', ready_for_launch: 'ready',
  not_started: 'new',
}

export function StatusBadge({ status, label }) {
  return <span className={'go-badge ' + (STATUS_TONE[status] || '')}>
    {label || (status || '').replace(/_/g, ' ')}
  </span>
}

export function Kpi({ label, value, sub, tone }) {
  return (
    <div className={'go-kpi' + (tone ? ' ' + tone : '')}>
      <div className="k">{label}</div>
      <div className="v">{value}</div>
      {sub ? <div className="s">{sub}</div> : null}
    </div>
  )
}

export function Panel({ title, count, hot, children, actions }) {
  return (
    <section className="go-panel">
      {title ? (
        <h2>
          {title}
          {count !== undefined && count !== null
            ? <span className={'count' + (hot && count > 0 ? ' hot' : '')}>{count}</span>
            : null}
          {actions ? <span style={{ marginLeft: 'auto' }}>{actions}</span> : null}
        </h2>
      ) : null}
      {children}
    </section>
  )
}

/** A value the server did not have. Says so, rather than showing a plausible
 *  blank that an operator could mistake for real data. */
export function Fact({ k, v }) {
  const empty = v === null || v === undefined || v === ''
  return (
    <div className="go-fact">
      <div className="k">{k}</div>
      <div className={'v' + (empty ? ' none' : '')}>{empty ? 'not captured' : v}</div>
    </div>
  )
}

export function Empty({ children }) {
  return <div className="go-empty">{children}</div>
}

export function Bar({ percent }) {
  const p = Math.max(0, Math.min(100, Number(percent) || 0))
  return <div className="go-bar"><i style={{ width: p + '%' }} /></div>
}

/** Turn an API error into something an operator can act on.
 *
 *  FastAPI's `detail` is sometimes a string and sometimes an object (the launch
 *  route returns a message plus a warnings array). Rendering the object raw
 *  would print "[object Object]" at exactly the moment somebody needs to read
 *  why their launch was refused.
 */
export function errText(e) {
  const d = e && (e.detail !== undefined ? e.detail : e.message)
  if (!d) return 'Something went wrong.'
  if (typeof d === 'string') return d
  if (d.message) return d.message
  try { return JSON.stringify(d) } catch (_) { return String(d) }
}

export function errWarnings(e) {
  const d = e && e.detail
  return (d && Array.isArray(d.warnings)) ? d.warnings : []
}
