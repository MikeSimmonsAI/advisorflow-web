/**
 * SINGLE-FEED GRID HELPERS, EXECUTED.   node tests/frontend/singleFeed.test.mjs
 * Synthetic data only.
 */
import assert from 'node:assert/strict'
import {
  appointmentsFromFeed, filterAppointments, locationsOf, agendaToday, upcomingOf,
  attentionOf, filterFeedEvents, makeRequestGuard, mergeOptions, describeFeed,
} from '../../frontend/src/pages/sales/sharedEvents.js'

let passed = 0
const failures = []
function check(name, fn) {
  try { fn(); passed += 1 } catch (e) { failures.push(name + ': ' + e.message) }
}

function ev(id, over = {}) {
  return {
    id: 'appointment:' + id, type: 'appointment', source_id: id, bucket: 'scheduled',
    status: 'scheduled', starts_at: '2026-10-06T15:00:00Z', ends_at: '2026-10-06T16:00:00Z',
    starts_at_local: '2026-10-06T10:00:00', ends_at_local: '2026-10-06T11:00:00',
    local_date: '2026-10-06', local_end_date: '2026-10-06', owner: { user_id: 'u1', name: 'A' },
    participant_user_ids: ['u1'],
    appointment: { id, title: 'Demo ' + id, meeting_type_id: 'mt1', location: 'Dallas',
      participants: [{ user_id: 'u1', full_name: 'Alice' }],
      confirmation_status: 'pending', sync_needs_attention: 0, outcome_state: {} },
    ...over,
  }
}

check('grid appointments are exactly the feed appointment events', () => {
  const feed = { events: [ev('a1'), { id: 'task:o1', type: 'task', bucket: 'task' }, ev('a2')] }
  const rows = appointmentsFromFeed(feed)
  assert.deepEqual(rows.map(r => r.event_id), ['appointment:a1', 'appointment:a2'])
  assert.deepEqual(rows.map(r => r.id), ['a1', 'a2'])
})

check('a stale legacy appointments list can never populate the grid', () => {
  const feed = { events: [ev('a1')], appointments: [{ id: 'ghost', starts_at_local: '2026-10-06T09:00:00' }] }
  assert.deepEqual(appointmentsFromFeed(feed).map(r => r.id), ['a1'])
  assert.deepEqual(appointmentsFromFeed(null), [])
  assert.deepEqual(appointmentsFromFeed({}), [])
})

check('event fields win over detail fields (status, bucket, local date)', () => {
  const e = ev('a1', { status: 'completed', bucket: 'completed', local_date: '2026-10-07' })
  e.appointment.status = 'scheduled'
  e.appointment.starts_at_local = '2026-10-06T00:00:00'
  const [r] = appointmentsFromFeed({ events: [e] })
  assert.equal(r.status, 'completed')
  assert.equal(r.bucket, 'completed')
  assert.equal(r.local_date, '2026-10-07')
})

check('cancelled hidden by default, shown on request; completed kept', () => {
  const feed = { events: [ev('a1', { bucket: 'cancelled', status: 'cancelled' }),
    ev('a2', { bucket: 'completed', status: 'completed' })] }
  assert.deepEqual(appointmentsFromFeed(feed).map(r => r.id), ['a2'])
  assert.deepEqual(appointmentsFromFeed(feed, { includeCancelled: true }).map(r => r.id), ['a1', 'a2'])
})

check('duplicate event ids render once', () => {
  assert.equal(appointmentsFromFeed({ events: [ev('a1'), ev('a1')] }).length, 1)
})

check('cross-midnight and month-boundary events land on the feed local date once', () => {
  const late = ev('late', { starts_at: '2026-11-01T04:30:00Z', starts_at_local: '2026-10-31T23:30:00',
    ends_at_local: '2026-11-01T00:30:00', local_date: '2026-10-31', local_end_date: '2026-11-01' })
  const rows = appointmentsFromFeed({ events: [late] })
  assert.equal(rows.length, 1)
  assert.equal(rows[0].local_date, '2026-10-31')
  assert.equal(rows[0].starts_at_local.slice(0, 10), '2026-10-31')
})

