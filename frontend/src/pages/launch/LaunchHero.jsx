/**
 * LaunchHero — the customer's welcome.
 *
 * ===========================================================================
 * TWO THINGS THIS FIXES
 * ===========================================================================
 *
 * 1. THE COPY NAMED A THIRD PARTY. It read "…your ComparePower relationship…"
 *    in the customer-facing paragraph. ComparePower is one vertical's rate
 *    marketplace. A fibre reseller, a funeral home or a benefits agency
 *    onboarding through this same engine was welcomed by a sentence about a
 *    company they have never heard of — the same white-label failure as
 *    showing them another brand's name, just less obvious.
 *
 *    The paragraph now describes the SHAPE of what is asked for, and the
 *    specifics live in the steps, where the server's schema supplies them.
 *
 * 2. THE TARGET DATE NEVER RENDERED. It read `customer.targetGoLive`;
 *    GET /launch/me returns the date on `implementation.target_launch_date`.
 *    The chip was therefore always absent, silently — the same class of
 *    wrong-field-name bug as the zeros on the owner dashboard.
 *
 * The brand leads, because the customer bought from the brand. AdvisorFlow is
 * credited once, in the rail footer, and never here.
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

export default function LaunchHero({ brand, customer, implementation, state }) {
  const golive = dateLabel(implementation && implementation.target_launch_date)
  const st = intakeState(state)

  return (
    <section className="lp-hero">
      <div className="lp-hero-top">
        <div className="lp-hero-id">
          <Mark src={customer.logoUrl} label={customer.name} size="l" />
          <div>
            <p className="lp-eyebrow">Welcome to {brand.name}</p>
            <h1>{customer.name}</h1>
            <p className="lp-h2">Let’s get your business ready to launch</p>
          </div>
        </div>

        <div className="lp-hero-side">
          <span className="lp-chip">{st.label}</span>
          {golive ? <span className="lp-chip blue">Target launch · {golive}</span> : null}
          <span className="lp-poweredby">Delivered by {brand.name}</span>
        </div>
      </div>

      <p>
        {st.customer} This short guided process collects what {brand.name} needs
        to build, integrate, test and launch your system — your company details,
        your brand, where your website and domain live, where your enquiries come
        from, the tools your team uses today, and what happens after a customer
        reaches you. Work through it in any order, save whenever you like, and
        come back to finish. Nothing is sent until you sign off at the end.
      </p>
    </section>
  )
}
