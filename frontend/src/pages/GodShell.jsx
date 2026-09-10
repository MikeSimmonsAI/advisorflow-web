/**
 * GodShell — permanent God Mode layout shell.
 *
 * Aug 25 2026: extended, not replaced. What was already here and is PRESERVED —
 * the SVG icon set, the impersonation banner + RETURN TO PLATFORM control, the
 * live clock, sign-out, and the active-route logic. What was ADDED — a
 * collapsible rail, three nav entries from the owner spec (Platforms, Leads,
 * Pipeline & Cadence), and honest NEEDS BUILD markers so navigation never
 * pretends a screen exists.
 *
 * Only two God routes are registered in App.jsx today: /god and
 * /god/organizations. Every other entry is marked `built: false` and routes to
 * the Command Center via the /god/* catch-all. When you build a screen, add its
 * <Route> in App.jsx and flip `built` to true here — nowhere else.
 */
import { useState, useEffect } from 'react'
import { NavLink, useNavigate, useLocation } from 'react-router-dom'
// `api` for the brand list. The rail's BRANDS section is driven by the platform
// records rather than by a constant in this file — see the BRANDS block below.
import { api, getCurrentUser, logout } from '../api/client'
import { classifyRoute, PLATFORM } from '../auth/routeAuthority'
import AppearanceToggle from '../components/AppearanceToggle'
import GodStyles from './god/GodStyles'

function Ico({ d, size = 16, children }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round"
         style={{ flexShrink: 0 }}>
      {d ? <path d={d} /> : children}
    </svg>
  )
}

const ICONS = {
  command:   'M18 3a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3 3 3 0 0 0 3-3 3 3 0 0 0-3-3H6a3 3 0 0 0-3 3 3 3 0 0 0 3 3 3 3 0 0 0 3-3V6a3 3 0 0 0-3-3 3 3 0 0 0-3 3 3 3 0 0 0 3 3h12a3 3 0 0 0 3-3 3 3 0 0 0-3-3z',
  layers:    'M12 2 2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  building:  'M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  users:     'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 7a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  trending:  'M23 6l-9.5 9.5-5-5L1 18M17 6h6v6',
  activity:  'M22 12h-4l-3 9L9 3l-3 9H2',
  message:   'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  branch:    'M6 3v12M18 9a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM6 21a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM18 9a9 9 0 0 1-9 9',
  monitor:   'M2 3h20v14H2zM8 21h8M12 17v4',
  dollar:    'M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6',
  flag:      'M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1zM4 22v-7',
  link:      'M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71',
  shield:    'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  settings:  'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
  logout:    'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  arrowLeft: 'M19 12H5M12 19l-7-7 7-7',
  chevron:   'M9 18l6-6-6-6',
  grid:      'M3 3h7v7H3zM14 3h7v7h-7zM14 14h7v7h-7zM3 14h7v7H3z',
  briefcase: 'M20 7H4a2 2 0 0 0-2 2v10a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2zM16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16',
  globe:     'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20zM2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z',
  external:  'M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6M15 3h6v6M10 14 21 3',
}

/**
 * Where the owner can jump FROM God Mode.
 *
 * `external: true` opens a new tab, so God Mode is still sitting there when you
 * come back. Internal jumps navigate in place — every destination renders
 * GodReturnBar for a god_admin, so there is always a way back without signing
 * in again. That round trip is the whole point; a one-way link is worse than
 * no link.
 */
const JUMP = [
  // ONE ENTRY, AND ONLY BECAUSE IT IS NOT A LINK.
  //
  // Customer App cannot be a NavLink: it must carry the currently-selected org
  // context into the tenant application. Routing through "/" hits
  // HomeRedirect, which reads default_context from /auth/my-contexts and sends
  // God to /executive when executive_contexts exist — ignoring the org entirely.
  // Routing through /workspace/{id} hits WorkspaceRoute → assert_workspace_membership,
  // which deliberately does not exempt god_admin (the org switcher must not
  // enumerate every workspace). The correct path is /god/customer-app, which
  // requires god_admin, reads the org context already set via X-Org-Override,
  // and renders the tenant application directly. action: 'customer_app' tells
  // the render loop to produce a button with navigate() instead of a NavLink.
  //
  // WORKSPACES WAS REMOVED FROM HERE. It is a first-class destination in
  // CUSTOMERS below, and carrying it in both places meant the same rail listed
  // the same screen twice — which reads as two different things and teaches the
  // owner to distrust the grouping. A "Jump To" section earns its place only
  // for destinations that are genuinely a different context; anything that
  // duplicates primary navigation belongs in primary navigation, once.
  { label: 'Customer App',    action: 'customer_app', icon: 'grid',
    hint: 'The tenant application, as an organization sees it' },
]

