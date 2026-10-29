/* EVOSYS PRO SIGN-IN (owner-approved design, Oct 28).
 *
 * Rendered by Login.jsx ONLY when the host is an EvoSys Pro domain; every other
 * brand keeps its own sign-in. This file is presentation: the credential
 * handling, token storage, session-expiry message and post-login routing are
 * Login.jsx's, passed in unchanged.
 *
 * What this page must never do (owner directive):
 *   - name or link God Mode, the Executive Suite or any administration area -
 *     where a person lands is decided by the server after sign-in;
 *   - show statistics, testimonials, certifications or provider buttons that
 *     are not real;
 *   - link a customer workspace: the six industry cards are information only;
 *   - use anything but the official logo (public-site/assets/evosyspro-logo.png).
 */
import { useState } from 'react'
import bgCity from './img/bg-city.webp'
import cardWholesale from './img/card-wholesale.webp'
import cardRoofing from './img/card-roofing.webp'
import cardInsurance from './img/card-insurance.webp'
import cardEnergy from './img/card-energy.webp'
import cardFuneral from './img/card-funeral.webp'
import cardCommercial from './img/card-commercial.webp'
import './evosys-login.css'

const ICON = {
  ai: 'M12 8V4H8M4 12h2M18 12h2M9 13v2M15 13v2M6 8h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2z',
  calendar: 'M8 2v4M16 2v4M3 10h18M5 4h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zM8 14h2M14 14h2M8 18h2',
  building: 'M4 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18M16 10h2a2 2 0 0 1 2 2v10M2 22h20M8 6h4M8 10h4M8 14h4M8 18h4',
  layers: 'M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5',
  home: 'M3 11l9-8 9 8M5 9v12h14V9M9 21v-6h6v6',
  roof: 'M2 12L12 4l10 8M6 9.5V20h12V9.5M10 20v-5h4v5',
  shield: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10zM9 12l2 2 4-4',
  bolt: 'M13 2L4 14h7l-1 8 9-12h-7z',
  heart: 'M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 0 0-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 0 0 0-7.8z',
  briefcase: 'M3 7h18v13H3zM8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 13h18',
  eye: 'M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z',
  eyeOff: 'M3 3l18 18M10.6 5.1A10 10 0 0 1 12 5c6 0 10 7 10 7a17 17 0 0 1-3.2 3.9M6.6 6.6C3.9 8.3 2 12 2 12s4 7 10 7a9.6 9.6 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2',
  arrow: 'M5 12h14M13 6l6 6-6 6',
  mail: 'M4 4h16v16H4zM4 6l8 7 8-7',
  lock: 'M5 11h14v10H5zM8 11V7a4 4 0 0 1 8 0v4',
}
function Ico({ n, size = 20 }) {
  return (
    <svg className="evl-ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={ICON[n]} /></svg>
  )
}

// The official mark. A decorative failure never blanks the page: on error the
// brand name stays as text (alt) and the layout holds.
function Logo({ className }) {
  // evosyspro-logo-on-dark-440.png is the official mark (public-site/assets/evosyspro-logo.png)
  // scaled to 440 px with its black ground turned into transparency (colour / max-channel),
  // so it sits on navy exactly as it does on black - no redraw, no recolour.
  const [failed, setFailed] = useState(false)
  return (
    <span className={`evl-logo ${className || ''}`}>
      {failed ? <span className="evl-logo__text">EvoSys Pro</span>
        : <img src="/brands/evosyspro-logo-on-dark-440.png" alt="EvoSys Pro" width="440" height="281" decoding="async" onError={() => setFailed(true)} />}
    </span>
  )
}

const CAPABILITIES = [
  ['ai', 'AI Workforce', 'AI assistants that take on routine follow-up and research, with your team in control.'],
  ['calendar', 'Lead-to-Appointment Automation', 'Capture, qualify and route new leads toward booked appointments.'],
  ['building', 'Multi-Business Operations', 'Run separate businesses from one sign-in, each in its own secure workspace.'],
  ['layers', 'CRM, Communications & Analytics', 'Contacts, conversations and reporting working together in one place.'],
]

const INDUSTRIES = [
  { key: 'wholesale', icon: 'home', name: 'Wholesale', steps: ['Find', 'Analyze', 'Acquire'], img: cardWholesale, featured: true,
    text: 'Distressed-property discovery, acquisitions and deal management for real-estate wholesaling.' },
  { key: 'roofing', icon: 'roof', name: 'Roofing', steps: ['Generate', 'Inspect', 'Close'], img: cardRoofing,
    text: 'Lead management, inspections and roofing sales.' },
  { key: 'insurance', icon: 'shield', name: 'Insurance', steps: ['Capture', 'Quote', 'Enroll'], img: cardInsurance,
    text: 'Agency operations, client management and policy workflows.' },
  { key: 'energy', icon: 'bolt', name: 'Energy', steps: ['Compare', 'Enroll', 'Manage'], img: cardEnergy,
    text: 'Energy sales: compare plans, enroll customers and manage accounts.' },
  { key: 'funeral', icon: 'heart', name: 'Funeral Services', steps: ['File Review', 'Appointments', 'Pre-Planning'], img: cardFuneral,
    text: 'Funeral home operations and caring customer follow-up.' },
  { key: 'commercial', icon: 'briefcase', name: 'Commercial Services', steps: ['Leads', 'Operations', 'Growth'], img: cardCommercial,
    text: 'B2B service businesses and multi-location operations.' },
]

