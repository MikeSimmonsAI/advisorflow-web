import { useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { useJewelry } from './JewelryShell'
import { CustomerPicker, Empty, ErrorNote, Field, Loading, Modal, Pill, errText, localInput, time, useApi, useToast, when } from './jwui'

const STATUS_LABEL = { scheduled: 'Scheduled', confirmed: 'Confirmed', checked_in: 'Checked in', completed: 'Completed',
                       no_show: 'No-show', cancelled: 'Cancelled' }
const p2 = (x) => String(x).padStart(2, '0')
const ymd = (d) => `${d.getFullYear()}-${p2(d.getMonth() + 1)}-${p2(d.getDate())}`
const startOfWeek = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); x.setDate(x.getDate() - x.getDay()); return x }
const addDays = (d, k) => { const x = new Date(d); x.setDate(x.getDate() + k); return x }

function range(view, anchor) {
  if (view === 'day') { const s = new Date(anchor); s.setHours(0, 0, 0, 0); return [s, addDays(s, 1)] }
  if (view === 'week') { const s = startOfWeek(anchor); return [s, addDays(s, 7)] }
  const s = new Date(anchor.getFullYear(), anchor.getMonth(), 1)
  const gs = startOfWeek(s)
  return [gs, addDays(gs, 42)]
}

