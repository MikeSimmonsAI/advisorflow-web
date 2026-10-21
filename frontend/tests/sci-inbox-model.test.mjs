import assert from 'node:assert/strict'
import test from 'node:test'
import { integerValue, needsAttention, queueMatches, sortInbox, statusMatches } from '../src/pages/program/sci/inboxModel.js'
const at = (id, status, cls, when, sla_breached = false) =>
  ({id, status, class: cls, received_at: when, sla_breached})
const rows = [
  at('closed', 'closed', 'hot', '2026-10-20T11:00:00Z'),
  at('old-hot', 'new', 'hot', '2026-10-18T11:00:00Z'),
  at('newer', 'new', 'active', '2026-10-20T12:00:00Z'),
  at('sla', 'new', 'hot', '2026-10-17T12:00:00Z', true),
  at('follow', 'opened', 'active', '2026-10-20T13:00:00Z'),
]
test('opens with SLA, HOT new, then other new regardless of date', () => {
  assert.deepEqual(sortInbox(rows).map(r => r.id), ['sla', 'old-hot', 'newer', 'follow', 'closed'])
  assert.deepEqual(sortInbox(rows, 'recent').map(r => r.id),
    ['follow', 'newer', 'closed', 'old-hot', 'sla'])
  assert.equal(rows[0].id, 'closed') // sorting never mutates loaded records
})
test('queues distinguish new work and completed work', () => {
  assert.deepEqual(rows.filter(r => queueMatches(r, 'new')).map(r => r.id), ['old-hot', 'newer', 'sla'])
  assert.deepEqual(rows.filter(r => queueMatches(r, 'hot')).map(r => r.id), ['old-hot', 'sla'])
  assert.deepEqual(rows.filter(r => queueMatches(r, 'followup')).map(r => r.id), ['follow'])
  assert.deepEqual(rows.filter(r => queueMatches(r, 'handled')).map(r => r.id), ['closed'])
  assert.deepEqual(rows.filter(r => queueMatches(r, 'attention')).map(r => r.id), ['old-hot', 'newer', 'sla', 'follow'])
  assert.equal(statusMatches(rows[0], 'open'), false)
  assert.equal(statusMatches(rows[4], 'opened'), true)
  assert.equal(needsAttention(rows[2]), true)
})
test('missing live analytics values are unknown, not fabricated zeros', () => {
  assert.equal(integerValue(null, 'delivered'), null)
  assert.equal(integerValue({}, 'replies'), null)
  assert.equal(integerValue({delivered:{value:0}}, 'delivered'), 0)
  assert.equal(integerValue({delivered:{value:128}}, 'delivered'), 128)
})
