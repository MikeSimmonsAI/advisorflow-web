/* Wholesale > Appointments. For a rep this is "my day": every visit today, in
 * order, with directions, the seller's number and one tap to mark it done.
 * For you and managers it is the team's calendar: who is going where, with
 * reassign, move and cancel. Server: /wholesale/appointments. */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import { Alert, Empty, EvoApp, PageHead, Seg, Skeleton, Tag } from '../ds/ds'
import '../ds/evo-pages.css'
import BookAppointment from './BookAppointment'
import VisitDrawer from './VisitDrawer'
import { centralToIso, dayLabel, isoToCentral, todayCentral } from './apptTime'
import './appts.css'

const OUTCOME = { signed: ['Signed', 'live'], follow_up: ['Follow up', 'info'], dead: ['Dead', 'danger'] }
const STATUS = { scheduled: ['Scheduled', 'info'], completed: ['Done', 'live'], no_show: ['No-show', 'danger'], cancelled: ['Cancelled', null] }

export function ApptCard({ a, isField, reps, onChanged }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const [more, setMore] = useState(false)
  const [visit, setVisit] = useState(false)
  const [logging, setLogging] = useState(false)
  const [fuDay, setFuDay] = useState('')
  const [fuTime, setFuTime] = useState('10:00')
  const start = isoToCentral(a.starts_at)
  const [day, setDay] = useState(start.day)
  const [time, setTime] = useState(start.time)
  const [note, setNote] = useState('')
  const [label, tone] = STATUS[a.status] || [a.status, null]
  const open = a.status === 'scheduled'

  async function patch(body) {
    setBusy(true); setErr(null)
    try { onChanged(await api.patch(`/wholesale/appointments/${a.id}`, body)); setMore(false); setLogging(false); setNote('') }
    catch (e) { setErr(e.message || 'That did not save.') } finally { setBusy(false) }
  }
  const withNote = (body) => (note.trim() ? { ...body, notes: [a.notes, note.trim()].filter(Boolean).join('\n') } : body)

  return (
    <li className={`ap-card${open ? '' : ' is-done'}`}>
      <div className="ap-card__top">
        <span className="ap-time">{a.local_time}</span>
        <span className="ap-title">{a.title || 'Visit'}</span>
        <Tag kind={tone || undefined}>{label}</Tag>
        {a.outcome ? <Tag kind={(OUTCOME[a.outcome] || [])[1] || undefined}>{(OUTCOME[a.outcome] || [a.outcome])[0]}</Tag> : null}
        {a.is_test ? <Tag kind="sandbox">Test</Tag> : null}
      </div>
      {a.address ? <div className="ap-addr">{a.address}</div> : null}
      <div className="ap-meta">
        {a.seller_name ? <span>Seller: <strong>{a.seller_name}</strong></span> : null}
        {a.seller_phone ? <a href={`tel:${a.seller_phone}`}>{a.seller_phone}</a> : null}
        <span>{a.duration_minutes} min</span>
        <span>{a.assigned_to_name ? `Rep: ${a.assigned_to_name}` : 'No rep yet'}</span>
      </div>
      {a.notes ? <p className="ap-notes">{a.notes}</p> : null}
      {a.outcome_note ? <p className="ap-notes"><strong>How it went:</strong> {a.outcome_note}</p> : null}
      <div className="ap-actions">
        {a.directions_url ? <a className="evo-btn evo-btn--secondary evo-btn--sm" href={a.directions_url} target="_blank" rel="noreferrer">Directions</a> : null}
        <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" onClick={() => setVisit(true)}>Visit notes</button>
        {a.evosense_property_id ? <Link className="evo-btn evo-btn--ghost evo-btn--sm" to={`/wholesale/evosense/property/${a.evosense_property_id}`}>Open property</Link> : null}
        {open || (a.status === 'completed' && !a.outcome) ? <button type="button" className="evo-btn evo-btn--primary evo-btn--sm" disabled={busy} onClick={() => setLogging((v) => !v)} aria-expanded={logging}>Log outcome</button> : null}
        {open ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" disabled={busy} onClick={() => patch(withNote({ status: 'no_show' }))}>No-show</button> : null}
        {!isField ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setMore((v) => !v)} aria-expanded={more}>{more ? 'Close' : 'Change'}</button> : null}
      </div>
      {open && !logging ? (
        <input className="ap-input" placeholder="Add a note (optional) - saved with No-show"
               value={note} onChange={(e) => setNote(e.target.value)} aria-label="Note" />
      ) : null}
      {logging ? (
        <div className="ap-outcome">
          <textarea className="ap-input" rows={2} placeholder="How did it go? (what they said, next step)"
                    value={note} onChange={(e) => setNote(e.target.value)} aria-label="How it went" />
          <div className="ap-actions">
            <button type="button" className="evo-btn evo-btn--success evo-btn--sm" disabled={busy}
                    onClick={() => patch({ outcome: 'signed', outcome_note: note })}>Signed</button>
            <button type="button" className="evo-btn evo-btn--danger evo-btn--sm" disabled={busy}
                    onClick={() => patch({ outcome: 'dead', outcome_note: note })}>Dead</button>
          </div>
          <div className="ap-more">
            <label className="ap-lab">Follow up on<input type="date" className="ap-input" value={fuDay} onChange={(e) => setFuDay(e.target.value)} /></label>
            <label className="ap-lab">at<input type="time" step="900" className="ap-input" value={fuTime} onChange={(e) => setFuTime(e.target.value)} /></label>
            <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy || !fuDay}
                    onClick={() => patch({ outcome: 'follow_up', outcome_note: note, follow_up_at: centralToIso(fuDay, fuTime) })}>Follow up</button>
          </div>
          <p className="evo-muted evo-small" style={{ margin: 0 }}>Follow up puts a call-back in the Callback Center for whoever booked this visit.</p>
        </div>
      ) : null}
      {more && !isField ? (
        <div className="ap-more">
          <label className="ap-lab">Day<input type="date" className="ap-input" value={day} onChange={(e) => setDay(e.target.value)} /></label>
          <label className="ap-lab">Time<input type="time" step="900" className="ap-input" value={time} onChange={(e) => setTime(e.target.value)} /></label>
          <button type="button" className="evo-btn evo-btn--secondary evo-btn--sm" disabled={busy}
                  onClick={() => patch({ starts_at: centralToIso(day, time) })}>Move</button>
          <label className="ap-lab">Rep
            <select className="ap-input" value={a.assigned_to_id || ''} disabled={busy}
                    onChange={(e) => patch({ assigned_to_id: e.target.value })}>
              <option value="">Nobody yet</option>
              {reps.map((r) => <option key={r.user_id} value={r.user_id}>{r.name}</option>)}
            </select></label>
          {a.status !== 'cancelled'
            ? <button type="button" className="evo-btn evo-btn--danger evo-btn--sm" disabled={busy} onClick={() => patch({ status: 'cancelled' })}>Cancel visit</button>
            : <span />}
        </div>
      ) : null}
      <Alert>{err}</Alert>
      {visit ? <VisitDrawer open={visit} onClose={() => setVisit(false)} appt={a} /> : null}
    </li>
  )
}

