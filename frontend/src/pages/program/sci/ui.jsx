/* Shared building blocks for the SCI Family Service Center screens (EvoSys Pro
   navy/gold system, 2026-10 command-center redesign). One set of components so
   the ten screens stay one visual system: page header band, KPI tiles, panels,
   chips, drawers, typed-confirm dialogs and toasts. Nothing here decides a
   send - every gate stays on the server. */
import { useEffect, useRef, useState } from 'react'
import { API_BASE } from '../../../api/client'
import landscapeUrl from './landscape.svg'

export const LANDSCAPE = landscapeUrl

export const errText = e => e?.detail || e?.message || 'Request failed'
export const num = n => (typeof n === 'number' ? n.toLocaleString() : '—')
export const pct = (part, whole) => (whole ? Math.round((1000 * part) / whole) / 10 : 0)
export const when = iso => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
// Appointment times are SCI calendar (Central) wall times sent with their
// offset: always shown in Central with the zone, on any device.
export const APPT_TZ = 'America/Chicago'
export const apptWhen = iso => {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString([], { timeZone: APPT_TZ, month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })
}
export const ago = iso => {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const m = Math.round((Date.now() - d.getTime()) / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.round(m / 60)
  if (h < 24) return `${h}h ago`
  const days = Math.round(h / 24)
  return days < 8 ? `${days}d ago` : d.toLocaleDateString([], { month: 'short', day: 'numeric' })
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
  dashboard: 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  send: 'M22 2 11 13M22 2l-7 20-4-9-9-4z',
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
  users: 'M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM23 21v-2a4 4 0 0 0-3-3.9M16 3.1a4 4 0 0 1 0 7.8',
  check: 'M22 11.1V12a10 10 0 1 1-5.9-9.1M22 4 12 14l-3-3',
  alert: 'M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0zM12 9v4M12 17h.01',
  calendar: 'M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM16 2v4M8 2v4M3 10h18',
  pin: 'M12 21s-7-6.2-7-11.5A7 7 0 0 1 19 9.5C19 14.8 12 21 12 21zM12 12a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5z',
  chevron: 'M9 18l6-6-6-6',
  down: 'M6 9l6 6 6-6',
  arrow: 'M5 12h14M12 5l7 7-7 7',
  x: 'M18 6 6 18M6 6l12 12',
  mail: 'M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM22 6l-10 7L2 6',
  sms: 'M21 11.5a8.4 8.4 0 0 1-9 8.4 8.5 8.5 0 0 1-3.8-.9L3 21l1.9-5.7A8.4 8.4 0 0 1 12 3a8.5 8.5 0 0 1 9 8.5zM8 11h.01M12 11h.01M16 11h.01',
  voicemail: 'M5.5 16a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zM18.5 16a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zM5.5 16h13',
  database: 'M12 8c5 0 9-1.3 9-3s-4-3-9-3-9 1.3-9 3 4 3 9 3zM21 12c0 1.7-4 3-9 3s-9-1.3-9-3M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5',
  copy: 'M20 9h-9a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2zM5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1',
  refresh: 'M23 4v6h-6M1 20v-6h6M3.5 9a9 9 0 0 1 14.9-3.4L23 10M1 14l4.6 4.4A9 9 0 0 0 20.5 15',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  lock: 'M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2zM7 11V7a5 5 0 0 1 10 0v4',
  play: 'M5 3l14 9-14 9V3z',
  image: 'M3 5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2zM8.5 10a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3zM21 15l-5-5L5 21',
  star: 'M12 2l3.1 6.3 6.9 1-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1z',
  clock: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 6v6l4 2',
  external: 'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3',
  collapse: 'M11 17l-5-5 5-5M18 17l-5-5 5-5',
  expand: 'M13 17l5-5-5-5M6 17l5-5-5-5',
  globe: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z',
  link: 'M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7',
  bolt: 'M13 2 3 14h9l-1 8 10-12h-9z',
  inbox: 'M22 12h-6l-2 3h-4l-2-3H2M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1z',
  filter: 'M22 3H2l8 9.5V19l4 2v-8.5z',
  pause: 'M6 4h4v16H6zM14 4h4v16h-4z',
  edit: 'M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7M18.5 2.5a2.1 2.1 0 0 1 3 3L12 15l-4 1 1-4z',
  user: 'M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2M12 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  ban: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM4.9 4.9l14.2 14.2',
  info: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM12 16v-4M12 8h.01',
}

export function Icon({ name, title, size }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round"
      width={size} height={size} aria-hidden={title ? undefined : 'true'} role={title ? 'img' : undefined} focusable="false">
      {title && <title>{title}</title>}
      <path d={PATHS[name] || ''} />
    </svg>
  )
}

