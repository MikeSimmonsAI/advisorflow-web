/**
 * DUAL-ROLE LANDING, EXECUTED.   node tests/frontend/homeDestination.test.mjs
 *
 * Four identities as /auth/my-contexts shapes them (synthetic ids only), run
 * through the real decideHomeDestination plus the switcher/guard helpers.
 */
import { decideHomeDestination } from '../../frontend/src/auth/homeDestination.js'
import { switchTargets, decideWorkspaceLanding } from '../../frontend/src/auth/workspaceLanding.js'
import { decideWorkspaceAccess, AUTHORIZED, VERIFYING }
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

const WS = { type: 'workspace', organization_id: 'org-A', path: '/workspace/org-A' }
const SALES = { type: 'platform', path: '/sales', role: 'sales_rep' }

const salesOnly = { has_back_office: true, workspace_count: 0, workspace_contexts: [],
  platform_contexts: [SALES], executive_contexts: [], default_context: SALES }
const tenantOnly = { has_back_office: false, workspace_count: 1, workspace_contexts: [WS],
  platform_contexts: [], executive_contexts: [], default_context: WS }
const dual = { has_back_office: true, workspace_count: 1, workspace_contexts: [WS],
  platform_contexts: [SALES], executive_contexts: [], default_context: SALES }
const god = { has_back_office: true, workspace_count: 0, workspace_contexts: [],
  platform_contexts: [{ type: 'platform', path: '/god' }], executive_contexts: [],
  default_context: { type: 'platform', path: '/god' } }

check('sales-only lands in the back office, never a workspace', () => {
  eq(decideHomeDestination(salesOnly, { role: 'advisor' }).to, '/sales')
  eq(switchTargets(salesOnly, null), [])
  eq(decideWorkspaceLanding('ready', salesOnly).kind, 'none')
})

check('tenant-only lands in their workspace', () => {
  eq(decideHomeDestination(tenantOnly, { role: 'org_admin' }).to, '/workspace/org-A')
})

check('dual-role lands in back office (was tenant Overview) and keeps both contexts', () => {
  eq(decideHomeDestination(dual, { role: 'advisor' }).to, '/sales')
  eq(switchTargets(dual, null).map(w => w.organization_id), ['org-A'])
  eq(dual.has_back_office, true)
})

check('dual-role can enter only the listed workspace; a typed id is not authorized', () => {
  const ok = decideWorkspaceAccess({ organizationId: 'org-A', contextsPhase: 'ready', contexts: dual })
  eq(ok.state, AUTHORIZED)
  const other = decideWorkspaceAccess({ organizationId: 'org-B', contextsPhase: 'ready',
    contexts: dual, confirmPhase: 'idle' })
  eq(other.state, VERIFYING)  // not authorized; server confirm will 403
})

check('god keeps the tenant home path, gets no redirect to /sales', () => {
  eq(decideHomeDestination(god, { role: 'god_admin' }).to, null)
})

check('dual-role is not granted operator powers by landing', () => {
  eq(decideHomeDestination(dual, { role: 'advisor' }).to === '/god', false)
})

check('missing or malformed context grants nothing and redirects nowhere', () => {
  eq(decideHomeDestination(null, { role: 'advisor' }).to, null)
  eq(decideHomeDestination('x', null).to, null)
})

if (failures.length) {
  console.error(failures.join('\n'))
  process.exit(1)
}
console.log('homeDestination: ' + passed + ' checks passed')
