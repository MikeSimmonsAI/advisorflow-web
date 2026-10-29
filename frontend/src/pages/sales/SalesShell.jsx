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
import { createContext, useContext, useEffect, useRef, useState, useCallback } from 'react'
import { NavLink, useNavigate, useLocation } from 'react-router-dom'
import { api, logout, fetchMyContexts, getCurrentUser } from '../../api/client'
import GodReturnBar from '../../components/GodReturnBar'
import { brandMarkUrl } from '../../theme'
import SalesStyles from './SalesStyles'
import SimpleStyles from './SimpleStyles'
import { Icon } from './SimpleParts'
import { followUpList, otherWorkspaceCount } from './simpleSales'
import { initials, ErrorBar } from './parts'
import { setSalesBrand } from './salesBrand'

const SalesCtx = createContext(null)

export function useSales() {
  return useContext(SalesCtx)
}

// THE APPROVED SIMPLE NAV (Lane F, #281 — "Simplified Sales Workflow – My
// Day"). Seven items in the order the approved design shows them, so a new rep
// sees what to do and nothing else. A manager is still two people at once: this
// is their MY WORK as a seller, and MY TEAM below is unchanged.
const NAV = [
  { to: '/sales',              label: 'My Day',         icon: 'home', end: true },
  { to: '/sales/prospects',    label: 'Leads',          icon: 'users' },
  { to: '/sales/follow-ups',   label: 'Follow Ups',     icon: 'phone', countKey: 'follow_ups_due' },
  { to: '/sales/appointments', label: 'Appointments',   icon: 'calendar' },
  // DEMOS TO BUILD is MY WORK, not a manager screen. `/sales/demo-queue`
  // scopes to the caller's own book exactly as the pipeline does.
  { to: '/sales/demos',        label: 'Demos to Build', icon: 'play' },
  { to: '/sales/pipeline',     label: 'Pipeline',       icon: 'pipeline' },
  { to: '/sales/my-results',   label: 'Reports',        icon: 'chart' },
]

// Everything a rep could reach before the redesign and still can — kept one
// click away under "More" instead of crowding the seven items above. Nothing
// here was removed; Sold / Onboarding is where a Won deal hands off.
const MORE_NAV = [
  { to: '/sales/book',            label: 'Book a Demo',       icon: 'calendar' },
  { to: '/sales/pipeline/stages', label: 'All Deal Stages',   icon: 'pipeline' },
  { to: '/sales/availability',    label: 'My Availability',   icon: 'calendar' },
  // THE DEMO SUITE. `present_demo` is the server's own answer from the
  // `demo_suite` capability grant — the same row the Suite's routes check.
  { to: '/demo-suite',            label: 'Demo Suite',        icon: 'play',
    permission: 'present_demo' },
  { to: '/sales/onboarding',      label: 'Sold / Onboarding', icon: 'folder' },
  // A rep's OWN compensation, scoped by their token server-side.
  { to: '/sales/my-compensation', label: 'My Compensation',   icon: 'chart' },
]

// Team Availability belongs to BOTH groups by right: a rep needs it to find a
// colleague's free time, a manager needs it to run the week. It sits under
// More for a rep and under MY TEAM for a manager, never in both.
const REP_ONLY_MORE = [
  { to: '/sales/team', label: 'Team Availability', icon: 'team' },
]

