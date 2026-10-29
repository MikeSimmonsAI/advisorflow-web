/* OLP - OVERLOOKED PROPERTIES, POWERED BY BOOKABOOST: sign-in (brand exploration).
 *
 * Rendered by Login.jsx for the BookaBoost theme when the BookaBoost product
 * brand is "olp" (src/brand/productBrand.js). It is presentation only: the
 * credential call, token storage, session-expiry flag, safe next-path and the
 * server-decided landing all stay in Login.jsx and are passed in unchanged -
 * exactly as for the approved BookaBoost and EvoSys Pro pages.
 *
 * Owner rules this page keeps:
 *   - OLP leads; BookaBoost is the endorsement ("Powered by BookaBoost");
 *   - only capabilities the product has - no counts, coverage, ROI, results,
 *     compliance badges or trademark symbols;
 *   - never names or links the owner's administration areas;
 *   - no third-party sign-in button (none is configured for this brand);
 *   - the capability tiles are information, not links (no dead controls).
 */
import { useEffect, useRef, useState } from 'react'
import '@fontsource/playfair-display/600.css'
import '@fontsource/playfair-display/700.css'
import bgEstate from './img/bg-estate.webp'
import { OLP } from '../../brand/productBrand'
import './olp-login.css'

const ICON = {
  radar: 'M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM21 21l-4.6-4.6',
  chart: 'M5 20V12M10 20V6M15 20V10M20 20V4M3 20h18',
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
    <svg className="olp-ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={ICON[n]} /></svg>
  )
}

// The proposed OLP stacked logo (owner-supplied vector, awaiting final approval).
// If the file ever fails, the names stay as text so the page never shows a broken image.
function Logo({ className, width }) {
  const [failed, setFailed] = useState(false)
  return (
    <span className={`olp-logo ${className || ''}`}>
      {failed
        ? <span className="olp-logo__text"><b>{OLP.short}</b><span>{OLP.name}</span><small>{OLP.endorsement}</small></span>
        : <img src={OLP.logo} alt={`${OLP.short} - ${OLP.name}, ${OLP.endorsement.replace(/^Powered/, 'powered')}`} width={width}
               height={Math.round(width * 486 / 920)} decoding="async" onError={() => setFailed(true)} />}
    </span>
  )
}

// What the product does - plain descriptions, no numbers or coverage claims.
const CAPABILITIES = [
  ['radar', 'Opportunity Radar', 'Find and analyze overlooked properties in your workspace’s records.'],
  ['chart', 'Property Intelligence', 'Research the source, date and confidence behind every signal.'],
  ['users', 'Buyer Matching', 'See which of your buyers’ buy boxes match each opportunity.'],
  ['deal', 'Deal Room', 'Move the right opportunities into the Deal Room and close.'],
]
const MARKETS = ['Residential', 'Wholesale & distressed', 'Land', 'Farms & ranches', 'Commercial']

const NETWORK_FAIL = /failed to fetch|networkerror|load failed|network request failed/i
function friendly(error) {
  if (!error) return ''
  if (NETWORK_FAIL.test(error)) return 'We couldn’t reach the server. Check your connection and try again.'
  if (/incorrect email or password/i.test(error)) return 'That email and password don’t match. Check them and try again.'
  return error
}

function hideOnError(e) { e.currentTarget.style.visibility = 'hidden' }

