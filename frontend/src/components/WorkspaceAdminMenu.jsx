/**
 * ONE DISCREET ADMIN CONTROL, INSIDE A CUSTOMER'S OWN WORKSPACE.
 *
 * ===========================================================================
 * WHAT IT REPLACES AND WHY
 * ===========================================================================
 *
 * A configured vertical workspace is the customer's product. Standing in one
 * as an operator, five separate pieces of platform chrome used to sit between
 * the customer's wordmark and their first screen:
 *
 *   GodReturnBar          a full-width amber strip, "GOD ADMIN — viewing the
 *                         customer app", with BACK TO COMMAND CENTER
 *   ContextBanner         a second full-width strip spelling out the internal
 *                         hierarchy: AdvisorFlow -> EvoSys Pro -> <customer>
 *   ContextSwitcher       a "Back Office" button in the top bar
 *   Command Center        the first item in the customer's own rail
 *   the org picker        an amber workspace selector under it
 *
 * Every one of those is internal architecture. The hierarchy strip is the
 * worst of them: the white-label chain is not part of the customer's product
 * and naming it on their home screen is the one thing a white-label layer
 * exists to prevent. Together they made the workspace read as "an AdvisorFlow
 * administrator looking at a customer" rather than as the customer's software.
 *
 * So inside a vertical workspace those five collapse into this: one compact
 * control in the top bar, holding every action they offered.
 *
 * ===========================================================================
 * NOTHING IS REMOVED, AND NOTHING IS GRANTED
 * ===========================================================================
 *
 * Command Center, Switch workspace, Return to God Mode and Back Office are
 * all still one click away — they moved, they did not go. And this is a
 * visual affordance, not a permission: /god is guarded by GodRoute in the
 * client and `require_god` on every god endpoint, the back office re-checks
 * membership server-side, and the workspace selector is the same server
 * answer (/auth/my-contexts) the old button used.
 *
 * A CUSTOMER'S OWN STAFF SEE NOTHING HERE. Not a greyed control, not an empty
 * chip — the component returns null, because a door somebody cannot open is
 * worse than no door at all, and because an Atlantis user must never learn
 * what is above Atlantis.
 */
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, clearAllContext, clearWorkspaceContext, fetchMyContexts,
         getCurrentUser } from '../api/client'
import './WorkspaceAdminMenu.css'

export default function WorkspaceAdminMenu() {
  const navigate = useNavigate()
  const user = getCurrentUser()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [hasBackOffice, setHasBackOffice] = useState(false)
  const boxRef = useRef(null)

  const isGod = user?.role === 'god_admin'
  const isSuper = user?.role === 'super_admin'

  // The same server answer the old Back Office button used. A failure means
  // "no back office" for rendering purposes; the route refuses independently.
  useEffect(() => {
    let live = true
    fetchMyContexts()
      .then(d => { if (live) setHasBackOffice(!!d?.has_back_office) })
      .catch(() => { if (live) setHasBackOffice(false) })
    return () => { live = false }
  }, [])

  useEffect(() => {
    function onDocClick(e) {
      if (boxRef.current && !boxRef.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [])

  if (!isGod && !isSuper && !hasBackOffice) return null

  async function returnToGodMode() {
    setBusy(true)
    // Audited server-side; leaving either way, because a failed exit that
    // strands an operator inside a customer is the worse outcome.
    try { await api.post('/god/platform/context/exit', {}) } catch { /* noop */ }
    clearAllContext()
    window.location.href = '/god/platform'
  }

  function backToOffice() {
    // Clearing first: a stale header would keep scoping back-office requests
    // to the customer the operator has just walked out of.
    clearWorkspaceContext()
    navigate('/sales')
  }

  const items = []
  if (isGod) {
    items.push({ key: 'command', label: 'Command Center',
                 hint: 'Platform control plane', run: () => navigate('/god') })
    items.push({ key: 'switch', label: 'Switch workspace',
                 hint: 'Enter a different customer',
                 run: () => { window.location.href = '/god/workspaces' } })
  }
  if (hasBackOffice) {
    items.push({ key: 'office', label: 'Back Office',
                 hint: 'Brand sales workspace', run: backToOffice })
  }
  if (isGod) {
    items.push({ key: 'exit', label: busy ? 'Leaving…' : 'Return to God Mode',
                 hint: 'Leave this customer context', run: returnToGodMode,
                 danger: true })
  }
  if (items.length === 0) return null

  return (
    <div className="wam" ref={boxRef}>
      <button type="button" className="wam-trigger"
              aria-haspopup="menu" aria-expanded={open}
              title="Platform administration"
              onClick={() => setOpen(o => !o)}>
        <span className="wam-dot" aria-hidden="true" />
        <span className="wam-label">Admin</span>
        <svg className="wam-caret" width="10" height="10" viewBox="0 0 24 24"
             fill="none" stroke="currentColor" strokeWidth="3" aria-hidden="true">
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>
      {open && (
        <div className="wam-menu" role="menu">
          {/* Says whose records this screen writes — the one thing the
              hierarchy strip was genuinely for — without printing the
              white-label chain across the customer's home screen. */}
          <div className="wam-menu-head">Platform administration</div>
          {items.map(item => (
            <button key={item.key} type="button" role="menuitem" disabled={busy}
                    className={`wam-item${item.danger ? ' wam-item--danger' : ''}`}
                    onClick={() => { setOpen(false); item.run() }}>
              <span className="wam-item-name">{item.label}</span>
              <span className="wam-item-hint">{item.hint}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
