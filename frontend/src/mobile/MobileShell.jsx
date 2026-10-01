/**
 * THE MOBILE SHELL — one shell for every brand and vertical.
 *
 * Auth: no token -> /m/login. Workspace: the same X-Workspace-Id selection the
 * desktop ContextSwitcher writes (setWorkspaceContext); a person holding
 * several workspaces and none selected is sent to /m/workspaces.
 * Branding: GET /branding/org via fetchAndStoreBranding (the desktop's own
 * call), then the skin is chosen by mobileSkin() — no per-brand code path.
 */
import { createContext, useContext, useEffect, useMemo, useState, useCallback } from 'react'
import { NavLink, Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { api, getBranding, fetchAndStoreBranding, fetchMyContexts, getWorkspaceContext,
         setWorkspaceContext, getObservationContext } from '../api/client'
import { mobileSkin, brandVars, brandName, badgeCount } from './mobileHelpers'
import { registerServiceWorker, useInstallPrompt } from './pwa'
import './mobile.css'

const MobileCtx = createContext(null)
export function useMobile() { return useContext(MobileCtx) || {} }

export function isAuthenticated() {
  try { return !!(localStorage.getItem('af_token') || localStorage.getItem('bookaboost_token')) } catch { return false }
}

/** Load an existing endpoint; {data, error, loading, reload}. */
export function useApi(path, deps = []) {
  const [state, setState] = useState({ data: null, error: null, loading: !!path })
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!path) { setState({ data: null, error: null, loading: false }); return }
    let live = true
    setState(s => ({ ...s, loading: true, error: null }))
    api.get(path, { skipRedirect: true })
      .then(d => { if (live) setState({ data: d, error: null, loading: false }) })
      .catch(e => { if (live) setState({ data: null, error: e, loading: false }) })
    return () => { live = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, n, ...deps])
  const reload = useCallback(() => setN(x => x + 1), [])
  return { ...state, reload }
}

const NAV = [
  { to: '/m', end: true, label: 'Home', icon: 'home' },
  { to: '/m/conversations', label: 'Inbox', icon: 'chat', badgeKey: 'needs_attention' },
  { to: '/m/tasks', label: 'Tasks', icon: 'check', badgeKey: 'open_tasks' },
  { to: '/m/appointments', label: 'Calendar', icon: 'cal' },
  { to: '/m/more', label: 'More', icon: 'more' },
]

export function Icon({ name, size = 22 }) {
  const p = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
              strokeWidth: 1.9, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true }
  switch (name) {
    case 'home': return <svg {...p}><path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/></svg>
    case 'chat': return <svg {...p}><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>
    case 'check': return <svg {...p}><rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 12l3 3 5-6"/></svg>
    case 'cal': return <svg {...p}><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/></svg>
    case 'more': return <svg {...p}><circle cx="5" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="19" cy="12" r="1.4"/></svg>
    case 'back': return <svg {...p}><path d="M15 18l-6-6 6-6"/></svg>
    case 'bell': return <svg {...p}><path d="M6 8a6 6 0 1 1 12 0c0 7 3 8 3 8H3s3-1 3-8"/><path d="M10 20a2 2 0 0 0 4 0"/></svg>
    case 'phone': return <svg {...p}><path d="M22 16.9v3a2 2 0 0 1-2.2 2 19.8 19.8 0 0 1-8.6-3.1 19.5 19.5 0 0 1-6-6A19.8 19.8 0 0 1 2.1 4.2 2 2 0 0 1 4.1 2h3a2 2 0 0 1 2 1.7c.1 1 .4 1.9.7 2.8a2 2 0 0 1-.5 2.1L8 9.9a16 16 0 0 0 6 6l1.3-1.3a2 2 0 0 1 2.1-.4c.9.3 1.8.6 2.8.7a2 2 0 0 1 1.7 2z"/></svg>
    case 'send': return <svg {...p}><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4z"/></svg>
    case 'flame': return <svg {...p}><path d="M12 2s5 5 5 10a5 5 0 0 1-10 0c0-2 1-3.5 1-3.5S9 11 11 11c0-4 1-9 1-9z"/></svg>
    default: return null
  }
}

