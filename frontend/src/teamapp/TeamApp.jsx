/**
 * EVOSYS PRO MOBILE (/app).
 *
 * One EvoSys login opens Programs (the existing Program Access answer), Chat,
 * Activity and Account. Mounted in App.jsx with one line: <Route path="/app/*" .../>.
 *
 * LAYOUT: the navy header and the navy bottom tabs never move; only the middle
 * panel scrolls, so the whole program list stays reachable. The staging/preview
 * strip renders INSIDE the shell (under the header), never above the page.
 *
 * WHAT THIS SHELL DECIDES: nothing about permission. It asks the server
 * (/team-app/me) which tabs this person has and draws those. Every screen's
 * data is re-authorized on the server; a typed URL to a hidden screen gets the
 * server's refusal, shown in plain words.
 *
 * INSTALL: on /app routes the page points at /manifest-evosys.webmanifest and
 * the EP emblem icons (same swap the field app uses), so Add to Home Screen
 * installs "EvoSys" opening on Programs. The service worker (public/sw.js)
 * caches the app shell only — never the API, which is a different origin and
 * carries messages, files and activity.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { Navigate, NavLink, Outlet, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { api, getCurrentUser, login, logout, fetchMyContexts, clearWorkspaceContext,
         safeNextPath, startKeepAlive, startRefreshLoop, stopKeepAlive, stopRefreshLoop } from '../api/client'
import { registerServiceWorker } from '../mobile/pwa'
import { badge, initials, safeAppPath, tabsFor } from './teamHelpers'
import ProgramsScreen from './ProgramsScreen'
import { ChatList, Conversation, NewChat, RoomMembers } from './ChatScreens'
import ActivityScreen from './ActivityScreen'
import StagingBanner from '../components/StagingBanner'
import { AccountScreen, TeamAccessScreen } from './AccountScreens'
import './teamapp.css'

const EMBLEM = '/brands/evosyspro-emblem-96.png'
const LOCKUP = '/brands/evosyspro-logo-on-dark-440.png'

const Ctx = createContext(null)
export function useTeam() { return useContext(Ctx) || {} }

export function isAuthed() {
  try { return !!(localStorage.getItem('af_token') || localStorage.getItem('bookaboost_token')) } catch { return false }
}

/** GET a team-app endpoint. {data, error, loading, reload}; `pollMs` refreshes quietly. */
export function useTeamApi(path, { pollMs = 0, deps = [] } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: !!path })
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!path) { setState({ data: null, error: null, loading: false }); return undefined }
    let live = true
    const load = (quiet) => {
      if (!quiet) setState(s => ({ ...s, loading: true, error: null }))
      api.get(path, { skipRedirect: true, noOrgContext: true })
        .then(d => { if (live) setState({ data: d, error: null, loading: false }) })
        .catch(e => { if (live) setState(s => ({ data: quiet ? s.data : null, error: e, loading: false })) })
    }
    load(false)
    const t = pollMs ? setInterval(() => { if (document.visibilityState !== 'hidden') load(true) }, pollMs) : null
    return () => { live = false; if (t) clearInterval(t) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, n, pollMs, ...deps])
  const reload = useCallback(() => setN(x => x + 1), [])
  return { ...state, reload }
}

