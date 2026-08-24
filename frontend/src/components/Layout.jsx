import { useEffect, useState } from 'react'
import { NavLink, useNavigate, useLocation } from 'react-router-dom'
import { getCurrentUser, refreshCurrentUser, logout, getBranding, applyBrandingCSS, applyBrandingDOM, fetchAndStoreBranding, getOrgContext, setOrgContext, clearOrgContext, api, stopKeepAlive, stopRefreshLoop } from '../api/client'
import { detectTheme, BRAND_CONFIG, THEMES } from '../theme.js'
import SignalPulse from './SignalPulse'
import NotificationBell from './NotificationBell'
import ProfileOnboarding from './ProfileOnboarding'
import './Layout.css'

// Detect which platform brand is running on this hostname — resolved once at module
// load time so it never changes mid-session.
const PLATFORM_THEME = detectTheme()
const PLATFORM_BRAND = BRAND_CONFIG[PLATFORM_THEME]

// Advisor-level nav — every logged-in user sees these
const NAV_ITEMS = [
  { to: '/', label: 'Overview', icon: 'grid' },
  { to: '/leads', label: 'Leads', icon: 'users' },
  { to: '/replies', label: 'Replies', icon: 'message' },
  { to: '/ai-hub', label: 'AI Hub', icon: 'cpu' },
  { to: '/email-queue', label: 'Email Queue', icon: 'mail' },
  { to: '/activity', label: 'Activity', icon: 'send' },
  { to: '/availability', label: 'Availability', icon: 'calendar' },
  { to: '/re-engagement', label: 'Re-engagement', icon: 'thermometer' },
  { to: '/compliance', label: 'DNC List', icon: 'shield-check' },
  { to: '/settings', label: 'Settings', icon: 'settings' },
  { to: '/fiber-capture', label: 'Fiber Lead', icon: 'zap', fiberOnly: true },
]

// Admin-only nav items — always visible to org_admin and above (no feature flag)
const ADMIN_ONLY_NAV_ITEMS = [
  { to: '/billing',      label: 'Billing',       icon: 'credit-card' },
  { to: '/cadence',      label: 'Cadence',        icon: 'repeat' },
  { to: '/system-health',label: 'System Health',  icon: 'activity' },
]

const ADMIN_NAV_ITEMS = [
  { to: '/admin',            label: 'Master Dashboard',   icon: 'shield',       featureKey: 'master_dashboard' },
  { to: '/reports',          label: 'Reports',            icon: 'activity',     featureKey: 'reports' },
  { to: '/users',            label: 'Users',              icon: 'user-plus',    featureKey: 'users' },
  { to: '/campaigns',        label: 'Campaigns',          icon: 'target',       featureKey: 'campaigns' },
  { to: '/crm',              label: 'CRM',                icon: 'database',     featureKey: null },
  { to: '/crm-connectors',   label: 'CRM Connectors',     icon: 'link',         featureKey: null },
  { to: '/lead-cleanup',     label: 'Lead Cleanup',       icon: 'users',        featureKey: 'lead_cleanup' },
  { to: '/tier-definitions', label: 'Tier Config',        icon: 'layers',       featureKey: 'tier_config' },
  { to: '/10dlc',            label: 'A2P 10DLC',          icon: 'shield-check', featureKey: 'a2p_10dlc' },
  { to: '/org-settings',     label: 'Branding & Settings',icon: 'settings',    featureKey: 'branding_settings' },
  { to: '/audit-log',        label: 'Audit Log',          icon: 'activity',     featureKey: 'audit_log' },
]

// Platform Admin — super admin only, always visible
const SUPER_ADMIN_NAV_ITEMS = [
  { to: '/provision-client', label: 'Provision Client', icon: 'user-plus' },
  { to: '/templates', label: 'Templates', icon: 'file-text' },
  { to: '/cadence-templates', label: 'Cadence Builder', icon: 'sliders' },
  { to: '/orgs', label: 'Org Manager', icon: 'building' },
]