/**
 * THE PRIMARY NAVIGATION CARRIES WORKING MODULES ONLY.
 *
 * It used to carry seventeen entries, ten of them tagged NEEDS BUILD, all
 * routing to the /god/* catch-all — so two thirds of the owner's navigation was
 * a list of doors that opened onto the same room, and a product with a real
 * control plane read as a prototype.
 *
 * Nothing was faked to remove those tags. Every entry below is a registered
 * route in App.jsx backed by real endpoints. The unfinished work did not
 * disappear either: it is stated once, honestly, in PRODUCT STATUS on the
 * Command Center (COMING NEXT), where each item names what it is actually
 * waiting on. `Roadmap` at the bottom of this rail jumps straight to it.
 *
 * If you build one of those, add its <Route> in App.jsx, flip `live` in
 * ProductStatus.MODULES, and add it here. Three edits, no other bookkeeping.
 */
const NAV = [
  { group: 'COMMAND' },
  { label: 'Command Center',   path: '/god',                  icon: 'command'  },
  // Platform overview is where the owner should LAND — with no customer
  // selected — rather than arriving already inside somebody's tenant.
  { label: 'Platform',         path: '/god/platform',         icon: 'layers'   },

  // ══════════════════════════════════════════════════════════════════════
  // CUSTOMERS — three nouns the platform deliberately keeps separate.
  // ══════════════════════════════════════════════════════════════════════
  //
  // They were adjacent in the rail with no explanation, which made them look
  // like three names for one thing. The backend separates them on purpose and
  // the hints below say why, because an owner who cannot tell them apart picks
  // one at random and concludes the product is confused:
  //
  //   ORGANIZATIONS  the records and their administration
  //   CUSTOMERS      the commercial relationship — lifecycle and Customer 360
  //   WORKSPACES     the live tenant environments people actually work in
  { group: 'CUSTOMERS' },
  { label: 'Organizations',    path: '/god/organizations',    icon: 'building',
    hint: 'Organization records and administration' },
  { label: 'Customers',        path: '/god/customers',        icon: 'globe',
    hint: 'Commercial lifecycle and Customer 360' },
  { label: 'Workspaces',       path: '/god/workspaces',       icon: 'layers',
    hint: 'Live tenant environments — enter one as its brand or customer' },
  { label: 'Users & Identity', path: '/god/users-all',        icon: 'users',
    hint: 'One row per human, every context they hold' },
  { label: 'Manage Access',    path: '/god/access',           icon: 'shield',
    hint: 'Open a person: brands, workspaces, demo, training — corrected in place' },
  { label: 'Demo Suite',       path: '/god/demo-suite',       icon: 'monitor',
    hint: "Each brand's demonstration environment, and who has presented" },
  { label: 'Training',         path: '/god/training',         icon: 'flag',
    hint: 'Who has been asked to learn what, and where they stopped' },
  // Implementations moved here from OPERATIONS: it is the handoff that follows
  // a won customer, so it belongs beside the customer, not beside a scraper.
  { label: 'Implementations',  path: '/god/implementations',  icon: 'branch',
    hint: 'Onboarding handoff for sold customers' },
  // Customer Launches sits directly beneath Implementations because it is the
  // same customers seen from the other side: Implementations is what WE owe
  // them, Launches is what THEY still owe us. Two entries, one record —
  // /god/launch reads the Implementation rows rather than tracking its own.
  { label: 'Customer Launches', path: '/god/launches',        icon: 'branch',
    hint: 'Onboarding intake — including customers who never started' },

  // ══════════════════════════════════════════════════════════════════════
  // SALES & REVENUE — money in, and who earned it.
  // ══════════════════════════════════════════════════════════════════════
  //
  // These four sat under PLATFORM with diagnostics and system health, which
  // put a payment run in the same group as a log viewer. They are one
  // operational domain and they now read as one.
  { group: 'SALES & REVENUE' },
  { label: 'Sales Operations', path: '/god/sales-operations', icon: 'trending',
    hint: 'Pipeline and sales org operations' },
  // Pricing & Comp DEFINES the rules — plans, rates, caps, holdbacks, and who
  // may see or settle them. Sales Compensation is the money those rules
  // produced. Billing & Revenue is what CUSTOMERS pay. Three jobs, three
  // entries: merging any two would put one of them behind a screen name where
  // nobody would think to look for it.
  { label: 'Pricing & Compensation', path: '/god/pricing',    icon: 'dollar',
    hint: 'Discount floors, commission plans, caps' },
  { label: 'Sales Compensation', path: '/god/compensation',   icon: 'briefcase',
    hint: 'The ledger — earned, payable, paid' },
  { label: 'Billing & Revenue', path: '/god/billing',         icon: 'dollar',
    hint: 'What customers pay and who needs chasing' },
  { label: 'Revenue History',  path: '/god/revenue-history', icon: 'trending',
    hint: 'Payment history, invoice status breakdown, plan breakdown' },

  // ══════════════════════════════════════════════════════════════════════
  // LEADS & AUTOMATION
  // ══════════════════════════════════════════════════════════════════════
  //
  // Only what is actually built and routed. Nothing here is a placeholder —
  // the unfinished work is stated once in PRODUCT STATUS on the Command
  // Center, which Roadmap below jumps to.
  { group: 'LEADS & AUTOMATION' },
  // Asks, of the population a user may already reach, WHO MAY ACTUALLY BE
  // CONTACTED on a channel and why not for the rest. A different question from
  // Access Diagnostic, which is why they are not merged.
  { label: 'Lead Qualification', path: '/god/diagnostics/qualification', icon: 'shield',
    hint: 'Who may be contacted on each channel, and why not' },
  // Back-office acquisition, and it now renders in THIS shell rather than the
  // tenant one — see the route comment in App.jsx.
  { label: 'Lead Scraper',     path: '/god/lead-scraper',     icon: 'grid',
    hint: 'Back-office prospecting — import into a chosen customer' },
  { label: 'Lead Browser',     path: '/god/lead-browser',     icon: 'users',
    hint: 'Search and browse all leads across every organization' },

  // ══════════════════════════════════════════════════════════════════════
  // SECURITY & PLATFORM
  // ══════════════════════════════════════════════════════════════════════
  { group: 'SECURITY & PLATFORM' },
  // Asks WHAT MAY THIS PERSON REACH — identity, memberships, workspace
  // resolution, scope. Owner-only by the endpoint behind it, not by the
  // absence of this link.
  { label: 'Access & Permissions', path: '/god/diagnostics/user-access', icon: 'shield',
    hint: 'What a given person can actually reach' },
  // WHICH CUSTOMERS EACH EXECUTIVE OVERSEES. Executive visibility used to be
  // brand-wide with no way to narrow it and no screen that managed it; this is
  // where the per-organization assignment is set.
  { label: 'Executive Access', path: '/god/executive-access', icon: 'shield',
    hint: 'Which customers each executive oversees' },
  { label: 'Audit & Security', path: '/god/audit',            icon: 'shield'   },
  { label: 'System Health',    path: '/god#platform-health',  icon: 'monitor'  },
  { label: 'Roadmap',          path: '/god/roadmap',           icon: 'flag',
    hint: '93 capability items across 15 systems — what is complete, what needs finishing' },

  // ══════════════════════════════════════════════════════════════════════
  // DIAGNOSTICS
  // ══════════════════════════════════════════════════════════════════════
  { group: 'DIAGNOSTICS' },
  { label: 'Twilio Diagnostics', path: '/god/diagnostics/twilio',   icon: 'monitor',
    hint: 'Delivery receipt config and message delivery breakdown' },
  { label: 'Background Jobs',    path: '/god/diagnostics/job-runs', icon: 'activity',
    hint: 'Run history for cadence, AI, and review loops' },
  { label: 'Voice Configuration', path: '/god/voice',               icon: 'settings',
    hint: 'Agent mappings, version pins, attempt policy, test calls' },
  { label: 'Maintenance Ops',    path: '/god/maintenance',           icon: 'tool',
    hint: 'Booking cleanup (dry-run), phone audit — silent, no SMS/email sent' },
]