/** The navy page header band. Replaces the old serif heading and the big hero. */
export function PageHead({ title, sub, children, eyebrow = 'Family Service Center · EvoSys Pro' }) {
  return (
    <header className="sci-head">
      <div className="sci-head-text">
        <p className="sci-eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        {sub && <p className="sci-head-sub">{sub}</p>}
      </div>
      {children && <div className="sci-head-actions">{children}</div>}
    </header>
  )
}

const MARK_SKIP = new Set(['funeral', 'home', 'homes', 'cemetery', 'memorial', 'park', 'gardens', 'garden', 'chapel', 'mortuary',
  'the', 'of', 'and', '&', 'funeral-home', 'parlors', 'center', 'care', 'mausoleum'])
/** Two letters for a location: its distinctive words, not "Funeral Home". */
export const locationInitials = name => {
  const words = String(name || '').replace(/[()]/g, ' ').split(/[\s\-]+/).filter(Boolean)
  const key = words.filter(w => !MARK_SKIP.has(w.toLowerCase()) && /^[A-Za-z]/.test(w))
  const use = (key.length ? key : words).slice(0, 2)
  return use.map(w => w[0]).join('').toUpperCase() || '?'
}

/** The location's own mark: its uploaded logo, otherwise its initials. Never a corporate badge. */
export function LocationMark({ name, logoUrl, size = 38 }) {
  return (
    <span className="sci-identity-mark" style={{ width: size, height: size }} aria-hidden="true" title={name}>
      {logoUrl ? <img src={assetSrc(logoUrl)} alt="" style={{ width: '100%', height: '100%', objectFit: 'contain', borderRadius: 8, background: '#fff' }} /> : locationInitials(name)}
    </span>
  )
}

export function Chip({ tone = '', children, plain = false, title }) {
  return <span className={`sci-chip ${tone}${plain ? ' plain' : ''}`} title={title}>{children}</span>
}

/** A KPI tile: tinted icon, big number, label, optional note or meter.
    With onClick it is a button that opens the matching filtered view. */
export function Kpi({ icon, tone = 'blue', value, label, note, meter, onClick, title }) {
  const body = (
    <>
      <span className={`sci-kpi-icon ${tone}`} aria-hidden="true"><Icon name={icon} /></span>
      <span className="sci-kpi-body">
        <span className="sci-kpi-num">{typeof value === 'number' ? num(value) : (value ?? '—')}</span>
        <span className="sci-kpi-label">{label}</span>
        {meter != null && <span className="sci-kpi-meter" aria-hidden="true"><i className={tone} style={{ width: `${Math.max(0, Math.min(100, meter))}%` }} /></span>}
        {note && <span className="sci-kpi-note">{note}</span>}
      </span>
      {onClick && <span className="sci-kpi-go" aria-hidden="true"><Icon name="chevron" /></span>}
    </>
  )
  return onClick
    ? <button type="button" className="sci-kpi" onClick={onClick} title={title}>{body}</button>
    : <div className="sci-kpi" title={title}>{body}</div>
}

/** Older call sites: a plain KPI card. Kept so no screen loses its numbers. */
export function Metric({ label, value, note, tone = '', onClick }) {
  const iconTone = { bad: 'red', warn: 'gold', ok: 'green', info: 'blue' }[tone] || 'slate'
  return <Kpi icon="info" tone={iconTone} value={value} label={label} note={note} onClick={onClick} />
}

