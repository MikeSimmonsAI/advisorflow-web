/**
 * Team Calendar — /sales/calendar
 *
 * WHAT THIS SCREEN ANSWERS: what is booked, across the Sales Workspace team.
 * That is all. "Who is free" is Team Availability's question and lives at
 * /sales/team. The two are deliberately NOT merged and there is no third
 * combined screen — a grid optimised to show commitments and a grid optimised
 * to show gaps want opposite defaults, and a screen that tries to do both ends
 * up poor at each.
 *
 * WHAT REPLACED WHAT. The previous version of this file was a 310-line list of
 * meetings grouped by day. It was honest and it worked, and it could not
 * answer "is Thursday afternoon clear" without the reader doing the work in
 * their head. This is the approved redesign: a real time grid, Day / Week /
 * Month / Agenda, the filter row, the roster, provider health, and the three
 * panels — all from ONE request, so no panel can contradict the grid beside it
 * mid-paint.
 *
 * FOUR VIEWS, ONE QUERY. Day, Week, Month and Agenda are the same endpoint
 * with a different date range. The server does not know or care which is on
 * screen. That is what stops four view modes becoming four subtly different
 * queries that disagree at the edges.
 *
 * TIME. Every rendered clock comes from a `*_local` field the server already
 * resolved in the brand's timezone, read through `calendarTime`. Nothing here
 * hands a naive instant to `new Date()` and hopes — see that module's header
 * for the bug that rule exists to prevent.
 *
 * PRIVACY. External busy blocks render as "Busy — external calendar" and
 * nothing else, because the interval is genuinely all the server stored. There
 * is no title in the payload to leak even by accident.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import { Card, Chip, Empty, ErrorBar, Metric, initials } from './parts'
import FindTeamTime from './FindTeamTime'
import BookAppointment from './BookAppointment'
import OutcomeDialog from './OutcomeDialog'
import {
  ymd, addDays, dayFromYmd, startOfWeek, startOfMonth, monthGridRange,
  sameDay, wallTime, wallMinutes, hourLabel, dayName, monthName, rangeLabel,
  placeSpan, assignLanes, zoneMinutes, zoneYmd, apptKind, CAL_LEGEND,
  spanTouchesDay, clampToDay,
} from './calendarTime'

// The visible band. 7am–8pm covers a selling day with room either side; the
// hours outside it are not hidden so much as shaded, and anything booked
// beyond them still renders clipped at the edge rather than vanishing.
const START_HOUR = 7
const END_HOUR = 20
const PX_PER_HOUR = 46

const VIEWS = [
  { key: 'day', label: 'Day' },
  { key: 'week', label: 'Week' },
  { key: 'month', label: 'Month' },
  { key: 'agenda', label: 'Agenda' },
]

/** The date range each view needs. The ONLY place view mode becomes dates. */
function rangeFor(view, anchor) {
  if (view === 'day') return { from: anchor, to: anchor }
  if (view === 'week') {
    const s = startOfWeek(anchor)
    return { from: s, to: addDays(s, 6) }
  }
  if (view === 'month') {
    const { first, last } = monthGridRange(anchor)
    return { from: first, to: last }
  }
  // Agenda looks forward rather than at a calendar block: "what is coming".
  return { from: anchor, to: addDays(anchor, 13) }
}

function stepFor(view) {
  return view === 'day' ? 1 : view === 'week' ? 7 : view === 'month' ? 0 : 14
}

// ── a block on the grid ─────────────────────────────────────────────────────

function EventBlock({ item, onOpen }) {
  const { appt, kind, top, height, lane, lanes, label, sub, readOnly } = item
  const width = 100 / lanes
  const style = {
    top, height,
    left: 'calc(' + (lane * width) + '% + 3px)',
    width: 'calc(' + width + '% - 6px)',
  }
  const cls = ['cal-ev', 't-' + kind]
  if (appt && appt.confirmation_status === 'pending') cls.push('is-unconfirmed')

  const flag = !appt ? null
    : appt.sync_conflicts ? '◆'
      : appt.sync_needs_attention ? '⚠'
        : appt.outcome_state?.needs_outcome ? '●' : null

  const title = [
    label,
    sub,
    appt && appt.confirmation_status === 'pending' ? 'Prospect has not confirmed' : null,
    appt && appt.sync_needs_attention ? 'A calendar could not be written' : null,
    appt && appt.sync_conflicts ? 'Changed outside EvoSys Pro' : null,
    appt && appt.outcome_state?.needs_outcome ? 'No outcome recorded' : null,
  ].filter(Boolean).join(' · ')

  if (readOnly) {
    return (
      <div className={cls.join(' ')} style={style} title={title}>
        <b>{label}</b>{sub ? <span>{sub}</span> : null}
      </div>
    )
  }
  return (
    <button className={cls.join(' ')} style={style} title={title}
            onClick={() => onOpen(appt)}>
      <b>{label}</b>
      {sub ? <span>{sub}</span> : null}
      {flag ? <i className="cal-flag" aria-hidden="true">{flag}</i> : null}
    </button>
  )
}

// ── the time grid (day and week) ────────────────────────────────────────────

