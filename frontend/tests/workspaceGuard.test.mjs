/**
 * THE JASON SHAPE, EXECUTED.
 *
 * Not a source grep. This imports the real decision module the running app
 * imports and drives every lifecycle the incident could have taken, because
 * the defect being fixed was never visible in the source of any one line - it
 * was in which outcomes a `.catch()` had quietly merged.
 *
 *   node frontend/tests/workspaceGuard.test.mjs
 *
 * Exits non-zero on the first failure and prints every check either way.
 */
import {
  decideWorkspaceAccess, contextsListWorkspace,
  VERIFYING, AUTHORIZED, DENIED, UNVERIFIED, DENIAL_STATUSES,
} from '../src/auth/workspaceGuard.js'

const RESTLAND = '01fed629-9a95-412b-895a-c8b09f37a98c'
const OTHER = 'ffffffff-0000-0000-0000-000000000000'

let passed = 0
const failures = []

function check(name, actual, expected) {
  if (actual === expected) {
    passed += 1
    console.log('  ok   ' + name)
  } else {
    failures.push(name + ': expected ' + expected + ', got ' + actual)
    console.log('  FAIL ' + name + ' -> expected ' + expected + ', got ' + actual)
  }
}

// An error shaped exactly like the one api/client.js throws for an HTTP error.
function httpErr(status, detail) {
  const e = new Error(detail || 'Request failed')
  e.status = status
  e.detail = detail
  return e
}

// And the one it throws for a transport failure: NO status property at all.
function networkErr() {
  return new Error('Unable to reach the server. Please check your connection or try again in a moment.')
}

function sessionExpiredErr() {
  // client.js throws this bare Error after a 401 while it redirects to /login.
  return new Error('Session expired')
}

// Jason: one active Restland customer_org membership, advisor role, exactly
// the /auth/my-contexts payload workspace_access.authorized_contexts builds.
const JASON_CONTEXTS = {
  contexts: [{
    type: 'workspace', label: 'Restland Cemetery and Funeral Home',
    organization_id: RESTLAND, organization_name: 'Restland Cemetery and Funeral Home',
    organization_slug: 'restland', role: 'advisor', path: '/workspace/' + RESTLAND,
  }],
  platform_contexts: [],
  workspace_contexts: [{
    type: 'workspace', label: 'Restland Cemetery and Funeral Home',
    organization_id: RESTLAND, organization_name: 'Restland Cemetery and Funeral Home',
    organization_slug: 'restland', role: 'advisor', path: '/workspace/' + RESTLAND,
  }],
  has_back_office: false,
  workspace_count: 1,
  default_context: {
    type: 'workspace', organization_id: RESTLAND, path: '/workspace/' + RESTLAND,
  },
}

const NO_WORKSPACES = {
  contexts: [], platform_contexts: [], workspace_contexts: [],
  has_back_office: false, workspace_count: 0,
  default_context: { type: 'legacy_tenant', path: '/' },
}

function state(input) {
  return decideWorkspaceAccess(input).state
}
function reason(input) {
  return decideWorkspaceAccess(input).reason
}

console.log('\n── 1. THE JASON SHAPE: advisor, one authorized workspace ──')

check('before the context list answers, nothing is concluded',
  state({ organizationId: RESTLAND, contextsPhase: 'loading' }), VERIFYING)

check('...and it is certainly not a refusal',
  state({ organizationId: RESTLAND, contextsPhase: 'idle' }), VERIFYING)

check('server lists the workspace -> authorized',
  state({ organizationId: RESTLAND, contextsPhase: 'ready', contexts: JASON_CONTEXTS }),
  AUTHORIZED)

check('...on the server\'s own list, with no second request',
  reason({ organizationId: RESTLAND, contextsPhase: 'ready', contexts: JASON_CONTEXTS }),
  'listed-by-server')

console.log('\n── 2. FAILURES ARE NOT REFUSALS (the actual bug) ──')

const TRANSPORT_CASES = [
  ['500 from the server', httpErr(500, 'Internal Server Error')],
  ['502 bad gateway (Render restart)', httpErr(502, 'Bad Gateway')],
  ['503 service unavailable (cold start)', httpErr(503, 'Service Unavailable')],
  ['504 timeout', httpErr(504, 'Gateway Timeout')],
  ['404 route missing (frontend newer than backend)', httpErr(404, 'Not Found')],
  ['network failure, no status at all', networkErr()],
  ['a thrown string-only error', new Error('boom')],
  ['null error', null],
]

for (const [label, err] of TRANSPORT_CASES) {
  check('context list ' + label + ' -> unverified, NOT denied',
    state({ organizationId: RESTLAND, contextsPhase: 'error', contextsError: err }),
    UNVERIFIED)
  check('confirmation ' + label + ' -> unverified, NOT denied',
    state({
      organizationId: OTHER, contextsPhase: 'ready', contexts: JASON_CONTEXTS,
      confirmPhase: 'error', confirmError: err,
    }),
    UNVERIFIED)
}

check('a transport failure never renders the workspace either (no fail-open)',
  state({ organizationId: RESTLAND, contextsPhase: 'error', contextsError: httpErr(500) })
    === AUTHORIZED, false)

console.log('\n── 3. 401 IS RE-AUTHENTICATION, NOT REFUSAL ──')

check('401 on the context list -> verifying',
  state({ organizationId: RESTLAND, contextsPhase: 'error', contextsError: httpErr(401, 'Session expired') }),
  VERIFYING)

