/**
 * THE PERSISTENT CONTEXT INDICATOR.
 *
 * When the platform owner is operating inside a brand or a customer, this sits
 * at the top of every screen and says whose records are about to change. That
 * is the whole job: an owner who forgets which tenant they entered edits the
 * wrong company.
 *
 * THE SERVER DECIDES WHAT THIS SAYS. It renders the trail from
 * GET /god/platform/context, not the value in localStorage. Those two can
 * disagree — a stale browser tab, a context cleared elsewhere — and when they
 * do, the one that decides which rows get written is the server. A banner that
 * trusts localStorage would keep saying "Restland" after the backend had
 * stopped agreeing, which is worse than no banner at all.
 *
 * IT SHOWS THREE LEVELS, because there are now three. A brand entered with no
 * customer inside it is a real place to stand — it is what the brand's own
 * sales workspace runs in — and it renders as AdvisorFlow → EvoSys Pro. Enter a
 * customer and the trail grows a third step rather than replacing the second.
 *
 * Renders nothing for everyone else. A customer admin has exactly one context
 * and cannot leave it, so there is nothing to warn them about.
 */
import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api, clearAllContext, getBranding, getOrgContext, getBrandContext } from '../api/client'
import { classifyRoute, PLATFORM } from '../auth/routeAuthority'
import { verticalFor } from '../verticals/workspaceVertical'
import './ContextBanner.css'

export default function ContextBanner() {
  const [ctx, setCtx] = useState(null)
  const [busy, setBusy] = useState(false)
  const location = useLocation()

  // ON A PLATFORM SURFACE THE CUSTOMER TRAIL IS A FALSE STATEMENT.
  //
  // The banner's job is to say WHOSE RECORDS THIS SCREEN WILL CHANGE. Since
  // the API client stopped sending X-Org-Override outside customer space, a
  // platform tool no longer operates as the entered customer — so a trail
  // ending in "Restland Cemetery and Funeral Home" over Sales Compensation or
  // the Lead Scraper would now be describing something that is not happening.
  //
  // The selection is not cleared. It is simply not ASSERTED here: return to
  // the customer app and the banner comes back, because the context did.
  const onPlatformSurface = classifyRoute(location.pathname) === PLATFORM

  // INSIDE A CONFIGURED VERTICAL WORKSPACE THE TRAIL IS THE WRONG THING TO
  // PRINT, EVEN THOUGH IT IS TRUE.
  //
  // "AdvisorFlow -> EvoSys Pro -> <customer>" is the white-label chain. It is
  // internal architecture, it is not part of the product the customer bought,
  // and a full-width strip naming it across the top of their own home screen
  // is precisely what a white-label layer exists to prevent — an operator
  // demonstrating the workspace is showing the reseller structure to the
  // room. (It renders for operators only, so this was never a leak to the
  // customer's staff; it was the platform announcing itself on a screen that
  // is supposed to be theirs.)
  //
  // The banner's real job — say whose records this screen writes, and offer
  // the way out — is carried by WorkspaceAdminMenu in the top bar: the
  // workspace is named in the rail's own wordmark, and Switch workspace and
  // Return to God Mode are both in that one control. Nothing is lost.
  const inVerticalWorkspace = Boolean(verticalFor(getBranding()))

  useEffect(() => {
    let live = true
    // Only ask when the browser thinks it is in SOME context. For everybody
    // else this endpoint is a 403 and there is no reason to call it on every
    // page. A brand alone counts — that is the level that used to be invisible.
    if (!getOrgContext() && !getBrandContext()) { setCtx(null); return }
    api.get('/god/platform/context')
      .then(r => { if (live) setCtx(r) })
      .catch(() => { if (live) setCtx(null) })
    return () => { live = false }
  }, [])

  async function leaveAll() {
    setBusy(true)
    try { await api.post('/god/platform/context/exit', {}) } catch { /* audited server-side; leaving anyway */ }
    clearAllContext()
    window.location.href = '/god/platform'
  }

  function switchWorkspace() {
    // Deliberately does NOT clear first. The owner is choosing where to go
    // next, not abandoning where they are — and if they change their mind on
    // the selector, the context they came from is still intact.
    window.location.href = '/god/workspaces'
  }

  if (!ctx || ctx.is_neutral) return null
  if (onPlatformSurface) return null
  if (inVerticalWorkspace) return null

  const trail = Array.isArray(ctx.trail) && ctx.trail.length
    ? ctx.trail
    : ['AdvisorFlow']

  return (
    <div className="ctx-banner" role="status">
      <span className="ctx-dot" aria-hidden="true" />
      <span className="ctx-text">
        {trail.map((step, i) => (
          <span key={i}>
            {i > 0 && <span className="ctx-sep" aria-hidden="true"> → </span>}
            <span className={i === trail.length - 1 ? 'ctx-here' : undefined}>{step}</span>
          </span>
        ))}
      </span>
      <span className="ctx-spacer" />
      <button className="ctx-exit" onClick={switchWorkspace} disabled={busy}>
        Switch workspace
      </button>
      <button className="ctx-exit" onClick={leaveAll} disabled={busy}>
        {busy ? 'Leaving…' : 'Return to God Mode'}
      </button>
    </div>
  )
}
