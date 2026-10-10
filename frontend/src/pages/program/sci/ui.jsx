/* Shared building blocks for the SCI Family Service Center screens.
   One set of components so the nine screens stay one visual system. */
import { API_BASE } from '../../../api/client'
import landscapeUrl from './landscape.svg'

export const LANDSCAPE = landscapeUrl

export const errText = e => e?.detail || e?.message || 'Request failed'
export const num = n => (typeof n === 'number' ? n.toLocaleString() : '—')
export const when = iso => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
export const assetSrc = url => (url ? `${API_BASE}${url}` : null)
export const initials = name => {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean)
  if (!parts.length) return '?'
  return ((parts[0][0] || '') + (parts.length > 1 ? parts[parts.length - 1][0] : '')).toUpperCase()
}
export const CLASS_LABEL = { hot: 'HOT', active: 'ACTIVE', low: 'LOW', opt_out: 'OPT-OUT', bad_data: 'BAD DATA', wrong_person: 'WRONG PERSON' }
export const CLASS_TONE = { hot: 'bad', active: 'info', low: '', opt_out: 'warn', bad_data: 'warn', wrong_person: 'warn' }
export const STATUS_TONE = { ok: 'ok', warn: 'warn', fail: 'bad', off: '' }

const PATHS = {
  dashboard: 'M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z',
  responses: 'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  review: 'M9 11l3 3L22 4M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11',
  locations: 'M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21zM12 12a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
  campaigns: 'M3 11l18-8v18L3 13zM11.6 16.8a3 3 0 1 1-5.8-1.6',
  assets: 'M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM8.5 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM21 15l-5-5L5 21',
  launch: 'M4.5 16.5c-1.5 1.3-2 5-2 5s3.7-.5 5-2c.7-.8.7-2.1-.1-2.9a2.2 2.2 0 0 0-2.9-.1zM12 15l-3-3a22 22 0 0 1 2-3.9A12.9 12.9 0 0 1 22 2c0 2.7-.8 7.5-6 11a22.4 22.4 0 0 1-4 2zM9 12H4s.6-3 2-4c1.6-1.1 5 0 5 0M12 15v5s3-.6 4-2c1.1-1.6 0-5 0-5',
  health: 'M22 12h-4l-3 9L9 3l-3 9H2',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  search: 'M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16zM21 21l-4.3-4.3',
  bell: 'M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0',
  menu: 'M3 6h18M3 12h18M3 18h18',
  back: 'M19 12H5M12 19l-7-7 7-7',
  doc: 'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M8 13h8M8 17h8',
  phone: 'M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z',
  upload: 'M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12',
}

export function Icon({ name, title }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden={title ? undefined : 'true'} role={title ? 'img' : undefined}>
      {title && <title>{title}</title>}
      <path d={PATHS[name] || ''} />
    </svg>
  )
}

export function PageHead({ title, sub, children }) {
  return (
    <header className="sci-head">
      <div>
        <p className="sci-eyebrow">SCI / Family Service Center</p>
        <h1>{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {children && <div className="sci-head-actions">{children}</div>}
    </header>
  )
}

export function Chip({ tone = '', children, plain = false }) {
  return <span className={`sci-chip ${tone}${plain ? ' plain' : ''}`}>{children}</span>
}

/** A KPI card. With onClick it is a button that opens the matching filtered view. */
export function Metric({ label, value, note, tone = '', onClick }) {
  const body = (
    <>
      <div className="sci-metric-label">{label}</div>
      <div className={`sci-metric-num ${tone}`}>{typeof value === 'number' ? num(value) : (value ?? '—')}</div>
      {note && <div className="sci-metric-note">{note}</div>}
    </>
  )
  return onClick
    ? <button type="button" className="sci-metric" onClick={onClick}>{body}</button>
    : <div className="sci-metric">{body}</div>
}

export function Empty({ title, children }) {
  return <div className="sci-empty">{title && <strong>{title}</strong>}{children}</div>
}

export function Loading({ label = 'Loading…' }) {
  return <p className="sci-loading" role="status">{label}</p>
}

export function ErrorLine({ text }) {
  return text ? <div className="sci-alert" role="alert">{text}</div> : null
}

export function Banner({ tone = '', title, children, action }) {
  return (
    <div className={`sci-banner ${tone}`} role={tone === 'bad' ? 'alert' : undefined}>
      <div>{title && <strong>{title}</strong>}{children}</div>
      {action}
    </div>
  )
}

export function Panel({ title, aside, children, pad = true, className = '', labelledBy }) {
  return (
    <section className={`sci-panel ${pad ? 'sci-pad' : ''} ${className}`} aria-labelledby={labelledBy}>
      {(title || aside) && (
        <div className="sci-panel-head">
          {title && <h2 id={labelledBy}>{title}</h2>}
          {aside}
        </div>
      )}
      {children}
    </section>
  )
}

/** Labeled input with inline validation. */
export function Field({ label, hint, error, full, children }) {
  return (
    <label className={`sci-field${full ? ' full' : ''}${error ? ' invalid' : ''}`}>
      {label}
      {children}
      {hint && !error && <span className="sci-hint">{hint}</span>}
      {error && <span className="sci-err">{error}</span>}
    </label>
  )
}
