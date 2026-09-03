/**
 * ExecutiveSuite — the Executive Suite shell.
 *
 * Deliberately NOT the tenant Layout (which carries tenant nav) and NOT the
 * owner shell. This shell is for brand executives: people who need brand-level
 * visibility without customer operational access or owner controls.
 *
 * Auth: /executive/context — 401/403 if no brand_executive grant.
 * Isolation: every data call is server-side scoped to the executive's platform.
 *
 * GOD BRAND-SELECTION PATH
 * When /executive/context returns 403 (no brand context set), we check
 * executive_contexts from /auth/my-contexts:
 *   - brands available → brand selector ("Choose Executive Suite")
 *   - no brands        → "Access Not Available" (genuinely unauthorized)
 * Selecting a brand calls setBrandContext (sets X-Brand-Override) and
 * re-fetches /executive/context — no membership row is created.
 */

import { useState, useEffect, useCallback } from 'react'
import { Link, useNavigate, useLocation } from 'react-router-dom'
import { api, fetchMyContexts, setBrandContext, getCurrentUser, clearAllContext, logout } from '../../api/client'

function useExecutiveContext(refreshKey) {
  const [state, setState] = useState({ loading: true, ctx: null, needsBrandSelect: false, error: null })
  useEffect(() => {
    setState({ loading: true, ctx: null, needsBrandSelect: false, error: null })
    api.get('/executive/context')
      .then(r => setState({ loading: false, ctx: r, needsBrandSelect: false, error: null }))
      .catch(err => {
        const status = err?.status
        if (status === 403) {
          // Could be "no brand selected" (god) or genuinely unauthorized.
          // We resolve this by checking executive_contexts from my-contexts
          // in the parent; store needsBrandSelect=true so the parent can branch.
          setState({ loading: false, ctx: null, needsBrandSelect: true, error: null })
        } else {
          setState({
            loading: false,
            ctx: null,
            needsBrandSelect: false,
            error: 'Unable to load your brand context. Please try again.',
          })
        }
      })
  }, [refreshKey])
  return state
}

/**
 * Fetches the server-authorized context list so the sidebar can show only
 * the contexts this user actually holds — never hardcoded, never guessed.
 * Failure is silent: the switcher simply does not render.
 */
function useAuthorizedSwitcher() {
  const [switchCtx, setSwitchCtx] = useState(null)
  useEffect(() => {
    fetchMyContexts()
      .then(d => setSwitchCtx(d))
      .catch(() => {/* switcher hidden on error — not a fatal condition */})
  }, [])
  return switchCtx
}

const NAV_ITEMS = [
  { label: 'Command Center', to: '/executive/command-center' },
  { label: 'Organizations',  to: '/executive/organizations' },
]

