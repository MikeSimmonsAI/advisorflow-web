/* Appointments: booked visits for the program's families (GET /program/appointments),
   grouped by day, filtered by the location chosen at the top. Click one to open
   the family's record. */
import { useEffect, useState } from 'react'
import { api } from '../../../api/client'
import { Chip, Empty, ErrorLine, FilterChips, Kpi, Loading, PageHead, Panel, errText, num, when as ago } from './ui'

const WHEN = [['upcoming', 'Upcoming'], ['past', 'Past'], ['all', 'All']]
const STATUS = { booked: ['Booked', 'ok'], confirmed: ['Confirmed', 'ok'], cancelled: ['Cancelled', 'warn'], completed: ['Completed', ''] }

// appointment times: Central wall time with its offset - shown in Central on any device
const dayKey = iso => new Date(iso).toLocaleDateString([], { timeZone: 'America/Chicago', weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
const timeOf = iso => new Date(iso).toLocaleTimeString([], { timeZone: 'America/Chicago', hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })

export default function Appointments({ locationId, openPerson }) {
  const [when, setWhen] = useState('upcoming')
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [reload, setReload] = useState(0)

  useEffect(() => {
    let alive = true
    setData(null); setErr('')
    const p = new URLSearchParams({ when })
    if (locationId) p.set('location_id', locationId)
    api.get(`/program/appointments?${p}`)
      .then(d => { if (alive) setData(d) })
      .catch(e => { if (alive) { setErr(errText(e)); setData({ items: [] }) } })
    return () => { alive = false }
  }, [when, locationId, reload])

  const groups = []
  for (const a of data?.items || []) {
    const k = dayKey(a.when)
    const g = groups.find(x => x.k === k)
    if (g) g.items.push(a); else groups.push({ k, items: [a] })
  }

  return (
    <>
      <PageHead title="Appointments" sub="Visits families booked. Times show in your own time zone." />
      <ErrorLine text={err} />
      <MessageSettings />
      <PendingAttendance onDone={() => setReload(x => x + 1)} openPerson={openPerson} reload={reload} />
      <BookingActivity openPerson={openPerson} />
      <Panel title="Booked visits" labelledBy="sci-appts"
        aside={<span className="sci-micro sci-muted">{data ? `${num(data.items.length)} shown` : 'Loading…'}</span>}>
        <div style={{ marginBottom: 12 }}><FilterChips items={WHEN} value={when} onChange={setWhen} label="Which appointments" /></div>
        {!data ? <Loading /> : !groups.length ? (
          <Empty icon="calendar" title={when === 'upcoming' ? 'No upcoming visits' : 'No visits here'}>
            When a family books from their link, the visit shows up here.
          </Empty>
        ) : groups.map(g => (
          <div key={g.k} style={{ marginBottom: 16 }}>
            <h3 className="sci-h3" style={{ margin: '4px 0 8px' }}>{g.k}</h3>
            <div className="sci-tablewrap">
              <table className="sci-table compact">
                <tbody>
                  {g.items.map(a => (
                    <tr key={a.id} style={{ cursor: 'pointer' }} onClick={() => openPerson(a.lead_id)}>
                      <td style={{ width: 90, whiteSpace: 'nowrap' }}><b>{timeOf(a.when)}</b></td>
                      <td className="sci-cell-main">
                        <button type="button" className="sci-namebtn" onClick={e => { e.stopPropagation(); openPerson(a.lead_id) }}>{a.name}</button>
                        <small>{[a.phone, a.email].filter(Boolean).join(' · ')}</small>
                      </td>
                      <td className="sci-small">{a.meeting_type === 'home' ? 'Family\'s home' : (a.location || '—')}<span className="sci-micro sci-muted" style={{ display: 'block' }}>{a.label || 'Visit'}{a.minutes ? ` · ${a.minutes} min` : ''}</span>
                        {a.meeting_type === 'home' && a.visit_address ? <span className="sci-micro" style={{ display: 'block' }}>{a.visit_address}</span> : null}</td>
                      <td className="sci-small" onClick={e => e.stopPropagation()}>
                        <AssignTo appt={a} onDone={() => setReload(x => x + 1)} />
                        <ApptActions appt={a} onDone={() => setReload(x => x + 1)} />
                      </td>
                      <td style={{ textAlign: 'right' }}><Chip tone={(STATUS[a.status] || [])[1] || ''} plain>{(STATUS[a.status] || [a.status])[0]}</Chip></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
      </Panel>
    </>
  )
}


/* BOOKING PAGE ACTIVITY: how many families opened their booking link, looked
   at times and booked - and WHEN (hour of day, weekday, Central time), so the
   morning-vs-afternoon question has an answer. GET /program/booking-activity. */
const EVENT = { opened: 'Opened booking page', day_viewed: 'Looked at times', booked: 'Booked' }
const hourLabel = h => `${h % 12 === 0 ? 12 : h % 12}${h < 12 ? 'a' : 'p'}`

function Bars({ rows, labelKey, fmt }) {
  const max = Math.max(1, ...rows.map(r => r.opened))
  return (
    <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 120, padding: '8px 0 0' }}>
      {rows.map(r => (
        <div key={r[labelKey]} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 4 }}
          title={`${fmt(r[labelKey])}: ${r.opened} opened, ${r.booked} booked`}>
          <div style={{ width: '100%', height: 90, display: 'flex', alignItems: 'flex-end', gap: 2 }}>
            <div style={{ flex: 1, height: `${(r.opened / max) * 100}%`, minHeight: r.opened ? 3 : 0, background: '#2f6f73', borderRadius: '3px 3px 0 0' }} />
            <div style={{ flex: 1, height: `${(r.booked / max) * 100}%`, minHeight: r.booked ? 3 : 0, background: '#c9a24a', borderRadius: '3px 3px 0 0' }} />
          </div>
          <span className="sci-micro sci-muted" style={{ fontSize: 10.5 }}>{fmt(r[labelKey])}</span>
        </div>
      ))}
    </div>
  )
}

function BookingActivity({ openPerson }) {
  const [d, setD] = useState(null)
  const [days, setDays] = useState(30)
  useEffect(() => { setD(null); api.get(`/program/booking-activity?days=${days}`).then(setD).catch(() => setD({ error: true })) }, [days])
  if (!d) return <Panel title="Booking page activity"><Loading /></Panel>
  if (d.error) return null
  return (
    <Panel title="Booking page activity" labelledBy="sci-book-act"
      aside={<FilterChips items={[[7, '7 days'], [30, '30 days'], [90, '90 days']]} value={days} onChange={setDays} label="Period" />}>
      <section className="sci-kpis four" aria-label="Booking summary" style={{ marginBottom: 12 }}>
        <Kpi icon="users" tone="blue" value={d.opened_people} label="Families opened the page" note={`${num(d.open_events)} visits in all`} />
        <Kpi icon="clock" tone="slate" value={d.looked_at_times_people} label="Looked at times" />
        <Kpi icon="check" tone="green" value={d.booked_people} label="Booked" />
        <Kpi icon="bolt" tone="gold" value={d.booking_rate == null ? '—' : `${d.booking_rate}%`} label="Opened → booked" />
      </section>
      <div className="sci-grid-even">
        <div className="sci-card">
          <h3 className="sci-h3" style={{ marginBottom: 0 }}>By time of day <span className="sci-micro sci-muted">(Central)</span></h3>
          <Bars rows={d.by_hour} labelKey="hour" fmt={hourLabel} />
        </div>
        <div className="sci-card">
          <h3 className="sci-h3" style={{ marginBottom: 0 }}>By day of week</h3>
          <Bars rows={d.by_weekday} labelKey="day" fmt={x => x} />
        </div>
      </div>
      <p className="sci-micro sci-muted" style={{ margin: '8px 0 12px' }}>
        <span style={{ display: 'inline-block', width: 10, height: 10, background: '#2f6f73', borderRadius: 2, marginRight: 4 }} />Opened
        <span style={{ display: 'inline-block', width: 10, height: 10, background: '#c9a24a', borderRadius: 2, margin: '0 4px 0 12px' }} />Booked
      </p>
      {d.recent.length === 0 ? <Empty icon="calendar" title="No activity yet">When a family opens their booking link it shows up here.</Empty> : (
        <div className="sci-rows">
          {d.recent.slice(0, 15).map((r, i) => (
            <div className="sci-kv" key={i} style={{ cursor: r.lead_id ? 'pointer' : 'default' }} onClick={() => r.lead_id && openPerson && openPerson(r.lead_id)}>
              <span><b>{r.name}</b> · {EVENT[r.event] || r.event}{r.event === 'day_viewed' && r.detail ? ` (${r.detail})` : ''}</span>
              <span className="sci-muted">{ago(r.at)}</span>
            </div>
          ))}
        </div>
      )}
    </Panel>
  )
}


/* ASSIGN TO (staff only, Issue #507): move a booked appointment to another
   team member. The server checks that person is free (their bookings plus the
   gap, their calendar) and records who changed it. The family is not told. */
function AssignTo({ appt, onDone }) {
  const [team, setTeam] = useState(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const upcoming = appt.status === 'booked' || appt.status === 'confirmed'
  if (!upcoming) return <span className="sci-micro sci-muted">{appt.staff || '—'}</span>
  const open = () => {
    if (team) return
    api.get(`/program/command/appointments/${appt.id}/assignees`).then(d => setTeam(d.team || [])).catch(e => setMsg(errText(e)))
  }
  const change = async uid => {
    if (!uid || uid === appt.staff_id) return
    setBusy(true); setMsg('')
    try { await api.post(`/program/command/appointments/${appt.id}/assign`, { user_id: uid }); onDone && onDone() }
    catch (e) { setMsg(errText(e)) } finally { setBusy(false) }
  }
  return (
    <label className="sci-micro" style={{ display: 'block' }}>
      <span className="sci-muted">With</span>{' '}
      <select aria-label="Assign to" disabled={busy} value={appt.staff_id || ''} onFocus={open} onMouseDown={open}
        onChange={e => change(e.target.value)}>
        <option value={appt.staff_id || ''}>{appt.staff || 'Unassigned'}</option>
        {(team || []).filter(u => u.id !== appt.staff_id).map(u => <option key={u.id} value={u.id}>{u.name}</option>)}
      </select>
      {msg ? <span style={{ display: 'block', color: '#8a1c1c' }}>{msg}</span> : null}
    </label>
  )
}


/* STAFF CONTROLS (Issue #507): record attendance (a COMPLETED visit gets the
   survey 24 hours later), cancel, move to another open time, and see every
   message this appointment has - scheduled, sent, skipped and why. */
const MSG_LABEL = { confirmation: 'Confirmation', updated: 'Change notice', cancelled: 'Cancellation notice',
  reminder_24h: 'Reminder (24 hours)', reminder_2h: 'Reminder (2 hours)', survey: 'Survey invitation' }
function ApptActions({ appt, onDone }) {
  const [open, setOpen] = useState(false)
  const [d, setD] = useState(null)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [moveDay, setMoveDay] = useState('')
  const [slots, setSlots] = useState(null)
  const live = appt.status === 'booked' || appt.status === 'confirmed'
  const past = new Date(appt.when).getTime() < Date.now()
  const load = () => api.get(`/program/command/appointments/${appt.id}/messages`).then(setD).catch(e => setMsg(errText(e)))
  const act = async (path, body, ok) => {
    setBusy(true); setMsg('')
    try { await api.post(`/program/command/appointments/${appt.id}/${path}`, body || {}); setMsg(ok); onDone && onDone(); if (open) load() }
    catch (e) { setMsg(errText(e)) } finally { setBusy(false) }
  }
  const pickDay = async day => {
    setMoveDay(day); setSlots(null)
    if (!day) return
    try { setSlots(await api.get(`/program/command/day-slots?lead_id=${appt.lead_id}&date=${day}`)) } catch (e) { setSlots({ slots: [], reason: errText(e) }) }
  }
  return (
    <div className="sci-micro" style={{ marginTop: 4 }}>
      {live && past ? <>
        <button type="button" className="sci-link" disabled={busy} onClick={() => act('attendance', { status: 'completed' }, 'Marked completed. The survey is scheduled for 24 hours after the visit.')}>Completed</button>{' · '}
        <button type="button" className="sci-link" disabled={busy} onClick={() => act('attendance', { status: 'no_show' }, 'Marked no-show. No survey.')}>No-show</button>{' · '}
      </> : null}
      {live && !past ? <>
        <button type="button" className="sci-link" disabled={busy} onClick={() => { if (window.confirm('Cancel this appointment? The family gets one cancellation notice (if appointment messages are on).')) act('cancel', {}, 'Cancelled.') }}>Cancel</button>{' · '}
        <label>Move to <input type="date" value={moveDay} onChange={e => pickDay(e.target.value)} aria-label="Move to day" /></label>{' · '}
      </> : null}
      <button type="button" className="sci-link" onClick={() => { setOpen(!open); if (!open) load() }}>{open ? 'Hide messages' : 'Messages'}</button>
      {slots ? <div>{(slots.slots || []).length ? slots.slots.map(x => <button key={x.value} type="button" disabled={busy} style={{ marginRight: 6 }}
        onClick={() => act('reschedule', { slot: x.value }, `Moved to ${x.label}.`)}>{x.time}</button>) : <span className="sci-muted">{slots.reason || 'No open times that day.'}</span>}</div> : null}
      {msg ? <div>{msg}</div> : null}
      {open && d ? <div style={{ marginTop: 6 }}>
        {(d.messages || []).length ? <table className="sci-table compact"><tbody>{d.messages.map(m => <tr key={m.id}>
          <td>{MSG_LABEL[m.purpose] || m.purpose}</td><td>{m.channel === 'sms' ? 'Text' : 'Email'}</td>
          <td>{m.status}{m.delivery ? ` · ${m.delivery}` : ''}</td><td>{m.due_at ? new Date(m.due_at).toLocaleString([], { timeZone: 'America/Chicago', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }) : ''}</td>
          <td className="sci-muted">{m.reason || m.error || ''}</td></tr>)}</tbody></table> : <span className="sci-muted">No messages for this appointment.</span>}
        {d.survey ? <div style={{ marginTop: 6 }}>Survey: {d.survey.submitted_at ? `answered · ${d.survey.rating}/5 · helpful: ${d.survey.helpful}${d.survey.follow_up ? ` · wants follow-up by ${d.survey.follow_up_method} (task created)` : ''}${d.survey.comments ? ` · “${d.survey.comments}”` : ''}` : 'not answered yet'}</div> : null}
      </div> : null}
    </div>
  )
}

/* Visits that have ended with no attendance recorded: staff work, never an
   assumed visit (no survey goes until someone marks it completed). */
function PendingAttendance({ onDone, openPerson, reload }) {
  const [d, setD] = useState(null)
  useEffect(() => { api.get('/program/command/appointments-pending-attendance').then(setD).catch(() => setD(null)) }, [reload])
  if (!d || !(d.items || []).length) return null
  return (
    <Panel title="Record attendance" aside={<Chip tone="warn" plain>{num(d.items.length)} waiting</Chip>}>
      <p className="sci-micro sci-muted">These visits have ended. Mark each one completed or no-show; only completed visits get the survey.</p>
      <table className="sci-table compact"><tbody>{d.items.map(a => <tr key={a.id}>
        <td><button type="button" className="sci-namebtn" onClick={() => openPerson(a.lead_id)}>{a.name || 'Family'}</button></td>
        <td>{timeOf(a.when)} · {dayKey(a.when)}</td><td>{a.meeting_type === 'home' ? 'Home visit' : 'At Restland'}</td>
        <td><ApptActions appt={{ ...a, status: 'booked' }} onDone={onDone} /></td></tr>)}</tbody></table>
    </Panel>
  )
}

/* APPOINTMENT MESSAGES settings (managers). Off by default; saving sends nothing. */
function MessageSettings() {
  const [d, setD] = useState(null)
  const [msg, setMsg] = useState('')
  const [form, setForm] = useState(null)
  useEffect(() => { api.get('/program/command/appointment-messages/settings').then(r => { setD(r); setForm(r.settings) }).catch(() => setD(null)) }, [])
  if (!d || !form) return null
  const save = async patch => {
    setMsg('')
    try { const r = await api.post('/program/command/appointment-messages/settings', patch); setForm(r.settings); setMsg('Saved. Nothing was sent.') }
    catch (e) { setMsg(errText(e)) }
  }
  const hrs = m => (m % 60 === 0 ? `${m / 60} hour${m === 60 ? '' : 's'}` : `${m} minutes`)
  return (
    <Panel title="Appointment messages" aside={<Chip tone={form.enabled ? 'ok' : 'warn'} plain>{form.enabled ? 'On' : 'Off'}</Chip>}>
      <dl className="rl-kv">
        <dt>Confirmation</dt><dd>Right after booking, by text and email (where allowed)</dd>
        <dt>Reminders</dt><dd>{form.reminder_minutes.map(hrs).join(' and ')} before the visit · between {form.window[0]} and {form.window[1]} Central. A reminder that falls outside those hours moves earlier; if that puts it within 12 hours of another message for the same visit, it is not sent. A 10:00 AM visit gets the 24-hour reminder only.</dd>
        <dt>Survey</dt><dd>{form.survey_delay_hours} hours after a visit marked completed · {form.survey_enabled ? 'on' : 'off'}</dd>
      </dl>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginTop: 8 }}>
        <button type="button" onClick={() => save({ enabled: !form.enabled })}>{form.enabled ? 'Turn appointment messages off' : 'Turn appointment messages on'}</button>
        <button type="button" onClick={() => save({ survey_enabled: !form.survey_enabled })}>{form.survey_enabled ? 'Turn survey off' : 'Turn survey on'}</button>
      </div>
      <ol className="sci-micro sci-muted" style={{ marginTop: 8 }}>{(d.order || []).map(x => <li key={x}>{x.replace(/^\d+\. /, '')}</li>)}</ol>
      {msg ? <div className="sci-micro">{msg}</div> : null}
    </Panel>
  )
}
