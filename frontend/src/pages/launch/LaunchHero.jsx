/**
 * LaunchHero — the customer's welcome.
 *
 * This is the first thing Atlantis sees on their launch day. It names THEM,
 * not the software, and it says what the next half hour is for. The brand
 * appears as the party doing the work; AdvisorFlow does not appear at all.
 *
 * THE MARK IS A PLACEHOLDER AND LOOKS LIKE ONE. See LaunchUI.Mark — no
 * invented Atlantis logo is baked into this bundle. Set customer.logoUrl when
 * the real asset arrives; the layout does not change.
 */
import { Mark } from './LaunchUI'

function goLiveLabel(iso) {
  if (!iso) return null
  const d = new Date(iso + 'T00:00:00')
  if (Number.isNaN(d.getTime())) return null
  return d.toLocaleDateString('en-US',
    { month: 'long', day: 'numeric', year: 'numeric' })
}

export default function LaunchHero({ brand, customer }) {
  const golive = goLiveLabel(customer.targetGoLive)
  return (
    <section className="lp-hero">
      <div className="lp-hero-top">
        <div className="lp-hero-id">
          <Mark src={customer.logoUrl} label={customer.name} size="l" />
          <div>
            <p className="lp-eyebrow">Welcome to your launch pad</p>
            <h1>{customer.name}</h1>
            <p className="lp-h2">Client Onboarding &amp; Integration</p>
          </div>
        </div>

        <div className="lp-hero-side">
          <span className="lp-chip">Implementation in progress</span>
          {golive ? <span className="lp-chip blue">Target go-live · {golive}</span> : null}
          <span className="lp-poweredby">Delivered by {brand.name}</span>
        </div>
      </div>

      <p>
        This guided process collects everything {brand.name} needs to build,
        integrate, test and launch your system — your company details, brand
        assets, website and hosting access, your ComparePower relationship, the
        tools your team uses today, and the way you handle a customer once they
        reach you. Work through it in any order, save a draft whenever you like,
        and come back to finish. Nothing is submitted until you sign off at the
        end.
      </p>
    </section>
  )
}
