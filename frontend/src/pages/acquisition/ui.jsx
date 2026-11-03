/** Shared pieces for the acquisition client portal and operations (Issue #452). */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'

const PATHS = {
  dashboard: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  calendar: 'M5 5h14v15H5zM5 9h14M9 3v4M15 3v4',
  target: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8z',
  doc: 'M7 3h7l4 4v14H7zM14 3v4h4M10 12h5M10 16h5',
  contract: 'M6 3h12v18H6zM9 8h6M9 12h6M9 16h3',
  store: 'M4 9l2-5h12l2 5M5 9v11h14V9M9 20v-6h6v6',
  chat: 'M4 5h16v11H9l-5 4z',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM16 16l5 5',
  users: 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM2 21c0-4 3-6 7-6s7 2 7 6M17 11a3 3 0 1 0 0-6M22 20c0-3-2-5-5-5',
  mail: 'M3 6h18v12H3zM3 7l9 6 9-6',
  reply: 'M10 8 4 13l6 5M4 13h10a6 6 0 0 1 6 6',
  pipeline: 'M4 6h16M7 12h10M10 18h4',
  chart: 'M4 20V10M10 20V4M16 20v-8M22 20H2',
  heart: 'M3 12h4l2-5 4 10 2-5h6',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19 12l2-1-2-4-2 1-2-1-1-2h-4l-1 2-2 1-2-1-2 4 2 1v0l-2 1 2 4 2-1 2 1 1 2h4l1-2 2-1 2 1 2-4z',
  building: 'M4 21V5l8-2v18M12 7h8v14M7 8h2M7 12h2M7 16h2M15 11h2M15 15h2',
  handshake: 'M3 12l4-4 5 3 5-3 4 4-6 6-3-2-3 2zM8 15l2 2',
  check: 'M5 12l5 5 9-10',
  menu: 'M4 6h16M4 12h16M4 18h16',
  pause: 'M8 5v14M16 5v14',
  shield: 'M12 3l8 3v6c0 4.5-3.3 7.6-8 9-4.7-1.4-8-4.5-8-9V6z',
  spark: 'M12 3l2 6 6 2-6 2-2 6-2-6-6-2 6-2z',
  upload: 'M12 16V4M7 9l5-5 5 5M4 16v4h16v-4',
  database: 'M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3',
  flask: 'M9 3h6M10 3v6l-5 9a2 2 0 0 0 2 3h10a2 2 0 0 0 2-3l-5-9V3',
  share: 'M18 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 22a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8.6 13.5l6.8 4M15.4 6.5l-6.8 4',
  dollar: 'M12 2v20M17 6H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7v5l3 2',
  list: 'M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01',
  back: 'M15 18l-6-6 6-6',
  key: 'M14 10a4 4 0 1 0-4 4l1 1h2v2h2v2h3v-3l-4-4z',
}

export function Icon({ name, size = 18 }) {
  return (
    <svg viewBox="0 0 24 24" width={size} height={size} aria-hidden="true" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"><path d={PATHS[name] || PATHS.spark} /></svg>
  )
}

export function errText(e) {
  if (typeof e?.detail === 'string') return e.detail
  return e?.message || 'Something went wrong. Please try again.'
}

export function money(v, suffix = '') {
  if (v === null || v === undefined || v === '') return '—'
  return '$' + Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 }) + suffix
}

export function when(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function DateChip({ iso }) {
  const d = iso ? new Date(iso) : null
  if (!d || Number.isNaN(d.getTime())) return <span className="aq-date">—</span>
  return (
    <span className="aq-date">{d.toLocaleDateString([], { weekday: 'short' })}<br />
      {d.toLocaleDateString([], { month: 'short', day: 'numeric' })}</span>
  )
}

export function clock(iso) {
  const d = iso ? new Date(iso) : null
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '—'
}

export function initials(name) {
  return String(name || '').split(/\s+/).filter(w => /^[A-Za-z]/.test(w)).slice(0, 2).map(w => w[0]).join('').toUpperCase() || 'EV'
}

export function Pill({ stage, label }) {
  return <span className={`aq-pill aq-pill--${stage}`}>{label}</span>
}

export function apptPill(appt) {
  if (!appt) return null
  const ok = appt.status === 'confirmed' || appt.status === 'booked'
  return <span className={`aq-pill ${ok ? 'aq-pill--ok' : ''}`}>{ok ? 'Confirmed' : appt.status}</span>
}

export function useToast() {
  const [msg, setMsg] = useState(null)
  useEffect(() => { if (!msg) return undefined; const t = setTimeout(() => setMsg(null), 3200); return () => clearTimeout(t) }, [msg])
  return [msg ? <div className="aq-toast" role="status">{msg}</div> : null, setMsg]
}

export function Loading({ label = 'Loading…' }) {
  return <div className="aq-empty" aria-busy="true">{label}</div>
}

export function useLoad(path) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const load = async () => {
    if (!path) return
    try { setData(await api.get(path)); setError(null) } catch (e) { setError(e) }
  }
  useEffect(() => { load() }, [path])
  return [data, error, load]
}