function TimeGrid({ days, appts, layers, tz, showExternal, onOpen, nowLocal }) {
  const hours = []
  for (let h = START_HOUR; h < END_HOUR; h++) hours.push(h)
  const full = (END_HOUR - START_HOUR) * PX_PER_HOUR

  /**
   * One column's worth of blocks.
   *
   * Appointments are placed from `*_local` — the wall clock the SERVER
   * resolved in the brand's timezone — so the grid reads the same for a viewer
   * in Denver as for one in Dallas. The non-appointment layers arrive as naive
   * UTC instants and are placed through `zoneMinutes` in the brand's zone,
   * which is the same wall clock reached by a different route.
   */
  const columns = useMemo(() => days.map(d => {
    const key = ymd(d)
    const items = []

    appts.filter(a => String(a.starts_at_local || '').slice(0, 10) === key)
      .forEach(a => {
        const s = wallMinutes(a.starts_at_local)
        const e = wallMinutes(a.ends_at_local)
        items.push({
          appt: a, kind: apptKind(a),
          startMin: s, endMin: (e == null || e <= s) ? (s || 0) + 30 : e,
          label: a.meeting_type || a.title || 'Meeting',
          sub: a.opportunity_company || a.prospect?.company
            || a.prospect?.name || a.location || '',
        })
      })

    // PTO and external busy are not appointments and are never clickable —
    // there is nothing to open. They are drawn because the alternative is
    // blank space, and blank space in a calendar reads as bookable.
    layers.forEach(p => {
      // OVERLAP, not "starts or ends today". A Wed-to-Fri block of PTO has a
      // Thursday in the middle that belongs to neither endpoint, and asking
      // the endpoint question left that Thursday looking bookable.
      (p.time_off || []).forEach(t => {
        if (!spanTouchesDay(t.starts_at, t.ends_at, tz, key)) return
        const { startMin, endMin } = clampToDay(t.starts_at, t.ends_at, tz, key,
                                                END_HOUR)
        items.push({
          appt: null, kind: 'pto', readOnly: true,
          startMin, endMin,
          label: t.label || 'Time off', sub: p.full_name,
        })
      })
      if (!showExternal) return
      ;(p.external_busy || []).forEach(x => {
        if (!spanTouchesDay(x.starts_at, x.ends_at, tz, key)) return
        const { startMin, endMin } = clampToDay(x.starts_at, x.ends_at, tz, key,
                                                END_HOUR)
        items.push({
          appt: null, kind: 'external', readOnly: true,
          startMin, endMin,
          // There is no title in the payload. This is the whole label.
          label: 'Busy', sub: p.full_name,
        })
      })
    })

    const placed = assignLanes(items).map(it => {
      const box = placeSpan(it.startMin, it.endMin,
        { startHour: START_HOUR, endHour: END_HOUR, pxPerHour: PX_PER_HOUR })
      return box ? { ...it, ...box } : null
    }).filter(Boolean)

    return { day: d, key, items: placed }
  }), [days, appts, layers, tz, showExternal])

  const nowMin = nowLocal ? wallMinutes(nowLocal) : null
  const nowTop = nowMin == null ? null : (nowMin / 60 - START_HOUR) * PX_PER_HOUR

  return (
    <div className="cal-grid" style={{ '--cal-cols': days.length }}>
      <div className="cal-grid-in">
        <div className="cal-head">
          <div className="cal-head-c">{(tz || '').split('/').pop() || 'TIME'}</div>
          {days.map(d => (
            <div key={ymd(d)}
                 className={'cal-head-c' + (sameDay(d, new Date()) ? ' is-today' : '')}>
              {dayName(d)}
              <small>{d.getDate()}</small>
            </div>
          ))}
        </div>
        <div className="cal-body">
          <div className="cal-gut">
            {hours.map(h => <div key={h}>{hourLabel(h)}</div>)}
          </div>
          {columns.map(col => {
            const isWeekend = col.day.getDay() === 0 || col.day.getDay() === 6
            const isToday = sameDay(col.day, new Date())
            return (
              <div key={col.key}
                   className={'cal-col' + (isWeekend ? ' is-weekend' : '')}
                   style={{ height: full }}>
                {isToday && nowTop != null && nowTop >= 0 && nowTop <= full && (
                  <div className="cal-nowline" style={{ top: nowTop }} />
                )}
                {col.items.map((it, i) => (
                  <EventBlock key={(it.appt?.id || 'x') + '-' + i}
                              item={it} onOpen={onOpen} />
                ))}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

// ── month ───────────────────────────────────────────────────────────────────

function MonthGrid({ anchor, appts, onOpen, onPickDay }) {
  const { first } = monthGridRange(anchor)
  const cells = Array.from({ length: 42 }, (_, i) => addDays(first, i))
  const month = anchor.getMonth()

  const byDay = useMemo(() => {
    const m = new Map()
    appts.forEach(a => {
      const k = String(a.starts_at_local || '').slice(0, 10)
      if (!m.has(k)) m.set(k, [])
      m.get(k).push(a)
    })
    return m
  }, [appts])

  return (
    <div className="cal-month">
      <div className="cal-month-h">
        {['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
          .map(d => <span key={d}>{d}</span>)}
      </div>
      <div className="cal-month-b">
        {cells.map(d => {
          const key = ymd(d)
          const rows = byDay.get(key) || []
          const cls = ['cal-cell']
          if (d.getMonth() !== month) cls.push('is-out')
          if (sameDay(d, new Date())) cls.push('is-today')
          return (
            <div key={key} className={cls.join(' ')}>
              <div className="cal-cell-d">{d.getDate()}</div>
              {rows.slice(0, 3).map(a => (
                <button key={a.id} className={'cal-pill t-' + apptKind(a)}
                        title={a.title} onClick={() => onOpen(a)}>
                  {wallTime(a.starts_at_local)}{' '}
                  {a.opportunity_company || a.prospect?.company
                    || a.meeting_type || a.title}
                </button>
              ))}
              {rows.length > 3 && (
                <button className="cal-more" onClick={() => onPickDay(d)}>
                  +{rows.length - 3} more
                </button>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── agenda ──────────────────────────────────────────────────────────────────

function AgendaList({ appts, onOpen }) {
  const groups = useMemo(() => {
    const m = new Map()
    appts.forEach(a => {
      const k = String(a.starts_at_local || '').slice(0, 10)
      if (!m.has(k)) m.set(k, [])
      m.get(k).push(a)
    })
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [appts])

  if (!groups.length) {
    return (
      <Card>
        <Empty title="Nothing booked in this range">
          A real answer, not a placeholder — the schedule is genuinely empty for
          these dates. Step forward to find the next meeting.
        </Empty>
      </Card>
    )
  }

  return (
    <>
      {groups.map(([day, rows]) => (
        <Card key={day}
              title={dayFromYmd(day).toLocaleDateString(undefined,
                { weekday: 'long', month: 'long', day: 'numeric' }).toUpperCase()}
              sub={rows.length + (rows.length === 1 ? ' meeting' : ' meetings')}>
          {rows.map(a => (
            <div key={a.id} className="cal-row">
              <span className="cal-row-t">{wallTime(a.starts_at_local)}</span>
              <div className="cal-row-m">
                <b>{a.meeting_type || a.title}
                  {a.opportunity_company ? ' · ' + a.opportunity_company : ''}</b>
                <small>
                  {(a.participants || []).map(p => p.full_name).join(', ') || '—'}
                  {a.location ? ' · ' + a.location : ''}
                </small>
              </div>
              <div className="sw-flex" style={{ gap: 6 }}>
                {a.confirmation_status === 'confirmed'
                  ? <Chip tone="green">confirmed</Chip>
                  : a.confirmation_status === 'declined'
                    ? <Chip tone="red">declined</Chip>
                    : <Chip tone="amber">unconfirmed</Chip>}
                {a.outcome_state?.needs_outcome
                  ? <Chip tone="blue">outcome due</Chip> : null}
                <button className="sw-tiny" onClick={() => onOpen(a)}>Open</button>
              </div>
            </div>
          ))}
        </Card>
      ))}
    </>
  )
}

// ── the mini month in the rail ──────────────────────────────────────────────

function MiniMonth({ anchor, selected, appts, onPick }) {
  const [shown, setShown] = useState(() => startOfMonth(anchor))
  useEffect(() => { setShown(startOfMonth(anchor)) }, [anchor])

  const { first } = monthGridRange(shown)
  const cells = Array.from({ length: 42 }, (_, i) => addDays(first, i))
  const busy = useMemo(() => new Set(
    appts.map(a => String(a.starts_at_local || '').slice(0, 10))), [appts])

  return (
    <div className="cal-mini">
      <div className="cal-mini-h">
        <button onClick={() => setShown(new Date(shown.getFullYear(),
          shown.getMonth() - 1, 1, 12))} aria-label="Previous month">‹</button>
        <b>{monthName(shown)}</b>
        <button onClick={() => setShown(new Date(shown.getFullYear(),
          shown.getMonth() + 1, 1, 12))} aria-label="Next month">›</button>
      </div>
      <div className="cal-mini-g">
        {['S', 'M', 'T', 'W', 'T', 'F', 'S'].map((d, i) => <span key={i}>{d}</span>)}
        {cells.map(d => {
          const cls = []
          if (d.getMonth() !== shown.getMonth()) cls.push('is-out')
          if (sameDay(d, selected)) cls.push('is-sel')
          if (sameDay(d, new Date())) cls.push('is-today')
          if (busy.has(ymd(d))) cls.push('has-ev')
          return (
            <button key={ymd(d)} className={cls.join(' ')}
                    onClick={() => onPick(d)}>{d.getDate()}</button>
          )
        })}
      </div>
    </div>
  )
}

// ── the screen ──────────────────────────────────────────────────────────────

export default function TeamCalendar() {
  const nav = useNavigate()
  const [view, setView] = useState('week')
  const [anchor, setAnchor] = useState(() => new Date())
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)

  // Filters. `memberIds === null` means "everyone" and is NOT the same as an
  // empty array, which means "nobody selected" — the server treats them
  // differently and so must the UI, or unticking the last person silently
  // shows the whole team again.
  const [memberIds, setMemberIds] = useState(null)
  const [typeIds, setTypeIds] = useState([])
  const [loc, setLoc] = useState('')
  const [showExternal, setShowExternal] = useState(true)
  const [search, setSearch] = useState('')

  const [finding, setFinding] = useState(false)
  const [booking, setBooking] = useState(false)
  const [openAppt, setOpenAppt] = useState(null)
  const [outcomeFor, setOutcomeFor] = useState(null)
  const [flash, setFlash] = useState(null)

  const range = useMemo(() => rangeFor(view, anchor), [view, anchor])
  const from = ymd(range.from)
  const to = ymd(range.to)

  // A ref rather than state: it exists only to stop an in-flight response for
  // an old range painting over a newer one, and putting it in state would
  // re-render on every request for nothing.
  const reqId = useRef(0)

  const load = useCallback(async () => {
    const mine = ++reqId.current
    setLoading(true)
    setError(null)
    const q = new URLSearchParams({
      date_from: from, date_to: to, scope: 'team',
      include_external: showExternal ? 'true' : 'false',
    })
    if (memberIds) q.set('member_ids', memberIds.join(','))
    if (typeIds.length) q.set('meeting_type_ids', typeIds.join(','))
    if (loc) q.set('location', loc)
    try {
      const r = await api.get('/sales/calendar/view?' + q.toString())
      if (reqId.current !== mine) return
      setData(r)
    } catch (e) {
      if (reqId.current !== mine) return
      setError(e.message || 'Could not load the calendar.')
    } finally {
      if (reqId.current === mine) setLoading(false)
    }
  }, [from, to, memberIds, typeIds, loc, showExternal])

  useEffect(() => { load() }, [load])

  const isManager = data?.is_manager
  const tz = data?.brand_sales_org?.timezone
  const people = data?.people || []

  const appts = useMemo(() => {
    let rows = data?.appointments || []
    if (search.trim()) {
      // Client-side because it is a "find the one I'm thinking of" filter over
      // an already-loaded window, not a query. Sending it to the server would
      // make every keystroke a round trip for no better answer.
      const q = search.trim().toLowerCase()
      rows = rows.filter(a => [
        a.title, a.meeting_type, a.opportunity_company, a.location,
        a.prospect?.name, a.prospect?.company,
        ...(a.participants || []).map(p => p.full_name),
      ].filter(Boolean).some(v => String(v).toLowerCase().includes(q)))
    }
    return rows
  }, [data, search])

  const days = useMemo(() => {
    const n = view === 'day' ? 1 : 7
    const start = view === 'day' ? anchor : startOfWeek(anchor)
    return Array.from({ length: n }, (_, i) => addDays(start, i))
  }, [view, anchor])

  function step(dir) {
    if (view === 'month') {
      setAnchor(new Date(anchor.getFullYear(), anchor.getMonth() + dir, 1, 12))
      return
    }
    setAnchor(addDays(anchor, dir * stepFor(view)))
  }

  function toggleMember(id) {
    const current = memberIds || people.map(p => p.user_id)
    const next = current.includes(id)
      ? current.filter(x => x !== id)
      : [...current, id]
    // Back to "everyone" when every box is ticked, so the filter clears itself
    // rather than leaving a full-looking filter permanently applied.
    setMemberIds(next.length === people.length ? null : next)
  }

  const unconfirmed = appts.filter(a => a.confirmation_status === 'pending').length
  const syncTrouble = appts.filter(a => a.sync_needs_attention > 0).length
  const conflicts = appts.filter(a => a.sync_conflicts > 0).length
  const outcomesDue = appts.filter(a => a.outcome_state?.needs_outcome).length

  const ext = data?.external_visibility
  const sync = data?.sync_status

  async function afterChange(msg) {
    setFlash(msg)
    setOpenAppt(null)
    setOutcomeFor(null)
    await load()
  }

  return (
    <SalesShell
      title={isManager === false ? 'My Calendar' : 'Team Calendar'}
      subtitle={isManager === false
        ? 'Every meeting you are on, or that belongs to a deal you own.'
        : 'Every meeting your team has booked, with who is required and whether the prospect confirmed.'}
      actions={
        <div className="cal-bar">
          <div className="cal-seg">
            {VIEWS.map(v => (
              <button key={v.key} className={view === v.key ? 'is-on' : ''}
                      onClick={() => setView(v.key)}>{v.label}</button>
            ))}
          </div>
          <button className="sw-btn" onClick={() => step(-1)} aria-label="Previous">←</button>
          <button className="sw-btn" onClick={() => setAnchor(new Date())}>Today</button>
          <button className="sw-btn" onClick={() => step(1)} aria-label="Next">→</button>
          <button className="sw-btn" onClick={() => setFinding(true)}>Find Team Time</button>
          <button className="sw-btn sw-primary" onClick={() => setBooking(true)}>
            + New Appointment
          </button>
        </div>
      }
    >
      <ErrorBar error={error} onRetry={load} />

      {flash && (
        <div className="sw-note sw-flex sw-between">
          <span>{flash}</span>
          <button className="sw-tiny" onClick={() => setFlash(null)}>Dismiss</button>
        </div>
      )}

      {isManager === false && (
        <div className="sw-note">
          Showing your own meetings. The whole team's calendar is available to
          sales managers.
        </div>
      )}

      {/* ── the filter row ─────────────────────────────────────────────── */}
      <div className="cal-filters">
        <b style={{ fontSize: 12, marginRight: 2 }}>
          {view === 'month' ? monthName(anchor) : rangeLabel(range.from, range.to)}
        </b>
        <div className="sw-spacer" />
        <select className="sw-select" style={{ width: 168 }}
                value={memberIds ? 'some' : 'all'}
                onChange={e => setMemberIds(e.target.value === 'all'
                  ? null : people.map(p => p.user_id))}>
          <option value="all">All Team Members</option>
          <option value="some">
            {memberIds ? memberIds.length + ' selected' : 'Select in the roster'}
          </option>
        </select>
        <select className="sw-select" style={{ width: 176 }}
                value={typeIds[0] || ''}
                onChange={e => setTypeIds(e.target.value ? [e.target.value] : [])}>
          <option value="">All Appointment Types</option>
          {(data?.meeting_types || []).map(t => (
            <option key={t.id} value={t.id}>{t.name}</option>
          ))}
        </select>
        <select className="sw-select" style={{ width: 150 }}
                value={loc} onChange={e => setLoc(e.target.value)}>
          <option value="">All Locations</option>
          {(data?.locations || []).map(l => <option key={l} value={l}>{l}</option>)}
        </select>
        <input className="sw-input" style={{ width: 168 }} placeholder="Search calendar…"
               value={search} onChange={e => setSearch(e.target.value)} />
        <label className="cal-toggle"
               title="External calendars provide free/busy only — never event details.">
          <input type="checkbox" checked={showExternal}
                 onChange={e => setShowExternal(e.target.checked)} />
          Show external calendars
        </label>
        <button className="sw-btn" onClick={load} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {/* THE HONESTY BANNER. Rendered only when somebody's outside calendar
          could not actually be read, because "free" and "we could not check"
          must never look the same on a screen people book from. */}
      {ext && !ext.complete && ext.note && showExternal && (
        <div className="cal-unverified">
          <span aria-hidden="true">⚠</span>
          <span><b>Partly unverified.</b> {ext.note}</span>
        </div>
      )}

      <div className="sw-metrics">
        <Metric label="Meetings in range" value={appts.length}
                sub={rangeLabel(range.from, range.to)} />
        <Metric label="Unconfirmed" value={unconfirmed} attn={unconfirmed > 0}
                sub={unconfirmed ? 'prospect has not confirmed' : 'all confirmed'} />
        <Metric label="Calendar sync" value={syncTrouble + conflicts}
                attn={syncTrouble + conflicts > 0}
                sub={conflicts ? conflicts + ' changed outside EvoSys Pro'
                  : syncTrouble ? 'need attention' : 'healthy'} />
        <Metric label="Outcome not recorded" value={outcomesDue}
                attn={outcomesDue > 0}
                sub={outcomesDue ? 'past meetings with no verdict' : 'all recorded'} />
      </div>

      <div className="cal-layout">
        <div>
          {/* ── desktop: the grid ─────────────────────────────────────── */}
          <div className="cal-desk">
            {loading && !data ? <div className="sw-subtle">Loading…</div> : null}

            {data && (view === 'day' || view === 'week') && (
              <>
                <TimeGrid days={days} appts={appts} layers={people} tz={tz}
                          showExternal={showExternal}
                          nowLocal={data.now_local}
                          onOpen={setOpenAppt} />
                <Card bodyless>
                  <div className="cal-legend">
                    {CAL_LEGEND.map(l => (
                      <span key={l.kind}>
                        <i className={'cal-sw t-' + l.kind} aria-hidden="true" />
                        {l.label}
                      </span>
                    ))}
                  </div>
                </Card>
              </>
            )}

            {data && view === 'month' && (
              <MonthGrid anchor={anchor} appts={appts} onOpen={setOpenAppt}
                         onPickDay={d => { setAnchor(d); setView('day') }} />
            )}

            {data && view === 'agenda' && (
              <AgendaList appts={appts} onOpen={setOpenAppt} />
            )}
          </div>

          {/* ── phone: the field view ─────────────────────────────────── */}
          {/* The same payload. A seven-column time grid does not fit on a
              phone, and pinch-zooming to find your 2pm is not a feature — so
              at this width the grid is REPLACED, not squeezed. */}
          <div className="cal-mob">
            {!appts.length && data ? (
              <Card><Empty title="Nothing booked in this range" /></Card>
            ) : null}
            {appts.map(a => (
              <MobileCard key={a.id} a={a} onOpen={setOpenAppt}
                          onOutcome={() => setOutcomeFor(a)} />
            ))}
          </div>

          {/* ── the three panels ──────────────────────────────────────── */}
          <div className="cal-bottom">
            <Card title="TODAY'S AGENDA"
                  sub={data?.now_local
                    ? dayFromYmd(String(data.now_local).slice(0, 10))
                      .toLocaleDateString(undefined,
                        { weekday: 'long', month: 'short', day: 'numeric' })
                    : ''}>
              {(data?.agenda_today || []).length === 0 && (
                <div className="sw-subtle">Nothing scheduled today.</div>
              )}
              {(data?.agenda_today || []).map(a => (
                <div key={a.id} className="cal-row">
                  <span className="cal-row-t">{wallTime(a.starts_at_local)}</span>
                  <div className="cal-row-m">
                    <b>{a.meeting_type || a.title}</b>
                    <small>{a.opportunity_company || a.prospect?.company
                      || a.location || 'No company named'}</small>
                  </div>
                  {a.video?.join_url
                    ? <a className="sw-tiny sw-primary" href={a.video.join_url}
                         target="_blank" rel="noreferrer">Join</a>
                    : <button className="sw-tiny" onClick={() => setOpenAppt(a)}>
                        Details
                      </button>}
                </div>
              ))}
            </Card>

            <Card title="NEEDS ATTENTION"
                  sub={(data?.attention || []).length + ' item'
                    + ((data?.attention || []).length === 1 ? '' : 's')}>
              {(data?.attention || []).length === 0 && (
                <div className="sw-subtle">Nothing needs a human right now.</div>
              )}
              {(data?.attention || []).slice(0, 8).map((it, i) => (
                <div key={it.appointment_id + '-' + it.kind + '-' + i}
                     className={'cal-attn k-' + it.kind}>
                  <i className="cal-attn-b" aria-hidden="true" />
                  <div className="cal-row-m">
                    <b>{it.label}</b>
                    <small>{it.title}
                      {it.starts_at_local ? ' · ' + wallTime(it.starts_at_local) : ''}</small>
                  </div>
                  <button className="sw-tiny" onClick={() => {
                    const a = (data.appointments || [])
                      .find(x => x.id === it.appointment_id)
                    if (!a) return
                    if (it.kind === 'outcome') setOutcomeFor(a)
                    else setOpenAppt(a)
                  }}>
                    {it.kind === 'outcome' ? 'Record'
                      : it.kind === 'unconfirmed' ? 'Confirm' : 'Review'}
                  </button>
                </div>
              ))}
            </Card>

            <Card title="UPCOMING"
                  sub={view === 'agenda' ? 'next two weeks' : 'in this range'}>
              {(data?.upcoming || []).length === 0 && (
                <div className="sw-subtle">Nothing upcoming in this range.</div>
              )}
              {(data?.upcoming || []).slice(0, 8).map(a => (
                <div key={a.id} className="cal-row">
                  <span className="cal-row-t">
                    {dayFromYmd(String(a.starts_at_local).slice(0, 10))
                      .toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                  </span>
                  <div className="cal-row-m">
                    <b>{a.meeting_type || a.title}</b>
                    <small>{a.opportunity_company || a.prospect?.company || '—'}</small>
                  </div>
                  {a.confirmation_status === 'confirmed'
                    ? <Chip tone="green">confirmed</Chip>
                    : <Chip tone="amber">unconfirmed</Chip>}
                </div>
              ))}
            </Card>
          </div>
        </div>

        {/* ── the right rail ───────────────────────────────────────────── */}
        <aside className="cal-rail">
          <Card bodyless>
            <MiniMonth anchor={anchor} selected={anchor}
                       appts={data?.appointments || []}
                       onPick={d => { setAnchor(d); if (view === 'month') setView('day') }} />
          </Card>

          <Card title="TEAM MEMBERS" sub={people.length + ' in this brand'} bodyless
                right={memberIds
                  ? <button className="sw-tiny" onClick={() => setMemberIds(null)}>
                      Show all
                    </button>
                  : null}>
            <div className="cal-roster">
              {people.map(p => {
                const on = !memberIds || memberIds.includes(p.user_id)
                const unverified = p.external
                  && p.external.external_checked === false
                  && p.external.state !== 'not_connected'
                return (
                  <div key={p.user_id} className="cal-person">
                    <input type="checkbox" checked={on}
                           onChange={() => toggleMember(p.user_id)}
                           aria-label={'Show ' + p.full_name} />
                    <div className="sw-flex" style={{ gap: 9, minWidth: 0 }}>
                      <div className="sw-avatar">{initials(p.full_name)}</div>
                      <div className="cal-person-n">
                        <b>{p.full_name}</b>
                        <small>
                          <i className={'cal-dot'
                            + (p.status === 'in_meeting' ? ' is-busy' : '')}
                             aria-hidden="true" />
                          {p.status_label}
                          {!p.accepts_bookings ? ' · not bookable' : ''}
                        </small>
                      </div>
                    </div>
                    {unverified
                      ? <span title={p.external.message}
                              style={{ fontSize: 11, opacity: 0.75 }}>⚠</span>
                      : null}
                  </div>
                )
              })}
              {!people.length && (
                <div className="sw-card-b sw-subtle">
                  Nobody holds an active membership in this brand yet.
                </div>
              )}
            </div>
          </Card>

          {/* CALENDAR INTEGRATIONS. Every row carries a state, a reason and an
              action — a bare green dot beside a two-week-old sync is exactly
              what this panel is not allowed to be. */}
          <Card title="CALENDAR INTEGRATIONS"
                sub="Free/busy in, EvoSys Pro appointments out" bodyless
                right={<button className="sw-tiny"
                               onClick={() => nav('/sales/availability')}>Manage</button>}>
            <div className="cal-integ">
              {(sync?.mine || []).map(p => (
                <div key={p.provider} className="cal-int">
                  <div>
                    <b>{p.label}</b>
                    <small>
                      {p.state_label}
                      {p.last_sync_at ? ' · last sync ' + wallTime(p.last_sync_at) : ''}
                      {p.detail ? ' — ' + p.detail : ''}
                    </small>
                  </div>
                  {p.state === 'connected'
                    ? <Chip tone="green">connected</Chip>
                    : p.state === 'not_connected'
                      ? <Chip>not connected</Chip>
                      : <Chip tone="red">{p.state_label}</Chip>}
                </div>
              ))}
              {sync?.team && (
                <div className="cal-int">
                  <div>
                    <b>Across the team</b>
                    <small>
                      {sync.team.counts.connected} connected ·{' '}
                      {sync.team.counts.not_connected} not connected
                      {sync.team.counts.reauth_required
                        ? ' · ' + sync.team.counts.reauth_required + ' need reconnecting'
                        : ''}
                      {sync.team.counts.degraded
                        ? ' · ' + sync.team.counts.degraded + ' degraded' : ''}
                    </small>
                  </div>
                  {(sync.team.counts.reauth_required || sync.team.counts.degraded)
                    ? <Chip tone="amber">attention</Chip>
                    : <Chip tone="green">healthy</Chip>}
                </div>
              )}
            </div>
          </Card>

          <Card title="QUICK ACTIONS" bodyless>
            <div className="cal-qa">
              <button onClick={() => setFinding(true)}>
                <span aria-hidden="true">◷</span> Find Team Time
              </button>
              <button onClick={() => setBooking(true)}>
                <span aria-hidden="true">＋</span> New Appointment
              </button>
              <button onClick={() => nav('/sales/team')}>
                <span aria-hidden="true">▦</span> View Team Availability
              </button>
              <button onClick={() => nav('/sales/availability')}>
                <span aria-hidden="true">⚙</span> My Availability &amp; Calendars
              </button>
              {isManager && (
                <button onClick={() => setView('agenda')}>
                  <span aria-hidden="true">▤</span> Agenda for the next two weeks
                </button>
              )}
            </div>
          </Card>
        </aside>
      </div>

      {/* ── overlays ─────────────────────────────────────────────────────── */}
      {finding && (
        <FindTeamTime asModal
                      onClose={() => setFinding(false)}
                      onBooked={a => {
                        setFinding(false)
                        afterChange('Booked: ' + (a.title || 'meeting'))
                      }} />
      )}

      {booking && (
        <BookAppointment onClose={() => setBooking(false)}
                         onBooked={a => {
                           setBooking(false)
                           afterChange('Booked: ' + (a.title || 'meeting'))
                         }} />
      )}

      {openAppt && (
        <AppointmentDetail appt={openAppt}
                           isManager={isManager}
                           onClose={() => setOpenAppt(null)}
                           onOutcome={() => { setOutcomeFor(openAppt); setOpenAppt(null) }}
                           onChanged={afterChange}
                           onOpenDeal={id => nav('/sales/opportunities/' + id)} />
      )}

      {outcomeFor && (
        <OutcomeDialog appt={outcomeFor}
                       onClose={() => setOutcomeFor(null)}
                       onRecorded={() => afterChange('Outcome recorded.')} />
      )}

      <p className="sw-subtle" style={{ marginTop: 14 }}>
        A dashed edge means the prospect has not confirmed. <b>⚠</b> means a
        participant's calendar could not be written; <b>◆</b> means the event
        was changed outside EvoSys Pro; <b>●</b> means the meeting has happened
        and no outcome has been recorded. External calendars contribute
        free/busy only — never event details.
      </p>
    </SalesShell>
  )
}

// ── the phone card ──────────────────────────────────────────────────────────

/**
 * One appointment, with the actions a salesperson in a car actually needs.
 *
 * `tel:`, `sms:` and maps links rather than in-app actions, deliberately — the
 * phone's own dialler is better than anything this screen could build, and a
 * texting UI here would bypass the consent and eligibility rules the comms
 * layer enforces. Confirm / Reschedule / Cancel / Outcome go through the same
 * endpoints the desktop uses, so there is one scheduling truth and not two.
 */
function MobileCard({ a, onOpen, onOutcome }) {
  const who = a.prospect?.name || a.opportunity_company || a.prospect?.company
  return (
    <div className="cal-mob-card">
      <div className="cal-mob-when">
        {wallTime(a.starts_at_local)}
        <small>
          {dayFromYmd(String(a.starts_at_local).slice(0, 10))
            .toLocaleDateString(undefined,
              { weekday: 'short', month: 'short', day: 'numeric' })}
          {' · ' + a.duration_minutes + ' min'}
        </small>
      </div>
      <h4>{a.meeting_type || a.title}</h4>
      <p>
        {who || 'No prospect named'}
        {a.location ? ' · ' + a.location : ''}
        {a.meeting_provider ? ' · ' + a.meeting_provider : ''}
      </p>
      <div className="cal-mob-chips">
        {a.confirmation_status === 'confirmed'
          ? <Chip tone="green">confirmed</Chip>
          : a.confirmation_status === 'declined'
            ? <Chip tone="red">declined</Chip>
            : <Chip tone="amber">unconfirmed</Chip>}
        {(a.participants || []).map(p => (
          <Chip key={p.user_id}>{p.full_name}{p.is_required ? ' *' : ''}</Chip>
        ))}
      </div>
      <div className="cal-mob-act">
        {a.video?.join_url && (
          <a className="is-primary" href={a.video.join_url}
             target="_blank" rel="noreferrer">Join</a>
        )}
        {a.prospect?.phone && <a href={'tel:' + a.prospect.phone}>Call</a>}
        {a.prospect?.phone && <a href={'sms:' + a.prospect.phone}>Text</a>}
        {a.prospect?.email && <a href={'mailto:' + a.prospect.email}>Email</a>}
        {a.location && (
          <a href={'https://maps.google.com/?q=' + encodeURIComponent(a.location)}
             target="_blank" rel="noreferrer">Directions</a>
        )}
        <button onClick={() => onOpen(a)}>Details</button>
        {a.outcome_state?.needs_outcome && (
          <button className="is-primary" onClick={onOutcome}>Outcome</button>
        )}
      </div>
    </div>
  )
}

// ── the detail sheet ────────────────────────────────────────────────────────

/**
 * One appointment, and everything that can be done to it.
 *
 * Lives here rather than on its own route because a calendar is a screen you
 * act from without losing your place — navigating away to confirm a meeting
 * and navigating back to the same week is four clicks for a one-click job.
 */
function AppointmentDetail({ appt, isManager, onClose, onOutcome, onChanged, onOpenDeal }) {
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)
  const [reason, setReason] = useState('')

  async function act(label, fn) {
    setBusy(label); setError(null)
    try {
      await fn()
      onChanged(label + ' done.')
    } catch (e) {
      setError(e.message || 'That did not work.')
    } finally {
      setBusy(null)
    }
  }

  const conflicted = (appt.participants || []).filter(p => p.sync_conflict)

  return (
    <div className="sw-modal-back"
         onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="sw-modal" style={{ maxWidth: 620 }}>
        <div className="sw-card-h">
          <div>
            <h3>{(appt.meeting_type || appt.title || 'MEETING').toUpperCase()}</h3>
            <small>
              {dayFromYmd(String(appt.starts_at_local).slice(0, 10))
                .toLocaleDateString(undefined,
                  { weekday: 'long', month: 'long', day: 'numeric' })}
              {' · ' + wallTime(appt.starts_at_local)}
              {' – ' + wallTime(appt.ends_at_local)}
              {appt.timezone ? ' · ' + appt.timezone : ''}
            </small>
          </div>
          <div className="sw-spacer" />
          <button className="sw-btn" onClick={onClose}>Close</button>
        </div>

        <div className="sw-card-b">
          <ErrorBar error={error} />

          <div className="sw-flex"
               style={{ gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
            {appt.confirmation_status === 'confirmed'
              ? <Chip tone="green">confirmed</Chip>
              : appt.confirmation_status === 'declined'
                ? <Chip tone="red">declined</Chip>
                : <Chip tone="amber">unconfirmed</Chip>}
            <Chip>{appt.status}</Chip>
            {appt.outcome_state?.outcome_label
              ? <Chip tone="blue">{appt.outcome_state.outcome_label}</Chip> : null}
            {appt.outcome_state?.needs_outcome
              ? <Chip tone="amber">outcome not recorded</Chip> : null}
            {appt.meeting_provider ? <Chip>{appt.meeting_provider}</Chip> : null}
          </div>

          <div className="sw-info">
            <span>Prospect</span>
            <b>{appt.prospect?.name || '—'}
              {appt.prospect?.company ? ' · ' + appt.prospect.company : ''}</b>
          </div>
          {appt.location && (
            <div className="sw-info"><span>Location</span><b>{appt.location}</b></div>
          )}
          <div className="sw-info">
            <span>Participants</span>
            <b>{(appt.participants || []).map(p =>
              p.full_name + (p.is_required ? ' *' : '')).join(', ') || '—'}</b>
          </div>

          {/* Per-participant calendar truth, stated plainly. "On their Outlook
              calendar", "we emailed an invite" and "we could not reach it" are
              three different facts and the screen says which. */}
          <div className="sw-mt">
            {(appt.participants || []).map(p => (
              <div key={p.user_id} className="cal-row">
                <span className="cal-row-t">{p.full_name}</span>
                <div className="cal-row-m">
                  <b>{p.sync_label || p.sync_status}</b>
                  <small>{p.sync_conflict
                    ? p.sync_conflict_label + ' — ' + (p.sync_conflict_detail || '')
                    : (p.sync_error || p.role_label || '')}</small>
                </div>
                {p.sync_conflict
                  ? <Chip tone="red">conflict</Chip>
                  : p.needs_attention
                    ? <Chip tone="amber">attention</Chip>
                    : p.calendar_synced
                      ? <Chip tone="green">on calendar</Chip>
                      : <Chip>—</Chip>}
              </div>
            ))}
          </div>

          {conflicted.length > 0 && isManager && (
            <div className="sw-notbuilt sw-mt">
              <b>CHANGED OUTSIDE EVOSYS PRO</b>
              <p>
                Neither version was overwritten. If the new time was agreed with
                the prospect, reschedule here so every calendar and the
                prospect's confirmation follow it. Otherwise restore the EvoSys
                Pro time.
              </p>
              <div className="sw-flex" style={{ gap: 7, marginTop: 8 }}>
                {conflicted.map(p => (
                  <button key={p.user_id} className="sw-tiny" disabled={!!busy}
                          onClick={() => act('Restore', () => api.post(
                            '/sales/appointments/' + appt.id + '/resolve-conflict',
                            { user_id: p.user_id, action: 'push_evosys' }))}>
                    Restore on {p.full_name}'s calendar
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="sw-field sw-mt">
            <label>REASON — used for a cancellation notice</label>
            <input className="sw-input" value={reason}
                   onChange={e => setReason(e.target.value)} placeholder="Optional" />
          </div>

          <div className="sw-flex" style={{ gap: 7, flexWrap: 'wrap', marginTop: 12 }}>
            {appt.video?.join_url && (
              <a className="sw-btn sw-primary" href={appt.video.join_url}
                 target="_blank" rel="noreferrer">Join</a>
            )}
            {appt.confirmation_status !== 'confirmed' && (
              <button className="sw-btn" disabled={!!busy}
                      onClick={() => act('Confirmation', () => api.post(
                        '/sales/appointments/' + appt.id + '/confirmation',
                        { confirmation_status: 'confirmed', source: 'staff_manual' }))}>
                Mark confirmed
              </button>
            )}
            <button className="sw-btn" disabled={!!busy}
                    onClick={() => act('Invitation', () => api.post(
                      '/sales/appointments/' + appt.id + '/resend-invitation', {}))}>
              Send confirmation
            </button>
            <button className="sw-btn" disabled={!!busy}
                    onClick={() => act('Resync', () => api.post(
                      '/sales/appointments/' + appt.id + '/resync', {}))}>
              Retry calendar sync
            </button>
            <button className="sw-btn" disabled={!!busy}
                    onClick={() => act('Reconcile', () => api.post(
                      '/sales/appointments/' + appt.id + '/reconcile', {}))}>
              Check external calendars
            </button>
            <button className="sw-btn" onClick={onOutcome}>Record outcome</button>
            <button className="sw-btn" disabled={!!busy}
                    onClick={() => act('Cancellation', () => api.post(
                      '/sales/appointments/' + appt.id + '/cancel',
                      { reason: reason || undefined }))}>
              Cancel meeting
            </button>
            {appt.opportunity_id && (
              <button className="sw-btn" onClick={() => onOpenDeal(appt.opportunity_id)}>
                Open deal
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
