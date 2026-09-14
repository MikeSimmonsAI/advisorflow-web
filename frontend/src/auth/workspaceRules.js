/**
 * THE ENTITLEMENT RULES THE SHELL RENDERS FROM — pure, and deliberately
 * dependency-free.
 *
 * ===========================================================================
 * THE DEFECT THIS EXISTS TO CLOSE
 * ===========================================================================
 *
 * `workspaceAuthority.js` made three surfaces read ONE features value. It did
 * not make them apply ONE RULE to it, and by the time an operator stood inside
 * a customer there were three:
 *
 *   Layout.jsx     isElevated ? null : featuresOf(branding)
 *                  → an operator saw EVERY module of EVERY customer, always.
 *   App.jsx        featuresOf(branding), then `role !== 'god_admin'`
 *                  → routes exempted god but not super_admin.
 *   readAuthority  elevatedSeesAll && elevated && !orgContext
 *                  → the dashboard said customer-view shows the customer's
 *                    modules, which is the opposite of what the sidebar did.
 *
 * So the Atlantis customer-view — an organization with NO modules enabled —
 * drew a full sidebar, a full dashboard, and fired six lead-only requests,
 * while the dashboard's own documentation said it would not. Three surfaces
 * reading the same number and disagreeing about what it means is the same
 * defect as three surfaces reading three numbers.
 *
 * ===========================================================================
 * THE RULES, AND THERE ARE ONLY TWO
 * ===========================================================================
 *
 * 1. WHAT THE SHELL RENDERS  — `workspaceFeatures`. An operator standing
 *    OUTSIDE any customer is not subject to any customer's allow-list, so the
 *    answer is null (open). Standing INSIDE one, the answer is that customer's
 *    list, because customer-view exists to show what the customer sees. An
 *    operator who cannot see that a module is switched off cannot diagnose the
 *    screen the customer is complaining about.
 *
 * 2. WHAT A DIRECT ROUTE REFUSES — `operatorRouteExempt`. The owner is never
 *    given a dead end. Rendering is honest; access is not removed. God Mode
 *    keeps every door it had, including the Features screen that switches a
 *    module back on — which is the same exemption `entitlements.require_feature`
 *    makes on the server, so the browser and the API agree about god too.
 *
 * This file imports nothing. That is not tidiness: it is what lets
 * `frontend/tests/workspaceRules.test.mjs` run the whole matrix under plain
 * `node`, with no bundler, no DOM and no test framework to install.
 */

/* ── features ─────────────────────────────────────────────────────────────── */

/**
 * NULL MEANS EVERYTHING, AND THAT IS NOT A DEFAULT — IT IS THE MIGRATION.
 *
 * An organization that predates entitlements has `enabled_features = NULL` and
 * is deliberately open. One configured down to nothing has `[]`. Collapsing
 * the two is how the sidebar came to render every module for every customer,
 * so the distinction is preserved at every hop: the column, the API payload,
 * `client.js` (which uses `??`, never `||`, because `[]` is falsy), and here.
 */
export function featuresOf(branding) {
  const f = branding?.enabled_features
  return f === undefined ? null : f
}

export function featureEnabled(branding, key) {
  if (!key) return true
  const f = featuresOf(branding)
  return f === null || f.includes(key)
}

/* ── who is an operator ───────────────────────────────────────────────────── */

/** Platform-level identities. `super_admin` is included deliberately: it was
 *  elevated for the sidebar and not for the routes, which is one of the three
 *  disagreements above. */
export function isOperator(user) {
  const r = user?.role
  return r === 'god_admin' || r === 'super_admin'
}

/* ── rule 1: what the shell renders ───────────────────────────────────────── */

/**
 * The allow-list this shell renders from — for EVERY surface.
 *
 * @param branding    the cached `/branding/org` payload for the ACTIVE workspace
 * @param user        the signed-in identity
 * @param orgContext  the customer an operator has entered, or null
 */
export function workspaceFeatures(branding, user, orgContext) {
  if (isOperator(user) && !orgContext) return null
  return featuresOf(branding)
}

/* ── rule 2: what a direct route refuses ──────────────────────────────────── */

/** The owner is never routed into a dead end. Mirrors `require_feature`, which
 *  returns early for god_admin on the server. */
export function operatorRouteExempt(user) {
  return user?.role === 'god_admin'
}

/** Should `<ProtectedRoute feature={k}>` refuse? The single expression, so a
 *  route cannot decide a module is available while the nav hides it. */
export function routeFeatureDenied(feature, branding, user, orgContext) {
  if (!feature) return false
  if (operatorRouteExempt(user)) return false
  const enabled = workspaceFeatures(branding, user, orgContext)
  return enabled !== null && !enabled.includes(feature)
}

/* ── role ─────────────────────────────────────────────────────────────────── */

/**
 * THE ROLE IN THIS WORKSPACE, never `users.role`.
 *
 * `users.role` is one value for a whole human and cannot be true of two
 * customers at once. The server resolves the membership role for the selected
 * workspace and sends it as `workspace_role`; the fallback to `user.role`
 * covers only the moment before branding has loaded, and a single-workspace
 * customer where the two agree by construction.
 */
export function roleOf(branding, user) {
  return branding?.workspace_role || user?.role || null
}

export function isManagerRole(role) {
  return role === 'org_admin' || role === 'super_admin' || role === 'god_admin'
}
