/* BOOKABOOST SIGN-IN - private application login (app.bookaboost.live/login).
 *
 * Rendered by Login.jsx ONLY for the BookaBoost theme; EvoSys Pro, Harmony
 * Hustle and AdvisorFlow keep their own sign-in. This file is presentation:
 * the credential call, token storage, session-expiry flag, safe next-path and
 * the server-decided landing page are Login.jsx's, passed in unchanged.
 *
 * Owner rules this page keeps:
 *   - official BookaBoost mark and wordmark only (/brands/bookaboost-*.svg);
 *   - describe only what the product actually does - no counts, coverage,
 *     ROI or results;
 *   - never name or link the owner's administration areas; where a person
 *     lands is decided by the server after sign-in;
 *   - no third-party sign-in button: none is configured for this brand;
 *   - not the public bookaboost.live website, and no Opportunity Radar here.
 */
import { useRef, useState } from 'react'
import '@fontsource/playfair-display/600.css'
import '@fontsource/playfair-display/700.css'
import bgCity from './img/bg-city.webp'
import './bookaboost-login.css'

const ICON = {
  search: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4.6-4.6M8.5 11h5M11 8.5v5',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  users: 'M16 20v-1.5a3.5 3.5 0 0 0-3.5-3.5h-5A3.5 3.5 0 0 0 4 18.5V20M10 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM20 20v-1.5a3.5 3.5 0 0 0-2.5-3.35M15.5 4.2a3.5 3.5 0 0 1 0 6.6',
  deal: 'M3 7h18v13H3zM8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18M10 12v2h4v-2',
  mail: 'M4 5h16v14H4zM4 7l8 6 8-6',
  lock: 'M6 11h12v10H6zM8.5 11V7.5a3.5 3.5 0 0 1 7 0V11',
  eye: 'M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  eyeOff: 'M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 3.9M6.6 6.6C3.9 8.3 2 12 2 12s3.6 7 10 7a9.6 9.6 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2',
  arrow: 'M5 12h14M13 6l6 6-6 6',
  alert: 'M12 8v5M12 16.5v.5M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z',
  clock: 'M12 7v5l3 2M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z',
}
function Ico({ n, size = 20 }) {
  return (
    <svg className="bbl-ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={ICON[n]} /></svg>
  )
}

// The official wordmark. If the file ever fails to load, the brand name stays
// as text so the page never shows a broken image.
function Logo({ className, width = 168 }) {
  const [failed, setFailed] = useState(false)
  return (
    <span className={`bbl-logo ${className || ''}`}>
      {failed
        ? <span className="bbl-logo__text">Booka<b>Boost</b></span>
        : <img src="/brands/bookaboost-logo.svg" alt="BookaBoost" width={width} height={Math.round(width * 38 / 168)}
               decoding="async" onError={() => setFailed(true)} />}
    </span>
  )
}

// What BookaBoost Wholesale does today - plain descriptions, no numbers.
const CAPABILITIES = [
  ['search', 'Property Intelligence', 'Research properties with the source, date and confidence behind every signal.'],
  ['chart', 'Market Research', 'Review the property, foreclosure and county records your workspace holds.'],
  ['users', 'Buyer Matching', 'Check opportunities against your own buyers’ buy boxes.'],
  ['deal', 'Deal Room', 'Move the right opportunities into the Deal Room and work them to close.'],
]

const NETWORK_FAIL = /failed to fetch|networkerror|load failed|network request failed/i
function friendly(error) {
  if (!error) return ''
  if (NETWORK_FAIL.test(error)) return 'We couldn’t reach BookaBoost. Check your connection and try again.'
  if (/incorrect email or password/i.test(error)) return 'That email and password don’t match. Check them and try again.'
  return error
}

function hideOnError(e) { e.currentTarget.style.visibility = 'hidden' }

