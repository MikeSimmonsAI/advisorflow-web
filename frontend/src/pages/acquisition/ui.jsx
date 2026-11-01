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