check('all-day event keeps its local day', () => {
  const [r] = appointmentsFromFeed({ events: [ev('ad', { all_day: true, local_date: '2026-10-09',
    starts_at_local: '2026-10-09T00:00:00' })] })
  assert.equal(r.starts_at_local.slice(0, 10), '2026-10-09')
})

check('filters narrow the set but facet options come from the unfiltered set', () => {
  const feed = { events: [ev('a1'),
    ev('a2', { appointment: { ...ev('a2').appointment, location: 'Austin', meeting_type_id: 'mt2' } })] }
  const all = appointmentsFromFeed(feed)
  const narrowed = filterAppointments(all, { loc: 'austin', typeIds: ['mt2'] })
  assert.deepEqual(narrowed.map(r => r.id), ['a2'])
  assert.deepEqual(locationsOf(all), ['Austin', 'Dallas'])
  assert.deepEqual(locationsOf(narrowed), ['Austin'])   // callers must pass the unfiltered set
  assert.deepEqual(mergeOptions(['Austin'], ['Dallas']), ['Dallas', 'Austin'])
})

check('member filter: null = everyone, [] = nobody', () => {
  const all = appointmentsFromFeed({ events: [ev('a1')] })
  assert.equal(filterAppointments(all, { memberIds: null }).length, 1)
  assert.equal(filterAppointments(all, { memberIds: [] }).length, 0)
  assert.equal(filterAppointments(all, { memberIds: ['u1'] }).length, 1)
  assert.equal(filterAppointments(all, { memberIds: ['zz'] }).length, 0)
})

check('panels derive from the same rows', () => {
  const all = appointmentsFromFeed({ events: [ev('a1'),
    ev('a2', { local_date: '2026-10-07', starts_at: '2026-10-07T15:00:00Z' })] })
  assert.deepEqual(agendaToday(all, '2026-10-06').map(r => r.id), ['a1'])
  assert.deepEqual(upcomingOf(all, '2026-10-06T12:00:00Z').map(r => r.id), ['a1', 'a2'])
  assert.deepEqual(upcomingOf(all, '2026-10-07T00:00:00Z').map(r => r.id), ['a2'])
  assert.deepEqual(attentionOf(all, '2026-10-06T12:00:00Z').map(i => i.kind),
    ['unconfirmed', 'unconfirmed'])
})

check('stale response guard: only the latest range paints', () => {
  const g = makeRequestGuard()
  const oct = g.begin(); const nov = g.begin()
  assert.equal(g.isCurrent(oct), false)
  assert.equal(g.isCurrent(nov), true)
})

check('partial / empty / error states', () => {
  assert.equal(describeFeed({ events: [], unscheduled: [], unavailable_sources: ['tasks'] }).state, 'error')
  assert.equal(describeFeed({ events: [ev('a1')], unavailable_sources: ['tasks'] }).state, 'partial')
  assert.equal(describeFeed({ events: [], unscheduled: [] }).state, 'empty')
  assert.equal(describeFeed(null).state, 'loading')
})

check('feed card chips filter client-side', () => {
  const evs = [ev('a1'), { id: 'task:o1', type: 'task', owner: { user_id: 'u2' }, participant_user_ids: [] }]
  assert.deepEqual(filterFeedEvents(evs, { types: ['task'] }).map(e => e.id), ['task:o1'])
  assert.deepEqual(filterFeedEvents(evs, { ownerIds: ['u1'] }).map(e => e.id), ['appointment:a1'])
  assert.equal(filterFeedEvents(evs, {}).length, 2)
})

if (failures.length) {
  console.error(failures.join('\n'))
  process.exit(1)
}
console.log('singleFeed: ' + passed + ' passed')
