// EvoSys Sales "Calls": Business Growth Calls (design pages 11-18).
// Everything books, moves and cancels through the existing scheduler, so the
// Zoom room, calendar event, invite and double-booking checks are the real ones.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'
import { ctDay, ctTime, errText, initials, Toast } from './Inbox'

const API = '/sales/evosys'
const BASE = '/program/evosys-sales'
const TZS = [['America/Chicago', 'Central Time'], ['America/New_York', 'Eastern Time'], ['America/Denver', 'Mountain Time'],
             ['America/Phoenix', 'Arizona Time'], ['America/Los_Angeles', 'Pacific Time']]
const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
const hm = m => { const h = Math.floor(m / 60), mi = m % 60; return `${((h + 11) % 12) + 1}:${String(mi).padStart(2, '0')} ${h < 12 || h === 24 ? 'AM' : 'PM'}` }
const ctDate = d => new Date(d).toLocaleDateString('en-CA', { timeZone: 'America/Chicago' })
const todayCT = () => ctDate(new Date())
const addDays = (s, n) => { const d = new Date(s + 'T12:00:00Z'); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10) }
const fmtLong = s => new Date(s + 'T12:00:00Z').toLocaleDateString('en-US', { timeZone: 'UTC', month: 'short', day: 'numeric', year: 'numeric' })

// local wall time in a zone -> UTC ISO (handles daylight saving by asking Intl)
export function zonedToUtc(dateStr, timeStr, tz) {
  const [y, mo, d] = dateStr.split('-').map(Number), [h, mi] = timeStr.split(':').map(Number)
  const guess = Date.UTC(y, mo - 1, d, h, mi)
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: tz, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).formatToParts(new Date(guess))
  const g = Object.fromEntries(parts.map(p => [p.type, p.value]))
  const asLocal = Date.UTC(+g.year, +g.month - 1, +g.day, +g.hour, +g.minute)
  return new Date(guess - (asLocal - guess)).toISOString()
}

function Head({ title, sub, children }) {
  return (
    <header className="v2-pagehead"><h1>{title}</h1>{sub && <p>{sub}</p>}{children && <div className="v2-row v2-headacts">{children}</div>}</header>
  )
}
function CallsNav({ here }) {
  const nav = useNavigate()
  return (
    <div className="v2-row v2-headacts">
      <button className="v2-btn v2-primary" onClick={() => nav(`${BASE}/appointments/new`)}>+ New appointment</button>
      {here !== 'list' && <button className="v2-btn" onClick={() => nav(`${BASE}/appointments`)}>Appointments</button>}
      {here !== 'calendar' && <button className="v2-btn" onClick={() => nav(`${BASE}/appointments/calendar`)}>Calendar</button>}
      {here !== 'settings' && <button className="v2-btn" onClick={() => nav(`${BASE}/appointments/settings`)}>Settings</button>}
    </div>
  )
}
const joinUrl = c => c?.video?.join_url || c?.meeting_url

// ── list ─────────────────────────────────────────────────────────────────────
export function CallsList() {
  const [sp, setSp] = useSearchParams()
  const view = sp.get('view') || 'upcoming'
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { setD(null); api.get(`${API}/calls-list?view=${view}`).then(setD).catch(e => setErr(errText(e))) }, [view])
  return (
    <div className="v2-page">
      <Head title="Appointments" sub="Book, send, and manage Business Growth Calls." />
      <CallsNav here="list" />
      <div className="v2-filters" role="tablist">
        {[['today', 'Today'], ['upcoming', 'Upcoming'], ['past', 'Past']].map(([k, l]) => (
          <button key={k} role="tab" aria-selected={view === k} className={'v2-filter' + (view === k ? ' on' : '')} onClick={() => setSp({ view: k })}>{l}</button>
        ))}
      </div>
      {err && <p className="v2-err">{err}</p>}
      {!d && !err && [0, 1].map(i => <div key={i} className="v2-skel card" />)}
      {d && !d.calls.length && <div className="v2-card v2-empty"><b>{view === 'past' ? 'No past calls yet' : view === 'today' ? 'No calls today' : 'No calls coming up'}</b><span>Book one with “New appointment”.</span></div>}
      <div className="v2-cards">
        {(d?.calls || []).map(c => (
          <article key={c.id} className={'v2-card v2-callcard' + (c.status === 'cancelled' ? ' muted' : '')}>
            <div className="v2-eyebrow">{ctDay(c.starts_at + 'Z')} · {ctTime(c.starts_at + 'Z')}–{ctTime(c.ends_at + 'Z')} CT{c.status === 'cancelled' ? ' · Cancelled' : ''}</div>
            <h3 className="v2-serif">{c.opportunity_company || c.prospect?.company || c.prospect?.name || c.title}</h3>
            <div className="v2-sub">{c.title}{c.video?.label ? ' · Zoom ' + c.video.label.toLowerCase() : ''}</div>
            <div className="v2-row">
              {view !== 'past' && joinUrl(c) && <a className="v2-btn v2-primary" href={joinUrl(c)} target="_blank" rel="noopener noreferrer">Join Zoom</a>}
              {view === 'past' && c.status !== 'cancelled' && <Link className="v2-btn v2-primary" to={`${BASE}/appointments/${c.id}?tab=outcome`}>{c.outcome_state?.outcome ? 'Outcome: ' + c.outcome_state.outcome : 'Record outcome'}</Link>}
              <Link className="v2-btn" to={`${BASE}/appointments/${c.id}`}>Open appointment</Link>
              {view === 'past' && <Link className="v2-btn" to={`${BASE}/appointments/${c.id}?tab=replays`}>View replays</Link>}
            </div>
          </article>
        ))}
      </div>
    </div>
  )
}

