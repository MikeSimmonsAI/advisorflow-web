/**
 * EVOSYS PRO MOBILE - BOOK / MOVE / CANCEL A FAMILY'S APPOINTMENT (program
 * workspaces: SCI, Restland).
 *
 * The phone uses the SAME server steps as the website's command center:
 *   GET  /program/command/slots?lead_id=        next open times + where options
 *   GET  /program/command/day-slots?lead_id=&date=   every open time that day
 *   POST /program/command/schedule              book (only "booked" once the
 *                                               calendar reads it back)
 *   POST /program/command/appointments/<id>/reschedule   move
 *   POST /program/command/appointments/<id>/cancel       cancel (notify or not)
 * so the calendar, the family's confirmation and the team alerts behave exactly
 * as they do on the website. Nothing is sent until the person taps Confirm.
 */
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import { Icon, Loading } from './TeamApp'
import { programRequest, upcomingBooking, whenLabel } from './programMobile'

const TZ = 'America/Chicago'

function errText(e, fallback) {
  const d = e && (e.detail || (e.data && e.data.detail))
  if (typeof d === 'string') return d
  return (e && e.message) || fallback
}

function todayCentral() {
  return new Date().toLocaleDateString('en-CA', { timeZone: TZ })
}

const PROGRAM_ROUTES = {
  reschedule: id => '/program/command/appointments/' + encodeURIComponent(id) + '/reschedule',
  cancel: id => '/program/command/appointments/' + encodeURIComponent(id) + '/cancel',
}

/** `appointment` (optional) = an appointment chosen elsewhere (Restland BookIT's
 *  own rows): { id, whenText, label, staff, status }, moved and cancelled through
 *  `routes` (BookIT's own reschedule / cancel). */
