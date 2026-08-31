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
import { api, clearAllContext, getOrgContext, getBrandContext } from '../api/client'
import './ContextBanner.css'

export default function ContextBanner() {
  const [ctx, setCtx] = useState(null)
  const [busy, setBusy] = useState(false)

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