// ── calendar ─────────────────────────────────────────────────────────────────
export function CallsCalendar() {
  const nav = useNavigate()
  const [day, setDay] = useState(todayCT())
  const [mode, setMode] = useState('day')
  const [days, setDays] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    setDays(null); setErr(null)
    const list = mode === 'day' ? [day] : [0, 1, 2, 3, 4, 5, 6].map(i => addDays(day, i))
    Promise.all(list.map(x => api.get(`${API}/calls/day?day=${x}`))).then(setDays).catch(e => setErr(errText(e)))
  }, [day, mode])
  return (
    <div className="v2-page">
      <Head title="Calendar" sub="See booked calls, open times, and conflicts in Central Time." />
      <CallsNav here="calendar" />
      <div className="v2-row">
        <button className="v2-btn" aria-label="Earlier" onClick={() => setDay(addDays(day, mode === 'day' ? -1 : -7))}>‹</button>
        <span className="v2-btn v2-static">{fmtLong(day)}</span>
        <button className="v2-btn" aria-label="Later" onClick={() => setDay(addDays(day, mode === 'day' ? 1 : 7))}>›</button>
        <button className={'v2-btn' + (mode === 'day' ? ' on' : '')} onClick={() => setMode('day')}>Day</button>
        <button className={'v2-btn' + (mode === 'week' ? ' on' : '')} onClick={() => setMode('week')}>Week</button>
      </div>
      <div className="v2-hint">America/Chicago · Central Time</div>
      {err && <p className="v2-err">{err}</p>}
      {!days && !err && [0, 1, 2].map(i => <div key={i} className="v2-skel row" />)}
      {days && days.map(dd => {
        const open = dd.items.filter(i => i.kind === 'open')
        const shown = mode === 'day' ? dd.items : dd.items.filter(i => i.kind !== 'open')
        return (
          <section key={dd.date} className={mode === 'week' ? 'v2-weekday' : ''}>
            {mode === 'week' && <h3 className="v2-serif v2-dayhead">{new Date(dd.date + 'T12:00:00Z').toLocaleDateString('en-US', { timeZone: 'UTC', weekday: 'long', month: 'short', day: 'numeric' })} <small>{open.length} open {open.length === 1 ? 'time' : 'times'}</small></h3>}
            {!shown.length && <div className="v2-card v2-slot muted">{mode === 'day' ? 'No open times or calls this day.' : 'No calls booked.'}</div>}
            {shown.map((i, k) => (
              <div key={k} className={'v2-card v2-slot v2-slot-' + i.kind}>
                <div><div className="v2-eyebrow">{ctTime(i.starts_at)}</div><b>{i.kind === 'open' ? 'Available' : i.kind === 'busy' ? 'Busy · Calendar conflict' : i.title}</b></div>
                {i.kind === 'open' && <button className="v2-btn v2-primary" onClick={() => nav(`${BASE}/appointments/new?start=${encodeURIComponent(i.starts_at)}`)}>Select time</button>}
                {i.kind === 'call' && <Link className="v2-btn" to={`${BASE}/appointments/${i.id}`}>Open appointment</Link>}
              </div>
            ))}
            {mode === 'week' && open.length > 0 && <button className="v2-btn v2-ghostlink" onClick={() => { setDay(dd.date); setMode('day') }}>See open times</button>}
          </section>
        )
      })}
    </div>
  )
}

