/**
 * LaunchHeader — the top bar.
 *
 * The ecosystem selector on the left is where a customer who belongs to more
 * than one brand relationship would switch between them. In Stage 1 it names
 * the one ecosystem this customer is in and is not a menu — an affordance that
 * opens an empty list is worse than one that clearly has nothing to open yet.
 *
 * Search and notifications are prototype furniture: present so the finished
 * shape can be judged, inert because there is nothing to search or notify.
 *
 * ===========================================================================
 * THERE IS NOT ALWAYS A PERSON
 * ===========================================================================
 *
 * This read `customer.user.name` unconditionally, and `customer.user` is NULL
 * on every internal preview — deliberately so, because the preview must not
 * paint the OPERATOR's name and initials into the customer's own avatar. The
 * result was a TypeError during render, which unmounted the entire React tree
 * and left the page completely blank: no header, no banner, no error, just the
 * background colour. A real customer's onboarding could not be previewed at
 * all, and the failure said nothing about itself.
 *
 * So the person block renders only when there IS a person. When there is not,
 * the bar names the ORGANIZATION and says plainly that nobody is signed in,
 * rather than inventing a plausible-looking name to fill the space — a fake
 * identity in the customer's own header is exactly what the preview exists to
 * avoid.
 */
import { Mark, Ico } from './LaunchUI'

export default function LaunchHeader({ brand, customer, preview = false }) {
  const person = (customer && customer.user) || null
  return (
    <header className="lp-top">
      <button type="button" className="lp-eco" disabled>
        <Mark src={brand.logoUrl} label={brand.name} size="s" />
        <span>
          <span className="lp-ecolabel">Ecosystem</span>
          <b>{brand.ecosystem}</b>
        </span>
      </button>

      <div className="lp-search">
        <span className="lp-si"><Ico name="search" size={15} /></span>
        <input type="search" placeholder="Search your workspace…"
          aria-label="Search your workspace" />
      </div>

      <div className="lp-topspace" />

      <button type="button" className="lp-iconbtn" aria-label="Notifications">
        <Ico name="bell" size={16} />
        <span className="lp-dot" />
      </button>

      <div className="lp-user">
        <div className="lp-un">
          <b>{person ? person.name : (customer ? customer.name : '')}</b>
          <span>
            {person ? customer.name
              : preview ? 'Nobody is signed in — preview'
                : 'Not signed in'}
          </span>
        </div>
        {/* The customer's own mark when there is no person, never an invented
            set of initials for somebody who is not here. */}
        <div className="lp-avatar" aria-hidden="true">
          {person ? person.initials : ((customer && customer.short) || '—')}
        </div>
      </div>
    </header>
  )
}
