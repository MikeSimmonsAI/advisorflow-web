/**
 * ExecutiveSuite - the Executive shell.
 *
 * ===========================================================================
 * WHAT THIS LAYER IS, AND WHAT IT IS NOT
 * ===========================================================================
 *
 * The hierarchy is permanent:
 *
 *     PLATFORM / OWNER      the whole estate, every brand
 *          |
 *     BRAND / EXECUTIVE     one authorized portfolio          <- this shell
 *          |
 *     CUSTOMER WORKSPACE    the tenant's own operating tools
 *
 * Executive is NOT "the owner shell with fewer permissions" and it is NOT "a
 * customer workspace in read-only". It is its own operating layer, and it
 * answers its own question: how is my business doing, and what needs me today.
 *
 * AUTHORITY COMES FROM A GRANT, NEVER FROM A HEADER OR AN IMPERSONATION.
 * Every call this shell and its pages make is authorized server-side by
 * `require_brand_executive`, which reads a Membership row. There is no
 * override that can substitute for it, no way to widen a portfolio from the
 * client, and observing an organization never creates membership in it.
 *
 * ===========================================================================
 * THE NAVIGATION CONTAINS NOTHING THAT DOES NOT WORK
 * ===========================================================================
 *
 * Every item below resolves to a registered route backed by a live endpoint
 * returning real data. Areas an executive would reasonably expect, that this
 * platform cannot yet honestly serve, are deliberately ABSENT rather than
 * present-and-empty:
 *
 *   REPORTS / TRENDS - there is no historical snapshot anywhere in the
 *       schema. Every figure the platform holds is a current count, so a
 *       trend line would have to be drawn from a single point. A chart that
 *       invents its own history is worse than no chart.
 *
 *   COMPENSATION - compensation authority is `sales_comp_view`, granted per
 *       brand and independent of an executive grant. An executive who should
 *       see it is given that capability and reaches the Compensation Command
 *       Center directly; putting a link here would either 403 for most
 *       executives or imply an authority they do not hold.
 *
 *   PIPELINE - open opportunities belong to the brand's sales organization,
 *       which is the Back Office surface. The switcher below offers it to
 *       anybody the server says holds it.
 *
 * A dead nav item is worse than a missing one: it teaches the reader that
 * things in this product do not work.
 */

import { useCallback, useEffect, useState } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import {
  api, clearAllContext, fetchMyContexts, getCurrentUser, logout, setBrandContext,
} from '../../api/client'
import AppearanceToggle from '../../components/AppearanceToggle'
import ExecStyles from './ExecStyles'

function useExecutiveContext(refreshKey) {
  const [state, setState] = useState({
    loading: true, ctx: null, needsBrandSelect: false, error: null })
  useEffect(() => {
    setState({ loading: true, ctx: null, needsBrandSelect: false, error: null })
    api.get('/executive/context')
      .then(r => setState({ loading: false, ctx: r, needsBrandSelect: false, error: null }))
      .catch(err => {
        if (err?.status === 403) {
          // Either "no brand selected yet" (an owner arriving here) or
          // genuinely unauthorized. The parent resolves which by asking the
          // server what contexts this person actually holds - never by
          // guessing from the role.
          setState({ loading: false, ctx: null, needsBrandSelect: true, error: null })
        } else {
          setState({
            loading: false, ctx: null, needsBrandSelect: false,
            error: 'Unable to load your brand context. Please try again.' })
        }
      })
  }, [refreshKey])
  return state
}

/** The server-authorized context list. Nothing here is hardcoded or inferred;
 *  a failure simply hides the switcher rather than breaking the shell. */
function useAuthorizedSwitcher() {
  const [switchCtx, setSwitchCtx] = useState(null)
  useEffect(() => {
    fetchMyContexts().then(setSwitchCtx).catch(() => {})
  }, [])
  return switchCtx
}

/** How many organizations are carrying a standing exception right now.
 *  Rendered as a badge on Portfolio so the count is visible before anybody
 *  navigates. Silent on failure - a badge is not worth an error state. */
function useAttentionCount() {
  const [n, setN] = useState(null)
  useEffect(() => {
    api.get('/executive/portfolio')
      .then(r => setN(r?.attention_total ?? null))
      .catch(() => setN(null))
  }, [])
  return n
}

const NAV = [
  {
    group: 'Command',
    items: [{ label: 'Command Center', to: '/executive/command-center' }],
  },
  {
    group: 'Portfolio',
    items: [
      { label: 'Organizations', to: '/executive/organizations', badge: 'attention' },
      { label: 'Revenue', to: '/executive/revenue' },
    ],
  },
  {
    group: 'Team',
    items: [{ label: 'Sales Team', to: '/executive/team' }],
  },
  {
    group: 'Workspace',
    items: [{ label: 'Workspace', to: '/executive/workspace' }],
  },
]

