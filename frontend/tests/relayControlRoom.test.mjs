// node --test frontend/tests/relayControlRoom.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  POLL_MS, stateUrl, failedState, isWorking, headline, liveElapsedMinutes, actionsRunText, leaseText,
} from '../src/utils/relayControlRoom.js'

test('polls every 20 seconds', () => assert.equal(POLL_MS, 20000))

test('every fetch URL is unique (cache busting)', () => {
  const a = stateUrl(1000), b = stateUrl(1000)
  assert.notEqual(a, b)
  assert.match(a, /^\/god\/relay\/state\?_=/)
})

test('failed fetch yields an unavailable state that is never Working', () => {
  const s = failedState('boom')
  assert.equal(s.available, false)
  assert.equal(isWorking(s), false)
  assert.equal(headline(s), 'Relay status unavailable')
})

test('only an available active Working worker counts as Working', () => {
  const w = { state: 'active', display: 'Working' }
  assert.equal(isWorking({ available: true, worker: w }), true)
  assert.equal(isWorking({ available: false, worker: w }), false)
  assert.equal(isWorking({ available: true, worker: { state: 'idle', display: 'Idle' } }), false)
  assert.equal(isWorking({ available: true, worker: { state: 'terminal', display: 'COMPLETED' } }), false)
})

test('live elapsed grows for active workers only', () => {
  const gen = '2026-10-06T22:00:00Z'
  const now = Date.parse(gen) + 5 * 60000
  assert.equal(liveElapsedMinutes({ state: 'active', elapsed_min: 3 }, gen, now), 8)
  assert.equal(liveElapsedMinutes({ state: 'terminal', elapsed_min: 3 }, gen, now), 3)
})

test('unmapped Actions run ID is shown honestly; lease is 30 minutes', () => {
  assert.equal(actionsRunText({ actions_run_id: null }), 'not mapped')
  assert.equal(actionsRunText({ actions_run_id: 42 }), '42')
  assert.match(leaseText({ elapsed_min: 10 }), /30-minute lease \(20 min left\)/)
})