function Icon({ name }) {
  const paths = {
    grid: <path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z" />,
    users: <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />,
    message: <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />,
    repeat: <path d="M17 1l4 4-4 4M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4M21 13v2a4 4 0 0 1-4 4H3" />,
    mail: <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2zM22 6l-10 7L2 6" />,
    zap: <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />,
    send: <path d="M22 2L11 13M22 2L15 22l-4-9-9-4 20-7z" />,
    settings: <path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />,
    shield: <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />,
    'shield-check': <><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" /><path d="M9 12l2 2 4-4" /></>,
    'file-text': <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zM14 2v6h6M16 13H8M16 17H8M10 9H8" />,
    'user-plus': <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM20 8v6M23 11h-6" />,
    target: <><circle cx="12" cy="12" r="10" /><circle cx="12" cy="12" r="6" /><circle cx="12" cy="12" r="2" /></>,
    activity: <path d="M22 12h-4l-3 9L9 3l-3 9H2" />,
    sliders: <><line x1="4" y1="21" x2="4" y2="14" /><line x1="4" y1="10" x2="4" y2="3" /><line x1="12" y1="21" x2="12" y2="12" /><line x1="12" y1="8" x2="12" y2="3" /><line x1="20" y1="21" x2="20" y2="16" /><line x1="20" y1="12" x2="20" y2="3" /><line x1="1" y1="14" x2="7" y2="14" /><line x1="9" y1="8" x2="15" y2="8" /><line x1="17" y1="16" x2="23" y2="16" /></>,
    calendar: <><rect x="3" y="4" width="18" height="18" rx="2" ry="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" /></>,
    cpu: <><rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" /><line x1="9" y1="1" x2="9" y2="4" /><line x1="15" y1="1" x2="15" y2="4" /><line x1="9" y1="20" x2="9" y2="23" /><line x1="15" y1="20" x2="15" y2="23" /><line x1="20" y1="9" x2="23" y2="9" /><line x1="20" y1="14" x2="23" y2="14" /><line x1="1" y1="9" x2="4" y2="9" /><line x1="1" y1="14" x2="4" y2="14" /></>,
    phone: <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07A19.5 19.5 0 0 1 4.69 13a19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 3.6 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L7.91 9.91a16 16 0 0 0 6.08 6.08l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92z" />,
    sun: <><circle cx="12" cy="12" r="5" /><line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" /><line x1="4.22" y1="4.22" x2="5.64" y2="5.64" /><line x1="18.36" y1="18.36" x2="19.78" y2="19.78" /><line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" /><line x1="4.22" y1="19.78" x2="5.64" y2="18.36" /><line x1="18.36" y1="5.64" x2="19.78" y2="4.22" /></>,
    moon: <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />,
    link: <><path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" /><path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" /></>,
    building: <><rect x="2" y="7" width="20" height="15" rx="1" /><line x1="16" y1="22" x2="16" y2="7" /><line x1="2" y1="12" x2="22" y2="12" /><path d="M7 22v-5h4v5" /><polyline points="2 7 2 5 22 5 22 7" /></>,
    layers: <><polygon points="12 2 2 7 12 12 22 7 12 2" /><polyline points="2 17 12 22 22 17" /><polyline points="2 12 12 17 22 12" /></>,
    thermometer: <><path d="M14 14.76V3.5a2.5 2.5 0 0 0-5 0v11.26a4.5 4.5 0 1 0 5 0z" /></>,
    database: <><ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" /><path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" /></>,
    'credit-card': <><rect x="1" y="4" width="22" height="16" rx="2" ry="2" /><line x1="1" y1="10" x2="23" y2="10" /></>,
    search: <><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></>,
  }
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {paths[name]}
    </svg>
  )
}

function LiveClock() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  return (
    <div className="top-bar-clock">
      <span className="top-bar-time">
        {now.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })}
      </span>
      <span className="top-bar-date">
        {now.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })}
      </span>
    </div>
  )
}

function ThemeToggle() {
  const isBrandTheme = PLATFORM_THEME !== THEMES.BOOKABOOST
  const [dark, setDark] = useState(() => {
    if (isBrandTheme) return true
    const saved = localStorage.getItem('af_theme')
    return saved !== 'light'
  })
  useEffect(() => {
    if (isBrandTheme) return
    document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light')
    localStorage.setItem('af_theme', dark ? 'dark' : 'light')
  }, [dark, isBrandTheme])
  if (isBrandTheme) return null
  return (
    <button className="theme-toggle" onClick={() => setDark(!dark)} title={dark ? 'Switch to light mode' : 'Switch to dark mode'}>
      <Icon name={dark ? 'sun' : 'moon'} />
    </button>
  )
}

