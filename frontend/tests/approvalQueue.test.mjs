// node --test frontend/tests/approvalQueue.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import { usd, decisionBody, approveBlockedReason, errorMessage, countsLine } from '../src/utils/approvalQueue.js'

test('money is a server display string; missing is a dash, never $0', () => {
  assert.equal(usd({ cents: 90005, display: '900.05' }), '$900.05')
  assert.equal(usd(null), '—')
  assert.equal(usd({ cents: 0 }), '—')
})

test('decision body carries the row version and a trimmed note', () => {
  assert.deepEqual(decisionBody({ version: 'v1.a.b' }, true, '  ok '), { approve: true, expected_version: 'v1.a.b', note: 'ok' })
  assert.equal(decisionBody({ version: 'v' }, 'yes', '').approve, false)
  assert.equal(decisionBody({ version: 'v' }, false, undefined).note, null)
})

test('blocked reasons', () => {
  assert.equal(approveBlockedReason({ status: 'pending', actionable: true }), null)
  assert.match(approveBlockedReason({ status: 'pending', actionable: false, blocker_text: 'Locked.' }), /Locked/)
  assert.match(approveBlockedReason({ status: 'approved' }), /no longer pending/)
  assert.ok(approveBlockedReason(null))
})

test('409 forces reload and is never success; 403 and others do not', () => {
  assert.equal(errorMessage({ status: 409, detail: 'changed' }).reload, true)
  assert.equal(errorMessage({ status: 404, detail: 'x' }).reload, true)
  assert.equal(errorMessage({ status: 403 }).reload, false)
  assert.equal(errorMessage({ status: 500, message: 'boom' }).text, 'boom')
})

test('counts never become zero when unavailable', () => {
  assert.equal(countsLine(null), 'Counts unavailable.')
  assert.equal(countsLine({ pending_count: null }), 'Counts unavailable.')
  assert.equal(countsLine({ pending_count: 0, actionable_count: 0, blocked_count: 0 }), '0 pending · 0 actionable · 0 blocked')
})
