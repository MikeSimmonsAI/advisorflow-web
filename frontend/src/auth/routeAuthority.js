/**
 * ROUTE AUTHORITY — which operating level does this screen belong to?
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE DEFECT THIS EXISTS TO CLOSE
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * `X-Org-Override` was sent on EVERY request unless a call site opted out with
 * `noOrgContext: true`. On the server, `deps.get_current_user` responds to that
 * header by doing:
 *
 *     user.organization_id = org_override
 *
 * for a god_admin. Roughly 179 routers read `current_user.organization_id` at
 * face value. So once the owner entered Restland, EVERY later request —
 * including ones to platform tools — arrived claiming to be Restland, and the
 * `_god_all_orgs` flag that means "no customer selected, show the estate" was
 * silently off.
 *
 * That is not only a confusing banner. A platform acquisition tool whose
 * destination defaults to `current_user.organization_id` would import into
 * whichever customer the owner last looked at.
 *
 * ═══════════════════════════════════════════════════════════════════════════
 * THE INVARIANT
 * ═══════════════════════════════════════════════════════════════════════════
 *
 *   CUSTOMER WORKSPACE CONTEXT APPLIES ONLY TO CUSTOMER WORKSPACE OPERATIONS.
 *
 * A platform operation is never silently scoped by a stale customer selection.
 * Where a platform tool legitimately acts ON a customer — the scraper's import
 * destination, Customer 360 — it takes that organization as an EXPLICIT,
 * VISIBLE parameter, which is a different thing from inheriting it.
 *
 * Mike's option B, chosen deliberately over A: the selection is REMEMBERED
 * (so returning to the customer app does not mean re-picking them) but it is
 * NOT SENT to platform APIs and NOT DISPLAYED as though a platform page were
 * customer-scoped. Clearing the selection on every platform navigation would
 * make the common round trip — check a customer, glance at billing, go back —
 * needlessly hostile.
 */

export const PLATFORM = 'platform'
export const BRAND_SALES = 'brand_sales'
export const EXECUTIVE = 'executive'
export const CUSTOMER = 'customer'

/**
 * Paths that are PLATFORM surfaces even though they sit outside /god.
 *
 * These are the leaks. Each is `requireGodAdmin` and renders real platform
 * data, but lives at a bare path and — before this file — was served inside the
 * tenant Layout, so it inherited the customer banner and the customer breadcrumb.
 * `/scraper` is the one Mike caught: a back-office acquisition tool presenting
 * itself as "AdvisorFlow → EvoSys Pro → Restland Cemetery and Funeral Home".
 */
const PLATFORM_PATHS = [
  '/scraper',
  '/god/lead-scraper',
  '/provision-client',
  '/orgs',
]

/**
 * The one /god path that IS customer space.
 *
 * Entering a customer renders the tenant application here, so this must keep
 * sending the override — it is the whole point of the screen. Listed as an
 * exception rather than pattern-matched, because getting it wrong in either
 * direction is silent: too broad and platform tools leak again, too narrow and
 * the customer app renders unscoped and empty.
 */
const CUSTOMER_INSIDE_GOD = ['/god/customer-app']

/** Brand-sales back office. Scoped by BRAND, never by a customer. */
const BRAND_SALES_PREFIXES = ['/sales']

/**
 * THE EXECUTIVE LAYER — scoped by a PORTFOLIO, and by nothing else.
 *
 * An executive oversees one brand's customers. Their authority comes from a
 * membership grant the server checks on every call, so a customer selection
 * has no business travelling with these requests:
 *
 *   - Sending `X-Org-Override` here would either be ignored (it is — the
 *     executive endpoints never read `current_user.organization_id`) or, if a
 *     future route ever did read it, would silently narrow a portfolio to
 *     whichever customer was last opened. An executive looking at a portfolio
 *     of one cannot tell that from a portfolio that really has one.
 *
 *   - Showing the "VIEWING AS <customer>" banner over an executive screen
 *     would be a false statement about what the page is doing. The executive
 *     drill-down states its own context, from the organization in its URL.
 *
 * Executive routes are their own class rather than folded into PLATFORM,
 * because they are not platform-wide: an executive sees one brand, and the
 * distinction matters for what a banner is allowed to claim.
 */
const EXECUTIVE_PREFIXES = ['/executive']

export function classifyRoute(pathname) {
  const path = (pathname || '/').split('?')[0]

  if (CUSTOMER_INSIDE_GOD.some(p => path === p || path.startsWith(p + '/'))) {
    return CUSTOMER
  }
  if (path === '/god' || path.startsWith('/god/')) return PLATFORM
  if (PLATFORM_PATHS.some(p => path === p || path.startsWith(p + '/'))) {
    return PLATFORM
  }
  if (EXECUTIVE_PREFIXES.some(p => path === p || path.startsWith(p + '/'))) {
    return EXECUTIVE
  }
  if (BRAND_SALES_PREFIXES.some(p => path === p || path.startsWith(p + '/'))) {
    return BRAND_SALES
  }
  // Everything else is the tenant application: Overview, Leads, Replies,
  // Campaigns, Billing, Settings and the rest. Defaulting to CUSTOMER is the
  // safe direction — a customer screen that lost its scope would show another
  // tenant's data, which is far worse than a platform screen that kept one.
  return CUSTOMER
}

/**
 * May this request carry the selected customer's `X-Org-Override`?
 *
 * ONLY on customer-space routes. This is the single line that closes the leak:
 * the header is what turns the owner into that customer on the server, so not
 * sending it is what keeps a platform tool platform-wide.
 */
export function shouldSendOrgOverride(pathname) {
  return classifyRoute(pathname) === CUSTOMER
}

/**
 * Should the "VIEWING AS <customer>" banner appear here?
 *
 * Same rule, and deliberately the same function's answer: the banner must mean
 * "this screen is operating as that customer". A banner shown over a platform
 * tool is not decoration — it is a false statement about what the page will do.
 */
export function shouldShowCustomerBanner(pathname) {
  return shouldSendOrgOverride(pathname)
}

/** What to tell the user about where they are. */
export function contextLabel(pathname, { brandName, orgName } = {}) {
  const kind = classifyRoute(pathname)
  if (kind === PLATFORM) {
    return { mode: 'PLATFORM', detail: 'AdvisorFlow Platform', kind }
  }
  if (kind === BRAND_SALES) {
    return { mode: 'BRAND SALES', detail: brandName ? brandName + ' Sales' : 'Brand Sales', kind }
  }
  if (kind === EXECUTIVE) {
    // The executive shell draws its own brand header, so this label exists for
    // anything outside it that asks. It names the PORTFOLIO, never a customer.
    return { mode: 'EXECUTIVE', detail: brandName ? brandName + ' Portfolio'
      : 'Executive Portfolio', kind }
  }
  return {
    mode: 'CUSTOMER WORKSPACE',
    detail: [brandName, orgName].filter(Boolean).join(' → ') || 'Customer Workspace',
    kind,
  }
}
