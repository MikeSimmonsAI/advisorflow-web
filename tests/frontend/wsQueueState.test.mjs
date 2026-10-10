/** Pure proof for the Callback Center queue (SYNTHETIC; no DOM):
 *  node tests/frontend/wsQueueState.test.mjs */
import {
  statusLabel, itemLabel, assigneeLabel, sortQueue, initialQueue, loadStarted,
  loadSucceeded, loadFailed, queueView, bucketCount, truncationNote,
} from '../../frontend/src/pages/wholesale/wsQueueState.js'

let passed = 0, failed = 0
function check(name, cond) { if (cond) passed += 1; else { failed += 1; console.log('FAIL:', name) } }

check('due reads Scheduled', statusLabel('due') === 'Scheduled')
check('cancelled reads Cancelled', statusLabel('cancelled') === 'Cancelled')
check('unknown is not guessed', statusLabel('weird') === 'Unknown (weird)' && statusLabel(null) === 'Unknown')
check('overdue open is Missed', itemLabel({ status: 'due', bucket: 'overdue' }) === 'Missed — still open')
check('completed is not Missed', itemLabel({ status: 'completed', bucket: 'completed' }) === 'Completed')
check('assignee named', assigneeLabel({ assigned_to_name: 'Pat' }) === 'Assigned to Pat')
check('assignee id only', assigneeLabel({ assigned_to_id: 'u1' }) === 'Assigned (name not available)')
check('unassigned', assigneeLabel({}) === 'Unassigned')

const tie = [
  { id: 'b', due_at: '2026-10-07T10:00:00Z', created_at: '2026-10-01T00:00:00Z' },
  { id: 'a', due_at: '2026-10-07T10:00:00Z', created_at: '2026-10-01T00:00:00Z' },
  { id: 'c', due_at: null, created_at: null },
  { id: 'd', due_at: '2026-10-06T10:00:00Z', created_at: null },
  { id: 'e', due_at: '2026-10-07T10:00:00Z', created_at: '2026-09-01T00:00:00Z' },
]
const order = sortQueue(tie, 'due_now').map(i => i.id).join('')
check('ties break by created then id, null due last', order === 'deabc')
check('sort is input-order independent', sortQueue(tie.slice().reverse(), 'due_now').map(i => i.id).join('') === order)
check('sort does not mutate', tie[0].id === 'b')
const done = sortQueue([
  { id: 'x', completed_at: '2026-10-01T00:00:00Z' },
  { id: 'y', completed_at: '2026-10-03T00:00:00Z' },
  { id: 'w', completed_at: '2026-10-03T00:00:00Z' },
  { id: 'z', completed_at: null },
], 'completed').map(i => i.id).join('')
check('completed newest first, ties by id, unknown last', done === 'wyxz')

let s = initialQueue()
check('initial is loading', queueView(s, 'due_now') === 'loading')
s = loadStarted(s); const g1 = s.latest
s = loadFailed(s, g1, 'No access [forbidden]')
check('first failure is error, no data', queueView(s, 'due_now') === 'error' && s.error.includes('forbidden'))
s = loadStarted(s); const g2 = s.latest
s = loadSucceeded(s, g2, { buckets: { due_now: [], completed: [{ id: '1' }] }, counts: { due_now: 0, completed: 3 } })
check('empty bucket is empty', queueView(s, 'due_now') === 'empty')
check('populated is ready', queueView(s, 'completed') === 'ready')
check('count shown', bucketCount(s, 'completed') === 3)
check('count unknown is a dash', bucketCount(initialQueue(), 'completed') === '—')
check('truncation said', truncationNote(s, 'completed') === 'Showing the 1 most recent of 3.')
check('no truncation note when whole', truncationNote(s, 'due_now') === '')
s = loadStarted(s); const older = s.latest
check('refresh keeps data and says refreshing', queueView(s, 'completed') === 'refreshing' && s.data !== null)
s = loadStarted(s); const newer = s.latest
const before = s
s = loadSucceeded(s, older, { buckets: { completed: [] }, counts: { completed: 0 } })
check('stale success ignored', s === before && s.data.counts.completed === 3)
s = loadFailed(s, older, 'late failure')
check('stale failure ignored', s === before && s.error === '')
s = loadFailed(s, newer, 'Server down [e_500]')
check('failed refresh keeps rows as stale', queueView(s, 'completed') === 'stale' && s.data.counts.completed === 3)
s = loadStarted(s); s = loadSucceeded(s, s.latest, { buckets: { completed: [{ id: '1' }] }, counts: { completed: 1 } })
check('retry recovers', queueView(s, 'completed') === 'ready' && s.error === '' && !s.failedRefresh)

console.log(`wsQueueState: ${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
