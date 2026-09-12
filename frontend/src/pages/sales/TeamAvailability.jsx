/**
 * Team Availability — /sales/team
 *
 * WHAT THIS SCREEN ANSWERS: who is free, who is busy, and when can every
 * required person meet. Team Calendar answers "what is booked" and lives at
 * /sales/calendar. They stay two screens — same reason as stated over there:
 * a grid tuned to show commitments and a grid tuned to show gaps want opposite
 * defaults, and merging them was explicitly out of scope.
 *
 * WHAT CHANGED, AND WHY IT MATTERED
 * ---------------------------------
 * The previous grid drew two things: free time and meetings. Every OTHER
 * reason a person was unavailable — lunch, PTO, a block they set themselves,
 * a meeting on their Outlook calendar — rendered as blank space, and blank
 * space in an availability grid reads as bookable. So a rep would look at a
 * colleague's Tuesday afternoon, see white, and be refused when they booked
 * it, with no way to know why.
 *
 * This draws all of it, each band distinct, because "at lunch" and "on leave
 * all week" lead to different decisions:
 *
 *   Available · Customer meeting · Internal meeting · Lunch / blocked ·
 *   PTO / time off · External calendar (busy)
 *
 * AND IT SAYS WHAT IT COULD NOT SEE. If somebody's connected calendar failed
 * to read, their column is marked unverified. "Free" and "we could not check"
 * must never look the same on a screen people book from — that is the
 * difference between a scheduler and a guess.
 *
 * PRIVACY, ENFORCED SERVER-SIDE
 * -----------------------------
 * A rep needs to see that a colleague is occupied — that is the whole point of
 * the screen — but gets the TITLE only for meetings they are on themselves.
 * The server sends the literal string "Busy" rather than a title this viewer
 * may not read, and external busy carries no title at all because none was
 * ever stored. Neither rule can be undone in the browser, which is why they
 * live where they do.
 *
 * PER-PERSON TIMEZONES. Each column is that person's OWN working day. Blocks
 * are placed with `zoneMinutes` in their zone, not the viewer's and not the
 * brand's, because a human's day starts when their day starts.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import FindTeamTime from './FindTeamTime'
import BookAppointment from './BookAppointment'
import { Card, Chip, Empty, ErrorBar, initials } from './parts'
import {
  ymd, addDays, dayFromYmd, hourLabel, placeSpan, assignLanes,
  zoneMinutes, zoneYmd, zoneTime, AV_LEGEND, spanTouchesDay, clampToDay,
} from './calendarTime'

const START_HOUR = 7
const END_HOUR = 19
const PX_PER_HOUR = 46

export default function TeamAvailability() {
  const nav = useNavigate()
  const [day, setDay] = useState(() => ymd(new Date()))
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [showExternal, setShowExternal] = useState(true)
  const [only, setOnly] = useState(null)       // null = everyone
  const [booked, setBooked] = useState(null)
  const [booking, setBooking] = useState(false)
  const [findAsModal, setFindAsModal] = useState(false)

  const load = useCallback(async (d) => {
    setLoading(true); setError(null)
    try {
      setData(await api.get('/sales/availability/team?day=' + d))
    } catch (e) {
      setError(e.message || 'Could not load team availability.')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load(day) }, [load, day])

  function shift(n) { setDay(ymd(addDays(dayFromYmd(day), n))) }

  const members = useMemo(() => {
    const all = data?.members || []
    return only ? all.filter(m => only.includes(m.user_id)) : all
  }, [data, only])

  const hours = []
  for (let h = START_HOUR; h < END_HOUR; h++) hours.push(h)
  const gridHeight = (END_HOUR - START_HOUR) * PX_PER_HOUR

  const ext = data?.external_visibility
  const sync = data?.sync_status

  /**
   * One person's column, as typed bands.
   *
   * ORDER IS LOAD-BEARING. Available is painted first and full-width as the
   * canvas; everything that REMOVES time is painted over it. Drawing them the
   * other way round would let a green "available" band cover a meeting, which
   * is the single most dangerous thing this screen could get wrong.
   */
  function bandsFor(m) {
    const tz = m.timezone
    const items = []

    ;(m.free || []).forEach(f => {
      if (!spanTouchesDay(f.starts_at, f.ends_at, tz, day)) return
      const { startMin, endMin } = clampToDay(f.starts_at, f.ends_at, tz, day,
                                              END_HOUR)
      items.push({
        kind: 'free', z: 0, startMin, endMin,
        label: 'Available', sub: null, noLane: true,
      })
    })

    ;(m.blocked || []).forEach(b => {
      if (!spanTouchesDay(b.starts_at, b.ends_at, tz, day)) return
      const { startMin, endMin } = clampToDay(b.starts_at, b.ends_at, tz, day,
                                              END_HOUR)
      items.push({
        kind: 'blocked', z: 1, startMin, endMin,
        label: b.label || 'Blocked',
        sub: zoneTime(b.starts_at, tz), noLane: true,
      })
    })

    // PTO is the one band that is routinely MULTI-DAY, and the endpoint test
    // this used to do dropped every day in the middle of it — a Wednesday-to-
    // Friday absence rendered on Wednesday and Friday and left Thursday
    // looking bookable, which is the worst possible day to get wrong.
    ;(m.time_off || []).forEach(t => {
      if (!spanTouchesDay(t.starts_at, t.ends_at, tz, day)) return
      const { startMin, endMin } = clampToDay(t.starts_at, t.ends_at, tz, day,
                                              END_HOUR)
      items.push({
        kind: 'pto', z: 1, startMin, endMin,
        label: t.label || 'Time off', sub: null, noLane: true,
      })
    })

    if (showExternal) {
      ;(m.external_busy || []).forEach(x => {
        if (!spanTouchesDay(x.starts_at, x.ends_at, tz, day)) return
        const { startMin, endMin } = clampToDay(x.starts_at, x.ends_at, tz, day,
                                                END_HOUR)
        items.push({
          kind: 'external', z: 2, startMin, endMin,
          // The server's own wording. There is no title in the payload to show.
          label: 'Busy', sub: 'external calendar',
        })
      })
    }

    ;(m.busy || []).forEach(b => {
      if (!spanTouchesDay(b.starts_at, b.ends_at, tz, day)) return
      const bx = clampToDay(b.starts_at, b.ends_at, tz, day, END_HOUR)
      items.push({
        kind: b.kind === 'blocked' ? 'blocked'
          : b.kind === 'internal' ? 'internal' : 'customer',
        z: 3,
        startMin: bx.startMin,
        endMin: bx.endMin,
        // "Busy" when the server would not tell this viewer the title.
        label: b.title || 'Busy',
        sub: zoneTime(b.starts_at, tz)
          + (b.confirmation_status ? ' · ' + b.confirmation_status : ''),
        appointmentId: b.appointment_id,
        needsOutcome: b.needs_outcome,
      })
    })

    // Meetings and external busy can genuinely overlap, so they get lanes.
    // The full-width background bands do not: they are the canvas, not content.
    const laned = assignLanes(items.filter(i => !i.noLane))
    const plain = items.filter(i => i.noLane).map(i => ({ ...i, lane: 0, lanes: 1 }))

    return [...plain, ...laned].map(it => {
      const box = placeSpan(it.startMin, it.endMin, {
        startHour: START_HOUR, endHour: END_HOUR, pxPerHour: PX_PER_HOUR,
        minHeight: it.kind === 'free' ? 6 : 21,
      })
      return box ? { ...it, ...box } : null
    }).filter(Boolean).sort((a, b) => a.z - b.z)
  }

  return (
    <SalesShell
      title="Team Availability"
      subtitle="Who is free, who is busy, and the first time everyone can meet."
      actions={
        <div className="cal-bar">
          <button className="sw-btn" onClick={() => shift(-1)} aria-label="Previous day">←</button>
          <input className="sw-input" type="date" style={{ width: 158 }}
                 value={day} onChange={e => setDay(e.target.value)} />
          <button className="sw-btn" onClick={() => shift(1)} aria-label="Next day">→</button>
          <button className="sw-btn" onClick={() => setDay(ymd(new Date()))}>Today</button>
          <button className="sw-btn" onClick={() => nav('/sales/calendar')}>
            Team Calendar
          </button>
          <button className="sw-btn sw-primary" onClick={() => setBooking(true)}>
            + New Appointment
          </button>
        </div>
      }
    >
      <ErrorBar error={error} onRetry={() => load(day)} />

      {booked && (
        <div className="sw-card" style={{ marginBottom: 14 }}>
          <div className="sw-card-b sw-flex sw-between">
            <div>
              <Chip tone="green">Booked</Chip>
              <b style={{ marginLeft: 8, fontSize: 12 }}>{booked.title}</b>
              <div className="sw-subtle" style={{ marginTop: 4 }}>
                {(booked.participants || []).map(p => p.full_name).join(', ')}
                {booked.starts_at_local
                  ? ' · ' + String(booked.starts_at_local).slice(11, 16) : ''}
              </div>
            </div>
            <button className="sw-tiny" onClick={() => setBooked(null)}>Dismiss</button>
          </div>
        </div>
      )}

      {/* ── the filter strip ───────────────────────────────────────────── */}
      <div className="cal-filters">
        <b style={{ fontSize: 12 }}>
          {dayFromYmd(day).toLocaleDateString(undefined,
            { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })}
        </b>
        <div className="sw-spacer" />
        <select className="sw-select" style={{ width: 182 }}
                value={only ? 'some' : 'all'}
                onChange={e => setOnly(e.target.value === 'all'
                  ? null : (data?.members || []).map(m => m.user_id))}>
          <option value="all">
            All Team Members ({(data?.members || []).length})
          </option>
          <option value="some">
            {only ? only.length + ' shown' : 'Choose below'}
          </option>
        </select>
        <label className="cal-toggle"
               title="External calendars contribute free/busy only — never event details.">
          <input type="checkbox" checked={showExternal}
                 onChange={e => setShowExternal(e.target.checked)} />
          Show external calendars
        </label>
        <button className="sw-btn" onClick={() => load(day)} disabled={loading}>
          {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      {/* The honesty banner. Only rendered when a connected calendar genuinely
          could not be read — see the header note about why this is not a
          tooltip. */}
      {ext && !ext.complete && ext.note && showExternal && (
        <div className="cal-unverified">
          <span aria-hidden="true">⚠</span>
          <span><b>Partly unverified.</b> {ext.note}</span>
        </div>
      )}

      {loading && !data && <div className="sw-subtle">Loading…</div>}

      {data && (data.members || []).length === 0 && (
        <Card>
          <Empty title="No team members">
            Nobody holds an active membership in this brand sales organization
            yet, so there is no availability to compute.
          </Empty>
        </Card>
      )}

      {data && (data.members || []).length > 0 && (
        <div className="av-layout">
          <div>
            <Card title={'TEAM DAY VIEW · ' + (data.brand_sales_org?.name || '')}
                  sub="Each column is that person's own working day"
                  bodyless
                  right={only
                    ? <button className="sw-tiny" onClick={() => setOnly(null)}>
                        Show everyone
                      </button>
                    : null}>
              <div className="av-grid" style={{ '--av-cols': members.length }}>
                <div className="av-grid-in">
                  {/* header */}
                  <div className="av-hc">TIME</div>
                  {members.map(m => {
                    const unverified = m.external
                      && m.external.external_checked === false
                      && m.external.state !== 'not_connected'
                    const inMeeting = (m.busy || []).some(b => {
                      const s = zoneMinutes(b.starts_at, m.timezone)
                      const e = zoneMinutes(b.ends_at, m.timezone)
                      const now = new Date()
                      const nowMin = now.getHours() * 60 + now.getMinutes()
                      return day === ymd(now) && s <= nowMin && e > nowMin
                    })
                    return (
                      <div key={m.user_id} className="av-hc">
                        <div className="av-hc-who">
                          <div className="sw-avatar">{initials(m.full_name)}</div>
                          <div style={{ minWidth: 0 }}>
                            <b>{m.full_name}</b>
                            <small>
                              <i className={'cal-dot' + (inMeeting ? ' is-busy' : '')}
                                 aria-hidden="true" />
                              {inMeeting ? 'In a meeting' : 'Available'}
                              {!m.accepts_bookings ? ' · not bookable' : ''}
                            </small>
                            <small style={{ marginTop: 1 }}>
                              {m.timezone}
                              {unverified ? ' · unverified' : ''}
                            </small>
                          </div>
                          {unverified && (
                            <span title={m.external.message}
                                  style={{ fontSize: 11, opacity: 0.8 }}>⚠</span>
                          )}
                        </div>
                      </div>
                    )
                  })}

                  {/* the time gutter */}
                  <div className="av-gut">
                    {hours.map(h => <div key={h}>{hourLabel(h)}</div>)}
                  </div>

                  {/* one column per person */}
                  {members.map(m => (
                    <div key={m.user_id} className="av-col"
                         style={{ height: gridHeight }}>
                      {bandsFor(m).map((b, i) => (
                        <div key={b.kind + '-' + i}
                             className={'av-blk t-' + b.kind}
                             title={[b.label, b.sub].filter(Boolean).join(' · ')}
                             style={{
                               top: b.top, height: b.height,
                               zIndex: b.z,
                               left: b.noLane ? 0
                                 : 'calc(' + (b.lane * (100 / b.lanes)) + '% + 4px)',
                               right: b.noLane ? 0 : undefined,
                               width: b.noLane ? undefined
                                 : 'calc(' + (100 / b.lanes) + '% - 8px)',
                             }}>
                          {b.height >= 21 && <b>{b.label}</b>}
                          {b.height >= 34 && b.sub ? <span>{b.sub}</span> : null}
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              </div>

              {/* ── the legend ───────────────────────────────────────── */}
              <div className="cal-legend"
                   style={{ borderTop: '1px solid var(--sw-line2)' }}>
                {AV_LEGEND.map(l => (
                  <span key={l.kind}>
                    <i className={'cal-sw t-' + l.kind} aria-hidden="true" />
                    {l.label}
                  </span>
                ))}
              </div>
              <div className="sw-card-b sw-subtle"
                   style={{ borderTop: '1px solid var(--sw-line2)' }}>
                A meeting shows its title only to somebody who is on it. Anything
                else reads <b>Busy</b>, and an external calendar contributes the
                interval only — never what the meeting is.
              </div>
            </Card>

            {/* ── who is shown ─────────────────────────────────────────── */}
            <Card title="WHO IS SHOWN" sub="Untick to narrow the grid" bodyless>
              <div className="cal-roster">
                {(data.members || []).map(m => {
                  const on = !only || only.includes(m.user_id)
                  return (
                    <div key={m.user_id} className="cal-person">
                      <input type="checkbox" checked={on}
                             aria-label={'Show ' + m.full_name}
                             onChange={() => {
                               const cur = only || (data.members || [])
                                 .map(x => x.user_id)
                               const next = cur.includes(m.user_id)
                                 ? cur.filter(x => x !== m.user_id)
                                 : [...cur, m.user_id]
                               setOnly(next.length === (data.members || []).length
                                 ? null : next)
                             }} />
                      <div className="sw-flex" style={{ gap: 9, minWidth: 0 }}>
                        <div className="sw-avatar">{initials(m.full_name)}</div>
                        <div className="cal-person-n">
                          <b>{m.full_name}</b>
                          <small>
                            {m.timezone}
                            {' · '}
                            {m.external?.state === 'checked'
                              ? 'external calendar checked'
                              : m.external?.state === 'not_connected'
                                ? 'no external calendar'
                                : (m.external?.message || 'external state unknown')}
                          </small>
                        </div>
                      </div>
                      {m.external?.external_checked
                        ? <Chip tone="green">verified</Chip>
                        : m.external?.state === 'not_connected'
                          ? <Chip>unlinked</Chip>
                          : <Chip tone="amber">unverified</Chip>}
                    </div>
                  )
                })}
              </div>
            </Card>
          </div>

          {/* ── the right panel ──────────────────────────────────────────── */}
          <aside className="cal-rail">
            {/* FIND TEAM TIME, as a panel rather than a modal. Image 4 puts it
                here, and that is the right call: an opening only means
                something beside the columns it came from. */}
            <FindTeamTime team={(data.members || []).map(m => ({
              id: m.user_id, full_name: m.full_name,
            }))}
                          onBooked={a => { setBooked(a); load(day) }} />

            {/* TEAM SYNC STATUS. State, reason, action — never a bare dot. */}
            <Card title="TEAM SYNC STATUS"
                  sub="What EvoSys Pro can actually see"
                  bodyless
                  right={<button className="sw-tiny"
                                 onClick={() => nav('/sales/availability')}>
                    Manage
                  </button>}>
              <div className="cal-integ">
                {(sync?.mine || []).map(p => (
                  <div key={p.provider} className="cal-int">
                    <div>
                      <b>{p.label}</b>
                      <small>
                        {p.state_label}
                        {p.last_sync_at
                          ? ' · last sync ' + String(p.last_sync_at).slice(11, 16)
                          : ''}
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
                          ? ' · ' + sync.team.counts.reauth_required
                            + ' need reconnecting' : ''}
                        {sync.team.counts.degraded
                          ? ' · ' + sync.team.counts.degraded + ' degraded' : ''}
                      </small>
                    </div>
                    {(sync.team.counts.reauth_required || sync.team.counts.degraded)
                      ? <Chip tone="amber">attention</Chip>
                      : <Chip tone="green">healthy</Chip>}
                  </div>
                )}
                {!sync && (
                  <div className="sw-card-b sw-subtle">
                    Calendar connection status is unavailable right now.
                  </div>
                )}
              </div>
            </Card>

            <Card title="AVAILABILITY IS CALCULATED FROM" bodyless>
              <div className="cal-qa">
                <button onClick={() => nav('/sales/availability')}>
                  <span aria-hidden="true">◷</span> My working hours &amp; buffers
                </button>
                <button onClick={() => nav('/sales/availability')}>
                  <span aria-hidden="true">▤</span> My time off
                </button>
                <button onClick={() => nav('/sales/calendar')}>
                  <span aria-hidden="true">▦</span> What is already booked
                </button>
              </div>
              <div className="sw-card-b sw-subtle"
                   style={{ borderTop: '1px solid var(--sw-line2)' }}>
                Working hours, lunch and blocked time, time off, meeting
                buffers, minimum notice, the booking horizon, EvoSys Pro
                appointments, and busy time on connected calendars. Nobody is
                called available just because EvoSys Pro happens to hold no
                appointment for them.
              </div>
            </Card>
          </aside>
        </div>
      )}

      {booking && (
        <BookAppointment onClose={() => setBooking(false)}
                         onFindTime={() => setFindAsModal(true)}
                         onBooked={a => {
                           setBooking(false)
                           setBooked(a)
                           load(day)
                         }} />
      )}

      {findAsModal && (
        <FindTeamTime asModal
                      team={(data?.members || []).map(m => ({
                        id: m.user_id, full_name: m.full_name,
                      }))}
                      onClose={() => setFindAsModal(false)}
                      onBooked={a => {
                        setFindAsModal(false)
                        setBooked(a)
                        load(day)
                      }} />
      )}
    </SalesShell>
  )
}
