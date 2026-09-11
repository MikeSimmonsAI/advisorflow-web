/**
 * New Appointment — the manual booking flow.
 *
 * TWO WAYS TO BOOK, ONE ENDPOINT. Find Team Time is the good path: it asks who
 * must be there and offers only times they can all make. This is the other
 * one — "I already agreed Tuesday at two on the phone, put it in the system" —
 * and it has to exist, because a rep who cannot record a commitment they have
 * already made will keep it in their head instead.
 *
 * It is NOT a lesser path. It posts to the same `POST /sales/appointments`,
 * which means it gets the same protections: the participant overlap check
 * inside the transaction, the Postgres exclusion constraint behind that, the
 * final external-calendar revalidation, and the same 409 when somebody was
 * booked a moment ago. A second booking route with weaker checks is how a
 * scheduling system acquires its first double-booking.
 *
 * WHAT IT REFUSES TO DO: create a lead, a company or a contact. A meeting is
 * attached to an EXISTING prospect or opportunity, or to nobody. Booking a
 * meeting is not the moment to mint a duplicate customer record.
 *
 * THE 409 IS THE INTERESTING CASE. When the server refuses because the slot
 * went, this does not just show the error — it says which check caught it, and
 * offers Find Team Time, because the useful next action is finding a time that
 * still exists rather than retyping the one that does not.
 */
import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import { ErrorBar } from './parts'

const FORMATS = [
  { key: 'video', label: 'Video' },
  { key: 'phone', label: 'Phone' },
  { key: 'in_person', label: 'In person' },
]

function todayYmd() {
  const d = new Date()
  const p = x => String(x).padStart(2, '0')
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
}

