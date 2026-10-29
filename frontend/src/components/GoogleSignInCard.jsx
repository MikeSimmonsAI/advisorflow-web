/* Settings -> "Sign in with Google" (#430). The ONLY way a Google account gets
 * attached to a login: the signed-in person starts it here, Google confirms the
 * account, and the server records the link. Hidden entirely while the feature
 * is off, and for accounts it does not apply to (platform owner by default). */
import { useEffect, useState } from 'react'
import { api } from '../api/client'

const ERR = {
  already_linked: 'That Google account is already connected to another login, or this login already has one.',
  not_allowed: 'Google sign-in is not available for this account.',
  unverified: "Google reports that email address as unverified, so it can't be connected.",
  cancelled: 'Connecting Google was cancelled.',
  expired: 'That took too long. Please try again.',
}

export default function GoogleSignInCard() {
  const [st, setSt] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const load = () => api.get('/auth/google/link', { skipRedirect: true }).then(setSt).catch(() => setSt(null))
  useEffect(() => {
    load()
    const p = new URLSearchParams(window.location.search)
    if (p.get('google') === 'linked') setMsg({ ok: true, text: 'Google account connected. You can now use “Continue with Google” on the sign-in page.' })
    else if (p.get('google_error')) setMsg({ ok: false, text: ERR[p.get('google_error')] || "Google couldn't be connected. Please try again." })
  }, [])
  if (!st || !st.enabled || !st.allowed) return null
  const connect = async () => {
    setBusy(true); setMsg(null)
    try {
      const r = await api.post('/auth/google/link/intent', { origin: window.location.origin, next: '/settings' })
      window.location.assign(r.url)
    } catch (e) { setMsg({ ok: false, text: e.message || "Google couldn't be connected." }); setBusy(false) }
  }
  const disconnect = async () => {
    setBusy(true); setMsg(null)
    try { await api.delete('/auth/google/link'); await load(); setMsg({ ok: true, text: 'Google account disconnected. Use your email and password to sign in.' }) }
    catch (e) { setMsg({ ok: false, text: e.message || "Couldn't disconnect." }) } finally { setBusy(false) }
  }
  return (
    <section className="panel" style={{ marginBottom: 16 }} aria-labelledby="gsi-h">
      <div className="panel-header"><h2 id="gsi-h" className="panel-title">Sign in with Google</h2></div>
      {st.linked ? (
        <p style={{ margin: '0 0 12px', fontSize: 14 }}>Connected to <b>{st.email || 'your Google account'}</b>. You can sign in with Google or with your password.</p>
      ) : (
        <p style={{ margin: '0 0 12px', fontSize: 14, color: 'var(--text-secondary)' }}>
          Connect your Google account to use “Continue with Google” on the sign-in page. Your password keeps working, and your access stays exactly the same.
        </p>
      )}
      {msg ? <div className={msg.ok ? 'settings-banner settings-banner--success' : 'settings-error'} role="status" style={{ marginBottom: 12 }}>{msg.text}</div> : null}
      {st.linked
        ? <button type="button" className="btn btn--secondary" onClick={disconnect} disabled={busy}>Disconnect Google</button>
        : <button type="button" className="btn btn--primary" onClick={connect} disabled={busy}>{busy ? 'Opening Google…' : 'Connect Google account'}</button>}
    </section>
  )
}
