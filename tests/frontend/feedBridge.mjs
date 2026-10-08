// Reads a feed JSON on stdin, prints what the grids / panels derive from it.
// Used by tests/test_shared_calendar_single_feed.py to cross-check the Python
// decision against the production JS helpers.   Synthetic data only.
import {
  appointmentsFromFeed, groupByLocalDate, agendaToday, upcomingOf, attentionOf,
} from '../../frontend/src/pages/sales/sharedEvents.js'

let raw = ''
process.stdin.on('data', c => { raw += c })
process.stdin.on('end', () => {
  const { feed, includeCancelled, todayLocal, nowUtc } = JSON.parse(raw)
  const appts = appointmentsFromFeed(feed, { includeCancelled: !!includeCancelled })
  const days = {}
  for (const a of appts) (days[a.local_date] = days[a.local_date] || []).push(a.event_id)
  console.log(JSON.stringify({
    grid: appts.map(a => ({ event_id: a.event_id, id: a.id, bucket: a.bucket,
      status: a.status, local_date: a.local_date })),
    days,
    agendaDays: groupByLocalDate(feed.events).map(([d, evs]) => [d, evs.map(e => e.id)]),
    today: agendaToday(appts, todayLocal).map(a => a.event_id),
    upcoming: upcomingOf(appts, nowUtc).map(a => a.event_id),
    attention: attentionOf(appts, nowUtc).map(a => a.kind),
  }))
})