export default function WholesaleAppointments() {
  const [when, setWhen] = useState('today')
  const [day, setDay] = useState(todayCentral())
  const [rep, setRep] = useState('')
  const [data, setData] = useState(null)
  const [reps, setReps] = useState([])
  const [err, setErr] = useState(null)
  const [booking, setBooking] = useState(false)
  const [notice, setNotice] = useState(null)

  const load = useCallback(async () => {
    const q = new URLSearchParams({ when })
    if (when === 'today') q.set('day', day)
    if (rep) q.set('rep', rep)
    try { setData(await api.get(`/wholesale/appointments?${q}`)); setErr(null) }
    catch (e) { setErr(e.message || 'Appointments could not be loaded.') }
  }, [when, day, rep])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    api.get('/wholesale/appointments/reps').then((r) => setReps(r.items || [])).catch(() => setReps([]))
  }, [])

  const isField = !!data?.is_field
  const items = data?.items || []
  const replace = (a) => setData((d) => ({ ...d, items: d.items.map((x) => (x.id === a.id ? a : x)) }))
  const groups = when === 'today' ? [[day, items]] : Object.entries(items.reduce((g, a) => {
    (g[a.local_date] ||= []).push(a); return g
  }, {}))
  const isToday = when === 'today' && day === todayCentral()

  return (
    <EvoApp world="operations">
      <PageHead world="operations" eyebrow="Wholesale" title={isField ? 'My appointments' : 'Appointments'}
                sub={isField ? 'Your visits, in order. Tap Directions to drive there.' : 'Who is going where, and when. All times are Central.'}
                actions={data?.can_book ? <button type="button" className="evo-btn evo-btn--primary" onClick={() => setBooking(true)}>Book appointment</button> : null} />
      <div className="ap-bar">
        <Seg label="Which appointments" value={when} onChange={setWhen}
             items={[{ key: 'today', label: 'Day' }, { key: 'upcoming', label: 'Upcoming' }, { key: 'past', label: 'Past' }]} />
        {when === 'today' ? (
          <>
            <input type="date" className="ap-input" value={day} onChange={(e) => setDay(e.target.value || todayCentral())} aria-label="Day" />
            {!isToday ? <button type="button" className="evo-btn evo-btn--ghost evo-btn--sm" onClick={() => setDay(todayCentral())}>Today</button> : null}
          </>
        ) : null}
        {!isField ? (
          <select className="ap-input" value={rep} onChange={(e) => setRep(e.target.value)} aria-label="Rep">
            <option value="">Everyone</option>
            <option value="me">Mine</option>
            <option value="unassigned">No rep yet</option>
            {reps.map((r) => <option key={r.user_id} value={r.user_id}>{r.name}</option>)}
          </select>
        ) : null}
      </div>
      <Alert kind="ok">{notice}</Alert>
      <Alert>{err}</Alert>
      {!data && !err ? <Skeleton rows={4} height={60} /> : null}
      {data && !items.length ? (
        <Empty title={when === 'today' ? (isToday ? 'Nothing booked today' : `Nothing booked ${dayLabel(day)}`) : 'Nothing here'}>
          {data.can_book ? 'Book one from a property, from the Callback Center, or with the button above.' : 'When your manager books a visit for you it shows up here.'}
        </Empty>
      ) : null}
      {groups.map(([d, list]) => (
        <section key={d} aria-label={dayLabel(d)}>
          {when !== 'today' ? <h2 className="ap-day">{dayLabel(d)}</h2> : null}
          <ul className="ap-list">
            {list.map((a) => <ApptCard key={a.id} a={a} isField={isField} reps={reps} onChanged={replace} />)}
          </ul>
        </section>
      ))}
      <BookAppointment open={booking} onClose={() => setBooking(false)}
                       onBooked={(a) => { setNotice(`Booked for ${dayLabel(a.local_date)} at ${a.local_time}.`); load() }} />
    </EvoApp>
  )
}
