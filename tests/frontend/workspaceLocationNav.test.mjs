/**
 * WORKSPACE (LOCATION) ENTITLEMENTS — the browser half of spec section 15.
 *
 *   Org has Wholesale ON. Workspace A inherits ON. Workspace B explicitly OFF.
 *   User in A: nav shows, direct URL allowed.
 *   User in B: nav hides, direct URL denied (ProtectedRoute -> routeFeatureDenied).
 *
 * The server resolves the location and returns a /branding/org answer that is
 * ALREADY narrowed for it (tests/test_workspace_location_entitlements.py pins
 * that). The payloads below have exactly the shape it returns. What this file
 * proves is that the shell's three surfaces - sidebar entry, route guard and
 * product entry - read that answer and agree, and that the selector and the
 * header plumbing exist where the server contract needs them.
 *
 *     node tests/frontend/workspaceLocationNav.test.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

import {
  featureEnabled, routeFeatureDenied, canEnterProduct, productOffered,
  workspaceLocationChoices, activeLocationId, WHOLESALE_FEATURE,
} from '../../frontend/src/auth/workspaceRules.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const REPO = join(HERE, '..', '..')

let passed = 0
const failures = []
function check(name, fn) {
  try { fn(); passed += 1 } catch (e) { failures.push(name + '\n    ' + e.message) }
}
function eq(actual, expected, what) {
  const a = JSON.stringify(actual), b = JSON.stringify(expected)
  if (a !== b) throw new Error((what || 'value') + ': expected ' + b + ', got ' + a)
}

const ALL = ['leads', 'campaigns', 'sms', 'crm', 'reports', 'wholesale_real_estate']
const LOC_A = { id: 'loc-a', name: 'Workspace A', is_primary: true }
const LOC_B = { id: 'loc-b', name: 'Workspace B', is_primary: false }
const EVOSYS = { slug: 'evosyspro', products: { wholesale: 'EvoSys Wholesale' } }
const BB = { slug: 'bookaboost', products: { wholesale: null } }

function payload({ features, offered, platform = EVOSYS, mode, selected, available, header = 'X-Workspace-Location', rejected = false }) {
  return {
    organization_id: 'org-1', workspace_role: 'advisor', enabled_features: features,
    platform: { ...platform, offered: { wholesale: offered } },
    workspace_location: { header, mode, selected, available, rejected, enforced: true,
                          effective_location_ids: selected ? [selected.id] : [] },
  }
}

// Exactly what GET /branding/org returns for each person in the spec matrix.
const IN_A = payload({ features: ALL, offered: true, mode: 'implicit_single', selected: LOC_A, available: [LOC_A] })
const IN_B = payload({ features: ALL.filter(k => k !== WHOLESALE_FEATURE), offered: false,
                       mode: 'implicit_single', selected: LOC_B, available: [LOC_B] })
const IN_BOTH = payload({ features: ALL.filter(k => k !== WHOLESALE_FEATURE), offered: false,
                          mode: 'all_assigned_most_restrictive', selected: null, available: [LOC_A, LOC_B] })
const ADVISOR = { role: 'advisor' }
const GOD = { role: 'god_admin' }

check('user in A: nav shows Wholesale and the direct URL is allowed', () => {
  eq(featureEnabled(IN_A, WHOLESALE_FEATURE), true, 'feature')
  eq(canEnterProduct(IN_A, ADVISOR, null, 'wholesale', WHOLESALE_FEATURE), true, 'nav entry')
  eq(routeFeatureDenied(WHOLESALE_FEATURE, IN_A, ADVISOR, null), false, 'direct URL')
})

check('user in B: nav hides Wholesale and the direct URL is denied', () => {
  eq(featureEnabled(IN_B, WHOLESALE_FEATURE), false, 'feature')
  eq(productOffered(IN_B, 'wholesale'), false, 'offered')
  eq(canEnterProduct(IN_B, ADVISOR, null, 'wholesale', WHOLESALE_FEATURE), false, 'nav entry')
  eq(routeFeatureDenied(WHOLESALE_FEATURE, IN_B, ADVISOR, null), true, 'direct URL')
  // Only the one module is narrowed.
  eq(routeFeatureDenied('leads', IN_B, ADVISOR, null), false, 'leads still open')
})

check('same in a BookaBoost workspace (the brand offers it without a product name)', () => {
  const a = { ...IN_A, platform: { ...BB, offered: { wholesale: true } } }
  const b = { ...IN_B, platform: { ...BB, offered: { wholesale: false } } }
  eq(canEnterProduct(a, ADVISOR, null, 'wholesale', WHOLESALE_FEATURE), true, 'A')
  eq(canEnterProduct(b, ADVISOR, null, 'wholesale', WHOLESALE_FEATURE), false, 'B')
  eq(routeFeatureDenied(WHOLESALE_FEATURE, b, ADVISOR, null), true, 'B direct URL')
})

check('several locations and no selection: most restrictive, nav hides', () => {
  eq(canEnterProduct(IN_BOTH, ADVISOR, null, 'wholesale', WHOLESALE_FEATURE), false, 'entry')
  eq(routeFeatureDenied(WHOLESALE_FEATURE, IN_BOTH, ADVISOR, null), true, 'route')
  eq(activeLocationId(IN_BOTH), null, 'no single active location')
})

check('the selector is offered only when there is a real choice', () => {
  eq(workspaceLocationChoices(IN_A).length, 0, 'one location: no selector')
  eq(workspaceLocationChoices(IN_BOTH).map(l => l.id), ['loc-a', 'loc-b'], 'two: selector')
  eq(workspaceLocationChoices({}).length, 0, 'no payload')
  eq(workspaceLocationChoices(null).length, 0, 'null branding')
})

check('no selector when the server has not advertised a header the browser may send', () => {
  const noHeader = { ...IN_BOTH, workspace_location: { ...IN_BOTH.workspace_location, header: null } }
  eq(workspaceLocationChoices(noHeader).length, 0, 'CORS not ready')
})

check('the active location is the one the server resolved', () => {
  eq(activeLocationId(IN_A), 'loc-a', 'A')
  eq(activeLocationId(IN_B), 'loc-b', 'B')
})

check('god impersonating a customer still reaches the route (server exempts god too)', () => {
  eq(routeFeatureDenied(WHOLESALE_FEATURE, IN_B, GOD, { orgId: 'org-1' }), false, 'god route')
  // ...but the nav shows the customer's truth: B does not offer it.
  eq(canEnterProduct(IN_B, GOD, { orgId: 'org-1' }, 'wholesale', WHOLESALE_FEATURE), false, 'god nav')
})

/* ── plumbing the server contract depends on ─────────────────────────────── */