export default function BookaBoostLogin({ email, setEmail, password, setPassword, error, loading, onSubmit, expired, nextPath, supportEmail }) {
  const [show, setShow] = useState(false)
  const [fieldErr, setFieldErr] = useState({})
  const emailRef = useRef(null)
  const passRef = useRef(null)
  const support = supportEmail || 'support@bookaboost.live'
  const forgot = '/forgot-password' + (nextPath ? '?next=' + encodeURIComponent(nextPath) : '')
  const message = friendly(error)

  // Field checks happen here so the person gets a clear message next to the
  // field; the sign-in itself is still Login.jsx's handleSubmit.
  function submit(e) {
    e.preventDefault()
    const errs = {}
    const em = (email || '').trim()
    if (!em) errs.email = 'Enter your email address.'
    else if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(em)) errs.email = 'Enter a valid email address, like name@company.com.'
    if (!password) errs.password = 'Enter your password.'
    setFieldErr(errs)
    if (errs.email) { emailRef.current && emailRef.current.focus(); return }
    if (errs.password) { passRef.current && passRef.current.focus(); return }
    onSubmit(e)
  }

  const described = message ? 'bbl-err' : expired ? 'bbl-expired' : undefined

  return (
    <div className="bbl" data-brand="bookaboost">
      <div className="bbl-bg" aria-hidden="true">
        <img className="bbl-bg__img" src={bgCity} alt="" decoding="async" onError={hideOnError} />
        <div className="bbl-bg__veil" />
      </div>

      <main className="bbl-main">
        {/* The sign-in comes first in reading order: on a phone it is the first
            thing on screen, and keyboard users reach it before the introduction. */}
        <section className="bbl-panel" aria-labelledby="bbl-signin-h">
          <Logo className="bbl-logo--panel" width={184} />
          <p className="bbl-panel__tag" aria-hidden="true">Find What <span>Others Miss.</span></p>
          <h1 id="bbl-signin-h" className="bbl-panel__h">Sign in</h1>
          <p className="bbl-panel__sub">Enter your credentials to continue.</p>

          <form className="bbl-form" onSubmit={submit} noValidate aria-describedby={described}>
            <div className="bbl-field">
              <label className="bbl-field__l" htmlFor="bbl-email">Email address</label>
              <span className={`bbl-input${fieldErr.email ? ' is-bad' : ''}`}>
                <Ico n="mail" size={18} />
                <input id="bbl-email" className="bbl-in" ref={emailRef} type="email" name="email" autoComplete="username" inputMode="email"
                       autoCapitalize="none" spellCheck="false" required autoFocus
                       value={email} placeholder="you@company.com"
                       onChange={(e) => { setEmail(e.target.value); if (fieldErr.email) setFieldErr((f) => ({ ...f, email: undefined })) }}
                       aria-invalid={fieldErr.email || message ? 'true' : undefined}
                       aria-describedby={fieldErr.email ? 'bbl-email-err' : undefined} />
              </span>
              {fieldErr.email ? <p id="bbl-email-err" className="bbl-field__err">{fieldErr.email}</p> : null}
            </div>

            <div className="bbl-field">
              <label className="bbl-field__l" htmlFor="bbl-password">Password</label>
              <span className={`bbl-input${fieldErr.password ? ' is-bad' : ''}`}>
                <Ico n="lock" size={18} />
                <input id="bbl-password" className="bbl-in" ref={passRef} type={show ? 'text' : 'password'} name="password" autoComplete="current-password" required
                       value={password} placeholder="Your password"
                       onChange={(e) => { setPassword(e.target.value); if (fieldErr.password) setFieldErr((f) => ({ ...f, password: undefined })) }}
                       aria-invalid={fieldErr.password || message ? 'true' : undefined}
                       aria-describedby={fieldErr.password ? 'bbl-password-err' : undefined} />
                <button type="button" className="bbl-eye" onClick={() => setShow((s) => !s)} aria-pressed={show}
                        aria-label={show ? 'Hide password' : 'Show password'} aria-controls="bbl-password">
                  <Ico n={show ? 'eyeOff' : 'eye'} size={19} />
                </button>
              </span>
              {fieldErr.password ? <p id="bbl-password-err" className="bbl-field__err">{fieldErr.password}</p> : null}
            </div>

            {expired && !message ? (
              <p id="bbl-expired" className="bbl-note" role="status"><Ico n="clock" size={18} />
                <span>Your session ended. Sign in again to pick up where you left off.</span></p>
            ) : null}
            {message ? (
              <p id="bbl-err" className="bbl-error" role="alert"><Ico n="alert" size={18} /><span>{message}</span></p>
            ) : null}

            <button type="submit" className="bbl-submit" disabled={loading} aria-busy={loading || undefined}>
              {loading ? <><span className="bbl-spin" aria-hidden="true" />Signing in…</> : <>Sign in<Ico n="arrow" size={18} /></>}
            </button>
            <a className="bbl-forgot" href={forgot}>Forgot password?</a>
          </form>
          <p className="bbl-help">Need help? <a href={`mailto:${support}`}>{support}</a></p>
        </section>

        <section className="bbl-hero" aria-labelledby="bbl-headline">
          <Logo className="bbl-logo--hero" width={230} />
          <p className="bbl-eyebrow">Real estate opportunity platform</p>
          <h2 id="bbl-headline" className="bbl-h1"><span>Find What</span> <span className="bbl-h1--gold">Others Miss.</span></h2>
          <p className="bbl-lead">Discover overlooked real estate opportunities, research the evidence, and connect with qualified buyers—all in one professional workspace.</p>
          <ul className="bbl-caps" aria-label="What BookaBoost does">
            {CAPABILITIES.map(([ic, t, d]) => (
              <li key={t} className="bbl-cap">
                <span className="bbl-cap__ic"><Ico n={ic} size={22} /></span>
                <span className="bbl-cap__tx"><b>{t}</b><small>{d}</small></span>
              </li>
            ))}
          </ul>
          <p className="bbl-markets">Residential · Wholesale &amp; distressed · Land · Farms &amp; ranches · Commercial</p>
        </section>
      </main>

      <footer className="bbl-foot">
        <span>© {new Date().getFullYear()} BookaBoost</span>
        <a href="/privacy.html">Privacy</a>
        <a href={`mailto:${support}`}>Support</a>
      </footer>
    </div>
  )
}
