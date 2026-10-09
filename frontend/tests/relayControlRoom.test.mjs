// node --test frontend/tests/relayControlRoom.test.mjs
import test from 'node:test'
import assert from 'node:assert/strict'
import {
  POLL_MS, stateUrl, failedState, isWorking, headline, liveElapsedMinutes, actionsRunText, leaseText,
  createRefresher, leaseStatus,
} from '../src/utils/relayControlRoom.js'

test('manual refresh issues a fresh request even while a poll is in flight; stale reply dropped', async () => {
  const urls = []
  const resolvers = []
  const seen = []
  const refresh = createRefresher({
    fetchState: (url) => { urls.push(url); return new Promise(r => resolvers.push(r)) },
    onLoading: () => {}, onState: s => seen.push(s.id), onError: () => seen.push('err'),
  })
  const poll = refresh(false)
  const manual = refresh(true)
  assert.equal(urls.length, 2)
  assert.notEqual(urls[0], urls[1])
  // Replies must be well-formed states (normalizeState rejects anything else as a failure).
  const st = (id) => ({ id, available: true, worker: { state: 'idle' } })
  resolvers[1](st('newer')); await manual
  resolvers[0](st('older')); await poll
  assert.deepEqual(seen, ['newer'])
})

test('hung request times out into an error state and loading clears', async () => {
  const log = []
  const refresh = createRefresher({
    fetchState: () => new Promise(() => {}), timeoutMs: 5,
    onLoading: (v) => log.push(v), onState: () => log.push('state'), onError: s => log.push(s.available),
  })
  await refresh(true)
  assert.deepEqual(log, [true, false, false])
})

test('lease expires from server expiry against live clock; terminal never stale', () => {
  const w = { state: 'active', lease_expires_at: '2026-10-07T23:00:00Z' }
  assert.equal(leaseStatus(w, Date.parse('2026-10-07T22:50:00Z')).minutesLeft, 10)
  assert.equal(leaseStatus(w, Date.parse('2026-10-07T23:01:00Z')).expired, true)
  assert.equal(leaseStatus({ ...w, state: 'terminal' }, Date.parse('2026-10-08T00:00:00Z')).expired, false)
})

test('polls every 20 seconds', () => assert.equal(POLL_MS, 20000))

test('every fetch URL is unique (cache busting)', () => {
  const a = stateUrl(1000), b = stateUrl(1000)
  assert.notEqual(a, b)
  assert.match(a, /^\/god\/relay\/worker\?_=/)
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

import { upNextSplit, currentBlockers, olderBlockerCount, workerView, runOutcomesToday } from '../src/utils/relayControlRoom.js'

test('first screen: queued head is not a worker; stale queued and old blockers leave the live view', () => {
  const now = Date.parse('2026-10-08T15:45:00Z')
  const s = {
    available: true,
    worker: { state: 'queued', display: 'Queued - STALE', relay_run_id: 'q1', health: 'STALE', last_update_at: '2026-10-08T14:03:00Z' },
    queued_behind: [
      { state: 'queued', display: 'Queued', relay_run_id: 'q2', health: 'ok', last_update_at: '2026-10-08T15:40:00Z' },
      { state: 'queued', display: 'Queued - STALE', relay_run_id: 'q3', health: 'STALE', last_update_at: '2026-10-06T20:41:00Z' },
    ],
    history: [
      { state: 'terminal', result: 'BLOCKED', relay_run_id: 'b-new', blocked_reason: 'x', last_update_at: '2026-10-08T12:00:00Z' },
      { state: 'terminal', result: 'BLOCKED', relay_run_id: 'b-old', blocked_reason: 'y', last_update_at: '2026-10-06T12:00:00Z' },
      { state: 'terminal', result: 'COMPLETED', relay_run_id: 'c1', last_update_at: '2026-10-08T14:01:00Z' },
    ],
  }
  const w = workerView(s)
  assert.equal(w.state, 'idle')
  assert.equal(w.queuedHead.relay_run_id, 'q1')
  const split = upNextSplit(s)
  assert.deepEqual(split.fresh.map(u => u.relay_run_id), ['q2'])
  assert.deepEqual(split.stale.map(u => u.relay_run_id).sort(), ['q1', 'q3'])
  assert.deepEqual(currentBlockers(s, now).map(b => b.relay_run_id), ['b-new'])
  assert.equal(olderBlockerCount(s, now), 1)
  const o = runOutcomesToday(s, now)
  assert.equal(o.completed, 1); assert.equal(o.blocked, 1)
  // an accepted worker is still shown as the worker
  assert.equal(workerView({ ...s, worker: { state: 'active', display: 'Working' } }).state, 'active')
})