export function Kpi({ icon, value, label, sub }) {
  return (
    <div className="aq-kpi">
      <div className="aq-kpi__icon"><Icon name={icon} size={19} /></div>
      <b>{value}</b><span>{label}</span>{sub && <small>{sub}</small>}
    </div>
  )
}

export function Logo() {
  return (
    <div className="aq-logo">
      <span className="aq-logo__mark"><svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="#fff" strokeWidth="2.4"
        strokeLinejoin="round"><path d="M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 3v18M4 7.5l16 9M20 7.5l-16 9" opacity=".9" /></svg></span>
      EvoSys Pro
    </div>
  )
}

export function useFont() {
  useEffect(() => {
    if (document.getElementById('aq-font')) return
    const l = document.createElement('link')
    l.id = 'aq-font'
    l.rel = 'stylesheet'
    l.href = 'https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap'
    document.head.appendChild(l)
  }, [])
}

// ── Acquisition Hub design system (#474) ──────────────────────────────────
// Shared by every Hub page so the product reads as one system.
export function PageHeader({ eyebrow, title, sub, actions }) {
  return (
    <div className="hb-head">
      <div>{eyebrow && <div className="hb-eyebrow">{eyebrow}</div>}
        <h1 className="hb-title">{title}</h1>{sub && <p className="hb-sub">{sub}</p>}</div>
      {actions && <div className="hb-head__actions">{actions}</div>}
    </div>
  )
}

export function Stat({ icon, label, value, sub, tone = 'violet', to }) {
  const body = (<>
    <span className={`hb-stat__icon hb-tone--${tone}`}><Icon name={icon} size={20} /></span>
    <span className="hb-stat__txt"><span className="hb-stat__label">{label}</span><b>{value ?? '—'}</b>{sub && <small>{sub}</small>}</span>
  </>)
  return to ? <a className={`hb-stat hb-stat--${tone}`} href={to}>{body}</a> : <div className={`hb-stat hb-stat--${tone}`}>{body}</div>
}

export function Funnel({ steps }) {
  return (
    <div className="hb-funnel" role="list">
      {steps.map(s => (
        <div key={s.key} className="hb-funnel__step" role="listitem"><b>{s.value}</b><span>{s.label}</span></div>
      ))}
    </div>
  )
}

export function Stepper({ steps, current }) {
  return (
    <ol className="hb-steps">
      {steps.map((s, i) => (
        <li key={s} className={i < current ? 'done' : i === current ? 'on' : ''}>
          <span className="hb-steps__dot">{i < current ? <Icon name="check" size={14} /> : i + 1}</span><span>{s}</span>
        </li>))}
    </ol>
  )
}

export function Dot({ tone = 'gray', children }) {
  return <span className={`hb-dot hb-dot--${tone}`}><i />{children}</span>
}

export function Empty({ icon = 'spark', title, text, action }) {
  return (
    <div className="hb-empty"><span className="hb-empty__icon"><Icon name={icon} size={22} /></span>
      <b>{title}</b>{text && <p>{text}</p>}{action}</div>
  )
}

export function Toolbar({ children }) {
  return <div className="hb-toolbar">{children}</div>
}

export function Drawer({ title, onClose, children }) {
  useEffect(() => {
    const k = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
  return (
    <div className="hb-drawer" role="dialog" aria-modal="true" aria-label={title}>
      <div className="hb-drawer__scrim" onClick={onClose} />
      <aside className="hb-drawer__panel">
        <div className="hb-drawer__head"><b>{title}</b><button className="aq-btn aq-btn--sm" onClick={onClose} aria-label="Close">Close</button></div>
        <div className="hb-drawer__body">{children}</div>
      </aside>
    </div>
  )
}

// An in-page confirmation (never a browser dialog).
export function Confirm({ text, confirmLabel = 'Confirm', danger, busy, onConfirm, onCancel, children }) {
  return (
    <div className={`hb-confirm ${danger ? 'hb-confirm--danger' : ''}`} role="alertdialog">
      <div><b>{text}</b>{children}</div>
      <div className="hb-confirm__btns"><button className="aq-btn aq-btn--sm" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className={`aq-btn aq-btn--sm ${danger ? 'aq-btn--danger' : 'aq-btn--primary'}`} onClick={onConfirm} disabled={busy}>{busy ? 'Working…' : confirmLabel}</button></div>
    </div>
  )
}

export function Feed({ items, empty = 'Nothing yet.' }) {
  if (!items?.length) return <div className="aq-empty">{empty}</div>
  return (
    <ul className="hb-feed">{items.map((x, i) => (
      <li key={i}><span className={`hb-feed__icon hb-tone--${x.tone || 'violet'}`}><Icon name={x.icon || 'spark'} size={16} /></span>
        <div><b>{x.title}</b>{x.sub && <span>{x.sub}</span>}</div><time>{x.at ? when(x.at) : ''}</time></li>))}</ul>
  )
}
