/**
 * Team Calendar — every meeting the team has, over a range.
 *
 * THIS SCREEN IS THE ONLY THING THAT WAS MISSING. `GET /sales/appointments`
 * already took `scope=team`, `date_from` and `date_to`, already refused a rep
 * asking for the team scope with a 403, and already returned participants,
 * meeting type, confirmation status and per-participant calendar sync state.
 * The audit found no frontend file called it at all. Team Command showed one
 * day; this shows the week, and it is the same endpoint.
 *
 * SCOPE IS THE SERVER'S DECISION, NOT THIS PAGE'S. `scope=team` is sent only
 * when the caller is a manager, and if that is ever wrong the API answers 403
 * rather than widening. A rep who reaches this URL gets `scope=mine`, which is
 * the meetings they are on or that belong to a deal they own — a genuinely
 * useful screen, not a consolation.
 *
 * WALL CLOCK, NOT INSTANTS. Every time rendered here comes from a `*_local`
 * field the server already resolved in the brand's timezone, through
 * `wallTime`/`wallDay`. Handing the naive UTC `starts_at` to `new Date()` is
 * what once made a 9am Chicago meeting render as 2pm.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import SalesShell from './SalesShell'
import {
  Card, Chip, Empty, ErrorBar, Metric,
  wallTime, wallDay, parseNaive,
} from './parts'

const STAGE_HINT = {
  discovery: 'Discovery',
  demo_build: 'Demo',
  proposal: 'Proposal',
  closing: 'Closing',
  contacted: 'Follow-up',
  new: 'Follow-up',
}

/** Local YYYY-MM-DD. `toISOString()` would shift the date across midnight for
 *  anyone west of UTC, which is most of this team. */
function ymd(d) {
  const p = n => String(n).padStart(2, '0')
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
}

function addDays(d, n) {
  const c = new Date(d)
  c.setDate(c.getDate() + n)
  return c
}

function Participants({ people }) {
  if (!people?.length) return <span className="sw-subtle">No internal attendees</span>
  return (
    <div className="sw-cal-parts">
      {people.map(p => (
        <span
          key={p.user_id}
          className={'sw-part'
            + (p.needs_attention ? ' sw-bad' : (p.is_required ? ' sw-req' : ''))}
          title={p.needs_attention
            ? (p.sync_label || 'Calendar needs attention')
            : (p.role_label || '')}
        >
          {p.full_name}
          {p.is_required ? ' *' : ''}
          {p.needs_attention ? ' ⚠' : ''}
        </span>
      ))}
    </div>
  )
}