// Shown only to a manager - `permission` names the flag on /sales/me that
// decides. This is presentation: every one of these routes is gated
// server-side, so a rep who types the URL gets a 403, not a screen. The nav
// item is a courtesy, never the control.
const MANAGER_NAV = [
  { to: '/sales/manager',       label: 'Team Command',      icon: 'home',
    permission: 'view_team_pipeline', end: true },
  { to: '/sales/calendar',      label: 'Team Calendar',     icon: 'calendar',
    permission: 'view_team_pipeline' },
  { to: '/sales/team-pipeline', label: 'Team Pipeline',     icon: 'pipeline',
    permission: 'view_team_pipeline' },
  { to: '/sales/team',          label: 'Team Availability', icon: 'team',
    permission: 'view_team_pipeline' },
  { to: '/sales/proposals',     label: 'Demos / Proposals', icon: 'play',
    permission: 'view_team_pipeline' },
  { to: '/sales/salespeople',   label: 'Salespeople',       icon: 'users',
    permission: 'view_team_pipeline' },
  // What the compensation rules PRODUCED — earned, on hold, payable, paid —
  // and the settlement of it. Not the configuration screen: rates and plans
  // live in God Mode → Pricing & Comp. Gated server-side by manager scope;
  // settling payments needs platform finance authority on top.
  // state tells CompensationCommand that it was opened from SalesShell so it
  // can wrap itself in SalesShell chrome instead of a disconnected Back button.
  { to: '/sales/compensation',  label: 'Compensation',      icon: 'chart',
    permission: 'view_team_pipeline', state: { fromSales: true } },
  // Reports is genuinely not built, and its PURPOSE has not been decided yet.
  // Team Command was built on the principle that a manager screen measuring
  // effort instead of obstacles becomes a stick; Reports may cut against that,
  // so it waits for a decision rather than for an engineer. Shown disabled so
  // nobody wonders whether it is hiding somewhere.
  { to: '/sales/reports',       label: 'Team Reports',           icon: 'chart',
    permission: 'view_team_pipeline', soon: true },
]