// ── new appointment + review ─────────────────────────────────────────────────
function LeadPicker({ value, onPick }) {
  const [q, setQ] = useState('')
  const [rows, setRows] = useState([])
  useEffect(() => {
    if (!q.trim()) { setRows([]); return }
    const t = setTimeout(() => api.get(`${API}/leads?q=${encodeURIComponent(q)}`).then(r => setRows((r.leads || r.rows || r || []).slice(0, 8))).catch(() => setRows([])), 250)
    return () => clearTimeout(t)
  }, [q])
  if (value) return <div className="v2-input v2-picked"><span>{value.company}</span><button className="v2-linkbtn" onClick={() => onPick(null)}>Change</button></div>
  return (
    <div className="v2-picker">
      <input className="v2-input" placeholder="Search a lead, or leave empty to add a guest" value={q} onChange={e => setQ(e.target.value)} aria-label="Lead" />
      {rows.length > 0 && <div className="v2-picklist">{rows.map(r => <button key={r.id} onClick={() => { onPick(r); setQ('') }}><span className="v2-avatar sm">{initials(r.company)}</span>{r.company}<small>{r.contact || ''}</small></button>)}</div>}
    </div>
  )
}

export function NewCall() {
  const nav = useNavigate()
  const [sp] = useSearchParams()
  const [meta, setMeta] = useState(null)
  const [lead, setLead] = useState(null)
  const [f, setF] = useState(() => {
    const st = sp.get('start')
    const base = st ? new Date(st) : new Date(Date.now() + 86400000)
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Chicago', hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).formatToParts(base)
    const g = Object.fromEntries(parts.map(p => [p.type, p.value]))
    return { guest_name: '', guest_email: '', guest_phone: '', title: 'Business Growth Call', date: `${g.year}-${g.month}-${g.day}`,
             time: st ? `${g.hour}:${g.minute}` : '15:00', duration: 30, tz: 'America/Chicago', host_id: '', notes: '' }
  })
  const [chk, setChk] = useState(null)
  const [review, setReview] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const set = (k, v) => setF(x => ({ ...x, [k]: v }))
  useEffect(() => { api.get(`${API}/calls/meta`).then(m => { setMeta(m); setF(x => ({ ...x, host_id: x.host_id || m.default_host_id })) }).catch(e => setErr(errText(e))) }, [])
  useEffect(() => {
    const id = sp.get('lead')
    if (id) api.get(`${API}/leads/${id}`).then(d => { setLead(d.lead); setF(x => ({ ...x, guest_email: d.lead.email || '', guest_phone: d.lead.phone || '' })) }).catch(() => {})
  }, [sp])
  const startsAt = useMemo(() => { try { return zonedToUtc(f.date, f.time, f.tz) } catch { return null } }, [f.date, f.time, f.tz])
  useEffect(() => {
    if (!startsAt || !f.host_id) return
    setChk(null)
    const t = setTimeout(() => api.post(`${API}/calls/check`, { starts_at: startsAt, duration_minutes: Number(f.duration), host_id: f.host_id }).then(setChk).catch(() => setChk(null)), 300)
    return () => clearTimeout(t)
  }, [startsAt, f.duration, f.host_id])
  const hostName = meta?.hosts?.find(h => h.id === f.host_id)?.name || 'Host'
  const who = lead ? lead.company : (f.guest_name || f.guest_email || 'Guest')
  const canReview = startsAt && (lead || f.guest_email || f.guest_phone) && chk?.available
  const book = async send => {
    setBusy(true); setErr(null)
    try {
      const r = await api.post(`${API}/calls`, { lead_id: lead?.id, guest_name: f.guest_name || undefined, guest_email: f.guest_email || undefined,
        guest_phone: f.guest_phone || undefined, title: f.title, starts_at: startsAt, duration_minutes: Number(f.duration),
        timezone: f.tz, host_id: f.host_id, notes: f.notes || undefined, send })
      nav(`${BASE}/appointments/${r.id}?booked=${send ? 'sent' : 'quiet'}`)
    } catch (e) { setErr(errText(e)); setReview(false) } finally { setBusy(false) }
  }
  const when = startsAt ? `${new Date(startsAt).toLocaleDateString('en-US', { timeZone: f.tz, weekday: 'short', month: 'short', day: 'numeric' })} · ${new Date(startsAt).toLocaleTimeString('en-US', { timeZone: f.tz, hour: 'numeric', minute: '2-digit' })}–${new Date(new Date(startsAt).getTime() + f.duration * 60000).toLocaleTimeString('en-US', { timeZone: f.tz, hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })}` : ''
  if (review) return (
    <div className="v2-page">
      <Head title="Review and send invite" sub="Confirm the recipient, calendar event, and Zoom details before sending." />
      <article className="v2-card v2-form">
        <h2 className="v2-serif">{f.title}</h2>
        <div className="v2-eyebrow">{when}</div>
        <dl className="v2-dl">
          <dt>To</dt><dd>{who}{f.guest_email ? ' · ' + f.guest_email : ''}{(lead?.phone || f.guest_phone) ? ' · text to ' + (lead?.phone || f.guest_phone) : ''}</dd>
          <dt>Calendar</dt><dd>{hostName} · creates the calendar event and a Zoom participant link</dd>
          <dt>Invite and reminders</dt><dd>{f.guest_email ? 'Calendar invite by email. ' : 'No email on file, so no calendar invite. '}{(lead?.phone || f.guest_phone) && lead ? 'Confirmation text from the EvoSys Sales number with the Zoom link, then reminders the day before and 1 hour before.' : ''}</dd>
          <dt>Message preview</dt><dd>Looking forward to talking through your growth goals. Your meeting details are in the invite.</dd>
          {f.notes && <><dt>Note for the guest</dt><dd>{f.notes}</dd></>}
        </dl>
      </article>
      {err && <p className="v2-err">{err}</p>}
      <div className="v2-row">
        <button className="v2-btn v2-primary" disabled={busy} onClick={() => book(true)}>{busy ? 'Booking…' : 'Book and send invite'}</button>
        <button className="v2-btn" disabled={busy} onClick={() => book(false)}>Book without sending</button>
        <button className="v2-btn" disabled={busy} onClick={() => setReview(false)}>Back</button>
      </div>
    </div>
  )
  return (
    <div className="v2-page">
      <Head title="New appointment" sub="Create a Business Growth Call for a specific date and time." />
      <article className="v2-card v2-form">
        <label className="v2-field"><span>Lead or guest</span><LeadPicker value={lead} onPick={l => { setLead(l); if (l) setF(x => ({ ...x, guest_email: l.email || '', guest_phone: l.phone || '' })) }} /></label>
        {!lead && <label className="v2-field"><span>Guest name</span><input className="v2-input" value={f.guest_name} onChange={e => set('guest_name', e.target.value)} /></label>}
        <div className="v2-grid2">
          <label className="v2-field"><span>Guest email</span><input className="v2-input" type="email" placeholder="Choose saved email or enter an address" value={f.guest_email} onChange={e => set('guest_email', e.target.value)} /></label>
          <label className="v2-field"><span>Guest phone</span><input className="v2-input" type="tel" value={f.guest_phone} onChange={e => set('guest_phone', e.target.value)} /></label>
        </div>
        <label className="v2-field"><span>Title</span><input className="v2-input" value={f.title} onChange={e => set('title', e.target.value)} /></label>
        <div className="v2-grid2">
          <label className="v2-field"><span>Date</span><input className="v2-input" type="date" value={f.date} onChange={e => set('date', e.target.value)} /></label>
          <label className="v2-field"><span>Start time</span><input className="v2-input" type="time" step="900" value={f.time} onChange={e => set('time', e.target.value)} /></label>
          <label className="v2-field"><span>Duration</span><select className="v2-input" value={f.duration} onChange={e => set('duration', Number(e.target.value))}>{(meta?.durations || [15, 30, 45, 60]).map(m => <option key={m} value={m}>{m} minutes</option>)}</select></label>
          <label className="v2-field"><span>Time zone</span><select className="v2-input" value={f.tz} onChange={e => set('tz', e.target.value)}>{TZS.map(([z, l]) => <option key={z} value={z}>{l}</option>)}</select></label>
          <label className="v2-field"><span>Host</span><select className="v2-input" value={f.host_id} onChange={e => set('host_id', e.target.value)}>{(meta?.hosts || []).map(h => <option key={h.id} value={h.id}>{h.name}</option>)}</select></label>
          <label className="v2-field"><span>Location</span><input className="v2-input" value="Zoom · link created on booking" readOnly /></label>
        </div>
        <label className="v2-field"><span>Notes for guest</span><input className="v2-input" placeholder="Optional note" value={f.notes} onChange={e => set('notes', e.target.value)} /></label>
        <div className={'v2-availability' + (chk ? (chk.available ? ' ok' : ' bad') : '')} aria-live="polite">
          {!chk ? 'Checking the host calendar…'
            : chk.available ? `${hostName} is free then${chk.outside_hours ? ' (outside the usual hours)' : ''}.${chk.calendar_readable ? '' : ' The connected calendar could not be read just now.'}`
              : (chk.conflicts || []).join(' · ')}
        </div>
        {err && <p className="v2-err">{err}</p>}
        <div className="v2-row">
          <button className="v2-btn v2-primary" disabled={!canReview} onClick={() => setReview(true)}>Review invite</button>
          <button className="v2-btn" onClick={() => nav(-1)}>Cancel</button>
        </div>
      </article>
    </div>
  )
}

