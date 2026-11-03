/**
 * EvoSys Pro — CLEANING CLIENT PORTAL (/program/acquisition/*), Issue #452.
 *
 * Owner decision 2026-11-03: EvoSys sells BOOKED APPOINTMENTS. A cleaning
 * company sees the appointments EvoSys booked for it, reports what happened,
 * and asks EvoSys to follow up. It never sees EvoSys's prospect research,
 * outreach, unbooked interest or another client's records.
 *
 * The server is the authority: /acquisition/* returns only delivered
 * appointments, and a customer login is refused on every other API.
 */
import { createContext, Suspense, useContext, useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api } from '../../api/client'
import ProgramSwitchLink from '../../components/ProgramSwitchLink'
import { Icon, Loading, errText, initials, useFont } from './ui'
import './acquisition.css'
import './hub.css'

export { errText, money, when, Pill, useToast, Loading } from './ui'

const Ctx = createContext(null)
export const useAcq = () => useContext(Ctx)
export const BASE = '/program/acquisition'

const NAV = [
  { to: BASE, label: 'Dashboard', icon: 'dashboard', end: true },
  { to: `${BASE}/appointments`, label: 'Appointments', icon: 'calendar', count: 'upcoming' },
  { to: `${BASE}/results`, label: 'Results', icon: 'chart' },
  { to: `${BASE}/estimates`, label: 'Estimates & Contracts', icon: 'doc' },
  { to: `${BASE}/settings`, label: 'My Business', icon: 'store' },
  { to: `${BASE}/feedback`, label: 'Feedback & Support', icon: 'chat' },
]
const TITLES = Object.fromEntries(NAV.map(n => [n.to, n.label]))

export default function AcquisitionShell() {
  useFont()
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
      setCounts({ upcoming: d.upcoming?.length || 0 })
    } catch { /* counts are a convenience; pages show their own errors */ }
  }
  useEffect(() => { loadStatus() }, [])
  useEffect(() => { if (status) loadCounts() }, [loc.pathname, status])
  useEffect(() => { setOpen(false) }, [loc.pathname])

  if (error) {
    const denied = error.status === 403 || error.status === 402
    return (
      <div className="aq aq-dark aq-gate">
        <div className="aq-card">
          <h1 className="aq-h1">{denied ? 'This workspace doesn’t have the appointment portal' : 'This page couldn’t load'}</h1>
          <p className="aq-lede">{denied ? 'Switch to your cleaning company’s workspace to see your appointments.' : errText(error)}</p>
          <div className="aq-actions">
            <a className="aq-btn" href="/workspaces">Program Access</a>
            {!denied && <button className="aq-btn aq-btn--primary" onClick={loadStatus}>Try again</button>}
          </div>
        </div>
      </div>
    )
  }
  if (!status) return <div className="aq aq-dark"><Loading label="Opening your appointment portal…" /></div>

  const name = status.profile?.business_name || status.organization_name
  const me = status.me?.name || ''
  const page = TITLES[loc.pathname] || 'Appointment'
  return (
    <Ctx.Provider value={{ status, name, reloadStatus: loadStatus, reloadCounts: loadCounts }}>
      <div className="aq aq-app aq-dark">
        {open && <div className="aq-scrim" onClick={() => setOpen(false)} />}
        <aside className={`aq-side ${open ? 'open' : ''}`} aria-label="Client portal navigation">
          <div className="aq-logo"><span className="aq-logo__mark"><Icon name="spark" size={16} /></span>
            <span>EvoSys Pro<small>Client Portal</small></span></div>
          <nav className="aq-nav" style={{ marginTop: 10 }}>
            {NAV.map(it => (
              <NavLink key={it.to} to={it.to} end={it.end}>
                <Icon name={it.icon} />{it.label}
                {it.count && counts[it.count] > 0 && <span className="aq-nav__count">{counts[it.count]}</span>}
              </NavLink>
            ))}
          </nav>
          <div className="aq-help"><b>Need something?</b>Use “Request EvoSys Follow-Up” on any appointment, or Feedback &amp; Support.</div>
        </aside>
        <div className="aq-main">
          <header className="aq-top">
            <button className="aq-btn aq-btn--sm aq-top__menu" onClick={() => setOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button>
            <nav className="hb-crumbs" aria-label="Breadcrumb"><a href="/workspaces">Program Access</a><span>›</span>
              <span className="hb-crumb-mid">Client Portal</span><span className="hb-crumb-mid">›</span><b>{page}</b></nav>
            <div className="aq-top__right">
              <ProgramSwitchLink />
              <span className="aq-me"><span className="aq-avatar">{initials(name)}</span>
                <div><b>{name}</b><span>{me ? `${me.split(' ')[0]} · Client` : 'Client'}</span></div></span>
            </div>
          </header>
          <Suspense fallback={<Loading />}><Outlet /></Suspense>
        </div>
      </div>
    </Ctx.Provider>
  )
}
