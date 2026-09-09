/**
 * OnboardingProgressPanel — the right rail.
 *
 * Three cards, in the order somebody stuck in a long form needs them: how far
 * am I, who do I ask, and is there something I can read offline.
 *
 * The ring is drawn with two SVG circles rather than a library — one
 * dependency avoided for one shape, and it inherits the surface tokens.
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
                                                  overallPct, brand }) {
  const done = steps.filter(s => s.pct >= 100).length
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
        <div className="lp-scard-h"><h3>Need Help?</h3></div>
        <div className="lp-scard-b lp-help">
          <p>
            Your {brand.name} implementation team is on this account. Ask us
            anything — including which of these answers you can safely skip
            for now.
          </p>
          <div className="lp-hrow">
            <Ico name="mail" size={15} />{brand.supportEmail}
          </div>
          <div className="lp-hrow">
            <Ico name="phone" size={15} />{brand.supportPhone}
          </div>
        </div>
      </div>

      <div className="lp-scard">
        <div className="lp-scard-h"><h3>Onboarding Guide</h3></div>
        {/* STAGE 1: no document behind this yet. It is the card's shape and
            placement being judged, not a download. */}
        <button type="button" className="lp-guide" disabled>
          <span className="lp-gi"><Ico name="doc" size={17} /></span>
          <span>
            <b>Onboarding checklist (PDF)</b>
            <span>Everything asked for here, in one printable list you can
              hand around your team.</span>
          </span>
        </button>
      </div>
    </>
  )
}
