/**
 * WHERE "LEAVE ONBOARDING" GOES, AND WHY IT IS NOT ONE PLACE.
 *
 * THE DEFECT. Launch had no exit at all. The header carries a breadcrumb, a
 * search box, a save state and the person — and nothing that leaves. Once
 * inside, the browser's Back button was the only way out, which is not a
 * navigation affordance: it is the absence of one. An operator who entered a
 * customer's onboarding from God Mode had no way back to God Mode, and a
 * customer who opened Launch from their own sidebar had no way back to their
 * workspace.
 *
 * WHY A SINGLE DESTINATION WOULD BE WRONG. The same screen is reached by three
 * different people standing in three different places:
 *
 *   a customer                 → their own workspace. Sending them to a
 *                                back-office screen would show them a product
 *                                they did not buy.
 *   an operator in customer-view → God Mode. Sending them to "/" would drop
 *                                them into the customer's app still wearing
 *                                the customer's context, which is precisely
 *                                the confusion the context banner exists to
 *                                prevent.
 *   brand back-office           → the brand's own launches list, which is
 *                                where they were working.
 *
 * So the destination is RESOLVED FROM WHERE THEY CAME FROM, and every branch
 * names itself in the button. A button that says "Back" and does something
 * unexpected is worse than no button.
 *
 * Nothing here grants or checks access — every destination is a route that
 * enforces its own guard. This decides only what to offer.
 */
import { getCurrentUser, getObservationContext, getOrgContext } from '../../api/client'

export function resolveLaunchExit() {
  let user = null
  let observing = null
  let orgCtx = null
  try {
    user = getCurrentUser()
    observing = getObservationContext()
    orgCtx = getOrgContext()
  } catch (e) {
    // localStorage can be unavailable; an exit that cannot resolve its
    // context still has to exist, so fall through to the customer default.
  }

  const role = user?.role

  // THE OWNER, STANDING INSIDE A CUSTOMER. The context banner is already
  // telling them whose workspace this is; the exit has to agree with it.
  if (role === 'god_admin') {
    return { to: '/god', label: 'Return to God Mode' }
  }

  // AN EXECUTIVE OBSERVING. Read-only entry from the portfolio, so the way
  // back is the portfolio rather than a customer screen they do not operate.
  if (observing) {
    return { to: '/executive', label: 'Return to Executive Suite' }
  }

  // BRAND BACK OFFICE. A sales or brand operator reached this from the
  // launches board they work out of.
  if (role === 'super_admin' || role === 'sales_manager' || role === 'sales_rep'
      || role === 'brand_executive') {
    return { to: '/god/launches', label: 'Return to Launches' }
  }

  // THE CUSTOMER'S OWN WORKSPACE — the default, and the common case. Named
  // with their organization where it is known, so the button says where it
  // actually goes.
  const name = orgCtx?.name
  return { to: '/', label: name ? `Back to ${name}` : 'Back to my workspace' }
}
