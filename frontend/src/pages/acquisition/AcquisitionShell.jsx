/**
 * Managed client acquisition - the CLIENT's portal (/program/acquisition/*),
 * Issue #452, in the owner-approved purple theme. Shows interested prospects,
 * appointments, estimates, contracts, business settings and feedback. No
 * discovery, scoring, evidence or automation controls are here - those live
 * in the operations area (/acquisition-ops).
 *
 * The server is the authority: /acquisition/status answers 403 for any
 * workspace that does not list `client_acquisition` explicitly.
 */
import { createContext, Suspense, useContext, useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api } from '../../api/client'
import ProgramSwitchLink from '../../components/ProgramSwitchLink'
import { Icon, Loading, Logo, errText, initials, useFont } from './ui'
import './acquisition.css'

export { errText, money, when, Pill, useToast, Loading } from './ui'

const Ctx = createContext(null)
export const useAcq = () => useContext(Ctx)
export const BASE = '/program/acquisition'

const NAV = [
  { to: BASE, label: 'Dashboard', icon: 'dashboard', end: true },
  { to: `${BASE}/appointments`, label: 'Appointments', icon: 'calendar', count: 'upcoming' },
  { to: `${BASE}/prospects`, label: 'Opportunities', icon: 'target', count: 'interested' },
  { to: `${BASE}/estimates`, label: 'Estimates', icon: 'doc' },
  { to: `${BASE}/results`, label: 'Contracts', icon: 'contract' },
  { to: `${BASE}/settings`, label: 'My Business', icon: 'store' },
  { to: `${BASE}/feedback`, label: 'Feedback', icon: 'chat' },
]

export function PilotBadge({ status }) {
  if (status.pilot_day) {
    return <span className="aq-badge"><Icon name="calendar" size={15} />Pilot (Day {status.pilot_day} of 30)</span>
  }
  return <span className="aq-badge aq-badge--off"><Icon name="calendar" size={15} />Pilot not started</span>
}

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
  const me = status.me?.name || ''
  return (
    <Ctx.Provider value={{ status, reloadStatus: loadStatus, reloadCounts: loadCounts }}>
      <div className="aq aq-app">
        {open && <div className="aq-scrim" onClick={() => setOpen(false)} />}
        <aside className={`aq-side ${open ? 'open' : ''}`} aria-label="Navigation">
          <Logo />
          <div className="aq-side__label">Program Access</div>
          <nav className="aq-nav">
            {NAV.map(it => (
              <NavLink key={it.to} to={it.to} end={it.end}>
                <Icon name={it.icon} />{it.label}
                {it.count && counts[it.count] > 0 && <span className="aq-nav__count">{counts[it.count]}</span>}
              </NavLink>
            ))}
          </nav>
          <div className="aq-help"><b>Need help?</b>Send us a note on the Feedback page and your EvoSys team will get back to you.</div>
        </aside>
        <div className="aq-main">
          <header className="aq-top">
            <button className="aq-btn aq-btn--sm aq-top__menu" onClick={() => setOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button>
            <span className="aq-top__title">{name}</span>
            <div className="aq-top__right">
              <ProgramSwitchLink />
              <span className="aq-me"><span className="aq-avatar">{initials(me)}</span>
                <div><b>{me.split(' ')[0]}</b><span>Client</span></div></span>
            </div>
          </header>
          <div className="aq-band">
            <span className="aq-band__mark">{initials(name)}</span>
            <div><h1>{name}</h1><p>Client Portal • Commercial Cleaning Growth Program</p></div>
            <div className="aq-band__right"><PilotBadge status={status} /></div>
          </div>
          <Suspense fallback={<Loading />}><Outlet /></Suspense>
        </div>
      </div>
    </Ctx.Provider>
  )
}