function hideOnError(e) { e.currentTarget.style.visibility = 'hidden' }

export default function EvosysLogin({ email, setEmail, password, setPassword, error, loading, onSubmit, expired, nextPath, supportEmail }) {
  const [show, setShow] = useState(false)
  const support = supportEmail || 'support@evosyspro.live'
  const forgot = '/forgot-password' + (nextPath ? '?next=' + encodeURIComponent(nextPath) : '')
  return (
    <div className="evl" data-brand="evosyspro">
      <div className="evl-bg" aria-hidden="true">
        <img className="evl-bg__img" src={bgCity} alt="" onError={hideOnError} />
        <div className="evl-bg__veil" />
      </div>

      <header className="evl-top">
        <Logo className="evl-logo--top" />
      </header>

      <main className="evl-main" id="evl-main">
        <section className="evl-hero" aria-labelledby="evl-headline">
          <p className="evl-pill">AI Business Operating System</p>
          <h1 id="evl-headline" className="evl-h1">
            <span>One Platform.</span>
            <span className="evl-h1--blue">Multiple Businesses.</span>
            <span>Real Results.</span>
          </h1>
          <p className="evl-lead">AI-powered tools, automation, and people working together to help businesses acquire leads, book appointments, manage operations, and grow.</p>
          <ul className="evl-caps" aria-label="Platform capabilities">
            {CAPABILITIES.map(([ic, t, d]) => (
              <li key={t} className="evl-cap">
                <span className="evl-cap__ic"><Ico n={ic} size={22} /></span>
                <span><b>{t}</b><small>{d}</small></span>
              </li>
            ))}
          </ul>
        </section>

        <section className="evl-panel" aria-labelledby="evl-signin-h">
          <Logo className="evl-logo--panel" />
          <h2 id="evl-signin-h" className="evl-panel__h">Sign in</h2>
          <p className="evl-panel__sub">Enter your credentials to continue.</p>
          <form className="evl-form" onSubmit={onSubmit} noValidate={false} aria-describedby={error ? 'evl-err' : expired ? 'evl-expired' : undefined}>
            <label className="evl-field" htmlFor="evl-email">
              <span className="evl-field__l">Email</span>
              <span className="evl-input">
                <Ico n="mail" size={18} />
                <input id="evl-email" type="email" name="email" autoComplete="username" inputMode="email" required autoFocus
                       value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@company.com"
                       aria-invalid={error ? 'true' : undefined} />
              </span>
            </label>
            <label className="evl-field" htmlFor="evl-password">
              <span className="evl-field__l">Password</span>
              <span className="evl-input">
                <Ico n="lock" size={18} />
                <input id="evl-password" type={show ? 'text' : 'password'} name="password" autoComplete="current-password" required
                       value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Your password"
                       aria-invalid={error ? 'true' : undefined} />
                <button type="button" className="evl-eye" onClick={() => setShow((s) => !s)} aria-pressed={show}
                        aria-label={show ? 'Hide password' : 'Show password'} aria-controls="evl-password">
                  <Ico n={show ? 'eyeOff' : 'eye'} size={19} />
                </button>
              </span>
            </label>

            {expired && !error ? <p id="evl-expired" className="evl-note" role="status">Your session ended. Sign in again to pick up where you left off.</p> : null}
            {error ? <p id="evl-err" className="evl-error" role="alert">{error}</p> : null}

            <button type="submit" className="evl-submit" disabled={loading} aria-busy={loading || undefined}>
              {loading ? <><span className="evl-spin" aria-hidden="true" />Signing in…</> : <>Sign in<Ico n="arrow" size={18} /></>}
            </button>
            <a className="evl-forgot" href={forgot}>Forgot password?</a>
          </form>
          <p className="evl-help">Need help? <a href={`mailto:${support}`}>{support}</a></p>
        </section>
      </main>

      <section className="evl-ind" aria-labelledby="evl-ind-h">
        <p className="evl-eyebrow">Built for multiple businesses</p>
        <h2 id="evl-ind-h" className="evl-h2">One platform across your business portfolio</h2>
        <ul className="evl-cards">
          {INDUSTRIES.map((x) => (
            <li key={x.key} className={`evl-card${x.featured ? ' evl-card--featured' : ''}`}>
              <div className="evl-card__media">
                <img src={x.img} alt="" loading="lazy" decoding="async" width="640" height="300" onError={hideOnError} />
                
              </div>
              <div className="evl-card__body">
                <span className="evl-card__ic"><Ico n={x.icon} size={20} /></span>
                <h3 className="evl-card__h">{x.name}</h3>
                <p className="evl-card__steps">{x.steps.map((st, i) => <span key={st}>{st}{i < x.steps.length - 1 ? ' •' : ''}</span>).reduce((a, el) => a.length ? [...a, ' ', el] : [el], [])}</p>
                <p className="evl-card__t">{x.text}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>

      <footer className="evl-foot">
        <span>© {new Date().getFullYear()} EvoSys Pro</span>
        <a href="/privacy.html">Privacy</a>
        <a href={`mailto:${support}`}>Support</a>
      </footer>
    </div>
  )
}
