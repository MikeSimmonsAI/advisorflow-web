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

// A manager is two people at once: an individual seller with their own book,
// and the person running a team. Splitting the nav on exactly that line is the
// point - MY WORK is "what do I owe?", MY TEAM is "what does my team need from
// me?". The same deal appears under both only when the manager owns it, which
// is honest rather than duplicated.
//
// Items marked soon:true render visibly disabled with a marker rather than
// being hidden, so the shape of the workspace is legible without anything
// unfinished being clickable.
const NAV = [
  { to: '/sales',              label: 'My Day',            icon: '⌂', end: true },
  { to: '/sales/pipeline',     label: 'My Pipeline',       icon: '⇢', countKey: 'active_opportunities' },
  { to: '/sales/prospects',    label: 'Prospects',         icon: '◇' },
  { to: '/sales/availability', label: 'My Availability',   icon: '◷' },
  { to: '/sales/onboarding',   label: 'Sold / Onboarding', icon: '✓' },
]

// Team Availability is the one item that belongs to BOTH groups by right: a rep
// needs it to find a colleague's free time, a manager needs it to run the week.
// Rather than render the same link twice, it moves group depending on who is
// looking. Rendering it in both places is the "confusing duplicate link" this
// nav is meant to avoid.
const REP_ONLY_NAV = [
  { to: '/sales/team', label: 'Team Availability', icon: '▦' },
]

// Shown only to a manager - `permission` names the flag on /sales/me that
// decides. This is presentation: every one of these routes is gated
// server-side, so a rep who types the URL gets a 403, not a screen. The nav
// item is a courtesy, never the control.
const MANAGER_NAV = [
  { to: '/sales/manager',       label: 'Team Command',      icon: '◎',
    permission: 'view_team_pipeline', end: true },
  { to: '/sales/calendar',      label: 'Team Calendar',     icon: '▤',
    permission: 'view_team_pipeline' },
  { to: '/sales/team-pipeline', label: 'Team Pipeline',     icon: '⇉',
    permission: 'view_team_pipeline' },
  { to: '/sales/team',          label: 'Team Availability', icon: '▦',
    permission: 'view_team_pipeline' },
  { to: '/sales/proposals',     label: 'Demos / Proposals', icon: '◈',
    permission: 'view_team_pipeline' },
  { to: '/sales/salespeople',   label: 'Salespeople',       icon: '⚇',
    permission: 'view_team_pipeline' },
  // Reports is genuinely not built, and its PURPOSE has not been decided yet.
  // Team Command was built on the principle that a manager screen measuring
  // effort instead of obstacles becomes a stick; Reports may cut against that,
  // so it waits for a decision rather than for an engineer. Shown disabled so
  // nobody wonders whether it is hiding somewhere.
  { to: '/sales/reports',       label: 'Reports',           icon: '◱',
    permission: 'view_team_pipeline', soon: true },
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
          {/* THE SERVER'S REASON, NOT THIS FILE'S GUESS.
              This used to state "This account has no active brand-sales
              membership" unconditionally, whatever the server actually said.
              For the platform owner that sentence was simply false — god access
              never consults memberships — and it pointed at a fix (grant a
              membership) that would have papered over the real condition: the
              selected brand had no sales team record yet. ErrorBar above already
              renders the server's own message; this line now only adds the
              membership explanation when that IS the reason. */}
          <p style={{ fontSize: 12, color: '#5f7182', lineHeight: 1.7 }}>
            {/membership/i.test(error || '')
              ? 'This account has no active brand-sales membership, so there is no sales workspace to open. If that is wrong, ask an administrator to grant the membership.'
              : 'Pick a different workspace, or return to God Mode above.'}
          </p>
          <button className="sw-btn sw-mt" onClick={handleSignOut}>Sign out</button>
        </div>
      </div>
    )
  }

  const brand = ctx?.brand_sales_org?.name || 'Sales'
  const platform = ctx?.platform?.name || ''
  const person = ctx?.user?.full_name || ''

  // One source of truth for "is this person running a team", read from the
  // server's own permission flag rather than from the role string, so the nav
  // and the API can never disagree about who a manager is.
  const isManager = !!ctx?.permissions?.view_team_pipeline
  const managerNav = MANAGER_NAV.filter(i => ctx?.permissions?.[i.permission])
  // Team Availability moves into MY TEAM for a manager, so it is never drawn
  // twice.
  const myWork = isManager ? NAV : [...NAV.slice(0, 3), ...REP_ONLY_NAV, ...NAV.slice(3)]

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

          <div className="sw-navtitle">
            MY WORK{isManager ? <span className="sw-navhint"> — as a seller</span> : null}
          </div>
          <nav className="sw-nav">
            {myWork.map(item => {
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

          {managerNav.length ? (
            <>
              <div className="sw-navtitle">MY TEAM<span className="sw-navhint"> — as a manager</span></div>
              <nav className="sw-nav">
                {managerNav.map(item => {
                  if (item.soon) {
                    return (
                      <a key={item.to} className="sw-disabled" aria-disabled="true"
                         title="Deferred until its purpose is agreed — not hidden, not half-built.">
                        <span>{item.icon}</span><span>{item.label}</span>
                        <span className="sw-soon">LATER</span>
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
                    </NavLink>
                  )
                })}
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