function ApptForm({ initial, types, staff, status, onSaved, onClose }) {
  const editing = !!initial?.id
  const [f, setF] = useState(() => ({
    lead_id: initial?.lead_id || null, type: initial?.type || types[0], starts_at: initial?.starts_at || localInput(new Date(Date.now() + 86400000)).slice(0, 11) + '11:00',
    duration_minutes: initial?.duration_minutes || status.profile?.appointment_minutes || 30, staff_id: initial?.staff_id || (staff.length === 1 ? staff[0].id : ''),
    internal_notes: initial?.internal_notes || '', follow_up_days: '', status: initial?.status || 'scheduled' }))
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const save = async (override = {}) => {
    setBusy(true); setErr(null)
    const f2 = { ...f, ...override }
    try {
      const body = { type: f2.type, starts_at: f2.starts_at, duration_minutes: Number(f2.duration_minutes), staff_id: f2.staff_id || null,
                     internal_notes: f2.internal_notes }
      const out = editing ? await api.patch('/jewelry-ops/appointments/' + initial.id, { ...body, status: f2.status })
                          : await api.post('/jewelry-ops/appointments', { ...body, lead_id: f2.lead_id, follow_up_days: f2.follow_up_days ? Number(f2.follow_up_days) : null })
      onSaved(out, editing)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  return (
    <Modal title={editing ? 'Appointment' : 'New appointment'} onClose={onClose} wide>
      {editing ? <p><b>{initial.customer_name}</b></p> : <Field label="Customer"><CustomerPicker value={f.lead_id} onChange={(v) => setF({ ...f, lead_id: v })} /></Field>}
      <div className="jw-form2">
        <Field label="Appointment type"><select className="jw-input" value={f.type} onChange={set('type')}>{types.map(t => <option key={t}>{t}</option>)}</select></Field>
        <Field label="Staff member"><select className="jw-input" value={f.staff_id} onChange={set('staff_id')}>
          <option value="">Choose…</option>{staff.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
        <Field label="Date and time"><input className="jw-input" type="datetime-local" value={f.starts_at} onChange={set('starts_at')} /></Field>
        <Field label="Length (minutes)"><input className="jw-input" type="number" min="10" max="480" step="5" value={f.duration_minutes} onChange={set('duration_minutes')} /></Field>
        {editing ? (
          <Field label="Status"><select className="jw-input" value={f.status} onChange={set('status')}>{Object.entries(STATUS_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></Field>
        ) : (
          <Field label="Follow-up reminder for staff" hint="Creates a follow-up task this many days after the visit">
            <select className="jw-input" value={f.follow_up_days} onChange={set('follow_up_days')}><option value="">None</option><option value="1">1 day</option><option value="3">3 days</option><option value="7">1 week</option><option value="30">1 month</option></select></Field>
        )}
      </div>
      <Field label="Internal notes"><textarea className="jw-input" rows={3} value={f.internal_notes} onChange={set('internal_notes')} placeholder="e.g. Bringing her grandmother's chain and two rings" /></Field>
      <p className="jw-note">Customer reminders are off: nothing is texted or emailed when you book, change or cancel an appointment.</p>
      <ErrorNote>{err}</ErrorNote>
      <div className="jw-row jw-row--end">
        <button className="jw-btn" onClick={onClose}>Close</button>
        {editing && f.status !== 'cancelled' && <button className="jw-btn jw-btn--danger" disabled={busy} onClick={() => save({ status: 'cancelled' })}>Cancel appointment</button>}
        <button className="jw-btn jw-btn--gold" disabled={busy || (!editing && !f.lead_id)} onClick={() => save()}>{busy ? 'Saving…' : editing ? 'Save' : 'Book appointment'}</button>
      </div>
    </Modal>
  )
}

export default function Appointments() {
  const { status } = useJewelry()
  const [params, setParams] = useSearchParams()
  const [view, setView] = useState(window.innerWidth < 760 ? 'day' : 'week')
  const [anchor, setAnchor] = useState(() => new Date())
  const [staffFilter, setStaffFilter] = useState('')
  const [editing, setEditing] = useState(null)
  const [toast, toastNode] = useToast()
  const [s, e] = range(view, anchor)
  const path = `/jewelry-ops/appointments?start=${ymd(s)}T00:00&end=${ymd(e)}T00:00` + (staffFilter ? `&staff_id=${staffFilter}` : '')
  const { data, error, loading, reload } = useApi(path, [path])
  const creating = params.get('new') === '1' || !!params.get('lead')
  const types = data?.types || status.profile?.appointment_types || []
  const staff = data?.staff || []
  const byDay = useMemo(() => {
    const m = {}
    for (const a of data?.appointments || []) (m[a.starts_at.slice(0, 10)] ||= []).push(a)
    return m
  }, [data])
  const move = (k) => setAnchor(view === 'month' ? new Date(anchor.getFullYear(), anchor.getMonth() + k, 1) : addDays(anchor, k * (view === 'week' ? 7 : 1)))
  const title = view === 'month' ? anchor.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })
    : view === 'week' ? `${s.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })} – ${addDays(e, -1).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}`
    : anchor.toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })
  const today = ymd(new Date())
  const Ev = ({ a }) => (
    <button className={`jw-ev jw-ev--${a.status}`} onClick={() => setEditing(a)} title={`${a.type} · ${a.customer_name}`}>
      <b>{time(a.starts_at)}</b> {a.customer_name}<small>{a.type}</small>
    </button>
  )
  const days = view === 'day' ? [s] : view === 'week' ? [...Array(7)].map((_, i) => addDays(s, i)) : [...Array(42)].map((_, i) => addDays(s, i))
  const all = data?.appointments || []
  return (
    <div>
      <div className="jw-head">
        <div><div className="jw-eyebrow">Appointment Center</div><h1>Appointments</h1><p>{status.can_manage ? 'Every staff member’s calendar' : 'Your appointments'} · reminders off until messaging is connected</p></div>
        <button className="jw-btn jw-btn--gold" onClick={() => setParams({ new: '1' })}>+ New appointment</button>
      </div>
      <div className="jw-card">
        <div className="jw-row jw-row--between jw-row--wrap">
          <div className="jw-row">
            <button className="jw-btn jw-btn--sm" onClick={() => move(-1)} aria-label="Previous">‹</button>
            <button className="jw-btn jw-btn--sm" onClick={() => setAnchor(new Date())}>Today</button>
            <button className="jw-btn jw-btn--sm" onClick={() => move(1)} aria-label="Next">›</button>
            <b className="jw-cal-title">{title}</b>
          </div>
          <div className="jw-row">
            {status.can_manage && staff.length > 1 && (
              <select className="jw-input jw-input--sm" value={staffFilter} onChange={ev => setStaffFilter(ev.target.value)} aria-label="Staff">
                <option value="">All staff</option>{staff.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select>
            )}
            <div className="jw-seg">{['day', 'week', 'month'].map(v => <button key={v} className={view === v ? 'on' : ''} onClick={() => setView(v)}>{v[0].toUpperCase() + v.slice(1)}</button>)}</div>
          </div>
        </div>
        <ErrorNote onRetry={reload}>{error}</ErrorNote>
        {loading && !data && <Loading />}
        {data && (
          <div className={`jw-cal jw-cal--${view}`}>
            {view !== 'day' && ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].map(d => <div key={d} className="jw-cal__dow">{d}</div>)}
            {days.map(d => {
              const k = ymd(d), list = byDay[k] || []
              const outside = view === 'month' && d.getMonth() !== anchor.getMonth()
              return (
                <div key={k} className={`jw-cal__day ${k === today ? 'today' : ''} ${outside ? 'outside' : ''}`}>
                  <div className="jw-cal__num">{view === 'day' ? '' : d.getDate()}</div>
                  {list.slice(0, view === 'month' ? 3 : 50).map(a => <Ev key={a.id} a={a} />)}
                  {view === 'month' && list.length > 3 && <button className="jw-more" onClick={() => { setView('day'); setAnchor(d) }}>+{list.length - 3} more</button>}
                  {view === 'day' && list.length === 0 && <Empty title="Nothing booked this day" />}
                </div>
              )
            })}
          </div>
        )}
      </div>
      {data && (
        <div className="jw-card">
          <div className="jw-card__head"><h3>List</h3><small>{all.length} in view</small></div>
          {all.length === 0 ? <Empty title="No appointments in this view" /> : (
            <div className="jw-table-wrap"><table className="jw-table"><thead><tr><th>When</th><th>Customer</th><th>Type</th><th>Staff</th><th>Status</th></tr></thead>
              <tbody>{all.map(a => (
                <tr key={a.id} className="click" onClick={() => setEditing(a)}><td>{when(a.starts_at)}</td><td>{a.customer_name}</td><td>{a.type}</td><td>{a.staff_name}</td>
                  <td><Pill tone={a.status === 'cancelled' || a.status === 'no_show' ? 'err' : a.status === 'completed' ? 'ok' : ''}>{STATUS_LABEL[a.status]}</Pill></td></tr>
              ))}</tbody></table></div>
          )}
        </div>
      )}
      {(creating || editing) && (
        <ApptForm initial={editing || (params.get('lead') ? { lead_id: params.get('lead') } : null)} types={types} staff={staff} status={status}
                  onClose={() => { setEditing(null); setParams({}) }}
                  onSaved={(a, wasEdit) => { setEditing(null); setParams({}); setAnchor(new Date(a.starts_at)); reload(); toast(wasEdit ? 'Appointment updated.' : `Booked ${a.type.toLowerCase()} for ${when(a.starts_at)}. No confirmation was sent.`) }} />
      )}
      {toastNode}
    </div>
  )
}
