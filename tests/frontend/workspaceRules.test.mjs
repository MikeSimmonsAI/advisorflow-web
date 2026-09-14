/**
 * THE FOUR SURFACES MUST AGREE — asserted, not asserted-in-a-comment.
 *
 * Sidebar navigation, dashboard buttons, dashboard widgets and direct routes
 * all render from `workspaceFeatures` and refuse from `routeFeatureDenied`.
 * This file evaluates that matrix directly. It runs under plain `node` with no
 * bundler, no DOM and no test framework, which is the whole reason
 * `src/auth/workspaceRules.js` imports nothing:
 *
 *     node tests/frontend/workspaceRules.test.mjs
 *
 * The cases are the ones that were actually wrong in production, named after
 * the organizations that found them.
 */
import {
  featureEnabled, featuresOf, isManagerRole, isOperator, operatorRouteExempt,
  roleOf, routeFeatureDenied, workspaceFeatures,
} from '../../frontend/src/auth/workspaceRules.js'

let passed = 0
const failures = []

function check(name, fn) {
  try { fn(); passed += 1 }
  catch (e) { failures.push(name + '\n    ' + e.message) }
}

function eq(actual, expected, what) {
  const a = JSON.stringify(actual), b = JSON.stringify(expected)
  if (a !== b) throw new Error((what || 'value') + ': expected ' + b + ', got ' + a)
}

/* ── fixtures ─────────────────────────────────────────────────────────────── */

const ADVISOR  = { role: 'advisor' }
const ORGADMIN = { role: 'org_admin' }
const SUPER    = { role: 'super_admin' }
const GOD      = { role: 'god_admin' }

// WUPA after the correction: a lead platform that now actually has `leads`.
const WUPA = { enabled_features: ['leads', 'replies', 'campaigns', 'imports'],
               workspace_role: 'advisor', organization_id: 'org-wupa' }
// Atlantis Light & Power: configured down to nothing. NOT the same as legacy.
const ATLANTIS = { enabled_features: [], workspace_role: 'advisor',
                   organization_id: 'org-atlantis' }
// An organization that predates entitlements at all.
const LEGACY = { enabled_features: null, workspace_role: 'org_admin',
                 organization_id: 'org-legacy' }
// The payload shape before `enabled_features` was added to it.
const PRE_MIGRATION = { workspace_role: 'advisor', organization_id: 'org-old' }

const IN_ATLANTIS = { orgId: 'org-atlantis', orgName: 'Atlantis Light & Power' }
const NO_CONTEXT = null

/* ── null is not [] ───────────────────────────────────────────────────────── */

check('an absent enabled_features is legacy-open, never "no modules"', () => {
  eq(featuresOf(PRE_MIGRATION), null)
  eq(featuresOf(LEGACY), null)
})

check('an empty list means no modules, and is not collapsed into open', () => {
  eq(featuresOf(ATLANTIS), [])
  eq(featureEnabled(ATLANTIS, 'leads'), false)
})

check('legacy-open enables everything asked of it', () => {
  eq(featureEnabled(LEGACY, 'leads'), true)
  eq(featureEnabled(LEGACY, 'anything_at_all'), true)
})

check('no key asked means nothing to refuse', () => {
  eq(featureEnabled(ATLANTIS, null), true)
  eq(featureEnabled(ATLANTIS, undefined), true)
})

/* ── rule 1: what the shell renders ───────────────────────────────────────── */

check('a customer renders from their own allow-list', () => {
  eq(workspaceFeatures(WUPA, ADVISOR, NO_CONTEXT), WUPA.enabled_features)
  eq(workspaceFeatures(ATLANTIS, ADVISOR, NO_CONTEXT), [])
})

check('an org_admin is a customer too and gets no wider answer', () => {
  eq(workspaceFeatures(ATLANTIS, ORGADMIN, NO_CONTEXT), [])
})

check('an operator OUTSIDE any customer is subject to no allow-list', () => {
  eq(workspaceFeatures(ATLANTIS, GOD, NO_CONTEXT), null)
  eq(workspaceFeatures(ATLANTIS, SUPER, NO_CONTEXT), null)
})

check('an operator INSIDE a customer sees what that customer sees', () => {
  // THE REGRESSION THIS FILE EXISTS FOR. Layout.jsx returned null here, so
  // customer-view for a zero-module organization drew the whole product.
  eq(workspaceFeatures(ATLANTIS, GOD, IN_ATLANTIS), [])
  eq(workspaceFeatures(ATLANTIS, SUPER, IN_ATLANTIS), [])
})

