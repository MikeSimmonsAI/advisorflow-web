/**
 * ENTERING A CUSTOMER WORKSPACE — WHICH CUSTOMER THE REQUEST IS ABOUT.
 *
 * THE PRODUCTION DEFECT THIS FILE HOLDS SHUT. A platform owner entering a
 * customer from God Mode landed on the PLATFORM dashboard instead of that
 * customer's own, and stayed there until the browser was refreshed. The rail,
 * the skin and the vocabulary were all correct; only the page in the middle of
 * them was wrong, which is what made it look like a rendering bug.
 *
 * It was not. `enterCustomer` clears the branding cache and re-reads
 * `/branding/org` so the workspace opens on the customer's own presentation —
 * and that read happens while the address bar still says `/god`. The header
 * that names the customer was decided from the address bar alone, so it was
 * stripped from exactly that call. The server answered for the neutral owner:
 * `industry: null`. That was cached, the app navigated into the workspace, and
 * every screen chosen from `industry` — the dashboard, the rail, the words —
 * fell back to the platform's generic ones.
 *
 * Runs under plain node, no bundler, no DOM, no framework:
 *
 *     node tests/frontend/workspaceEntry.test.mjs
 *
 * `src/auth/routeAuthority.js` imports nothing, which is why this can call the
 * real decision rather than a copy of it. tests/test_workspace_entry.py runs
 * this file as part of the Python suite.
 */
import {
  classifyRoute, orgOverrideFor, shouldSendOrgOverride,
} from '../../frontend/src/auth/routeAuthority.js'

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

/* ── fixtures: the two customers this was found and verified on ──────────── */

const CCB = '6fe0124d-4a58-4654-8a0c-87e045a59218'   // cleaning
const ATLANTIS = 'org-atlantis-light-and-power'      // energy

// Where the browser is standing when `enterCustomer` makes its branding read:
// still on the God Mode screen the operator clicked Enter from.
const WHILE_ENTERING = '/god'

/* ── the defect ───────────────────────────────────────────────────────────── */

check('entering names the customer, from a God Mode route', () => {
  eq(orgOverrideFor(WHILE_ENTERING, { asCustomer: CCB }), CCB,
     'the branding read during entry')
})

check('the same is true from every God Mode screen Enter is offered on', () => {
  for (const from of ['/god', '/god/platform', '/god/customers',
                      '/god/organizations', '/god/workspaces']) {
    eq(orgOverrideFor(from, { asCustomer: CCB }), CCB, from)
  }
})

check('it is the customer being ENTERED, not the one last stood in', () => {
  // Switching customers: the stale context still names the previous one.
  eq(orgOverrideFor(WHILE_ENTERING, { orgId: CCB, asCustomer: ATLANTIS }),
     ATLANTIS, 'CCB → Atlantis')
  eq(orgOverrideFor(WHILE_ENTERING, { orgId: ATLANTIS, asCustomer: CCB }),
     CCB, 'Atlantis → CCB')
})

check('without it, a God Mode route still sends nothing — the leak stays shut', () => {
  eq(orgOverrideFor(WHILE_ENTERING, { orgId: CCB }), null)
  eq(orgOverrideFor('/god/billing', { orgId: CCB }), null)
  eq(shouldSendOrgOverride('/god'), false)
  eq(classifyRoute('/god'), 'platform')
})

/* ── and the rule it must not have replaced ──────────────────────────────── */

check('inside the workspace, the selected customer still scopes every request', () => {
  eq(orgOverrideFor('/', { orgId: CCB }), CCB, 'the dashboard')
  eq(orgOverrideFor('/reports', { orgId: CCB }), CCB, 'reports')
  eq(orgOverrideFor('/view/prospects', { orgId: CCB }), CCB, 'a configured screen')
  eq(orgOverrideFor('/leads/abc-123', { orgId: CCB }), CCB, 'a record')
})

check('noOrgContext still opts a platform-wide read out from inside a workspace', () => {
  eq(orgOverrideFor('/', { orgId: CCB, noOrgContext: true }), null)
})

check('an explicit customer beats noOrgContext, because the caller named one', () => {
  // Nothing in the app does this today. Asserted so the precedence is a
  // decision on the record rather than an accident of statement order.
  eq(orgOverrideFor('/god', { noOrgContext: true, asCustomer: CCB }), CCB)
})

check('no customer anywhere means no header, whatever the route', () => {
  eq(orgOverrideFor('/', {}), null)
  eq(orgOverrideFor('/god', {}), null)
  eq(orgOverrideFor('/sales', { orgId: CCB }), null, 'brand sales is not customer space')
  eq(orgOverrideFor('/executive', { orgId: CCB }), null, 'the executive suite')
})

check('a signed-out or unknown path is treated as customer space, as before', () => {
  // The default in classifyRoute, restated here because orgOverrideFor is now
  // what the request layer calls and this is the direction that is safe.
  eq(orgOverrideFor('/something-new', { orgId: CCB }), CCB)
})

/* ── report ───────────────────────────────────────────────────────────────── */

if (failures.length) {
  console.error(failures.length + ' FAILED, ' + passed + ' passed\n')
  for (const f of failures) console.error('  FAIL  ' + f)
  process.exit(1)
}
console.log(passed + ' passed')
