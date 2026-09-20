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
import { api, setOrgContext, setBrandContext, clearOrgContext, clearBranding,
         fetchAndStoreBranding } from '../../api/client'
// THE SECOND CACHE THAT DESCRIBES THIS WORKSPACE. `af_branding` and
// `af_terminology` answer the same question — which customer is this — and
// only the first was ever dropped on a switch. See the call sites below.
import { clearTerminology } from '../../terminology'

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

  // The cached branding belongs to whoever was entered LAST, and it carries
  // `enabled_features` — an allow-list keyed to one organization. Keeping it
  // would render the previous customer's modules under this customer's banner.
  clearBranding()
  // AND THE VOCABULARY WITH IT, for the same reason and because of the same
  // bug found twice. `clearTerminology` existed and was called from nowhere,
  // so the words AND the company name from the customer entered last survived
  // the switch — and the terminology cache key was the `X-Workspace-Id`
  // header, which is null for an operator who entered through God Mode, so
  // every customer entered this way shared one key. The result was a client
  // dashboard headed with another customer's company name.
  clearTerminology()

  // AND THE NEW ONE IS FETCHED BEFORE THE REDIRECT, not after the first paint.
  //
  // `Login.jsx` already awaits this for exactly the same reason: an absent
  // cache reads as legacy-open, so a dashboard that paints before the answer
  // arrives renders every module and fires every module's request, then
  // retracts them a second later. A customer logging in never sees that
  // because login waits. An operator switching customer did, twice per
  // switch. Failure is not fatal — it leaves the cache absent, which is
  // exactly where this line started.

  // Establish brand context from the server's resolved platform so that
  // X-Brand-Override is always current, even when switching between customers
  // that belong to different brands. Never hardcoded — always from the server.
  // BEFORE the branding fetch below, so that call already carries it.
  if (r && r.context && r.context.platform) {
    setBrandContext(r.context.platform.id, r.context.platform.name)
  }

  try { await fetchAndStoreBranding({ applyTheme: false }) } catch (_) { /* open */ }

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
  // Same reason as entering: the allow-list left behind belongs to the
  // customer just left, and nothing outside a customer should render from it.
  clearBranding()
  clearTerminology()
}
