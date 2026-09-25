/**
 * THE WHOLESALE MODULE'S FOUR SURFACES MUST AGREE — and the key must be real.
 *
 * The defect this file exists to prevent is the one entitlements.py records at
 * length: a sidebar that asks `isFeatureEnabled('some_key')` for a key the
 * server has never heard of. That check returns true for a legacy-open
 * workspace and false for every configured one, so the nav item silently
 * disappears for exactly the customers who paid for it — or worse, appears and
 * opens onto a 402.
 *
 * So this asserts two things:
 *
 *   1. every `featureKey` the Wholesale nav group uses is a key the SERVER
 *      registers, read out of app/services/entitlements.py rather than
 *      re-typed here, and
 *   2. the nav and the route guard give the SAME answer for the same workspace.
 *
 * Runs under plain node, no bundler, no DOM, no test framework:
 *
 *     node tests/frontend/wholesaleNav.test.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

import {
  featureEnabled, isManagerRole, roleOf, routeFeatureDenied,
} from '../../frontend/src/auth/workspaceRules.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..')

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

/* ── What the server actually registers ───────────────────────────────────── */

const entitlements = readFileSync(
  join(REPO, 'app', 'services', 'entitlements.py'), 'utf8')

// Everything between `FEATURES: Dict[str, str] = {` and its closing brace.
const featuresBlock = entitlements.split('FEATURES: Dict[str, str] = {')[1]
  .split('\nALL_FEATURE_KEYS')[0]
const SERVER_KEYS = new Set(
  [...featuresBlock.matchAll(/^\s*"([a-z0-9_]+)"\s*:/gm)].map((m) => m[1]))

/* ── What the sidebar and the routes ask for ──────────────────────────────── */

const layout = readFileSync(
  join(REPO, 'frontend', 'src', 'components', 'Layout.jsx'), 'utf8')
const app = readFileSync(join(REPO, 'frontend', 'src', 'App.jsx'), 'utf8')

const NAV_WHOLESALE_KEYS = [...layout.matchAll(
  /to:\s*'\/wholesale[^']*',[^}]*featureKey:\s*'([a-z0-9_]+)'/g)].map((m) => m[1])

const ROUTE_WHOLESALE_KEYS = [...app.matchAll(
  /path="\/wholesale[^"]*"[^>]*feature="([a-z0-9_]+)"/g)].map((m) => m[1])

const NAV_WHOLESALE_ITEMS = [...layout.matchAll(
  /\{\s*to:\s*'(\/wholesale[^']*)'[^}]*\}/g)].map((m) => m[0])

const ROUTE_WHOLESALE_PATHS = [...app.matchAll(
  /<Route path="(\/wholesale[^"]*)"/g)].map((m) => m[1])

/* ── 1. The key is real ───────────────────────────────────────────────────── */

check('the server registers wholesale_real_estate', () => {
  if (!SERVER_KEYS.has('wholesale_real_estate')) {
    throw new Error('entitlements.FEATURES has no wholesale_real_estate key')
  }
})

check('every wholesale nav item names a key the server knows', () => {
  if (!NAV_WHOLESALE_KEYS.length) throw new Error('no wholesale nav items found')
  for (const key of NAV_WHOLESALE_KEYS) {
    if (!SERVER_KEYS.has(key)) {
      throw new Error(`nav uses featureKey '${key}', which the server does not register`)
    }
  }
})

check('every wholesale route names a key the server knows', () => {
  if (!ROUTE_WHOLESALE_KEYS.length) throw new Error('no wholesale routes found')
  for (const key of ROUTE_WHOLESALE_KEYS) {
    if (!SERVER_KEYS.has(key)) {
      throw new Error(`route uses feature='${key}', which the server does not register`)
    }
  }
})

check('every wholesale nav item has a route, and vice versa', () => {
  const navPaths = NAV_WHOLESALE_ITEMS
    .map((s) => s.match(/to:\s*'([^']+)'/)[1]).sort()
  const routePaths = ROUTE_WHOLESALE_PATHS
    .filter((p) => !p.includes(':')).sort()
  eq(navPaths, routePaths, 'nav paths vs route paths')
})

check('every wholesale nav item carries a feature key', () => {
  for (const item of NAV_WHOLESALE_ITEMS) {
    if (!/featureKey:/.test(item)) {
      throw new Error('a wholesale nav item has no featureKey: ' + item)
    }
  }
})

/* ── 2. The surfaces agree ────────────────────────────────────────────────── */

const ADVISOR = { role: 'advisor' }
const ORGADMIN = { role: 'org_admin' }
const GOD = { role: 'god_admin' }

// A customer who bought the module. `leads` is present because the server's
// REQUIRES map says the module cannot work without it.
const ENABLED = {
  enabled_features: ['leads', 'wholesale_real_estate'],
  workspace_role: 'advisor', organization_id: 'org-wholesaler',
}
// A customer who did not. NOT the same as legacy-open.
const NOT_ENABLED = {
  enabled_features: ['leads'], workspace_role: 'advisor',
  organization_id: 'org-funeral',
}
// Legacy: enabled_features absent entirely means "everything", and that
// asymmetry is deliberate on the server too.
const LEGACY = { workspace_role: 'advisor', organization_id: 'org-legacy' }

check('an entitled workspace sees the nav and passes the route', () => {
  eq(featureEnabled(ENABLED, 'wholesale_real_estate'), true, 'nav')
  eq(routeFeatureDenied('wholesale_real_estate', ENABLED, ADVISOR, 'org-wholesaler'),
     false, 'route')
})

check('an unentitled workspace sees neither', () => {
  eq(featureEnabled(NOT_ENABLED, 'wholesale_real_estate'), false, 'nav')
  eq(routeFeatureDenied('wholesale_real_estate', NOT_ENABLED, ADVISOR, 'org-funeral'),
     true, 'route')
})

check('a legacy workspace keeps everything, here as everywhere else', () => {
  eq(featureEnabled(LEGACY, 'wholesale_real_estate'), true, 'nav')
  eq(routeFeatureDenied('wholesale_real_estate', LEGACY, ADVISOR, 'org-legacy'),
     false, 'route')
})

check('god_admin is exempt from the route gate, as on the server', () => {
  eq(routeFeatureDenied('wholesale_real_estate', NOT_ENABLED, GOD, 'org-funeral'),
     false, 'route')
})

/* ── 3. Only settings is admin-only ───────────────────────────────────────── */

check('the day-to-day screens are not adminOnly; settings is', () => {
  for (const item of NAV_WHOLESALE_ITEMS) {
    const to = item.match(/to:\s*'([^']+)'/)[1]
    const admin = /adminOnly:\s*true/.test(item)
    if (to === '/wholesale/settings' && !admin) {
      throw new Error('wholesale settings should be adminOnly')
    }
    if (to !== '/wholesale/settings' && admin) {
      throw new Error(to + ' should not be adminOnly — an acquisitions person '
                      + 'who is not an org admin works these screens all day')
    }
  }
})

check('an ordinary advisor is not a manager role', () => {
  eq(isManagerRole(roleOf(ENABLED, ADVISOR)), false, 'advisor')
  eq(isManagerRole(roleOf({ ...ENABLED, workspace_role: 'org_admin' }, ORGADMIN)),
     true, 'org_admin')
})

/* ── report ───────────────────────────────────────────────────────────────── */

if (failures.length) {
  console.error(`\n${failures.length} FAILED, ${passed} passed\n`)
  for (const f of failures) console.error('  ✕ ' + f)
  process.exit(1)
}
console.log(`wholesaleNav: ${passed} passed`)
