/**
 * LaunchFooter — the line under the customer's launch page.
 *
 * Small, and it closes a real gap: a premium onboarding experience that simply
 * stops at the bottom of a form reads as an internal tool. The customer is
 * being asked for their company details, their website credentials and their
 * documents; a privacy link and a terms link are the least the page owes them,
 * and they belong where people look for them.
 *
 * THE LINKS ARE CONFIGURATION. A brand with its own privacy policy points at
 * it; a brand that has not set one gets the platform's routes. A link whose
 * href resolves to nothing is rendered as plain text rather than as an anchor
 * that goes nowhere — a dead link in a footer is how a customer decides the
 * company is not real.
 *
 * ===========================================================================
 * V2: THE THIRD AND LAST PLACE HELP APPEARS
 * ===========================================================================
 *
 * Support lives in exactly three places on this surface — the rail foot, the
 * progress rail, and here — and nowhere else. The direction was specific that
 * help must be easy to find without giant help cards duplicated across the
 * screen, and three quiet touchpoints at the three places a person stops
 * scrolling is what that looks like.
 *
 * The response line comes from the resolved configuration (see present.js).
 * There is no timeframe written in this file, and there must never be: a
 * promise about how fast somebody replies is an entitlement a brand sells, not
 * copy a component invents.
 *
 * THE PLATFORM IS NOT NAMED HERE. The customer's portal is delivered by the
 * brand; the infrastructure underneath is the brand's business relationship,
 * not the customer's, and exposing it in the customer's own footer is the
 * white-label leak this surface exists to avoid.
 */
import { supportLine } from './present'

export default function LaunchFooter({ brand, customer = null,
                                       presentation = {} }) {
  const p = presentation || {}
  const footer = p.footer || {}
  const links = Array.isArray(footer.links) ? footer.links : []
  const copyright = footer.copyright
    || ('© ' + new Date().getFullYear() + ' ' + brand.name + '. All rights reserved.')

  return (
    <footer className="lp-foot">
      <div className="lp-foot-help">
        <b>Need a hand with your launch?</b>
        <span>{supportLine(p, brand.name)}</span>
      </div>

      <nav className="lp-foot-links">
        {links.map((link, i) => (
          link && link.href
            ? <a key={i} href={link.href}>{link.label}</a>
            : <span key={i}>{(link && link.label) || ''}</span>
        ))}
      </nav>

      <p className="lp-foot-note">
        {customer && customer.name
          ? <>{customer.name} launch portal<br /></> : null}
        Delivered by <b>{brand.name}</b>
        <br />
        {copyright}
      </p>
    </footer>
  )
}
