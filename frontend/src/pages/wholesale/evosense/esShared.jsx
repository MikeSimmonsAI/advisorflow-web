/* EvoSense — shared pieces for the acquisition-engine screens (Wholesale Phase 7).
 *
 * TWO RULES LIVE HERE.
 *   1. A score is never shown without its WHY. `ScoreCard` renders the value,
 *      the band, the version and every factor with its points.
 *   2. SANDBOX is always visible. Any record from a sandbox adapter carries a
 *      SANDBOX tag, and a page holding sandbox data carries the banner. Sandbox
 *      data is never presented as live.
 */
import { NavLink } from 'react-router-dom'
import { fmtLabel } from '../wsShared'

export function cents(v, blank = '—') {
  if (v === null || v === undefined) return blank
  const n = Number(v) / 100
  return '$' + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function ago(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const s = Math.round((Date.now() - d.getTime()) / 1000)
  if (s < 60) return 'just now'
  if (s < 3600) return `${Math.round(s / 60)} min ago`
  if (s < 86400) return `${Math.round(s / 3600)} h ago`
  return d.toLocaleDateString()
}

export function EvoNav() {
  const tabs = [
    ['/wholesale/evosense', 'Command Center', true],
    ['/wholesale/evosense/inbox', 'Discovery Inbox'],
    ['/wholesale/evosense/strategies', 'Strategies'],
    ['/wholesale/evosense/controls', 'Providers & Controls'],
  ]
  return (
    <nav className="es-nav" aria-label="EvoSense">
      {tabs.map(([to, label, end]) => (
        <NavLink key={to} to={to} end={!!end}
                 className={({ isActive }) => `es-nav__tab${isActive ? ' is-active' : ''}`}>
          {label}
        </NavLink>
      ))}
    </nav>
  )
}

export function SandboxBanner({ sandbox }) {
  if (!sandbox || !sandbox.banner) return null
  return (
    <div className="es-sandbox" role="note">
      <span className="es-tag es-tag--sandbox">SANDBOX</span>
      <span>{sandbox.banner}</span>
    </div>
  )
}

export function Tag({ kind, children }) {
  return <span className={`es-tag es-tag--${kind || 'plain'}`}>{children}</span>
}

const KIND_TAG = {
  sandbox: ['sandbox', 'SANDBOX'], real: ['real', 'LIVE'], manual: ['manual', 'MANUAL'],
  import: ['import', 'IMPORT'], interface_only: ['plain', 'INTERFACE ONLY'],
  not_built: ['plain', 'NOT BUILT'],
}
export function ConnectorTag({ kind }) {
  const [k, label] = KIND_TAG[kind] || ['plain', fmtLabel(kind)]
  return <Tag kind={k}>{label}</Tag>
}

export const BAND_LABEL = { hot: 'Hot', high: 'High', medium: 'Medium', low: 'Low',
                            insufficient: 'Insufficient evidence', none: 'None' }

export function band(value) {
  if (value === null || value === undefined) return 'insufficient'
  if (value >= 80) return 'hot'
  if (value >= 65) return 'high'
  if (value >= 45) return 'medium'
  return 'low'
}

/* A number with its band. `null` is INSUFFICIENT EVIDENCE, never 0. */
export function ScoreChip({ value, label }) {
  const b = band(value)
  return (
    <span className={`es-score es-score--${b}`} title={label ? `${label}: ${BAND_LABEL[b]}` : BAND_LABEL[b]}>
      <span className="es-score__n">{value === null || value === undefined ? '—' : value}</span>
      {label ? <span className="es-score__l">{label}</span> : null}
    </span>
  )
}

export function ScoreCard({ title, question, score, emptyText }) {
  const value = score ? score.value : null
  const b = band(value)
  return (
    <section className={`es-scorecard es-scorecard--${b}`}>
      <header className="es-scorecard__head">
        <div>
          <h3 className="es-scorecard__title">{title}</h3>
          <p className="es-scorecard__q">{question}</p>
        </div>
        <div className="es-scorecard__value">
          <span className="es-scorecard__n">{value === null || value === undefined ? '—' : value}</span>
          <span className="es-scorecard__band">{score && score.label && isNaN(Number(score.label))
            ? fmtLabel(score.label) : BAND_LABEL[b]}</span>
        </div>
      </header>
      {!score ? <p className="es-muted">{emptyText || 'Not scored yet.'}</p> : (
        <>
          <p className="es-why-label">Why</p>
          <ul className="es-factors">
            {(score.factors || []).map((f, i) => (
              <li key={i} className={f.points > 0 ? 'is-plus' : f.points < 0 ? 'is-minus' : 'is-zero'}>
                <span className="es-factors__pts">{f.points > 0 ? `+${f.points}` : f.points}</span>
                <span>{f.label}</span>
              </li>
            ))}
          </ul>
          <p className="es-version">{score.version}</p>
        </>
      )}
    </section>
  )
}

const BUCKET_TONE = {
  needs_you: 'hot', ready_for_outreach: 'good', high_opportunity: 'good', contact_found: 'info',
  outreach_active: 'info', responded: 'good', budget_blocked: 'warn', waiting_for_data: 'warn',
  needs_enrichment: 'warn', needs_review: 'warn', suppressed: 'bad', closed_out: 'muted',
  nurture: 'info', low_opportunity: 'muted', new: 'muted', promoted: 'good',
}
export function StatusPill({ status, label }) {
  return <span className={`es-status es-status--${BUCKET_TONE[status] || 'muted'}`}>
    {label || fmtLabel(status)}</span>
}

export function Signals({ items, max = 5 }) {
  if (!items || !items.length) return <span className="es-muted">No signals</span>
  const shown = items.slice(0, max)
  return (
    <span className="es-signals">
      {shown.map((s) => <span key={s} className="es-signal">{s}</span>)}
      {items.length > max ? <span className="es-signal es-signal--more">+{items.length - max}</span> : null}
    </span>
  )
}

export function Stat({ label, value, tone, note }) {
  return (
    <div className={`es-stat${tone ? ' is-' + tone : ''}`}>
      <div className="es-stat__v">{value === null || value === undefined ? '—' : value}</div>
      <div className="es-stat__l">{label}</div>
      {note ? <div className="es-stat__n">{note}</div> : null}
    </div>
  )
}
