/**
 * Access activation — the first screen a new customer admin OR a
 * brand-sales user sees. One page, two token families, routed by the token's
 * own prefix so neither has to be guessed.
 *
 * Public by necessity: they have no account to log into yet. It takes a one-time
 * token from the URL, confirms who the invitation is for, and lets them set
 * their own password.
 *
 * NO PASSWORD IS EVER SHOWN OR PREFILLED. There is no temporary credential to
 * reveal — the account was created with a secret nobody knows. Every failure
 * says the same thing, so a bad token cannot be probed for whether it once
 * existed and an email address cannot be confirmed by trying links.
 *
 * On success this sends them to the normal login screen rather than signing them
 * in. One front door means one set of lockout, session and audit behaviour.
 *
 * IT HAS ITS OWN STYLESHEET. Not God Mode's operator sheet and not a tenant
 * brand theme: this page is seen by a stranger, on an unknown brand, before any
 * branding has been resolved, so it has to look finished standing alone.
 */
import { useEffect, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import './Activate.css'

const GENERIC = 'This activation link is invalid or has expired.'

// Two token families reach this page, and they live in two different tables:
//   act_...  a CUSTOMER admin joining their tenant   -> /auth/activation
//   stf_...  a BRAND-SALES user unlocking their login -> /auth/staff-activation
//
// The token says which. Routing on the prefix means no guessing and no
// try-one-then-the-other, which would leak which family a token belongs to by
// timing and would double every rate-limit hit.
function apiBaseFor(token) {
  return (token || '').startsWith('stf_') ? '/auth/staff-activation' : '/auth/activation'
}

export default function Activate() {
  const [params] = useSearchParams()
  const nav = useNavigate()
  const token = params.get('token') || ''
  const [invite, setInvite] = useState(null)
  const [err, setErr] = useState('')
  const [pw, setPw] = useState('')
  const [pw2, setPw2] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  useEffect(() => {
    if (!token) { setErr(GENERIC); return }
    api.get(apiBaseFor(token) + '?token=' + encodeURIComponent(token))
      .then(setInvite)
      .catch(e => setErr(e.message || GENERIC))
  }, [token])

  async function submit(e) {
    e.preventDefault()
    if (pw !== pw2) { setErr('The two passwords do not match.'); return }
    setBusy(true); setErr('')
    try {
      await api.post(apiBaseFor(token) + '/accept', { token, new_password: pw })
      setDone(true)
    } catch (ex) {
      setErr(ex.message || 'Could not set your password.')
      setBusy(false)
    }
  }

  if (done) {
    return (
      <div className="act-scope">
        <div className="act-card">
          <h1>You're all set</h1>
          <div className="act-note ok">Your password is saved. You can sign in now.</div>
          <button className="act-btn" onClick={() => nav('/login')}>Go to sign in</button>
        </div>
      </div>
    )
  }

  if (err && !invite) {
    return (
      <div className="act-scope">
        <div className="act-card">
          <h1>This link isn't usable</h1>
          <div className="act-note err">{err}</div>
          <p className="act-quiet">
            Activation links expire, and issuing a new one replaces any earlier
            link. Ask whoever set up your account to send a fresh one.
          </p>
        </div>
      </div>
    )
  }

  if (!invite) {
    return (
      <div className="act-scope">
        <div className="act-card">
          <h1>Checking your link…</h1>
          <p className="act-quiet">One moment.</p>
        </div>
      </div>
    )
  }

  return (
    <div className="act-scope">
      <div className="act-card">
        <h1>Set your password</h1>
        <p className="lede">
          {invite.purpose === 'reset' ? (
            <>Welcome back, {invite.full_name}. Choose a new password for your{' '}
              <strong>{invite.workspace || invite.organization_name}</strong> access.</>
          ) : invite.workspace ? (
            <>Welcome, {invite.full_name}. You're being set up in the{' '}
              <strong>{invite.workspace}</strong> sales workspace. Choose a password —
              nobody else has ever had one for this account.</>
          ) : (
            <>Welcome, {invite.full_name}. You're being set up as an administrator for{' '}
              <strong>{invite.organization_name}</strong>. Choose a password — nobody
              else has ever had one for this account.</>
          )}
        </p>

        {err ? <div className="act-note err">{err}</div> : null}

        <form onSubmit={submit}>
          <div className="act-field">
            <label>Your email</label>
            <input value={invite.email} readOnly />
          </div>
          <div className="act-field">
            <label>New password</label>
            <input type="password" value={pw} onChange={e => setPw(e.target.value)}
                   autoComplete="new-password" minLength={10} required autoFocus />
            <div className="hint">At least 10 characters.</div>
          </div>
          <div className="act-field">
            <label>Confirm password</label>
            <input type="password" value={pw2} onChange={e => setPw2(e.target.value)}
                   autoComplete="new-password" minLength={10} required />
          </div>
          <button className="act-btn" type="submit" disabled={busy || pw.length < 10}>
            {busy ? 'Saving…' : 'Set password'}
          </button>
        </form>

        <p className="act-quiet">
          This link works once and then stops working. Nobody — including the
          person who set up your account — has ever known a password for it.
        </p>
      </div>
    </div>
  )
}
