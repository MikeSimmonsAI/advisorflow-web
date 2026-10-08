import test from 'node:test'
import assert from 'node:assert/strict'
import * as l from '../frontend/src/utils/workforceLedger.js'

const item = (o = {}) => ({
  kind: 'work_item', id: 'w1', state: 'review_required', version: 'v1',
  worker: { employee_id: 'e1' },
  decisions: { review: { available: true, blocker: null } },
  phases: {
    source_complete: { status: 'unavailable' }, tests_complete: { status: 'unavailable' },
    deployed: { status: 'unavailable' }, live_verified: { status: 'unavailable' },
  },
  ...o,
})

test('unknown state is Unknown and lands in no section but unknown', () => {
  assert.equal(l.stateLabel('zzz'), 'Unknown')
  const s = l.sections([item({ state: 'zzz' })])
  assert.equal(s.unknown.length, 1)
  assert.equal(s.active.length + s.review.length + s.recent.length, 0)
})

test('unavailable facts are never zero or passed', () => {
  for (const f of [undefined, null, { status: 'unavailable' }, 0, '']) assert.equal(l.factText(f), 'Unavailable')
  assert.equal(l.factText({ status: 'passed' }), 'Passed')
})

test('done requires all four phases passed; one phase is not done', () => {
  assert.equal(l.phaseRows(item()).done, false)
  const one = item({ phases: { ...item().phases, source_complete: { status: 'passed' } } })
  const r = l.phaseRows(one)
  assert.equal(r.done, false)
  assert.equal(r.rows.filter(x => x.passed).length, 1)
  const all = {}
  for (const k of ['source_complete', 'tests_complete', 'deployed', 'live_verified']) all[k] = { status: 'passed' }
  assert.equal(l.phaseRows(item({ phases: all })).done, true)
})

test('review payload carries expected_version and only when server allows', () => {
  assert.deepEqual(l.reviewPayload(item(), 'return_to_queue'), { decision: 'return_to_queue', note: undefined, expected_version: 'v1' })
  assert.equal(l.reviewPayload(item({ version: null }), 'return_to_queue'), null)
  assert.equal(l.reviewPayload(item({ decisions: { review: { available: false, blocker: 'x' } } }), 'return_to_queue'), null)
  assert.equal(l.reviewPayload(item({ kind: 'run' }), 'return_to_queue'), null)
  assert.equal(l.reviewPayload(item(), ''), null)
})

test('no false success: only a state echo counts, replay is flagged', () => {
  assert.equal(l.reviewOutcome({}).ok, false)
  assert.equal(l.reviewOutcome(null).ok, false)
  assert.deepEqual(l.reviewOutcome({ state: 'eligibility_pending', replayed: true }), { ok: true, replayed: true, state: 'eligibility_pending' })
})

test('filters apply client-side and do not mutate the list', () => {
  const list = [item(), item({ id: 'r1', kind: 'run', state: 'failed', worker: { employee_id: 'e2' } })]
  assert.equal(l.filterItems(list, { state: 'failed' }).length, 1)
  assert.equal(l.filterItems(list, { kind: 'work_item' }).length, 1)
  assert.equal(l.filterItems(list, { employeeId: 'e2' })[0].id, 'r1')
  assert.equal(list.length, 2)
})

test('sections separate active, review and recent', () => {
  const s = l.sections(['running', 'queued', 'blocked', 'review_required', 'completed', 'failed', 'cancelled'].map((st, i) => item({ id: 'i' + i, state: st })))
  assert.deepEqual([s.active.length, s.review.length, s.recent.length, s.unknown.length], [3, 1, 3, 0])
})