export default function BookAppointment({
  opportunity, presetStartsAt, presetRequired, onClose, onBooked, onFindTime,
}) {
  const [types, setTypes] = useState([])
  const [team, setTeam] = useState([])
  const [typeId, setTypeId] = useState('')
  const [required, setRequired] = useState(presetRequired || [])
  const [optional, setOptional] = useState([])
  const [day, setDay] = useState(() =>
    presetStartsAt ? String(presetStartsAt).slice(0, 10) : todayYmd())
  const [time, setTime] = useState(() =>
    presetStartsAt ? String(presetStartsAt).slice(11, 16) : '10:00')
  const [duration, setDuration] = useState('')
  const [format, setFormat] = useState('video')
  const [location, setLocation] = useState('')
  const [meetingUrl, setMeetingUrl] = useState('')
  const [notes, setNotes] = useState('')
  const [title, setTitle] = useState('')
  const [prospectName, setProspectName] = useState('')
  const [prospectEmail, setProspectEmail] = useState('')
  const [prospectPhone, setProspectPhone] = useState('')
  const [sendConfirmation, setSendConfirmation] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [conflict, setConflict] = useState(false)

  const oppId = opportunity?.id

  useEffect(() => {
    const q = oppId ? '?opportunity_id=' + encodeURIComponent(oppId) : ''
    api.get('/sales/meeting-types' + q)
      .then(t => {
        setTypes(t)
        if (t.length && !typeId) applyType(t[0], t)
      })
      .catch(e => setError(e.message))
    api.get('/sales/team').then(setTeam).catch(() => setTeam([]))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [oppId])

  /**
   * Pre-fill participants from the meeting type's resolved role slots.
   *
   * A slot with exactly one candidate fills itself; an ambiguous one is
   * reported and left empty rather than guessed. Guessing which of three
   * managers should be in the room produces a meeting the wrong person has to
   * decline, which is worse than asking.
   */
  function applyType(t, all) {
    setTypeId(t.id)
    const found = (all || types).find(x => x.id === t.id) || t
    if (presetRequired?.length) return
    const req = [], opt = []
    ;(found.resolved?.required || []).forEach(s => {
      if (s.auto_selected_user_id) req.push(s.auto_selected_user_id)
      else if (s.candidates?.length === 1) req.push(s.candidates[0].id)
    })
    ;(found.resolved?.optional || []).forEach(s => {
      if (s.auto_selected_user_id) opt.push(s.auto_selected_user_id)
    })
    setRequired([...new Set(req)])
    setOptional([...new Set(opt.filter(u => !req.includes(u)))])
  }

  const selectedType = types.find(t => t.id === typeId)
  const effDuration = Number(duration) || selectedType?.duration_minutes || 30

  const ambiguous = useMemo(() => {
    if (!selectedType) return []
    return (selectedType.resolved?.required || [])
      .filter(s => !s.auto_selected_user_id && (s.candidates?.length || 0) !== 1)
  }, [selectedType])

  function toggle(list, setList, id, other, setOther) {
    if (list.includes(id)) setList(list.filter(x => x !== id))
    else {
      setList([...list, id])
      if (other.includes(id)) setOther(other.filter(x => x !== id))
    }
  }

  async function submit() {
    setBusy(true); setError(null); setConflict(false)
    try {
      const slots = {}
      ;(selectedType?.resolved?.required || []).forEach(s => {
        if (s.auto_selected_user_id) slots[s.auto_selected_user_id] = s.slot
      })
      const appt = await api.post('/sales/appointments', {
        // A naive local datetime, exactly as the server's `_parse_dt` expects.
        starts_at: day + 'T' + time + ':00',
        meeting_type_id: typeId || undefined,
        duration_minutes: Number(duration) || undefined,
        opportunity_id: oppId,
        title: title.trim() || undefined,
        required_user_ids: required,
        optional_user_ids: optional,
        role_slot_by_user: slots,
        meeting_provider: format === 'video' ? undefined : format,
        meeting_url: meetingUrl.trim() || undefined,
        location: format === 'in_person' ? (location.trim() || undefined) : undefined,
        notes: notes.trim() || undefined,
        prospect_name: prospectName.trim() || undefined,
        prospect_email: prospectEmail.trim() || undefined,
        prospect_phone: prospectPhone.trim() || undefined,
      })

      // The invitation is a SEPARATE call, deliberately. Booking succeeded the
      // moment the appointment came back; failing to send an email must not
      // make the rep think the meeting did not save.
      if (sendConfirmation && appt?.id) {
        try {
          await api.post('/sales/appointments/' + appt.id + '/resend-invitation', {})
        } catch { /* reported on the appointment row; the booking stands */ }
      }
      onBooked && onBooked(appt)
    } catch (e) {
      const msg = e.message || 'Could not book that time.'
      setError(msg)
      // 409 is not a generic error — it is "that time is gone", and the useful
      // next step is finding one that is not.
      if (e.status === 409 || /already booked|conflicting event|booked one of/i.test(msg)) {
        setConflict(true)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="sw-modal-back"
         onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="sw-modal" style={{ maxWidth: 700 }}>
        <div className="sw-card-h">
          <div>
            <h3>NEW APPOINTMENT</h3>
            <small>
              {opportunity
                ? opportunity.company_name
                  + (opportunity.contact_name ? ' · ' + opportunity.contact_name : '')
                : 'Booked directly — every availability check still applies'}
            </small>
          </div>
          <div className="sw-spacer" />
          <button className="sw-btn" onClick={onClose}>Close</button>
        </div>

        <div className="sw-card-b">
          <ErrorBar error={error} />

          {conflict && (
            <div className="sw-notbuilt">
              <b>THAT TIME IS NO LONGER FREE</b>
              <p>
                The final check before saving found a conflict — either an
                EvoSys Pro meeting or an event that appeared on a connected
                calendar. Nothing was booked. Pick another time, or let the
                finder show you the ones that are genuinely open.
              </p>
              {onFindTime && (
                <button className="sw-tiny sw-primary" style={{ marginTop: 8 }}
                        onClick={() => { onClose(); onFindTime() }}>
                  Find Team Time instead
                </button>
              )}
            </div>
          )}

          <div className="sw-field">
            <label>APPOINTMENT TYPE</label>
            <select className="sw-select" value={typeId}
                    onChange={e => {
                      const t = types.find(x => x.id === e.target.value)
                      if (t) applyType(t)
                    }}>
              {types.map(t => (
                <option key={t.id} value={t.id}>
                  {t.name} · {t.duration_minutes} min
                  {t.is_internal ? ' · internal' : ''}
                </option>
              ))}
            </select>
            {selectedType?.description && (
              <div className="sw-subtle" style={{ marginTop: 6 }}>
                {selectedType.description}
              </div>
            )}
          </div>

          {ambiguous.length > 0 && (
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

          <div className="sw-field">
            <label>REQUIRED — every one of these must be free</label>
            <div className="sw-chips">
              {team.map(m => (
                <button key={m.id} type="button"
                        className={'sw-chip' + (required.includes(m.id) ? ' sw-green' : '')}
                        style={{ cursor: 'pointer' }}
                        onClick={() => toggle(required, setRequired, m.id,
                          optional, setOptional)}>
                  {m.full_name}
                </button>
              ))}
            </div>
          </div>

          <div className="sw-field">
            <label>OPTIONAL — nice to have, never blocks the booking</label>
            <div className="sw-chips">
              {team.filter(m => !required.includes(m.id)).map(m => (
                <button key={m.id} type="button"
                        className={'sw-chip' + (optional.includes(m.id) ? ' sw-blue' : '')}
                        style={{ cursor: 'pointer' }}
                        onClick={() => toggle(optional, setOptional, m.id,
                          required, setRequired)}>
                  {m.full_name}
                </button>
              ))}
            </div>
          </div>

          <div className="sw-grid-even">
            <div className="sw-field">
              <label>DATE</label>
              <input className="sw-input" type="date" value={day}
                     onChange={e => setDay(e.target.value)} />
            </div>
            <div className="sw-field">
              <label>TIME</label>
              <input className="sw-input" type="time" value={time}
                     onChange={e => setTime(e.target.value)} step={900} />
            </div>
          </div>

          <div className="sw-grid-even">
            <div className="sw-field">
              <label>DURATION</label>
              <select className="sw-select" value={duration}
                      onChange={e => setDuration(e.target.value)}>
                <option value="">
                  Type default · {selectedType?.duration_minutes || 30} min
                </option>
                {[15, 30, 45, 60, 90, 120].map(n => (
                  <option key={n} value={n}>{n} minutes</option>
                ))}
              </select>
            </div>
            <div className="sw-field">
              <label>FORMAT</label>
              <div className="cal-seg">
                {FORMATS.map(f => (
                  <button key={f.key} type="button"
                          className={format === f.key ? 'is-on' : ''}
                          onClick={() => setFormat(f.key)}>{f.label}</button>
                ))}
              </div>
            </div>
          </div>

          {format === 'in_person' && (
            <div className="sw-field">
              <label>ADDRESS</label>
              <input className="sw-input" value={location}
                     onChange={e => setLocation(e.target.value)}
                     placeholder="Where the meeting happens" />
            </div>
          )}

          {format === 'video' && (
            <div className="sw-field">
              <label>MEETING LINK — leave blank to have one provisioned</label>
              <input className="sw-input" value={meetingUrl}
                     onChange={e => setMeetingUrl(e.target.value)}
                     placeholder="Optional" />
              <div className="sw-subtle" style={{ marginTop: 5 }}>
                Meeting types configured for video provision their own room when
                the appointment is created.
              </div>
            </div>
          )}

          {/* The prospect. Carried from the deal when there is one, and never
              used to CREATE a record — see the header note. */}
          {!opportunity && (
            <>
              <div className="sw-field">
                <label>TITLE — optional, generated from the type otherwise</label>
                <input className="sw-input" value={title}
                       onChange={e => setTitle(e.target.value)} />
              </div>
              <div className="sw-grid-even">
                <div className="sw-field">
                  <label>PROSPECT NAME</label>
                  <input className="sw-input" value={prospectName}
                         onChange={e => setProspectName(e.target.value)} />
                </div>
                <div className="sw-field">
                  <label>PROSPECT EMAIL</label>
                  <input className="sw-input" type="email" value={prospectEmail}
                         onChange={e => setProspectEmail(e.target.value)} />
                </div>
              </div>
              <div className="sw-field">
                <label>PROSPECT PHONE</label>
                <input className="sw-input" value={prospectPhone}
                       onChange={e => setProspectPhone(e.target.value)} />
              </div>
              <div className="sw-subtle">
                These details are stored on the appointment only. No lead,
                contact or company record is created.
              </div>
            </>
          )}

          <div className="sw-field">
            <label>INTERNAL NOTES</label>
            <textarea className="sw-input" rows={2} value={notes}
                      onChange={e => setNotes(e.target.value)}
                      placeholder="Never sent to the prospect, and never written into a calendar event." />
          </div>

          <label className="cal-toggle">
            <input type="checkbox" checked={sendConfirmation}
                   onChange={e => setSendConfirmation(e.target.checked)} />
            Send the prospect a confirmation invitation
          </label>
          {sendConfirmation && !prospectEmail && !opportunity && (
            <div className="sw-subtle" style={{ marginTop: 5 }}>
              No prospect email, so there is nowhere to send it. The meeting will
              still be booked and synced to the team's calendars.
            </div>
          )}

          <div className="sw-flex sw-mt" style={{ justifyContent: 'space-between' }}>
            <span className="sw-subtle">
              {required.length
                ? required.length + ' required · ' + effDuration + ' min'
                : 'Select at least one required participant'}
            </span>
            <div className="sw-flex" style={{ gap: 8 }}>
              {onFindTime && (
                <button className="sw-btn" onClick={() => { onClose(); onFindTime() }}>
                  Find a time instead
                </button>
              )}
              <button className="sw-btn sw-primary" onClick={submit}
                      disabled={busy || required.length === 0}>
                {busy ? 'Booking…' : 'Book appointment'}
              </button>
            </div>
          </div>

          <div className="sw-subtle" style={{ marginTop: 10 }}>
            Availability is re-checked against every participant's EvoSys Pro
            schedule and their connected calendars at the moment of saving, not
            when this form was opened.
          </div>
        </div>
      </div>
    </div>
  )
}
