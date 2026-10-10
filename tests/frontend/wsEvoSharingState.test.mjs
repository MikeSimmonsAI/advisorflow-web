/* EvoControls / Sharing recovery helpers (pure, dependency-free):
 *   node tests/frontend/wsEvoSharingState.test.mjs */
import assert from 'node:assert/strict'
import { evidenceBlockedReason, copyOutcome } from '../../frontend/src/pages/wholesale/wsListState.js'

let n = 0
const t = async (name, fn) => { await fn(); n += 1; console.log('ok -', name) }

await t('evidence: ready and refreshing are usable', () => {
  assert.equal(evidenceBlockedReason('The source registry', 'ready'), '')
  assert.equal(evidenceBlockedReason('The source registry', 'refreshing'), '')
})
await t('evidence: loading and error are blocked with a reason', () => {
  assert.match(evidenceBlockedReason('The source registry', 'error'), /not available/)
  assert.match(evidenceBlockedReason('The source registry', 'loading'), /not available/)
})
await t('evidence: stale is blocked and says out of date', () => {
  assert.match(evidenceBlockedReason('The source registry', 'stale'), /out of date/)
})
await t('copy: resolved write is ok', async () => {
  let got = null
  const r = await copyOutcome(async (x) => { got = x }, 'http://x/y')
  assert.equal(r.ok, true); assert.equal(got, 'http://x/y')
})
await t('copy: rejected write is not ok and has a reason', async () => {
  const r = await copyOutcome(async () => { throw new Error('denied') }, 'u')
  assert.equal(r.ok, false); assert.match(r.reason, /clipboard/)
})
await t('copy: synchronous throw is not ok', async () => {
  const r = await copyOutcome(() => { throw new Error('x') }, 'u')
  assert.equal(r.ok, false)
})
await t('copy: missing clipboard is not ok', async () => {
  assert.equal((await copyOutcome(null, 'u')).ok, false)
})

console.log(`${n} passed`)