export default function OLPLogin({ email, setEmail, password, setPassword, error, loading, onSubmit, expired, nextPath, supportEmail }) {
  const [show, setShow] = useState(false)
  const [fieldErr, setFieldErr] = useState({})
  const emailRef = useRef(null)
  const passRef = useRef(null)
  const support = supportEmail || 'support@bookaboost.live'
  const forgot = '/forgot-password' + (nextPath ? '?next=' + encodeURIComponent(nextPath) : '')
  const message = friendly(error)

  // The tab names the product while this page is open; the previous title
  // comes back when it closes.
  useEffect(() => {
    const prev = document.title
    document.title = `Sign in · ${OLP.short} - ${OLP.name}`
    return () => { document.title = prev }
  }, [])

  // Field checks here give a clear message next to the field; the sign-in
  // itself is still Login.jsx's handleSubmit.
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

  const described = message ? 'olp-err' : expired ? 'olp-expired' : undefined

  return (
    <div className="olp" data-brand="bookaboost" data-product="olp">
      <div className="olp-bg" aria-hidden="true">
        <img className="olp-bg__img" src={bgEstate} alt="" decoding="async" onError={hideOnError} />
        <div className="olp-bg__veil" />
      </div>

      <main className="olp-main">
        {/* Phones: the brand and headline sit just above the form, as in the
            mobile mockup. Hidden on wider screens, where the left column has them. */}
        <div className="olp-mhead" aria-hidden="true">
          <Logo className="olp-logo--mhead" width={230} />
          <p className="olp-mhead__h">Find What <span>Others Miss.</span></p>
        </div>

        <section className="olp-panel" aria-labelledby="olp-signin-h">
          <Logo className="olp-logo--panel" width={220} />
          <h1 id="olp-signin-h" className="olp-panel__h">Sign in</h1>
          <p className="olp-panel__sub">Enter your credentials to continue.</p>

          <form className="olp-form" onSubmit={submit} noValidate aria-describedby={described}>
            <div className="olp-field">
              <label className="olp-field__l" htmlFor="olp-email">Email address</label>
              <span className={`olp-input${fieldErr.email ? ' is-bad' : ''}`}>
                <Ico n="mail" size={18} />
                <input id="olp-email" className="olp-in" ref={emailRef} type="email" name="email" autoComplete="username" inputMode="email"
                       autoCapitalize="none" spellCheck="false" required autoFocus
                       value={email} placeholder="you@company.com"
                       onChange={(e) => { setEmail(e.target.value); if (fieldErr.email) setFieldErr((f) => ({ ...f, email: undefined })) }}
                       aria-invalid={fieldErr.email || message ? 'true' : undefined}
                       aria-describedby={fieldErr.email ? 'olp-email-err' : undefined} />
              </span>
              {fieldErr.email ? <p id="olp-email-err" className="olp-field__err">{fieldErr.email}</p> : null}
            </div>

            <div className="olp-field">
              <label className="olp-field__l" htmlFor="olp-password">Password</label>
              <span className={`olp-input${fieldErr.password ? ' is-bad' : ''}`}>
                <Ico n="lock" size={18} />
                <input id="olp-password" className="olp-in" ref={passRef} type={show ? 'text' : 'password'} name="password" autoComplete="current-password" required
                       value={password} placeholder="Your password"
                       onChange={(e) => { setPassword(e.target.value); if (fieldErr.password) setFieldErr((f) => ({ ...f, password: undefined })) }}
                       aria-invalid={fieldErr.password || message ? 'true' : undefined}
                       aria-describedby={fieldErr.password ? 'olp-password-err' : undefined} />
                <button type="button" className="olp-eye" onClick={() => setShow((s) => !s)} aria-pressed={show}
                        aria-label={show ? 'Hide password' : 'Show password'} aria-controls="olp-password">
                  <Ico n={show ? 'eyeOff' : 'eye'} size={19} />
                </button>
              </span>
              {fieldErr.password ? <p id="olp-password-err" className="olp-field__err">{fieldErr.password}</p> : null}
            </div>

            {expired && !message ? (
              <p id="olp-expired" className="olp-note" role="status"><Ico n="clock" size={18} />
                <span>Your session ended. Sign in again to pick up where you left off.</span></p>
            ) : null}
            {message ? (
              <p id="olp-err" className="olp-error" role="alert"><Ico n="alert" size={18} /><span>{message}</span></p>
            ) : null}

            <button type="submit" className="olp-submit" disabled={loading} aria-busy={loading || undefined}>
              {loading ? <><span className="olp-spin" aria-hidden="true" />Signing in…</> : <>Sign in<Ico n="arrow" size={18} /></>}
            </button>
            <a className="olp-forgot" href={forgot}>Forgot password?</a>
          </form>
          <p className="olp-help">Need help? <a href={`mailto:${support}`}>{support}</a></p>
        </section>

        <section className="olp-hero" aria-labelledby="olp-headline">
          <Logo className="olp-logo--hero" width={360} />
          <h2 id="olp-headline" className="olp-h1"><span>Find What</span> <span className="olp-h1--gold">Others Miss.</span></h2>
          <p className="olp-lead">Discover overlooked real estate opportunities, research the evidence, and connect with qualified buyers—all in one professional workspace.</p>
          <ul className="olp-caps" aria-label={`What ${OLP.short} does`}>
            {CAPABILITIES.map(([ic, t, d]) => (
              <li key={t} className="olp-cap">
                <span className="olp-cap__ic"><Ico n={ic} size={24} /></span>
                <span className="olp-cap__tx"><b>{t}</b><small>{d}</small></span>
              </li>
            ))}
          </ul>
        </section>
      </main>

      <section className="olp-markets" aria-label="Markets">
        <p className="olp-markets__eyebrow">Real estate opportunity platform</p>
        <ul className="olp-markets__list">
          {MARKETS.map((m) => <li key={m}>{m}</li>)}
        </ul>
      </section>

      <footer className="olp-foot">
        <span>© {new Date().getFullYear()} {OLP.name} ({OLP.short}) <i aria-hidden="true">|</i> <span className="olp-foot__by">{OLP.endorsement}</span></span>
        <span className="olp-foot__links">
          <a href="/privacy.html">Privacy</a>
          <a href={`mailto:${support}`}>Support</a>
        </span>
      </footer>
    </div>
  )
}
