/* RESTLAND BOOKIT SIGN-IN (owner, Nov 27): Restland's own sign-in page for its
 * staff, at /restland. Restland's look only - no platform name, logo, link or
 * support address anywhere on it. Presentation only: the credential call,
 * tokens, session-expiry flag, safe next-path and the server-decided landing
 * all stay in Login.jsx and are passed in unchanged, exactly as for the other
 * branded sign-in pages. The memorial park picture is an illustration, never
 * presented as a photograph of the property. */
import { useState } from 'react'
import { useRestlandBrand } from './RestlandAccess'
import './restland-login.css'

export const RESTLAND_BRAND_KEY = 'af_login_brand'

export function isRestlandLogin() {
  try {
    const p = new URLSearchParams(window.location.search)
    return window.location.pathname === '/restland' || p.get('brand') === 'restland'
      || localStorage.getItem(RESTLAND_BRAND_KEY) === 'restland'
  } catch { return false }
}

export default function RestlandLogin({ email, setEmail, password, setPassword, error, loading, onSubmit, expired, nextPath }) {
  const [show, setShow] = useState(false)
  useRestlandBrand('Sign in | Restland BookIT')
  const forgot = '/forgot-password?brand=restland' + (nextPath ? '&next=' + encodeURIComponent(nextPath) : '')
  return (
    <div className="rll">
      <section className="rll-art" aria-hidden="true">
        <div className="rll-art__text"><strong>Restland</strong><span>Cemetery &amp; Funeral Home</span>
          <p>Book It. Close It. Track It.</p></div>
      </section>
      <main className="rll-main">
        <form className="rll-card" onSubmit={onSubmit}>
          <div className="rll-brand"><strong>Restland</strong><span>BookIT</span></div>
          <h1>Sign in</h1>
          <p className="rll-lede">Use your work email and the password you set.</p>
          <label htmlFor="rll-email">Email</label>
          <input id="rll-email" type="email" name="email" autoComplete="username" inputMode="email" required autoFocus
                 value={email} onChange={e => setEmail(e.target.value)} />
          <label htmlFor="rll-password">Password</label>
          <div className="rll-pw">
            <input id="rll-password" type={show ? 'text' : 'password'} name="password" autoComplete="current-password" required
                   value={password} onChange={e => setPassword(e.target.value)} />
            <button type="button" onClick={() => setShow(!show)} aria-label={show ? 'Hide password' : 'Show password'}>{show ? 'Hide' : 'Show'}</button>
          </div>
          {error ? <p className="rll-note err" role="alert">{error}</p> : null}
          {expired && !error ? <p className="rll-note" role="status">Your session ended. Sign in again to pick up where you left off.</p> : null}
          <button className="rll-submit" type="submit" disabled={loading}>{loading ? 'Signing in…' : 'Sign in'}</button>
          <a className="rll-forgot" href={forgot}>Forgot password?</a>
          <p className="rll-help">Need help signing in? Ask your manager.</p>
        </form>
      </main>
    </div>
  )
}
