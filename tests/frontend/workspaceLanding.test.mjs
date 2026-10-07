/**
 * NORMAL-ACCOUNT WORKSPACE LANDING AND SWITCHING, EXECUTED.
 *
 *     node tests/frontend/workspaceLanding.test.mjs
 *
 * Runs the real decision modules (they import nothing): zero / one / many
 * workspaces, unknown-is-not-zero, switch targets taken only from the server
 * list, a customer manager never being an operator, and the stale-response
 * rule a workspace switch depends on. Synthetic ids only.
 */
import {
  decideWorkspaceLanding, switchTargets, isStaleForWorkspace, workspacesOf,
  LANDING_LOADING, LANDING_ERROR, LANDING_NONE, LANDING_SINGLE, LANDING_MULTIPLE,
} from '../../frontend/src/auth/workspaceLanding.js'
import {
  isOperator, operatorRouteExempt, routeFeatureDenied, roleOf, workspaceFeatures,
} from '../../frontend/src/auth/workspaceRules.js'
import { decideWorkspaceAccess, AUTHORIZED, DENIED, VERIFYING }
  from '../../frontend/src/auth/workspaceGuard.js'

let passed = 0
const failures = []
function check(name, fn) {
  try { fn(); passed += 1 } catch (e) { failures.push(name + '\n    ' + e.message) }
}
function eq(a, b, what) {
  const x = JSON.stringify(a), y = JSON.stringify(b)
  if (x !== y) throw new Error((what || 'value') + ': expected ' + y + ', got ' + x)
}

const A = { organization_id: 'org-a', organization_name: 'Org A', role: 'org_admin' }
const B = { organization_id: 'org-b', organization_name: 'Org B', role: 'advisor' }
const ctx = (...ws) => ({ workspace_contexts: ws, executive_contexts: [], has_back_office: false })

/* ── zero / one / many ───────────────────────────────────────────────────── */
check('zero workspaces is an honest NONE, never a list or a landing', () => {
  const d = decideWorkspaceLanding('ready', ctx())
  eq(d.kind, LANDING_NONE)
  eq(d.workspace, undefined, 'no workspace to land in')
})
check('one workspace lands directly in it', () => {
  const d = decideWorkspaceLanding('ready', ctx(A))
  eq(d.kind, LANDING_SINGLE); eq(d.workspace.organization_id, 'org-a')
})
check('several workspaces require an explicit choice', () => {
  const d = decideWorkspaceLanding('ready', ctx(A, B))
  eq(d.kind, LANDING_MULTIPLE); eq(d.workspaces.length, 2)
})
check('rows without an organization id are not workspaces', () => {
  eq(workspacesOf(ctx(A, { organization_name: 'ghost' }, null)).length, 1)
})

/* ── unknown is not zero ─────────────────────────────────────────────────── */
check('loading / idle render as LOADING, not NONE', () => {
  eq(decideWorkspaceLanding('loading', null).kind, LANDING_LOADING)
  eq(decideWorkspaceLanding('idle', null).kind, LANDING_LOADING)
  eq(decideWorkspaceLanding('ready', null).kind, LANDING_LOADING)
})
check('a failed load is ERROR (retryable), not NONE', () => {
  eq(decideWorkspaceLanding('error', null).kind, LANDING_ERROR)
})
check('a ready answer missing workspace_contexts is ERROR, not NONE', () => {
  eq(decideWorkspaceLanding('ready', {}).kind, LANDING_ERROR)
  eq(decideWorkspaceLanding('ready', { workspace_contexts: 'x' }).kind, LANDING_ERROR)
})

/* ── switch targets ──────────────────────────────────────────────────────── */
check('switch targets exclude the active workspace', () => {
  eq(switchTargets(ctx(A, B), 'org-a').map(w => w.organization_id), ['org-b'])
})
check('single membership has nothing to switch to', () => {
  eq(switchTargets(ctx(A), 'org-a'), [])
})
check('an id the server did not list is never offered', () => {
  eq(switchTargets(ctx(A), 'org-zzz').map(w => w.organization_id), ['org-a'])
  eq(switchTargets(null, 'org-a'), [])
})

/* ── membership removed: the guard fails closed ──────────────────────────── */
check('workspace dropped from the list and refused with 403 is DENIED', () => {
  const d = decideWorkspaceAccess({
    organizationId: 'org-b', contextsPhase: 'ready', contexts: ctx(A),
    confirmPhase: 'error', confirmError: { status: 403 } })
  eq(d.state, DENIED)
})
check('listed workspace is AUTHORIZED; unlisted while confirming is VERIFYING', () => {
  eq(decideWorkspaceAccess({ organizationId: 'org-a', contextsPhase: 'ready',
    contexts: ctx(A) }).state, AUTHORIZED)
  eq(decideWorkspaceAccess({ organizationId: 'org-b', contextsPhase: 'ready',
    contexts: ctx(A), confirmPhase: 'loading' }).state, VERIFYING)
})

/* ── customer manager is not a platform operator ─────────────────────────── */
const manager = { role: 'org_admin', organization_id: 'org-a' }
const branding = { organization_id: 'org-a', workspace_role: 'org_admin',
                   enabled_features: ['leads'] }
check('a customer manager is not an operator and gets no route exemption', () => {
  eq(isOperator(manager), false)
  eq(operatorRouteExempt(manager), false)
})
check('a membership role cannot make an advisor an operator', () => {
  const advisor = { role: 'advisor' }
  eq(isOperator(advisor), false)
  eq(roleOf({ workspace_role: 'org_admin' }, advisor), 'org_admin')
  eq(isOperator({ role: roleOf({ workspace_role: 'org_admin' }, advisor) }), false)
})
check('a manager is still bound by the workspace allow-list', () => {
  eq(routeFeatureDenied('billing', branding, manager, null), true)
  eq(routeFeatureDenied('leads', branding, manager, null), false)
  eq(workspaceFeatures(branding, manager, null), ['leads'])
})
check('only god_admin passes the route exemption', () => {
  eq(operatorRouteExempt({ role: 'god_admin' }), true)
  eq(operatorRouteExempt({ role: 'super_admin' }), false)
})

/* ── stale responses across a switch ─────────────────────────────────────── */
check('a response issued for A is stale once B is selected', () => {
  eq(isStaleForWorkspace('org-a', 'org-b'), true)
  eq(isStaleForWorkspace('org-a', null), true)
  eq(isStaleForWorkspace(null, 'org-b'), true)
})
check('a response for the still-selected workspace is kept', () => {
  eq(isStaleForWorkspace('org-a', 'org-a'), false)
  eq(isStaleForWorkspace(null, ''), false)
})
check('simulated: slow A response landing after switch to B is dropped', () => {
  let selected = 'org-a', cache = null
  const askedFor = selected                       // request for A issued
  selected = 'org-b'                              // user switches
  cache = { organization_id: 'org-b' }            // B's answer stored
  const arrivesLate = { organization_id: 'org-a' }
  if (!isStaleForWorkspace(askedFor, selected)) cache = arrivesLate
  eq(cache.organization_id, 'org-b', 'cache after late A response')
})

if (failures.length) {
  console.log('FAILED ' + failures.length + ' of ' + (passed + failures.length))
  failures.forEach(f => console.log('  FAIL ' + f))
  process.exit(1)
}
console.log('workspaceLanding: ' + passed + ' checks passed')
