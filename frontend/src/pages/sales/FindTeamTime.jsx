/**
 * Find Team Time — the shared-availability finder.
 *
 * THE CORE REQUIREMENT, AND THE ONE THING IT MUST NEVER DO
 * -------------------------------------------------------
 * The slots shown are the INTERSECTION the server returned: only times when
 * every REQUIRED participant is free. Optional participants never remove a
 * slot — each opening reports which of them happen to be free too, so a fuller
 * room can be preferred without a viable time being hidden.
 *
 * It must never offer a time that is not real. Two things protect that, and
 * they are different:
 *
 *   1. The server refreshes every candidate's EXTERNAL calendar before it
 *      intersects, so the answer accounts for meetings that live only in
 *      Outlook or Google.
 *   2. Booking RE-CHECKS at the moment of saving. A slot displayed thirty
 *      seconds ago is a claim about the past. When the recheck refuses, this
 *      panel does not just show the error — it re-runs the search, because the
 *      useful response to "that time went" is the list of times that have not.
 *
 * And when a calendar could NOT be read, it says so on the face of the panel.
 * "Free" and "we could not check" must never look the same on a screen people
 * book from.
 *
 * TWO PRESENTATIONS, ONE COMPONENT
 * --------------------------------
 * `asModal` renders it over the Team Calendar, where the calendar is the
 * context. Without it, it renders as the side PANEL Image 4 specifies, beside
 * the availability columns — which is the better home for it: an opening only
 * means something next to the grid it came from, and a modal hides exactly
 * that.
 */
import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
// `wallDay` lives in parts.jsx and `wallTime` in calendarTime.js. Both read a
// server-resolved wall clock the same way — see calendarTime's header for why
// that distinction matters and why neither hands a naive instant to Date().
import { Card, ErrorBar, initials, wallDay } from './parts'
import { wallTime, ymd, addDays } from './calendarTime'

function iso(d) { return ymd(d) }

const DURATIONS = [15, 30, 45, 60, 90, 120]