export function Icon({ name, size = 22 }) {
  const p = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
              strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true, focusable: false }
  switch (name) {
    case 'layers': return <svg {...p}><path d="M12 3l9 5-9 5-9-5 9-5z"/><path d="M3 13l9 5 9-5"/></svg>
    case 'chat': return <svg {...p}><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>
    case 'clock': return <svg {...p}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>
    case 'calendar': return <svg {...p}><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4"/><path d="M8 14h3v3H8z"/></svg>
    case 'calendarCheck': return <svg {...p}><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4M9 15l2 2 4-4"/></svg>
    case 'calendarEdit': return <svg {...p}><path d="M21 11V7a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h6"/><path d="M3 10h18M8 3v4M16 3v4"/><path d="M14 21l1-3 5-5 2 2-5 5z"/></svg>
    case 'calendarX': return <svg {...p}><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4M16 3v4M10 13l4 4M14 13l-4 4"/></svg>
    case 'folder': return <svg {...p}><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>
    case 'building': return <svg {...p}><rect x="4" y="3" width="10" height="18" rx="1"/><path d="M14 9h5a1 1 0 0 1 1 1v11H14M7 7h2M7 11h2M7 15h2M17 13h1M17 17h1"/></svg>
    case 'star': return <svg {...p}><path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1L3.2 9.5l6.1-.9z"/></svg>
    case 'broom': return <svg {...p}><path d="M14 4l6 6M17 7l-6 6"/><path d="M11 13l-6 2-1 5 5-1 2-6z"/><path d="M7 16l1 1"/></svg>
    case 'home': return <svg {...p}><path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/></svg>
    case 'bolt': return <svg {...p}><path d="M13 2L4 14h7l-1 8 9-12h-7z"/></svg>
    case 'target': return <svg {...p}><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/></svg>
    case 'shield': return <svg {...p}><path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/></svg>
    case 'diamond': return <svg {...p}><path d="M6 3h12l3 6-9 12L3 9z"/><path d="M3 9h18M9 3l3 18 3-18"/></svg>
    case 'calculator': return <svg {...p}><rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 12h2M12 12h2M16 12h0M8 16h2M12 16h2"/></svg>
    case 'grid': return <svg {...p}><rect x="4" y="4" width="7" height="7" rx="1"/><rect x="13" y="4" width="7" height="7" rx="1"/><rect x="4" y="13" width="7" height="7" rx="1"/><rect x="13" y="13" width="7" height="7" rx="1"/></svg>
    case 'search': return <svg {...p}><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
    case 'at': return <svg {...p}><circle cx="12" cy="12" r="4"/><path d="M16 12v1.5a2.5 2.5 0 0 0 5 0V12a9 9 0 1 0-3.5 7.1"/></svg>
    case 'user': return <svg {...p}><circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/></svg>
    case 'back': return <svg {...p}><path d="M15 18l-6-6 6-6"/></svg>
    case 'chev': return <svg {...p}><path d="M9 6l6 6-6 6"/></svg>
    case 'send': return <svg {...p}><path d="M22 2L11 13"/><path d="M22 2l-7 20-4-9-9-4z"/></svg>
    case 'clip': return <svg {...p}><path d="M21 11l-8.5 8.5a5 5 0 0 1-7-7L14 4a3.5 3.5 0 0 1 5 5l-8.5 8.5a2 2 0 0 1-3-3L15 7"/></svg>
    case 'compose': return <svg {...p}><path d="M4 20h4L19 9l-4-4L4 16v4z"/><path d="M13 7l4 4"/><path d="M14 4h6v6" opacity="0"/></svg>
    case 'team': return <svg {...p}><circle cx="9" cy="8" r="3"/><circle cx="17" cy="9" r="2.5"/><path d="M3 20c0-3 3-5 6-5s6 2 6 5"/><path d="M15 15c3 0 6 1.5 6 4.5"/></svg>
    case 'megaphone': return <svg {...p}><path d="M3 10v4h4l7 4V6L7 10H3z"/><path d="M18 9a4 4 0 0 1 0 6"/></svg>
    case 'briefcase': return <svg {...p}><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18"/></svg>
    case 'file': return <svg {...p}><path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6"/></svg>
    case 'link': return <svg {...p}><path d="M10 14a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-1 1"/><path d="M14 10a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l1-1"/></svg>
    case 'reply': return <svg {...p}><path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 0 1 6 6v4"/></svg>
    case 'x': return <svg {...p}><path d="M6 6l12 12M18 6L6 18"/></svg>
    case 'eye': return <svg {...p}><path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/></svg>
    case 'mail': return <svg {...p}><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>
    case 'lock': return <svg {...p}><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>
    default: return null
  }
}

export function Avatar({ name, text, size = 40 }) {
  return <span className="ta-avatar" style={{ width: size, height: size, fontSize: size * 0.38 }} aria-hidden>{text || initials(name)}</span>
}

export function ScreenTitle({ title, action }) {
  return <div className="ta-title-row"><h1 className="ta-title">{title}</h1>{action}</div>
}

export function Loading() { return <div className="ta-state" role="status">Loading…</div> }

export function ErrorNote({ error, onRetry }) {
  const status = error && error.status
  const msg = status === 403 ? (error.message || 'This is not part of your access.')
    : status === 404 ? 'Not found, or not available to you.'
      : (error && error.message) || 'Could not load. Check your connection.'
  return (
    <div className="ta-state ta-state--err" role="alert">
      <p>{msg}</p>
      {onRetry && status !== 403 && status !== 404 && <button type="button" className="ta-btn ta-btn--ghost" onClick={onRetry}>Try again</button>}
    </div>
  )
}

