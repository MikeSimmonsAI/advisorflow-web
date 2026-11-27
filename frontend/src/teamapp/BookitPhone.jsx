/**
 * EVOSYS PRO MOBILE - RESTLAND BOOKIT ON THE PHONE (owner preview).
 *
 * BookIT's own day, from BookIT's own server routes (nothing new on the server):
 *   GET  /restland-bookit/my-day   today's numbers (KPIs)
 *   GET  /restland-bookit/work     this person's appointments and follow-ups
 *                                  (a manager sees the team's, as on the web)
 * A tap opens the family: Call / Text / Email, and for an appointment Move /
 * Cancel through BookIT's own routes (POST /restland-bookit/appointments/<row>/
 * reschedule | cancel - the time must be open, the calendar event moves, the
 * family gets the usual note). A follow-up can be booked into a real open time
 * (the program's /program/command/schedule, same as the website).
 */
import { useMemo, useState } from 'react'
import { Icon, Loading, ErrorNote } from './TeamApp'
import { useProgramGet } from './ProgramWorkspace'
import AppointmentSheet from './AppointmentSheet'
import { contactLinks } from './programMobile'
import { formatPhone } from '../utils/phone'

const BOOKIT_ROUTES = {
  reschedule: id => '/restland-bookit/appointments/' + encodeURIComponent(id) + '/reschedule',
  cancel: id => '/restland-bookit/appointments/' + encodeURIComponent(id) + '/cancel',
  wrap: body => ({ data: body }),   // BookIT's routes take { data: {...} }
}

function todayCentral() { return new Date().toLocaleDateString('en-CA', { timeZone: 'America/Chicago' }) }

/** "Today 10:00 AM" / "Tomorrow 1:00 PM" / "Mon, Dec 1 · 9:30 AM" from BookIT's date + clock text. */
export function bookitWhen(date, time, today = todayCentral()) {
  if (!date) return time || 'No date'
  const d = new Date(date + 'T12:00:00')
  const t = new Date(today + 'T12:00:00')
  const days = Math.round((d - t) / 86400000)
  const day = days === 0 ? 'Today' : days === 1 ? 'Tomorrow' : days === -1 ? 'Yesterday'
    : d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }) + ' ·'
  return (day + ' ' + (time || '')).trim()
}

/** Upcoming (today on, not cancelled) appointments first by date; open follow-ups due now. */
export function bookitLists(work, today = todayCentral()) {
  const appts = ((work && work.appointments) || [])
    .filter(a => a.date && a.date >= today && !String(a.status || '').startsWith('Cancel'))
    .sort((a, b) => (a.date + (a.time || '')).localeCompare(b.date + (b.time || '')))
  const due = ((work && work.follow_ups) || []).filter(f => f.open && f.due_now)
    .sort((a, b) => String(a.follow_up_on || '').localeCompare(String(b.follow_up_on || '')))
  return { appts, due }
}

export function BookitDashboard({ item }) {
  const day = useProgramGet(item, '/restland-bookit/my-day')
  const work = useProgramGet(item, '/restland-bookit/work')
  const [open, setOpen] = useState(null)   // { kind: 'appt'|'fu', row }
  const [book, setBook] = useState(null)   // { row, appointment? }
  const today = todayCentral()
  const { appts, due } = useMemo(() => bookitLists(work.data, today), [work.data, today])
  const k = (day.data && day.data.kpis) || {}
  if (work.error) return <ErrorNote error={work.error} onRetry={work.reload} />
  if (!work.data) return <Loading />
  const todayAppts = appts.filter(a => a.date === today)
  const later = appts.filter(a => a.date !== today).slice(0, 30)
  return (
    <div className="ta-bookit">
      <div className="ta-kpis" role="list">
        <Kpi n={k.appointments_today ?? todayAppts.length} label="Appointments today" />
        <Kpi n={k.follow_ups_due ?? due.length} label="Follow-ups due" warn={k.follow_ups_overdue > 0} sub={k.follow_ups_overdue ? k.follow_ups_overdue + ' overdue' : null} />
        <Kpi n={k.upcoming_appointments ?? appts.length} label="Upcoming" />
      </div>

      <Section title="Today" empty="No appointments today.">
        {todayAppts.map(a => <ApptRow key={a.id} a={a} onOpen={() => setOpen({ kind: 'appt', row: a })} />)}
      </Section>
      <Section title={'Follow-ups due (' + due.length + ')'} empty="No follow-ups due. Nice.">
        {due.map(f => <FuRow key={f.id} f={f} today={today} onOpen={() => setOpen({ kind: 'fu', row: f })} />)}
      </Section>
      <Section title="Coming up" empty="Nothing else on the calendar.">
        {later.map(a => <ApptRow key={a.id} a={a} onOpen={() => setOpen({ kind: 'appt', row: a })} />)}
      </Section>

      {open && <FamilySheet open={open} onClose={() => setOpen(null)}
                            onMove={(row, mode) => { setOpen(null); setBook({ row, mode, appointment: { id: row.id, whenText: bookitWhen(row.date, row.time, today), label: row.visit_detail || row.how, staff: row.assigned, status: row.status } }) }}
                            onBook={row => { setOpen(null); setBook({ row }) }} />}
      {book && <AppointmentSheet item={item} leadId={book.row.lead_id} name={book.row.family || 'The family'}
                                 bookings={[]} appointment={book.appointment || null} routes={BOOKIT_ROUTES} startMode={book.mode}
                                 onClose={() => setBook(null)} onDone={() => { work.reload(); day.reload() }} />}
    </div>
  )
}