export default function MobileShell() {
  const location = useLocation()
  const navigate = useNavigate()
  const [branding, setBranding] = useState(() => { try { return getBranding() } catch { return null } })
  const [identity, setIdentity] = useState(null)
  const [summary, setSummary] = useState(null)
  const [contexts, setContexts] = useState(null)
  const [workspaceId, setWsId] = useState(() => getWorkspaceContext())
  const [canInstall, promptInstall] = useInstallPrompt()
  const authed = isAuthenticated()

  useEffect(() => { registerServiceWorker() }, [])

  // Workspace resolution: the server's list decides; nothing is derived locally.
  useEffect(() => {
    if (!authed) return
    let live = true
    fetchMyContexts().then(ctx => {
      if (!live) return
      setContexts(ctx)
      const list = (ctx && ctx.workspace_contexts) || []
      const current = getWorkspaceContext()
      if (!current && list.length === 1) {
        setWorkspaceContext(list[0].organization_id); setWsId(list[0].organization_id)
      } else if (!current && list.length > 1 && !(ctx && ctx.has_back_office)
                 && location.pathname !== '/m/workspaces') {
        navigate('/m/workspaces', { replace: true })
      }
    }).catch(() => { if (live) setContexts(null) })
    return () => { live = false }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authed])

  // Branding + identity + counts for the ACTIVE workspace; re-read on switch.
  useEffect(() => {
    if (!authed) return
    let live = true
    fetchAndStoreBranding({ applyTheme: false }).then(b => { if (live && b) setBranding(b) })
    api.get('/work/identity', { skipRedirect: true }).then(d => { if (live) setIdentity(d) }).catch(() => {})
    api.get('/communications/summary', { skipRedirect: true }).then(d => { if (live) setSummary(d) }).catch(() => { if (live) setSummary(null) })
    return () => { live = false }
  }, [authed, workspaceId])

  const skin = mobileSkin(branding, { pathname: location.pathname })
  const vars = brandVars(branding)
  const name = brandName(branding, identity)

  // The browser chrome colour follows the workspace (status bar on Android).
  useEffect(() => {
    let meta = document.querySelector('meta[name="theme-color"]')
    if (!meta) { meta = document.createElement('meta'); meta.name = 'theme-color'; document.head.appendChild(meta) }
    const prev = meta.content
    meta.content = vars['--m-brand'] || (skin === 'luxe' ? '#0b0b0c' : skin === 'energy' ? '#0f2a4d' : skin === 'wholesale' ? '#111827' : '#0f172a')
    return () => { meta.content = prev }
  }, [skin, vars['--m-brand']])

  const switchWorkspace = useCallback((orgId) => {
    setWorkspaceContext(orgId)
    setWsId(orgId)
    setIdentity(null); setSummary(null)
    navigate('/m', { replace: true })
  }, [navigate])

  const ctx = useMemo(() => ({ branding, identity, summary, contexts, workspaceId, skin, switchWorkspace,
                               observing: !!getObservationContext(), canInstall, promptInstall,
                               refreshSummary: () => api.get('/communications/summary', { skipRedirect: true }).then(setSummary).catch(() => {}) }),
                      [branding, identity, summary, contexts, workspaceId, skin, switchWorkspace, canInstall, promptInstall])

  if (!authed) return <Navigate to="/m/login" replace />

  return (
    <MobileCtx.Provider value={ctx}>
      <div className="mshell" data-mskin={skin} style={vars}>
        <header className="mshell-top">
          <div className="mshell-brand">
            {branding && branding.brand_logo_url
              ? <img src={branding.brand_logo_url} alt="" className="mshell-logo" />
              : <span className="mshell-mark" aria-hidden>{(name || '·').slice(0, 1)}</span>}
            <span className="mshell-name">{name || 'Workspace'}</span>
          </div>
          <NavLink to="/m/notifications" className="mshell-bell" aria-label="Notifications"><Icon name="bell" /></NavLink>
        </header>
        <main className="mshell-main">
          <Outlet />
        </main>
        <nav className="mshell-nav" aria-label="Primary">
          {NAV.map(item => {
            const b = item.badgeKey && summary ? badgeCount(summary[item.badgeKey]) : ''
            return (
              <NavLink key={item.to} to={item.to} end={item.end}
                       className={({ isActive }) => 'mshell-tab' + (isActive ? ' is-active' : '')}>
                <span className="mshell-tab-icon"><Icon name={item.icon} />{b && <span className="mshell-badge">{b}</span>}</span>
                <span className="mshell-tab-label">{item.label}</span>
              </NavLink>
            )
          })}
        </nav>
      </div>
    </MobileCtx.Provider>
  )
}

/** Shared screen header with optional back link. */
export function ScreenHead({ title, sub, back }) {
  const navigate = useNavigate()
  return (
    <div className="mscreen-head">
      {back && <button type="button" className="mback" onClick={() => (window.history.length > 1 ? navigate(-1) : navigate(back))} aria-label="Back"><Icon name="back" /></button>}
      <div>
        <h1 className="mscreen-title">{title}</h1>
        {sub && <div className="mscreen-sub">{sub}</div>}
      </div>
    </div>
  )
}

export function Loading() { return <div className="mstate">Loading…</div> }
export function ErrorState({ error, onRetry }) {
  const forbidden = error && (error.status === 402 || error.status === 403)
  return (
    <div className="mstate mstate--err">
      {forbidden ? 'This is not available in this workspace.' : (error && error.message) || 'Could not load.'}
      {onRetry && !forbidden && <button type="button" className="mbtn mbtn--ghost" onClick={onRetry}>Retry</button>}
    </div>
  )
}
export function Empty({ children }) { return <div className="mstate">{children}</div> }
