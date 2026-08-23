/**
 * GodShell — Permanent God Mode layout shell.
 * Bloomberg Terminal + SpaceX Mission Control aesthetic.
 * Visual system: #070c18 bg, #2fb6ff blue, #1ef0a8 teal, #f5b942 amber, #ff5f69 red
 */
import { useState, useEffect } from 'react'
import { NavLink, useNavigate, useLocation } from 'react-router-dom'
import { getCurrentUser, logout } from '../api/client'

function Ico({ d, size = 16, children }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
      {d ? <path d={d} /> : children}
    </svg>
  )
}

const ICONS = {
  command:   'M18 3a3 3 0 0 0-3 3v12a3 3 0 0 0 3 3 3 3 0 0 0 3-3 3 3 0 0 0-3-3H6a3 3 0 0 0-3 3 3 3 0 0 0 3 3 3 3 0 0 0 3-3V6a3 3 0 0 0-3-3 3 3 0 0 0-3 3 3 3 0 0 0 3 3h12a3 3 0 0 0 3-3 3 3 0 0 0-3-3z',
  building:  'M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z',
  users:     'M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 7a4 4 0 1 0 0 8 4 4 0 0 0 0-8zM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75',
  activity:  'M22 12h-4l-3 9L9 3l-3 9H2',
  message:   'M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z',
  monitor:   'M2 3h20v14H2zM8 21h8M12 17v4',
  dollar:    'M12 1v22M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6',
  flag:      'M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1zM4 22v-7',
  link:      'M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71',
  shield:    'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z',
  settings:  'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z',
  logout:    'M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9',
  arrowLeft: 'M19 12H5M12 19l-7-7 7-7',
}

