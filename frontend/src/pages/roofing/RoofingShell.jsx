/**
 * EvoSys Pro Roofing - the authenticated workspace shell (/program/roofing/*).
 *
 * Its own navigation (the roofing operating floor, not the platform rail),
 * mounted `bare` behind ProtectedRoute feature="roofing_program". The server
 * is the authority: /roofing-ops/status answers 403 for any workspace that
 * does not list the roofing feature explicitly, and that answer is shown.
 */
import { createContext, Suspense, useContext, useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api, getCurrentUser } from '../../api/client'
import { Avatar, ErrorNote, Icon, Loading, SampleTag, errText } from './ui'
import ProgramSwitchLink from '../../components/ProgramSwitchLink'
import './roofing.css'

const RoofingCtx = createContext(null)
export const useRoofing = () => useContext(RoofingCtx)

export const BASE = '/program/roofing'
const NAV = [
  { to: BASE, label: 'Mission Control', icon: 'home', end: true },
  { to: `${BASE}/queue`, label: 'AI Work Queue', icon: 'spark' },
  { to: `${BASE}/conversations`, label: 'Conversations', icon: 'chat', count: 'attention' },
  { to: `${BASE}/inspections`, label: 'Inspections', icon: 'calendar', count: 'upcoming' },
  { to: `${BASE}/performance`, label: 'Performance', icon: 'chart' },
  { to: `${BASE}/pipeline`, label: 'Pipeline', icon: 'pipeline' },
  { to: `${BASE}/leads`, label: 'Leads & Data', icon: 'users' },
  { to: `${BASE}/settings`, label: 'Roofing Setup', icon: 'settings' },
]

export function BrandMark({ className = 'rf-brand__mark' }) {
  return (
    <svg className={className} viewBox="0 0 40 40" aria-hidden="true">
      <path d="M4 22 20 8l16 14" fill="none" stroke="#f26a1b" strokeWidth="4.2" strokeLinecap="round" strokeLinejoin="round" />
      <path d="M10 25 20 16.5 30 25" fill="none" stroke="#f26a1b" strokeWidth="4.2" strokeLinecap="round" strokeLinejoin="round" opacity=".7" />
      <path d="M15.5 28.5 20 24.5l4.5 4" fill="none" stroke="#f26a1b" strokeWidth="4.2" strokeLinecap="round" strokeLinejoin="round" opacity=".45" />
    </svg>
  )
}

export function Brand({ dark = true }) {
  return (
    <span className="rf-brand" style={dark ? undefined : { color: 'var(--rf-ink)' }}>
      <BrandMark />
      <span>
        <span className="rf-brand__name">EvoSys <span>Pro</span></span>
        <span className="rf-brand__sub">ROOFING</span>
      </span>
    </span>
  )
}

function useFont() {
  useEffect(() => {
    if (document.getElementById('rf-font')) return
    const l = document.createElement('link')
    l.id = 'rf-font'
    l.rel = 'stylesheet'
    l.href = 'https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap'
    document.head.appendChild(l)
  }, [])
}

export default function RoofingShell() {
  useFont()
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [counts, setCounts] = useState({})
  const [open, setOpen] = useState(false)
  const loc = useLocation()
  const user = getCurrentUser()

  const loadStatus = async () => {
    try { setStatus(await api.get('/roofing-ops/status')); setError(null) } catch (e) { setError(e) }
  }
  const loadCounts = async () => {
    try {
      const m = await api.get('/roofing-ops/mission')
      setCounts({ attention: m.attention_total, upcoming: m.outcomes?.upcoming_inspections })
    } catch { /* counts are a convenience; the pages show their own errors */ }
  }
  useEffect(() => { loadStatus() }, [])
  useEffect(() => { loadCounts() }, [loc.pathname])
  useEffect(() => { setOpen(false) }, [loc.pathname])

  if (error) {
    const denied = error.status === 403 || error.status === 402
    return (
      <div className="rf rf-gate">
        <div className="rf-panel rf-gate__card">
          <Brand dark={false} />
          <h1 className="rf-h1">{denied ? 'Roofing isn’t turned on for this workspace' : 'Roofing couldn’t load'}</h1>
          <p className="rf-lede">{denied
            ? 'Switch to your roofing company’s workspace, or ask the platform owner to enable the Roofing program for it.'
            : errText(error)}</p>
          <div style={{ display: 'flex', gap: 10, marginTop: 18 }}>
            <a className="rf-btn rf-btn--ghost" href="/workspaces">Switch program</a>
            {!denied && <button className="rf-btn rf-btn--primary" onClick={loadStatus}>Try again</button>}
          </div>
        </div>
      </div>
    )
  }
  if (!status) return <div className="rf"><Loading label="Opening your roofing workspace" /></div>

  const p = status.profile || {}
  const ctx = { status, reloadStatus: loadStatus, reloadCounts: loadCounts, base: BASE }
  return (
    <RoofingCtx.Provider value={ctx}>
      <div className="rf rf-app" style={{ '--rf-company': p.color_primary }}>
        {open && <div className="rf-scrim" onClick={() => setOpen(false)} />}
        <aside className={`rf-side ${open ? 'open' : ''}`} aria-label="Roofing navigation">
          <Brand />
          <div className="rf-company">
            <b>{p.company_name}</b>
            {status.is_demo ? <SampleTag /> : <span>{status.can_manage ? 'Owner view' : 'Salesperson view'}</span>}
          </div>
          <nav className="rf-nav">
            {NAV.map((it) => (
              <NavLink key={it.to} to={it.to} end={it.end}>
                <Icon name={it.icon} size={19} />
                {it.label}
                {it.count && counts[it.count] > 0 && <span className="rf-nav__count">{counts[it.count]}</span>}
              </NavLink>
            ))}
          </nav>
          <div className="rf-side__foot">
            <a href="/roofing" target="_blank" rel="noreferrer"><Icon name="roof" size={16} />Public roofing page</a>
          </div>
        </aside>
        <div className="rf-main">
          <header className="rf-top">
            <button className="rf-btn rf-btn--ghost rf-btn--sm rf-top__menu" onClick={() => setOpen(true)} aria-label="Open navigation">
              <Icon name="menu" size={18} />
            </button>
            <span className="rf-top__title">{p.company_name}</span>
            {status.is_demo && <SampleTag />}
            <div className="rf-top__right">
              <ProgramSwitchLink />
              <span className={`rf-live ${status.texts_on ? 'on' : ''}`} title="Automated texting for this workspace">
                <span className="rf-live__dot" />{status.texts_on ? 'Automated texts on' : 'Automated texts off'}
              </span>
              <span className="rf-me">
                <Avatar name={status.me?.name || user?.full_name} size={34} />
                <div><b>{status.me?.name}</b><span>{status.can_manage ? 'Owner' : 'Salesperson'}</span></div>
              </span>
            </div>
          </header>
          <Suspense fallback={<Loading />}><Outlet /></Suspense>
        </div>
      </div>
    </RoofingCtx.Provider>
  )
}
