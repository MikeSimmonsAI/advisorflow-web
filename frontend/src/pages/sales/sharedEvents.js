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

// ── appointments for the grids ──────────────────────────────────────────────
// Month / week / day / agenda, the side panels and the metrics all read the
// appointment events of the ONE feed. The roster/availability route carries no
// appointment list at all, so it cannot paint a second, conflicting grid.

// Each appointment event carries the read-only detail the grid cards need under
// `appointment`. Identity, status, bucket, instants and local dates come from
// the event itself, so every view places the same row on the same day.
export function appointmentsFromFeed(feed, { includeCancelled = false } = {}) {
  const out = []
  const seen = new Set()
  for (const e of (feed && feed.events) || []) {
    if (e.type !== 'appointment' || !e.appointment) continue
    if (seen.has(e.id)) continue            // one event, one card, whatever the payload
    seen.add(e.id)
    if (e.bucket === 'cancelled' && !includeCancelled) continue
    out.push({
      ...e.appointment,
      event_id: e.id,
      bucket: e.bucket,
      status: e.status,
      starts_at: e.starts_at,
      ends_at: e.ends_at,
      starts_at_local: e.starts_at_local,
      ends_at_local: e.ends_at_local,
      local_date: e.local_date,
      local_end_date: e.local_end_date,
    })
  }
  return out
}

// Client-side narrowing of the already-loaded, already-authorised set. Facet
// options are computed from the UNFILTERED set so a filter never removes the
// way back out of itself.
export function filterAppointments(appts, { memberIds, typeIds, loc, search } = {}) {
  let rows = appts || []
  if (memberIds) {
    const keep = new Set(memberIds)
    rows = rows.filter(a => (a.participants || []).some(p => keep.has(p.user_id)))
  }
  if (typeIds && typeIds.length) rows = rows.filter(a => typeIds.includes(a.meeting_type_id))
  if (loc) {
    const l = loc.toLowerCase()
    rows = rows.filter(a => String(a.location || '').toLowerCase().includes(l))
  }
  const q = (search || '').trim().toLowerCase()
  if (q) {
    rows = rows.filter(a => [
      a.title, a.meeting_type, a.opportunity_company, a.location,
      a.prospect?.name, a.prospect?.company,
      ...(a.participants || []).map(p => p.full_name),
    ].filter(Boolean).some(v => String(v).toLowerCase().includes(q)))
  }
  return rows
}

export function locationsOf(appts) {
  return Array.from(new Set((appts || []).map(a => (a.location || '').trim())
    .filter(Boolean))).sort()
}

export function agendaToday(appts, todayLocal) {
  return (appts || []).filter(a => a.local_date === todayLocal)
}

export function upcomingOf(appts, nowUtc, limit = 12) {
  return (appts || []).filter(a => a.status === 'scheduled' && a.starts_at >= nowUtc)
    .slice(0, limit)
}

export function attentionOf(appts, nowUtc) {
  const out = []
  for (const a of appts || []) {
    if (a.status === 'cancelled') continue
    const base = { appointment_id: a.id, title: a.title, starts_at_local: a.starts_at_local }
    if (a.confirmation_status === 'pending' && a.starts_at >= nowUtc) {
      out.push({ ...base, kind: 'unconfirmed', label: 'Prospect has not confirmed' })
    }
    if (a.sync_needs_attention) {
      out.push({ ...base, kind: 'sync', label: a.sync_needs_attention
        + ' calendar' + (a.sync_needs_attention === 1 ? '' : 's') + ' could not be written' })
    }
    if (a.sync_conflicts) out.push({ ...base, kind: 'conflict', label: 'Changed outside EvoSys Pro' })
    if (a.outcome_state && a.outcome_state.needs_outcome) {
      out.push({ ...base, kind: 'outcome', label: 'No outcome recorded' })
    }
  }
  return out
}

// Narrow feed rows by the Tasks & Activity card's own type/owner chips.
export function filterFeedEvents(events, { types, ownerIds } = {}) {
  return (events || []).filter(e =>
    (!types || !types.length || types.includes(e.type)) &&
    (!ownerIds || !ownerIds.length ||
      (e.owner && ownerIds.includes(e.owner.user_id)) ||
      (e.participant_user_ids || []).some(id => ownerIds.includes(id))))
}

export function timeLabel(e) {
  if (e.all_day) return 'All day'
  const s = e.starts_at_local
  if (!s) return 'No time'
  const t = s.slice(11, 16)
  const en = e.ends_at_local ? e.ends_at_local.slice(11, 16) : null
  return en ? t + '–' + en : t
}