const NAV = [
  { label: 'Command Center',  path: '/god',               icon: 'command'  },
  { label: 'Organizations',   path: '/god/organizations', icon: 'building' },
  { label: 'Users',           path: '/god/users-all',     icon: 'users'    },
  { label: 'Activity Feed',   path: '/god/activity',      icon: 'activity' },
  { label: 'Messaging',       path: '/god/messaging',     icon: 'message'  },
  { label: 'System Health',   path: '/god/system-health', icon: 'monitor'  },
  { label: 'Billing & Usage', path: '/god/billing',       icon: 'dollar'   },
  { label: 'Feature Flags',   path: '/god/features',      icon: 'flag'     },
  { label: 'Integrations',    path: '/god/integrations',  icon: 'link'     },
  { label: 'Audit Logs',      path: '/god/audit',         icon: 'shield'   },
  { label: 'Settings',        path: '/god/settings',      icon: 'settings' },
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

export default function GodShell({ children, orgSession = null, onExitOrgSession }) {
  const navigate = useNavigate()
  const location = useLocation()
  const user     = getCurrentUser()

  function handleLogout() { logout(); navigate('/login') }

  return (
    <div style={{ display: 'flex', height: '100vh', background: '#070c18', color: '#c8d6e5',
                  fontFamily: "'Inter', system-ui, sans-serif", fontSize: '13px', overflow: 'hidden' }}>

      {/* ── Sidebar ── */}
      <aside style={{ width: 220, minWidth: 220, background: '#0a1222', borderRight: '1px solid #1a2840',
                      display: 'flex', flexDirection: 'column', flexShrink: 0 }}>

        {/* Brand */}
        <div style={{ padding: '20px 18px 16px', borderBottom: '1px solid #1a2840' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
            <div style={{ width: 8, height: 8, borderRadius: '50%', background: '#1ef0a8',
                          boxShadow: '0 0 8px #1ef0a8', flexShrink: 0 }} />
            <span style={{ color: '#2fb6ff', fontSize: '11px', fontWeight: 700,
                           letterSpacing: '0.14em', textTransform: 'uppercase' }}>GOD MODE</span>
          </div>
          <div style={{ color: '#4a6280', fontSize: '10px', letterSpacing: '0.06em' }}>
            ADVISORFLOW PLATFORM
          </div>
        </div>

        {/* Nav */}
        <nav style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
          {NAV.map(({ label, path, icon }) => {
            const isActive = path === '/god'
              ? location.pathname === '/god'
              : location.pathname.startsWith(path)
            return (
              <NavLink key={path} to={path}
                style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 18px',
                  color: isActive ? '#2fb6ff' : '#5c7a96',
                  background: isActive ? 'rgba(47,182,255,0.06)' : 'transparent',
                  borderLeft: isActive ? '2px solid #2fb6ff' : '2px solid transparent',
                  textDecoration: 'none', fontSize: '12.5px',
                  fontWeight: isActive ? 600 : 400, letterSpacing: '0.02em' }}
                onMouseEnter={e => { if (!isActive) { e.currentTarget.style.color='#8ab4cc'; e.currentTarget.style.background='rgba(47,182,255,0.03)' } }}
                onMouseLeave={e => { if (!isActive) { e.currentTarget.style.color='#5c7a96'; e.currentTarget.style.background='transparent' } }}
              >
                <Ico d={ICONS[icon]} size={14} />{label}
              </NavLink>
            )
          })}
        </nav>

        {/* Footer */}
        <div style={{ padding: '12px 18px', borderTop: '1px solid #1a2840' }}>
          <div style={{ color: '#3a5270', fontSize: '11px', marginBottom: 10 }}>
            {user?.full_name || user?.email}
          </div>
          <button onClick={handleLogout}
            style={{ display: 'flex', alignItems: 'center', gap: 8, background: 'none',
              border: 'none', color: '#3a5270', cursor: 'pointer', fontSize: '12px', padding: 0 }}
          >
            <Ico d={ICONS.logout} size={13} />
            Sign out
          </button>
        </div>
      </aside>

      {/* ── Main area ─────────────────────────────────────────────────── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        {/* Top bar */}
        <header style={{ height: 44, background: '#0a1222', borderBottom: '1px solid #1a2840',
          display: 'flex', alignItems: 'center', padding: '0 20px', gap: 16, flexShrink: 0 }}>
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: 12 }}>
            <span style={{ color: '#2a4060', fontSize: '11px' }}>ADVISORFLOW</span>
            <span style={{ color: '#1a3050' }}>/</span>
            <span style={{ color: '#4a7090', fontSize: '11px', letterSpacing: '0.04em' }}>
              {NAV.find(n => n.path === '/god' ? location.pathname === '/god' : location.pathname.startsWith(n.path))?.label?.toUpperCase() || 'GOD MODE'}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, color: '#3a6080', fontSize: '11px' }}>
            <div style={{ width: 6, height: 6, borderRadius: '50%', background: '#1ef0a8', boxShadow: '0 0 6px #1ef0a8' }} />
            LIVE
          </div>
          <div style={{ color: '#2a4060', fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
            <LiveClock />
          </div>
        </header>

        {/* God Mode Org Session Banner */}
        {orgSession && (
          <div style={{ background: 'rgba(245,185,66,0.1)', borderBottom: '1px solid rgba(245,185,66,0.3)',
            padding: '8px 20px', display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
            <div style={{ width: 7, height: 7, borderRadius: '50%', background: '#f5b942', boxShadow: '0 0 8px #f5b942' }} />
            <span style={{ color: '#f5b942', fontWeight: 700, fontSize: '11px', letterSpacing: '0.1em' }}>GOD MODE SESSION</span>
            <span style={{ color: '#a88030', fontSize: '11px' }}>—</span>
            <span style={{ color: '#c09040', fontSize: '11px' }}>Viewing {orgSession.org_name}</span>
            <div style={{ flex: 1 }} />
            <button onClick={onExitOrgSession}
              style={{ display: 'flex', alignItems: 'center', gap: 6,
                background: 'rgba(245,185,66,0.15)', border: '1px solid rgba(245,185,66,0.4)',
                borderRadius: 3, color: '#f5b942', cursor: 'pointer',
                fontSize: '11px', fontWeight: 600, letterSpacing: '0.06em', padding: '3px 10px' }}
            >
              <Ico d={ICONS.arrowLeft} size={12} />
              RETURN TO PLATFORM
            </button>
          </div>
        )}

        {/* Page content */}
        <main style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden', background: '#070c18' }}>
          {children}
        </main>
      </div>
    </div>
  )
}