export function Empty({ title, children, icon, action }) {
  return (
    <div className="sci-empty">
      {icon && <span className="sci-empty-icon" aria-hidden="true"><Icon name={icon} /></span>}
      {title && <strong>{title}</strong>}
      {children && <span>{children}</span>}
      {action}
    </div>
  )
}

export function Loading({ label = 'Loading…' }) {
  return <p className="sci-loading" role="status"><span className="sci-spinner" aria-hidden="true" />{label}</p>
}

export function ErrorLine({ text }) {
  return text ? <div className="sci-alert" role="alert"><Icon name="alert" />{text}</div> : null
}

export function Banner({ tone = '', title, children, action, icon }) {
  return (
    <div className={`sci-banner ${tone}`} role={tone === 'bad' ? 'alert' : undefined}>
      {icon && <span className="sci-banner-icon" aria-hidden="true"><Icon name={icon} /></span>}
      <div className="sci-banner-body">{title && <strong>{title}</strong>}{children}</div>
      {action}
    </div>
  )
}

export function Panel({ title, aside, children, pad = true, className = '', labelledBy, style }) {
  return (
    <section className={`sci-panel ${pad ? 'sci-pad' : ''} ${className}`} aria-labelledby={title ? labelledBy : undefined} style={style}>
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

/** "View all →" style text action used in panel headers. */
export function MoreLink({ onClick, children = 'View all' }) {
  return <button type="button" className="sci-more" onClick={onClick}>{children}<Icon name="arrow" /></button>
}

/** Labeled input with inline validation. */
export function Field({ label, hint, error, full, children }) {
  return (
    <label className={`sci-field${full ? ' full' : ''}${error ? ' invalid' : ''}`}>
      <span className="sci-field-label">{label}</span>
      {children}
      {hint && !error && <span className="sci-hint">{hint}</span>}
      {error && <span className="sci-err">{error}</span>}
    </label>
  )
}

/** Pill filter group (aria-pressed buttons). items: [key, label, count?] */
export function FilterChips({ items, value, onChange, label }) {
  return (
    <div className="sci-filterchips" role="group" aria-label={label}>
      {items.map(([k, text, n]) => (
        <button key={k} type="button" aria-pressed={value === k} onClick={() => onChange(k)}>
          {text}{n != null && <span className="n">{num(n)}</span>}
        </button>
      ))}
    </div>
  )
}

/** In-panel tab bar with roving aria-selected. items: [key, label] */
export function Tabs({ items, value, onChange, label }) {
  return (
    <div className="sci-tabs" role="tablist" aria-label={label}>
      {items.map(([k, text]) => (
        <button key={k} type="button" role="tab" aria-selected={value === k} tabIndex={value === k ? 0 : -1}
          onClick={() => onChange(k)}
          onKeyDown={e => {
            const i = items.findIndex(x => x[0] === value)
            if (e.key === 'ArrowRight') onChange(items[(i + 1) % items.length][0])
            if (e.key === 'ArrowLeft') onChange(items[(i - 1 + items.length) % items.length][0])
          }}>
          {text}
        </button>
      ))}
    </div>
  )
}

/** Search box used in panels and the top bar. */
export function SearchBox({ id, value, onChange, placeholder, label = 'Search', width }) {
  return (
    <div className="sci-search" style={width ? { width } : undefined}>
      <Icon name="search" />
      <label htmlFor={id} className="sci-sr">{label}</label>
      <input id={id} type="search" value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} />
    </div>
  )
}

function useEscape(onClose, active = true) {
  useEffect(() => {
    if (!active) return undefined
    const h = e => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', h)
    return () => document.removeEventListener('keydown', h)
  }, [onClose, active])
}

/** Right-side slide-over inspector. Focus moves in on open and returns on close. */
export function Drawer({ open, title, sub, onClose, children, footer, width = 460 }) {
  const ref = useRef(null)
  const back = useRef(null)
  useEscape(onClose, open)
  useEffect(() => {
    if (open) { back.current = document.activeElement; setTimeout(() => ref.current?.focus(), 0) }
    else if (back.current?.focus) back.current.focus()
  }, [open])
  if (!open) return null
  return (
    <div className="sci-drawer-wrap">
      <button type="button" className="sci-drawer-scrim" aria-label="Close panel" onClick={onClose} tabIndex={-1} />
      <aside className="sci-drawer" role="dialog" aria-modal="true" aria-label={title} style={{ '--w': `${width}px` }} ref={ref} tabIndex={-1}>
        <div className="sci-drawer-head">
          <div style={{ minWidth: 0 }}>
            <h2>{title}</h2>
            {sub && <div className="sci-micro sci-muted">{sub}</div>}
          </div>
          <button type="button" className="sci-iconbtn ghost" aria-label="Close" onClick={onClose}><Icon name="x" /></button>
        </div>
        <div className="sci-drawer-body">{children}</div>
        {footer && <div className="sci-drawer-foot">{footer}</div>}
      </aside>
    </div>
  )
}

/** Modal that requires typing an exact phrase before the action is allowed.
    The server still enforces the same phrase; this only collects it. */
export function TypedConfirm({ open, title, children, phrase, actionLabel, tone = 'primary', busy, onCancel, onConfirm }) {
  const [text, setText] = useState('')
  const inputRef = useRef(null)
  useEscape(onCancel, open)
  useEffect(() => { if (open) { setText(''); setTimeout(() => inputRef.current?.focus(), 0) } }, [open])
  if (!open) return null
  const ok = phrase == null || text === phrase
  return (
    <div className="sci-modal-wrap" role="presentation">
      <button type="button" className="sci-drawer-scrim" aria-label="Cancel" onClick={onCancel} tabIndex={-1} />
      <div className="sci-modal" role="alertdialog" aria-modal="true" aria-labelledby="sci-modal-title">
        <h2 id="sci-modal-title">{title}</h2>
        <div className="sci-modal-body">{children}</div>
        <form onSubmit={e => { e.preventDefault(); if (ok && !busy) onConfirm(text) }}>
          {phrase != null && (
            <label className="sci-field" style={{ marginTop: 12 }}>
              <span className="sci-field-label">Type <b className="sci-phrase">{phrase}</b> to confirm</span>
              <input ref={inputRef} value={text} onChange={e => setText(e.target.value)} autoComplete="off" spellCheck={false} />
            </label>
          )}
          <div className="sci-modal-foot">
            <button type="button" className="sci-btn" onClick={onCancel} disabled={busy}>Cancel</button>
            <button type="submit" className={`sci-btn ${tone}`} disabled={!ok || busy}>{busy ? 'Working…' : actionLabel}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

/** Small status line for success/failure feedback, announced politely. */
export function Notice({ tone = 'ok', text, onClose }) {
  if (!text) return null
  return (
    <div className={tone === 'ok' ? 'sci-okmsg' : 'sci-alert'} role={tone === 'ok' ? 'status' : 'alert'}>
      <Icon name={tone === 'ok' ? 'check' : 'alert'} />
      <span style={{ flex: 1 }}>{text}</span>
      {onClose && <button type="button" className="sci-x" aria-label="Dismiss" onClick={onClose}><Icon name="x" /></button>}
    </div>
  )
}

/** Simple horizontal bar row used by pipeline and breakdowns. */
export function BarRow({ label, value, total, tone = 'teal', onClick }) {
  const share = pct(value, total)
  const inner = (
    <>
      <span className="sci-bar-label">{label}</span>
      <span className="sci-bar-track" aria-hidden="true"><i className={tone} style={{ width: `${share}%` }} /></span>
      <span className="sci-bar-val">{num(value)} <small>({share}%)</small></span>
    </>
  )
  return onClick
    ? <button type="button" className="sci-bar" onClick={onClick}>{inner}</button>
    : <div className="sci-bar">{inner}</div>
}

/** Key/value definition list used in inspectors. */
export function KV({ rows }) {
  return (
    <dl className="sci-kvlist">
      {rows.filter(Boolean).map(([k, v]) => (
        <div key={k}><dt>{k}</dt><dd className={v == null || v === '' ? 'missing' : ''}>{v == null || v === '' ? 'Not provided' : v}</dd></div>
      ))}
    </dl>
  )
}
