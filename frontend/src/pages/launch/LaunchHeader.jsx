/**
 * LaunchHeader — the top bar.
 *
 * ===========================================================================
 * WHAT V2 TOOK OUT, AND WHY
 * ===========================================================================
 *
 * The bar used to open with an "Ecosystem" plate naming the PLATFORM. On a
 * white-label customer portal that is the one name that should not be there:
 * the customer bought an implementation from a brand, the platform underneath
 * is infrastructure, and putting its name in the first element of the first
 * row tells them they are a tenant of somebody they have never met. It is
 * replaced by a breadcrumb — where in their own onboarding they are — which is
 * the question the top-left of a portal is actually for.
 *
 * What took its place on the right is the save state. A twenty-field form
 * whose only feedback lives at the bottom of the page is a form people scroll
 * down to check; saying it up here, permanently, is what makes "can I close
 * this?" answerable without scrolling.
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
import { Ico } from './LaunchUI'

export default function LaunchHeader({ brand, customer, preview = false,
                                       section = null, saveState = null }) {
  const person = (customer && customer.user) || null
  return (
    <header className="lp-top">
      <div className="lp-crumb">
        <b>Onboarding</b>
        {section ? (
          <>
            <span className="lp-crumbsep" aria-hidden="true">
              <Ico name="caret" size={13} />
            </span>
            <span className="lp-crumbnow">{section}</span>
          </>
        ) : null}
      </div>

      <div className="lp-search">
        <span className="lp-si"><Ico name="search" size={15} /></span>
        <input type="search" placeholder="Search your onboarding…"
          aria-label="Search your onboarding" />
      </div>

      <div className="lp-topspace" />

      {/* The save state, stated where it can be read without scrolling. It
          says what is true and nothing more — "No changes yet" is not a
          promise that anything was written. */}
      {saveState ? (
        <span className="lp-savestate">
          <Ico name="info" size={13} />
          {saveState}
        </span>
      ) : null}

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