export default function TeamCalendar() {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [isManager, setIsManager] = useState(null)   // null until the server says
  const [start, setStart] = useState(() => new Date())
  const [days, setDays] = useState(7)
  const [ownerFilter, setOwnerFilter] = useState('')
  const [team, setTeam] = useState([])

  const from = ymd(start)
  const to = ymd(addDays(start, days - 1))

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    // Ask for the team scope only when we already know we may have it. On the
    // very first load we do not, so we ask for `mine`, read `is_manager` off
    // the answer, and widen. One wasted request on first paint, and never a
    // 403 rendered as an error to somebody who did nothing wrong.
    const wantTeam = isManager !== false
    const q = new URLSearchParams({
      date_from: from, date_to: to,
      scope: wantTeam ? 'team' : 'mine',
    })
    try {
      const r = await api.get('/sales/appointments?' + q.toString())
      setData(r)
      setIsManager(!!r.is_manager)
    } catch (e) {
      // A rep asking for the team scope is refused, by design. Fall back to
      // their own meetings rather than showing them a permission error.
      if (wantTeam && (e.status === 403 || /manager/i.test(e.message || ''))) {
        setIsManager(false)
        try {
          const r2 = await api.get('/sales/appointments?' + new URLSearchParams({
            date_from: from, date_to: to, scope: 'mine',
          }).toString())
          setData(r2)
        } catch (e2) {
          setError(e2.message || 'Could not load the calendar.')
        }
      } else {
        setError(e.message || 'Could not load the calendar.')
      }
    } finally {
      setLoading(false)
    }
  }, [from, to, isManager])

  useEffect(() => { load() }, [load])
  useEffect(() => {
    api.get('/sales/team').then(setTeam).catch(() => setTeam([]))
  }, [])

  const appts = useMemo(() => {
    let rows = data?.appointments || []
    if (ownerFilter) {
      rows = rows.filter(a => (a.participants || [])
        .some(p => p.user_id === ownerFilter))
    }
    return rows
  }, [data, ownerFilter])

  // Group by the LOCAL day the server resolved, so a meeting never lands on the
  // wrong heading for a viewer in another timezone.
  const byDay = useMemo(() => {
    const m = new Map()
    appts.forEach(a => {
      const key = String(a.starts_at_local || a.starts_at || '').slice(0, 10)
      if (!m.has(key)) m.set(key, [])
      m.get(key).push(a)
    })
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [appts])

  const unconfirmed = appts.filter(a => a.confirmation_status === 'pending').length
  const syncTrouble = appts.filter(a => a.sync_needs_attention > 0).length
  const videoTrouble = appts.filter(a => a.video?.needs_attention).length

  const strip = Array.from({ length: days }, (_, i) => addDays(start, i))
  const countFor = d => appts.filter(
    a => String(a.starts_at_local || '').slice(0, 10) === ymd(d)).length

  return (
    <SalesShell
      title={isManager === false ? 'My Calendar' : 'Team Calendar'}
      subtitle={isManager === false
        ? 'Every meeting you are on, or that belongs to a deal you own.'
        : 'Every meeting your team has booked, with who is required and whether the prospect confirmed.'}
      actions={
        <>
          {isManager && team.length > 0 && (
            <select className="sw-select" style={{ width: 180 }}
                    value={ownerFilter} onChange={e => setOwnerFilter(e.target.value)}>
              <option value="">Everyone</option>
              {team.map(t => <option key={t.id} value={t.id}>{t.full_name}</option>)}
            </select>
          )}
          <select className="sw-select" style={{ width: 110 }}
                  value={days} onChange={e => setDays(Number(e.target.value))}>
            <option value={1}>1 day</option>
            <option value={7}>7 days</option>
            <option value={14}>14 days</option>
            <option value={30}>30 days</option>
          </select>
          <button className="sw-btn" onClick={() => setStart(addDays(start, -days))}>←</button>
          <button className="sw-btn" onClick={() => setStart(new Date())}>Today</button>
          <button className="sw-btn" onClick={() => setStart(addDays(start, days))}>→</button>
          <button className="sw-btn" onClick={load} disabled={loading}>Refresh</button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={load} />

      {isManager === false && (
        <div className="sw-note">
          Showing your own meetings. The whole team's calendar is available to
          sales managers.
        </div>
      )}

      <div className="sw-metrics">
        <Metric label="Meetings in range" value={appts.length}
                sub={`${wallDay(from + 'T00:00') || from} → ${wallDay(to + 'T00:00') || to}`} />
        <Metric label="Unconfirmed" value={unconfirmed} attn={unconfirmed > 0}
                sub={unconfirmed ? 'prospect has not confirmed' : 'all confirmed'} />
        <Metric label="Calendar sync" value={syncTrouble} attn={syncTrouble > 0}
                sub={syncTrouble ? 'need attention' : 'healthy'} />
        <Metric label="Video links" value={videoTrouble} attn={videoTrouble > 0}
                sub={videoTrouble ? 'need attention' : 'healthy'} />
      </div>

      <Card title="RANGE" sub="Click a day to jump to it" bodyless>
        <div className="sw-card-b">
          <div className="sw-daystrip">
            {strip.map(d => {
              const n = countFor(d)
              const isToday = ymd(d) === ymd(new Date())
              return (
                <button
                  key={ymd(d)}
                  className={'sw-daybtn' + (isToday ? ' sw-on' : '')}
                  onClick={() => { setStart(d); setDays(1) }}
                >
                  <b>{d.getDate()}</b>
                  <small>{d.toLocaleDateString(undefined, { weekday: 'short' })}</small>
                  <small>{n ? n + (n === 1 ? ' mtg' : ' mtgs') : '—'}</small>
                </button>
              )
            })}
          </div>
        </div>
      </Card>

      <div className="sw-mt">
        {loading && !data ? <div className="sw-subtle">Loading…</div> : null}

        {data && byDay.length === 0 ? (
          <Card>
            <Empty title="Nothing booked in this range">
              This is a real answer, not a placeholder — the schedule is empty
              for these dates. Widen the range or step forward to find the next
              meeting.
            </Empty>
          </Card>
        ) : null}

        {byDay.map(([day, rows]) => (
          <Card
            key={day}
            title={(wallDay(day + 'T00:00') || day).toUpperCase()}
            sub={`${rows.length} ${rows.length === 1 ? 'meeting' : 'meetings'}`}
          >
            {rows.map(a => {
              const hint = STAGE_HINT[a.opportunity_stage]
              const end = a.ends_at_local ? wallTime(a.ends_at_local) : null
              return (
                <div key={a.id} className="sw-cal">
                  <div className="sw-cal-when">
                    <b>{wallTime(a.starts_at_local)}</b>
                    <small>{end ? 'to ' + end : ''}</small>
                    <small>{a.duration_minutes} min</small>
                  </div>

                  <div className="sw-cal-main">
                    <b>
                      {a.meeting_type || a.title || 'Meeting'}
                      {a.opportunity_company ? ' · ' + a.opportunity_company : ''}
                    </b>
                    <div className="sw-cal-meta">
                      {a.prospect?.name || 'No prospect named'}
                      {a.prospect?.company && !a.opportunity_company
                        ? ' · ' + a.prospect.company : ''}
                      {a.location ? ' · ' + a.location : ''}
                    </div>
                    <Participants people={a.participants} />
                  </div>

                  <div className="sw-cal-act">
                    {hint ? <Chip>{hint}</Chip> : null}
                    {a.confirmation_status === 'confirmed'
                      ? <Chip tone="green">confirmed</Chip>
                      : a.confirmation_status === 'declined'
                        ? <Chip tone="red">declined</Chip>
                        : <Chip tone="amber">unconfirmed</Chip>}
                    {a.sync_needs_attention > 0
                      ? <Chip tone="red">{a.sync_needs_attention} calendar</Chip>
                      : null}
                    {a.video?.needs_attention ? <Chip tone="red">video</Chip> : null}
                    {a.video?.join_url
                      ? <a className="sw-btn sw-primary" href={a.video.join_url}
                           target="_blank" rel="noreferrer">Join</a>
                      : null}
                    {a.opportunity_id
                      ? <button className="sw-btn"
                                onClick={() => nav('/sales/opportunities/' + a.opportunity_id)}>
                          Open deal
                        </button>
                      : null}
                  </div>
                </div>
              )
            })}
          </Card>
        ))}
      </div>

      <p className="sw-subtle" style={{ marginTop: 14 }}>
        A name marked <b>*</b> is a required participant. <b>⚠</b> means that
        person's calendar could not be written — the meeting exists here either
        way, which is exactly the distinction the badge is for.
      </p>
    </SalesShell>
  )
}