// ── Install metadata, only while inside /app ────────────────────────────────
const APP_HEAD = { manifest: '/manifest-evosys.webmanifest', icon: '/icons/evosys-apple-180.png', title: 'EvoSys', theme: '#0b1a3a' }

function useAppHead() {
  useEffect(() => {
    const get = (sel, make) => { let el = document.querySelector(sel); if (!el) { el = make(); document.head.appendChild(el) } return el }
    const m = get('link[rel="manifest"]', () => Object.assign(document.createElement('link'), { rel: 'manifest' }))
    const i = get('link[rel="apple-touch-icon"]', () => Object.assign(document.createElement('link'), { rel: 'apple-touch-icon' }))
    const t = get('meta[name="apple-mobile-web-app-title"]', () => Object.assign(document.createElement('meta'), { name: 'apple-mobile-web-app-title' }))
    const c = get('meta[name="theme-color"]', () => Object.assign(document.createElement('meta'), { name: 'theme-color' }))
    const saved = { m: m.getAttribute('href'), i: i.getAttribute('href'), t: t.getAttribute('content'), c: c.getAttribute('content'), title: document.title }
    m.setAttribute('href', APP_HEAD.manifest); i.setAttribute('href', APP_HEAD.icon)
    t.setAttribute('content', APP_HEAD.title); c.setAttribute('content', APP_HEAD.theme)
    document.title = 'EvoSys Pro'
    return () => {
      if (saved.m) m.setAttribute('href', saved.m)
      if (saved.i) i.setAttribute('href', saved.i)
      if (saved.t) t.setAttribute('content', saved.t); else t.removeAttribute('content')
      if (saved.c) c.setAttribute('content', saved.c)
      document.title = saved.title
    }
  }, [])
}

// ── Sign in ─────────────────────────────────────────────────────────────────
export function AppLogin() {
  const navigate = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [show, setShow] = useState(false)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [logoOk, setLogoOk] = useState(true)
  const params = new URLSearchParams(window.location.search)
  const next = safeAppPath(safeNextPath(params.get('next')))
  const expired = params.get('expired') === '1'
  async function submit(e) {
    e.preventDefault()
    setBusy(true); setErr(null)
    try {
      const data = await login(email.trim(), password)
      if (data && data.must_change_password) { navigate('/change-password'); return }
      try { startKeepAlive(); startRefreshLoop() } catch { /* optional */ }
      navigate(next, { replace: true })
    } catch (ex) {
      setErr(ex.message || 'Sign-in failed. Check your email and password.')
    } finally { setBusy(false) }
  }
  if (isAuthed() && !busy) return <Navigate to={next} replace />
  return (
    <div className="ta-login">
      <StagingBanner inShell />
      <div className="ta-login-top">
        {logoOk
          ? <img className="ta-login-logo" src={LOCKUP} alt="EvoSys Pro" width="440" height="281" onError={() => setLogoOk(false)} />
          : <div className="ta-login-logo-text">EvoSys Pro</div>}
        <h1>Sign in</h1>
      </div>
      <form className="ta-login-form" onSubmit={submit}>
        <label className="ta-field"><Icon name="mail" size={20} />
          <span className="ta-sr">Email</span>
          <input type="email" inputMode="email" autoComplete="username" placeholder="Email" value={email}
                 onChange={e => setEmail(e.target.value)} required /></label>
        <label className="ta-field"><Icon name="lock" size={20} />
          <span className="ta-sr">Password</span>
          <input type={show ? 'text' : 'password'} autoComplete="current-password" placeholder="Password" value={password}
                 onChange={e => setPassword(e.target.value)} required />
          <button type="button" className="ta-icon-btn" onClick={() => setShow(s => !s)}
                  aria-label={show ? 'Hide password' : 'Show password'}><Icon name="eye" size={20} /></button></label>
        {expired && !err && <div className="ta-note" role="status">Your session ended. Sign in again to pick up where you left off.</div>}
        {err && <div className="ta-note ta-note--err" role="alert">{err}</div>}
        <button className="ta-btn ta-btn--primary ta-btn--block" disabled={busy} type="submit">{busy ? 'Signing in…' : 'Sign in'}</button>
        <a className="ta-link-center" href="/forgot-password">Forgot password?</a>
      </form>
    </div>
  )
}