export default function ExecutiveSuite({ children }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const { loading, ctx, needsBrandSelect, error } = useExecutiveContext(refreshKey)
  const switchCtx = useAuthorizedSwitcher()
  const location = useLocation()
  const navigate = useNavigate()

  const selectBrand = useCallback((platformId, platformName) => {
    setBrandContext(platformId, platformName)
    setRefreshKey(k => k + 1)
  }, [])

  if (loading) {
    return (
      <div style={styles.fullPage}>
        <p style={styles.muted}>Loading your brand context…</p>
      </div>
    )
  }

  // ── 403 PATH ─────────────────────────────────────────────────────────────
  // /executive/context returned 403 — either no brand selected (god path)
  // or genuinely unauthorized (non-executive user).
  // Resolve by checking executive_contexts from my-contexts.
  if (needsBrandSelect) {
    // Wait for my-contexts to load (both fetches run in parallel)
    if (switchCtx === null) {
      return (
        <div style={styles.fullPage}>
          <p style={styles.muted}>Loading your brand context…</p>
        </div>
      )
    }

    const availableBrands = switchCtx.executive_contexts || []

    if (availableBrands.length === 0) {
      // Genuinely unauthorized — server confirms no executive grants
      return (
        <div style={styles.fullPage}>
          <div style={styles.errorCard}>
            <h2 style={styles.errorTitle}>Access Not Available</h2>
            <p style={styles.errorMsg}>
              You do not have Executive Suite access for any brand.
            </p>
            <button style={styles.btn} onClick={() => navigate('/login')}>
              Sign in with a different account
            </button>
          </div>
        </div>
      )
    }

    // Has brands but no brand context set — show brand selector
    return (
      <div style={styles.fullPage}>
        <div style={styles.selectorCard}>
          <h2 style={styles.selectorTitle}>Choose Executive Suite</h2>
          <p style={styles.selectorSub}>Select a brand to enter its Executive Suite.</p>
          <div style={styles.brandList}>
            {availableBrands.map(b => (
              <button
                key={b.platform_id}
                style={styles.brandBtn}
                onClick={() => selectBrand(b.platform_id, b.platform_name)}
              >
                {b.platform_name}
              </button>
            ))}
          </div>
          {switchCtx.has_back_office && (
            <button style={styles.backLink} onClick={() => navigate('/sales')}>
              ← Back Office / Sales
            </button>
          )}
        </div>
      </div>
    )
  }

  // ── NON-403 ERROR ─────────────────────────────────────────────────────────
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

  // ── AUTHORISED SHELL ──────────────────────────────────────────────────────
  // Detect god_admin from the JWT role, not from the executive sentinel, so
  // the sidebar can show god-specific controls without altering the sentinel.
  const isGod = getCurrentUser()?.role === 'god_admin'

  async function handleGodReturn() {
    clearAllContext()
    navigate('/god/platform')
  }

  async function handleLogout() {
    await logout()
    navigate('/login')
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
        {/* Context switcher — only renders when the server has authorized
            more than one context for this user. Labels and destinations are
            derived entirely from the server response; nothing is hardcoded. */}
        {switchCtx && (switchCtx.has_back_office || (switchCtx.executive_contexts || []).length > 1) && (
          <div style={styles.switcherBlock}>
            <span style={styles.switcherLabel}>Switch view</span>
            {/* Current view — non-clickable active state */}
            <div style={styles.switcherCurrent}>Executive Suite</div>
            {/* Back Office / Sales — shown only when server grants it */}
            {switchCtx.has_back_office && (
              <button
                style={styles.switcherBtn}
                onClick={() => navigate('/sales')}
              >
                Back Office / Sales
              </button>
            )}
          </div>
        )}
        {/* God controls — only when the owner is in an Executive Suite via
            god brand-selection. Preserves Executive Suite + Sales switcher for
            non-god brand_executive members, who see none of this block. */}
        {isGod && (
          <div style={styles.godBlock}>
            <button style={styles.godReturnBtn} onClick={handleGodReturn}>
              ← Return to Platform
            </button>
            <button style={styles.godLogoutBtn} onClick={handleLogout}>
              Log Out
            </button>
          </div>
        )}
        <div style={styles.sidebarFooter}>
          <span style={styles.userEmail}>{ctx.email}</span>
          {!isGod && (
            <button style={styles.footerLogoutBtn} onClick={handleLogout}>
              Log Out
            </button>
          )}
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
  switcherBlock: {
    padding: '12px 12px 16px',
    borderTop: '1px solid #2d3354',
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
  },
  switcherLabel: { fontSize: 10, color: '#7b83a6', fontWeight: 600,
    textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 4 },
  switcherCurrent: {
    padding: '7px 10px', fontSize: 13, color: '#7ea9ff',
    background: '#2563eb22', borderRadius: 6, fontWeight: 600,
  },
  switcherBtn: {
    padding: '7px 10px', fontSize: 13, color: '#b0b7d4',
    background: 'transparent', border: '1px solid #2d3354',
    borderRadius: 6, cursor: 'pointer', textAlign: 'left', fontWeight: 500,
  },
  godBlock: {
    padding: '12px 12px 8px',
    borderTop: '1px solid #2d3354',
    display: 'flex',
    flexDirection: 'column',
    gap: 6,
  },
  godReturnBtn: {
    padding: '7px 10px', fontSize: 13, color: '#f59e0b',
    background: 'rgba(245,158,11,0.1)', border: '1px solid rgba(245,158,11,0.3)',
    borderRadius: 6, cursor: 'pointer', textAlign: 'left', fontWeight: 600,
  },
  godLogoutBtn: {
    padding: '7px 10px', fontSize: 13, color: '#b0b7d4',
    background: 'transparent', border: '1px solid #2d3354',
    borderRadius: 6, cursor: 'pointer', textAlign: 'left', fontWeight: 500,
  },
  sidebarFooter: { padding: '16px 20px', borderTop: '1px solid #2d3354' },
  userEmail: { fontSize: 11, color: '#7b83a6', wordBreak: 'break-all', display: 'block', marginBottom: 8 },
  footerLogoutBtn: {
    background: 'transparent', border: '1px solid #2d3354', borderRadius: 6,
    color: '#7b83a6', cursor: 'pointer', fontSize: 12, padding: '5px 10px',
    width: '100%', textAlign: 'left',
  },
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
  // Brand selector styles
  selectorCard: {
    background: '#fff', border: '1px solid #e5e7eb', borderRadius: 12,
    padding: '40px 48px', textAlign: 'center', maxWidth: 440, width: '100%',
  },
  selectorTitle: { margin: '0 0 8px', fontSize: 20, color: '#1a1f36', fontWeight: 700 },
  selectorSub: { color: '#6b7280', margin: '0 0 24px', lineHeight: 1.5, fontSize: 14 },
  brandList: { display: 'flex', flexDirection: 'column', gap: 10, marginBottom: 20 },
  brandBtn: {
    background: '#f8fafc', color: '#1a1f36', border: '1px solid #e5e7eb',
    borderRadius: 8, padding: '12px 20px', cursor: 'pointer',
    fontSize: 15, fontWeight: 600, textAlign: 'left',
    transition: 'background 0.15s',
  },
  backLink: {
    background: 'transparent', border: 'none', color: '#6b7280',
    fontSize: 13, cursor: 'pointer', marginTop: 4, padding: '4px 0',
  },
}
