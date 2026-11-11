// "Connect ChatGPT" - the VOLT sign-in page ChatGPT sends a person to
// (Issue #571, 2026-11-10). The person signs in with THEIR OWN EvoSys login;
// only an authorized VOLT development principal can allow. Allowing hands
// ChatGPT a one-time code, which it swaps for a revocable sign-in token that
// acts as this person only. No password or key ever passes through ChatGPT.
import { useEffect, useState } from 'react'
import { Navigate, useLocation, useSearchParams } from 'react-router-dom'
import { api, getCurrentUser, logout } from '../../api/client'
import './volt.css'

function signedIn() {
  return !!(localStorage.getItem('af_token') || localStorage.getItem('bookaboost_token'))
}

export default function VoltDevConnect() {
  const [sp] = useSearchParams()
  const loc = useLocation()
  const id = sp.get('request') || ''
  const [info, setInfo] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState('')
  const authed = signedIn()

  useEffect(() => {
    if (!authed || !id) return
    api.get(`/volt/dev/oauth/request/${encodeURIComponent(id)}`)
      .then(setInfo)
      .catch((e) => setErr(e?.message || 'This connection request could not be loaded.'))
  }, [authed, id])

  if (!authed) {
    return <Navigate to={`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`} replace />
  }

  async function decide(allow) {
    if (busy) return
    setBusy(true); setErr('')
    try {
      const r = await api.post(`/volt/dev/oauth/request/${encodeURIComponent(id)}/decision`, { allow })
      setDone(allow ? 'Connected. Returning you to ChatGPT…' : 'Declined. Returning you to ChatGPT…')
      window.location.assign(r.redirect)
    } catch (e) {
      setErr(e?.message || 'That did not work.')
      setBusy(false)
    }
  }

  async function switchUser() {
    await logout()
    window.location.assign(`/login?next=${encodeURIComponent(loc.pathname + loc.search)}`)
  }

  const me = getCurrentUser()
  return (
    <div className="volt" style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 16 }}>
      <section className="v-panel" style={{ maxWidth: 520, width: '100%' }} data-dev-connect>
        <h2 style={{ marginTop: 0 }}>Connect ChatGPT to VOLT development</h2>
        {!id && <div className="v-alert v-alert--err" role="alert">This link is missing its request. Start again from ChatGPT.</div>}
        {err && <div className="v-alert v-alert--err" role="alert" data-connect-error>{err}</div>}
        {done && <div className="v-alert v-alert--info" role="status">{done}</div>}
        {info && !done && (
          <>
            <p>Signed in as <b>{info.you}</b> ({info.email}).{' '}
              <button type="button" className="v-btn v-btn--link" onClick={switchUser}>Not you?</button></p>
            {!info.authorized ? (
              <div className="v-alert v-alert--err" role="alert" data-connect-denied>
                This login is not one of VOLT&apos;s authorized development principals, so it cannot connect ChatGPT.
              </div>
            ) : (
              <>
                <p><b>{info.client}</b> (returning to <code>{info.returns_to}</code>) is asking to act as you for VOLT Marketing development. It will be able to:</p>
                <ul className="v-small" data-connect-can>{info.can.map((c) => <li key={c}>{c}</li>)}</ul>
                <p className="v-small v-muted">Only VOLT Marketing files can change, and nothing reaches production unless you approve a specific tested commit.
                  You can disconnect any time in VOLT Operations → Development → ChatGPT access.</p>
                <div className="v-row" style={{ justifyContent: 'flex-end', gap: 8 }}>
                  <button type="button" className="v-btn v-btn--line" onClick={() => decide(false)} disabled={busy} data-connect-deny>Don&apos;t allow</button>
                  <button type="button" className="v-btn v-btn--red" onClick={() => decide(true)} disabled={busy} data-connect-allow>{busy ? 'Connecting…' : 'Allow'}</button>
                </div>
              </>
            )}
          </>
        )}
        {!info && !err && id && <p className="v-small v-muted">Loading…</p>}
        {me && !info && err && <p className="v-small"><button type="button" className="v-btn v-btn--link" onClick={switchUser}>Sign in as someone else</button></p>}
      </section>
    </div>
  )
}
