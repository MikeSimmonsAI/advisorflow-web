/**
 * THE DENIED-SWITCH PATH, EXECUTED: select B -> 403 -> state cleared -> a late
 * B reply is dropped -> A is never restored -> /auth/my-contexts recovers.
 *
 *     node tests/frontend/workspaceDenial.test.mjs
 *
 * The decisions are the REAL production functions (api/workspaceDenial.js,
 * which client.js calls). The store and transport are a small model of
 * client.js request(): same order - read selection, send, drop a stale reply,
 * then apply a denial - against a fake server that mirrors app/deps.py.
 */
import {
  WORKSPACE_DENIED_DETAIL, isWorkspaceDenial, isStaleWorkspaceResponse,
  applyWorkspaceDenial,
} from '../../frontend/src/api/workspaceDenial.js'

let passed = 0
const failures = []
async function check(name, fn) {
  try { await fn(); passed += 1 } catch (e) { failures.push(name + '\n    ' + e.message) }
}
function eq(a, b, what) {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    throw new Error((what || 'value') + ': expected ' + JSON.stringify(b) + ', got ' + JSON.stringify(a))
  }
}

function makeClient(held) {
  const st = { ws: null, branding: null, location: null, inflight: new Map(), rendered: [] }
  const effects = {
    clearBranding: () => { st.branding = null },
    clearWorkspaceLocation: () => { st.location = null },
    resetInFlightGets: () => { st.inflight.clear() },
    clearWorkspaceContext: () => { st.ws = null },
  }
  // fake server mirroring deps.get_current_user: header held -> 200 for that
  // workspace, header not held -> the one canonical 403; /auth/* always open.
  function server(path, wsHeader) {
    if (path.startsWith('/auth/')) {
      return { status: 200, body: { contexts: [...held] } }
    }
    if (wsHeader && !held.has(wsHeader)) {
      return { status: 403, detail: WORKSPACE_DENIED_DETAIL }
    }
    return { status: 200, body: { workspace: wsHeader || 'home' } }
  }
  // request(): captured header at send time, reply delivered later.
  function send(path) {
    const sent = st.ws
    const reply = server(path, sent)
    return () => {  // deliver
      if (isStaleWorkspaceResponse(sent, st.ws, path)) return { dropped: true }
      if (reply.status !== 200) {
        applyWorkspaceDenial(reply.status, reply.detail, effects)
        return { error: reply.status, detail: reply.detail }
      }
      st.rendered.push(reply.body)
      return { ok: reply.body }
    }
  }
  return { st, effects, send }
}

await check('denial predicate is exact: only 403 + the canonical sentence', () => {
  eq(isWorkspaceDenial(403, WORKSPACE_DENIED_DETAIL), true)
  eq(isWorkspaceDenial(403, 'Forbidden'), false)
  eq(isWorkspaceDenial(404, WORKSPACE_DENIED_DETAIL), false)
  eq(isWorkspaceDenial(401, WORKSPACE_DENIED_DETAIL), false)
})

await check('denied switch A -> B clears everything and never restores A', () => {
  const c = makeClient(new Set(['A']))      // B is not held
  c.st.ws = 'A'; c.st.branding = { org: 'A' }; c.st.location = { org: 'A', loc: 1 }
  c.st.inflight.set('k', 'A-read')
  // switchWorkspace(B): production drops A's caches, then stores B.
  c.effects.clearBranding(); c.effects.clearWorkspaceLocation()
  c.effects.resetInFlightGets(); c.st.ws = 'B'
  const r = c.send('/leads')()
  eq(r.error, 403); eq(r.detail, WORKSPACE_DENIED_DETAIL)
  eq(c.st.ws, null, 'selection'); eq(c.st.branding, null, 'branding')
  eq(c.st.location, null, 'location'); eq(c.st.inflight.size, 0, 'in-flight')
  eq(c.st.rendered, [], 'nothing rendered (no A, no home data under B)')
})

await check('a B reply landing after the denial is dropped, not rendered', () => {
  const c = makeClient(new Set(['A', 'B']))
  c.st.ws = 'B'
  const lateB = c.send('/leads')              // sent while B selected, granted
  c.st.ws = null                              // a denial elsewhere cleared the selection
  eq(lateB(), { dropped: true })
  eq(c.st.rendered, [])
})

await check('a denial reply for B arriving after switching to C does not clear C', () => {
  const c = makeClient(new Set(['A', 'C']))
  c.st.ws = 'B'
  const toB = c.send('/leads')                // will be a 403
  c.st.ws = 'C'; c.st.branding = { org: 'C' }
  eq(toB(), { dropped: true })
  eq(c.st.ws, 'C'); eq(c.st.branding, { org: 'C' })
})

await check('after the denial /auth/my-contexts recovers and a held pick works', () => {
  const c = makeClient(new Set(['A']))
  c.st.ws = 'B'
  c.send('/leads')()                          // denied, selection cleared
  eq(c.st.ws, null)
  const ctx = c.send('/auth/my-contexts')()
  eq(ctx.ok, { contexts: ['A'] })
  c.st.ws = 'A'                               // person chooses again
  eq(c.send('/leads')().ok, { workspace: 'A' })
})

await check('/auth/* replies are never dropped as stale (recovery must land)', () => {
  eq(isStaleWorkspaceResponse('B', null, '/auth/my-contexts'), false)
  eq(isStaleWorkspaceResponse('B', null, '/auth/refresh'), false)
})

await check('staleness rules: no sent id never stale; mismatch stale; match fresh', () => {
  eq(isStaleWorkspaceResponse(null, 'A', '/leads'), false)   // legacy / login flows
  eq(isStaleWorkspaceResponse('', 'A', '/leads'), false)
  eq(isStaleWorkspaceResponse('B', 'A', '/leads'), true)
  eq(isStaleWorkspaceResponse('B', null, '/leads'), true)
  eq(isStaleWorkspaceResponse('B', 'B', '/leads'), false)
})

await check('applyWorkspaceDenial ignores every other failure', () => {
  let calls = 0
  const fx = { clearBranding: () => calls++, clearWorkspaceLocation: () => calls++,
               resetInFlightGets: () => calls++, clearWorkspaceContext: () => calls++ }
  eq(applyWorkspaceDenial(403, 'Forbidden', fx), false)
  eq(applyWorkspaceDenial(500, WORKSPACE_DENIED_DETAIL, fx), false)
  eq(calls, 0)
  eq(applyWorkspaceDenial(403, WORKSPACE_DENIED_DETAIL, fx), true)
  eq(calls, 4)
})

if (failures.length) {
  console.error(failures.length + ' FAILED, ' + passed + ' passed\n' + failures.join('\n'))
  process.exit(1)
}
console.log(passed + ' passed')
