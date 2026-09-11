/**
 * OnboardingProgressPanel — the right rail.
 *
 * Four cards, in the order somebody stuck in a long form needs them: how far
 * am I, who do I ask, is there something I can read offline, and — last,
 * because it is decoration rather than help — the line the brand chose to
 * leave them with.
 *
 * The ring is drawn with two SVG circles rather than a library: one dependency
 * avoided for one shape, and it inherits the surface tokens.
 *
 * ===========================================================================
 * THE PERCENTAGE IS NOT A GUESS AND NOT A PAGE COUNT
 * ===========================================================================
 *
 * `overallPct` comes from the server's intake overview, which counts REQUIRED
 * FIELDS ACTUALLY ANSWERED across the stored sections. A customer who clicks
 * through every step without typing sees 0%, because they have done nothing.
 * A ring driven by which screens somebody visited is a progress bar that lies
 * to the one person it is meant to orient.
 *
 * THE GUIDE CARD IS EITHER A DOCUMENT OR AN HONEST ABSENCE. When the brand
 * has configured a URL it is a real download; when it has not, the card says
 * the checklist is not published yet rather than offering a link that 404s.
 */
import { Ico } from './LaunchUI'

function Ring({ pct }) {
  const r = 30, c = 2 * Math.PI * r
  const on = Math.max(0, Math.min(100, pct)) / 100
  return (
    <svg width="76" height="76" viewBox="0 0 76 76" aria-hidden="true">
      <circle cx="38" cy="38" r={r} fill="none" strokeWidth="7"
        stroke="rgba(150,182,220,.16)" />
      <circle cx="38" cy="38" r={r} fill="none" strokeWidth="7"
        stroke="url(#lpring)" strokeLinecap="round"
        strokeDasharray={c} strokeDashoffset={c * (1 - on)}
        transform="rotate(-90 38 38)"
        style={{ transition: 'stroke-dashoffset .45s ease' }} />
      <defs>
        <linearGradient id="lpring" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#caa155" />
          <stop offset="100%" stopColor="#f5e0aa" />
        </linearGradient>
      </defs>
    </svg>
  )
}

export default function OnboardingProgressPanel({ steps, activeKey, onSelect,
                                                  overallPct, brand,
                                                  presentation = {} }) {
  const done = steps.filter(s => s.pct >= 100).length
  const p = presentation || {}
  const help = p.help || {}
  const guide = p.guide || {}
  const quote = p.quote || null

  return (
    <>
      <div className="lp-scard">
        <div className="lp-scard-h"><h3>Your Onboarding Progress</h3></div>
        <div className="lp-scard-b">
          <div className="lp-ring">
            <Ring pct={overallPct} />
            <div className="lp-rt">
              <b>{overallPct}%</b>
              <span>{done} of {steps.length} sections complete</span>
            </div>
          </div>

          <ul className="lp-steps">
            {steps.map(s => {
              const complete = s.pct >= 100
              const cls = 'lp-steplink'
                + (s.key === activeKey ? ' on' : '')
                + (complete ? ' done' : '')
              return (
                <li key={s.key}>
                  <button type="button" className={cls}
                    onClick={() => onSelect(s.key)}
                    aria-current={s.key === activeKey ? 'step' : undefined}>
                    <span className="lp-tick">{complete ? '✓' : s.n}</span>
                    <span className="lp-sn">{s.label}</span>
                    <span className="lp-sp">
                      {complete ? 'Done' : s.pct > 0 ? s.pct + '%' : '—'}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        </div>
      </div>

      <div className="lp-scard">
        <div className="lp-scard-h"><h3>{help.title || 'Need Help?'}</h3></div>
        <div className="lp-scard-b lp-help">
          <p>
            {help.body || ('Your ' + brand.name + ' implementation team is on '
              + 'this account. Ask us anything — including which of these '
              + 'answers you can safely skip for now.')}
          </p>
          {brand.supportEmail ? (
            <div className="lp-hrow">
              <Ico name="mail" size={15} />{brand.supportEmail}
            </div>
          ) : null}
          {brand.supportPhone ? (
            <div className="lp-hrow">
              <Ico name="phone" size={15} />{brand.supportPhone}
            </div>
          ) : null}
          {brand.supportEmail ? (
            <a className="lp-btn primary lp-cta"
               href={'mailto:' + brand.supportEmail}>
              {help.cta_label || ('Contact ' + brand.name)}
            </a>
          ) : null}
        </div>
      </div>

      <div className="lp-scard">
        <div className="lp-scard-h"><h3>{guide.title || 'Download Guide'}</h3></div>
        <div className="lp-scard-b">
          <p className="lp-gbody">
            {guide.body || ('Need a copy of the required information and files? '
              + 'Download the onboarding checklist.')}
          </p>
          {guide.url ? (
            <a className="lp-btn primary lp-cta" href={guide.url}
               target="_blank" rel="noopener noreferrer">
              <Ico name="download" size={15} />
              {guide.cta_label || 'Download PDF'}
            </a>
          ) : (
            <p className="lp-gnone">
              Your {brand.name} team has not published a checklist for this
              launch yet. Everything asked for is on these screens.
            </p>
          )}
        </div>
      </div>

      {quote && quote.text ? (
        <div className="lp-quote">
          <p>“{quote.text}”</p>
          {quote.attribution ? <span>{quote.attribution}</span> : null}
        </div>
      ) : null}
    </>
  )
}
