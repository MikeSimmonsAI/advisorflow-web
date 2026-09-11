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
                                     presentation = {} }) {
  const golive = dateLabel(implementation && implementation.target_launch_date)
  const st = intakeState(state)

  const p = presentation || {}
  const title = p.title || customer.name
  const heroImage = p.hero_image_url || null
  const heroLogo = p.hero_logo_url || customer.logoUrl || null
  const tagline = p.customer_tagline || null

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
            {heroImage ? null : <Mark src={heroLogo} label={customer.name} size="l" />}
            <div className="lp-hero-words">
              <p className="lp-eyebrow">
                {p.eyebrow || ('Welcome to ' + brand.name)}
              </p>
              <h1>{title}</h1>
              {p.subtitle ? <p className="lp-h2">{p.subtitle}</p> : null}
              {p.intro ? <p className="lp-hero-intro">{p.intro}</p> : null}
            </div>
          </div>

          {/* The customer's own mark, at hero scale, over their own image.
              Only when there is an image to put it on — floating a logo over
              flat navy looks like a placeholder, because it is one. */}
          {heroImage && heroLogo ? (
            <div className="lp-hero-art">
              <img src={heroLogo} alt={customer.name} />
              {tagline ? <p className="lp-hero-quote">{tagline}</p> : null}
            </div>
          ) : null}
        </div>

        <div className="lp-hero-chips">
          <span className="lp-chip">{st.label}</span>
          {golive ? <span className="lp-chip blue">Target launch · {golive}</span> : null}
          <span className="lp-poweredby">Delivered by {brand.name}</span>
        </div>

        {!p.intro ? (
          <p className="lp-hero-intro">
            {st.customer} This short guided process collects what {brand.name}
            {' '}needs to build, integrate, test and launch your system. Work
            through it in any order, save whenever you like, and come back to
            finish. Nothing is sent until you sign off at the end.
          </p>
        ) : null}
      </div>
    </section>
  )
}
