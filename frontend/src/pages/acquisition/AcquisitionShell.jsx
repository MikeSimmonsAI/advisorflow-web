/**
 * Managed client acquisition - the CLIENT's portal (/program/acquisition/*),
 * Issue #452. Shows interested prospects, appointments, outcomes, business
 * settings and feedback. No discovery, scoring, evidence or automation
 * controls are here - those live in God Mode (/god/acquisition).
 *
 * The server is the authority: /acquisition/status answers 403 for any
 * workspace that does not list `client_acquisition` explicitly.
 */
import { createContext, Suspense, useContext, useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api } from '../../api/client'
import ProgramSwitchLink from '../../components/ProgramSwitchLink'
import './acquisition.css'

const Ctx = createContext(null)
export const useAcq = () => useContext(Ctx)
export const BASE = '/program/acquisition'

const NAV = [
  { to: BASE, label: 'Home', end: true },
  { to: `${BASE}/prospects`, label: 'Interested businesses', count: 'interested' },
  { to: `${BASE}/appointments`, label: 'Appointments', count: 'upcoming' },
  { to: `${BASE}/results`, label: 'Results' },
  { to: `${BASE}/settings`, label: 'My business' },
  { to: `${BASE}/feedback`, label: 'Feedback' },
]

export function errText(e) {
  if (typeof e?.detail === 'string') return e.detail
  return e?.message || 'Something went wrong. Please try again.'
}

export function money(v) {
  if (v === null || v === undefined || v === '') return '—'
  return '$' + Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 })
}

export function when(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export function Pill({ stage, label }) {
  return <span className={`aq-pill aq-pill--${stage}`}>{label}</span>
}

export function useToast() {
  const [msg, setMsg] = useState(null)
  useEffect(() => { if (!msg) return undefined; const t = setTimeout(() => setMsg(null), 3200); return () => clearTimeout(t) }, [msg])
  return [msg ? <div className="aq-toast" role="status">{msg}</div> : null, setMsg]
}

export function Loading({ label = 'Loading…' }) {
  return <div className="aq-empty" aria-busy="true">{label}</div>
}

export default function AcquisitionShell() {
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [counts, setCounts] = useState({})
  const [open, setOpen] = useState(false)
  const loc = useLocation()

  const loadStatus = async () => {
    try { setStatus(await api.get('/acquisition/status')); setError(null) } catch (e) { setError(e) }
  }
  const loadCounts = async () => {
    try {
      const d = await api.get('/acquisition/dashboard')
      setCounts({ interested: d.new_interest?.length || 0, upcoming: d.upcoming?.length || 0 })
    } catch { /* counts are a convenience; pages show their own errors */ }
  }
  useEffect(() => { loadStatus() }, [])
  useEffect(() => { if (status) loadCounts() }, [loc.pathname, status])
  useEffect(() => { setOpen(false) }, [loc.pathname])

  if (error) {
    const denied = error.status === 403 || error.status === 402
    return (
      <div className="aq aq-gate">
        <div className="aq-card">
          <h1 className="aq-h1">{denied ? 'This workspace doesn’t have this program' : 'This page couldn’t load'}</h1>
          <p className="aq-lede">{denied ? 'Switch to your business workspace to see your appointments.' : errText(error)}</p>
          <div className="aq-actions">
            <a className="aq-btn" href="/workspaces">Switch program</a>
            {!denied && <button className="aq-btn aq-btn--primary" onClick={loadStatus}>Try again</button>}
          </div>
        </div>
      </div>
    )
  }
  if (!status) return <div className="aq"><Loading label="Opening your workspace…" /></div>

  const name = status.profile?.business_name || status.organization_name
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase()
  return (
    <Ctx.Provider value={{ status, reloadStatus: loadStatus, reloadCounts: loadCounts }}>
      <div className="aq aq-app">
        {open && <div className="aq-scrim" onClick={() => setOpen(false)} />}
        <aside className={`aq-side ${open ? 'open' : ''}`} aria-label="Navigation">
          <div className="aq-brand">
            <span className="aq-brand__mark">{initials || 'EV'}</span>
            <div><b>{name}</b><span>CLIENT ACQUISITION</span></div>
          </div>
          <nav className="aq-nav">
            {NAV.map(it => (
              <NavLink key={it.to} to={it.to} end={it.end}>
                {it.label}
                {it.count && counts[it.count] > 0 && <span className="aq-nav__count">{counts[it.count]}</span>}
              </NavLink>
            ))}
          </nav>
          <div className="aq-side__foot">Powered by EvoSys Pro.<br />We find and book the businesses; you meet them and close.</div>
        </aside>
        <div className="aq-main">
          <header className="aq-top">
            <button className="aq-btn aq-btn--sm aq-top__menu" onClick={() => setOpen(true)} aria-label="Open navigation">Menu</button>
            <span className="aq-top__title">{name}</span>
            <div className="aq-top__right">
              <ProgramSwitchLink />
              <span className="aq-pill">{status.me?.name}</span>
            </div>
          </header>
          <Suspense fallback={<Loading />}><Outlet /></Suspense>
        </div>
      </div>
    </Ctx.Provider>
  )
}
