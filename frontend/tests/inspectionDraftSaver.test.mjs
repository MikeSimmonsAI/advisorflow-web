import assert from 'node:assert/strict'
import test from 'node:test'
import { createDraftSaver } from '../src/pages/roofing/draftSaver.js'

function memStorage() {
  const m = new Map()
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), m }
}
// Manual clock: scheduled callbacks run only when we say so.
function clock() {
  let q = []
  return {
    schedule: (fn, ms) => { const h = { fn, ms }; q.push(h); return h },
    cancel: (h) => { q = q.filter((x) => x !== h) },
    runAll: async () => { const now = q; q = []; for (const h of now) await h.fn() },
    get size() { return q.length },
  }
}
const err = (status) => Object.assign(new Error('fail'), { status })

test('a failed save keeps the change, retries, and only then reports saved', async () => {
  const st = memStorage(); const c = clock(); const calls = []
  let fail = true
  const s = createDraftSaver({ id: 'i1', storage: st, schedule: c.schedule, cancel: c.cancel,
    patch: async (b) => { calls.push(b); if (fail) throw err(undefined); return { ok: 1 } } })
  s.set('notes', 'Hail on the north slope')
  assert.ok(st.getItem('rf-insp-draft:i1'), 'kept on the phone before any request')
  await c.runAll()
  assert.equal(s.state, 'error')
  assert.deepEqual(s.pending, { notes: 'Hail on the north slope' }, 'nothing lost after a failed request')
  assert.ok(st.getItem('rf-insp-draft:i1'))
  assert.equal(c.size, 1, 'a retry is scheduled')
  fail = false
  await c.runAll()
  assert.equal(s.state, 'saved')
  assert.deepEqual(s.pending, {})
  assert.equal(st.getItem('rf-insp-draft:i1'), null, 'phone copy cleared once the server has it')
  assert.equal(calls.length, 2)
})

test('typing during a failed request: the newer value wins', async () => {
  const st = memStorage(); const c = clock()
  let release
  const s = createDraftSaver({ id: 'i2', storage: st, schedule: c.schedule, cancel: c.cancel,
    patch: () => new Promise((_, rej) => { release = () => rej(err(undefined)) }) })
  s.set('notes', 'first')
  const p = s.flush()
  s.set('notes', 'second, typed while the first was in flight')
  s.set('severity', 'moderate')
  await new Promise((r) => setImmediate(r))
  release()
  assert.equal(await p, false)
  assert.deepEqual(s.pending, { notes: 'second, typed while the first was in flight', severity: 'moderate' })
})

test('rapid edits are sent one request at a time and all arrive', async () => {
  const st = memStorage(); const c = clock(); const server = {}
  let inFlight = 0; let maxInFlight = 0
  const s = createDraftSaver({ id: 'i3', storage: st, schedule: c.schedule, cancel: c.cancel,
    patch: async (b) => { inFlight += 1; maxInFlight = Math.max(maxInFlight, inFlight); await new Promise((r) => setImmediate(r)); Object.assign(server, b); inFlight -= 1; return server } })
  for (let i = 0; i < 20; i += 1) { s.set('notes', 'n' + i); if (i % 5 === 0) s.flush() }
  s.set('roof_condition', 'poor')
  assert.equal(await s.settle(), true)
  assert.equal(maxInFlight, 1)
  assert.deepEqual(server, { notes: 'n19', roof_condition: 'poor' })
})

test('completion is refused while the server is unreachable', async () => {
  const st = memStorage(); const c = clock()
  const s = createDraftSaver({ id: 'i4', storage: st, schedule: c.schedule, cancel: c.cancel, patch: async () => { throw err(undefined) } })
  s.set('recommendation', 'replacement')
  assert.equal(await s.settle(), false, 'settle reports the changes did not reach the server')
  assert.equal(s.hasPending(), true)
})

test('after a refresh or closed tab, the phone copy is restored and sent', async () => {
  const st = memStorage(); const c = clock(); const sent = []
  const a = createDraftSaver({ id: 'i5', storage: st, schedule: c.schedule, cancel: c.cancel, patch: async () => { throw err(undefined) } })
  a.set('notes', 'written right before the page closed')
  a.dispose()                                   // the page goes away; the request never succeeded
  const b = createDraftSaver({ id: 'i5', storage: st, schedule: c.schedule, cancel: c.cancel, patch: async (x) => { sent.push(x); return {} } })
  assert.deepEqual(b.restore(), { notes: 'written right before the page closed' })
  await c.runAll()
  assert.deepEqual(sent, [{ notes: 'written right before the page closed' }])
  assert.equal(st.getItem('rf-insp-draft:i5'), null)
})

test('a value the server rejects is kept but not retried forever', async () => {
  const st = memStorage(); const c = clock()
  const s = createDraftSaver({ id: 'i6', storage: st, schedule: c.schedule, cancel: c.cancel, patch: async () => { throw err(400) } })
  s.set('roof_age_years', '999')
  assert.equal(await s.settle(), false)
  assert.equal(c.size, 0, 'no endless retry of a refused value')
  assert.deepEqual(s.pending, { roof_age_years: '999' })
})
