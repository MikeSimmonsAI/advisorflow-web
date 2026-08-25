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
}

/** built:false → destination has no <Route> yet. Shown, but marked, never faked. */
const NAV = [
  { label: 'Command Center',   path: '/god',               icon: 'command',  built: true  },
  { label: 'Platforms',        path: '/god/platforms',     icon: 'layers',   built: false },
  { label: 'Organizations',    path: '/god/organizations', icon: 'building', built: true  },
  { label: 'Users',            path: '/god/users-all',     icon: 'users',    built: false },
  { label: 'Leads',            path: '/god/leads',         icon: 'trending', built: false },
  { label: 'Communications',   path: '/god/messaging',     icon: 'message',  built: false },
  { label: 'Pipeline & Cadence', path: '/god/pipeline',    icon: 'branch',   built: false },
  { label: 'Activity Feed',    path: '/god/activity',      icon: 'activity', built: false },
  { label: 'System Health',    path: '/god/system-health', icon: 'monitor',  built: false },
  { label: 'Billing',          path: '/god/billing',       icon: 'dollar',   built: false },
  { label: 'Feature Flags',    path: '/god/features',      icon: 'flag',     built: false },
  { label: 'Integrations',     path: '/god/integrations',  icon: 'link',     built: false },
  { label: 'Audit & Security', path: '/god/audit',         icon: 'shield',   built: false },
  { label: 'System Settings',  path: '/god/settings',      icon: 'settings', built: false },
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

  function handleLogout() { logout(); navigate('/login') }

  const isActive = (path) =>
    path === '/god' ? location.pathname === '/god' : location.pathname.startsWith(path)
  const current = NAV.find(n => isActive(n.path))
  const railW = collapsed ? 62 : 220

  return (
    <div style={{ display: 'flex', height: '100vh', background: '#02050a', color: '#c8d6e5',
                  fontFamily: "'Inter', system-ui, sans-serif", fontSize: '13px', overflow: 'hidden' }}>
      <GodStyles />

      {/* ── Rail ── */}
      <aside style={{ width: railW, minWidth: railW, background: 'linear-gradient(180deg,rgba(3,9,17,.98),rgba(4,12,22,.98))',
                      borderRight: '1px solid rgba(78,157,211,.17)', display: 'flex', flexDirection: 'column',
                      flexShrink: 0, transition: 'width .16s ease' }}>

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
          {NAV.map(({ label, path, icon, built }) => {
            const active = isActive(path)
            return (
              <NavLink key={path} to={path} title={collapsed ? label + (built ? '' : ' — needs build') : undefined}
                className={`gm-nav-item ${active ? 'gm-active' : ''} ${built ? '' : 'gm-unbuilt'}`}
                style={{ justifyContent: collapsed ? 'center' : 'flex-start', padding: collapsed ? '10px 0' : '9px 16px' }}
              >
                <Ico d={ICONS[icon]} size={14} />
                {!collapsed && <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</span>}
                {!collapsed && !built && <span className="gm-nav-tag">NEEDS BUILD</span>}
              </NavLink>
            )
          })}
        </nav>

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
            <span style={{ color: '#2a4060', fontSize: '11px' }}>ADVISORFLOW</span>
            <span style={{ color: '#1a3050' }}>/</span>
            <span style={{ color: '#4a7090', fontSize: '11px', letterSpacing: '0.04em' }}>
              {(current?.label || 'GOD MODE').toUpperCase()}
            </span>
            {current && !current.built && (
              <span style={{ fontSize: 8, letterSpacing: '.09em', color: '#43607d',
                border: '1px solid #23394f', borderRadius: 3, padding: '2px 5px' }}>NEEDS BUILD</span>
            )}
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
