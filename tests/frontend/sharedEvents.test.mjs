/**
 * SHARED EVENT FEED HELPERS, EXECUTED.   node tests/frontend/sharedEvents.test.mjs
 * Synthetic data only.
 */
import assert from 'node:assert/strict'
import {
  eventQuery, makeRequestGuard, groupByLocalDate, mergeOptions, describeFeed,
  timeLabel,
} from '../../frontend/src/pages/sales/sharedEvents.js'

let passed = 0
const failures = []
function check(name, fn) {
  try { fn(); passed += 1 } catch (e) { failures.push(name + ': ' + e.message) }
}

check('query carries only chosen filters', () => {
  assert.equal(eventQuery({ from: '2026-10-05', to: '2026-10-11' }),
    'date_from=2026-10-05&date_to=2026-10-11')
  assert.match(eventQuery({ from: 'a', to: 'b', types: ['task'], ownerIds: ['u1', 'u2'] }),
    /owner_ids=u1%2Cu2.*types=task|types=task.*owner_ids/)
})

check('stale response cannot overwrite a newer one', () => {
  const g = makeRequestGuard()
  const first = g.begin()
  const second = g.begin()
  assert.equal(g.isCurrent(first), false)
  assert.equal(g.isCurrent(second), true)
})

check('groups by server local date, sorted, without re-zoning', () => {
  const days = groupByLocalDate([
    { id: 'b', local_date: '2026-10-06', starts_at: '2026-10-07T03:30:00Z' },
    { id: 'a', local_date: '2026-10-05' },
    { id: 'c', local_date: '2026-10-06' },
  ])
  assert.deepEqual(days.map(([d, e]) => [d, e.map(x => x.id)]),
    [['2026-10-05', ['a']], ['2026-10-06', ['b', 'c']]])
})

check('selected filter stays offered when facets no longer list it', () => {
  assert.deepEqual(mergeOptions(['task'], ['activity']), ['activity', 'task'])
  assert.deepEqual(mergeOptions([], undefined), [])
})

check('feed states: loading, empty, partial, error, ok', () => {
  assert.equal(describeFeed(null).state, 'loading')
  assert.equal(describeFeed({ events: [], unscheduled: [] }).state, 'empty')
  assert.equal(describeFeed({ events: [{}], unavailable_sources: ['tasks'] }).state, 'partial')
  assert.equal(describeFeed({ events: [{}], truncated: true }).state, 'partial')
  assert.equal(describeFeed({ events: [], unscheduled: [], unavailable_sources: ['tasks'] }).state,
    'error')
  assert.equal(describeFeed({ events: [{}] }).state, 'ok')
})

check('time label never invents a time', () => {
  assert.equal(timeLabel({ all_day: true }), 'All day')
  assert.equal(timeLabel({ starts_at_local: null }), 'No time')
  assert.equal(timeLabel({ starts_at_local: '2026-10-06T09:00:00' }), '09:00')
  assert.equal(timeLabel({ starts_at_local: '2026-10-06T09:00:00',
    ends_at_local: '2026-10-06T09:30:00' }), '09:00–09:30')
})

if (failures.length) {
  console.error(failures.join('\n'))
  process.exit(1)
}
console.log('sharedEvents: ' + passed + ' passed')
