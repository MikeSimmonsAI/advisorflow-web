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
import { NavLink, Navigate, Outlet, useLocation } from 'react-router-dom'
import { api, getAcqClientView } from '../../api/client'
import ProgramSwitchLink from '../../components/ProgramSwitchLink'
import { Icon, Loading, errText, initials, useFont } from './ui'
import './acquisition.css'
import './hub.css'
import './teal.css'
import { useT } from './portalText'

export { errText, money, when, Pill, useToast, Loading } from './ui'

const Ctx = createContext(null)
export const useAcq = () => useContext(Ctx)
export const BASE = '/program/acquisition'

const NAV = [
  { to: BASE, label: 'Dashboard', k: 'nav_dashboard', tab: 'tab_home', icon: 'dashboard', end: true },
  { to: `${BASE}/appointments`, label: 'Appointments', k: 'nav_appointments', tab: 'tab_appts', icon: 'calendar', count: 'upcoming' },
  { to: `${BASE}/results`, label: 'Results', k: 'nav_results', tab: 'tab_results', icon: 'chart' },
  { to: `${BASE}/estimates`, label: 'Estimates & Contracts', k: 'nav_estimates', icon: 'doc' },
  { to: `${BASE}/settings`, label: 'My Business', k: 'nav_business', tab: 'tab_business', icon: 'store' },
  { to: `${BASE}/feedback`, label: 'Support', k: 'nav_support', tab: 'tab_support', icon: 'headset' },
]
// Service Operations (#452/#506): only when EvoSys switched it on for this
// company, and only the screens this person's role may use (the server
// enforces the same rules on every request).
const OPS_NAV = [
  { to: `${BASE}/ops/overview`, label: 'Operations', icon: 'heart', any: ['reports', 'schedule'] },
  { to: `${BASE}/ops/customers`, label: 'Customers & Locations', icon: 'building', any: ['view_all'] },
  { to: `${BASE}/ops/jobs`, label: 'Jobs', icon: 'list', any: ['view_all', 'view_team', 'schedule'] },
  { to: `${BASE}/ops/schedule`, label: 'Schedule', icon: 'calendar', any: ['view_all'] },
  { to: `${BASE}/ops/team`, label: 'Team', icon: 'users', any: ['team', 'schedule'] },
  { to: `${BASE}/ops/history`, label: 'Service History', icon: 'check', any: ['view_all'] },
  { to: `${BASE}/ops/invoices`, label: 'Invoices', icon: 'dollar', any: ['billing'] },
]
const WORKER_ROLES = ['field_worker', 'team_lead']
const TITLES = Object.fromEntries([...NAV, ...OPS_NAV].map(n => [n.to, n.label]))

