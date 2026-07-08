import { useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { getCurrentUser, logout } from '../api/client'
import SignalPulse from './SignalPulse'
import NotificationBell from './NotificationBell'
import './Layout.css'

const NAV_ITEMS = [
  { to: '/', label: 'Overview', icon: 'grid' },
  { to: '/leads', label: 'Leads', icon: 'users' },
  { to: '/replies', label: 'Replies', icon: 'message' },
  { to: '/workqueue', label: 'Work Queue', icon: 'zap' },
  { to: '/auto-send', label: 'Auto-Send Queue', icon: 'send' },
  { to: '/cadence', label: 'Cadence', icon: 'repeat' },
  { to: '/email-queue', label: 'Email Queue', icon: 'mail' },
  { to: '/system-health', label: 'System Health', icon: 'activity' },
  { to: '/settings', label: 'Settings', icon: 'settings' },
]

const ADMIN_NAV_ITEMS = [
  { to: '/admin', label: 'Master Dashboard', icon: 'shield' },
  { to: '/reports', label: 'Reports', icon: 'activity' },
  { to: '/users', label: 'Users', icon: 'user-plus' },
  { to: '/templates', label: 'Templates', icon: 'file-text' },
  { to: '/campaigns', label: 'Campaigns', icon: 'target' },
  { to: '/lead-cleanup', label: 'Lead Cleanup', icon: 'users' },
  { to: '/compliance', label: 'Compliance', icon: 'shield-check' },
  { to: '/audit-log', label: 'Audit Log', icon: 'activity' },
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
    activity: <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
  }
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      {paths[name]}
    </svg>
  )
}

export default function Layout({ children }) {
  const user = getCurrentUser()
  const navigate = useNavigate()
  const [sidebarOpen, setSidebarOpen] = useState(false)

  function closeSidebar() {
    setSidebarOpen(false)
  }

  function handleLogout() {
    logout()
    window.location.href = '/login'
  }

  return (
    <div className={`layout ${sidebarOpen ? 'layout--sidebar-open' : ''}`}>
      <button
        type="button"
        className="mobile-menu-btn"
        onClick={() => setSidebarOpen(true)}
        aria-label="Open navigation menu"
      >
        <span />
        <span />
        <span />
      </button>
      <button
        type="button"
        className="sidebar-backdrop"
        onClick={closeSidebar}
        aria-label="Close navigation menu"
      />

      <aside className="sidebar">
        <div className="sidebar-brand">
          <SignalPulse color="blue" size={9} />
          <span className="brand-mark">Booka<span className="brand-accent">Boost</span></span>
          <button type="button" className="sidebar-close-btn" onClick={closeSidebar} aria-label="Close navigation menu">×</button>
        </div>

        <nav className="sidebar-nav">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
              onClick={closeSidebar}
            >
              <Icon name={item.icon} />
              {item.label}
            </NavLink>
          ))}

          {(user?.role === 'org_admin' || user?.role === 'super_admin') && (
            <>
              <div className="nav-divider" />
              {ADMIN_NAV_ITEMS.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  className={({ isActive }) => `nav-item ${isActive ? 'nav-item--active' : ''}`}
                  onClick={closeSidebar}
                >
                  <Icon name={item.icon} />
                  {item.label}
                </NavLink>
              ))}
            </>
          )}
        </nav>

        <div className="sidebar-footer">
          <div className="user-chip">
            <div className="user-avatar">{(user?.full_name || '?')[0]}</div>
            <div>
              <div className="user-name">{user?.full_name || 'Unknown'}</div>
              <div className="user-role">{user?.role?.replace('_', ' ')}</div>
            </div>
          </div>
          <button className="logout-btn" onClick={handleLogout}>Sign out</button>
        </div>
      </aside>

      <div className="content-area">
        <header className="top-bar">
          <NotificationBell />
        </header>
        <main className="main-content">{children}</main>
      </div>
    </div>
  )
}
