/**
 * CLIENT TOOLS (owner request 2026-11-18): the working parts the client
 * portal pages were missing.
 *   - AvailabilityPanel: the client says which hours EvoSys may book them.
 *   - QuickActions: one-tap starts for the everyday jobs of running the work.
 *   - NewScheduleForm: set up recurring service without hunting for a job.
 * The server decides what each person may change; failures show its words.
 */
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { BASE } from './AcquisitionShell'
import { Icon, Loading, errText, useLoad } from './ui'

const DAYS = [['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'], ['fri', 'Friday'], ['sat', 'Saturday'], ['sun', 'Sunday']]
const fld = (id, label, input) => <div className="aq-field"><label htmlFor={id}>{label}</label>{input}</div>
export const qsNew = () => { try { return new URLSearchParams(window.location.search).get('new') } catch { return null } }

function fmt12(t) {
  if (!t) return ''
  const [h, m] = t.split(':').map(Number)
  return `${((h + 11) % 12) + 1}:${String(m).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`
}

export function AvailabilityPanel({ say, compact }) {
  const [c, err, reload] = useLoad('/acquisition/calendar')
  const [f, setF] = useState(null)
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  useEffect(() => {
    if (!c) return
    const days = {}
    DAYS.forEach(([k]) => { const w = (c.booking_windows || {})[k] || []; days[k] = w.length ? { on: true, from: w[0][0], to: w[0][1], extra: w.slice(1) } : { on: false, from: '09:00', to: '17:00', extra: [] } })
    setF({ days, appointment_minutes: c.appointment_minutes || 45, min_notice_hours: c.min_notice_hours ?? 24,
           buffer_minutes: c.buffer_minutes ?? 15, meeting_location: c.meeting_location || '', editing: false })
  }, [c])
  if (err) return null
  if (!c || !f) return <section className="aq-card"><Loading /></section>
  const set = (k, v) => setF({ ...f, [k]: v })
  const setDay = (k, patch) => setF({ ...f, days: { ...f.days, [k]: { ...f.days[k], ...patch } } })
  const save = async () => {
    setBusy(true); setFail(null)
    try {
      const booking_windows = Object.fromEntries(DAYS.map(([k]) => [k, f.days[k].on ? [[f.days[k].from, f.days[k].to], ...f.days[k].extra] : []]))
      await api.put('/acquisition/availability', { booking_windows, appointment_minutes: Number(f.appointment_minutes),
        min_notice_hours: Number(f.min_notice_hours), buffer_minutes: Number(f.buffer_minutes), meeting_location: f.meeting_location })
      reload(); say?.('Availability saved — EvoSys only books inside these hours')
    } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  const open = DAYS.filter(([k]) => f.days[k].on)
  return (
    <section className="aq-card cp-avail">
      <div className="aq-row" style={{ alignItems: 'flex-start' }}>
        <div className="aq-row__main"><h2 style={{ margin: 0 }}><Icon name="calendar" /> When EvoSys can book you</h2>
          <span className="hb-muted">Set your open hours. Appointments are only booked inside these times, and never on top of something already on your calendar.</span></div>
        {!f.editing && <button className="aq-btn aq-btn--primary aq-btn--sm" onClick={() => set('editing', true)}>Edit availability</button>}
      </div>
      {!f.editing ? <div className="cp-avail__sum">
        {open.length ? open.map(([k, l]) => <span key={k} className="cp-chip"><b>{l.slice(0, 3)}</b> {[[f.days[k].from, f.days[k].to], ...f.days[k].extra].map(w => `${fmt12(w[0])}–${fmt12(w[1])}`).join(', ')}</span>)
          : <span className="hb-muted">No hours set yet — EvoSys can’t book anything until you add some.</span>}
        {!compact && <span className="cp-chip">{f.appointment_minutes}-min visits</span>}
        {!compact && <span className="cp-chip">{f.min_notice_hours} h notice</span>}
        {!compact && <span className="cp-chip">{f.buffer_minutes} min between</span>}
        <span className="cp-chip">Calendar: {c.ready ? 'connected' : 'not connected'}</span>
      </div> : <>
        <div className="cp-avail__days">{DAYS.map(([k, l]) => (
          <div className="cp-avail__day" key={k}>
            <label className="hb-check"><input type="checkbox" checked={f.days[k].on} onChange={e => setDay(k, { on: e.target.checked })} /> {l}</label>
            {f.days[k].on ? <span className="cp-avail__times">
              <input type="time" aria-label={`${l} start`} value={f.days[k].from} onChange={e => setDay(k, { from: e.target.value })} />
              <span>to</span>
              <input type="time" aria-label={`${l} end`} value={f.days[k].to} onChange={e => setDay(k, { to: e.target.value })} />
              {f.days[k].extra.map((w, i) => <span key={i} className="hb-muted">+ {fmt12(w[0])}–{fmt12(w[1])} <button className="aq-btn aq-btn--sm" onClick={() => setDay(k, { extra: f.days[k].extra.filter((_, j) => j !== i) })}>Remove</button></span>)}
              {f.days[k].extra.length === 0 && <button className="aq-btn aq-btn--sm" onClick={() => setDay(k, { extra: [['13:00', '17:00']] })}>Add second block</button>}
            </span> : <span className="hb-muted">Closed</span>}
          </div>))}</div>
        <div className="aq-grid aq-two">
          {fld('av-m', 'Visit length', <select id="av-m" value={f.appointment_minutes} onChange={e => set('appointment_minutes', e.target.value)}>{[15, 30, 45, 60, 90, 120].map(m => <option key={m} value={m}>{m} minutes</option>)}</select>)}
          {fld('av-n', 'Notice needed', <select id="av-n" value={f.min_notice_hours} onChange={e => set('min_notice_hours', e.target.value)}>{[2, 4, 12, 24, 48, 72].map(m => <option key={m} value={m}>{m} hours</option>)}</select>)}
          {fld('av-b', 'Gap between visits', <select id="av-b" value={f.buffer_minutes} onChange={e => set('buffer_minutes', e.target.value)}>{[0, 10, 15, 30, 45, 60].map(m => <option key={m} value={m}>{m} minutes</option>)}</select>)}
          {fld('av-l', 'Where you meet', <input id="av-l" placeholder="At the customer’s building" value={f.meeting_location} onChange={e => set('meeting_location', e.target.value)} />)}
        </div>
        {fail && <div className="aq-err" role="alert">{fail}</div>}
        <div className="aq-actions"><button className="aq-btn" onClick={() => { reload(); set('editing', false) }}>Cancel</button>
          <button className="aq-btn aq-btn--primary" disabled={busy || !open.length} onClick={save}>{busy ? 'Saving…' : 'Save availability'}</button></div>
      </>}
    </section>
  )
}

const OPS = `${BASE}/ops`
export function QuickActions({ me }) {
  const can = p => !!me?.perms?.includes(p)
  const items = [
    can('schedule') && ['plus', 'New job', 'Book a visit for a customer', `${OPS}/jobs?new=1`],
    can('schedule') && ['calendar', 'Recurring service', 'Weekly, monthly, or your own pattern', `${OPS}/schedule?new=1`],
    can('billing') && ['doc', 'New estimate', 'Price out work for a customer', `${OPS}/invoices?new=estimate`],
    can('billing') && ['dollar', 'New invoice', 'Bill for work or extras', `${OPS}/invoices?new=invoice`],
    can('customers') && ['building', 'Add customer', 'With their first location', `${OPS}/customers?new=1`],
    can('team') && ['users', 'Add team member', 'Crew lead or field worker', `${OPS}/team?new=1`],
    ['handshake', 'Won contracts', 'Turn a win into a job', `${BASE}/estimates`],
  ].filter(Boolean)
  return (
    <section className="aq-card"><h2>Quick actions</h2>
      <div className="cp-quick">{items.map(([ic, t, s, to]) => (
        <Link key={t} to={to} className="cp-quick__btn"><span className="cp-quick__ic"><Icon name={ic} /></span><span><b>{t}</b><small>{s}</small></span></Link>))}</div>
    </section>
  )
}

const FREQ = [['weekly', 'Every week'], ['biweekly', 'Every two weeks'], ['monthly', 'Every month'], ['daily', 'Every day'], ['custom', 'Every … days']]
export function NewScheduleForm({ locs, team, onDone, onCancel, say }) {
  const today = new Date().toISOString().slice(0, 10)
  const [f, setF] = useState({ location_id: locs[0]?.id || '', frequency: 'weekly', interval_days: 10, start_date: today, end_date: '',
                               start_time: '18:00', duration_min: 120, price_per_visit: '', expected_monthly_value: '', service_type: '', assigned_member_ids: [] })
  const [busy, setBusy] = useState(false)
  const set = k => e => setF({ ...f, [k]: e.target.value })
  if (!locs.length) return <section className="aq-card"><h2>New recurring service</h2><p className="hb-muted">Add a customer and location first.</p>
    <div className="aq-actions"><button className="aq-btn" onClick={onCancel}>Close</button><Link className="aq-btn aq-btn--primary" to={`${OPS}/customers?new=1`}>Add customer</Link></div></section>
  const save = async () => {
    setBusy(true)
    try {
      const body = { ...f, end_date: f.end_date || undefined, interval_days: f.frequency === 'custom' ? Number(f.interval_days) : undefined,
                     price_per_visit: f.price_per_visit || undefined, expected_monthly_value: f.expected_monthly_value || undefined }
      await api.post('/service-ops/schedules', body); onDone()
    } catch (e) { say(errText(e)) } finally { setBusy(false) }
  }
  return (
    <section className="aq-card"><h2>New recurring service</h2>
      <div className="aq-grid aq-two">
        {fld('rs-l', 'Customer location', <select id="rs-l" value={f.location_id} onChange={set('location_id')}>{locs.map(l => <option key={l.id} value={l.id}>{l.cname} — {l.name}</option>)}</select>)}
        {fld('rs-t', 'Service', <input id="rs-t" placeholder="e.g. Nightly office cleaning" value={f.service_type} onChange={set('service_type')} />)}
        {fld('rs-f', 'How often', <select id="rs-f" value={f.frequency} onChange={set('frequency')}>{FREQ.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>)}
        {f.frequency === 'custom' && fld('rs-i', 'Every how many days', <input id="rs-i" inputMode="numeric" value={f.interval_days} onChange={set('interval_days')} />)}
        {fld('rs-s', 'First visit', <input id="rs-s" type="date" value={f.start_date} onChange={set('start_date')} />)}
        {fld('rs-e', 'Ends (optional)', <input id="rs-e" type="date" value={f.end_date} onChange={set('end_date')} />)}
        {fld('rs-h', 'Start time', <input id="rs-h" type="time" value={f.start_time} onChange={set('start_time')} />)}
        {fld('rs-d', 'Visit length (min)', <input id="rs-d" inputMode="numeric" value={f.duration_min} onChange={set('duration_min')} />)}
        {fld('rs-p', 'Price per visit ($)', <input id="rs-p" inputMode="decimal" value={f.price_per_visit} onChange={set('price_per_visit')} />)}
        {fld('rs-v', 'Monthly contract value ($)', <input id="rs-v" inputMode="decimal" value={f.expected_monthly_value} onChange={set('expected_monthly_value')} />)}
        {team?.length > 0 && fld('rs-m', 'Crew', <select id="rs-m" multiple value={f.assigned_member_ids} onChange={e => setF({ ...f, assigned_member_ids: [...e.target.selectedOptions].map(o => o.value) })}>{team.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select>)}
      </div>
      <p className="hb-muted">Visits are added to Jobs up to five weeks ahead and keep their local time.</p>
      <div className="aq-actions"><button className="aq-btn" onClick={onCancel}>Cancel</button>
        <button className="aq-btn aq-btn--primary" disabled={busy || !f.location_id || !f.start_date} onClick={save}>{busy ? 'Saving…' : 'Create recurring service'}</button></div>
    </section>
  )
}
