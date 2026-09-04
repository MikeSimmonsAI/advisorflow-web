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
import { getCurrentUser, logout } from '../api/client'
import { detectTheme, BRAND_CONFIG } from '../theme'
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
  // Customer App is NOT a static NavLink. It must carry the currently-selected
  // org context into the tenant application. Routing through "/" hits
  // HomeRedirect, which reads default_context from /auth/my-contexts and sends
  // God to /executive when executive_contexts exist — ignoring the org entirely.
  // Routing through /workspace/{id} hits WorkspaceRoute → assert_workspace_membership,
  // which deliberately does not exempt god_admin (the org switcher must not
  // enumerate every workspace). The correct path is /god/customer-app, which
  // requires god_admin, reads the org context already set via X-Org-Override,
  // and renders the tenant application directly. action: 'customer_app' tells
  // the render loop to produce a button with navigate() instead of a NavLink.
  { label: 'Customer App',    action: 'customer_app', icon: 'grid',
    hint: 'The tenant application, as an organization sees it' },
  // WAS: a single 'Sales Workspace' jump straight to /sales, with the brand
  // named in this literal — 'EvoSys Pro brand sales'. There was no way to pick
  // another brand, and /sales returned EVERY brand's pipeline for the owner, so
  // a second brand would have silently merged two companies' deals onto one
  // screen. Workspaces is the doorway now, and it is driven by the platform
  // records rather than by this file.
  { label: 'Workspaces',      path: '/god/workspaces', icon: 'briefcase',
    hint: 'Choose a brand, then its sales workspace or one of its customers' },
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
  { label: 'Organizations',    path: '/god/organizations',    icon: 'building' },
  { label: 'Customers',        path: '/god/customers',        icon: 'globe'    },
  { label: 'Workspaces',       path: '/god/workspaces',       icon: 'layers'   },
  { label: 'Users & Identity', path: '/god/users-all',        icon: 'users'    },

  { group: 'OPERATIONS' },
  { label: 'Sales Operations', path: '/god/sales-operations', icon: 'trending' },
  // P7's cross-organization billing. NOT the customer /billing screen: that
  // one is scoped to whichever workspace the caller is standing in, this one
  // is the whole book, and only a god_admin holding `platform_billing` can
  // load anything behind it.
  { label: 'Billing',          path: '/god/billing',          icon: 'dollar'   },
  { label: 'Implementations',  path: '/god/implementations',  icon: 'branch'   },
  { label: 'Lead Scraper',     path: '/scraper',              icon: 'grid'     },

  { group: 'PLATFORM' },
  // Control-plane diagnostics. Owner-only by the endpoint behind it, not by
  // the absence of this link.
  { label: 'Access Diagnostic', path: '/god/diagnostics/user-access', icon: 'shield' },
  // TWO DIAGNOSTICS, AND THEY ANSWER DIFFERENT QUESTIONS. Access asks WHAT MAY
  // THIS PERSON REACH - identity, memberships, workspace resolution, scope.
  // Qualification asks, of the population they may already reach, WHO MAY
  // ACTUALLY BE CONTACTED on a channel, and why not for the rest. Merging them
  // would produce one screen that answers neither question well.
  { label: 'Lead Qualification', path: '/god/diagnostics/qualification', icon: 'shield' },
  { label: 'Audit & Security', path: '/god/audit',            icon: 'shield'   },
  { label: 'System Health',    path: '/god#platform-health',  icon: 'monitor'  },
  { label: 'Roadmap',          path: '/god#product-status',   icon: 'flag'     },
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

  // The marketing site for whichever brand this domain is. The AdvisorFlow
  // (god) brand deliberately has no websiteUrl — there is no public AdvisorFlow
  // site — so fall back to EvoSys Pro rather than rendering a dead link.
  const brand = BRAND_CONFIG[detectTheme()] || {}
  const websiteUrl = brand.websiteUrl || BRAND_CONFIG.evosyspro?.websiteUrl
  const websiteLabel = brand.websiteUrl
    ? (brand.displayName || 'Website')
    : (BRAND_CONFIG.evosyspro?.displayName || 'Website')

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
          {websiteUrl && (
            <a href={websiteUrl} target="_blank" rel="noopener noreferrer"
              className="gm-nav-item gm-jump"
              title={collapsed ? websiteLabel + ' website — opens in a new tab'
                               : 'Opens in a new tab, so God Mode stays open here'}
              style={{ justifyContent: collapsed ? 'center' : 'flex-start',
                       padding: collapsed ? '10px 0' : '9px 14px' }}
            >
              <Ico d={ICONS.globe} size={14} />
              {!collapsed && <span className="gm-nav-label">{websiteLabel} Site</span>}
              {!collapsed && <Ico d={ICONS.external} size={11} />}
            </a>
          )}
        </div>

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

        {/* God Mode Org Session Banner — PRESERVED. Never let the owner forget the tenant. */}
        {orgSession && (
          <div style={{ background: 'rgba(245,185,66,0.1)', borderBottom: '1px solid rgba(245,185,66,0.3)',
            padding: '8px 20px', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0, flexWrap: 'wrap' }}>
            <div style={{ width: 7, height: 7, borderRadius: '50%', background: '#f5b942', boxShadow: '0 0 8px #f5b942' }} />
            <span style={{ color: '#f5b942', fontWeight: 700, fontSize: '11px', letterSpacing: '0.1em' }}>GOD MODE SESSION</span>
            <span style={{ color: '#a88030', fontSize: '11px' }}>—</span>
            <span style={{ color: '#c09040', fontSize: '11px' }}>VIEWING AS: {orgSession.org_name}</span>
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
          </div>
        )}

        <main style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', background: '#02050a' }}>
          {children}
        </main>
      </div>
    </div>
  )
}