check('401 on the confirmation -> verifying',
  state({
    organizationId: OTHER, contextsPhase: 'ready', contexts: JASON_CONTEXTS,
    confirmPhase: 'error', confirmError: httpErr(401, 'Session expired'),
  }),
  VERIFYING)

check('the bare "Session expired" error client.js throws carries no status, so it is unverified rather than denied',
  state({ organizationId: RESTLAND, contextsPhase: 'error', contextsError: sessionExpiredErr() }),
  UNVERIFIED)

console.log('\n── 4. 403 IS THE ONLY DENIAL ──')

check('403 from the enforcement endpoint -> denied',
  state({
    organizationId: OTHER, contextsPhase: 'ready', contexts: JASON_CONTEXTS,
    confirmPhase: 'error', confirmError: httpErr(403, 'You do not have access to that workspace.'),
  }),
  DENIED)

check('...and it says the server refused, not that we guessed',
  reason({
    organizationId: OTHER, contextsPhase: 'ready', contexts: JASON_CONTEXTS,
    confirmPhase: 'error', confirmError: httpErr(403),
  }),
  'server-refused')

check('403 is the only status in the denial set', DENIAL_STATUSES.length, 1)
check('...and it is 403', DENIAL_STATUSES[0], 403)

check('typing another org id: while confirming, still verifying',
  state({
    organizationId: OTHER, contextsPhase: 'ready', contexts: JASON_CONTEXTS,
    confirmPhase: 'loading',
  }),
  VERIFYING)

check('a URL naming no workspace at all is refused by the browser',
  state({ organizationId: '', contextsPhase: 'ready', contexts: JASON_CONTEXTS }),
  DENIED)

console.log('\n── 5. A STALE LIST DOES NOT ACCUSE ANYBODY ──')

check('absent from the list but the server accepts it -> authorized',
  state({
    organizationId: OTHER, contextsPhase: 'ready', contexts: JASON_CONTEXTS,
    confirmPhase: 'ok',
  }),
  AUTHORIZED)

check('...credited to the server, not to the browser',
  reason({
    organizationId: OTHER, contextsPhase: 'ready', contexts: JASON_CONTEXTS,
    confirmPhase: 'ok',
  }),
  'confirmed-by-server')

check('a user with no workspaces at all is not denied before the server is asked',
  state({ organizationId: RESTLAND, contextsPhase: 'ready', contexts: NO_WORKSPACES }),
  VERIFYING)

console.log('\n── 6. THE LIST LOOKUP ITSELF ──')

check('finds the workspace', contextsListWorkspace(JASON_CONTEXTS, RESTLAND), true)
check('does not find another org', contextsListWorkspace(JASON_CONTEXTS, OTHER), false)
check('null contexts', contextsListWorkspace(null, RESTLAND), false)
check('null id', contextsListWorkspace(JASON_CONTEXTS, null), false)
check('malformed payload (workspace_contexts missing)',
  contextsListWorkspace({ contexts: [] }, RESTLAND), false)
check('malformed payload (workspace_contexts not an array)',
  contextsListWorkspace({ workspace_contexts: 'yes' }, RESTLAND), false)
check('a row with no organization_id is not a match',
  contextsListWorkspace({ workspace_contexts: [{ role: 'advisor' }] }, RESTLAND), false)
check('the empty string is never a match',
  contextsListWorkspace({ workspace_contexts: [{ organization_id: '' }] }, ''), false)

console.log('\n── 7. NO INPUT PRODUCES A DENIAL WITHOUT A 403 ──')
// The property the incident violated, asserted directly over the whole space
// of shapes this function can be handed.
const PHASES_CTX = ['idle', 'loading', 'ready', 'error']
const PHASES_CONF = ['idle', 'loading', 'ok', 'error']
const ERRORS = [null, networkErr(), sessionExpiredErr(),
  httpErr(400), httpErr(401), httpErr(403), httpErr(404),
  httpErr(409), httpErr(429), httpErr(500), httpErr(502), httpErr(503)]
const PAYLOADS = [null, undefined, JASON_CONTEXTS, NO_WORKSPACES, {}, { workspace_contexts: [] }]

let sweep = 0
let bad = 0
for (const cp of PHASES_CTX) {
  for (const fp of PHASES_CONF) {
    for (const ce of ERRORS) {
      for (const fe of ERRORS) {
        for (const payload of PAYLOADS) {
          for (const org of [RESTLAND, OTHER]) {
            sweep += 1
            const d = decideWorkspaceAccess({
              organizationId: org, contextsPhase: cp, contexts: payload,
              contextsError: ce, confirmPhase: fp, confirmError: fe,
            })
            if (d.state !== DENIED) continue
            // The ONLY denial permitted with an organization id present is one
            // backed by an authenticated 403 on the confirmation call.
            const legitimate = (
              cp === 'ready' && fp === 'error' &&
              !contextsListWorkspace(payload, org) &&
              fe && fe.status === 403
            )
            if (!legitimate) {
              bad += 1
              if (bad < 4) {
                console.log('  FAIL denial without a 403: ctx=' + cp + ' confirm=' + fp +
                  ' ctxErr=' + (ce && ce.status) + ' confErr=' + (fe && fe.status))
              }
            }
          }
        }
      }
    }
  }
}
check('swept ' + sweep + ' lifecycles; denials without an authenticated 403', bad, 0)

console.log('\n──────────────────────────────────────────')
if (failures.length) {
  console.log('FAILED ' + failures.length + ' of ' + (passed + failures.length))
  for (const f of failures) console.log('  - ' + f)
  process.exit(1)
}
console.log('PASSED ' + passed + ' checks (' + sweep + ' lifecycles swept)')
