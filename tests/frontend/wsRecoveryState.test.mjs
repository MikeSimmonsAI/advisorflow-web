/* Exceptions / Funding Partners / Command recovery helpers (pure, dependency-free):
 *   node tests/frontend/wsRecoveryState.test.mjs */
import assert from 'node:assert/strict'
import {
  initialRecord, recordStarted, recordSucceeded, recordFailed, recordView, sortByKeyThenId,
  exceptionStatusLabel, resolveBlockedReason, sweepSummary, validatePartner, partnerSaveBlockedReason,
  trackRecordText, roomsLoadState, roomTotal, roomIdsToLoad,
} from '../../frontend/src/pages/wholesale/wsListState.js'

let n = 0
const t = (name, fn) => { fn(); n += 1; console.log('ok -', name) }

t('record: first load is loading, then ready', () => {
  assert.equal(recordView(initialRecord()), 'loading')
  const s = recordSucceeded(recordStarted(initialRecord()), 1, { a: 1 })
  assert.equal(recordView(s), 'ready')
})
t('record: first-load failure is error, not stale', () => {
  const s = recordFailed(recordStarted(initialRecord()), 1, 'boom', 'EVO-1')
  assert.equal(recordView(s), 'error')
  assert.equal(s.supportCode, 'EVO-1')
})
t('record: failed refresh keeps the last good data and is stale', () => {
  const good = recordSucceeded(recordStarted(initialRecord()), 1, { a: 1 })
  const refreshing = recordStarted(good)
  assert.equal(recordView(refreshing), 'refreshing')
  assert.deepEqual(refreshing.data, { a: 1 })
  const failed = recordFailed(refreshing, 2, 'down')
  assert.equal(recordView(failed), 'stale')
  assert.deepEqual(failed.data, { a: 1 })
  assert.equal(failed.supportCode, '')
})
t('record: a stale response is ignored', () => {
  let s = recordStarted(initialRecord())      // gen 1
  s = recordStarted(s)                        // gen 2
  const late = recordSucceeded(s, 1, { old: true })
  assert.equal(late, s)
  assert.equal(recordFailed(s, 1, 'x'), s)
  assert.deepEqual(recordSucceeded(s, 2, { new: true }).data, { new: true })
})
t('record: a successful retry clears the stale flag', () => {
  const good = recordSucceeded(recordStarted(initialRecord()), 1, { a: 1 })
  const failed = recordFailed(recordStarted(good), 2, 'down')
  const ok = recordSucceeded(recordStarted(failed), 3, { a: 2 })
  assert.equal(recordView(ok), 'ready')
  assert.equal(ok.error, '')
})
t('sort: ties broken by id, blanks last, input not mutated', () => {
  const rows = [{ id: 'b', d: '2026-01-02' }, { id: 'a', d: '2026-01-02' }, { id: 'z', d: null }, { id: 'c', d: '2026-01-01' }]
  const out = sortByKeyThenId(rows, (r) => r.d)
  assert.deepEqual(out.map((r) => r.id), ['c', 'a', 'b', 'z'])
  assert.equal(rows[0].id, 'b')
  assert.deepEqual(sortByKeyThenId([{ deal_id: 2, d: 'x' }, { deal_id: 1, d: 'x' }], (r) => r.d, 1, (r) => r.deal_id).map((r) => r.deal_id), [1, 2])
  assert.deepEqual(sortByKeyThenId(null, (r) => r), [])
})
t('exception status uses operator wording, never the raw enum', () => {
  assert.equal(exceptionStatusLabel('in_progress'), 'In progress')
  assert.equal(exceptionStatusLabel('escalated'), 'Escalated to owner')
  assert.equal(exceptionStatusLabel('weird_new_status'), 'Status unknown')
  assert.equal(exceptionStatusLabel(undefined), 'Status unknown')
})
t('resolve: only Complete is allowed without a note', () => {
  assert.equal(resolveBlockedReason('complete', ''), '')
  assert.notEqual(resolveBlockedReason('escalate', '   '), '')
  assert.notEqual(resolveBlockedReason('needs_more_info', undefined), '')
  assert.equal(resolveBlockedReason('escalate', 'called twice'), '')
})
t('sweep: unreadable result is not "nothing new"', () => {
  assert.match(sweepSummary({ raised: { a: 2, b: 1 } }), /^3 new exceptions raised/)
  assert.match(sweepSummary({ raised: { a: 1 } }), /^1 new exception raised/)
  assert.match(sweepSummary({ raised: {} }), /Nothing new/)
  assert.match(sweepSummary({}), /not readable/)
  assert.match(sweepSummary(null), /not readable/)
})
t('partner form: name required, numbers must be numbers and in range', () => {
  assert.deepEqual(validatePartner({ name: 'Acme' }), {})
  assert.ok(validatePartner({ name: ' ' }).name)
  assert.ok(validatePartner({ name: 'A', min_loan: 'abc' }).min_loan)
  assert.ok(validatePartner({ name: 'A', max_ltv_pct: '120' }).max_ltv_pct)
  assert.ok(validatePartner({ name: 'A', min_credit_score: '250' }).min_credit_score)
  assert.ok(validatePartner({ name: 'A', typical_close_days: '-3' }).typical_close_days)
  assert.deepEqual(validatePartner({ name: 'A', max_ltv_pct: '0', min_loan: '' }), {})
})
t('partner form: max below min loan is flagged on max', () => {
  const e = validatePartner({ name: 'A', min_loan: '500000', max_loan: '100000' })
  assert.ok(e.max_loan)
  assert.deepEqual(validatePartner({ name: 'A', min_loan: '100', max_loan: '100' }), {})
})
t('partner save: reason while busy or invalid, empty when good', () => {
  assert.notEqual(partnerSaveBlockedReason({ name: 'A' }, true), '')
  assert.notEqual(partnerSaveBlockedReason({ name: '' }, false), '')
  assert.equal(partnerSaveBlockedReason({ name: 'A' }, false), '')
})
t('track record: unknown is not "no deals", zero is not invented figures', () => {
  assert.equal(trackRecordText(undefined), 'Track record not available')
  assert.equal(trackRecordText({ deals_submitted: 0 }), 'no deals sent yet')
  assert.equal(trackRecordText({ deals_submitted: 3, approvals: 2, funded: 0, declines: 1 }), '3 sent · 2 approved · 0 funded · 1 declined')
  assert.match(trackRecordText({ deals_submitted: 3 }), /— approved · — funded · — declined/)
})
t('rooms: load state counts failed and pending', () => {
  const s = roomsLoadState({ a: {}, b: null }, ['a', 'b', 'c'])
  assert.deepEqual(s, { failed: 1, pending: 1, loaded: 1, complete: false })
  assert.equal(roomsLoadState({ a: {} }, ['a']).complete, true)
})
t('rooms: total is unknown when any room failed or is pending, never a partial sum', () => {
  const cnt = (r) => (r.buyer_matches || []).length
  assert.equal(roomTotal({ a: { buyer_matches: [1, 2] }, b: null }, ['a', 'b'], cnt), '—')
  assert.equal(roomTotal({ a: { buyer_matches: [1, 2] } }, ['a', 'b'], cnt), '—')
  assert.equal(roomTotal({ a: { buyer_matches: [1, 2] }, b: { buyer_matches: [] } }, ['a', 'b'], cnt), 2)
})
t('rooms: no deals is a real zero; a loaded room with nothing is zero', () => {
  assert.equal(roomTotal({}, [], () => 9), 0)
  assert.equal(roomTotal({ a: {} }, ['a'], (r) => (r.buyer_matches || []).length), 0)
})
t('rooms: at most ten ids, in order', () => {
  const deals = Array.from({ length: 14 }, (_, i) => ({ deal_id: 'd' + i }))
  const ids = roomIdsToLoad(deals)
  assert.equal(ids.length, 10)
  assert.equal(ids[0], 'd0')
  assert.deepEqual(roomIdsToLoad(null), [])
})

console.log(`${n} passed`)
