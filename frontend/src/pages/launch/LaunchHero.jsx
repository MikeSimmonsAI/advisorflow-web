/**
 * LaunchHero — the customer's welcome.
 *
 * ===========================================================================
 * EVERY WORD AND EVERY PICTURE HERE IS CONFIGURATION
 * ===========================================================================
 *
 * The eyebrow, the title, the subtitle, the paragraph, the hero image, the
 * customer's own logo artwork and the line they describe themselves with all
 * arrive in `experience.presentation`, resolved on the server from four
 * layers: platform default, industry template, white-label brand, and this
 * customer. None of it is typed into this file.
 *
 * That is not tidiness. The alternative — a hero written for the first
 * customer — is a second customer with somebody else's imagery and a third
 * customer who needs a release.
 *
 * WHAT THIS FILE STILL DECIDES: the arrangement. Where the identity sits,
 * what overlaps the image, how it collapses on a narrow screen. Configuration
 * supplies content; the shell supplies the design, and a brand cannot
 * accidentally rearrange the page by editing a settings row.
 *
 * ===========================================================================
 * V2: THE STATUS PANEL, AND WHY IT IS THE ONLY ASSERTION ON THE SCREEN
 * ===========================================================================
 *
 * Everything to the left of the panel describes; the panel ASSERTS. It carries
 * the launch state, the percentage and the section count, and all three come
 * from the server's intake overview, which counts required fields actually
 * answered. A customer who has clicked through every stage and typed nothing
 * reads NOT STARTED and 0%, because that is what is true.
 *
 * Nothing in the configuration can move those numbers — the presentation
 * layer supplies the words around them and the composer supplies the values —
 * which is the property that lets an operator show this screen to a customer
 * and stand behind what it says.
 *
 * ===========================================================================
 * TWO EARLIER BUGS, KEPT FIXED
 * ===========================================================================
 *
 * 1. THE COPY NAMED A THIRD PARTY. The customer-facing paragraph referred by
 *    name to one vertical's rate marketplace — a system one customer in one
 *    industry uses, shown to every customer of every industry. The paragraph
 *    is now the configured intro, and any specific system is named only where
 *    the server's schema names it, for the customers it applies to.
 *
 * 2. THE TARGET DATE NEVER RENDERED. It read `customer.targetGoLive`;
 *    GET /launch/me returns it on `implementation.target_launch_date`.
 */
import { Mark } from './LaunchUI'
import { intakeState } from './present'

function dateLabel(iso) {
  if (!iso) return null
  const d = new Date(String(iso).length <= 10 ? iso + 'T00:00:00' : iso)
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString(undefined,
    { month: 'long', day: 'numeric', year: 'numeric' })
}

export default function LaunchHero({ brand, customer, implementation, state,
                                     presentation = {}, progress = null }) {
  const golive = dateLabel(implementation && implementation.target_launch_date)
  const st = intakeState(state)

  const p = presentation || {}
  const title = p.title || customer.name
  const heroImage = p.hero_image_url || null
  const heroLogo = p.hero_logo_url || customer.logoUrl || null
  const tagline = p.customer_tagline || null

  // The three numbers, defaulted to the honest zero rather than to nothing:
  // a panel with a blank where the percentage goes reads as broken, and a
  // panel that hides itself at 0% hides the state a new customer is actually
  // in.
  const pct = Math.max(0, Math.min(100, Number((progress || {}).pct) || 0))
  const done = Number((progress || {}).sectionsComplete) || 0
  const total = Number((progress || {}).sectionsTotal) || 0

  const cls = 'lp-hero'
    + (heroImage ? ' lp-hero--media' : '')
    + (p.hero_overlay ? ' lp-ov-' + p.hero_overlay : ' lp-ov-deep')

  return (
    <section className={cls}>
      {heroImage ? (
        <div className="lp-hero-bg" aria-hidden="true">
          <img src={heroImage} alt="" />
          <span className="lp-hero-scrim" />
        </div>
      ) : null}

      <div className="lp-hero-in">
        <div className="lp-hero-top">
          <div className="lp-hero-id">
            <Mark src={heroLogo} label={customer.name} size="l" />
            <div className="lp-hero-words">
              <p className="lp-eyebrow">{p.eyebrow || 'Client Onboarding & Launch'}</p>
              <h1>{title}</h1>
              {/* THE PROVIDER, NAMED UNDER THE CUSTOMER. This is the whole
                  hierarchy of the page in one line: it is their portal, and
                  the brand delivers it. */}
              <p className="lp-h2">
                {p.subtitle || 'Your launch portal'}
                <span className="lp-poweredby"> · Delivered by {brand.name}</span>
              </p>
              {p.intro ? <p className="lp-hero-intro">{p.intro}</p> : (
                <p className="lp-hero-intro">
                  Everything you enter here tells {brand.name} what to build for
                  you — your workspace, your connected systems, the testing and
                  the training — and then we take you live. Work through it in
                  any order and save as you go; nothing is final until you
                  submit it.
                </p>
              )}
              {tagline && !heroImage
                ? <p className="lp-hero-quote">{tagline}</p> : null}
            </div>
          </div>

          {/* WHERE THE LAUNCH ACTUALLY STANDS. Configured words, composed
              numbers; see the header for why the two are kept apart. */}
          <aside className="lp-hero-stat">
            <p className="lp-hs-lab">Launch status</p>
            <span className="lp-chip">{st.label}</span>
            <div className="lp-hs-pctrow">
              <b className="lp-hs-pct">{pct}%</b>
              <span className="lp-hs-pcts">
                complete{total ? ` · ${done} of ${total} sections` : ''}
              </span>
            </div>
            <div className="lp-hs-bar"><i style={{ width: pct + '%' }} /></div>
            <p className="lp-hs-next">
              <b>What happens next</b>
              {st.customer}{' '}
              {golive
                ? `Your target launch date is ${golive}.`
                : `${brand.name} begins the build once your onboarding is `
                  + 'submitted and reviewed.'}
            </p>
          </aside>
        </div>

        {/* The customer's own mark over their own image, when they supplied
            one. Only with an image — floating a logo over flat navy looks
            like a placeholder, because it is one. */}
        {heroImage && heroLogo ? (
          <div className="lp-hero-art">
            <img src={heroLogo} alt={customer.name} />
            {tagline ? <p className="lp-hero-quote">{tagline}</p> : null}
          </div>
        ) : null}
      </div>
    </section>
  )
}