function LiveClock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  return (
    <span style={{ fontVariantNumeric: 'tabular-nums', letterSpacing: '0.04em' }}>
      {now.toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' })}
      &nbsp;UTC
    </span>
  )
}

const RAIL_KEY = 'af_god_rail_collapsed'
const MOBILE_MAX = 900

/**
 * True when the viewport is phone-sized.
 *
 * The rail is a fixed 248px column in a flex row. On a 390px phone that leaves
 * 142px for the whole control plane, which is what the first Checkpoint 6
 * mobile screenshots showed: a full-height nav with the content sheared off the
 * right edge. Below MOBILE_MAX the rail becomes an overlay drawer that starts
 * closed, so the content gets the whole screen and the navigation is a tap away.
 */
function useIsMobile() {
  const [m, setM] = useState(() => {
    try { return window.matchMedia('(max-width: ' + MOBILE_MAX + 'px)').matches }
    catch (_) { return false }
  })
  useEffect(() => {
    let mq
    try { mq = window.matchMedia('(max-width: ' + MOBILE_MAX + 'px)') } catch (_) { return }
    const on = e => setM(e.matches)
    // Safari below 14 has addListener only.
    if (mq.addEventListener) mq.addEventListener('change', on)
    else mq.addListener(on)
    setM(mq.matches)
    return () => {
      if (mq.removeEventListener) mq.removeEventListener('change', on)
      else mq.removeListener(on)
    }
  }, [])
  return m
}

