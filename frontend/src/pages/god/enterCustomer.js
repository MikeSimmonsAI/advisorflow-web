/**
 * ENTER ORGANIZATION — the single implementation.
 *
 * WHY THIS FILE EXISTS. There were two ways into a tenant and only one of them
 * worked. `CustomerDetail` called POST /god/platform/context/customer/{id} and
 * then setOrgContext(), which is what actually puts `X-Org-Override` on every
 * subsequent request. The Command Center and the Organizations table called the
 * older POST /god/orgs/{id}/impersonate, which validates the org and writes a
 * log line but sets NO client context — so "Enter" from those two screens landed
 * the owner on the tenant app still holding no organization at all, seeing
 * empty lists, with a banner that came from React state rather than the server.
 *
 * A second path into a tenant is a second path to audit and to secure. So there
 * is one now, and it is this function.
 *
 * WHAT IT DOES NOT DO. It creates no membership. The server proves that: the
 * enter endpoint returns `memberships_before` and `memberships_after` and this
 * function refuses to continue if they differ, so the centralized-identity rule
 * is checked on every single entry rather than trusted.
 */
import { api, setOrgContext, clearOrgContext } from '../../api/client'

/**
 * Enter a customer organization's context.
 *
 * @param {string} orgId
 * @param {string} orgName  used for the local label only; the BANNER text is
 *                          always the server's, read back by ContextBanner.
 * @returns {Promise<object>} the server's resolved context
 * @throws  {Error} with a readable message when the server refuses
 */
export async function enterCustomer(orgId, orgName) {
  if (!orgId) throw new Error('No organization selected.')

  const r = await api.post('/god/platform/context/customer/' + orgId, {})

  // The load-bearing rule of the whole design, asserted rather than assumed.
  if (r && r.memberships_before !== r.memberships_after) {
    throw new Error(
      'Refusing to enter: the server reported a membership change ' +
      `(${r.memberships_before} → ${r.memberships_after}). Entering an ` +
      'organization must never grant one.'
    )
  }

  const name = (r && r.context && r.context.customer && r.context.customer.name) || orgName || orgId
  setOrgContext(orgId, name)
  return r ? r.context : null
}

/**
 * Leave the current customer context. Audited server-side; the local context is
 * cleared either way, because being stuck inside a tenant you cannot leave is
 * worse than an unrecorded exit.
 */
export async function exitCustomer() {
  try { await api.post('/god/platform/context/exit', {}) } catch (_) { /* leaving regardless */ }
  clearOrgContext()
}
