/**
 * GOD MODE CARD KIT — the shared building blocks of the redesigned God Mode.
 *
 * KPI cards, status pills, action queues, health rows, project and
 * organization cards, expandable panels, and the three states every data card
 * needs (loading, empty, error). God Mode tokens only, no third-party library.
 *
 * Rule for every component here: nothing renders a number or a status it was
 * not given. A missing value shows as "—" or an explicit empty/unknown state,
 * never as a zero or a guess.
 */
import { useState } from 'react'
import { Link } from 'react-router-dom'
import './godKit.css'

export function Page ({ eyebrow, title, sub, actions, children }) {
  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <div className="gk-page">
        <div className="gk-head">
          <div>
            {eyebrow ? <div className="gk-eyebrow">{eyebrow}</div> : null}
            <h1 className="gk-title">{title}</h1>
            {sub ? <p className="gk-sub">{sub}</p> : null}
          </div>
          {actions ? <div className="gk-actions">{actions}</div> : null}
        </div>
        {children}
      </div>
    </div>
  )
}

export function Card ({ title, note, action, children, style, className = '' }) {
  return (
    <section className={`gk-card ${className}`} style={style} aria-label={typeof title === 'string' ? title : undefined}>
      {(title || action) && (
        <div className="gk-card-head">
          <div>
            {title ? <h2 className="gk-card-title">{title}</h2> : null}
            {note ? <div className="gk-card-note">{note}</div> : null}
          </div>
          {action || null}
        </div>
      )}
      {children}
    </section>
  )
}

export function Kpi ({ label, value, sub, onClick, tone }) {
  const body = (
    <>
      <span className="gk-kpi-label">{label}</span>
      <span className="gk-kpi-value" style={tone ? { color: `var(--gm-${tone})` } : undefined}>
        {value === null || value === undefined || value === '' ? '—' : value}
      </span>
      {sub ? <span className="gk-kpi-sub">{sub}</span> : null}
    </>
  )
  return onClick
    ? <button type="button" className="gk-card gk-kpi" onClick={onClick}>{body}</button>
    : <div className="gk-card gk-kpi">{body}</div>
}

const PILL = {
  good: 'good', ok: 'good', healthy: 'good', green: 'good', yes: 'good',
  bad: 'bad', critical: 'bad', red: 'bad', error: 'bad', failing: 'bad',
  warn: 'warn', warning: 'warn', amber: 'warn', degraded: 'warn', high: 'warn',
  info: 'info', blue: 'info', test: 'test', practice: 'test',
}

export function Pill ({ tone = 'off', children, title }) {
  const t = PILL[tone] || (['good', 'bad', 'warn', 'info', 'test'].includes(tone) ? tone : '')
  return <span className={`gk-pill ${t ? 'gk-pill--' + t : ''}`} title={title}>{children}</span>
}

export function Loading ({ children = 'Loading…' }) {
  return <div className="gk-loading" role="status" aria-live="polite">{children}</div>
}

export function Empty ({ children }) {
  return <div className="gk-empty">{children}</div>
}

export function ErrorState ({ children, onRetry }) {
  return (
    <div className="gk-error" role="alert">
      {children}{' '}
      {onRetry ? <button type="button" className="gk-link" onClick={onRetry}>Try again</button> : null}
    </div>
  )
}

/** Where a row's action goes: an internal route, an external URL, or a handler. */
export function ActionLink ({ to, href, onClick, children, primary, small = true, danger, disabled }) {
  const cls = `gk-btn ${small ? 'gk-btn--sm' : ''} ${primary ? 'gk-btn--primary' : ''} ${danger ? 'gk-btn--danger' : ''}`
  if (onClick || disabled) {
    return <button type="button" className={cls} onClick={onClick} disabled={disabled}>{children}</button>
  }
  if (href) return <a className={cls} href={href} target="_blank" rel="noopener noreferrer">{children}</a>
  if (to) return <Link className={cls} to={to}>{children}</Link>
  return null
}