export default function AppointmentSheet({ item, leadId, name, bookings, appointment = null, routes = PROGRAM_ROUTES, startMode = null, onClose, onDone }) {
  const req = useMemo(() => programRequest(item), [item])
  const current = appointment || upcomingBooking(bookings)
  const wrap = routes.wrap || (b => b)
  // mode: view (has one) | pick (choose a time to book or move to) | cancel
  const [mode, setMode] = useState(startMode || (current ? 'view' : 'pick'))
  const [slots, setSlots] = useState(null)
  const [day, setDay] = useState('')
  const [daySlots, setDaySlots] = useState(null)
  const [pick, setPick] = useState(null)
  const [where, setWhere] = useState('')
  const [addr, setAddr] = useState('')
  const [notify, setNotify] = useState(true)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [result, setResult] = useState(null)
  const moving = !!current && mode === 'pick'

  useEffect(() => {
    if (mode !== 'pick' || slots) return undefined
    let live = true
    api.get('/program/command/slots?lead_id=' + encodeURIComponent(leadId), req)
      .then(s => { if (!live) return; setSlots(s || { slots: [] }); const o = (s && s.meeting_options) || []; if (o.length && !where) setWhere(o[0].key) })
      .catch(() => { if (live) setSlots({ slots: [], reason: 'The calendar could not be reached.' }) })
    return () => { live = false }
  }, [mode, slots, leadId, req, where])
  useEffect(() => {
    if (!day) { setDaySlots(null); return undefined }
    let live = true
    setDaySlots('loading'); setPick(null)
    api.get('/program/command/day-slots?lead_id=' + encodeURIComponent(leadId) + '&date=' + day, req)
      .then(s => { if (live) setDaySlots(s || { slots: [] }) })
      .catch(() => { if (live) setDaySlots({ slots: [], reason: 'The calendar could not be reached.' }) })
    return () => { live = false }
  }, [day, leadId, req])

  const options = (slots && slots.meeting_options) || []
  const opt = options.find(o => o.key === where)
  const list = day ? (daySlots && daySlots !== 'loading' ? daySlots.slots : []) : ((slots && slots.slots) || [])
  const why = day ? (daySlots && daySlots !== 'loading' ? daySlots.reason : null) : (slots && slots.reason)

  async function confirmPick() {
    if (!pick) return
    if (!moving && opt && opt.needs_address && !addr.trim()) { setErr('Add the family’s address for a home visit.'); return }
    setBusy(true); setErr(null)
    try {
      if (moving) {
        const r = await api.post(routes.reschedule(current.id), wrap({ slot: pick.value }), req)
        setResult({ ok: true, msg: (r && r.message) || 'Moved to ' + pick.label + '. ' + name + ' gets the usual “moved” note.' })
      } else {
        const place = options.length ? { meeting_key: where || null, visit_address: opt && opt.needs_address ? addr.trim() : null } : {}
        const r = await api.post('/program/command/schedule', { lead_id: leadId, slot: pick.value, kind: 'visit', ...place }, req)
        if (r && r.ok) {
          setResult({ ok: true, msg: r.simulated ? 'Test family: ' + r.label + ' is open, but nothing was booked or sent.'
            : 'Booked ' + r.label + '. ' + name + ' gets the confirmation, and it’s on the calendar.' })
        } else {
          setErr(r && r.reason ? 'Not booked: ' + r.reason + '.' : 'Not booked: that time is no longer open. Pick another.')
          setSlots(null); setPick(null); setBusy(false); return
        }
      }
      onDone && onDone()
    } catch (e) { setErr(errText(e, 'That did not go through. Nothing was changed.')) } finally { setBusy(false) }
  }

  async function confirmCancel() {
    setBusy(true); setErr(null)
    try {
      await api.post(routes.cancel(current.id), wrap({ notify }), req)
      setResult({ ok: true, msg: 'Cancelled.' + (notify ? ' ' + name + ' gets the usual cancellation note.' : ' Nothing was sent to the family.') })
      onDone && onDone()
    } catch (e) { setErr(errText(e, 'Not cancelled. Nothing was changed.')) } finally { setBusy(false) }
  }

  return (
    <div className="ta-sheet" role="presentation" onClick={e => { if (e.target === e.currentTarget && !busy) onClose() }}>
      <div className="ta-sheet-card ta-appt" role="dialog" aria-modal="true" aria-labelledby="ta-appt-h">
        <div className="ta-sheet-grip" aria-hidden />
        <h2 id="ta-appt-h" className="ta-sheet-title">{result ? 'Done' : mode === 'cancel' ? 'Cancel appointment' : moving ? 'Move appointment' : current ? 'Appointment' : 'Book a time'}</h2>
        <p className="ta-muted ta-small">{name}</p>

        {result && <>
          <div className={'ta-note' + (result.ok ? ' ta-note--ok' : ' ta-note--err')} role="status">{result.msg}</div>
          <button type="button" className="ta-btn ta-btn--primary ta-btn--block" onClick={onClose}>Close</button>
        </>}

        {!result && mode === 'view' && current && <>
          <div className="ta-card ta-appt-now" data-booking={current.id}>
            <Icon name="calendar" size={26} />
            <div>
              <div className="ta-appt-when">{current.whenText || whenLabel(current.when, TZ)}</div>
              <div className="ta-row-sub">{[current.label, current.staff, current.status === 'confirmed' ? 'Confirmed' : null].filter(Boolean).join(' · ')}</div>
            </div>
          </div>
          <div className="ta-sheet-actions ta-appt-actions">
            <button type="button" className="ta-btn ta-btn--ghost" onClick={() => { setMode('cancel'); setErr(null) }}>Cancel</button>
            <button type="button" className="ta-btn ta-btn--primary" onClick={() => { setMode('pick'); setErr(null) }}>Reschedule</button>
          </div>
        </>}

        {!result && mode === 'cancel' && current && <>
          <p>Cancel <strong>{current.whenText || whenLabel(current.when, TZ)}</strong>{current.label ? ' (' + current.label + ')' : ''}?</p>
          <label className="ta-keep"><input type="checkbox" checked={notify} onChange={e => setNotify(e.target.checked)} />
            <span>Tell {name.split(' ')[0] || 'the family'} it’s cancelled (the usual note)</span></label>
          <div className="ta-sheet-actions">
            <button type="button" className="ta-btn ta-btn--ghost" onClick={() => setMode('view')} disabled={busy}>Keep it</button>
            <button type="button" className="ta-btn ta-btn--danger" onClick={confirmCancel} disabled={busy}>{busy ? 'Cancelling…' : 'Confirm cancel'}</button>
          </div>
        </>}

        {!result && mode === 'pick' && <>
          {!moving && options.length > 1 && (
            <div className="ta-chips ta-chips--wrap" role="group" aria-label="Where">
              {options.map(o => <button key={o.key} type="button" className={'ta-chip' + (where === o.key ? ' is-on' : '')} aria-pressed={where === o.key} onClick={() => setWhere(o.key)}>{o.label}</button>)}
            </div>
          )}
          {!moving && opt && opt.needs_address && (
            <label className="ta-lfield"><span>Family’s address</span><input value={addr} onChange={e => setAddr(e.target.value)} autoComplete="off" /></label>
          )}
          <div className="ta-appt-day">
            <span className="ta-h3">{day ? 'Open times that day' : 'Next open times'}</span>
            <label className="ta-linkbtn ta-appt-daypick">
              {day ? 'Change day' : 'Pick a day'}
              <input type="date" min={todayCentral()} value={day} onChange={e => setDay(e.target.value)} aria-label="Pick a day" />
            </label>
          </div>
          {((!day && !slots) || daySlots === 'loading') ? <Loading /> : list.length === 0
            ? <div className="ta-card ta-muted">{why || 'No open times. Pick another day.'}</div>
            : <div className="ta-slots" role="radiogroup" aria-label="Open times">
                {list.map(s => (
                  <button key={s.value} type="button" role="radio" aria-checked={!!pick && pick.value === s.value}
                          className={'ta-slot' + (pick && pick.value === s.value ? ' is-on' : '')} onClick={() => setPick(s)}>
                    <span>{s.day}</span><strong>{s.time}</strong></button>
                ))}
              </div>}
          {pick && <p className="ta-small ta-muted">{moving ? 'Moves it to ' : 'Books '}<strong>{pick.label}</strong>. {name.split(' ')[0] || 'The family'} gets the usual {moving ? '“moved”' : 'confirmation'} message.</p>}
          <div className="ta-sheet-actions">
            <button type="button" className="ta-btn ta-btn--ghost" onClick={() => (current ? setMode('view') : onClose())} disabled={busy}>Back</button>
            <button type="button" className="ta-btn ta-btn--primary" onClick={confirmPick} disabled={busy || !pick}>
              {busy ? 'Saving…' : moving ? 'Confirm new time' : 'Confirm booking'}</button>
          </div>
        </>}
        {err && <div className="ta-note ta-note--err" role="alert">{err}</div>}
      </div>
    </div>
  )
}