export default function GodShell({ children, orgSession = null, onExitOrgSession }) {
  const navigate = useNavigate()
  const location = useLocation()
  const user     = getCurrentUser()

  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(RAIL_KEY) === '1' } catch { return false }
  })
  function toggleRail() {
    setCollapsed(c => {
      const next = !c
      try { localStorage.setItem(RAIL_KEY, next ? '1' : '0') } catch { /* private mode */ }
      return next
    })
  }

  const isMobile = useIsMobile()
  const [drawer, setDrawer] = useState(false)
  // Any navigation closes the drawer. Leaving it open over the screen the user
  // just asked for is the classic mobile-nav bug.
  useEffect(() => { setDrawer(false) }, [location.pathname])

  function handleLogout() { logout(); navigate('/login') }

  // A hash entry ("/god#platform-health") is a jump WITHIN the Command Center,
  // so it must never claim the active state — otherwise two rail items light up
  // at once on /god.
  const isActive = (path) => {
    if (!path || path.includes('#')) return false
    return path === '/god' ? location.pathname === '/god' : location.pathname.startsWith(path)
  }
  const current = NAV.find(n => n.path && isActive(n.path))
  // 248, not 220. At 220 the label had ~93px left after the icon, the gap and
  // the NEEDS BUILD tag, so "Pipeline & Cadence", "Communications" and
  // "Audit & Security" were all being ellipsised.
  const railW = isMobile ? 264 : (collapsed ? 62 : 248)

  // Which authority is in force on THIS screen. Everything the God shell is
  // allowed to claim about scope follows from this one answer.
  const onPlatformSurface = classifyRoute(location.pathname) === PLATFORM

  // EVERY BRAND WITH A CONFIGURED SITE, from the platform records.
  //
  // Read once when the rail mounts. A failure here costs the BRANDS section
  // and nothing else — a rail that will not render because a secondary list
  // could not load is worse than a rail with one section missing.
  const [brands, setBrands] = useState([])
  useEffect(() => {
    let live = true
    api.get('/god/platform/overview', { noOrgContext: true })
      .then(r => {
        if (!live) return
        setBrands((r?.platforms || [])
          // Active brands only, and only those with a real destination. A
          // brand with no website_url gets no link rather than a guessed one.
          .filter(p => p.is_active && p.website_url)
          .map(p => ({ id: p.id, name: p.name, website_url: p.website_url })))
      })
      .catch(() => { if (live) setBrands([]) })
    return () => { live = false }
  }, [])

  return (
    <div style={{ display: 'flex', height: '100vh', background: '#02050a', color: '#c8d6e5',
                  fontFamily: "'Inter', system-ui, sans-serif", fontSize: '13px', overflow: 'hidden' }}>
      <GodStyles />

      {/* ── Rail ── */}
      {/* On a phone the rail leaves the flex row entirely and becomes an overlay,
          so the content is not competing with it for width. */}
      {isMobile && drawer ? (
        <div onClick={() => setDrawer(false)}
             style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,.55)', zIndex: 40 }} />
      ) : null}
      <aside style={{ width: railW, minWidth: railW, background: 'linear-gradient(180deg,rgba(3,9,17,.98),rgba(4,12,22,.98))',
                      borderRight: '1px solid rgba(78,157,211,.17)', display: 'flex', flexDirection: 'column',
                      flexShrink: 0, transition: 'transform .18s ease, width .16s ease',
                      ...(isMobile ? {
                        position: 'fixed', top: 0, bottom: 0, left: 0, zIndex: 41,
                        transform: drawer ? 'none' : 'translateX(-100%)',
                        boxShadow: drawer ? '0 0 40px rgba(0,0,0,.6)' : 'none',
                      } : {}) }}>

        {/* Brand */}
        <div style={{ padding: collapsed ? '18px 0 14px' : '20px 16px 16px',
                      borderBottom: '1px solid rgba(78,157,211,.14)',
                      display: 'flex', alignItems: 'center',
                      justifyContent: collapsed ? 'center' : 'space-between', gap: 8 }}>
          {collapsed ? (
            <div title="AdvisorFlow God Mode" style={{
              width: 34, height: 34, borderRadius: 10, display: 'grid', placeItems: 'center',
              fontWeight: 800, letterSpacing: '-.04em', color: '#06111a', fontSize: 12,
              background: 'linear-gradient(135deg,#6fd5ff,#23efb2)', boxShadow: '0 0 22px rgba(57,189,248,.20)',
            }}>AF</div>
          ) : (
            <div style={{ minWidth: 0 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#23efb2',
                              boxShadow: '0 0 8px #23efb2', flexShrink: 0 }} />
                <span style={{ color: '#39bdf8', fontSize: '11px', fontWeight: 700,
                               letterSpacing: '0.14em', textTransform: 'uppercase' }}>GOD MODE</span>
              </div>
              <div style={{ color: '#4a6280', fontSize: '10px', letterSpacing: '0.06em' }}>
                ADVISORFLOW PLATFORM
              </div>
            </div>
          )}
        </div>

        {/* Collapse toggle */}
        <button onClick={toggleRail}
          hidden={isMobile}
          title={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
          style={{ background: 'none', border: 'none', borderBottom: '1px solid rgba(78,157,211,.10)',
            color: '#415b78', cursor: 'pointer', padding: '7px 0', display: 'flex',
            alignItems: 'center', justifyContent: collapsed ? 'center' : 'flex-end',
            paddingRight: collapsed ? 0 : 16, fontFamily: 'inherit' }}>
          <span style={{ display: 'inline-block', transform: collapsed ? 'none' : 'rotate(180deg)', transition: 'transform .16s ease' }}>
            <Ico d={ICONS.chevron} size={13} />
          </span>
        </button>

        {/* Nav */}
        <nav style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', padding: '8px 0' }}>
          {NAV.map((item, i) => {
            if (item.group) {
              // Collapsed, a heading has no room and no icon to stand in for
              // it, so it becomes a hairline rule instead of clipped text.
              return collapsed
                ? <div key={'g' + i} className="gm-nav-rule" />
                : <div key={'g' + i} className="gm-nav-head">{item.group}</div>
            }
            const { label, path, icon } = item
            const active = isActive(path)
            return (
              <NavLink key={path} to={path} title={collapsed ? label : undefined}
                className={`gm-nav-item ${active ? 'gm-active' : ''}`}
                style={{ justifyContent: collapsed ? 'center' : 'flex-start', padding: collapsed ? '10px 0' : '9px 14px' }}
              >
                <Ico d={ICONS[icon]} size={14} />
                {!collapsed && <span className="gm-nav-label">{label}</span>}
              </NavLink>
            )
          })}
        </nav>

        {/* ── Jump to the other sides of the platform ──
            Internal links go in-place; every destination shows GodReturnBar to
            a god_admin so the trip is never one-way. The website opens in a new
            tab, which leaves this window sitting on God Mode. */}
        <div style={{ borderTop: '1px solid rgba(78,157,211,.14)', padding: '8px 0', flexShrink: 0 }}>
          {!collapsed && (
            <div style={{ color: '#33506e', fontSize: 8.5, letterSpacing: '.16em',
                          padding: '2px 14px 7px', fontWeight: 700 }}>
              JUMP TO
            </div>
          )}
          {JUMP.map((item) => {
            const { label, path, icon, hint, action } = item
            // Customer App: dynamic — navigate to the selected workspace via the
            // God-specific entry route, or to the customer list if none selected.
            if (action === 'customer_app') {
              const dest = orgSession?.org_id ? '/god/customer-app' : '/god/customers'
              return (
                <button key="customer-app"
                  className="gm-nav-item gm-jump"
                  title={collapsed ? label + ' — ' + hint : hint}
                  style={{ justifyContent: collapsed ? 'center' : 'flex-start',
                           padding: collapsed ? '10px 0' : '9px 14px',
                           background: 'none', border: 'none', cursor: 'pointer',
                           color: 'inherit', fontFamily: 'inherit', fontSize: 'inherit',
                           width: '100%', textAlign: 'left' }}
                  onClick={() => navigate(dest)}
                >
                  <Ico d={ICONS[icon]} size={14} />
                  {!collapsed && <span className="gm-nav-label">{label}</span>}
                </button>
              )
            }
            // Static entries remain NavLinks.
            return (
              <NavLink key={path} to={path} className="gm-nav-item gm-jump"
                title={collapsed ? label + ' — ' + hint : hint}
                style={{ justifyContent: collapsed ? 'center' : 'flex-start',
                         padding: collapsed ? '10px 0' : '9px 14px' }}
              >
                <Ico d={ICONS[icon]} size={14} />
                {!collapsed && <span className="gm-nav-label">{label}</span>}
              </NavLink>
            )
          })}
        </div>

        {/* ── BRANDS ──────────────────────────────────────────────────────
            EVERY BRAND, FROM THE BRAND RECORDS. NOT ONE HARD-CODED LINK.

            This rail used to render a single "EvoSys Pro Site" — whichever
            brand the current DOMAIN happened to be, falling back to EvoSys Pro
            when it could not tell. On a white-label platform that is the one
            arrangement guaranteed to be wrong: BookaBoost and Harmony & Hustle
            had no destination at all, and a fourth brand would have had none
            either, forever, until somebody edited this file.

            `Platform.website_url` has existed all along and no API returned it.
            It does now, so this list is configuration rather than code: adding
            a brand with a site configured adds its link here with no deploy.

            A BRAND WITH NO SITE CONFIGURED RENDERS NOTHING. Guessing a URL from
            a slug would produce a dead link that looks like a broken product,
            and the whole section disappears when no brand has one. */}
        {brands.length > 0 && (
          <div style={{ borderTop: '1px solid rgba(78,157,211,.14)', padding: '8px 0', flexShrink: 0 }}>
            {!collapsed && (
              <div style={{ color: '#33506e', fontSize: 8.5, letterSpacing: '.16em',
                            padding: '2px 14px 7px', fontWeight: 700 }}>
                BRANDS
              </div>
            )}
            {brands.map(b => (
              <a key={b.id} href={b.website_url} target="_blank" rel="noopener noreferrer"
                className="gm-nav-item gm-jump"
                title={collapsed ? b.name + ' site — opens in a new tab'
                                 : b.name + ' — opens in a new tab, God Mode stays open here'}
                style={{ justifyContent: collapsed ? 'center' : 'flex-start',
                         padding: collapsed ? '10px 0' : '9px 14px' }}
              >
                <Ico d={ICONS.globe} size={14} />
                {!collapsed && <span className="gm-nav-label">{b.name}</span>}
                {!collapsed && <Ico d={ICONS.external} size={11} />}
              </a>
            ))}
          </div>
        )}

        {/* Footer — owner identity + role */}
        <div style={{ padding: collapsed ? '12px 0' : '12px 16px', borderTop: '1px solid rgba(78,157,211,.14)',
                      display: 'flex', flexDirection: 'column', alignItems: collapsed ? 'center' : 'stretch', gap: 9 }}>
          {collapsed ? (
            <span title={`${user?.email} · GOD ADMIN`} style={{ color: '#ffd968', fontSize: 15 }}>⚡</span>
          ) : (
            <>
              <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                <span style={{ color: '#ffd968', fontSize: 11 }}>⚡</span>
                <span style={{ color: '#ffd968', fontSize: 9, fontWeight: 800, letterSpacing: '.12em' }}>GOD ADMIN</span>
              </div>
              <div style={{ color: '#3a5270', fontSize: '11px', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {user?.full_name || user?.email}
              </div>
            </>
          )}
          {/* CHANGE MY OWN PASSWORD.
              The owner's account is the one account the reset action on
              /god/users-all deliberately will NOT act on - that screen refuses
              every self-action, and an administrative reset is the wrong tool
              for your own credential anyway: it does not ask for the current
              password, so a borrowed unlocked browser would be enough. This
              goes to the ordinary self-service change, which requires the
              current password first. Without this link the page existed but
              nothing in God Mode led to it, so the owner had to know the URL.
              A successful change signs every session for the account out, so
              there is deliberately nowhere to come back to - the destination
              afterwards is the sign-in screen. */}
          <button
            onClick={() => navigate('/change-password')}
            title="Change your own password — asks for your current one first"
            style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'none',
              border: 'none', color: '#3a5270', cursor: 'pointer', fontSize: '12px', padding: 0,
              justifyContent: collapsed ? 'center' : 'flex-start', fontFamily: 'inherit' }}
          >
            <Ico d={ICONS.settings} size={13} />
            {!collapsed && 'Change password'}
          </button>
          {/* APPEARANCE. In the rail footer beside the other per-person
              settings, because that is what it is — Mike's choice, not the
              brand's. Hidden when the rail is collapsed rather than shrunk to
              three ambiguous glyphs in a 62px column. */}
          {!collapsed && (
            <div style={{ paddingTop: 2 }}>
              <AppearanceToggle compact />
            </div>
          )}
          <button onClick={handleLogout} title="Sign out"
            style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'none',
              border: 'none', color: '#3a5270', cursor: 'pointer', fontSize: '12px', padding: 0,
              justifyContent: collapsed ? 'center' : 'flex-start', fontFamily: 'inherit' }}
          >
            <Ico d={ICONS.logout} size={13} />
            {!collapsed && 'Sign out'}
          </button>
        </div>
      </aside>

      {/* ── Main area ── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
        <header style={{ height: 44, background: '#06101d', borderBottom: '1px solid rgba(72,147,200,.18)',
          display: 'flex', alignItems: 'center', padding: '0 20px', gap: 16, flexShrink: 0 }}>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 12, minWidth: 0 }}>
            {isMobile ? (
              <button onClick={() => setDrawer(d => !d)} aria-label="Navigation"
                      style={{ background: 'none', border: '1px solid rgba(78,157,211,.28)',
                               borderRadius: 6, color: '#7fb2d8', cursor: 'pointer',
                               padding: '4px 9px', fontSize: 14, lineHeight: 1,
                               fontFamily: 'inherit', flexShrink: 0 }}>☰</button>
            ) : null}
            {!isMobile ? <span style={{ color: '#2a4060', fontSize: '11px' }}>ADVISORFLOW</span> : null}
            {!isMobile ? <span style={{ color: '#1a3050' }}>/</span> : null}
            <span style={{ color: '#4a7090', fontSize: '11px', letterSpacing: '0.04em',
                           overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {(current?.label || 'GOD MODE').toUpperCase()}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#3a6080', fontSize: '11px' }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#23efb2', boxShadow: '0 0 6px #23efb2' }} />
            LIVE
          </div>
          <div style={{ color: '#2a4060', fontSize: '11px' }}><LiveClock /></div>
        </header>

        {/* ══════════════════════════════════════════════════════════════
            THE AUTHORITY BAR. WHAT THIS SCREEN WILL ACTUALLY AFFECT.
            ══════════════════════════════════════════════════════════════

            THE BUG THIS FIXES. This used to render "GOD MODE SESSION —
            VIEWING AS: Restland Cemetery and Funeral Home" over EVERY God
            screen the moment a customer had been entered — Sales Compensation,
            the Lead Scraper, Billing & Revenue, System Health. It was not just
            confusing: the API client was also SENDING that customer's
            X-Org-Override on those requests, and the server answers that
            header by setting `user.organization_id`, so the banner was
            describing something that really was happening.

            The client no longer sends the override outside customer space
            (see auth/routeAuthority.js), so on a platform screen the honest
            statement is PLATFORM — and the remembered customer is shown as a
            RETURN AFFORDANCE rather than as a scope, because it no longer
            scopes anything here.

            Three states, and each says which authority is in force:
              PLATFORM        a God/back-office tool, estate-wide
              CUSTOMER VIEW   operating as that customer, override in force
            The customer state keeps the amber treatment precisely because it
            is the one where somebody else's records are about to change. */}
        <div style={{
          background: onPlatformSurface ? 'rgba(47,182,255,0.07)' : 'rgba(245,185,66,0.1)',
          borderBottom: '1px solid ' + (onPlatformSurface
            ? 'rgba(47,182,255,0.22)' : 'rgba(245,185,66,0.3)'),
          padding: '8px 20px', display: 'flex', alignItems: 'center', gap: 12,
          flexShrink: 0, flexWrap: 'wrap' }}>
          <div style={{ width: 7, height: 7, borderRadius: '50%',
            background: onPlatformSurface ? '#2fb6ff' : '#f5b942',
            boxShadow: '0 0 8px ' + (onPlatformSurface ? '#2fb6ff' : '#f5b942') }} />
          {onPlatformSurface ? (
            <>
              <span style={{ color: '#2fb6ff', fontWeight: 700, fontSize: '11px',
                             letterSpacing: '0.1em' }}>PLATFORM</span>
              <span style={{ color: '#3a6a90', fontSize: '11px' }}>—</span>
              <span style={{ color: '#5d90b4', fontSize: '11px' }}>
                AdvisorFlow Platform · estate-wide, not scoped to a customer
              </span>
              <div style={{ flex: 1 }} />
              {/* The remembered selection, offered as a way BACK rather than
                  claimed as this screen's scope. */}
              {orgSession && (
                <button onClick={() => navigate('/god/customer-app')}
                  title={'Return to ' + orgSession.org_name + "'s workspace"}
                  style={{ display: 'flex', alignItems: 'center', gap: 6,
                    background: 'rgba(47,182,255,0.12)', border: '1px solid rgba(47,182,255,0.32)',
                    borderRadius: 3, color: '#7cc7f5', cursor: 'pointer', fontFamily: 'inherit',
                    fontSize: '11px', fontWeight: 600, padding: '3px 10px', maxWidth: 320,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  RESUME: {orgSession.org_name}
                </button>
              )}
              {orgSession && (
                <button onClick={onExitOrgSession}
                  title="Forget the remembered customer entirely"
                  style={{ background: 'none', border: '1px solid rgba(120,150,175,0.28)',
                    borderRadius: 3, color: '#6f8ba5', cursor: 'pointer', fontFamily: 'inherit',
                    fontSize: '11px', fontWeight: 600, padding: '3px 10px' }}>
                  CLEAR
                </button>
              )}
            </>
          ) : orgSession ? (
            <>
              <span style={{ color: '#f5b942', fontWeight: 700, fontSize: '11px',
                             letterSpacing: '0.1em' }}>CUSTOMER WORKSPACE</span>
              <span style={{ color: '#a88030', fontSize: '11px' }}>—</span>
              <span style={{ color: '#c09040', fontSize: '11px' }}>
                VIEWING AS: {orgSession.org_name}
              </span>
              <div style={{ flex: 1 }} />
              <button onClick={onExitOrgSession}
                style={{ display: 'flex', alignItems: 'center', gap: 6,
                  background: 'rgba(245,185,66,0.15)', border: '1px solid rgba(245,185,66,0.4)',
                  borderRadius: 3, color: '#f5b942', cursor: 'pointer', fontFamily: 'inherit',
                  fontSize: '11px', fontWeight: 600, letterSpacing: '0.06em', padding: '3px 10px' }}
              >
                <Ico d={ICONS.arrowLeft} size={12} />
                EXIT ORGANIZATION VIEW
              </button>
            </>
          ) : null}
        </div>

        <main style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', background: '#02050a' }}>
          {children}
        </main>
      </div>
    </div>
  )
}