function ago (iso) {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const m = Math.round((Date.now() - t) / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m} min ago`
  const h = Math.round(m / 60)
  if (h < 48) return `${h} hr ago`
  return `${Math.round(h / 24)} days ago`
}
export { ago }

/** A ranked list of things that need the owner, each with one action. */
export function ActionQueue ({ items, limit, onAction, empty = 'Nothing needs you right now.' }) {
  const shown = limit ? items.slice(0, limit) : items
  if (!shown.length) return <Empty>{empty}</Empty>
  return (
    <ul className="gk-list">
      {shown.map(x => (
        <li key={x.id} className="gk-row">
          <span className={`gk-sev gk-sev--${x.sev || 'info'}`} aria-label={`Severity ${x.sev || 'info'}`} />
          <div className="gk-row-main">
            <div className="gk-row-title">{x.title}</div>
            {x.detail ? <div className="gk-row-sub">{x.detail}</div> : null}
          </div>
          {x.org_name ? <span className="gk-row-meta" title="Organization">{x.org_name}</span> : null}
          {x.at || x.age ? <span className="gk-row-meta">{x.at ? ago(x.at) : x.age}</span> : null}
          {x.action ? (
            <ActionLink to={x.to && !x.to.startsWith('#') ? x.to : undefined}
              onClick={onAction && (!x.to || x.to.startsWith('#') || x.org) ? () => onAction(x) : undefined}>
              {x.action}
            </ActionLink>
          ) : null}
        </li>
      ))}
    </ul>
  )
}

const HEALTH_TONE = { ok: 'good', healthy: 'good', green: 'good', good: 'good',
  warning: 'warn', degraded: 'warn', amber: 'warn', attention: 'warn',
  critical: 'bad', red: 'bad', down: 'bad', error: 'bad',
  warn: 'warn', bad: 'bad', off: 'off', no_source: 'off',
  unknown: 'off', not_configured: 'off', idle: 'off' }

export function HealthRow ({ label, status, statusLabel, headline, to }) {
  return (
    <li className="gk-row">
      <div className="gk-row-main">
        <div className="gk-row-title">{label}</div>
        {headline ? <div className="gk-row-sub">{headline}</div> : null}
      </div>
      <Pill tone={HEALTH_TONE[(status || '').toLowerCase()] || 'off'}>{statusLabel || status || 'Unknown'}</Pill>
      {to ? <ActionLink to={to}>Open</ActionLink> : null}
    </li>
  )
}

/** A card whose body opens and closes. Keyboard and screen-reader friendly. */
export function Expandable ({ header, children, defaultOpen = false, id }) {
  const [open, setOpen] = useState(defaultOpen)
  const bodyId = (id || Math.random().toString(36).slice(2)) + '-body'
  return (
    <div className="gk-card">
      <button type="button" className="gk-expand" aria-expanded={open} aria-controls={bodyId}
        onClick={() => setOpen(o => !o)}>
        <svg className={`gk-chev ${open ? 'gk-chev--open' : ''}`} width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d="M9 18l6-6-6-6" /></svg>
        <div style={{ flex: 1, minWidth: 0 }}>{header}</div>
      </button>
      {open ? <div id={bodyId} className="gk-panel-body">{children}</div> : null}
    </div>
  )
}

/** A tiny bar chart from real daily counts. Renders nothing it was not given. */
export function Spark ({ values = [], label, color = 'var(--gm-blue)' }) {
  const max = Math.max(1, ...values)
  const w = 100 / Math.max(1, values.length)
  return (
    <svg className="gk-spark" viewBox="0 0 100 40" preserveAspectRatio="none" role="img" aria-label={label}>
      {values.map((v, i) => {
        const h = (v / max) * 36
        return <rect key={i} x={i * w + w * 0.15} width={w * 0.7} y={40 - h} height={Math.max(h, v ? 1.5 : 0.4)}
          rx="0.6" fill={v ? color : 'var(--gm-card-line)'} />
      })}
    </svg>
  )
}

/** The delivery ladder for a project: each stage is yes, no or unknown. */
export function Steps ({ steps }) {
  return (
    <div className="gk-steps">
      {steps.map(s => (
        <span key={s.key} className={`gk-step gk-step--${s.state}`}
          title={s.state === 'unknown' ? 'Unable to verify automatically' : undefined}>
          {s.state === 'yes' ? '✓ ' : s.state === 'unknown' ? '? ' : '· '}{s.label}
        </span>
      ))}
    </div>
  )
}