check('god and super_admin are treated alike by the render rule', () => {
  eq(workspaceFeatures(WUPA, GOD, IN_ATLANTIS),
     workspaceFeatures(WUPA, SUPER, IN_ATLANTIS))
})

check('an operator inside a legacy customer still gets open', () => {
  eq(workspaceFeatures(LEGACY, GOD, IN_ATLANTIS), null)
})

/* ── rule 2: what a direct route refuses ──────────────────────────────────── */

check('a customer typing a disabled module is refused', () => {
  eq(routeFeatureDenied('leads', ATLANTIS, ADVISOR, NO_CONTEXT), true)
})

check('a customer typing an enabled module is not refused', () => {
  eq(routeFeatureDenied('leads', WUPA, ADVISOR, NO_CONTEXT), false)
})

check('a legacy customer is refused nothing', () => {
  eq(routeFeatureDenied('leads', LEGACY, ADVISOR, NO_CONTEXT), false)
})

check('a route with no feature key refuses nobody', () => {
  eq(routeFeatureDenied(null, ATLANTIS, ADVISOR, NO_CONTEXT), false)
})

check('god is never routed into a dead end, inside a customer or out', () => {
  // Mirrors `entitlements.require_feature`, which returns early for god_admin.
  // The nav hides the item; the URL still works. That is deliberate and it is
  // why God Mode is not crippled by making the render rule honest.
  eq(routeFeatureDenied('leads', ATLANTIS, GOD, IN_ATLANTIS), false)
  eq(routeFeatureDenied('leads', ATLANTIS, GOD, NO_CONTEXT), false)
})

check('super_admin is NOT god and the route treats them as a customer', () => {
  // The server does the same: `require_feature` exempts god_admin only.
  eq(operatorRouteExempt(SUPER), false)
  eq(routeFeatureDenied('leads', ATLANTIS, SUPER, IN_ATLANTIS), true)
})

/* ── the agreement itself ─────────────────────────────────────────────────── */

check('nav, buttons, widgets and routes cannot disagree for a customer', () => {
  // Every surface asks one of these two functions and nothing else. For a
  // customer the answers are locked together: if the nav hides it, the route
  // refuses it, and the reverse.
  for (const key of ['leads', 'imports', 'campaigns', 'replies', 'crm']) {
    for (const branding of [WUPA, ATLANTIS, LEGACY, PRE_MIGRATION]) {
      for (const user of [ADVISOR, ORGADMIN]) {
        const f = workspaceFeatures(branding, user, NO_CONTEXT)
        const navShows = f === null || f.includes(key)
        const routeRefuses = routeFeatureDenied(key, branding, user, NO_CONTEXT)
        if (navShows === routeRefuses) {
          throw new Error('disagreement on ' + key + ' for ' + user.role
            + ' / ' + JSON.stringify(branding.enabled_features))
        }
      }
    }
  }
})

check('isOperator names both platform identities', () => {
  eq(isOperator(GOD), true)
  eq(isOperator(SUPER), true)
  eq(isOperator(ORGADMIN), false)
  eq(isOperator(ADVISOR), false)
  eq(isOperator(null), false)
})

/* ── role ─────────────────────────────────────────────────────────────────── */

check('the workspace role wins over the global row value', () => {
  // D'Angelo: users.role says advisor, his membership says org_admin.
  eq(roleOf({ workspace_role: 'org_admin' }, { role: 'advisor' }), 'org_admin')
  // And the reverse: a platform org_admin who is only an advisor here.
  eq(roleOf({ workspace_role: 'advisor' }, { role: 'org_admin' }), 'advisor')
})

check('the user row is the fallback only before branding has loaded', () => {
  eq(roleOf(null, ADVISOR), 'advisor')
  eq(roleOf({ workspace_role: null }, ORGADMIN), 'org_admin')
  eq(roleOf(null, null), null)
})

check('manager roles are exactly the three that administer', () => {
  eq(isManagerRole('org_admin'), true)
  eq(isManagerRole('super_admin'), true)
  eq(isManagerRole('god_admin'), true)
  eq(isManagerRole('advisor'), false)
  eq(isManagerRole('manager'), false)
  eq(isManagerRole(null), false)
})

/* ── report ───────────────────────────────────────────────────────────────── */

if (failures.length) {
  console.error(failures.length + ' FAILED, ' + passed + ' passed\n')
  for (const f of failures) console.error('  FAIL  ' + f)
  process.exit(1)
}
console.log(passed + ' passed')
