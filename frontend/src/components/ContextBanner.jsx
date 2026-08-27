/**
 * THE PERSISTENT CONTEXT INDICATOR.
 *
 * When the platform owner is operating inside a customer, this sits at the top
 * of every screen and says whose records are about to change. That is the whole
 * job: an owner who forgets which tenant they entered edits the wrong company.
 *
 * THE SERVER DECIDES WHAT THIS SAYS. It renders `banner` from
 * GET /god/platform/context, not the value in localStorage. Those two can
 * disagree — a stale browser tab, a context cleared elsewhere — and when they
 * do, the one that decides which rows get written is the server. A banner that
 * trusts localStorage would keep saying "SCI" after the backend had stopped
 * agreeing, which is worse than no banner at all.
 *
 * Renders nothing for everyone else. A customer admin has exactly one context
 * and cannot leave it, so there is nothing to warn them about.
 */
import { useEffect, useState } from 'react'
import { api, clearOrgContext, getOrgContext } from '../api/client'
import './ContextBanner.css'

export default function ContextBanner() {
  const [ctx, setCtx] = useState(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let live = true
    // Only ask when the browser thinks it is in a context. For everybody else
    // this endpoint is a 403 and there is no reason to call it on every page.
    if (!getOrgContext()) { setCtx(null); return }
    api.get('/god/platform/context')
      .then(r => { if (live) setCtx(r) })
      .catch(() => { if (live) setCtx(null) })
    return () => { live = false }
  }, [])

  async function exit() {
    setBusy(true)
    try { await api.post('/god/platform/context/exit', {}) } catch { /* audited server-side; leaving anyway */ }
    clearOrgContext()
    window.location.href = '/god/platform'
  }

  if (!ctx || ctx.is_neutral || !ctx.banner) return null

  return (
    <div className="ctx-banner" role="status">
      <span className="ctx-dot" aria-hidden="true" />
      <span className="ctx-text">{ctx.banner}</span>
      <span className="ctx-spacer" />
      <button className="ctx-exit" onClick={exit} disabled={busy}>
        {busy ? 'Leaving…' : 'Exit customer'}
      </button>
    </div>
  )
}
