/**
 * EvoSys Gold & Jewelry - the authenticated workspace shell (/program/jewelry/*).
 *
 * Mounted `bare` behind ProtectedRoute. The server is the authority:
 * /jewelry-ops/status answers 403 for any workspace that does not list the
 * jewelry_program feature explicitly, and that answer is shown.
 *
 * "← Back to Program Access" is a plain link to /workspaces on every screen,
 * desktop and mobile - it does not depend on browser history. It sits in the
 * header's right-hand action area, before the signed-in person, which is where
 * every other EvoSys program puts its way back to the launcher.
 *
 * The menu button exists only on narrow screens, where the sidebar becomes a
 * drawer (closes on the X, the backdrop, Escape, or after choosing a page).
 *
 * Sending / AI status is not repeated in the header: it is shown truthfully in
 * Settings & Team → Connections and on the screens where it applies (Campaign
 * Studio, AI Receptionist, Dashboard, Reports). The locks themselves are on the
 * server and are unchanged.
 */
import { createContext, Suspense, useContext, useEffect, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { api } from '../../api/client'
import { errText, Loading, MountainMark } from './jwui'
import './jewelry.css'

const Ctx = createContext(null)
export const useJewelry = () => useContext(Ctx)
export const BASE = '/program/jewelry'

const ICONS = {
  home: 'M3 3h7v9H3zM14 3h7v5h-7zM14 12h7v9h-7zM3 16h7v5H3z',
  users: 'M9 11.5a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20c.8-3.6 3.4-5.5 6.5-5.5s5.7 1.9 6.5 5.5M16 4.5a3.3 3.3 0 0 1 0 6.6M18 14.6c1.9.7 3.1 2.5 3.5 5.4',
  calendar: 'M3 4.5h18v16H3zM3 9.5h18M8 2.5v4M16 2.5v4',
  megaphone: 'M3 10v4l11 5V5L3 10zM14 8a4 4 0 0 1 0 8',
  gem: 'M6 3h12l3 5-9 13L3 8zM3 8h18M9 3l3 18M15 3l-3 18',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  spark: 'M12 3l1.8 4.7L18.5 9.5 13.8 11.3 12 16l-1.8-4.7L5.5 9.5l4.7-1.8z',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z',
  menu: 'M4 6h16M4 12h16M4 18h16',
  close: 'M6 6l12 12M18 6L6 18',
}
export function Icon({ name, size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={ICONS[name]} /></svg>
  )
}

function nav(status) {
  const items = [
    { to: BASE, label: 'Dashboard', icon: 'home', end: true },
    { to: `${BASE}/customers`, label: 'Customer Vault', icon: 'users' },
    { to: `${BASE}/appointments`, label: 'Appointments', icon: 'calendar' },
  ]
  if (status.can_manage) items.push({ to: `${BASE}/campaigns`, label: 'Campaign Studio', icon: 'megaphone' })
  items.push({ to: `${BASE}/inventory`, label: 'Inventory & Offers', icon: 'gem' })
  if (status.can_manage) items.push({ to: `${BASE}/reports`, label: 'Reports', icon: 'chart' })
  items.push({ to: `${BASE}/receptionist`, label: 'AI Receptionist', icon: 'spark' })
  if (status.can_manage) items.push({ to: `${BASE}/settings`, label: 'Settings & Team', icon: 'gear' })
  return items
}

function useFonts() {
  useEffect(() => {
    if (document.getElementById('jw-font')) return
    const l = document.createElement('link')
    l.id = 'jw-font'
    l.rel = 'stylesheet'
    l.href = 'https://fonts.googleapis.com/css2?family=Cormorant+Garamond:wght@600;700&family=Manrope:wght@400;500;600;700;800&display=swap'
    document.head.appendChild(l)
  }, [])
}

export function BackToProgramAccess({ className = '' }) {
  return (
    <a href="/workspaces" className={`jw-back ${className}`} data-testid="back-to-program-access" aria-label="Back to Program Access">
      <span aria-hidden="true">←</span>
      <span className="jw-back__long">Back to Program Access</span>
      <span className="jw-back__short" aria-hidden="true">Program Access</span>
    </a>
  )
}

export default function JewelryShell() {
  useFonts()
  const [status, setStatus] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(false)
  const loc = useLocation()
  const menuBtn = useRef(null)
  const closeBtn = useRef(null)
  const wasOpen = useRef(false)

  const load = async () => {
    try { setStatus(await api.get('/jewelry-ops/status')); setError(null) } catch (e) { setError(e) }
  }
  useEffect(() => { load() }, [])
  useEffect(() => { setOpen(false) }, [loc.pathname])
  // Drawer: Escape closes it; focus moves into it on open and back to the menu button on close.
  useEffect(() => {
    if (open) {
      closeBtn.current?.focus()
      const onKey = (e) => { if (e.key === 'Escape') setOpen(false) }
      window.addEventListener('keydown', onKey)
      wasOpen.current = true
      return () => window.removeEventListener('keydown', onKey)
    }
    if (wasOpen.current) { wasOpen.current = false; menuBtn.current?.focus() }
  }, [open])

  if (error) {
    const denied = error.status === 403 || error.status === 402 || error.status === 409
    return (
      <div className="jw jw-gate">
        <div className="jw-top jw-top--gate"><div className="jw-top__right"><BackToProgramAccess /></div></div>
        <div className="jw-card jw-gate__card">
          <MountainMark size={44} />
          <h1>{denied ? 'Gold & Jewelry isn’t turned on for this workspace' : 'This workspace couldn’t load'}</h1>
          <p>{denied ? 'Go back to Program Access and choose your gold & jewelry business, or ask the platform owner to enable the program for it.'
                     : errText(error)}</p>
          {!denied && <button className="jw-btn jw-btn--gold" onClick={load}>Try again</button>}
        </div>
      </div>
    )
  }
  if (!status) return <div className="jw"><Loading label="Opening your business workspace" /></div>

  const p = status.profile || {}
  const value = { status, reload: load, base: BASE }
  return (
    <Ctx.Provider value={value}>
      <div className="jw jw-app">
        {open && <div className="jw-scrim" onClick={() => setOpen(false)} aria-hidden="true" />}
        <aside id="jw-side" className={`jw-side ${open ? 'open' : ''}`} aria-label="Workspace navigation">
          <div className="jw-brand">
            <MountainMark />
            <div><b>{p.business_name}</b><small>BUSINESS COMMAND CENTER</small></div>
            <button ref={closeBtn} type="button" className="jw-side__close" onClick={() => setOpen(false)} aria-label="Close navigation">
              <Icon name="close" />
            </button>
          </div>
          <nav className="jw-nav">
            {nav(status).map(it => (
              <NavLink key={it.to} to={it.to} end={it.end} onClick={() => setOpen(false)}><Icon name={it.icon} />{it.label}</NavLink>
            ))}
          </nav>
          <div className="jw-side__foot">
            <div className="jw-powered">POWERED BY <b>EvoSys</b></div>
            <div className="jw-side__note">{status.is_demo ? 'Sample workspace' : 'Business workspace'} · Gold & Jewelry program</div>
          </div>
        </aside>
        <div className="jw-main">
          <header className="jw-top">
            <button ref={menuBtn} type="button" className="jw-top__menu" onClick={() => setOpen(true)}
                    aria-label="Open navigation" aria-controls="jw-side" aria-expanded={open}>
              <Icon name="menu" size={20} />
            </button>
            <span className="jw-top__title">{p.business_name}</span>
            <div className="jw-top__right">
              <BackToProgramAccess />
              <span className="jw-me"><b>{status.me?.name}</b><small>{status.role_label}</small></span>
            </div>
          </header>
          <main className="jw-content">
            <Suspense fallback={<Loading />}><Outlet /></Suspense>
          </main>
        </div>
      </div>
    </Ctx.Provider>
  )
}
