/**
 * ExecutiveSuite — the Executive Suite shell.
 *
 * Deliberately NOT the tenant Layout (which carries tenant nav) and NOT the
 * owner shell. This shell is for brand executives: people who need brand-level
 * visibility without customer operational access or owner controls.
 *
 * Auth: /executive/context — 401/403 if no brand_executive grant.
 * Isolation: every data call is server-side scoped to the executive's platform.
 */

import { useState, useEffect } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { api } from '../../api/client'

function useExecutiveContext() {
  const [state, setState] = useState({ loading: true, ctx: null, error: null })
  useEffect(() => {
    api.get('/executive/context')
      .then(r => setState({ loading: false, ctx: r.data, error: null }))
      .catch(err => {
        const status = err?.response?.status
        setState({
          loading: false,
          ctx: null,
          error: status === 403
            ? 'You do not have Executive Suite access for any brand.'
            : 'Unable to load your brand context. Please try again.',
        })
      })
  }, [])
  return state
}

const NAV_ITEMS = [
  { label: 'Command Center', to: '/executive/command-center' },
  { label: 'Organizations', to: '/executive/organizations' },
]

export default function ExecutiveSuite({ children }) {
  const { loading, ctx, error } = useExecutiveContext()
  const location = useLocation()
  const navigate = useNavigate()

  if (loading) {
    return (
      <div style={styles.fullPage}>
        <p style={styles.muted}>Loading your brand context…</p>
      </div>
    )
  }

  if (error || !ctx) {
    return (
      <div style={styles.fullPage}>
        <div style={styles.errorCard}>
          <h2 style={styles.errorTitle}>Access Not Available</h2>
          <p style={styles.errorMsg}>{error || 'No executive context found.'}</p>
          <button style={styles.btn} onClick={() => navigate('/login')}>
            Sign in with a different account
          </button>
        </div>
      </div>
    )
  }

  return (
    <div style={styles.shell}>
      <aside style={styles.sidebar}>
        <div style={styles.brandBlock}>
          <span style={styles.brandLabel}>{ctx.platform_name}</span>
          <span style={styles.roleChip}>Executive</span>
        </div>
        <nav style={styles.nav}>
          {NAV_ITEMS.map(item => (
            <Link
              key={item.to}
              to={item.to}
              style={{
                ...styles.navLink,
                ...(location.pathname.startsWith(item.to) ? styles.navLinkActive : {}),
              }}
            >
              {item.label}
            </Link>
          ))}
        </nav>
        <div style={styles.sidebarFooter}>
          <span style={styles.userEmail}>{ctx.email}</span>
        </div>
      </aside>
      <main style={styles.main}>
        {children}
      </main>
    </div>
  )
}

const styles = {
  shell: {
    display: 'flex',
    minHeight: '100vh',
    fontFamily: 'system-ui, -apple-system, sans-serif',
    background: '#f5f6fa',
  },
  sidebar: {
    width: 220,
    minHeight: '100vh',
    background: '#1a1f36',
    color: '#c9cee8',
    display: 'flex',
    flexDirection: 'column',
    flexShrink: 0,
  },
  brandBlock: {
    padding: '24px 20px 16px',
    borderBottom: '1px solid #2d3354',
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  brandLabel: { fontSize: 15, fontWeight: 700, color: '#fff', letterSpacing: '-0.02em' },
  roleChip: {
    display: 'inline-block',
    background: '#2563eb',
    color: '#fff',
    fontSize: 10,
    fontWeight: 600,
    padding: '2px 8px',
    borderRadius: 10,
    letterSpacing: '0.05em',
    textTransform: 'uppercase',
    alignSelf: 'flex-start',
  },
  nav: { display: 'flex', flexDirection: 'column', padding: '12px 0', flex: 1 },
  navLink: {
    display: 'block',
    padding: '10px 20px',
    color: '#b0b7d4',
    textDecoration: 'none',
    fontSize: 14,
    fontWeight: 500,
    borderRadius: 6,
    margin: '2px 8px',
  },
  navLinkActive: { background: '#2563eb22', color: '#7ea9ff' },
  sidebarFooter: { padding: '16px 20px', borderTop: '1px solid #2d3354' },
  userEmail: { fontSize: 11, color: '#7b83a6', wordBreak: 'break-all' },
  main: { flex: 1, overflowX: 'auto' },
  fullPage: {
    minHeight: '100vh', display: 'flex', alignItems: 'center',
    justifyContent: 'center', background: '#f5f6fa',
    fontFamily: 'system-ui, -apple-system, sans-serif',
  },
  muted: { color: '#888', fontSize: 14 },
  errorCard: {
    background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12,
    padding: '40px 48px', textAlign: 'center', maxWidth: 440,
  },
  errorTitle: { margin: '0 0 12px', fontSize: 20, color: '#1a1f36' },
  errorMsg: { color: '#6b7280', margin: '0 0 24px', lineHeight: 1.5 },
  btn: {
    background: '#2563eb', color: '#fff', border: 'none', borderRadius: 8,
    padding: '10px 24px', cursor: 'pointer', fontSize: 14, fontWeight: 600,
  },
}