function Kpi({ n, label, warn, sub }) {
  return (
    <div className={'ta-kpi' + (warn ? ' is-warn' : '')} role="listitem">
      <strong>{n ?? '–'}</strong><span>{label}</span>{sub && <small>{sub}</small>}
    </div>
  )
}

function Section({ title, empty, children }) {
  const rows = [].concat(children || []).filter(Boolean)
  return (
    <section className="ta-section">
      <h2 className="ta-h2">{title}</h2>
      {rows.length === 0 ? <div className="ta-card ta-muted">{empty}</div> : <ul className="ta-cards">{rows}</ul>}
    </section>
  )
}

function ApptRow({ a, onOpen }) {
  return (
    <li>
      <button type="button" className="ta-acard ta-bk-row" style={{ '--acc': '#087F68' }} onClick={onOpen} data-appt={a.id}>
        <span className="ta-acard-tile" aria-hidden><Icon name="calendar" size={22} /></span>
        <span className="ta-prow-main">
          <span className="ta-acard-title">{a.family || 'Family'}</span>
          <span className="ta-acard-time">{bookitWhen(a.date, a.time)}</span>
          <span className="ta-arow-sub">{[a.visit_detail || a.how, a.assigned, a.status].filter(Boolean).join(' · ')}</span>
        </span>
        <Icon name="chev" size={20} />
      </button>
    </li>
  )
}

function FuRow({ f, today, onOpen }) {
  const late = f.follow_up_on && f.follow_up_on < today
  return (
    <li>
      <button type="button" className={'ta-acard ta-bk-row' + (late ? ' is-late' : '')} style={{ '--acc': late ? '#C62839' : '#2563EB' }} onClick={onOpen} data-fu={f.id}>
        <span className="ta-acard-tile" aria-hidden><Icon name="phone" size={22} /></span>
        <span className="ta-prow-main">
          <span className="ta-acard-title">{f.family || 'Family'}</span>
          <span className="ta-arow-sub">{(f.why || 'Follow up').split('\n')[0]}</span>
          <span className="ta-acard-time">{late ? 'Overdue · ' : ''}{f.follow_up_on ? bookitWhen(f.follow_up_on, '') : 'Due now'}{f.assigned ? ' · ' + f.assigned : ''}</span>
        </span>
        <Icon name="chev" size={20} />
      </button>
    </li>
  )
}

function FamilySheet({ open, onClose, onMove, onBook }) {
  const r = open.row
  const links = contactLinks(r)
  const appt = open.kind === 'appt'
  return (
    <div className="ta-sheet" role="presentation" onClick={e => { if (e.target === e.currentTarget) onClose() }}>
      <div className="ta-sheet-card" role="dialog" aria-modal="true" aria-labelledby="ta-bkf-h">
        <div className="ta-sheet-grip" aria-hidden />
        <h2 id="ta-bkf-h" className="ta-sheet-title">{r.family || 'Family'}</h2>
        <p className="ta-muted ta-small">{[formatPhone(r.phone), r.email].filter(Boolean).join(' · ') || 'No phone or email on file'}</p>
        {appt
          ? <div className="ta-card ta-appt-now"><Icon name="calendar" size={24} /><div>
              <div className="ta-appt-when">{bookitWhen(r.date, r.time)}</div>
              <div className="ta-row-sub">{[r.visit_detail || r.how, r.home_address, r.assigned, r.status].filter(Boolean).join(' · ')}</div></div></div>
          : <div className="ta-card"><div className="ta-row-name">{(r.why || 'Follow up').split('\n')[0]}</div>
              <div className="ta-row-sub">{r.follow_up_on ? 'Due ' + bookitWhen(r.follow_up_on, '') : 'Due now'}{r.assigned ? ' · ' + r.assigned : ''}</div>
              {r.notes && <p className="ta-small">{r.notes}</p>}</div>}
        <div className="ta-contact" role="group" aria-label="Contact">
          <Btn href={links.call} icon="phone" label="Call" />
          <Btn href={links.text} icon="sms" label="Text" />
          <Btn href={links.email} icon="mail" label="Email" />
        </div>
        {appt
          ? <div className="ta-sheet-actions ta-appt-actions">
              <button type="button" className="ta-btn ta-btn--primary" onClick={() => onMove(r, 'pick')} disabled={!r.lead_id}>Reschedule</button>
              <button type="button" className="ta-btn ta-btn--danger" onClick={() => onMove(r, 'cancel')}>Cancel</button>
            </div>
          : <div className="ta-sheet-actions ta-appt-actions">
              <button type="button" className="ta-btn ta-btn--primary" onClick={() => onBook(r)} disabled={!r.lead_id}>Book a time</button>
            </div>}
        {!r.lead_id && <p className="ta-muted ta-small">This row isn’t linked to a family record yet, so booking needs the full workspace.</p>}
        <p className="ta-muted ta-small">To record what happened, use Open Full Workspace (coming to the phone next).</p>
      </div>
    </div>
  )
}

function Btn({ href, icon, label }) {
  if (!href) return <span className="ta-cbtn is-off" aria-disabled="true"><Icon name={icon} size={24} /><span>{label}</span></span>
  return <a className="ta-cbtn" href={href}><Icon name={icon} size={24} /><span>{label}</span></a>
}