export default function SalesShell({ title, subtitle, actions, children }) {
  const nav = useNavigate()
  const loc = useLocation()
  const [ctx, setCtx] = useState(null)
  const [counts, setCounts] = useState({})
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const others = useOtherWorkspaces()

  // The Follow Ups badge is counted from the SAME list the Follow Ups screen
  // shows — the caller's own open deals due today or earlier, in the TEAM's
  // time zone — so the badge and the page can never disagree. Screens call
  // refreshCounts() after a save so the badge moves with the list.
  const loadCounts = useCallback(async (tz) => {
    try {
      const o = await api.get('/sales/opportunities')
      const all = (o.stages || []).flatMap(st => st.opportunities || [])
      const own = o.is_manager ? all.filter(c => c.owner_user_id === o.viewer_user_id) : all
      setCounts({ follow_ups_due: followUpList(own, 'today', '', new Date(), tz).length })
    } catch { /* counts are a nicety, never a blocker */ }
  }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const me = await api.get('/sales/me')
      setSalesBrand(me)
      setCtx(me)
      await loadCounts(me?.brand_sales_org?.timezone)
    } catch (e) {
      setError(e.message || 'Could not load the sales workspace.')
    } finally {
      setLoading(false)
    }
  }, [loadCounts])

  useEffect(() => { load() }, [load])

  async function handleSignOut() {
    await logout()
    nav('/login', { replace: true })
  }

  // The server refused. Almost always: this user holds no active brand-sales
  // membership. Say that plainly instead of bouncing them somewhere confusing.
  if (!loading && !ctx) {
    return (
      <div className="sw-scope ss-shell">
        <SalesStyles />
        <SimpleStyles />
        {/* Especially here. This is the screen you land on when the server says
            no, and it is exactly where being stranded would hurt most. */}
        <GodReturnBar context="the Sales Workspace" />
        <div style={{ padding: 60, maxWidth: 560, margin: '0 auto' }}>
          <h2 style={{ fontSize: 18, margin: '0 0 10px' }}>Sales workspace unavailable</h2>
          <ErrorBar error={error} onRetry={load} />
          {/* THE SERVER'S REASON, NOT THIS FILE'S GUESS. ErrorBar above renders
              the server's own message; this line only adds the membership
              explanation when that IS the reason. */}
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
  // The brand's own mark when it has one (BookaBoost); otherwise the letter.
  const markUrl = brandMarkUrl(ctx?.platform?.slug)
  const person = ctx?.user?.full_name || ''

  // One source of truth for "is this person running a team", read from the
  // server's own permission flag rather than from the role string, so the nav
  // and the API can never disagree about who a manager is. Hiding a nav item
  // is presentation; every route is re-checked server-side.
  const isManager = !!ctx?.permissions?.view_team_pipeline
  const managerNav = MANAGER_NAV.filter(i => ctx?.permissions?.[i.permission])
  const allowed = i => !i.permission || ctx?.permissions?.[i.permission]
  const myWork = NAV.filter(allowed)
  const more = (isManager ? MORE_NAV : [...MORE_NAV, ...REP_ONLY_MORE]).filter(allowed)
  // Open "More" automatically when the current page lives in it, so the
  // highlighted item is never hidden.
  const inMore = more.some(i => loc.pathname === i.to || loc.pathname.startsWith(i.to + '/'))

  return (
    <div className="sw-scope ss-shell">
      <SalesStyles />
      <SimpleStyles />
      <div className="sw-app">
        <aside className="sw-sidebar" aria-label="Sales Workspace">
          <div className="sw-brand">
            {markUrl
              ? <img className="sw-brandmark sw-brandmark--img" src={markUrl} alt="" width="30" height="30" />
              : <div className="sw-brandmark">{(platform || brand).charAt(0).toUpperCase()}</div>}
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
                <small>{ctx?.role_label || 'Sales Workspace'}</small>
              </div>
            </div>
          </div>
          {/* DESKTOP: the one way to another business, under who you are.
              Phones get the same entry inside More (the sidebar is a bar). */}
          <OtherWorkspacesLink state={others} className="ss-otherws ss-otherws-side" />

          {isManager && <div className="sw-navtitle">MY WORK</div>}
          <nav className="sw-nav" aria-label="My work">
            {myWork.map(item => <NavItem key={item.to} item={item} count={item.countKey ? counts[item.countKey] : null} />)}
          </nav>

          <MoreGroup items={more} defaultOpen={inMore} />

          {managerNav.length ? (
            <>
              <div className="sw-navtitle">MY TEAM</div>
              <nav className="sw-nav" aria-label="My team">
                {managerNav.map(item => <NavItem key={item.to} item={item} />)}
              </nav>
            </>
          ) : null}

          <div className="sw-sidefill" />
          <div className="sw-mini">
            <b>{brand}</b>
            {ctx?.is_god && <>Owner access<br /></>}
            <button onClick={handleSignOut}>Sign out</button>
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
            <NavLink to="/sales/prospects" className="ss-iconbtn ss-hide-sm" aria-label="Search leads"
                     title="Search leads">
              <Icon name="search" size={20} />
            </NavLink>
            <div className="ss-circle" title={person} aria-hidden="true">{initials(person)}</div>
            {/* PHONES ONLY. The sidebar collapses to a scrolling bar of the
                seven main items, so everything under "More" — and Sign out —
                is reached from this one button. Same allowed list as the
                desktop "More"; the server still checks every route. */}
            <MobileMore items={more} onSignOut={handleSignOut} others={others} />
          </header>
          <div className="sw-body">
            {loading && !ctx
              ? <div className="sw-subtle">Loading…</div>
              : <SalesCtx.Provider value={{ ...ctx, counts, reloadContext: load,
                  refreshCounts: () => loadCounts(ctx?.brand_sales_org?.timezone) }}>
                  {children}
                </SalesCtx.Provider>}
          </div>
        </main>
      </div>
    </div>
  )
}

function NavItem({ item, count }) {
  if (item.soon) {
    return (
      <a className="sw-disabled" aria-disabled="true"
         title="Deferred until its purpose is agreed — not hidden, not half-built.">
        <Icon name={item.icon} /><span>{item.label}</span>
        <span className="sw-soon">LATER</span>
      </a>
    )
  }
  return (
    <NavLink to={item.to} end={item.end} state={item.state}
             className={({ isActive }) => (isActive ? 'sw-on' : '')}>
      <Icon name={item.icon} /><span>{item.label}</span>
      {count ? <span className="sw-count">{count}</span> : null}
    </NavLink>
  )
}

function MoreGroup({ items, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen)
  useEffect(() => { if (defaultOpen) setOpen(true) }, [defaultOpen])
  if (!items.length) return null
  return (
    <>
      <button type="button" className="ss-more" aria-expanded={open}
              onClick={() => setOpen(o => !o)}>
        <Icon name="more" size={14} /> {open ? 'Less' : 'More'}
      </button>
      {open && (
        <nav className="sw-nav" aria-label="More">
          {items.map(item => <NavItem key={item.to} item={item} />)}
        </nav>
      )}
    </>
  )
}

function MobileMore({ items, onSignOut, others }) {
  const [open, setOpen] = useState(false)
  const btn = useRef(null)
  const panel = useRef(null)
  const loc = useLocation()
  useEffect(() => { setOpen(false) }, [loc.pathname])
  useEffect(() => {
    if (!open) return undefined
    const first = panel.current && panel.current.querySelector('a,button')
    first && first.focus()
    const onKey = e => {
      if (e.key === 'Escape') { setOpen(false); btn.current && btn.current.focus() }
    }
    const onDown = e => {
      if (panel.current && !panel.current.contains(e.target) && !btn.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('keydown', onKey)
    document.addEventListener('pointerdown', onDown)
    return () => { document.removeEventListener('keydown', onKey); document.removeEventListener('pointerdown', onDown) }
  }, [open])
  const here = items.some(i => loc.pathname === i.to || loc.pathname.startsWith(i.to + '/'))
  return (
    <div className="ss-mmore">
      <button ref={btn} type="button" className={'ss-mmorebtn' + (here ? ' ss-here' : '')}
              aria-expanded={open} aria-controls="ss-mmore-panel" aria-haspopup="true"
              onClick={() => setOpen(o => !o)}>
        <Icon name="more" size={18} /> More
      </button>
      {open && (
        <div className="ss-mmorepanel" id="ss-mmore-panel" ref={panel}>
          <nav className="sw-nav" aria-label="More pages">
            {items.map(item => <NavItem key={item.to} item={item} />)}
          </nav>
          <OtherWorkspacesLink state={others} className="ss-otherws ss-otherws-menu" />
          <button type="button" className="ss-mmoreout" onClick={onSignOut}>Sign out</button>
        </div>
      )}
    </div>
  )
}

/**
 * OTHER WORKSPACES (#300) — replaces the header's "Switch program" link and
 * "Switch View" dropdown with one entry. It only opens the existing program
 * launcher (/workspaces), which lists the server's authorized destinations
 * and sets the workspace/brand context itself; nothing is chosen here and
 * no access is granted here (every route re-checks on the server).
 *
 * Shown only when /auth/my-contexts lists at least one other destination. If
 * that call fails it says so and offers a retry — never a guessed list.
 */
function useOtherWorkspaces() {
  const [state, setState] = useState({ phase: 'loading', count: 0 })
  const load = useCallback((force) => {
    setState(s => ({ ...s, phase: 'loading' }))
    fetchMyContexts(force ? { force: true } : {})
      .then(c => setState({ phase: 'ready', count: otherWorkspaceCount(c, getCurrentUser()?.role) }))
      .catch(() => setState({ phase: 'error', count: 0 }))
  }, [])
  useEffect(() => { load(false) }, [load])
  return { ...state, retry: () => load(true) }
}

function OtherWorkspacesLink({ state, className }) {
  if (!state) return null
  if (state.phase === 'error') {
    return (
      <div className={className + ' ss-otherws-err'} role="status">
        <span>Couldn’t check your other workspaces.</span>
        <button type="button" onClick={state.retry}>Try again</button>
      </div>
    )
  }
  if (state.phase !== 'ready' || state.count < 1) return null
  return (
    <NavLink to="/workspaces" className={className}
             title="Open the workspace launcher to switch to another business you have access to">
      <Icon name="grid" />
      <span>
        <b>Other Workspaces</b>
        <small>{state.count === 1 ? '1 other business' : state.count + ' other businesses'}</small>
      </span>
      <Icon name="chevright" size={16} />
    </NavLink>
  )
}