export default function AcquisitionShell() {
  useFont()
  const { t, lang, setLang } = useT()
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [counts, setCounts] = useState({})
  const [open, setOpen] = useState(false)
  const [ops, setOps] = useState(undefined)
  const loc = useLocation()
  // An EvoSys Acquisition admin looking through this company's portal.
  const adminView = getAcqClientView()
  const leaveAdminView = async () => {
    const { exitAcquisitionClient } = await import('../god/enterCustomer')
    await exitAcquisitionClient()
    window.location.href = '/acquisition-ops/clients'
  }

  const loadStatus = async () => {
    try { setStatus(await api.get('/acquisition/status')); setError(null) } catch (e) { setError(e) }
  }
  useEffect(() => { api.get('/service-ops/me').then(setOps).catch(() => setOps(null)) }, [])
  const loadCounts = async () => {
    try {
      const d = await api.get('/acquisition/dashboard')
      setCounts({ upcoming: d.upcoming?.length || 0 })
    } catch { /* counts are a convenience; pages show their own errors */ }
  }
  useEffect(() => { loadStatus() }, [])
  useEffect(() => { if (status) loadCounts() }, [loc.pathname, status])
  useEffect(() => { setOpen(false) }, [loc.pathname])

  // Field staff and the company's own customers have their own simple views.
  if (error && error.status === 403 && ops === undefined) return <div className="aq aq-dark"><Loading /></div>
  if (error && error.status === 403 && ops?.is_buyer) return <Navigate to={`${BASE}/customer`} replace />
  if (error && error.status === 403 && ops?.role && WORKER_ROLES.includes(ops.role)) return <Navigate to={`${BASE}/work`} replace />
  const opsOnly = !!(error && error.status === 403 && ops?.role)
  if (error && !opsOnly) {
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
  if (!status && !opsOnly) return <div className="aq aq-dark"><Loading label="Opening your appointment portal…" /></div>
  if (opsOnly && loc.pathname.replace(/\/$/, '') === BASE) return <Navigate to={`${BASE}/ops/jobs`} replace />

  const opsNav = ops?.ops_enabled && ops?.role ? OPS_NAV.filter(n => n.any.some(p => ops.perms.includes(p))) : []
  const name = status?.profile?.business_name || status?.organization_name || 'Service Operations'
  const me = status?.me?.name || ''
  const page = TITLES[loc.pathname] || (loc.pathname.endsWith('/setup') ? 'Setup' : loc.pathname.includes('/ops/jobs/') ? 'Job' : 'Appointment')
  const logo = status?.logo_url
  const main = opsOnly ? [] : NAV
  return (
    <Ctx.Provider value={{ status, name, ops, reloadStatus: loadStatus, reloadCounts: loadCounts }}>
      <div className="aq aq-app aq-dark aq-client">
        {open && <div className="aq-scrim" onClick={() => setOpen(false)} />}
        <aside className={`aq-side ${open ? 'open' : ''}`} aria-label="Client portal navigation">
          <div className="cp-brand">
            {logo ? <img src={logo} alt="" className="cp-brand__logo" /> : <span className="cp-initials cp-initials--lg" aria-hidden="true">{initials(name)}</span>}
            <b>{name}</b><small>{t('powered')}</small>
          </div>
          <nav className="aq-nav">
            {main.map(it => (
              <NavLink key={it.to} to={it.to} end={it.end}>
                <Icon name={it.icon} />{t(it.k)}
                {it.count && counts[it.count] > 0 && <span className="aq-nav__count">{counts[it.count]}</span>}
              </NavLink>
            ))}
          </nav>
          {(opsNav.length > 0 || ops?.member_id) && <div className="cp-more">
            <div className="cp-more__title"><Icon name="tools" size={16} />{t('nav_more')}</div>
            <nav className="aq-nav">
              {opsNav.map(it => <NavLink key={it.to} to={it.to}><Icon name={it.icon} />{it.label}</NavLink>)}
              {ops?.member_id && <NavLink to={`${BASE}/work`}><Icon name="clock" />My Jobs</NavLink>}
            </nav></div>}
          <div className="cp-powered">{t('powered')}</div>
        </aside>
        <div className="aq-main">
          <header className="aq-top">
            <button className="aq-btn aq-btn--sm aq-top__menu" onClick={() => setOpen(true)} aria-label="Open navigation"><Icon name="menu" /></button>
            <nav className="hb-crumbs" aria-label="Breadcrumb"><a href="/workspaces">Program Access</a><span>›</span><b>{page}</b></nav>
            <div className="aq-top__right">
              <div className="cp-lang" role="group" aria-label="Language">
                <button className={lang === 'en' ? 'on' : ''} onClick={() => setLang('en')}>English</button>
                <button className={lang === 'es' ? 'on' : ''} onClick={() => setLang('es')}>Español</button>
              </div>
              <ProgramSwitchLink />
              <span className="aq-me"><span className="aq-avatar">{initials(name)}</span>
                <div><b>{me || name}</b><span>{name}</span></div></span>
            </div>
          </header>
          {adminView && <div className="aq-adminview" role="status">
            <span>EvoSys admin view — <b>{adminView.orgName}</b>. You see what this company sees.</span>
            <button className="aq-btn aq-btn--sm" onClick={leaveAdminView}>Return to Acquisition Control Center</button></div>}
          <Suspense fallback={<Loading />}><Outlet /></Suspense>
          {!opsOnly && <nav className="cp-tabs" aria-label="Client portal">
            {NAV.filter(n => n.tab).map(it => <NavLink key={it.to} to={it.to} end={it.end}><Icon name={it.icon} size={20} /><span>{t(it.tab)}</span></NavLink>)}
          </nav>}
        </div>
      </div>
    </Ctx.Provider>
  )
}
