// Pure helpers for the shared event feed (GET /sales/calendar/events).
// Web and mobile read the same contract; the server decides who may see what,
// so nothing here filters by permission - it only groups, labels and guards.

export const BUCKET_LABELS = {
  scheduled: 'Scheduled',
  task: 'Task',
  completed: 'Done',
  cancelled: 'Cancelled',
  unscheduled: 'Unscheduled',
}

export const TYPE_LABELS = { appointment: 'Appointment', task: 'Task', activity: 'Activity' }

export function eventQuery({ from, to, ownerIds, types, buckets }) {
  const q = new URLSearchParams({ date_from: from, date_to: to })
  if (ownerIds && ownerIds.length) q.set('owner_ids', ownerIds.join(','))
  if (types && types.length) q.set('types', types.join(','))
  if (buckets && buckets.length) q.set('buckets', buckets.join(','))
  return q.toString()
}

// Latest-request-wins: only the response whose token is still current may paint.
export function makeRequestGuard() {
  let current = 0
  return {
    begin() { current += 1; return current },
    isCurrent(token) { return token === current },
  }
}

// Group by the event's OWN local date (server-derived), never by re-parsing the
// UTC instant in the browser's zone, which would move late-evening events.
export function groupByLocalDate(events) {
  const days = new Map()
  for (const e of events || []) {
    const key = e.local_date || 'unknown'
    if (!days.has(key)) days.set(key, [])
    days.get(key).push(e)
  }
  return Array.from(days.entries()).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
}

// The options a filter offers come from server facets, merged with what is
// currently selected, so choosing a filter can never remove its own way back.
export function mergeOptions(selected, available) {
  const out = []
  const seen = new Set()
  for (const v of [...(available || []), ...(selected || [])]) {
    if (!seen.has(v)) { seen.add(v); out.push(v) }
  }
  return out
}

export function describeFeed(data) {
  if (!data) return { state: 'loading' }
  const down = data.unavailable_sources || []
  const total = (data.events || []).length + (data.unscheduled || []).length
  if (down.length && !total) return { state: 'error', sources: down }
  if (down.length || data.truncated) {
    return { state: 'partial', sources: down, truncated: !!data.truncated }
  }
  return { state: total ? 'ok' : 'empty' }
}

export function timeLabel(e) {
  if (e.all_day) return 'All day'
  const s = e.starts_at_local
  if (!s) return 'No time'
  const t = s.slice(11, 16)
  const en = e.ends_at_local ? e.ends_at_local.slice(11, 16) : null
  return en ? t + '–' + en : t
}