// ── detail ───────────────────────────────────────────────────────────────────
function Reschedule({ call, onDone }) {
  const startLocal = new Date(call.starts_at + 'Z')
  const g = Object.fromEntries(new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Chicago', hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).formatToParts(startLocal).map(p => [p.type, p.value]))
  const [date, setDate] = useState(`${g.year}-${g.month}-${g.day}`)
  const [time, setTime] = useState(`${g.hour}:${g.minute}`)
  const [chk, setChk] = useState(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState(null)
  const at = useMemo(() => { try { return zonedToUtc(date, time, 'America/Chicago') } catch { return null } }, [date, time])
  const host = (call.participants || [])[0]?.user_id
  useEffect(() => {
    if (!at) return
    setChk(null)
    const t = setTimeout(() => api.post(`${API}/calls/check`, { starts_at: at, duration_minutes: call.duration_minutes, host_id: host, exclude_appointment_id: call.id }).then(setChk).catch(() => setChk(null)), 300)
    return () => clearTimeout(t)
  }, [at, call.id, call.duration_minutes, host])
  const save = async () => {
    setBusy(true); setErr(null)
    try { await api.post(`/sales/appointments/${call.id}/reschedule`, { starts_at: at, reason: 'Moved from EvoSys Sales', notify: true }); onDone('Call moved. The invite and Zoom link were updated.') }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="v2-card v2-form">
      <b>Move this call</b>
      <div className="v2-grid2">
        <label className="v2-field"><span>Date</span><input className="v2-input" type="date" value={date} onChange={e => setDate(e.target.value)} /></label>
        <label className="v2-field"><span>Start time (CT)</span><input className="v2-input" type="time" step="900" value={time} onChange={e => setTime(e.target.value)} /></label>
      </div>
      <div className={'v2-availability' + (chk ? (chk.available ? ' ok' : ' bad') : '')}>{!chk ? 'Checking…' : chk.available ? 'Free then. The original time stays booked until you save.' : chk.conflicts.join(' · ')}</div>
      {err && <p className="v2-err">{err}</p>}
      <div className="v2-row"><button className="v2-btn v2-primary" disabled={busy || !chk?.available} onClick={save}>Move call</button><button className="v2-btn" onClick={() => onDone(null)}>Keep current time</button></div>
    </div>
  )
}

function Outcome({ call, onDone }) {
  const [opts, setOpts] = useState(null)
  const [pick, setPick] = useState('')
  const [notes, setNotes] = useState('')
  const [err, setErr] = useState(null)
  useEffect(() => { api.get(`/sales/appointments/${call.id}/outcome-options`).then(setOpts).catch(e => setErr(errText(e))) }, [call.id])
  const list = opts?.options || opts?.outcomes || []
  const save = async () => {
    try { await api.post(`/sales/appointments/${call.id}/outcome`, { outcome: pick, notes: notes || undefined }); onDone('Outcome saved.') }
    catch (e) { setErr(errText(e)) }
  }
  return (
    <div className="v2-card v2-form">
      <b>Record outcome</b>
      {err && <p className="v2-err">{err}</p>}
      <div className="v2-chips">{list.map(o => { const k = o.key || o.value || o; const l = o.label || o; return <button key={k} className={'v2-chip' + (pick === k ? ' on' : '')} onClick={() => setPick(k)}>{l}</button> })}</div>
      <textarea className="v2-input" rows={2} placeholder="What happened? Next step?" value={notes} onChange={e => setNotes(e.target.value)} />
      <div className="v2-row"><button className="v2-btn v2-primary" disabled={!pick} onClick={save}>Save outcome</button><button className="v2-btn" onClick={() => onDone(null)}>Cancel</button></div>
    </div>
  )
}

export function CallDetail() {
  const { id } = useParams()
  const [sp, setSp] = useSearchParams()
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [mode, setMode] = useState(sp.get('tab') === 'outcome' ? 'outcome' : null)
  const [tab, setTab] = useState(sp.get('tab') === 'replays' ? 'replays' : 'overview')
  const [notes, setNotes] = useState('')
  const [toast, setToast] = useState(sp.get('booked') === 'sent' ? 'Booked and invite sent.' : sp.get('booked') === 'quiet' ? 'Booked. Nothing was sent to the guest.' : null)
  const [confirmCancel, setConfirmCancel] = useState(false)
  const load = useCallback(() => api.get(`${API}/calls/${id}`).then(x => { setD(x); setNotes(x.call.notes || '') }).catch(e => setErr(errText(e))), [id])
  useEffect(() => { load() }, [load])
  useEffect(() => { if (sp.get('booked')) { const p = new URLSearchParams(sp); p.delete('booked'); setSp(p, { replace: true }) } }, []) // eslint-disable-line
  if (err) return <div className="v2-page"><p className="v2-err">{err}</p></div>
  if (!d) return <div className="v2-page"><div className="v2-skel card" /><div className="v2-skel card" /></div>
  const c = d.call
  const join = joinUrl(c)
  const past = new Date(c.ends_at + 'Z') < new Date()
  const cancelled = c.status === 'cancelled'
  const done = t => { setMode(null); if (t) setToast(t); load() }
  const act = async (fn, ok) => { try { await fn(); setToast(ok); load() } catch (e) { setErr(errText(e)) } }
  const host = (c.participants || []).map(p => p.full_name).filter(Boolean).join(', ') || 'Host'
  const v = c.video || {}
  return (
    <div className="v2-page">
      <Head title="Appointment detail" sub="Open the meeting, manage the invite, and see related replies." />
      {!cancelled && (
        <div className="v2-row v2-headacts">
          {join && !past && <a className="v2-btn v2-primary" href={join} target="_blank" rel="noopener noreferrer">Join Zoom</a>}
          {join && <button className="v2-btn" onClick={() => navigator.clipboard?.writeText(join).then(() => setToast('Zoom link copied.'))}>Copy invite link</button>}
          {!past && <button className="v2-btn" onClick={() => act(() => api.post(`/sales/appointments/${c.id}/resend-invitation`, {}), 'Invite sent again.')} disabled={!c.prospect?.email}>Resend invite</button>}
          {!past && <button className="v2-btn" onClick={() => setMode('move')}>Reschedule</button>}
          {!past && <button className="v2-btn" onClick={() => setConfirmCancel(true)}>Cancel appointment</button>}
          {past && <button className="v2-btn v2-primary" onClick={() => setMode('outcome')}>Record outcome</button>}
        </div>
      )}
      {confirmCancel && (
        <div className="v2-confirm" role="alertdialog"><b>Cancel this call?</b> The guest gets a cancellation and the Zoom meeting is removed.
          <div className="v2-row"><button className="v2-btn v2-primary" onClick={() => { setConfirmCancel(false); act(() => api.post(`/sales/appointments/${c.id}/cancel`, { reason: 'Cancelled from EvoSys Sales' }), 'Call cancelled.') }}>Yes, cancel it</button><button className="v2-btn" onClick={() => setConfirmCancel(false)}>Keep it</button></div></div>
      )}
      {mode === 'move' && <Reschedule call={c} onDone={done} />}
      {mode === 'outcome' && <Outcome call={c} onDone={done} />}
      <div className="v2-filters" role="tablist">
        {[['overview', 'Overview'], ['replies', 'Replies'], ['replays', 'Replays'], ['activity', 'Activity']].map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={'v2-filter' + (tab === k ? ' on' : '')} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {tab === 'overview' && (
        <article className="v2-card">
          <h2 className="v2-serif">{c.opportunity_company || c.prospect?.company || c.prospect?.name || c.title}</h2>
          <div className="v2-eyebrow">{cancelled ? 'Cancelled' : past ? 'Done' : 'Booked'} · {ctDay(c.starts_at + 'Z')} · {ctTime(c.starts_at + 'Z')}–{ctTime(c.ends_at + 'Z')} CT</div>
          <dl className="v2-dl">
            <dt>Calendar and host</dt><dd>{host} · {(c.participants || []).some(p => p.calendar_synced) ? 'on the connected calendar' : (c.participants || [])[0]?.sync_label || 'calendar not synced'}</dd>
            <dt>Zoom</dt><dd>{join ? 'Participant link ready' : (v.label || 'No Zoom meeting yet')}{v.needs_attention ? ' · needs attention' : ''}{!join && !past && !cancelled ? <> · <button className="v2-linkbtn" onClick={() => act(() => api.post(`/sales/appointments/${c.id}/video/retry`, {}), 'Zoom link requested again.')}>Try again</button></> : null}</dd>
            <dt>Invitation</dt><dd>{c.prospect_invite_sent_at ? 'Sent ' + ctDay(c.prospect_invite_sent_at + 'Z') + ' ' + ctTime(c.prospect_invite_sent_at + 'Z') + (c.prospect?.email ? ' to ' + c.prospect.email : '') : c.prospect_invite_error ? 'Not delivered: ' + c.prospect_invite_error : c.prospect?.email ? 'Not sent' : 'No email on file'}</dd>
            {c.outcome_state?.outcome && <><dt>Outcome</dt><dd>{c.outcome_state.outcome_label || c.outcome_state.outcome}</dd></>}
          </dl>
          <label className="v2-field"><span>Internal notes</span><textarea className="v2-input" rows={3} placeholder="Add notes and next steps for this call." value={notes} onChange={e => setNotes(e.target.value)} /></label>
          <div className="v2-row">
            <button className="v2-btn v2-primary" onClick={() => act(() => api.post(`${API}/calls/${c.id}/notes`, { notes }), 'Notes saved.')}>Save notes</button>
            {c.opportunity_id && <Link className="v2-btn" to={`${BASE}/leads/${c.opportunity_id}`}>Open lead</Link>}
          </div>
        </article>
      )}
      {tab === 'replies' && (
        <article className="v2-card">
          {d.replies.length ? d.replies.map(m => <div key={m.id} className="v2-listrow"><b>{ctDay(m.at)} {ctTime(m.at)}</b><span>{m.translation || m.body}</span></div>) : <p className="v2-muted">No replies from this guest yet.</p>}
          {c.opportunity_id && <Link className="v2-btn v2-primary" to={`${BASE}?tab=needs&c=${c.opportunity_id}`}>Open conversation</Link>}
        </article>
      )}
      {tab === 'replays' && (
        <article className="v2-card">
          {d.replays.items.length ? d.replays.items.map((r, k) => <a key={k} className="v2-btn v2-primary" href={r.url}>Play replay</a>) : <p className="v2-muted">{d.replays.reason}</p>}
          <Link className="v2-btn" to={`${BASE}/appointments?view=past`}>View past calls</Link>
        </article>
      )}
      {tab === 'activity' && (
        <article className="v2-card">{d.activity.length ? d.activity.map((a, k) => <div key={k} className="v2-listrow"><b>{ctDay(a.at)} {ctTime(a.at)}</b><span>{a.summary}</span></div>) : <p className="v2-muted">No activity yet.</p>}</article>
      )}
      <Toast text={toast} onDone={() => setToast(null)} />
    </div>
  )
}

// ── settings ─────────────────────────────────────────────────────────────────
export function CallsSettings() {
  const [d, setD] = useState(null)
  const [tab, setTab] = useState('availability')
  const [err, setErr] = useState(null)
  const [fwd, setFwd] = useState('')
  const [toast, setToast] = useState(null)
  const load = useCallback(() => api.get(`${API}/calls-settings`).then(x => { setD(x); setFwd(x.calling?.forward_to || '') }).catch(e => setErr(errText(e))), [])
  useEffect(() => { load() }, [load])
  const saveFwd = async () => { try { await api.put(`${API}/outreach`, { voice_forward_to: fwd }); setToast('Saved. Calls to the Sales number ring ' + fwd + '.'); load() } catch (e) { setErr(errText(e)) } }
  return (
    <div className="v2-page">
      <Head title="Appointment settings" sub="Manage availability, calendar, Zoom, reminders, and alerts." />
      <CallsNav here="settings" />
      <div className="v2-filters" role="tablist">
        {[['availability', 'Availability'], ['calendar', 'Calendar'], ['zoom', 'Zoom'], ['notifications', 'Notifications'], ['calling', 'Calling']].map(([k, l]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={'v2-filter' + (tab === k ? ' on' : '')} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      {err && <p className="v2-err">{err}</p>}
      {!d && !err && <div className="v2-skel card" />}
      {d && tab === 'availability' && (
        <article className="v2-card v2-form">
          <dl className="v2-dl">
            <dt>Default host</dt><dd>{d.host.name}</dd>
            <dt>Appointment length</dt><dd>{d.duration_minutes} minutes, {d.gap_between_calls_minutes} minutes kept free between calls</dd>
            <dt>Time zone</dt><dd>{d.timezone}</dd>
            <dt>Notice</dt><dd>At least {Math.round((d.min_notice_minutes || 0) / 60)} hours ahead · up to {d.booking_horizon_days} days out</dd>
            <dt>Hours</dt><dd>{d.windows.length ? d.windows.map((w, k) => <div key={k}>{DAYS[w.day]} {hm(w.start)}–{hm(w.end)}</div>) : 'No hours set'}</dd>
            {d.blocks.length > 0 && <><dt>Blocked</dt><dd>{d.blocks.map((b, k) => <div key={k}>{DAYS[b.day]} {hm(b.start)}–{hm(b.end)} · {b.label}</div>)}</dd></>}
          </dl>
          <div className="v2-row"><Link className="v2-btn v2-primary" to="/sales/availability">Edit days, hours, and exceptions</Link></div>
        </article>
      )}
      {d && tab === 'calendar' && (
        <article className="v2-card">
          {d.calendars.length ? d.calendars.map((x, k) => <div key={k} className="v2-listrow"><b>{x.provider === 'google' ? 'Google Calendar' : x.provider}</b><span>{x.account || 'account'} · {x.connected ? 'Connected' : 'Not connected'}</span></div>) : <p className="v2-muted">No calendar connected for {d.host.name}.</p>}
          <div className="v2-row"><Link className="v2-btn" to="/settings">View connection and choose calendar</Link><button className="v2-btn" onClick={load}>Check connections</button></div>
        </article>
      )}
      {d && tab === 'zoom' && (
        <article className="v2-card"><div className="v2-listrow"><b>Zoom</b><span>{d.zoom.ready ? 'Connected · every booked call gets its own participant link' : 'Not ready' + (d.zoom.detail ? ': ' + d.zoom.detail : '')}</span></div>
          <div className="v2-row"><button className="v2-btn" onClick={load}>Check connections</button></div></article>
      )}
      {d && tab === 'notifications' && (
        <article className="v2-card">
          <b>Reminders to the guest</b>{d.reminders.map((r, k) => <div key={k} className="v2-listrow"><span>{r}</span></div>)}
          <b>Reply and booking alerts</b>{d.alerts.map((a, k) => <div key={k} className="v2-listrow"><b>{a.who}</b><span>{[a.phone && 'text ' + a.phone, a.email && 'email ' + a.email].filter(Boolean).join(' · ') || 'no contact set'}</span></div>)}
          <div className="v2-hint">Staff alerts come from the EvoSys notifications line, never the Sales number customers see.</div>
          <div className="v2-row"><Link className="v2-btn" to={`${BASE}/outreach`}>Edit alert recipients</Link></div>
        </article>
      )}
      {d && tab === 'calling' && (
        <article className="v2-card v2-form">
          <div className="v2-listrow"><b>Sales number</b><span>{d.calling.number || 'not set'} · {d.calling.ready ? 'Calls and texts ready' : d.calling.blocker}</span></div>
          <label className="v2-field"><span>When someone calls the Sales number, ring</span><input className="v2-input" type="tel" value={fwd} onChange={e => setFwd(e.target.value)} /></label>
          <div className="v2-row"><button className="v2-btn v2-primary" onClick={saveFwd}>Save settings</button></div>
        </article>
      )}
      <Toast text={toast} onDone={() => setToast(null)} />
    </div>
  )
}
