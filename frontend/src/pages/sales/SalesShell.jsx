/**
 * SalesShell — the frame every /sales screen renders inside.
 *
 * Deliberately NOT the tenant `Layout`. A salesperson has no customer
 * organization, so the tenant nav (Leads, Cadence, Replies, Admin) is not just
 * irrelevant to them, it points at data they must never see. Same reason God
 * Mode got its own GodShell.
 *
 * The nav rendered here is driven by /sales/me — the server says which brand,
 * which role, and which permissions. That is presentation only: every route is
 * re-checked server-side. Hiding a nav item is not access control.
 */
import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { api, logout } from '../../api/client'
import GodReturnBar from '../../components/GodReturnBar'
import SalesStyles from './SalesStyles'
import { initials, ErrorBar } from './parts'

const SalesCtx = createContext(null)

export function useSales() {
  return useContext(SalesCtx)
}

// Items marked soon:true are Checkpoint 2+. They render visibly disabled with a
// SOON marker rather than being hidden, so the rep can see the shape of the
// workspace without being able to click into something that does not work.
const NAV = [
  { to: '/sales',           label: 'My Day',            icon: '⌂', end: true },
  { to: '/sales/pipeline',  label: 'My Pipeline',       icon: '⇢', countKey: 'active_opportunities' },
  { to: '/sales/team',      label: 'Team Availability', icon: '▦' },
  { to: '/sales/availability', label: 'My Availability', icon: '◷' },
  // Checkpoint 4. Rendered visibly disabled rather than hidden so the shape of
  // the workspace is legible without anything unfinished being clickable.
  { to: '/sales/onboarding', label: 'Sold / Onboarding', icon: '✓', soon: true },
]

// Checkpoint 5. Shown only to a manager — `permission` names the flag on
// /sales/me that decides. This is presentation: /sales/manager/* is gated by
// require_sales_manager server-side, so a rep who types the URL gets a 403,
// not a screen. The nav item is a courtesy, never the control.
const MANAGER_NAV = [
  { to: '/sales/manager', label: 'Team Command', icon: '◎',
    permission: 'view_team_pipeline', end: true },
]

export default function SalesShell({ title, subtitle, actions, children }) {
  const nav = useNavigate()
  const [ctx, setCtx] = useState(null)
  const [counts, setCounts] = useState({})
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const me = await api.get('/sales/me')
      setCtx(me)
      try {
        const day = await api.get('/sales/my-day')
        setCounts(day.metrics || {})
      } catch { /* counts are a nicety, never a blocker */ }
    } catch (e) {
      setError(e.message || 'Could not load the sales workspace.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  async function handleSignOut() {
    await logout()
    nav('/login', { replace: true })
  }

  // The server refused. Almost always: this user holds no active brand-sales
  // membership. Say that plainly instead of bouncing them somewhere confusing.
  if (!loading && !ctx) {
    return (
      <div className="sw-scope">
        <SalesStyles />
        {/* Especially here. This is the screen you land on when the server says
            no, and it is exactly where being stranded would hurt most. */}
        <GodReturnBar context="the Sales Workspace" />
        <div style={{ padding: 60, maxWidth: 560, margin: '0 auto' }}>
          <h2 style={{ fontSize: 18, margin: '0 0 10px' }}>Sales workspace unavailable</h2>
          <ErrorBar error={error} onRetry={load} />
          <p style={{ fontSize: 12, color: '#5f7182', lineHeight: 1.7 }}>
            This account has no active brand-sales membership, so there is no
            sales workspace to open. If that is wrong, ask an administrator to
            grant the membership.
          </p>
          <button className="sw-btn sw-mt" onClick={handleSignOut}>Sign out</button>
        </div>
      </div>
    )
  }

  const brand = ctx?.brand_sales_org?.name || 'Sales'
  const platform = ctx?.platform?.name || ''
  const person = ctx?.user?.full_name || ''

  return (
    <div className="sw-scope">
      <SalesStyles />
      <div className="sw-app">
        <aside className="sw-sidebar">
          <div className="sw-brand">
            <div className="sw-brandmark">{(platform || brand).charAt(0).toUpperCase()}</div>
            <div>
              <b>{platform || brand}</b>
              <small>Sales Workspace</small>
            </div>
          </div>

          <div className="sw-profile">
            <div className="sw-who">
              <div className="sw-avatar">{initials(person)}</div>
              <div>
                <b>{person || '—'}</b>
                <small>{ctx?.role_label || ''}</small>
              </div>
            </div>
          </div>

          <div className="sw-navtitle">MY WORK</div>
          <nav className="sw-nav">
            {NAV.map(item => {
              const count = item.countKey ? counts[item.countKey] : null
              if (item.soon) {
                return (
                  <a key={item.to} className="sw-disabled" aria-disabled="true">
                    <span>{item.icon}</span><span>{item.label}</span>
                    <span className="sw-soon">SOON</span>
                  </a>
                )
              }
              return (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) => (isActive ? 'sw-on' : '')}
                >
                  <span>{item.icon}</span><span>{item.label}</span>
                  {count ? <span className="sw-count">{count}</span> : null}
                </NavLink>
              )
            })}
          </nav>

          {MANAGER_NAV.some(i => ctx?.permissions?.[i.permission]) ? (
            <>
              <div className="sw-navtitle">MY TEAM</div>
              <nav className="sw-nav">
                {MANAGER_NAV.filter(i => ctx?.permissions?.[i.permission]).map(item => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) => (isActive ? 'sw-on' : '')}
                  >
                    <span>{item.icon}</span><span>{item.label}</span>
                  </NavLink>
                ))}
              </nav>
            </>
          ) : null}

          <div className="sw-sidefill" />
          <div className="sw-mini">
            {brand}
            {ctx?.is_god && <><br />Owner access</>}
            <br />
            <button onClick={handleSignOut} style={{ marginTop: 8 }}>Sign out</button>
          </div>
        </aside>

        <main className="sw-main">
          {/* god_admin only — the way back to the Command Center. */}
          <GodReturnBar context="the Sales Workspace" />
          <header className="sw-topbar">
            <div>
              <h1>{title}</h1>
              {subtitle && <p>{subtitle}</p>}
            </div>
            <div className="sw-spacer" />
            {actions}
          </header>
          <div className="sw-body">
            {loading && !ctx
              ? <div className="sw-subtle">Loading…</div>
              : <SalesCtx.Provider value={{ ...ctx, reloadContext: load }}>
                  {children}
                </SalesCtx.Provider>}
          </div>
        </main>
      </div>
    </div>
  )
}