export default function FindTeamTime({
  opportunity, asModal, team: teamProp, preselect, onClose, onBooked,
}) {
  const [types, setTypes] = useState([])
  const [typeId, setTypeId] = useState('')
  const [team, setTeam] = useState(teamProp || [])
  const [required, setRequired] = useState(preselect || [])
  const [optional, setOptional] = useState([])
  const [duration, setDuration] = useState('')
  const [from, setFrom] = useState(() => iso(new Date()))
  const [to, setTo] = useState(() => iso(addDays(new Date(), 6)))
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [booking, setBooking] = useState(null)
  const [selected, setSelected] = useState(null)
  const [error, setError] = useState(null)
  const [adding, setAdding] = useState(false)
  const [showAll, setShowAll] = useState(false)

  const oppId = opportunity?.id

  useEffect(() => {
    const q = oppId ? '?opportunity_id=' + encodeURIComponent(oppId) : ''
    api.get('/sales/meeting-types' + q)
      .then(t => {
        setTypes(t)
        if (t.length && !typeId) applyType(t[0], t)
      })
      .catch(e => setError(e.message))
    if (!teamProp) {
      api.get('/sales/team').then(setTeam).catch(() => setTeam([]))
    }
    // THE VIEWER STARTS AS REQUIRED, on the standalone panel with no deal.
    //
    // An empty required list means no intersection to compute, so the panel
    // opened inert with its own button disabled — technically honest and
    // practically useless, because the person looking for a time is almost
    // always going to be in the meeting. Seeding THEM is a safe assumption;
    // seeding anyone else would be a guess about who else should attend.
    if (!preselect?.length && !oppId) {
      api.get('/sales/me')
        .then(me => {
          const id = me?.user?.id
          if (id) setRequired(prev => (prev.length ? prev : [id]))
        })
        .catch(() => { /* the chips still work; this is only a head start */ })
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [oppId])

  /** Pre-fill from the type's resolved role slots. Ambiguity is reported, not guessed. */
  function applyType(t, all) {
    setTypeId(t.id)
    setResult(null)
    setSelected(null)
    if (preselect?.length) return
    const found = (all || types).find(x => x.id === t.id) || t
    const req = [], opt = []
    ;(found.resolved?.required || []).forEach(s => {
      if (s.auto_selected_user_id) req.push(s.auto_selected_user_id)
      else if (s.candidates?.length === 1) req.push(s.candidates[0].id)
    })
    ;(found.resolved?.optional || []).forEach(s => {
      if (s.auto_selected_user_id) opt.push(s.auto_selected_user_id)
    })
    // With no deal, the role slots resolve to nothing, so a type change would
    // otherwise wipe the viewer seeded above and leave the panel inert again.
    // Keep whoever is already selected when the type has nothing to say.
    setRequired(prev => (req.length ? [...new Set(req)]
      : (oppId ? [] : prev)))
    setOptional([...new Set(opt.filter(u => !req.includes(u)))])
  }

  const selectedType = types.find(t => t.id === typeId)
  const byId = useMemo(() => {
    const m = {}
    team.forEach(t => { m[t.id] = t })
    return m
  }, [team])

  const ambiguous = useMemo(() => {
    if (!selectedType) return []
    return (selectedType.resolved?.required || [])
      .filter(s => !s.auto_selected_user_id && (s.candidates?.length || 0) !== 1)
  }, [selectedType])

  async function find() {
    setBusy(true); setError(null); setResult(null); setSelected(null)
    try {
      setResult(await api.post('/sales/availability/find', {
        meeting_type_id: typeId || undefined,
        duration_minutes: Number(duration) || undefined,
        opportunity_id: oppId,
        required_user_ids: required,
        optional_user_ids: optional,
        date_from: from,
        date_to: to,
      }))
    } catch (e) {
      setError(e.message || 'Could not calculate availability.')
    } finally {
      setBusy(false)
    }
  }

  async function book(slot) {
    setBooking(slot.starts_at); setError(null)
    try {
      const slots = {}
      ;(selectedType?.resolved?.required || []).forEach(s => {
        if (s.auto_selected_user_id) slots[s.auto_selected_user_id] = s.slot
      })
      const appt = await api.post('/sales/appointments', {
        starts_at: slot.starts_at,
        meeting_type_id: typeId || undefined,
        duration_minutes: Number(duration) || undefined,
        opportunity_id: oppId,
        required_user_ids: required,
        optional_user_ids: optional,
        role_slot_by_user: slots,
      })
      onBooked && onBooked(appt)
    } catch (e) {
      // The recheck refused — an EvoSys meeting or an external event appeared
      // between the search and the click. Re-run the search rather than leave
      // the rep staring at a list that is now wrong.
      setError((e.message || 'Could not book that time.')
        + ' The openings below have been recalculated.')
      find()
    } finally {
      setBooking(null)
    }
  }

  /** Grouped by the LOCAL day the server resolved, never by the UTC instant. */
  const grouped = useMemo(() => {
    if (!result?.slots) return []
    const out = []
    result.slots.forEach(s => {
      const k = String(s.starts_at_local || s.starts_at).slice(0, 10)
      const last = out[out.length - 1]
      if (last && last.key === k) last.slots.push(s)
      else out.push({ key: k, slots: [s] })
    })
    return out
  }, [result])

  const shown = showAll ? grouped : grouped.slice(0, 3)
  const ext = result?.external_visibility

  // ── the body, shared by both presentations ────────────────────────────────
  const body = (
    <div className="av-find-b">
      <ErrorBar error={error} />

      <div className="sw-subtle">
        Select who must be there and we will show only the times they can all
        make.
      </div>

      {/* REQUIRED PARTICIPANTS as removable chips — Image 4's shape. Required
          is the field that decides the answer, so it reads as a list of people
          rather than as a multi-select nobody can see the state of. */}
      <div className="sw-field" style={{ margin: 0 }}>
        <label>REQUIRED PARTICIPANTS</label>
        <div className="sw-chips">
          {required.map(id => (
            <span key={id} className="av-chip">
              <span className="sw-avatar">{initials(byId[id]?.full_name)}</span>
              {byId[id]?.full_name || 'Unknown'}
              <button type="button" aria-label={'Remove ' + (byId[id]?.full_name || '')}
                      onClick={() => setRequired(required.filter(x => x !== id))}>×</button>
            </span>
          ))}
          <button type="button" className="av-chip is-add"
                  onClick={() => setAdding(!adding)}>
            + Add people
          </button>
        </div>
        {!required.length && (
          <div className="sw-subtle" style={{ marginTop: 6 }}>
            With nobody required there is no intersection to compute.
          </div>
        )}
      </div>

      {adding && (
        <div className="sw-field" style={{ margin: 0 }}>
          <label>PICK FROM THE TEAM — click again for optional</label>
          <div className="sw-chips">
            {team.map(m => {
              const isReq = required.includes(m.id)
              const isOpt = optional.includes(m.id)
              return (
                <button key={m.id} type="button"
                        className={'sw-chip' + (isReq ? ' sw-green' : isOpt ? ' sw-blue' : '')}
                        style={{ cursor: 'pointer' }}
                        onClick={() => {
                          if (isReq) {
                            setRequired(required.filter(x => x !== m.id))
                            setOptional([...optional, m.id])
                          } else if (isOpt) {
                            setOptional(optional.filter(x => x !== m.id))
                          } else {
                            setRequired([...required, m.id])
                          }
                        }}>
                  {m.full_name}
                  {isReq ? ' · required' : isOpt ? ' · optional' : ''}
                </button>
              )
            })}
          </div>
          <div className="sw-subtle" style={{ marginTop: 6 }}>
            An optional participant never removes a time — each opening shows
            how many of them are also free.
          </div>
        </div>
      )}

      <div className="sw-field" style={{ margin: 0 }}>
        <label>MEETING DETAILS</label>
        <select className="sw-select" value={typeId}
                onChange={e => {
                  const t = types.find(x => x.id === e.target.value)
                  if (t) applyType(t)
                }}>
          {types.map(t => (
            <option key={t.id} value={t.id}>
              {t.name} · {t.duration_minutes} min
            </option>
          ))}
        </select>
      </div>

      <div className="sw-grid-even" style={{ margin: 0 }}>
        <div className="sw-field" style={{ margin: 0 }}>
          <label>DURATION</label>
          <select className="sw-select" value={duration}
                  onChange={e => setDuration(e.target.value)}>
            <option value="">
              Type default · {selectedType?.duration_minutes || 30} min
            </option>
            {DURATIONS.map(n => <option key={n} value={n}>{n} minutes</option>)}
          </select>
        </div>
        <div className="sw-field" style={{ margin: 0 }}>
          <label>SEARCH FROM</label>
          <input className="sw-input" type="date" value={from}
                 onChange={e => setFrom(e.target.value)} />
        </div>
      </div>

      <div className="sw-field" style={{ margin: 0 }}>
        <label>SEARCH TO</label>
        <input className="sw-input" type="date" value={to}
               onChange={e => setTo(e.target.value)} />
      </div>

      {/* Only meaningful WITH a deal in hand. A role like "Opportunity Owner"
          cannot resolve when there is no opportunity, so showing it as an
          unfilled role on the standalone panel reported a problem that does
          not exist — the participant chips above are the answer there. */}
      {opportunity && ambiguous.length > 0 && (
        <div className="sw-notbuilt">
          <b>PICK WHO FILLS THESE ROLES</b>
          <p>
            {ambiguous.map(s => s.label).join(', ')} —{' '}
            {ambiguous.some(s => s.candidates?.length)
              ? 'more than one person can fill this, so nothing was assumed.'
              : 'nobody in this brand can fill this role yet.'}
          </p>
        </div>
      )}

      <button className="sw-btn sw-primary" onClick={find}
              disabled={busy || required.length === 0}>
        {busy ? 'Calculating…' : 'Find Available Times'}
      </button>

      {/* Stated on the face of the panel, not in a tooltip. If somebody's
          outside calendar could not be read, these times are still the best
          available answer — and the rep is told which part is unverified, so a
          later clash is a known risk rather than a betrayal. */}
      {ext && !ext.complete && ext.note && (
        <div className="cal-unverified" style={{ margin: 0 }}>
          <span aria-hidden="true">⚠</span>
          <span><b>Partly unverified.</b> {ext.note}</span>
        </div>
      )}
    </div>
  )

  // ── the results list ──────────────────────────────────────────────────────
  const results = result && (
    <>
      <div className="sw-card-h" style={{ borderTop: '1px solid var(--sw-line2)' }}>
        <div>
          <h3>NEXT AVAILABLE TIMES</h3>
          <small>
            {result.total} opening{result.total === 1 ? '' : 's'} ·{' '}
            {result.duration_minutes} min · {result.timezone}
          </small>
        </div>
      </div>

      {result.total === 0 && (
        <div className="sw-card-b">
          <div className="sw-notbuilt">
            <b>NO SHARED OPENINGS</b>
            {/* The server says WHY. "No times available" with no reason is the
                single most infuriating thing a scheduler can say. */}
            {(result.blockers || []).map((b, i) => <p key={i}>{b}</p>)}
            <p>Try a wider date range, or move somebody to optional.</p>
          </div>
        </div>
      )}

      <div className="av-slots">
        {shown.map(g => (
          <div key={g.key}>
            <div className="sw-card-b sw-subtle"
                 style={{ paddingTop: 9, paddingBottom: 4, fontWeight: 800 }}>
              {wallDay(g.key + 'T00:00') || g.key}
            </div>
            {g.slots.map(s => {
              const isSel = selected === s.starts_at
              return (
                <button key={s.starts_at}
                        className={'av-slot' + (isSel ? ' is-sel' : '')}
                        disabled={!!booking}
                        onClick={() => {
                          // Two taps, deliberately. The first selects, the
                          // second books — because booking puts a meeting on
                          // three people's calendars and sends the prospect an
                          // invitation, which is not a thing to do on a
                          // mis-click in a scrolling list.
                          if (isSel) book(s)
                          else setSelected(s.starts_at)
                        }}>
                  <span className="av-slot-r" aria-hidden="true" />
                  <span className="av-slot-m">
                    <b>{wallTime(s.starts_at_local || s.starts_at)}</b>
                    <small>
                      {booking === s.starts_at ? 'Booking…'
                        : isSel ? 'Tap again to book'
                          : (optional.length
                            ? s.optional_available_count + ' of ' + optional.length
                              + ' optional also free'
                            : 'Everyone required is free')}
                    </small>
                  </span>
                  <span className="av-faces">
                    {required.slice(0, 3).map(id => (
                      <span key={id} className="sw-avatar"
                            title={byId[id]?.full_name}>
                        {initials(byId[id]?.full_name)}
                      </span>
                    ))}
                    {s.optional_available?.map(o => (
                      <span key={o.user_id} className="sw-avatar"
                            title={o.full_name + ' (optional)'}
                            style={{ opacity: 0.6 }}>
                        {initials(o.full_name)}
                      </span>
                    ))}
                  </span>
                </button>
              )
            })}
          </div>
        ))}
      </div>

      {grouped.length > 3 && (
        <div className="sw-card-b">
          <button className="sw-tiny" onClick={() => setShowAll(!showAll)}>
            {showAll ? 'Show fewer days' : 'View more times →'}
          </button>
        </div>
      )}

      {result.total > 0 && (
        <div className="sw-card-b sw-subtle">
          Availability is re-checked against every participant's schedule and
          their connected calendars at the moment of booking.
        </div>
      )}
    </>
  )

  if (asModal) {
    return (
      <div className="sw-modal-back"
           onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
        <div className="sw-modal" style={{ maxWidth: 620 }}>
          <div className="sw-card-h">
            <div>
              <h3>FIND TEAM TIME</h3>
              <small>
                {opportunity
                  ? opportunity.company_name
                    + (opportunity.contact_name ? ' · ' + opportunity.contact_name : '')
                  : 'Only times every required participant is free'}
              </small>
            </div>
            <div className="sw-spacer" />
            <button className="sw-btn" onClick={onClose}>Close</button>
          </div>
          {body}
          {results}
        </div>
      </div>
    )
  }

  return (
    <Card title="FIND TEAM TIME"
          sub="Only times every required participant is free"
          bodyless>
      <div className="av-find">
        {body}
        {results}
      </div>
    </Card>
  )
}
