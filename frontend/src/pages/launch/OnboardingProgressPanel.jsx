/**
 * OnboardingProgressPanel — the right rail.
 *
 * ===========================================================================
 * V2: TWO CARDS, NOT FOUR
 * ===========================================================================
 *
 * It used to be a progress ring, a help card, a guide card and a quote card
 * stacked beside a form — four blocks competing with the one thing the
 * customer came here to do, and heavy enough that the form looked like the
 * secondary content on its own page.
 *
 * The rail now answers three questions and stops:
 *
 *     WHERE AM I?          the percentage, and the bar under it
 *     WHAT REMAINS?        the eight sections, current one lit, rest quiet
 *     WHAT HAPPENS NEXT?   one line, in the card's own foot
 *
 * The quote is gone from here. The checklist, when a brand has published one,
 * is a link inside the support card rather than a card of its own — a
 * download does not need a hundred square pixels to be findable.
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
 */
import { Ico } from './LaunchUI'
import { supportBlock, supportLine } from './present'

export default function OnboardingProgressPanel({ steps, activeKey, onSelect,
                                                  overallPct, brand,
                                                  presentation = {} }) {
  const done = steps.filter(s => s.pct >= 100).length
  const p = presentation || {}
  const guide = p.guide || {}
  const support = supportBlock(p)
  const pct = Math.max(0, Math.min(100, Number(overallPct) || 0))

  return (
    <>
      <div className="lp-scard">
        <div className="lp-scard-h"><h3>Where you are</h3></div>
        <div className="lp-scard-b">
          <div className="lp-ring">
            <div className="lp-rt">
              <b>{pct}%</b>
              <span>of your onboarding · {done} of {steps.length} sections</span>
            </div>
          </div>
          <div className="lp-lbar"><i style={{ width: pct + '%' }} /></div>

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
                      {complete ? 'Done'
                        : s.key === activeKey ? 'Now'
                          : s.pct > 0 ? s.pct + '%' : ''}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>

          {/* WHAT HAPPENS NEXT — the third question, answered in the card's
              own foot rather than in a fifth card. It describes the process
              and asserts no date, because nobody has committed to one. */}
          <div className="lp-pfoot">
            <b>What happens next</b>
            Complete all {steps.length} sections and submit. {brand.name}
            {' '}reviews your answers, then the build begins.
          </div>
        </div>
      </div>

      <div className="lp-scard">
        <div className="lp-scard-h"><h3>{support.title}</h3></div>
        <div className="lp-scard-b lp-help">
          {/* The response line is whatever the configuration actually
              promises. With nothing configured this commits the brand to
              helping and to no timeframe — see present.js. There is no
              response time written in this file. */}
          <p>{supportLine(p, brand.name)}</p>

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
            <a className="lp-btn lp-cta" href={'mailto:' + brand.supportEmail}>
              Message your launch team
            </a>
          ) : null}

          {/* The checklist, when one exists. A link, not a card — and nothing
              at all when the brand has not published one, rather than a
              download that 404s. */}
          {guide.url ? (
            <div className="lp-hrow">
              <Ico name="download" size={15} />
              <a href={guide.url} target="_blank" rel="noopener noreferrer">
                {guide.cta_label || guide.title || 'Onboarding checklist'}
              </a>
            </div>
          ) : null}
        </div>
      </div>
    </>
  )
}
