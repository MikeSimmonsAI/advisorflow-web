/**
 * ExecObserveShell — Executive Observation Mode container.
 *
 * Renders when a brand executive navigates to:
 *   /executive/organizations/:orgId/view
 *
 * What it does:
 *   1. On mount: calls setObservationContext(orgId) so every api call in
 *      child components carries X-Executive-Observe. The server sees this
 *      header, validates the executive's brand_executive membership, validates
 *      that the org belongs to this brand, and injects organization_id in-flight
 *      so require_tenant_user passes. Mutations are blocked server-side.
 *   2. On unmount: calls clearObservationContext() so the header stops being sent.
 *   3. Fetches the org name for the persistent banner.
 *   4. Renders a persistent "EXECUTIVE VIEW — [Org Name] — READ ONLY" banner.
 *   5. Renders a simplified customer nav sidebar.
 *   6. Renders the matched child route inside ObservationProvider.
 *
 * Phase 1: Only the Overview sub-route is wired. Other nav items show
 * a coming-soon placeholder. The shell is separate from Layout.jsx and
 * GodShell deliberately — cross-contamination with owner/customer contexts
 * is a class of bug, not a style choice.
 */
import { useEffect, useState } from 'react'
import { useParams, useNavigate, Outlet, NavLink } from 'react-router-dom'
import { api, setObservationContext, clearObservationContext } from '../../api/client'
import { ObservationProvider } from '../../context/ObservationContext'

// ── Nav items available in Phase 1 observation mode ─────────────────────────
const NAV_ITEMS = [
  { label: 'Overview',  path: 'overview',  icon: '📊', live: true  },
  { label: 'Leads',     path: 'leads',     icon: '👥', live: false },
  { label: 'Replies',   path: 'replies',   icon: '💬', live: false },
  { label: 'Activity',  path: 'activity',  icon: '📅', live: false },
  { label: 'Campaigns', path: 'campaigns', icon: '📣', live: false },
]

export default function ExecObserveShell() {
  const { orgId } = useParams()
  const navigate = useNavigate()
  const [orgName, setOrgName] = useState(null)
  const [loadErr, setLoadErr] = useState(null)

  // ── Activate / deactivate observation context ────────────────────────────
  useEffect(() => {
    if (!orgId) return
    setObservationContext(orgId)
    return () => {
      clearObservationContext()
    }
  }, [orgId])

  // ── Fetch org name for the banner ────────────────────────────────────────
  // Uses the existing executive endpoint — does NOT use observation context
  // (this request fires before setObservationContext completes its first cycle
  // and the endpoint doesn't need tenant injection).
  useEffect(() => {
    if (!orgId) return
    api.get(`/executive/organizations/${orgId}`)
      .then(r => setOrgName(r.name || r.organization?.name || 'Organization'))
      .catch(() => setLoadErr('Could not load organization details.'))
  }, [orgId])

  function exitObservation() {
    clearObservationContext()
    navigate('/executive/organizations')
  }

  const base = `/executive/organizations/${orgId}/view`

  return (
    <div style={s.root}>
      {/* ── Persistent observation banner ── */}
      <div style={s.banner}>
        <span style={s.bannerBadge}>EXECUTIVE VIEW</span>
        <span style={s.bannerOrg}>{orgName || '…'}</span>
        <span style={s.bannerSep}>—</span>
        <span style={s.bannerReadOnly}>READ ONLY</span>
        <button style={s.exitBtn} onClick={exitObservation} title="Back to Executive Suite">
          ← Exit
        </button>
      </div>

      {loadErr && (
        <div style={s.err}>{loadErr}</div>
      )}

      <div style={s.body}>
        {/* ── Sidebar nav ── */}
        <nav style={s.sidebar}>
          <div style={s.sidebarLabel}>WORKSPACE</div>
          {NAV_ITEMS.map(item => (
            item.live ? (
              <NavLink
                key={item.path}
                to={`${base}/${item.path}`}
                style={({ isActive }) => ({
                  ...s.navItem,
                  ...(isActive ? s.navItemActive : {}),
                })}
              >
                <span style={s.navIcon}>{item.icon}</span>
                {item.label}
              </NavLink>
            ) : (
              <div key={item.path} style={{ ...s.navItem, ...s.navItemDisabled }} title="Coming soon in a future phase">
                <span style={s.navIcon}>{item.icon}</span>
                {item.label}
                <span style={s.comingSoon}>soon</span>
              </div>
            )
          ))}
          <div style={s.navDivider} />
          <button style={s.navExitBtn} onClick={exitObservation}>
            ← Back to Suite
          </button>
        </nav>

        {/* ── Customer content area ── */}
        <main style={s.main}>
          <ObservationProvider>
            <Outlet />
          </ObservationProvider>
        </main>
      </div>
    </div>
  )
}

