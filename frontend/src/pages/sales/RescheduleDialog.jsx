/**
 * Move a meeting.
 *
 * OFFERS OPENINGS, NOT A FREE-TEXT TIME BOX. Rescheduling runs the same shared
 * availability search as the original booking, so a moved meeting is subject to
 * exactly the rules the first one was: everyone required must genuinely be
 * free, buffers and notice apply, and external calendar commitments count.
 * A plain datetime field would happily book a time half the room is busy for
 * and only fail at the server — after the rep has already told the prospect.
 *
 * The current meeting is excluded from its own conflict check server-side, so
 * the slot it currently occupies is offered back as available.
 */
import { useEffect, useState, useCallback } from 'react'
import { api } from '../../api/client'
import { ErrorBar, wallDateTime } from './parts'

function isoDay(d) {
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString().slice(0, 10)
}

export default function RescheduleDialog({ appt, onClose, onDone }) {
  const [from, setFrom] = useState(isoDay(new Date()))
  const [days, setDays] = useState(7)
  const [slots, setSlots] = useState(null)
  const [blockers, setBlockers] = useState([])
  const [picked, setPicked] = useState(null)
  const [reason, setReason] = useState('')
  const [notify, setNotify] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const search = useCallback(async () => {
    setBusy(true); setError(null); setPicked(null)
    try {
      const r = await api.post('/sales/availability/find', {
        required_user_ids: (appt.participants || [])
          .filter(p => p.is_required).map(p => p.user_id),
        optional_user_ids: (appt.participants || [])
          .filter(p => !p.is_required).map(p => p.user_id),
        duration_minutes: appt.duration_minutes,
        date_from: from,
        date_to: isoDay(new Date(new Date(from + 'T00:00:00').getTime()
                                 + days * 86400000)),
        // Without this the meeting blocks its own slot and the time it already
        // occupies would never be offered back.
        exclude_appointment_id: appt.id,
      })
      setSlots(r.slots || [])
      setBlockers(r.blockers || [])
    } catch (e) { setError(e.message || 'Could not find openings.') }
    finally { setBusy(false) }
  }, [appt, from, days])

  useEffect(() => { search() }, [search])

  async function commit() {
    if (!picked) return
    setBusy(true); setError(null)
    try {
      await api.post('/sales/appointments/' + appt.id + '/reschedule', {
        starts_at: picked,
        duration_minutes: appt.duration_minutes,
        reason: reason.trim() || null,
        notify,
      })
      if (onDone) await onDone()
      onClose()
    } catch (e) { setError(e.message || 'Could not move the meeting.') }
    finally { setBusy(false) }
  }

  return (
    // Same interaction as FindTeamTime: backdrop mousedown closes, the panel
    // itself does not. Matching it means one modal behaviour in this workspace.
    <div className="sw-modal-back"
         onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="sw-modal" style={{ maxWidth: 680 }}>
        <div className="sw-card-h">
          <div>
            <h3>MOVE THIS MEETING</h3>
            <small>
              Currently {wallDateTime(appt.starts_at_local || appt.starts_at)} ·{' '}
              {appt.duration_minutes} min
            </small>
          </div>
          <div className="sw-spacer" />
          <button className="sw-btn" onClick={onClose}>Close</button>
        </div>

        <div className="sw-card-b">
          <ErrorBar error={error} />

          <div className="sw-flex" style={{ gap: 10, marginBottom: 12 }}>
            <div className="sw-field" style={{ margin: 0 }}>
              <label>FROM</label>
              <input className="sw-input" type="date" value={from}
                     onChange={e => setFrom(e.target.value)} />
            </div>
            <div className="sw-field" style={{ margin: 0 }}>
              <label>DAYS AHEAD</label>
              <select className="sw-select" value={days}
                      onChange={e => setDays(Number(e.target.value))}>
                <option value={3}>3</option>
                <option value={7}>7</option>
                <option value={14}>14</option>
                <option value={30}>30</option>
              </select>
            </div>
            <div style={{ alignSelf: 'flex-end' }}>
              <button className="sw-btn" onClick={search} disabled={busy}>
                {busy ? 'Searching…' : 'Find openings'}
              </button>
            </div>
          </div>

          {/* The same honest empty state the original finder uses: "no openings"
              and "we did not look" must never look the same. */}
          {slots && slots.length === 0 && (
            <div className="sw-subtle" style={{ marginBottom: 12 }}>
              {blockers.length
                ? blockers.join(' ')
                : 'No openings when everyone required is free in this range. Try a wider range.'}
            </div>
          )}

          {slots && slots.length > 0 && (
            <div className="sw-chips"
                 style={{ maxHeight: 260, overflowY: 'auto', marginBottom: 12 }}>
              {slots.map(s => (
                <button key={s.starts_at}
                        className={'sw-btn' + (picked === s.starts_at ? ' sw-primary' : '')}
                        onClick={() => setPicked(s.starts_at)}
                        title={s.optional_available_count
                          ? s.optional_available_count + ' optional also free'
                          : undefined}>
                  {wallDateTime(s.starts_at_local || s.starts_at)}
                  {s.optional_available_count > 0 && (
                    <span style={{ marginLeft: 6, opacity: 0.65, fontWeight: 400 }}>
                      +{s.optional_available_count}
                    </span>
                  )}
                </button>
              ))}
            </div>
          )}

          <div className="sw-field">
            <label>REASON (OPTIONAL)</label>
            <input className="sw-input" value={reason} placeholder="Prospect asked to move it"
                   onChange={e => setReason(e.target.value)} />
          </div>

          <label className="sw-flex" style={{ gap: 7, marginTop: 4 }}>
            <input type="checkbox" checked={notify}
                   onChange={e => setNotify(e.target.checked)} />
            <span style={{ fontSize: 11 }}>
              Update everyone's calendar and email the prospect the new time
            </span>
          </label>
          {/* Unticking is for a correction nobody should be told about. Said
              plainly, because a silent move that nobody hears about is the
              default way a meeting gets missed. */}
          {!notify && (
            <div className="sw-subtle" style={{ marginTop: 6, color: '#b45309' }}>
              Nobody will be told. Calendars will still show the old time.
            </div>
          )}

          <div className="sw-flex" style={{ justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
            <button className="sw-btn" onClick={onClose}>Cancel</button>
            <button className="sw-btn sw-primary" onClick={commit} disabled={!picked || busy}>
              {busy ? 'Moving…' : 'Move meeting'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