export default function ExecutiveSuite({ children }) {
  const [refreshKey, setRefreshKey] = useState(0)
  const { loading, ctx, needsBrandSelect, error } = useExecutiveContext(refreshKey)
  const switchCtx = useAuthorizedSwitcher()
  const attention = useAttentionCount()
  const location = useLocation()
  const navigate = useNavigate()

  const selectBrand = useCallback((platformId, platformName) => {
    setBrandContext(platformId, platformName)
    setRefreshKey(k => k + 1)
  }, [])

  if (loading) {
    return (
      <div className="ex-scope" data-surface="executive">
        <ExecStyles />
        <div className="ex-full"><p className="ex-muted">Loading your brand…</p></div>
      </div>
    )
  }

  // -- 403: either no brand chosen yet, or no executive grant at all --------
  if (needsBrandSelect) {
    if (switchCtx === null) {
      return (
        <div className="ex-scope" data-surface="executive">
          <ExecStyles />
          <div className="ex-full"><p className="ex-muted">Loading your brand…</p></div>
        </div>
      )
    }
    const brands = switchCtx.executive_contexts || []
    if (brands.length === 0) {
      return (
        <div className="ex-scope" data-surface="executive">
          <ExecStyles />
          <div className="ex-full">
            <div className="ex-panel">
              <h2>No executive access</h2>
              <p>
                Your account does not oversee a brand portfolio. If that is
                wrong, whoever manages access can grant it.
              </p>
              <button className="ex-btn ex-primary" onClick={() => navigate('/login')}>
                Sign in with a different account
              </button>
            </div>
          </div>
        </div>
      )
    }
    return (
      <div className="ex-scope" data-surface="executive">
        <ExecStyles />
        <div className="ex-full">
          <div className="ex-panel">
            <h2>Choose a portfolio</h2>
            <p>You oversee more than one brand. Pick the one you want to look at.</p>
            <div className="ex-choices">
              {brands.map(b => (
                <button key={b.platform_id} className="ex-choice"
                        onClick={() => selectBrand(b.platform_id, b.platform_name)}>
                  {b.platform_name}
                </button>
              ))}
            </div>
            {switchCtx.has_back_office && (
              <button className="ex-btn" onClick={() => navigate('/sales')}>
                Back Office / Sales
              </button>
            )}
          </div>
        </div>
      </div>
    )
  }

  if (error || !ctx) {
    return (
      <div className="ex-scope" data-surface="executive">
        <ExecStyles />
        <div className="ex-full">
          <div className="ex-panel">
            <h2>Executive view unavailable</h2>
            <p>{error || 'No executive context found.'}</p>
            <button className="ex-btn ex-primary" onClick={() => navigate('/login')}>
              Sign in with a different account
            </button>
          </div>
        </div>
      </div>
    )
  }

  // The owner arrives here through brand selection rather than a grant. Read
  // from the JWT role, never from the executive payload, so the sentinel the
  // server returns is never overloaded with a second meaning.
  const isOwner = getCurrentUser()?.role === 'god_admin'

  async function handleLogout() {
    await logout()
    navigate('/login')
  }

  return (
    <div className="ex-scope" data-surface="executive">
      <ExecStyles />
      <div className="ex-shell">
        <aside className="ex-rail">
          <div className="ex-brand">
            <b>{ctx.platform_name}</b>
            <span className="ex-role">Executive</span>
          </div>

          {NAV.map(group => (
            <div key={group.group}>
              <div className="ex-navgroup">{group.group}</div>
              <nav className="ex-nav">
                {group.items.map(item => {
                  const on = location.pathname === item.to
                    || location.pathname.startsWith(item.to + '/')
                  return (
                    <Link key={item.to} to={item.to} className={on ? 'on' : ''}>
                      {item.label}
                      {item.badge === 'attention' && attention
                        ? <span className="ex-badge">{attention}</span> : null}
                    </Link>
                  )
                })}
              </nav>
            </div>
          ))}

          <div className="ex-railfill" />

          <div className="ex-railfoot">
            {/* The switcher renders only what the SERVER says this person
                holds. An executive with one brand and no back office sees
                nothing here, which is correct - there is nowhere else to go. */}
            {switchCtx && (switchCtx.has_back_office
              || (switchCtx.executive_contexts || []).length > 1) && (
              <div>
                <div className="ex-navgroup" style={{ padding: '0 0 6px' }}>
                  Switch view
                </div>
                {switchCtx.has_back_office && (
                  <button className="ex-btn ex-small" style={{ width: '100%' }}
                          onClick={() => navigate('/sales')}>
                    Back Office / Sales
                  </button>
                )}
                {(switchCtx.executive_contexts || []).length > 1 && (
                  <select className="ex-select" style={{ width: '100%', marginTop: 8 }}
                          aria-label="Brand portfolio"
                          value={ctx.platform_id}
                          onChange={e => {
                            const b = (switchCtx.executive_contexts || [])
                              .find(x => x.platform_id === e.target.value)
                            if (b) selectBrand(b.platform_id, b.platform_name)
                          }}>
                    {(switchCtx.executive_contexts || []).map(b => (
                      <option key={b.platform_id} value={b.platform_id}>
                        {b.platform_name}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            )}

            <AppearanceToggle compact />

            {isOwner && (
              <button className="ex-btn ex-small" style={{ width: '100%' }}
                      onClick={() => { clearAllContext(); navigate('/god/platform') }}>
                Return to Platform
              </button>
            )}

            <div className="ex-who">{ctx.email}</div>
            <button className="ex-btn ex-small" style={{ width: '100%' }}
                    onClick={handleLogout}>
              Log out
            </button>
          </div>
        </aside>

        <main className="ex-main">{children}</main>
      </div>
    </div>
  )
}