// ── Styles ───────────────────────────────────────────────────────────────────
const s = {
  root: {
    display: 'flex',
    flexDirection: 'column',
    minHeight: '100vh',
    background: 'var(--bg, #f5f7fa)',
    fontFamily: 'system-ui, -apple-system, sans-serif',
  },
  // Observation banner — always visible, high contrast, cannot be dismissed
  banner: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '9px 20px',
    background: '#1a1f36',
    color: '#fff',
    fontSize: 13,
    fontWeight: 600,
    letterSpacing: '0.02em',
    position: 'sticky',
    top: 0,
    zIndex: 1000,
    flexShrink: 0,
  },
  bannerBadge: {
    background: '#3b4eff',
    color: '#fff',
    fontSize: 10,
    fontWeight: 800,
    letterSpacing: '0.1em',
    padding: '2px 8px',
    borderRadius: 4,
    textTransform: 'uppercase',
  },
  bannerOrg: {
    color: '#c7d0ff',
    fontWeight: 700,
    fontSize: 13,
  },
  bannerSep: {
    color: '#555a7a',
  },
  bannerReadOnly: {
    color: '#f87171',
    fontSize: 11,
    fontWeight: 800,
    letterSpacing: '0.1em',
    textTransform: 'uppercase',
    background: 'rgba(248,113,113,0.12)',
    padding: '2px 7px',
    borderRadius: 4,
  },
  exitBtn: {
    marginLeft: 'auto',
    background: 'transparent',
    border: '1px solid #3b4270',
    color: '#9ba3c8',
    padding: '4px 12px',
    borderRadius: 6,
    fontSize: 12,
    cursor: 'pointer',
    fontWeight: 600,
  },
  err: {
    padding: '10px 20px',
    background: '#fef2f2',
    color: '#b91c1c',
    fontSize: 13,
    borderBottom: '1px solid #fecaca',
  },
  body: {
    display: 'flex',
    flex: 1,
    overflow: 'hidden',
  },
  sidebar: {
    width: 200,
    background: '#fff',
    borderRight: '1px solid #e8ecf4',
    padding: '20px 12px',
    display: 'flex',
    flexDirection: 'column',
    gap: 2,
    flexShrink: 0,
  },
  sidebarLabel: {
    fontSize: 10,
    fontWeight: 800,
    color: '#9ca3af',
    letterSpacing: '0.1em',
    padding: '0 8px 10px',
    textTransform: 'uppercase',
  },
  navItem: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
    padding: '8px 10px',
    borderRadius: 7,
    fontSize: 13.5,
    fontWeight: 500,
    color: '#374151',
    textDecoration: 'none',
    cursor: 'pointer',
    border: 'none',
    background: 'transparent',
    width: '100%',
    textAlign: 'left',
  },
  navItemActive: {
    background: '#eef2ff',
    color: '#3b4eff',
    fontWeight: 700,
  },
  navItemDisabled: {
    color: '#9ca3af',
    cursor: 'not-allowed',
  },
  navIcon: {
    fontSize: 15,
    width: 20,
    textAlign: 'center',
    flexShrink: 0,
  },
  comingSoon: {
    marginLeft: 'auto',
    fontSize: 9,
    fontWeight: 700,
    background: '#f3f4f6',
    color: '#9ca3af',
    padding: '1px 5px',
    borderRadius: 3,
    textTransform: 'uppercase',
    letterSpacing: '0.05em',
  },
  navDivider: {
    height: 1,
    background: '#e8ecf4',
    margin: '12px 0',
  },
  navExitBtn: {
    background: 'transparent',
    border: 'none',
    color: '#6b7280',
    fontSize: 13,
    fontWeight: 500,
    cursor: 'pointer',
    padding: '8px 10px',
    textAlign: 'left',
    borderRadius: 7,
  },
  main: {
    flex: 1,
    overflow: 'auto',
  },
}
