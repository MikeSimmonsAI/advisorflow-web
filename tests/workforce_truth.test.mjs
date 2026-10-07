import test from 'node:test'
import assert from 'node:assert/strict'
import * as t from '../frontend/src/utils/workforceTruth.js'

test('unknown states are Unknown, never Working/Idle', () => {
  for (const s of [undefined, null, '', 'idle', 'zzz', 5]) assert.equal(t.lifecycleLabel(s), 'Unknown')
  assert.equal(t.lifecycleLabel('Cancelled'), 'Cancelled')
  assert.equal(t.lifecycleLabel('stale'), 'Stale / hung')
})
test('terminal states are not active', () => {
  for (const s of ['completed', 'failed', 'cancelled', 'skipped']) {
    assert.ok(t.isTerminal(s)); assert.ok(!t.isActive(s))
  }
})
test('percent only when evidenced', () => {
  assert.equal(t.evidencedPercent(42), 42)
  for (const v of [undefined, null, '50', NaN, -1, 101]) assert.equal(t.evidencedPercent(v), null)
})
test('newest active wins; terminal and superseded go to history', () => {
  const runs = [
    { id: 'a', state: 'running', updated_at: '2026-10-07T10:00:00Z' },
    { id: 'b', state: 'running', updated_at: '2026-10-07T10:05:00Z' },
    { id: 'c', state: 'running', updated_at: '2026-10-07T10:09:00Z', superseded_by: 'b' },
    { id: 'd', state: 'completed', updated_at: '2026-10-07T10:20:00Z' },
  ]
  const { current, history } = t.selectActiveRun(runs)
  assert.equal(current.id, 'b')
  assert.deepEqual(history.map(r => r.id), ['d', 'c', 'a'])
})
test('only terminal runs -> no current', () => {
  assert.equal(t.selectActiveRun([{ id: 1, state: 'failed' }]).current, null)
  assert.equal(t.selectActiveRun(null).current, null)
})
test('active worker never displays idle', () => {
  assert.equal(t.workerDisplayState({ state: 'idle' }, { state: 'running' }), 'running')
  assert.equal(t.workerDisplayState({ state: 'idle' }, null), 'unknown')
})
test('stale responses cannot overwrite newer', () => {
  const s = t.createSequencer(); const a = s.begin(); const b = s.begin()
  assert.ok(!s.isCurrent(a)); assert.ok(s.isCurrent(b))
})
test('guard is synchronous per action', () => {
  const g = t.createActionGuard()
  assert.ok(g.tryAcquire('pause')); assert.ok(!g.tryAcquire('pause'))
  assert.ok(g.tryAcquire('save')); g.release('pause'); assert.ok(g.tryAcquire('pause'))
})
test('failed refresh retains last-good and marks stale', () => {
  const ok = t.applyRefresh(null, { ok: true, data: { n: 1 }, at: 'x' })
  assert.equal(ok.stale, false)
  const bad = t.applyRefresh(ok, { ok: false, message: 'boom' })
  assert.deepEqual(bad.data, { n: 1 }); assert.ok(bad.stale); assert.equal(bad.error, 'boom')
  assert.equal(t.applyRefresh(null, { ok: false }).stale, false)
})
test('support code only when supplied', () => {
  assert.equal(t.supportCode({ message: 'x' }), null)
  assert.equal(t.supportCode({ detail: { support_code: ' AB-1 ' } }), 'AB-1')
})
test('evidence labels honest; secrets redacted', () => {
  assert.equal(t.evidenceLabel('tested'), 'Executed tests')
  assert.equal(t.evidenceLabel('made-up'), 'Unverified claim')
  const out = t.redact('key ghp_abcdefghijklmnop and password=hunter2 ok')
  assert.ok(!out.includes('ghp_abc') && !out.includes('hunter2'))
})
