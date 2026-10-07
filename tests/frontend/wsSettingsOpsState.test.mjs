/* Settings / Operations recovery helpers (pure, dependency-free):
 *   node tests/frontend/wsSettingsOpsState.test.mjs */
import assert from 'node:assert/strict'
import {
  initialRecord, recordStarted, recordSucceeded, recordFailed, recordView,
  pilotSaveBlockedReason, withCurrentOption, distributionLabel, smsReadinessLabel,
} from '../../frontend/src/pages/wholesale/wsListState.js'

let n = 0
const t = (name, fn) => { fn(); n += 1; console.log('ok -', name) }

t('pilot save: blocked while a save is in flight', () => {
  assert.match(pilotSaveBlockedReason('ready', true), /already in progress/)
})
t('pilot save: blocked while strategies load or failed, with a reason', () => {
  assert.match(pilotSaveBlockedReason('loading', false), /still loading/)
  assert.match(pilotSaveBlockedReason('error', false), /could not be loaded/)
  assert.match(pilotSaveBlockedReason('stale', false), /could not be loaded/)
})
t('pilot save: allowed when strategies are ready or refreshing', () => {
  assert.equal(pilotSaveBlockedReason('ready', false), '')
  assert.equal(pilotSaveBlockedReason('refreshing', false), '')
})
t('current option: a saved id missing from the list stays selectable', () => {
  const o = withCurrentOption(null, 'u1', 'Current assignee (name unavailable)')
  assert.equal(o.length, 1)
  assert.equal(o[0].id, 'u1')
  assert.equal(o[0].unavailable, true)
})
t('current option: no duplicate when the list has it; none when nothing saved', () => {
  assert.equal(withCurrentOption([{ id: 7, name: 'A' }], '7', 'x').length, 1)
  assert.equal(withCurrentOption([], '', 'x').length, 0)
  assert.equal(withCurrentOption(undefined, null, 'x').length, 0)
})
t('distribution: unknown is never OFF', () => {
  assert.match(distributionLabel(null), /unavailable/)
  assert.doesNotMatch(distributionLabel(null), /OFF/)
  assert.match(distributionLabel({ auto_distribution: false }), /OFF/)
  assert.match(distributionLabel({ auto_distribution: true }), /ON/)
})
t('sms readiness: unknown is not "Not sending", and only a loaded status says Ready', () => {
  assert.equal(smsReadinessLabel('loading', null), 'Checking…')
  assert.equal(smsReadinessLabel('error', null), 'Status unavailable')
  assert.equal(smsReadinessLabel('ready', { can_send: false }), 'Not sending')
  assert.match(smsReadinessLabel('ready', { can_send: true }), /^Ready/)
})
t('record reuse: failed first load is error with support code; refresh failure is stale', () => {
  const err = recordFailed(recordStarted(initialRecord()), 1, 'down', 'C-9')
  assert.equal(recordView(err), 'error')
  assert.equal(err.supportCode, 'C-9')
  const good = recordSucceeded(recordStarted(initialRecord()), 1, { items: [1] })
  const stale = recordFailed(recordStarted(good), 2, 'down', '')
  assert.equal(recordView(stale), 'stale')
  assert.deepEqual(stale.data, { items: [1] })
})
t('record reuse: a late response from an older request is ignored', () => {
  const s1 = recordStarted(initialRecord())
  const s2 = recordStarted(s1)
  assert.equal(recordSucceeded(s2, 1, { old: true }), s2)
  assert.equal(recordFailed(s2, 1, 'late', ''), s2)
})

console.log(`${n} passed`)