const client = readFileSync(join(REPO, 'frontend', 'src', 'api', 'client.js'), 'utf8')
const layout = readFileSync(join(REPO, 'frontend', 'src', 'components', 'Layout.jsx'), 'utf8')
const serverLoc = readFileSync(join(REPO, 'app', 'services', 'workspace_location.py'), 'utf8')

check('the client and the server name the same header', () => {
  const server = serverLoc.match(/LOCATION_HEADER = "([^"]+)"/)[1]
  const browser = client.match(/WORKSPACE_LOCATION_HEADER = '([^']+)'/)[1]
  eq(browser, server, 'header name')
})

check('request() sends the selected location and the dedupe key includes it', () => {
  const req = client.slice(client.indexOf('async function request('), client.indexOf('const _inFlightGets'))
  if (!/workspaceLocationHeader\(\)/.test(req)) throw new Error('request() does not send the location')
  const key = client.slice(client.indexOf('function _getDedupeKey'), client.indexOf('export function resetInFlightGets'))
  if (!/workspaceLocationHeader\(\)/.test(key)) throw new Error('dedupe key ignores the location')
})

check('the header is sent only when advertised and only for the org it was chosen in', () => {
  const fn = client.slice(client.indexOf('export function workspaceLocationHeader'))
  if (!/wl\.header !== WORKSPACE_LOCATION_HEADER/.test(fn)) throw new Error('not gated on the advertised header')
  if (!/sel\.orgId !== b\.organization_id/.test(fn)) throw new Error('not scoped to the organization')
})

check('storage access for the location is wrapped (private mode must not break the app)', () => {
  const block = client.slice(client.indexOf('const WORKSPACE_LOCATION_KEY'), client.indexOf('export function workspaceLocationHeader'))
  const uses = (block.match(/localStorage\./g) || []).length
  const guarded = (block.match(/try \{[^}]*localStorage\./g) || []).length
  if (uses === 0 || guarded < 3) throw new Error('unguarded localStorage use: ' + uses + ' uses, ' + guarded + ' guarded')
})

check('a rejected selection is cleared', () => {
  if (!/workspace_location\.rejected\) clearWorkspaceLocation\(\)/.test(client)) {
    throw new Error('rejected selection is not cleared')
  }
})

check('logout clears the location with the rest of the context', () => {
  const all = client.slice(client.indexOf('export function clearAllContext'))
  if (!/clearWorkspaceLocation\(\)/.test(all.slice(0, 300))) throw new Error('clearAllContext keeps it')
})

check('Layout renders the selector from the shared rule and remounts on a switch', () => {
  if (!/workspaceLocationChoices\(branding\)/.test(layout)) throw new Error('no selector rule')
  if (!/activeLocationId\(branding\)/.test(layout)) throw new Error('workspaceKey ignores the location')
})

check('the footer labels org, person and role separately', () => {
  for (const id of ['organization', 'person', 'role']) {
    if (!layout.includes('data-identity="' + id + '"')) throw new Error('no labelled ' + id + ' line')
  }
})

if (failures.length) {
  console.error(`\n${failures.length} FAILED, ${passed} passed\n`)
  for (const f of failures) console.error('  ✕ ' + f)
  process.exit(1)
}
console.log(`workspaceLocationNav: ${passed} passed`)