// ── Shell ───────────────────────────────────────────────────────────────────
function Shell() {
  const location = useLocation()
  const navigate = useNavigate()
  const authed = isAuthed()
  const [me, setMe] = useState(null)
  const [meError, setMeError] = useState(null)
  const [unread, setUnread] = useState(0)
  const live = useRef(true)

  useEffect(() => { registerServiceWorker() }, [])
  const loadMe = useCallback(() => {
    return api.get('/team-app/me', { skipRedirect: true, noOrgContext: true })
      .then(d => { if (live.current) { setMe(d); setMeError(null); setUnread(d.chat_unread || 0) } })
      .catch(e => {
        if (!live.current) return
        if (e && e.status === 401) { logout(); navigate('/app/login?expired=1&next=' + encodeURIComponent(location.pathname), { replace: true }); return }
        setMeError(e)
      })
  }, [navigate, location.pathname])
  useEffect(() => { live.current = true; if (authed) loadMe(); return () => { live.current = false } }, [authed]) // eslint-disable-line react-hooks/exhaustive-deps

  // Unread badge: a quiet poll, no new service.
  useEffect(() => {
    if (!authed || !me || !me.access || !me.access.chat) return undefined
    const t = setInterval(() => {
      if (document.visibilityState === 'hidden') return
      api.get('/team-app/chat/unread', { skipRedirect: true, noOrgContext: true })
        .then(d => setUnread(d.unread_total || 0)).catch(() => {})
    }, 8000)
    return () => clearInterval(t)
  }, [authed, me])

  const signOut = useCallback(async () => {
    try { stopKeepAlive(); stopRefreshLoop() } catch { /* ignore */ }
    clearWorkspaceContext()
    await logout()
    navigate('/app/login', { replace: true })
  }, [navigate])

  const value = useMemo(() => ({ me, reloadMe: loadMe, unread, setUnread, signOut,
                                 tz: (me && me.time_zone) || 'America/Chicago' }), [me, loadMe, unread, signOut])

  if (!authed) return <Navigate to={'/app/login?next=' + encodeURIComponent(location.pathname)} replace />
  const access = (me && me.access) || {}
  const tabs = tabsFor(me ? access : {})
  const user = (me && me.user) || getCurrentUser() || {}
  const inConversation = /^\/app\/chat\/(?!new$)[^/]+/.test(location.pathname)

  return (
    <Ctx.Provider value={value}>
      <div className={'ta-shell' + (inConversation ? ' ta-shell--convo' : '')}>
        {!inConversation && (
          <header className="ta-top">
            <div className="ta-brand"><img src={EMBLEM} alt="" width="34" height="34" /><span>EvoSys Pro</span></div>
            <NavLink to="/app/account" className="ta-me" aria-label="Account">{(me && me.user.initials) || initials(user.full_name || user.name)}</NavLink>
          </header>
        )}
        <div className="ta-envstrip"><StagingBanner inShell /></div>
        <main className="ta-main">
          {meError && !me ? <ErrorNote error={meError} onRetry={loadMe} /> : <Outlet />}
        </main>
        {!inConversation && (
          <nav className="ta-nav" aria-label="Main">
            {tabs.map(t => (
              <NavLink key={t.key} to={t.to} className={({ isActive }) => 'ta-tab' + (isActive ? ' is-active' : '')}>
                <span className="ta-tab-icon"><Icon name={t.icon} />
                  {t.key === 'chat' && badge(unread) && <span className="ta-badge" aria-label={unread + ' unread'}>{badge(unread)}</span>}</span>
                <span>{t.label}</span>
              </NavLink>
            ))}
          </nav>
        )}
      </div>
    </Ctx.Provider>
  )
}

export default function TeamApp() {
  useAppHead()
  return (
    <Routes>
      <Route path="login" element={<AppLogin />} />
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/app/programs" replace />} />
        <Route path="programs" element={<ProgramsScreen />} />
        <Route path="chat" element={<ChatList />} />
        <Route path="chat/new" element={<NewChat />} />
        <Route path="chat/:roomId" element={<Conversation />} />
        <Route path="chat/:roomId/members" element={<RoomMembers />} />
        <Route path="activity" element={<ActivityScreen />} />
        {/* Timekeeping is not part of EvoSys Pro Mobile: an old Time link
            (or a home-screen shortcut saved from the prototype) lands on Activity. */}
        <Route path="time/*" element={<Navigate to="/app/activity" replace />} />
        <Route path="account" element={<AccountScreen />} />
        <Route path="account/team-access" element={<TeamAccessScreen />} />
        <Route path="*" element={<Navigate to="/app/programs" replace />} />
      </Route>
    </Routes>
  )
}

export { fetchMyContexts }