export default function Layout({ children }) {
  const [user, setUser] = useState(() => getCurrentUser())
  const navigate = useNavigate()
  const location = useLocation()
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [profilePhoto, setProfilePhoto] = useState(null)
  const [logoFailed, setLogoFailed] = useState(false)
  const isSuperAdmin = user?.role === 'super_admin'
  const isGodAdmin = user?.role === 'god_admin'
  const isElevated = isSuperAdmin || isGodAdmin
  const [orgContext, setOrgCtx] = useState(() => isElevated ? getOrgContext() : null)
  const [branding, setBranding] = useState(() => isElevated ? null : getBranding())
  const [allOrgs, setAllOrgs] = useState([])
  const [orgPickerOpen, setOrgPickerOpen] = useState(false)

  const enabledFeatures = isElevated ? null : (branding?.enabled_features ?? null)
  const isFeatureEnabled = (key) => !key || enabledFeatures === null || enabledFeatures.includes(key)

  function handleExitOrg() {
    clearOrgContext()
    setOrgCtx(null)
    window.location.href = '/'
  }

  // For god_admin: fetch all orgs so they can switch into any org's view
  useEffect(() => {
    if (!isGodAdmin) return
    api.get('/god/orgs?limit=200').then(data => {
      const list = Array.isArray(data) ? data : (data?.orgs || [])
      setAllOrgs(list)
    }).catch(() => {})
  }, [isGodAdmin])

  // Close org-picker when clicking outside
  useEffect(() => {
    if (!orgPickerOpen) return
    function handleOutsideClick(e) {
      if (!e.target.closest('.god-org-picker')) setOrgPickerOpen(false)
    }
    document.addEventListener('mousedown', handleOutsideClick)
    return () => document.removeEventListener('mousedown', handleOutsideClick)
  }, [orgPickerOpen])

  function handleOrgSelect(org) {
    setOrgPickerOpen(false)
    if (!org) {
      clearOrgContext()
      setOrgCtx(null)
      window.location.href = '/god'
    } else {
      setOrgContext(org.id, org.name)
      setOrgCtx({ orgId: org.id, orgName: org.name })
      window.location.href = '/'
    }
  }

  useEffect(() => {
    if (isElevated) return
    const stored = getBranding()
    if (stored) { applyBrandingCSS(stored); applyBrandingDOM(stored) }
    fetchAndStoreBranding().then(b => { if (b) setBranding(b) })
  }, [isElevated, location.pathname])

  useEffect(() => {
    refreshCurrentUser().then(p => {
      if (p?.profile_photo_url) setProfilePhoto(p.profile_photo_url)
      setUser(getCurrentUser())
    }).catch(() => {})
  }, [])

  function closeSidebar() { if (window.innerWidth <= 1024) setSidebarOpen(false) }

  async function handleLogout() {
    stopKeepAlive()
    stopRefreshLoop()
    await logout()
    window.location.href = '/login'
  }

  const brandName = isGodAdmin ? 'AdvisorFlow' : (branding?.brand_name || PLATFORM_BRAND.displayName)
  const logoUrl = isElevated
    ? (PLATFORM_BRAND.logoUrl || null)
    : (branding?.brand_logo_url || PLATFORM_BRAND.logoUrl || null)

  // Reset logo failure state when the URL changes (e.g. org switch)
  useEffect(() => { setLogoFailed(false) }, [logoUrl])

  return (
    <div className={`layout ${sidebarOpen ? 'layout--sidebar-open' : ''}`}>
      <button type="button" className="mobile-menu-btn" onClick={() => setSidebarOpen(true)} aria-label="Open navigation menu">
        <span /><span /><span />
      </button>
      <button type="button" className="sidebar-backdrop" onClick={closeSidebar} aria-label="Close navigation menu" />

      <aside className="sidebar" style={isGodAdmin ? { borderRight: '1px solid rgba(245,158,11,0.3)', background: 'linear-gradient(180deg, rgba(245,158,11,0.06) 0%, transparent 120px)' } : {}}>
        <div className="sidebar-brand" style={isGodAdmin ? { borderBottom: '1px solid rgba(245,158,11,0.25)' } : {}}>
          {isGodAdmin ? (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', gap: 2 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: 20, lineHeight: 1 }}>⚡</span>
                <span className="brand-mark" style={{ color: '#f59e0b', letterSpacing: '0.04em' }}>AdvisorFlow</span>
              </div>
              <span style={{ fontSize: 10, color: '#b45309', fontWeight: 600, letterSpacing: '0.12em', textTransform: 'uppercase', paddingLeft: 28 }}>God Mode</span>
            </div>
          ) : logoUrl && !logoFailed ? (
            <img
              src={logoUrl}
              alt={brandName}
              style={{ height: 72, maxWidth: 180, objectFit: 'contain', borderRadius: 6, display: 'block', margin: '0 auto' }}
              onError={() => setLogoFailed(true)}
            />
          ) : (
            <><SignalPulse color="blue" size={9} /><span className="brand-mark">{brandName}</span></>
          )}
          <button type="button" className="sidebar-close-btn" onClick={closeSidebar} aria-label="Close">×</button>
        </div>

        <nav className="sidebar-nav">
          {/* God admin: Command Center + org switcher */}
          {isGodAdmin && (
            <>
              <NavLink to="/god"
                className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
                style={({ isActive }) => ({
                  color: isActive ? '#f59e0b' : '#d97706',
                  background: isActive ? 'rgba(245,158,11,0.12)' : 'transparent',
                  borderLeft: isActive ? '3px solid #f59e0b' : '3px solid transparent',
                  fontWeight: 600,
                })}
                onClick={closeSidebar}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
                </svg>
                Command Center
              </NavLink>

              {/* Org switcher — lets god_admin enter any org's regular app view */}
              <div className="god-org-picker" style={{ position: 'relative', padding: '6px 10px' }}>
                <button
                  type="button"
                  onClick={() => setOrgPickerOpen(o => !o)}
                  style={{
                    width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    gap: 6, padding: '7px 10px',
                    background: orgPickerOpen ? 'rgba(245,158,11,0.15)' : 'rgba(245,158,11,0.08)',
                    border: '1px solid rgba(245,158,11,0.3)', borderRadius: 6,
                    color: orgContext ? '#fbbf24' : '#92400e',
                    fontSize: 12, fontWeight: 600, cursor: 'pointer', letterSpacing: '0.02em',
                  }}
                >
                  <span style={{ display: 'flex', alignItems: 'center', gap: 5, minWidth: 0 }}>
                    <span style={{ fontSize: 13 }}>{orgContext ? '👁' : '🌐'}</span>
                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {orgContext ? orgContext.orgName : 'All Orgs (God View)'}
                    </span>
                  </span>
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"
                    style={{ flexShrink: 0, transform: orgPickerOpen ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>
                    <polyline points="6 9 12 15 18 9" />
                  </svg>
                </button>

                {orgPickerOpen && (
                  <div style={{
                    position: 'absolute', top: 'calc(100% - 2px)', left: 10, right: 10, zIndex: 200,
                    background: 'var(--surface-2, #1a1a2e)', border: '1px solid rgba(245,158,11,0.35)',
                    borderRadius: 6, boxShadow: '0 8px 24px rgba(0,0,0,0.5)', maxHeight: 260, overflowY: 'auto', fontSize: 12,
                  }}>
                    <button type="button" onClick={() => handleOrgSelect(null)} style={{
                      width: '100%', textAlign: 'left', padding: '9px 12px',
                      background: !orgContext ? 'rgba(245,158,11,0.15)' : 'transparent',
                      color: !orgContext ? '#fbbf24' : '#a3a3a3',
                      border: 'none', borderBottom: '1px solid rgba(245,158,11,0.15)', cursor: 'pointer',
                      fontWeight: !orgContext ? 700 : 500, display: 'flex', alignItems: 'center', gap: 6,
                    }}>
                      <span>🌐</span> All Orgs (God View)
                      {!orgContext && <span style={{ marginLeft: 'auto', color: '#f59e0b' }}>✓</span>}
                    </button>
                    {allOrgs.length === 0 && (
                      <div style={{ padding: '10px 12px', color: '#6b7280', fontStyle: 'italic' }}>Loading orgs…</div>
                    )}
                    {allOrgs.map(org => (
                      <button key={org.id} type="button" onClick={() => handleOrgSelect(org)} style={{
                        width: '100%', textAlign: 'left', padding: '8px 12px',
                        background: orgContext?.orgId === org.id ? 'rgba(245,158,11,0.12)' : 'transparent',
                        color: orgContext?.orgId === org.id ? '#fbbf24' : '#d1d5db',
                        border: 'none', borderBottom: '1px solid rgba(255,255,255,0.05)', cursor: 'pointer',
                        fontWeight: orgContext?.orgId === org.id ? 600 : 400,
                        display: 'flex', alignItems: 'center', gap: 6,
                      }}>
                        <span style={{ fontSize: 11, opacity: 0.6 }}>🏢</span>
                        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{org.name}</span>
                        {orgContext?.orgId === org.id && <span style={{ marginLeft: 'auto', color: '#f59e0b', flexShrink: 0 }}>✓</span>}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              <NavLink to="/scraper"
                className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
                style={({ isActive }) => ({
                  color: isActive ? '#f59e0b' : '#d97706',
                  background: isActive ? 'rgba(245,158,11,0.12)' : 'transparent',
                  borderLeft: isActive ? '3px solid #f59e0b' : '3px solid transparent',
                  fontWeight: 600,
                })}
                onClick={closeSidebar}
              >
                <Icon name="search" />
                Lead Scraper
              </NavLink>

              <div className="nav-divider" />
            </>
          )}

          {NAV_ITEMS.filter(item => !item.fiberOnly || (branding && branding.industry === 'fiber')).map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === '/'}
              className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
              onClick={closeSidebar}
            >
              <Icon name={item.icon} />{item.label}
            </NavLink>
          ))}

          {(user?.role === 'org_admin' || user?.role === 'super_admin' || isGodAdmin) && (
            <>
              <div className="nav-divider" />
              {ADMIN_ONLY_NAV_ITEMS.map((item) => (
                <NavLink key={item.to} to={item.to}
                  className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
                  onClick={closeSidebar}
                >
                  <Icon name={item.icon} />{item.label}
                </NavLink>
              ))}
              {ADMIN_NAV_ITEMS.filter(item => isFeatureEnabled(item.featureKey)).map((item) => (
                <NavLink key={item.to} to={item.to}
                  className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
                  onClick={closeSidebar}
                >
                  <Icon name={item.icon} />{item.label}
                </NavLink>
              ))}
            </>
          )}

          {(user?.role === 'super_admin' || isGodAdmin) && (
            <>
              <div className="nav-divider" />
              <div className="nav-section-label" style={isGodAdmin ? { color: '#b45309' } : {}}>Platform Admin</div>
              {SUPER_ADMIN_NAV_ITEMS.map((item) => (
                <NavLink key={item.to} to={item.to}
                  className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
                  onClick={closeSidebar}
                >
                  <Icon name={item.icon} />{item.label}
                </NavLink>
              ))}
            </>
          )}
        </nav>

        <div className="sidebar-footer">
          <div className="user-chip">
            <div className="user-avatar">
              {profilePhoto
                ? <img src={profilePhoto} alt={user?.full_name} style={{ width: '100%', height: '100%', objectFit: 'cover', borderRadius: '50%' }} />
                : (user?.full_name || '?')[0]
              }
            </div>
            <div>
              <div className="user-name">{user?.full_name || 'Unknown'}</div>
              <div className="user-role">{user?.role?.replace('_', ' ')}</div>
            </div>
          </div>          {PLATFORM_BRAND.websiteUrl && (
            <a
              href={PLATFORM_BRAND.websiteUrl}
              className="back-to-website-btn"
              target="_blank"
              rel="noopener noreferrer"
            >
              ? Back to website
            </a>
          )}
          
          <button className="logout-btn" onClick={handleLogout}>Sign out</button>
        </div>
      </aside>

      <div className="content-area">
        <header className="top-bar">
          <LiveClock />
          <div className="top-bar-right">
            <ThemeToggle />
            <NotificationBell />
          </div>
        </header>
        {orgContext && (
          <div className="org-context-banner">
            <span>👁 Viewing as <strong>{orgContext.orgName}</strong> — all data is scoped to this org</span>
            <button type="button" className="org-context-exit" onClick={handleExitOrg}>Exit Org View</button>
          </div>
        )}
        <main className="main-content">{children}</main>
      </div>
      <ProfileOnboarding />
    </div>
  )
}
