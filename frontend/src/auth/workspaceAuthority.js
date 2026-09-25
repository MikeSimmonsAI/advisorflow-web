/**
 * WHAT THIS WORKSPACE MAY DO — ONE ANSWER, FOR EVERY SURFACE THAT ASKS.
 *
 * ===========================================================================
 * THE DEFECT THIS EXISTS TO CLOSE
 * ===========================================================================
 *
 * Three screens derived the same authorization independently:
 *
 *   Layout.jsx    branding?.enabled_features ?? null  →  sidebar items
 *   App.jsx       branding?.enabled_features ?? null  →  route guards
 *   Overview.jsx  branding?.enabled_features ?? null  →  ONE button
 *
 * The first two agreed. The third applied the test to `campaigns` and to
 * nothing else, and used `user.role` — the global row value — where the other
 * two had already moved to `branding.workspace_role`. So an advisor whose
 * workspace has `leads` switched off got a sidebar with no Leads entry and a
 * dashboard that still offered Search leads, Import leads, four lead KPI
 * cards, Lead flow and Leads needing action, then fired six lead-only requests
 * that were refused and printed the refusals on the screen.
 *
 * A screen that computes its own version of an authorization rule will
 * eventually compute a different answer from the route it is guarding. The fix
 * is not to repeat the check in a fourth place; it is that there is one place.
 *
 * ===========================================================================
 * WHERE THE ANSWER COMES FROM
 * ===========================================================================
 *
 * The server. `GET /branding/org` resolves the ACTIVE WORKSPACE and returns
 * that workspace's `enabled_features` and the caller's `workspace_role` in it;
 * `GET /settings/my-capabilities` returns what they may administer, from the
 * same `capabilities.resolve` the routes call. Nothing here decides anything —
 * it reads what the server decided and hands the same object to every caller.
 *
 * NONE OF THIS IS ACCESS CONTROL. Every module and capability is enforced on
 * its own routes and refuses independently. This exists so the app stops
 * offering doors that open onto a refusal.
 */
import { useEffect, useState } from 'react'
import { api, getBranding, getCurrentUser, getOrgContext } from '../api/client'
// THE RULES THEMSELVES LIVE IN A FILE THAT IMPORTS NOTHING, so the sidebar,
// the dashboard, the route guard and a plain `node` test all apply the same
// two expressions rather than four paraphrases of them. See workspaceRules.js
// for the three that had already drifted.
import {
  featureEnabled, featuresOf, isManagerRole, isOperator, operatorRouteExempt,
  roleOf, routeFeatureDenied, workspaceFeatures,
  WHOLESALE_FEATURE, productOffered, canEnterProduct,
} from './workspaceRules'

// Re-exported so every existing import site keeps working and there is still
// only one module a surface needs to know about.
export {
  featureEnabled, featuresOf, isManagerRole, isOperator, operatorRouteExempt,
  roleOf, routeFeatureDenied, workspaceFeatures,
  WHOLESALE_FEATURE, productOffered, canEnterProduct,
}

/* ── the whole answer ─────────────────────────────────────────────────────── */

export function readAuthority({ capabilities = null } = {}) {
  const user = getCurrentUser()
  const branding = getBranding()
  const role = roleOf(branding, user)

  // ONE RULE, NOT THIS FILE'S OWN VERSION OF IT. An operator standing OUTSIDE
  // a customer is not subject to a customer's allow-list; inside one they are,
  // for rendering, because customer-view exists to show what the customer
  // sees. Direct routes still let god through — `operatorRouteExempt` — which
  // is what keeps God Mode able to reach a module that is switched off.
  const features = workspaceFeatures(branding, user, getOrgContext())

  return {
    user,
    branding,
    role,
    organizationId: branding?.organization_id ?? user?.organization_id ?? null,
    isManager: isManagerRole(role),
    features,
    // `capabilities === null` means NOT ANSWERED YET, and renders as no
    // capability rather than as all of them. A nav item that appears for a
    // moment before the answer arrives has already told somebody the door
    // exists.
    capabilities,
    isFeatureEnabled: (key) => !key || features === null || features.includes(key),
    hasCapability: (key) => !key || (capabilities !== null && capabilities.includes(key)),
  }
}

/**
 * React binding. Renders immediately from what is cached, then again when the
 * capability answer arrives.
 *
 * `orgContextKey` is in the dependency array because switching customer does
 * not remount the components that call this — React reconciles the same
 * element in the same position — and a stale capability list after a switch is
 * the previous customer's answer rendered as this one's.
 */
export function useWorkspaceAuthority(opts = {}) {
  const [capabilities, setCapabilities] = useState(null)
  const orgContextKey = getOrgContext()?.orgId || ''

  useEffect(() => {
    let cancelled = false
    api.get('/settings/my-capabilities')
      .then(d => {
        if (!cancelled) setCapabilities(Array.isArray(d?.capabilities) ? d.capabilities : [])
      })
      // A failed call means "not entitled" for rendering purposes. It cannot
      // grant anything, and the routes refuse independently either way.
      .catch(() => { if (!cancelled) setCapabilities([]) })
    return () => { cancelled = true }
  }, [orgContextKey])

  return readAuthority({ ...opts, capabilities })
}
