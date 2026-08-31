/**
 * ENTER BRAND — the level between AdvisorFlow and a customer.
 *
 * Deliberately the same shape as enterCustomer: one implementation, one thing
 * to audit, and the server's membership assertion checked on every entry rather
 * than trusted. A brand context creates no membership either — entering a brand
 * is administrative visibility, not employment.
 */
import { api, setBrandContext, clearBrandContext } from '../../api/client'

export async function enterBrand(platformId, brandName) {
  if (!platformId) throw new Error('No brand selected.')

  const r = await api.post('/god/platform/context/brand/' + platformId, {})

  if (r && r.memberships_before !== r.memberships_after) {
    throw new Error(
      'Refusing to enter: the server reported a membership change ' +
      `(${r.memberships_before} → ${r.memberships_after}). Entering a brand ` +
      'must never grant one.'
    )
  }

  const name = (r && r.context && r.context.platform && r.context.platform.name)
               || brandName || platformId
  setBrandContext(platformId, name)
  return r ? r.context : null
}

/** Leave the brand. Local context clears either way — being stuck is worse. */
export async function exitBrand() {
  try { await api.post('/god/platform/context/exit', {}) } catch (_) { /* leaving regardless */ }
  clearBrandContext()
}
