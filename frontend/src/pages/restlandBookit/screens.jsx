// Restland BookIT — screens. Data comes only from /restland-bookit/*, which is
// already scoped on the server to what this login may see.
import React, { useEffect, useMemo, useState } from 'react'
import { createPortal } from 'react-dom'
import { api } from '../../api/client'
import { useBk } from './BookitApp'
import { AreaChart, Bars, Empty, Icon, Kpi, Network, Skeleton, fmtDate, fmtStamp, fmtTime, label, newKey, num, pct, usd } from './ui'

const R = '/restland-bookit'

function useData(path, deps = []) {
  const { tick, say } = useBk()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    if (!path) return undefined
    let live = true
    const ctl = typeof AbortController !== 'undefined' ? new AbortController() : null
    api.get(path, ctl ? { signal: ctl.signal } : {}).then(d => { if (live) { setData(d); setError(null) } })
      .catch(e => { if (live) { setError(e.message || 'Could not load'); if (!data) say(e.message || 'Could not load', true) } })
    return () => { live = false; if (ctl) ctl.abort() }
  }, [path, tick, ...deps])                                    // eslint-disable-line react-hooks/exhaustive-deps
  return [data, error, setData]
}

async function send(fn, say, ok) {
  try { const r = await fn(); if (ok) say(ok); return r } catch (e) { say(e.message || 'That did not save', true); return null }
}

const today = () => new Date().toLocaleDateString('en-CA', { timeZone: 'America/Chicago' })

// ── search (only the caller's own records come back from the server) ────────

export function Search() {
  const { go } = useBk()
  const [q, setQ] = useState('')
  const [work, setWork] = useState(null)
  const [open, setOpen] = useState(false)
  useEffect(() => {
    if (!q) return undefined
    const t = setTimeout(() => { if (!work) api.get(R + '/work').then(setWork).catch(() => {}) }, 300)
    return () => clearTimeout(t)
  }, [q])                                                     // eslint-disable-line react-hooks/exhaustive-deps
  const hits = useMemo(() => {
    if (!work || q.length < 2) return []
    const s = q.toLowerCase()
    const m = x => [x.family, x.phone, x.email].some(v => (v || '').toLowerCase().includes(s))
    return [...work.appointments.filter(m).map(a => ({ ...a, kind: 'appointment' })),
            ...work.follow_ups.filter(m).map(f => ({ ...f, kind: 'follow-up' }))].slice(0, 12)
  }, [work, q])
  return (
    <div className="bk-search">
      <Icon name="search" />
      <input value={q} placeholder="Search your families" aria-label="Search your families"
             onChange={e => { setQ(e.target.value); setOpen(true) }} onBlur={() => setTimeout(() => setOpen(false), 200)} />
      {open && hits.length > 0 && <div className="bk-results">{hits.map(h => (
        <button key={h.kind + h.id} onMouseDown={() => { setQ(''); go((h.kind === 'appointment' ? 'appointments' : 'follow-ups') + '?open=' + encodeURIComponent(h.id)) }}>
          <b>{h.family}</b> <span className="faint">· {h.kind}{h.date ? ' · ' + fmtDate(h.date) : ''}</span>
        </button>))}</div>}
    </div>
  )
}

function openParam() { try { return new URLSearchParams(window.location.search).get('open') } catch { return null } }

// ── My Day ──────────────────────────────────────────────────────────────────


// Filters and buttons go top right, beside the page title (owner's preview).
export function Toolbar({ children }) {
  const { toolbar } = useBk()
  return toolbar ? createPortal(<div className="bk-actions">{children}</div>, toolbar) : <div className="bk-bar">{children}</div>
}

const isDue = s => /^(Due|Needs|Overdue)/.test(s || '')
export function Pill({ children }) { return <span className={'bk-pill' + (isDue(children) ? ' due' : /^No-?Show|Cancel/.test(children || '') ? ' err' : '')}>{children}</span> }

// One appointment line: "Wed, Nov 25 · 10:00 AM" / family / how · advisor, then status + Open.
export function ApptRow({ a, onOpen, showWho }) {
  return (
    <div className="bk-line">
      <div style={{ minWidth: 0 }}><div className="time">{fmtDate(a.date, { weekday: 'short', month: 'short', day: 'numeric' })} · {fmtTime(a.time)}</div>
        <strong>{a.family}</strong><p className="muted">{a.how}{showWho && a.assigned ? ' · ' + a.assigned : ''}{a.saving ? ' · Saving…' : ''}</p></div>
      <div className="bk-actions"><Pill>{a.outcome || a.status}</Pill><ApptButtons a={a} onOpen={onOpen} /></div>
    </div>
  )
}

// One follow-up: family + status, why · advisor, what they said, Review follow-up.
export function FuRow({ f, onOpen, showWho, t }) {
  const late = f.open && f.follow_up_on && f.follow_up_on < t
  const st = !f.open ? (f.status || 'Done') : late ? 'Overdue' : f.due_now ? 'Due now'
    : (f.why || '').startsWith('Needs a person') ? 'Needs a person' : f.follow_up_on ? 'Follow up ' + fmtDate(f.follow_up_on, { month: 'short', day: 'numeric' }) : 'Waiting'
  return (
    <div className="bk-line col">
      <div className="bk-line flat"><strong>{f.family}</strong><Pill>{st}</Pill></div>
      <p className="muted">{(f.why || 'Follow-up').split('\n')[0]}{showWho && f.assigned ? ' · ' + f.assigned : ''}</p>
      {f.said && <p className="said">{f.said.length > 220 ? f.said.slice(0, 220) + '…' : f.said}</p>}
      <div className="bk-actions"><button className="bk-btn" onClick={() => onOpen(f.id)}>Review follow-up</button></div>
    </div>
  )
}

// ── visit types (owner, Nov 26): one label and color everywhere ─────────────
export const VISIT = {
  onsite: ['Onsite Visit', 'onsite'], call: ['Call In', 'call'],
  offsite: ['Offsite Visit', 'offsite'], unknown: ['Type Unspecified', 'unknown'],
}
export function VisitTag({ v }) { const [l, c] = VISIT[v] || VISIT.unknown; return <span className={'bk-kind ' + c}>{l}</span> }

// Follow-up urgency for the advisor (owner, Nov 26 SS1/SS4) - not sales
// interest. Hot: overdue or an urgent unresolved issue. Warm: due now/today
// or a new reply that needs a person. Cool: upcoming or waiting on the family.
// Closed follow-ups show their real status instead. Same rules everywhere;
// "Overdue" here and the "N overdue" counts use the same Central-time date.
const URGENT = /urgent|upset|complain|angry|emergency|asap|frustrat/i
export function urgency(f, t) {
  if (!f.open) return null
  if (f.follow_up_on && f.follow_up_on < t) return { c: 'hot', l: 'Hot', why: 'Overdue', r: 0, k: f.follow_up_on }
  if (URGENT.test(f.why || '')) return { c: 'hot', l: 'Hot', why: 'Urgent', r: 1, k: f.added || '' }
  if (f.new_reply) return { c: 'warm', l: 'Warm', why: 'New reply', r: 2, k: f.family_at || '' }
  if (f.due_now || f.follow_up_on === t) return { c: 'warm', l: 'Warm', why: f.follow_up_on === t ? 'Due today' : 'Due now', r: 3, k: f.follow_up_on || f.added || '' }
  return { c: 'cool', l: 'Cool', why: f.follow_up_on ? 'Follow up ' + fmtDate(f.follow_up_on, { month: 'short', day: 'numeric' }) : 'Waiting on family', r: 4, k: f.follow_up_on || '9999' }
}
export const isOverdue = (f, t) => !!(f.open && f.follow_up_on && f.follow_up_on < t)
export function byUrgency(list, t) {
  return [...list].sort((x, y) => {
    const a = urgency(x, t) || { r: 9, k: '' }, b = urgency(y, t) || { r: 9, k: '' }
    return a.r - b.r || (a.r === 2 ? b.k.localeCompare(a.k) : a.k.localeCompare(b.k)) || String(x.family).localeCompare(String(y.family))
  })
}
export function Urgency({ f, t }) {
  const u = urgency(f, t)
  if (!u) return <Pill>{f.status === 'Open' ? 'Done' : (f.status || 'Done')}</Pill>
  return <span className={'bk-urg ' + u.c} title={u.why}><span className="bars"><i /><i /><i /></span>{u.l}<small>{u.why}</small></span>
}
// "Nov 26 · 4:18 PM" from a Central "YYYY-MM-DDTHH:MM".
export function ctStamp(s) {
  if (!s) return ''
  const [d, tm] = String(s).split('T')
  return fmtDate(d, { month: 'short', day: 'numeric' }) + (tm ? ' · ' + fmtTime(tm.slice(0, 5)) : '')
}
function nextAction(f) {
  if (f.open && f.new_reply) return 'New reply · Reply'
  if (!f.open && f.fu_result) return f.fu_result
  return (f.why || 'Follow-up').split('\n')[0]
}
// Compact follow-up rows: family / next action, urgency, last activity (CT).
export function FuTable({ rows, t, onOpen, showWho }) {
  return (
    <div className="bk-scroll"><table className="bk-compact bk-futable"><thead><tr><th>Family / next action</th><th>Urgency</th><th>Last activity (CT)</th>
      {showWho && <th>Advisor</th>}<th /></tr></thead>
      <tbody>{rows.map(f => <tr key={f.id} className={f.new_reply ? 'unread' : ''}>
        <td data-l="Family"><b>{f.family}</b>{f.new_reply && <span className="bk-newdot" title="New reply">New reply</span>}
          <div className="sub">{nextAction(f)}</div></td>
        <td data-l="Urgency"><Urgency f={f} t={t} /></td>
        <td data-l="Last activity">{f.activity ? <>{ctStamp(f.activity.at)}<div className="sub">{f.activity.kind}</div></> : <span className="muted">No activity recorded</span>}</td>
        {showWho && <td data-l="Advisor">{f.assigned || '—'}</td>}
        <td className="act"><button className="bk-btn sm" onClick={() => onOpen(f.id)}>Review</button></td></tr>)}</tbody></table></div>
  )
}

function Pager({ page, size, total, set }) {
  if (total <= size) return total ? <div className="bk-pager"><span className="muted">Showing {num(total)} of {num(total)}</span></div> : null
  const pages = Math.ceil(total / size)
  return (
    <div className="bk-pager"><span className="muted">Showing {num(page * size + 1)}–{num(Math.min(total, page * size + size))} of {num(total)}</span>
      <div className="bk-actions"><button className="bk-btn" disabled={page === 0} onClick={() => set(page - 1)}>‹ Previous</button>
        <span className="muted">Page {page + 1} of {pages}</span>
        <button className="bk-btn" disabled={page >= pages - 1} onClick={() => set(page + 1)}>Next ›</button></div></div>
  )
}
function useStored(key, init) {
  const [st, setSt] = useState(() => { try { return { ...init, ...JSON.parse(sessionStorage.getItem(key) || '{}') } } catch { return init } })
  const set = patch => setSt(cur => { const n = { ...cur, ...patch }; try { sessionStorage.setItem(key, JSON.stringify(n)) } catch { /* ignore */ } return n })
  return [st, set]
}

// Open, plus Reschedule / Cancel right on the line for a live booking (owner, Nov 27).
export function ApptButtons({ a, onOpen, sm }) {
  const c = 'bk-btn' + (sm ? ' sm' : '')
  const live = /^(Booked|Confirmed)/.test(a.status || '') && !a.outcome
  return <span className="bk-appt-btns">
    <button className={c} onClick={() => onOpen(a.id)}>Open</button>
    {live && <button className={c} onClick={() => onOpen(a.id, 'move')}>Reschedule</button>}
    {live && <button className={c + ' warn'} onClick={() => onOpen(a.id, 'cancel')}>Cancel</button>}
  </span>
}

// Compact appointment table: time (CT), family, visit type, advisor, status, Open.
export function ApptTable({ rows, onOpen, showWho, showDate = true }) {
  return (
    <div className="bk-scroll"><table className="bk-compact"><thead><tr><th>{showDate ? 'Date · time (CT)' : 'Time (CT)'}</th><th>Family</th><th>Visit type</th>
      {showWho && <th>Advisor</th>}<th>Status</th><th /></tr></thead>
      <tbody>{rows.map(a => <tr key={a.id}>
        <td className="time" data-l="When (CT)">{showDate ? fmtDate(a.date, { weekday: 'short', month: 'short', day: 'numeric' }) + ' · ' : ''}{fmtTime(a.time)}</td>
        <td data-l="Family"><b>{a.family}</b></td><td data-l="Visit"><VisitTag v={a.visit} /></td>{showWho && <td data-l="Advisor">{a.assigned || '—'}</td>}
        <td data-l="Status"><Pill>{a.outcome || a.status}</Pill></td>
        <td className="act"><ApptButtons a={a} onOpen={onOpen} sm /></td></tr>)}</tbody></table></div>
  )
}

// My Day — the owner's compact mockup: four numbers, then today's (or the next)
// appointments beside the most urgent follow-ups, five rows each, with links
// to the full views. Every number is real.
export function MyDay() {
  const { me, go } = useBk()
  const [person, setPerson] = useState('')
  const pq = person ? 'person=' + encodeURIComponent(person) : ''
  const [d] = useData(R + '/my-day' + (pq ? '?' + pq : ''), [person])
  const [w] = useData(R + '/work')
  const [perf] = useData(R + '/performance?range=this_month' + (pq ? '&' + pq : ''), [person])
  const [panel, setPanel] = useState(null)
  const picker = me.me.manager && <Toolbar><select value={person} onChange={e => setPerson(e.target.value)} aria-label="Advisor">
    <option value="">All advisors</option>{(me.team || []).map(n => <option key={n}>{n}</option>)}</select></Toolbar>
  if (!d || !w) return <>{picker}<div className="bk-card"><Skeleton rows={6} /></div></>
  const t = today()
  const k = d.kpis, m = perf?.metrics || {}
  const mine = x => !person || x.assigned === person
  const live = a => /^(Booked|Confirmed)/.test(a.status) && !a.outcome
  const todays = w.appointments.filter(a => mine(a) && a.date === t && !String(a.status).startsWith('Cancel'))
    .sort((x, y) => String(x.time).localeCompare(String(y.time)))
  const upcoming = w.appointments.filter(a => mine(a) && a.date > t && live(a))
  const showing = todays.length ? todays : upcoming
  const openFu = w.follow_ups.filter(f => mine(f) && f.open)
  const queue = byUrgency(openFu, t)
  const overdue = openFu.filter(f => isOverdue(f, t)).length
  const pick = a => {
    if (a.ref_type === 'appointment') setPanel({ type: 'appt', id: a.ref_id, close: a.kind === 'missing_outcome' })
    else if (a.ref_type === 'follow_up') setPanel({ type: 'fu', id: a.ref_id })
    else if (a.ref_type === 'referral_batch') go('evo6?open=' + a.ref_id)
    else if (a.ref_type === 'add_case') go('add-leads?open=' + a.ref_id)
    else if (a.ref_type === 'service_request') go('service?open=' + a.ref_id)
    else if (a.ref_type === 'sale') go('sales?open=' + a.ref_id)
  }
  const other = d.actions.filter(a => !['appointment', 'follow_up'].includes(a.kind))
  const who = me.me.manager && !person
  return (
    <div>
      {picker}
      <div className="bk-metrics compact">
        <Kpi l="Booked" v={num(k.upcoming_appointments)} s="Upcoming" onClick={() => go('calendar')} />
        <Kpi l="Showed" v={perf ? num(m.attended) : '…'} s="This month" onClick={() => go('performance')} />
        <Kpi l="Follow-ups open" v={num(openFu.length)} s={overdue ? num(overdue) + ' overdue' : 'None overdue'} onClick={() => go('follow-ups')} />
        <Kpi l="Contracts closed" v={perf ? num(m.closed_contracts) : '…'} s="This month" onClick={() => go('sales')} />
      </div>
      <div className="bk-split">
        <section className="bk-card"><h2>{todays.length ? 'Today’s appointments' : 'Next appointments'}<small>{todays.length ? num(todays.length) + ' today' : num(upcoming.length) + ' booked ahead'}</small></h2>
          {showing.length === 0 ? <p className="bk-line muted">Nothing on the schedule.</p>
            : <ApptTable rows={showing.slice(0, 5)} showWho={who} showDate={!todays.length} onOpen={(id, mode) => setPanel({ type: 'appt', id, mode })} />}
          <div className="bk-actions bk-panelfoot"><button className="bk-btn" onClick={() => go('appointments')}>Open appointments</button></div></section>
        <section className="bk-card"><h2>Follow-ups<small>{num(openFu.length)} open{overdue ? ' · ' + num(overdue) + ' overdue' : ''}</small></h2>
          {queue.length === 0 ? <p className="bk-line muted">No open follow-ups.</p>
            : <FuTable rows={queue.slice(0, 5)} t={t} showWho={who} onOpen={id => setPanel({ type: 'fu', id })} />}
          <div className="bk-actions bk-panelfoot"><button className="bk-btn" onClick={() => go('follow-ups')}>Open full queue{openFu.length > 5 ? ' (' + num(openFu.length) + ')' : ''}</button></div></section>
      </div>
      {other.length > 0 && <section className="bk-card" style={{ marginTop: 14 }}><h2>Also needs attention<small>{other.length}</small></h2>
        {other.slice(0, 5).map((a, i) => (
          <div key={i} className="bk-qline"><div className="bk-line flat">
            <div style={{ minWidth: 0 }}><strong>{a.name}</strong><p className="muted">{a.reason} · {a.next}</p></div>
            <div className="bk-actions"><Pill>{a.status}</Pill><button className="bk-btn sm" onClick={() => pick(a)}>Open</button></div></div></div>))}</section>}
      {panel?.type === 'appt' && <AppointmentPanel id={panel.id} startClose={panel.close} startMode={panel.mode} onClose={() => setPanel(null)} />}
      {panel?.type === 'fu' && <FollowUpPanel id={panel.id} onClose={() => setPanel(null)} />}
    </div>
  )
}

// ── Appointments (worklist) + Calendar (week grid) - different jobs over the
// same records and the same detail drawer (owner, Nov 26 SS2/SS3) ─────────

function weekDays(offset) {
  const d = new Date(today() + 'T12:00:00'); d.setDate(d.getDate() - d.getDay() + offset * 7)
  return Array.from({ length: 7 }).map((_, i) => { const x = new Date(d); x.setDate(d.getDate() + i); return x.toLocaleDateString('en-CA') })
}
const isLive = a => /^(Booked|Confirmed)/.test(a.status || '') && !a.outcome
const APPT_VIEWS = [['upcoming', 'Upcoming'], ['today', 'Today'], ['needs', 'Needs outcome'], ['past', 'Past']]
const APPT_STATUS = [['', 'Any status'], ['booked', 'Booked'], ['Showed', 'Showed'], ['No-Show', 'No-show'], ['Cancel', 'Cancelled']]
const APPT_PAGE = 15

function apptFilters(me, st, set) {
  return <>
    <select value={st.kind} onChange={e => set({ kind: e.target.value, page: 0 })} aria-label="Visit type"><option value="">All visit types</option>
      {Object.entries(VISIT).map(([k, [l]]) => <option key={k} value={k}>{l}</option>)}</select>
    {me.me.manager && <select value={st.who} onChange={e => set({ who: e.target.value, page: 0 })} aria-label="Advisor"><option value="">Every advisor</option>
      {(me.team || []).map(n => <option key={n}>{n}</option>)}</select>}
    <input placeholder="Family" value={st.q} onChange={e => set({ q: e.target.value, page: 0 })} aria-label="Find a family" /></>
}
const apptMatch = st => a => (!st.kind || (a.visit || 'unknown') === st.kind) && (!st.who || a.assigned === st.who)
  && (!st.q || [a.family, a.phone, a.email].some(x => (x || '').toLowerCase().includes(st.q.toLowerCase())))

export function Appointments() {
  const { me } = useBk()
  const [w] = useData(R + '/work')
  const [st, set] = useStored('bk_appts', { view: 'upcoming', status: '', kind: '', who: '', q: '', page: 0 })
  const [panel, setPanel] = useState(() => openParam())
  const [panelMode, setPanelMode] = useState(null)
  const [booking, setBooking] = useState(false)
  const bar = <Toolbar><div className="bk-seg">{APPT_VIEWS.map(([k, l]) => <button key={k} className={st.view === k ? 'on' : ''} onClick={() => set({ view: k, page: 0 })}>{l}</button>)}</div>
    {!me.me.view_only && <button className="bk-btn pri" onClick={() => setBooking(true)}>+ Book appointment</button>}</Toolbar>
  if (!w) return <>{bar}<div className="bk-card"><Skeleton rows={8} /></div></>
  const t = today()
  const inView = {
    upcoming: a => a.date >= t && !String(a.outcome || a.status).startsWith('Cancel') && !(a.date === t && a.outcome),
    today: a => a.date === t, needs: a => a.date && a.date < t && isLive(a), past: a => a.date && a.date < t,
  }[st.view] || (() => true)
  const byStatus = a => !st.status || (st.status === 'booked' ? isLive(a) : String(a.outcome || a.status).startsWith(st.status))
  const rows = w.appointments.filter(a => a.date && inView(a) && byStatus(a) && apptMatch(st)(a))
    .sort((x, y) => (st.view === 'past' || st.view === 'needs' ? -1 : 1) * (x.date + x.time).localeCompare(y.date + y.time))
  const pages = Math.max(1, Math.ceil(rows.length / APPT_PAGE))
  const page = Math.min(st.page, pages - 1)
  return (
    <div>
      {bar}
      <div className="bk-actions bk-filters">
        <select value={st.status} onChange={e => set({ status: e.target.value, page: 0 })} aria-label="Status">{APPT_STATUS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        {apptFilters(me, st, set)}
      </div>
      <section className="bk-card"><h2>Appointment worklist<small>{num(rows.length)} appointment{rows.length === 1 ? '' : 's'} · Central time</small></h2>
        {rows.length === 0 ? <p className="bk-line muted">No appointments match these filters.</p>
          : <ApptTable rows={rows.slice(page * APPT_PAGE, page * APPT_PAGE + APPT_PAGE)} showWho={me.me.manager} onOpen={(id, mode) => { setPanelMode(mode || null); setPanel(id) }} />}
        <Pager page={page} size={APPT_PAGE} total={rows.length} set={n => set({ page: n })} /></section>
      {panel && <AppointmentPanel id={panel} startMode={panelMode} onClose={() => { setPanel(null); setPanelMode(null) }} />}
      {booking && <NewAppointment onClose={() => setBooking(false)} />}
    </div>
  )
}

export function Calendar() {
  const { me } = useBk()
  const [w] = useData(R + '/work')
  const [st, set] = useStored('bk_calendar', { week: 0, kind: '', who: '', q: '', cancelled: false })
  const [panel, setPanel] = useState(() => openParam())
  const [booking, setBooking] = useState(false)
  const days = weekDays(st.week)
  const t = today()
  const bar = (
    <Toolbar>
      <div className="bk-seg"><button onClick={() => set({ week: st.week - 1 })}>‹ Previous</button>
        <button className={st.week === 0 ? 'on' : ''} onClick={() => set({ week: 0 })}>This week</button>
        <button onClick={() => set({ week: st.week + 1 })}>Next ›</button></div>
      {!me.me.view_only && <button className="bk-btn pri" onClick={() => setBooking(true)}>+ Book appointment</button>}
    </Toolbar>)
  if (!w) return <>{bar}<div className="bk-card"><Skeleton rows={8} /></div></>
  const rows = w.appointments.filter(a => days.includes(a.date) && (st.cancelled || !String(a.outcome || a.status).startsWith('Cancel')) && apptMatch(st)(a))
    .sort((x, y) => (x.date + x.time).localeCompare(y.date + y.time))
  return (
    <div>
      {bar}
      <div className="bk-actions bk-filters">
        {apptFilters(me, st, set)}
        <label className="bk-check"><input type="checkbox" checked={!!st.cancelled} onChange={e => set({ cancelled: e.target.checked })} /> Show cancelled</label>
        <span className="bk-legend">{Object.keys(VISIT).map(k => <VisitTag key={k} v={k} />)}</span>
      </div>
      <h2 className="bk-weekhead">{fmtDate(days[0], { weekday: 'short', month: 'short', day: 'numeric' })} – {fmtDate(days[6], { weekday: 'short', month: 'short', day: 'numeric' })} · Central time<small>{num(rows.length)} appointment{rows.length === 1 ? '' : 's'}</small></h2>
      <div className="bk-week">{days.map(d => {
        const list = rows.filter(a => a.date === d)
        return (
          <section key={d} className={'bk-card bk-day' + (d === t ? ' today' : '')}>
            <h3>{fmtDate(d, { weekday: 'short', month: 'short', day: 'numeric' })}</h3>
            {list.length === 0 ? <p className="faint">No appointments</p> : list.map(a => (
              <button key={a.id} className={'bk-event ' + (VISIT[a.visit] || VISIT.unknown)[1]} onClick={() => setPanel(a.id)}>
                <b>{fmtTime(a.time)}</b><span className="fam">{a.family}</span><VisitTag v={a.visit} />
                <small>{a.outcome || a.status}{me.me.manager && a.assigned ? ' · ' + a.assigned : ''}</small></button>))}
          </section>)
      })}</div>
      {panel && <AppointmentPanel id={panel} onClose={() => setPanel(null)} />}
      {booking && <NewAppointment onClose={() => setBooking(false)} />}
    </div>
  )
}
export const Schedule = Calendar

export function AppointmentPanel({ id, onClose, startClose, startMode }) {
  const { say, refresh, me } = useBk()
  const [d, , setD] = useData(R + '/appointments/' + encodeURIComponent(id), [id])
  const [mode, setMode] = useState(startClose ? 'close' : (startMode || 'view'))
  if (!d) return <Panel title="Appointment" onClose={onClose}><Skeleton rows={6} /></Panel>
  const a = d.appointment || {}
  const reload = () => api.get(R + '/appointments/' + encodeURIComponent(id)).then(setD).catch(() => {})
  return (
    <Panel title={a.family || 'Appointment'} sub={`${fmtDate(a.date)} · ${fmtTime(a.time)} CT · ${(VISIT[a.visit] || VISIT.unknown)[0]}`} onClose={onClose}
      foot={mode === 'view' && <>
        {a.phone && <a className="bk-btn" href={'tel:' + a.phone}><Icon name="phone" />Call</a>}
        {a.phone && <a className="bk-btn" href={'sms:' + a.phone}><Icon name="text" />Text</a>}
        {a.email && <a className="bk-btn" href={'mailto:' + a.email}><Icon name="mail" />Email</a>}
        {/^(Booked|Confirmed)/.test(a.status || '') && !a.outcome && <>
          <button className="bk-btn" onClick={() => setMode('move')}>Reschedule</button>
          <button className="bk-btn" onClick={() => setMode('cancel')}>Cancel</button></>}
        <button className="bk-btn" onClick={() => setMode('sale')}><Icon name="dollar" />Record sale</button>
        <button className="bk-btn pri" onClick={() => setMode('close')}><Icon name="check" />{d.disposition?.closed_at ? 'View ticket' : 'Close ticket'}</button></>}>
      {mode === 'move' && <MoveAppointment id={id} a={a} onDone={() => { reload(); refresh(); setMode('view') }} onCancel={() => setMode('view')} />}
      {mode === 'cancel' && <CancelAppointment id={id} a={a} onDone={() => { reload(); refresh(); setMode('view') }} onCancel={() => setMode('view')} />}
      {mode === 'close' && <CloseTicket d={d} onDone={() => { reload(); refresh(); setMode('view') }} onSale={() => setMode('sale')} />}
      {mode === 'sale' && <SaleEditor appointmentId={id} onDone={() => { reload(); refresh(); setMode('view') }} onCancel={() => setMode('view')} />}
      {mode === 'view' && <>
        <div className="bk-card" style={{ marginBottom: 12 }}>
          <dl className="bk-kv">
            <dt>Visit type</dt><dd><VisitTag v={a.visit} />{a.visit_detail ? ' ' + a.visit_detail : ''}{a.visit === 'unknown' && a.how ? ' (recorded as “' + a.how + '”)' : ''}</dd>
            <dt>Status</dt><dd>{statusBadge(a.outcome || a.status)}</dd>
            <dt>Assigned to</dt><dd>{d.assigned || '—'}</dd>
            <dt>Phone</dt><dd>{a.phone || '—'}</dd><dt>Email</dt><dd>{a.email || '—'}</dd>
            {a.home_address && <><dt>{a.visit === 'offsite' ? 'Visit address' : 'Home address'}</dt><dd>{a.home_address}</dd></>}
            {a.link && <><dt>Call link</dt><dd>{a.link}</dd></>}
            <dt>Came from</dt><dd>{label(d.attribution?.acquisition_source)}{d.attribution?.booking_source ? ' · booked by ' + label(d.attribution.booking_source) : ''}</dd>
            <dt>Paperwork</dt><dd>Permission to Access: {label(d.paperwork?.pta_status)} · Planning Guide: {label(d.paperwork?.ppg_status)}</dd>
            {d.sales?.length > 0 && <><dt>Sales</dt><dd>{d.sales.map(s => <div key={s.id}>{usd(s.net_cents)} · {label(s.status)}{s.contract_ref ? ' · ' + s.contract_ref : ''}</div>)}</dd></>}
          </dl>
        </div>
        <GoldFields kind="appointments" row={a} manager={me.me.manager} onSaved={reload} />
        <div className="bk-card"><h2>Conversation<small>last {d.conversation.length}</small></h2>
          {d.conversation.length === 0 ? <Empty>No messages yet.</Empty> : d.conversation.slice(0, 20).map((m, i) => (
            <div key={i} className={'bk-msg ' + m.dir}>{m.body}<small>{m.channel} · {fmtStamp(m.at)}</small></div>))}
        </div>
      </>}
    </Panel>
  )
}

// Reschedule / cancel - the same server path as the command center (open-time
// check, calendar move, the family's confirmation). Success is shown only after
// the server confirms it.
// Book a new appointment (owner, Nov 27): a family the caller may work, a time
// that is open on the real calendar, how it happens. "Booked" is shown only
// after the server confirms the calendar booked it; a repeat of the same
// family and time returns the same booking, never a second one.
export function NewAppointment({ leadId, onClose }) {
  const { me, say, refresh } = useBk()
  const [w] = useData(R + '/work')
  const [q, setQ] = useState('')
  const [v, setV] = useState({ lead_id: leadId || '', date: '', time: '', how: 'At Restland', address: '', notify: true })
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(null)
  const fams = useMemo(() => {
    const m = new Map()
    for (const x of [...(w?.follow_ups || []), ...(w?.appointments || [])]) if (x.lead_id && !m.has(x.lead_id)) m.set(x.lead_id, x)
    return [...m.values()].sort((x, y) => String(x.family).localeCompare(String(y.family)))
  }, [w])
  const picked = fams.find(f => f.lead_id === v.lead_id)
  const s = q.trim().toLowerCase()
  const hits = s.length < 2 ? [] : fams.filter(f => [f.family, f.phone, f.email].some(x => (x || '').toLowerCase().includes(s))).slice(0, 8)
  if (me.me.view_only) return <Panel title="Book an appointment" onClose={onClose}><Empty>Read-only preview: booking is turned off here.</Empty></Panel>
  const book = async () => {
    if (!v.lead_id) { say('Pick the family.', true); return }
    if (!v.date || !v.time) { say('Pick the date and the time.', true); return }
    if (v.how === 'At Their Home' && !v.address.trim()) { say('A visit at their home needs the full home address.', true); return }
    setBusy(true)
    const r = await send(() => api.post(R + '/appointments/new', { data: { lead_id: v.lead_id, slot: v.date + 'T' + v.time, how: v.how, address: v.address, notify: v.notify } }), say)
    setBusy(false)
    if (r && r.booked) { setDone(r); refresh() }
  }
  if (done) {
    const when = fmtDate(done.slot.slice(0, 10)) + ' at ' + fmtTime(done.slot.slice(11, 16)) + ' CT'
    return (
      <Panel title="Booked" sub={picked ? picked.family : ''} onClose={onClose} foot={<button className="bk-btn pri" onClick={onClose}>Done</button>}>
        <div className="bk-card"><p className="bk-line"><strong>{done.already ? 'Already booked for ' + when + '.' : 'Booked for ' + when + '.'}</strong></p>
          <p className="muted">{done.simulated ? 'Test family: checked against the calendar, nothing was sent.'
            : done.already ? 'Nothing new was sent.' : done.family_notified ? 'The calendar confirmed it and the family gets the usual confirmation.' : 'The calendar confirmed it. No confirmation was sent to the family.'}</p>
          <p className="faint">It shows on Appointments and Calendar now; the advisor is set on the board's next pass (about 2 minutes).</p></div>
      </Panel>)
  }
  return (
    <Panel title="Book an appointment" sub="Central time · checked against the real calendar" onClose={onClose}
      foot={<><button className="bk-btn" onClick={onClose}>Cancel</button>
        <button className="bk-btn pri" onClick={book} disabled={busy}>{busy ? 'Booking…' : 'Book appointment'}</button></>}>
      <div className="bk-card">
        <div className="bk-field gold"><label>Family</label>
          {picked ? <div className="bk-line flat"><strong>{picked.family}</strong>{!leadId && <button className="bk-btn sm" onClick={() => setV({ ...v, lead_id: '' })}>Change</button>}</div>
            : <><input placeholder="Search by name, phone or email" value={q} onChange={e => setQ(e.target.value)} autoFocus />
              {hits.length > 0 && <div className="bk-list">{hits.map(f => <button key={f.lead_id} className="bk-row" onClick={() => { setV({ ...v, lead_id: f.lead_id }); setQ('') }}>
                <div><div className="t">{f.family}</div><div className="d">{f.phone || f.email || ''}{me.me.manager && f.assigned ? ' · ' + f.assigned : ''}</div></div></button>)}</div>}
              {s.length >= 2 && hits.length === 0 && <p className="faint">No family of yours matches.</p>}</>}
        </div>
        <div className="bk-grid g2">
          <div className="bk-field gold"><label>Date</label><input type="date" min={today()} value={v.date} onChange={e => setV({ ...v, date: e.target.value })} /></div>
          <div className="bk-field gold"><label>Time (CT)</label><select value={v.time} onChange={e => setV({ ...v, time: e.target.value })}><option value="" />
            {me.choices.times.map(t => <option key={t} value={to24(t)}>{t}</option>)}</select></div>
        </div>
        <div className="bk-field gold"><label>How</label><select value={v.how} onChange={e => setV({ ...v, how: e.target.value })}>{me.choices.how.map(t => <option key={t}>{t}</option>)}</select></div>
        {v.how === 'At Their Home' && <div className="bk-field gold"><label>Home address</label><input value={v.address} placeholder="Street, city, state, ZIP" onChange={e => setV({ ...v, address: e.target.value })} /></div>}
        <label className="bk-check"><input type="checkbox" checked={v.notify} onChange={e => setV({ ...v, notify: e.target.checked })} /> Send the family the usual confirmation</label>
      </div>
    </Panel>
  )
}
// "10:00 AM" -> "10:00"
function to24(t) {
  const m = /^(\d{1,2}):(\d{2})\s*(AM|PM)$/i.exec(String(t).trim())
  if (!m) return t
  let h = Number(m[1]) % 12
  if (m[3].toUpperCase() === 'PM') h += 12
  return String(h).padStart(2, '0') + ':' + m[2]
}

function MoveAppointment({ id, a, onDone, onCancel }) {
  const { say } = useBk()
  const [slot, setSlot] = useState((a.date || today()) + 'T' + (a.time || '10:00'))
  const [busy, setBusy] = useState(false)
  const go = async () => {
    setBusy(true)
    const r = await send(() => api.post(R + '/appointments/' + encodeURIComponent(id) + '/reschedule', { data: { slot } }), say)
    setBusy(false)
    if (r && r.ok) { say('Moved to ' + fmtDate(slot.slice(0, 10)) + ' at ' + fmtTime(slot.slice(11, 16)) + ' CT. The family gets the confirmation.'); onDone() }
  }
  return (
    <div className="bk-card"><h2>Reschedule</h2>
      <div className="bk-field"><label>New date and time (Central)</label>
        <input type="datetime-local" step="900" value={slot} onChange={e => setSlot(e.target.value)} /></div>
      <p className="faint">The time has to be open on the calendar. The family gets the usual "your appointment moved" text or email.</p>
      <div className="bk-actions"><button className="bk-btn" onClick={onCancel}>Back</button>
        <button className="bk-btn pri" onClick={go} disabled={busy || !slot}>{busy ? 'Moving…' : 'Move appointment'}</button></div>
    </div>
  )
}

function CancelAppointment({ id, a, onDone, onCancel }) {
  const { say } = useBk()
  const [notify, setNotify] = useState(true)
  const [busy, setBusy] = useState(false)
  const go = async () => {
    setBusy(true)
    const r = await send(() => api.post(R + '/appointments/' + encodeURIComponent(id) + '/cancel', { data: { notify } }), say)
    setBusy(false)
    if (r && r.ok) { say('Cancelled.' + (notify ? ' The family gets the cancellation note.' : '')); onDone() }
  }
  return (
    <div className="bk-card"><h2>Cancel this appointment?</h2>
      <p>{a.family} · {fmtDate(a.date)} · {fmtTime(a.time)} CT</p>
      <label className="bk-line flat"><span><input type="checkbox" checked={notify} onChange={e => setNotify(e.target.checked)} /> Let the family know (text/email)</span></label>
      <p className="faint">The family goes back to a person to find a new time.</p>
      <div className="bk-actions"><button className="bk-btn" onClick={onCancel}>Keep it</button>
        <button className="bk-btn pri" onClick={go} disabled={busy}>{busy ? 'Cancelling…' : 'Cancel appointment'}</button></div>
    </div>
  )
}

function GoldFields({ kind, row, manager, onSaved }) {
  const { say, refresh, me } = useBk()
  const fields = kind === 'appointments'
    ? [['outcome', 'Outcome', me.choices.outcomes], ['sold', 'Sold?', me.choices.sold], ['sold_what', 'What was sold'], ['link', 'Call link / details'], ['notes', 'Team notes', null, true]]
    : []
  const [v, setV] = useState(() => Object.fromEntries(fields.map(([k]) => [k, row[k] || ''])))
  const save = async () => {
    const changes = Object.fromEntries(Object.entries(v).filter(([k, x]) => (row[k] || '') !== x))
    if (!Object.keys(changes).length) return
    const r = await send(() => api.patch(R + '/' + kind + '/' + encodeURIComponent(row.id), { data: changes }), say, 'Saved — it reaches the sheet and the app within a couple of minutes.')
    if (r) { onSaved && onSaved(); refresh() }
  }
  return (
    <div className="bk-card" style={{ marginBottom: 12 }}>
      <h2>Your update</h2>
      {fields.map(([k, l, opts, area]) => (
        <div key={k} className="bk-field gold"><label>{l}</label>
          {opts ? <select value={v[k]} onChange={e => setV({ ...v, [k]: e.target.value })}><option value="" />{opts.map(o => <option key={o}>{o}</option>)}</select>
            : area ? <textarea value={v[k]} onChange={e => setV({ ...v, [k]: e.target.value })} />
              : <input value={v[k]} onChange={e => setV({ ...v, [k]: e.target.value })} />}
        </div>))}
      <button className="bk-btn pri" onClick={save}>Save</button>
    </div>
  )
}

// Close Appointment Ticket — a compact step editor (s16).
function CloseTicket({ d, onDone, onSale }) {
  const { me, say, go } = useBk()
  const dis = d.disposition || {}
  const [v, setV] = useState({ outcome: dis.outcome || '', sales_disposition: dis.sales_disposition || '',
    pta_status: dis.pta_status || d.paperwork?.pta_status || '', ppg_status: dis.ppg_status || d.paperwork?.ppg_status || '',
    evo6_status: dis.evo6_status || '', evo6_reason: dis.evo6_reason || '', follow_up_on: dis.follow_up_on || '', final_notes: dis.final_notes || '' })
  const [step, setStep] = useState(0)
  const attended = v.outcome === 'Showed'
  const steps = attended ? ['Outcome', 'Sale', 'Paperwork', 'File Review 6 Block', 'Notes'] : ['Outcome', 'Notes']
  const set = (k, x) => setV(o => ({ ...o, [k]: x }))
  const save = async (close, then) => {
    const r = await send(() => api.post(R + '/appointments/' + encodeURIComponent(d.appointment.id) + '/ticket',
      { data: { ...v, follow_up_on: v.follow_up_on || null, close } }), say, close ? 'Ticket closed.' : 'Draft saved.')
    if (!r) return
    if (then === 'evo6') go('evo6?new=' + encodeURIComponent(d.lead_id) + '&appt=' + encodeURIComponent(d.appointment.id))
    else if (then === 'sale') onSale()
    else onDone()
  }
  const locked = dis.closed_at && !me.me.manager
  const sel = (k, opts) => <select value={v[k]} disabled={locked} onChange={e => set(k, e.target.value)}><option value="" />{opts.map(o => <option key={o} value={o}>{label(o)}</option>)}</select>
  const cur = steps[Math.min(step, steps.length - 1)]
  return (
    <div>
      {dis.closed_at && <div className="bk-chip" style={{ marginBottom: 10 }}>Closed {fmtStamp(dis.closed_at)} by {dis.closed_by}{locked ? ' · ask management to correct it' : ''}</div>}
      <div className="bk-steps">{steps.map((s, i) => <button key={s} className={(i === step ? 'on' : '') + (i < step ? ' done' : '')} onClick={() => setStep(i)}>{i + 1}. {s}</button>)}</div>
      <div className="bk-card">
        {cur === 'Outcome' && <div className="bk-field gold"><label>How did the appointment go?</label>{sel('outcome', me.choices.outcomes)}</div>}
        {cur === 'Sale' && <>
          <div className="bk-field gold"><label>Sales result</label>{sel('sales_disposition', me.choices.sales_dispositions)}</div>
          {v.sales_disposition === 'closed_sale' && <p className="muted">Record the products and amounts — the ticket can close once the sale is recorded. <button className="bk-btn ghost" onClick={() => save(false, 'sale')}>Open the sale editor</button></p>}
          {['pending_decision', 'follow_up_needed'].includes(v.sales_disposition) && <div className="bk-field gold"><label>Follow up on</label><input type="date" value={v.follow_up_on} onChange={e => set('follow_up_on', e.target.value)} /></div>}
          <p className="faint">A pending decision is never counted as a closed sale.</p></>}
        {cur === 'Paperwork' && <>
          <div className="bk-field gold"><label>Permission to Access form</label>{sel('pta_status', me.choices.pta)}</div>
          <div className="bk-field gold"><label>Personal Planning Guide</label>{sel('ppg_status', me.choices.ppg)}</div>
          <p className="faint">Record what actually happened — attending doesn't complete the paperwork by itself.</p></>}
        {cur === 'File Review 6 Block' && <>
          <div className="bk-field gold"><label>Family contacts collected</label>{sel('evo6_status', me.choices.evo6)}</div>
          {v.evo6_status && v.evo6_status !== 'complete' && <div className="bk-field gold"><label>Why not complete</label><input value={v.evo6_reason} onChange={e => set('evo6_reason', e.target.value)} placeholder="e.g. family will send names later" /></div>}
          <p className="faint">Six contacts is the goal, never a reason to invent one.</p></>}
        {cur === 'Notes' && <div className="bk-field gold"><label>Final notes</label><textarea value={v.final_notes} onChange={e => set('final_notes', e.target.value)} /></div>}
      </div>
      {!locked && <div className="bk-actions" style={{ marginTop: 12 }}>
        {step > 0 && <button className="bk-btn" onClick={() => setStep(step - 1)}>Back</button>}
        {step < steps.length - 1 && <button className="bk-btn" onClick={() => setStep(step + 1)}>Next</button>}
        <button className="bk-btn" onClick={() => save(false)}>Save draft</button>
        {v.pta_status === 'completed' && <button className="bk-btn" onClick={() => save(false, 'evo6')}><Icon name="network" />Save &amp; open File Review 6 Block</button>}
        <button className="bk-btn pri" onClick={() => save(true)}>Save &amp; close</button>
      </div>}
    </div>
  )
}

// Structured sale editor — server computes every total (s17).
export function SaleEditor({ appointmentId, leadId, onDone, onCancel }) {
  const { me, say } = useBk()
  const [key] = useState(newKey)
  const [lines, setLines] = useState([{ category: me.choices.categories[0], description: '', quantity: 1, unit_price: '', discount: '' }])
  const [h, setH] = useState({ status: 'closed', contract_ref: '', sale_date: today(), contract_discount: '', notes: '' })
  const [busy, setBusy] = useState(false)
  const preview = lines.reduce((s, l) => s + (Number(l.quantity) || 0) * (Number(String(l.unit_price).replace(/[,$]/g, '')) || 0) - (Number(String(l.discount).replace(/[,$]/g, '')) || 0), 0)
    - (Number(String(h.contract_discount).replace(/[,$]/g, '')) || 0)
  const setL = (i, k, v) => setLines(ls => ls.map((l, j) => (j === i ? { ...l, [k]: v } : l)))
  const save = async () => {
    setBusy(true)
    const r = await send(() => api.post(R + '/sales', { data: { ...h, idempotency_key: key, appointment_id: appointmentId || null,
      lead_id: leadId || null, lines: h.status === 'pending' && !lines.some(l => l.unit_price) ? [] : lines } }), say,
    h.status === 'closed' ? 'Sale recorded.' : 'Pending decision recorded.')
    setBusy(false)
    if (r) onDone(r)
  }
  return (
    <div className="bk-card">
      <h2><Icon name="dollar" width="18" />Record a sale</h2>
      <div className="bk-grid g3">
        <div className="bk-field"><label>Status</label><select value={h.status} onChange={e => setH({ ...h, status: e.target.value })}>
          <option value="closed">Closed sale</option><option value="pending">Pending decision</option></select></div>
        <div className="bk-field"><label>Contract #</label><input value={h.contract_ref} onChange={e => setH({ ...h, contract_ref: e.target.value })} /></div>
        <div className="bk-field"><label>Sale date</label><input type="date" value={h.sale_date} onChange={e => setH({ ...h, sale_date: e.target.value })} /></div>
      </div>
      {lines.map((l, i) => (
        <div key={i} className="bk-grid" style={{ gridTemplateColumns: '1.3fr 1.4fr .5fr .8fr .7fr auto', alignItems: 'end', gap: 8 }}>
          <div className="bk-field"><label>Category</label><select value={l.category} onChange={e => setL(i, 'category', e.target.value)}>{me.choices.categories.map(c => <option key={c}>{c}</option>)}</select></div>
          <div className="bk-field"><label>Product / service</label><input value={l.description} onChange={e => setL(i, 'description', e.target.value)} /></div>
          <div className="bk-field"><label>Qty</label><input inputMode="numeric" value={l.quantity} onChange={e => setL(i, 'quantity', e.target.value)} /></div>
          <div className="bk-field"><label>Unit price</label><input inputMode="decimal" value={l.unit_price} onChange={e => setL(i, 'unit_price', e.target.value)} placeholder="$" /></div>
          <div className="bk-field"><label>Discount</label><input inputMode="decimal" value={l.discount} onChange={e => setL(i, 'discount', e.target.value)} placeholder="$" /></div>
          <button className="bk-btn ghost" aria-label="Remove line" onClick={() => setLines(ls => ls.filter((_, j) => j !== i))} disabled={lines.length === 1}><Icon name="x" /></button>
        </div>))}
      <button className="bk-btn ghost" onClick={() => setLines([...lines, { category: me.choices.categories[0], description: '', quantity: 1, unit_price: '', discount: '' }])}><Icon name="plus" />Add a product</button>
      <div className="bk-grid g2" style={{ marginTop: 8 }}>
        <div className="bk-field"><label>Contract discount</label><input inputMode="decimal" value={h.contract_discount} onChange={e => setH({ ...h, contract_discount: e.target.value })} placeholder="$" /></div>
        <div className="bk-field"><label>Notes</label><input value={h.notes} onChange={e => setH({ ...h, notes: e.target.value })} /></div>
      </div>
      <p className="muted">Estimated net: <b className="money">{isFinite(preview) ? usd(Math.round(preview * 100)) : '—'}</b> <span className="faint">(the server calculates the final amounts)</span></p>
      <div className="bk-actions"><button className="bk-btn" onClick={onCancel}>Cancel</button>
        <button className="bk-btn pri" disabled={busy} onClick={save}>Save sale</button></div>
    </div>
  )
}

function Panel({ title, sub, onClose, children, foot }) {
  useEffect(() => {
    const k = e => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
  return (<>
    <div className="bk-scrim" onClick={onClose} />
    <aside className="bk-panel" role="dialog" aria-label={title}>
      <div className="ph"><div style={{ minWidth: 0 }}><h3>{title}</h3>{sub && <div className="faint">{sub}</div>}</div>
        <button className="bk-iconbtn" style={{ marginLeft: 'auto' }} onClick={onClose} aria-label="Close"><Icon name="x" /></button></div>
      <div className="pb">{children}</div>
      {foot && <div className="pf">{foot}</div>}
    </aside>
  </>)
}

// ── Follow-Ups ──────────────────────────────────────────────────────────────

const FU_VIEWS = [['priority', 'Priority'], ['unread', 'New replies'], ['due', 'Due now'], ['overdue', 'Overdue'], ['today', 'Today'],
  ['upcoming', 'Upcoming'], ['completed', 'Completed']]
const FU_PAGE = 10

export function FollowUps() {
  const { me } = useBk()
  const [w] = useData(R + '/work')
  const [st, set] = useStored('bk_fu', { view: 'priority', who: '', q: '', page: 0 })
  const [panel, setPanel] = useState(() => openParam())
  const bar = (
    <Toolbar>{me.me.manager && <select value={st.who} onChange={e => set({ who: e.target.value, page: 0 })} aria-label="Advisor"><option value="">Every advisor</option>{(me.team || []).map(n => <option key={n}>{n}</option>)}</select>}
      <input className="bk-famsearch" placeholder="Search families" value={st.q} onChange={e => set({ q: e.target.value, page: 0 })} aria-label="Search follow-ups" /></Toolbar>)
  if (!w) return <>{bar}<div className="bk-card"><Skeleton rows={8} /></div></>
  const t = today()
  const q = st.q.trim().toLowerCase()
  const base = w.follow_ups.filter(f => (!st.who || f.assigned === st.who)
    && (!q || [f.family, f.phone, f.email].some(x => (x || '').toLowerCase().includes(q))))
  const test = {
    priority: f => f.open, unread: f => f.open && f.new_reply, due: f => f.open && !!f.due_now && !isOverdue(f, t),
    overdue: f => isOverdue(f, t), today: f => f.open && f.follow_up_on === t,
    upcoming: f => f.open && !!f.follow_up_on && f.follow_up_on > t, completed: f => !f.open,
  }
  const counts = Object.fromEntries(Object.entries(test).map(([k, fn]) => [k, base.filter(fn).length]))
  let rows = base.filter(test[st.view] || test.priority)
  rows = st.view === 'completed' ? rows.sort((x, y) => String((y.activity || {}).at || '').localeCompare(String((x.activity || {}).at || '')))
    : st.view === 'upcoming' ? rows.sort((x, y) => String(x.follow_up_on).localeCompare(String(y.follow_up_on))) : byUrgency(rows, t)
  const pages = Math.max(1, Math.ceil(rows.length / FU_PAGE))
  const page = Math.min(st.page, pages - 1)
  return (
    <div>
      {bar}
      <div className="bk-sticky"><div className="bk-seg wrap">{FU_VIEWS.map(([k, l]) => <button key={k} className={st.view === k ? 'on' : ''} onClick={() => set({ view: k, page: 0 })}>{l}<span className="n">{num(counts[k])}</span></button>)}</div></div>
      <section className="bk-card"><h2>Follow-up queue<small>{num(rows.length)} {rows.length === 1 ? 'follow-up' : 'follow-ups'}</small></h2>
        {rows.length === 0 ? <p className="bk-line muted">No follow-ups match these filters.</p>
          : <FuTable rows={rows.slice(page * FU_PAGE, page * FU_PAGE + FU_PAGE)} t={t} showWho={me.me.manager} onOpen={setPanel} />}
        <Pager page={page} size={FU_PAGE} total={rows.length} set={n => set({ page: n })} /></section>
      {panel && <FollowUpPanel id={panel} onClose={() => setPanel(null)} />}
    </div>
  )
}

function FollowUpPanel({ id, onClose }) {
  const { me, say, refresh } = useBk()
  const [w] = useData(R + '/work')
  const f = w?.follow_ups.find(x => x.id === id)
  const [v, setV] = useState(null)
  const [next, setNext] = useState('')
  useEffect(() => {
    if (f && !v) setV({ fu_result: f.fu_result || '', fu_date: '', notes: f.notes || '', appt_date: '', appt_time: '', appt_how: f.appt_how || 'At Restland', appt_address: f.appt_address || '', done: f.done })
  }, [f])                                                    // eslint-disable-line react-hooks/exhaustive-deps
  // Opening it reads its new-reply alerts (the bell) - it does not complete the follow-up.
  useEffect(() => {
    if (me.me.view_only) return
    api.get(R + '/notifications?unread=true').then(r => {
      const ids = (r.notifications || []).filter(n => n.ref_type === 'follow_up' && n.ref_id === id).map(n => n.id)
      if (ids.length) api.post(R + '/notifications/read', { data: { ids } }).then(() => refresh()).catch(() => {})
    }).catch(() => {})
  }, [id])                                                   // eslint-disable-line react-hooks/exhaustive-deps
  if (!w) return <Panel title="Follow-up" onClose={onClose}><Skeleton /></Panel>
  if (!f) return <Panel title="Follow-up" onClose={onClose}><Empty>This follow-up isn't on your list.</Empty></Panel>
  if (!v) return null
  const r = v.fu_result
  const save = async () => {
    const data = { fu_result: v.fu_result, fu_date: v.fu_date || today(), notes: v.notes, done: v.done }
    if (r === 'Talked - booked appointment') {
      if (!v.appt_date || !v.appt_time) { say('Pick the appointment date and time.', true); return }
      Object.assign(data, { appt_date: v.appt_date, appt_time: v.appt_time, appt_how: v.appt_how, appt_address: v.appt_address })
    }
    if (r === 'Talked - call back later') {
      if (!next) { say('Pick the next follow-up date.', true); return }
      data.notes = ((v.notes || '') + `\nNext follow-up: ${next}`).trim()
    }
    if (r === 'Wrong number') data.notes = ((v.notes || '') + '\nWrong number - needs verification before any more outreach.').trim()
    const out = await send(() => api.patch(R + '/follow-ups/' + encodeURIComponent(id), { data }), say,
      r === 'Talked - booked appointment' ? 'Booking sent — the family gets one confirmation once it books.' : 'Saved.')
    if (out) { refresh(); onClose() }
  }
  return (
    <Panel title={f.family} sub={(f.why || '').split('\n')[0]} onClose={onClose}
      foot={<>{f.phone && <a className="bk-btn" href={'tel:' + f.phone}><Icon name="phone" />Call</a>}
        {f.phone && <a className="bk-btn" href={'sms:' + f.phone}><Icon name="text" />Text</a>}
        <button className="bk-btn pri" onClick={save}>Save</button></>}>
      <div className="bk-card" style={{ marginBottom: 12 }}>
        <dl className="bk-kv"><dt>Phone</dt><dd>{f.phone || '—'}</dd><dt>Email</dt><dd>{f.email || '—'}</dd>
          <dt>Why</dt><dd style={{ whiteSpace: 'pre-wrap' }}>{f.why}</dd><dt>They said</dt><dd>{f.said}</dd>
          <dt>Urgency</dt><dd><Urgency f={f} t={today()} /></dd>
          <dt>Follow up on</dt><dd>{f.follow_up_on ? fmtDate(f.follow_up_on) : 'Now'}</dd>
          <dt>Last activity</dt><dd>{f.activity ? ctStamp(f.activity.at) + ' CT · ' + f.activity.kind : 'No activity recorded'}</dd>
          <dt>Family's last message</dt><dd>{f.family_at ? ctStamp(f.family_at) + ' CT' : '—'}</dd>
          <dt>Last reply by a person</dt><dd>{f.replied_at ? ctStamp(f.replied_at) + ' CT' + (f.replied_by ? ' · by ' + f.replied_by : '') : 'None yet'}</dd>
          {f.fu_result && <><dt>Last attempt</dt><dd>{f.fu_result} {f.fu_date ? '· ' + f.fu_date : ''}</dd></>}</dl>
      </div>
      <div className="bk-card">
        <div className="bk-field gold"><label>What happened?</label><select value={r} onChange={e => setV({ ...v, fu_result: e.target.value })}>
          <option value="" />{me.choices.what_happened.map(o => <option key={o}>{o}</option>)}</select></div>
        {r === 'Talked - booked appointment' && <div className="bk-grid g2">
          <div className="bk-field gold"><label>Appointment date</label><input type="date" value={v.appt_date} min={today()} onChange={e => setV({ ...v, appt_date: e.target.value })} /></div>
          <div className="bk-field gold"><label>Time (CT)</label><select value={v.appt_time} onChange={e => setV({ ...v, appt_time: e.target.value })}><option value="" />{me.choices.times.map(t => <option key={t}>{t}</option>)}</select></div>
          <div className="bk-field gold"><label>How</label><select value={v.appt_how} onChange={e => setV({ ...v, appt_how: e.target.value })}>{me.choices.how.map(t => <option key={t}>{t}</option>)}</select></div>
          {v.appt_how === 'At Their Home' && <div className="bk-field gold"><label>Home address</label><input value={v.appt_address} onChange={e => setV({ ...v, appt_address: e.target.value })} /></div>}
          <p className="faint" style={{ gridColumn: '1 / -1' }}>The calendar is checked before it books; the family gets one confirmation.</p></div>}
        {r === 'Talked - call back later' && <div className="bk-field gold"><label>Next follow-up date</label><input type="date" min={today()} value={next} onChange={e => setNext(e.target.value)} /></div>}
        {['No answer', 'Left voicemail'].includes(r) && <p className="faint">Recorded as an attempt — not as a conversation.</p>}
        {r === 'Wrong number' && <p className="faint">Flagged for verification so no more outreach goes to this number until it's checked.</p>}
        <div className="bk-field gold"><label>Notes</label><textarea value={v.notes} onChange={e => setV({ ...v, notes: e.target.value })} /></div>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}><input type="checkbox" checked={!!v.done} onChange={e => setV({ ...v, done: e.target.checked })} /> Done — nothing more needed</label>
      </div>
    </Panel>
  )
}

// ── File Review 6 Block ─────────────────────────────────────────────────────────────

// File Review 6 Block (owner, Nov 26 SS6). "View network" only opens an
// existing network; "Start 6 Block" is the one action that creates one, shown
// only to a login that may write, for a completed review with no network yet.
// The read-only preview never creates anything. Repeated clicks reuse the same
// network (server: one per completed review, plus an idempotency key).
export function Evo6({ kind = 'evo6' }) {
  const { me, say, refresh } = useBk()
  const [d] = useData(R + '/networks/reviews')
  const [pick, setPick] = useState(() => openParam())
  const [busy, setBusy] = useState('')
  const [keys] = useState(() => ({}))
  const params = new URLSearchParams(window.location.search)
  const start = async (lead, appt) => {
    if (me.me.view_only || busy) return
    keys[appt] = keys[appt] || newKey()
    setBusy(appt)
    const r = await send(() => api.post(R + '/networks', { data: { idempotency_key: keys[appt], kind, origin_lead_id: lead, source_appointment_id: appt || null } }), say)
    setBusy('')
    if (r) { setPick(r.id); refresh() }
  }
  useEffect(() => { if (params.get('new') && !me.me.view_only) start(params.get('new'), params.get('appt')) }, [])   // eslint-disable-line react-hooks/exhaustive-deps
  if (pick) return <NetworkView id={pick} onBack={() => { setPick(null); refresh() }} />
  if (!d) return <div className="bk-card"><Skeleton rows={6} /></div>
  const T = d.totals
  return (
    <div>
      {d.view_only && <p className="bk-note">Read-only preview: you can open existing networks. Starting a 6 Block is turned off here.</p>}
      <div className="bk-metrics compact">
        <Kpi l="Completed reviews" v={num(T.reviews)} /><Kpi l="Family networks" v={num(T.networks)} />
        <Kpi l="Contacts collected" v={num(T.collected)} /><Kpi l="Eligible contacts" v={num(T.eligible)} s={num(T.booked) + ' booked'} />
      </div>
      <section className="bk-card"><h2>Completed file reviews<small>{num(d.reviews.length)}</small></h2>
        {d.reviews.length === 0 ? <Empty>Families appear here after their file review appointment is marked Showed.</Empty> :
          <div className="bk-scroll"><table className="bk-compact"><thead><tr><th>Family</th><th>Review (CT)</th>{me.me.manager && <th>Advisor</th>}
            <th>Permission to Access</th><th>Network</th><th /></tr></thead>
            <tbody>{d.reviews.map(r => <tr key={r.appointment_id}>
              <td data-l="Family"><b>{r.family}</b></td>
              <td data-l="Review">{fmtDate(r.date, { month: 'short', day: 'numeric' })} · {fmtTime(r.time)}</td>
              {me.me.manager && <td data-l="Advisor">{r.assigned || '—'}</td>}
              <td data-l="Permission">{label(r.pta)}</td>
              <td data-l="Network">{r.network ? <>{label(r.network.status)}<div className="sub">{num(r.network.collected)} contacts · {num(r.network.eligible)} eligible · {num(r.network.booked)} booked</div></> : <span className="muted">Not started</span>}</td>
              <td className="act">{r.network ? <button className="bk-btn sm" onClick={() => setPick(r.network.id)}>View network</button>
                : r.can_start ? <button className="bk-btn sm pri" disabled={busy === r.appointment_id} onClick={() => start(r.lead_id, r.appointment_id)}>{busy === r.appointment_id ? 'Starting…' : 'Start 6 Block'}</button>
                  : <span className="faint">{d.view_only ? 'Not started · preview' : 'Not started'}</span>}</td></tr>)}</tbody></table></div>}
      </section>
    </div>
  )
}

function NetworkView({ id, onBack }) {
  const { me, say, refresh } = useBk()
  const [b, , setB] = useData(R + '/networks/' + encodeURIComponent(id), [id])
  const [edit, setEdit] = useState(null)
  const [listMode, setListMode] = useState(false)
  if (!b) return <div className="bk-card"><Skeleton rows={6} /></div>
  const reload = () => api.get(R + '/networks/' + encodeURIComponent(id)).then(setB).catch(() => {})
  const p = b.progress
  return (
    <div className="bk-grid">
      <div className="bk-bar"><button className="bk-btn" onClick={onBack}>‹ All networks</button>
        <div className="bk-seg" style={{ marginLeft: 'auto' }}><button className={!listMode ? 'on' : ''} onClick={() => setListMode(false)}>Network</button><button className={listMode ? 'on' : ''} onClick={() => setListMode(true)}>List</button></div></div>
      <div className="bk-grid split">
        <div className="bk-card" style={{ background: 'linear-gradient(160deg,#FFFFFF,#F2FAF5)' }}>
          <h2>{b.origin_family || 'Family'}<small>{b.label} · {b.submitting_advisor}</small></h2>
          {listMode ? (
            <table className="bk-table"><thead><tr><th>Name</th><th>Relationship</th><th>Contact</th><th>Status</th><th>Campaign</th></tr></thead>
              <tbody>{b.contacts.map(c => <tr key={c.id} className="click" onClick={() => setEdit(c)}><td>{c.first_name} {c.last_name}</td><td>{c.relationship}</td>
                <td>{c.phone || c.email}</td><td>{c.eligible ? 'Eligible' : c.eligibility_reason}</td><td>{label(c.enrollment_state)}</td></tr>)}</tbody></table>
          ) : <Network batch={b} onPick={setEdit} onAdd={me.me.view_only ? undefined : () => setEdit({})} />}
          {me.me.view_only ? <p className="faint">Read-only preview: contacts can be viewed, not added or changed.</p>
            : <button className="bk-btn" onClick={() => setEdit({})}><Icon name="plus" />Add a family contact</button>}
        </div>
        <div className="bk-grid">
          <div className="bk-card"><h2>Progress</h2>
            <div className="bk-progress">{[['Collected', p.collected], ['Validated', p.validated], ['Eligible', p.eligible], ['Pending check', p.pending_verification],
              ['Enrolled', p.enrolled], ['Contacted', p.contacted], ['Booked', p.appointments_booked], ['Sales', p.sales_attributed]].map(([l, n]) =>
              <div key={l}><b>{n}</b><span>{l}</span></div>)}</div>
            <p className="faint">Status: {label(b.collection_status)}. Six is the goal — fewer is fine with a reason.</p>
            {b.collection_status !== 'complete' && <ExplainBatch b={b} onSaved={reload} />}
          </div>
          <div className="bk-card"><h2>Legend</h2>
            <div className="bk-actions"><span className="badge ok">● Eligible / enrolled</span><span className="badge gold">● Pending review</span>
              <span className="badge">○ Empty</span><span className="badge err">● Suppressed / invalid</span></div></div>
        </div>
      </div>
      {edit && <ContactPanel batch={b} c={edit} onClose={() => setEdit(null)} onSaved={() => { reload(); refresh() }} manager={me.me.manager} />}
    </div>
  )
}

function ExplainBatch({ b, onSaved }) {
  const { say } = useBk()
  const [t, setT] = useState(b.explanation || '')
  return (<div className="bk-field"><label>Why fewer than six (optional)</label>
    <div className="bk-actions"><input style={{ flex: 1 }} value={t} onChange={e => setT(e.target.value)} />
      <button className="bk-btn" onClick={async () => { if (await send(() => api.patch(R + '/networks/' + b.id, { data: { explanation: t } }), say, 'Saved.')) onSaved() }}>Save</button></div></div>)
}

function ContactPanel({ batch, c, onClose, onSaved, manager }) {
  const { say } = useBk()
  const [v, setV] = useState({ first_name: c.first_name || '', last_name: c.last_name || '', relationship: c.relationship || '', phone: c.phone || '',
    email: c.email || '', street: c.street || '', city: c.city || '', state: c.state || 'TX', zip: c.zip_code || '', notes: c.notes || '', outreach_authorized: !!c.outreach_authorized })
  const save = async () => {
    const path = R + '/networks/' + batch.id + '/contacts' + (c.id ? '/' + c.id : '')
    const r = await send(() => (c.id ? api.patch(path, { data: v }) : api.post(path, { data: v })), say, c.id ? 'Contact saved.' : 'Contact added.')
    if (r) { onSaved(); onClose() }
  }
  const enroll = async action => { if (await send(() => api.post(R + '/contacts/' + c.id + '/enrollment', { data: { action } }), say, 'Updated.')) { onSaved(); onClose() } }
  const F = (k, l, extra = {}) => <div className="bk-field"><label>{l}</label><input value={v[k]} onChange={e => setV({ ...v, [k]: e.target.value })} {...extra} /></div>
  return (
    <Panel title={c.id ? `${c.first_name || ''} ${c.last_name || ''}` : 'New family contact'} sub={c.id ? (c.eligible ? 'Eligible' : c.eligibility_reason) : batch.origin_family} onClose={onClose}
      foot={<>{c.id && c.eligible && c.enrollment_state === 'not_enrolled' && <button className="bk-btn" onClick={() => enroll('request')}>Request campaign</button>}
        {c.id && manager && c.enrollment_state === 'pending_approval' && <button className="bk-btn" onClick={() => enroll('approve')}>Approve enrollment</button>}
        {c.id && ['pending_approval', 'enrolled'].includes(c.enrollment_state) && <button className="bk-btn" onClick={() => enroll('stop')}>Stop</button>}
        <button className="bk-btn pri" onClick={save}>Save</button></>}>
      <div className="bk-grid g2">{F('first_name', 'First name')}{F('last_name', 'Last name')}{F('relationship', 'Relationship')}
        {F('phone', 'Phone', { inputMode: 'tel' })}{F('email', 'Email', { inputMode: 'email' })}{F('street', 'Street address')}
        {F('city', 'City')}{F('state', 'State')}{F('zip', 'ZIP', { inputMode: 'numeric' })}</div>
      <div className="bk-field"><label>Notes</label><textarea value={v.notes} onChange={e => setV({ ...v, notes: e.target.value })} /></div>
      <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}><input type="checkbox" checked={v.outreach_authorized} onChange={e => setV({ ...v, outreach_authorized: e.target.checked })} />
        <span>This person agreed to be contacted about Restland (documented). The Permission to Access form alone isn't permission to text or call.</span></label>
      {c.id && <p className="faint">Campaign: {label(c.enrollment_state)}. Nothing is sent until management approves and the message template is approved.</p>}
    </Panel>
  )
}

// ── At-Need 6 Blocks ───────────────────────────────────────────────────────────────

const ADD_FLOW = ['intake', 'review', 'contact', 'assist', 'resolved']

export function AddLeads() {
  const { me, say, refresh } = useBk()
  const [d] = useData(R + '/add-leads')
  const [open, setOpen] = useState(() => openParam())
  const [intake, setIntake] = useState(false)
  if (!d) return <div className="bk-card"><Skeleton rows={6} /></div>
  const cases = d.cases
  const count = s => cases.filter(c => c.status === s).length
  const sel = cases.find(c => c.id === open)
  return (
    <div className="bk-grid">
      <section className="bk-hero" style={{ background: 'linear-gradient(120deg,#FBF5E8,#FFFFFF)', borderColor: '#EFE3C6' }}>
        <h2>At-Need 6 Block Center</h2><p>Compassionate, timely family assistance.</p>
        <div className="chips">{[['intake', 'New'], ['review', 'Awaiting review'], ['contact', 'Contact attempted'], ['assist', 'In progress'], ['waiting_family', 'Waiting on family'], ['resolved', 'Resolved']].map(([s, l]) =>
          <span key={s} className="bk-chip"><b>{count(s)}</b> {l}</span>)}</div>
        <div style={{ marginTop: 12 }}><button className="bk-btn pri" onClick={() => setIntake(true)}><Icon name="plus" />New At-Need 6 Block</button></div>
      </section>
      <div className="bk-grid g2">{cases.filter(c => c.status !== 'resolved').map(c => (
        <div key={c.id} className="bk-card" style={{ cursor: 'pointer', borderColor: c.priority === 'urgent' ? '#E7D6AE' : undefined }} onClick={() => setOpen(c.id)}>
          <h2><Icon name="heart" width="16" />{c.deceased_name}<small>{fmtDate(c.reported_at)}</small></h2>
          <dl className="bk-kv"><dt>Family contact</dt><dd>{c.contact_name || '—'} {c.contact_relationship ? `(${c.contact_relationship})` : ''}</dd>
            <dt>Needs</dt><dd>{c.immediate_needs || '—'}</dd><dt>Best time</dt><dd>{c.preferred_time || '—'}</dd>
            {me.me.manager && <><dt>Advisor</dt><dd>{c.assigned_advisor || 'Not assigned'}</dd></>}</dl>
          <div className="bk-steps" style={{ marginTop: 10, marginBottom: 0 }}>{ADD_FLOW.map(s => <button key={s} className={c.status === s ? 'on' : ADD_FLOW.indexOf(s) < ADD_FLOW.indexOf(c.status) ? 'done' : ''} tabIndex="-1">{label(s)}</button>)}</div>
        </div>))}</div>
      {cases.length === 0 && <div className="bk-card"><Empty>No At-Need 6 Blocks yet.</Empty></div>}
      {intake && <AddIntake onClose={() => setIntake(false)} onSaved={() => { setIntake(false); refresh() }} />}
      {sel && <AddCasePanel c={sel} onClose={() => setOpen(null)} onSaved={() => refresh()} />}
    </div>
  )
}

function AddIntake({ onClose, onSaved }) {
  const { say, me } = useBk()
  const [key] = useState(newKey)
  const [v, setV] = useState({ deceased_name: '', date_of_passing: '', report_source: '', contact_name: '', contact_relationship: '', contact_phone: '',
    contact_email: '', preferred_channel: 'phone', preferred_time: '', immediate_needs: '', arrangement_status: '', priority: 'high', notes: '', assigned_advisor: '' })
  const F = (k, l, t = 'text') => <div className="bk-field"><label>{l}</label><input type={t} value={v[k]} onChange={e => setV({ ...v, [k]: e.target.value })} /></div>
  const save = async () => { if (await send(() => api.post(R + '/add-leads', { data: { ...v, idempotency_key: key, date_of_passing: v.date_of_passing || null } }), say, 'At-Need 6 Block created — the advisor is notified.')) onSaved() }
  return (
    <Panel title="New At-Need 6 Block" sub="Human-led. Nothing is sent to the family automatically." onClose={onClose} foot={<button className="bk-btn pri" onClick={save}>Create case</button>}>
      <div className="bk-grid g2">{F('deceased_name', 'Name of the person who passed')}{F('date_of_passing', 'Date of passing (if known)', 'date')}
        {F('contact_name', 'Family contact')}{F('contact_relationship', 'Relationship')}{F('contact_phone', 'Phone', 'tel')}{F('contact_email', 'Email', 'email')}
        {F('preferred_time', 'Best time to reach them')}{F('report_source', 'How we learned')}</div>
      <div className="bk-field"><label>Immediate needs</label><textarea value={v.immediate_needs} onChange={e => setV({ ...v, immediate_needs: e.target.value })} /></div>
      <div className="bk-grid g2"><div className="bk-field"><label>Priority</label><select value={v.priority} onChange={e => setV({ ...v, priority: e.target.value })}>{['normal', 'high', 'urgent'].map(p => <option key={p}>{p}</option>)}</select></div>
        {me.me.manager && <div className="bk-field"><label>Advisor</label><select value={v.assigned_advisor} onChange={e => setV({ ...v, assigned_advisor: e.target.value })}><option value="">Assign automatically</option>{(me.team || []).map(n => <option key={n}>{n}</option>)}</select></div>}</div>
    </Panel>
  )
}

function AddCasePanel({ c, onClose, onSaved }) {
  const { me, say, go } = useBk()
  const [v, setV] = useState({ status: c.status, notes: c.notes || '', resolution: c.resolution || '', immediate_needs: c.immediate_needs || '', assigned_advisor: c.assigned_advisor || '' })
  const [key] = useState(newKey)
  const save = async () => {
    const data = { ...v }
    if (!me.me.manager) delete data.assigned_advisor
    if (await send(() => api.patch(R + '/add-leads/' + c.id, { data }), say, 'Saved.')) { onSaved(); onClose() }
  }
  const network = async () => { const r = await send(() => api.post(R + '/networks', { data: { idempotency_key: key, kind: 'add', add_case_id: c.id } }), say); if (r) go('evo6?open=' + r.id) }
  return (
    <Panel title={c.deceased_name} sub={`Reported ${fmtDate(c.reported_at)} · ${label(c.priority)} priority`} onClose={onClose}
      foot={<>{c.contact_phone && <a className="bk-btn" href={'tel:' + c.contact_phone}><Icon name="phone" />Call {c.contact_name || ''}</a>}
        <button className="bk-btn" onClick={network}><Icon name="network" />Family network</button><button className="bk-btn pri" onClick={save}>Save</button></>}>
      <div className="bk-steps">{ADD_FLOW.map(s => <button key={s} className={v.status === s ? 'on' : ''} onClick={() => setV({ ...v, status: s })}>{label(s)}</button>)}</div>
      <div className="bk-card" style={{ marginBottom: 12 }}><dl className="bk-kv"><dt>Family contact</dt><dd>{c.contact_name} {c.contact_relationship ? `(${c.contact_relationship})` : ''}</dd>
        <dt>Phone</dt><dd>{c.contact_phone || '—'}</dd><dt>Email</dt><dd>{c.contact_email || '—'}</dd><dt>Best time</dt><dd>{c.preferred_time || '—'}</dd>
        <dt>Verification</dt><dd>{label(c.verification_status)}</dd></dl></div>
      <div className="bk-field"><label>Immediate needs</label><textarea value={v.immediate_needs} onChange={e => setV({ ...v, immediate_needs: e.target.value })} /></div>
      <div className="bk-field"><label>Notes</label><textarea value={v.notes} onChange={e => setV({ ...v, notes: e.target.value })} /></div>
      <div className="bk-field"><label>How the family was helped</label><textarea value={v.resolution} onChange={e => setV({ ...v, resolution: e.target.value })} /></div>
      {me.me.manager && <div className="bk-field"><label>Advisor</label><select value={v.assigned_advisor} onChange={e => setV({ ...v, assigned_advisor: e.target.value })}>{(me.team || []).map(n => <option key={n}>{n}</option>)}</select></div>}
      <p className="faint">Family care comes first — no sales appointment is required to help.</p>
    </Panel>
  )
}

// ── Families ────────────────────────────────────────────────────────────────

// My Families (owner's compact mockup): a searchable table, 25 a page, with a
// result count. Search and page are kept when you come back. Rows come from
// the caller's own work only (the server scopes /work); contact, advisor and
// follow-up columns show what this login is allowed to see.
const FAM_KEY = 'bk_families'
const FAM_PAGE = 10
export function Families() {
  const { me } = useBk()
  const [w] = useData(R + '/work')
  const [st, setSt] = useState(() => { try { return { q: '', page: 0, ...JSON.parse(sessionStorage.getItem(FAM_KEY) || '{}') } } catch { return { q: '', page: 0 } } })
  const set = patch => setSt(cur => { const n = { ...cur, ...patch }; try { sessionStorage.setItem(FAM_KEY, JSON.stringify(n)) } catch { /* ignore */ } return n })
  const [open, setOpen] = useState(() => openParam())
  const bar = <Toolbar><input className="bk-famsearch" placeholder="Search families" value={st.q} onChange={e => set({ q: e.target.value, page: 0 })} aria-label="Search families" /></Toolbar>
  if (!w) return <>{bar}<div className="bk-card"><Skeleton rows={6} /></div></>
  const t = today()
  const fam = new Map()
  for (const a of [...w.appointments, ...w.follow_ups]) {
    if (!a.lead_id) continue
    const f = fam.get(a.lead_id) || { lead_id: a.lead_id, family: a.family, phone: a.phone, email: a.email, assigned: a.assigned, open_fu: 0, next: null }
    if (a.open) f.open_fu += 1
    if (a.date && a.date >= t && /^(Booked|Confirmed)/.test(a.status || '') && (!f.next || a.date + a.time < f.next.date + f.next.time)) f.next = a
    if (!f.phone && a.phone) f.phone = a.phone
    if (!f.email && a.email) f.email = a.email
    fam.set(a.lead_id, f)
  }
  const q = st.q.trim().toLowerCase()
  const list = [...fam.values()].filter(f => !q || [f.family, f.phone, f.email].some(x => (x || '').toLowerCase().includes(q)))
    .sort((x, y) => String(x.family).localeCompare(String(y.family)))
  const pages = Math.max(1, Math.ceil(list.length / FAM_PAGE))
  const page = Math.min(st.page, pages - 1)
  const shown = list.slice(page * FAM_PAGE, page * FAM_PAGE + FAM_PAGE)
  return (
    <div>
      {bar}
      <section className="bk-card"><h2>Family directory<small>{num(list.length)} {list.length === 1 ? 'family' : 'families'}{q ? ' match' : ''}</small></h2>
        {list.length === 0 ? <p className="bk-line muted">No matching families.</p> : <>
          <div className="bk-scroll"><table className="bk-compact bk-fam"><thead><tr><th>Family</th><th className="sec">Phone</th><th className="sec">Email</th>
            {me.me.manager && <th className="sec">Advisor</th>}<th>Open follow-ups</th><th>Next appointment</th><th /></tr></thead>
            <tbody>{shown.map(f => <tr key={f.lead_id}>
              <td data-l="Family"><b>{f.family}</b></td><td className="sec" data-l="Phone">{f.phone || '—'}</td><td className="wrap sec" data-l="Email">{f.email || '—'}</td>
              {me.me.manager && <td className="sec" data-l="Advisor">{f.assigned || '—'}</td>}
              <td data-l="Follow-ups">{f.open_fu || '—'}</td>
              <td data-l="Next">{f.next ? <>{fmtDate(f.next.date, { month: 'short', day: 'numeric' })} · {fmtTime(f.next.time)} <VisitTag v={f.next.visit} /></> : '—'}</td>
              <td className="act"><button className="bk-btn sm" onClick={() => setOpen(f.lead_id)}>Open family</button></td></tr>)}</tbody></table></div>
          <Pager page={page} size={FAM_PAGE} total={list.length} set={n => set({ page: n })} /></>}
      </section>
      {open && <FamilyPanel id={open} onClose={() => setOpen(null)} />}
    </div>
  )
}

function FamilyPanel({ id, onClose }) {
  const { me, say } = useBk()
  const [d, , setD] = useData(R + '/families/' + encodeURIComponent(id), [id])
  const [tab, setTab] = useState('overview')
  const [booking, setBooking] = useState(false)
  if (!d) return <Panel title="Family" onClose={onClose}><Skeleton rows={6} /></Panel>
  const f = d.family
  const pw = async (k, val) => { if (await send(() => api.patch(R + '/families/' + id + '/paperwork', { data: { [k]: val } }), say, 'Saved.')) api.get(R + '/families/' + id).then(setD) }
  return (
    <Panel title={`${f.first_name || ''} ${f.last_name || ''}`} sub={[f.phone, f.email].filter(Boolean).join(' · ')} onClose={onClose}
      foot={!me.me.view_only && <button className="bk-btn pri" onClick={() => setBooking(true)}>Book appointment</button>}>
      {booking && <NewAppointment leadId={id} onClose={() => setBooking(false)} />}
      <div className="bk-seg" style={{ marginBottom: 12 }}>{['overview', 'appointments', 'follow-ups', 'paperwork', 'network', 'sales', 'activity'].map(t =>
        <button key={t} className={tab === t ? 'on' : ''} onClick={() => setTab(t)}>{label(t)}</button>)}</div>
      {tab === 'overview' && <div className="bk-card"><dl className="bk-kv"><dt>Address</dt><dd>{[f.street, f.city, f.state, f.zip].filter(Boolean).join(', ') || '—'}</dd>
        <dt>Came from</dt><dd>{label(f.source)}</dd><dt>Appointments</dt><dd>{d.appointments.length}</dd><dt>Follow-ups</dt><dd>{d.follow_ups.length}</dd>
        <dt>Permission to Access</dt><dd>{label(d.paperwork.pta_status)}</dd><dt>Planning Guide</dt><dd>{label(d.paperwork.ppg_status)}</dd>
        <dt>Texts / email</dt><dd>{f.allow_sms === false ? 'Texts off' : 'Texts on'} · {f.allow_email === false ? 'Email off' : 'Email on'}</dd></dl></div>}
      {tab === 'appointments' && <div className="bk-card">{d.appointments.length ? d.appointments.map(a => <div key={a.id} className="bk-row"><div className="ic"><Icon name="calendar" /></div><div><div className="t">{fmtDate(a.date)} · {fmtTime(a.time)}</div><div className="d">{a.how}</div></div><div className="r">{statusBadge(a.outcome || a.status)}</div></div>) : <Empty>None.</Empty>}</div>}
      {tab === 'follow-ups' && <div className="bk-card">{d.follow_ups.length ? d.follow_ups.map(x => <div key={x.id} className="bk-row"><div className="ic"><Icon name="repeat" /></div><div><div className="t">{(x.why || '').split('\n')[0]}</div><div className="d">{x.fu_result || x.said}</div></div><div className="r"><span className="badge">{x.status}</span></div></div>) : <Empty>None.</Empty>}</div>}
      {tab === 'paperwork' && <div className="bk-card">
        <div className="bk-field gold"><label>Permission to Access</label><select value={d.paperwork.pta_status} onChange={e => pw('pta_status', e.target.value)}>{me.choices.pta.map(o => <option key={o} value={o}>{label(o)}</option>)}</select></div>
        <div className="bk-field gold"><label>Personal Planning Guide</label><select value={d.paperwork.ppg_status} onChange={e => pw('ppg_status', e.target.value)}>{me.choices.ppg.map(o => <option key={o} value={o}>{label(o)}</option>)}</select></div>
        <p className="faint">Permission to Access is about who may see the family's file — not permission to send marketing.</p></div>}
      {tab === 'network' && <div className="bk-card">{d.evo6.length ? d.evo6.map(b => <div key={b.id}>{b.label}: {b.progress.collected} contacts · {label(b.collection_status)}</div>) : <Empty>No family network yet.</Empty>}
        {d.named_in.length > 0 && <p className="faint">Also named in {d.named_in.length} other family network(s).</p>}</div>}
      {tab === 'sales' && <div className="bk-card">{d.sales.length ? d.sales.map(s => <div key={s.id} className="bk-row"><div className="ic"><Icon name="dollar" /></div><div><div className="t">{usd(s.net_cents)}</div><div className="d">{label(s.acquisition_source)} · {s.contract_ref || ''}</div></div><div className="r"><span className="badge">{label(s.status)}</span></div></div>) : <Empty>No sales recorded.</Empty>}</div>}
      {tab === 'activity' && <div className="bk-card">{d.timeline.map((n, i) => <div key={i} className="bk-row" style={{ cursor: 'default' }}><div className="ic"><Icon name={n.by_person ? 'user' : 'spark'} /></div><div style={{ minWidth: 0 }}><div className="d" style={{ whiteSpace: 'normal' }}>{n.body}</div></div><div className="r faint">{fmtStamp(n.at)}</div></div>)}</div>}
    </Panel>
  )
}

// ── Service requests ────────────────────────────────────────────────────────

export function Service() {
  const { me, say, refresh } = useBk()
  const [d] = useData(R + '/service-requests')
  const [open, setOpen] = useState(() => openParam())
  const [add, setAdd] = useState(false)
  if (!d) return <div className="bk-card"><Skeleton rows={5} /></div>
  const sel = d.requests.find(r => r.id === open)
  return (
    <div className="bk-grid">
      <Toolbar><button className="bk-btn pri" onClick={() => setAdd(true)}><Icon name="plus" />New service request</button></Toolbar>
      <div className="bk-card">{d.requests.length === 0 ? <Empty>No service requests.</Empty> : <div className="bk-list">{d.requests.map(r => (
        <div key={r.id} className="bk-row" onClick={() => setOpen(r.id)} role="button" tabIndex="0"><div className={'ic' + (r.priority === 'urgent' ? ' err' : '')}><Icon name="wrench" /></div>
          <div><div className="t">{r.family || 'Family'}</div><div className="d">{r.description}</div></div>
          <div className="r"><span className={'badge' + (['resolved', 'closed'].includes(r.status) ? ' ok' : '')}>{label(r.status)}</span><span className="faint">{r.assigned_to}</span></div></div>))}</div>}</div>
      {(add || sel) && <ServicePanel r={sel} onClose={() => { setAdd(false); setOpen(null) }} onSaved={refresh} manager={me.me.manager} />}
    </div>
  )
}

function ServicePanel({ r, onClose, onSaved, manager }) {
  const { me, say } = useBk()
  const [v, setV] = useState({ family_name: r?.family || '', description: r?.description || '', priority: r?.priority || 'normal', status: r?.status || 'new',
    notes: r?.notes || '', resolution: r?.resolution || '', assigned_to: r?.assigned_to || '' })
  const save = async () => {
    const data = { ...v }
    if (!manager) delete data.assigned_to
    if (!r) delete data.status
    const out = await send(() => (r ? api.patch(R + '/service-requests/' + r.id, { data }) : api.post(R + '/service-requests', { data })), say, 'Saved.')
    if (out) { onSaved(); onClose() }
  }
  return (
    <Panel title={r ? 'Service request' : 'New service request'} onClose={onClose} foot={<button className="bk-btn pri" onClick={save}>Save</button>}>
      <div className="bk-field"><label>Family</label><input value={v.family_name} onChange={e => setV({ ...v, family_name: e.target.value })} /></div>
      <div className="bk-field"><label>What the family needs</label><textarea value={v.description} onChange={e => setV({ ...v, description: e.target.value })} /></div>
      <div className="bk-grid g2"><div className="bk-field"><label>Priority</label><select value={v.priority} onChange={e => setV({ ...v, priority: e.target.value })}>{['normal', 'high', 'urgent'].map(p => <option key={p}>{p}</option>)}</select></div>
        {r && <div className="bk-field"><label>Status</label><select value={v.status} onChange={e => setV({ ...v, status: e.target.value })}>{me.choices.service_status.map(s => <option key={s} value={s}>{label(s)}</option>)}</select></div>}
        {manager && <div className="bk-field"><label>Assigned to</label><select value={v.assigned_to} onChange={e => setV({ ...v, assigned_to: e.target.value })}><option value="">—</option>{(me.team || []).map(n => <option key={n}>{n}</option>)}</select></div>}</div>
      <div className="bk-field"><label>Notes</label><textarea value={v.notes} onChange={e => setV({ ...v, notes: e.target.value })} /></div>
      {r && <div className="bk-field"><label>Resolution</label><textarea value={v.resolution} onChange={e => setV({ ...v, resolution: e.target.value })} /></div>}
      <p className="faint">Family service never depends on a purchase.</p>
    </Panel>
  )
}

// ── Sales & Revenue ─────────────────────────────────────────────────────────

export function Sales() {
  const { me, say, refresh } = useBk()
  const [d] = useData(R + '/sales')
  const [perf] = useData(R + '/performance?range=this_month')
  const [open, setOpen] = useState(() => openParam())
  const [rec, setRec] = useState(false)
  if (!d || !perf) return <div className="bk-card"><Skeleton rows={6} /></div>
  const m = perf.metrics
  const sel = d.sales.find(s => s.id === open)
  return (
    <div className="bk-grid">
      <section className="bk-hero money"><div className="faint">{perf.range.label} · closed contract value (not cash collected)</div>
        <h2 className="money" style={{ fontSize: 34 }}>{usd(m.closed_value_cents)}</h2>
        <div className="chips"><span className="bk-chip"><b>{m.closed_contracts}</b> closed contracts</span><span className="bk-chip"><b>{usd(m.average_contract_cents)}</b> average</span>
          <span className="bk-chip"><b>{m.pending_opportunities}</b> pending decisions</span>
          {m.missing_sale_amounts > 0 && <span className="bk-chip warn"><b>{m.missing_sale_amounts}</b> sold without amounts</span>}</div>
        <div style={{ marginTop: 12 }}><button className="bk-btn pri" onClick={() => setRec(true)}><Icon name="plus" />Record a sale</button></div></section>
      <div className="bk-grid g2">
        <div className="bk-card"><h2>By source</h2><Bars data={perf.closed_value_by_source_cents} /></div>
        <div className="bk-card"><h2>By product category</h2><Bars data={perf.closed_value_by_category_cents} labels={{}} /></div>
      </div>
      <div className="bk-card"><h2>Sales<small>{d.sales.length}</small></h2>
        {d.sales.length === 0 ? <Empty>No sales recorded yet.</Empty> : <div className="bk-scroll"><table className="bk-table"><thead><tr><th>Date</th><th>Net</th><th>Status</th><th>Source</th><th>Closing advisor</th><th>Contract</th></tr></thead>
          <tbody>{d.sales.map(s => <tr key={s.id} className="click" onClick={() => setOpen(s.id)}><td>{fmtDate(s.sale_date)}</td><td className="money">{usd(s.net_cents)}</td>
            <td>{label(s.status)}{s.amount_missing ? ' · amount missing' : ''}</td><td>{label(s.acquisition_source)}</td><td>{s.closing_advisor}</td><td>{s.contract_ref}</td></tr>)}</tbody></table></div>}</div>
      {rec && <Panel title="Record a sale" onClose={() => setRec(false)}><PickAppointment onPick={id => setRec(id)} />{typeof rec === 'string' && <SaleEditor appointmentId={rec} onDone={() => { setRec(false); refresh() }} onCancel={() => setRec(false)} />}</Panel>}
      {sel && <SalePanel s={sel} onClose={() => setOpen(null)} onSaved={refresh} manager={me.me.manager} />}
    </div>
  )
}

function PickAppointment({ onPick }) {
  const [w] = useData(R + '/work')
  if (!w) return <Skeleton />
  const done = w.appointments.filter(a => (a.outcome || a.status) === 'Showed')
  return (<div className="bk-field"><label>Which appointment?</label><select defaultValue="" onChange={e => e.target.value && onPick(e.target.value)}>
    <option value="">Pick an attended appointment</option>{done.map(a => <option key={a.id} value={a.id}>{a.family} · {fmtDate(a.date)}</option>)}</select></div>)
}

function SalePanel({ s, onClose, onSaved, manager }) {
  const { say } = useBk()
  const [reason, setReason] = useState('')
  const [st, setSt] = useState(s.status)
  const save = async () => { if (await send(() => api.patch(R + '/sales/' + s.id, { data: { status: st, reason } }), say, 'Sale updated.')) { onSaved(); onClose() } }
  return (
    <Panel title={usd(s.net_cents, { cents: true })} sub={`${label(s.status)} · ${label(s.acquisition_source)}`} onClose={onClose}
      foot={manager && <button className="bk-btn pri" onClick={save}>Save correction</button>}>
      <div className="bk-card" style={{ marginBottom: 12 }}><dl className="bk-kv"><dt>Gross</dt><dd className="money">{usd(s.gross_cents, { cents: true })}</dd>
        <dt>Discounts</dt><dd className="money">{usd(s.discount_cents, { cents: true })}</dd><dt>Net</dt><dd className="money"><b>{usd(s.net_cents, { cents: true })}</b></dd>
        <dt>Contract</dt><dd>{s.contract_ref || '—'}</dd><dt>Closing advisor</dt><dd>{s.closing_advisor}</dd><dt>Referring advisor</dt><dd>{s.referring_advisor || '—'}</dd>
        <dt>Booking</dt><dd>{label(s.booking_source)}</dd><dt>Campaign</dt><dd>{s.campaign_key || '—'}</dd></dl></div>
      {manager ? <div className="bk-card"><h2>Correction (management)</h2>
        <div className="bk-field"><label>Status</label><select value={st} onChange={e => setSt(e.target.value)}>{['closed', 'pending', 'void'].map(x => <option key={x}>{x}</option>)}</select></div>
        <div className="bk-field"><label>Reason (required)</label><input value={reason} onChange={e => setReason(e.target.value)} /></div>
        <p className="faint">Every correction keeps the old value in the activity audit.</p></div>
        : <p className="faint">Closed sales are corrected by management.</p>}
    </Panel>
  )
}

// ── Performance ─────────────────────────────────────────────────────────────

const RANGES = [['today', 'Today'], ['yesterday', 'Yesterday'], ['this_week', 'This week'], ['last_week', 'Last week'], ['this_month', 'This month'],
  ['last_month', 'Last month'], ['quarter', 'Quarter'], ['ytd', 'Year to date'], ['lifetime', 'Lifetime']]

export function Performance({ person: fixed }) {
  const { me } = useBk()
  const [range, setRange] = useState('this_month')
  const [person, setPerson] = useState(fixed || '')
  const [d] = useData(R + '/performance?range=' + range + (person ? '&person=' + encodeURIComponent(person) : ''), [range, person])
  if (!d) return <div className="bk-card"><Skeleton rows={6} /></div>
  const m = d.metrics
  return (
    <div className="bk-grid">
      <Toolbar><select value={range} onChange={e => setRange(e.target.value)} aria-label="Date range">{RANGES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select>
        {me.me.manager && !fixed && <select value={person} onChange={e => setPerson(e.target.value)} aria-label="Advisor"><option value="">All advisors</option>{(me.team || []).map(n => <option key={n}>{n}</option>)}</select>}</Toolbar>
      <p className="faint" style={{ margin: '0 0 12px' }}>{d.range.cohorts}</p>
      <section className="bk-hero money"><div className="faint">{d.name} · {d.range.label}</div><h2 className="money">{usd(m.closed_value_cents)} closed</h2>
        <p>{m.closed_contracts} contracts · {usd(m.average_contract_cents)} average · {usd(m.closed_value_per_generated_appointment_cents)} per appointment</p></section>
      <div className="bk-grid g8">
        <Kpi l="Appointments" v={num(m.assigned_appointments)} /><Kpi l="Attended" v={num(m.attended)} /><Kpi l="No-shows" v={num(m.no_shows)} />
        <Kpi l="Show rate" v={pct(m.show_rate)} s="Showed ÷ (Showed + No-Show)" /><Kpi l="Appointments with sales" v={num(m.appointments_with_sales)} />
        <Kpi l="Closing rate" v={pct(m.closing_rate)} /><Kpi l="Follow-ups worked" v={num(m.follow_ups_worked)} /><Kpi l="Follow-up conversion" v={pct(m.follow_up_conversion)} />
        <Kpi l="Permission to Access done" v={num(m.pta_completed)} /><Kpi l="File Review 6 Blocks" v={num(m.evo6_submitted)} /><Kpi l="Contacts per review" v={m.average_contacts_per_review ?? 'N/A'} />
        <Kpi l="At-Need 6 Blocks" v={num(m.add_leads_assigned)} /></div>
      <div className="bk-grid g2">
        <div className="bk-card"><h2>Daily activity</h2>{d.daily.length ? <Bars money={false} data={Object.fromEntries(d.daily.map(x => [fmtDate(x.date), x.follow_ups_worked + x.appointments]))} labels={{}} /> : <Empty>No activity in this range.</Empty>}</div>
        <div className="bk-card"><h2>Production by source</h2><Bars data={d.closed_value_by_source_cents} /></div>
      </div>
    </div>
  )
}

// ── Command Center (management) ─────────────────────────────────────────────

export function Command() {
  const { me, go } = useBk()
  const [range, setRange] = useState('this_month')
  const [person, setPerson] = useState('')
  const [source, setSource] = useState('')
  const [drill, setDrill] = useState(null)
  const q = '?range=' + range + (person ? '&person=' + encodeURIComponent(person) : '') + (source ? '&source=' + source : '')
  const [d] = useData(R + '/command-center' + q, [range, person, source])
  if (drill) return <div><button className="bk-btn" onClick={() => setDrill(null)}>‹ Command Center</button><div style={{ height: 12 }} /><Performance person={drill} /></div>
  if (!d) return <div className="bk-card"><Skeleton rows={8} /></div>
  const k = d.kpis, h = d.hero
  return (
    <div className="bk-grid">
      <Toolbar>
        <select value={range} onChange={e => setRange(e.target.value)} aria-label="Date range">{RANGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
        <select value={person} onChange={e => setPerson(e.target.value)} aria-label="Advisor"><option value="">Every advisor</option>{(me.team || []).map(n => <option key={n}>{n}</option>)}</select>
        <select value={source} onChange={e => setSource(e.target.value)} aria-label="Source"><option value="">Every source</option>
          {['evosys_appointment', 'evosys_follow_up', 'evo6_block', 'add_lead', 'independent', 'unknown'].map(s => <option key={s} value={s}>{label(s)}</option>)}</select></Toolbar>
      <section className="bk-hero money">
        <div className="faint">TOTAL EVOSYS-GENERATED CLOSED CONTRACT VALUE · {d.range.label}</div>
        <h2 className="money" style={{ fontSize: 40 }}>{usd(h.evosys_closed_value_cents)}</h2>
        <div className="chips"><span className="bk-chip"><b>{h.closed_contracts}</b> closed contracts</span><span className="bk-chip"><b>{usd(h.average_contract_cents)}</b> average contract</span>
          <span className="bk-chip"><b>{usd(h.closed_value_per_generated_appointment_cents)}</b> per generated appointment</span></div>
        <div style={{ marginTop: 10 }}><AreaChart points={h.trend} /></div>
        <div className="faint">Contract value as recorded by advisors — not cash collected. {d.range.cohorts}.</div>
      </section>
      <div className="bk-grid g8">
        <Kpi l="Appointments generated" v={num(k.appointments_generated)} onClick={() => go('appointments')} /><Kpi l="Attended" v={num(k.attended)} />
        <Kpi l="Show rate" v={pct(k.show_rate)} /><Kpi l="Appointments with sales" v={num(k.appointments_with_sales)} />
        <Kpi l="Closing rate" v={pct(k.closing_rate)} /><Kpi l="Open follow-ups" v={num(k.open_follow_ups)} onClick={() => go('follow-ups')} />
        <Kpi l="Overdue follow-ups" v={num(k.overdue_follow_ups)} tone={k.overdue_follow_ups ? '#B7791F' : null} onClick={() => go('follow-ups')} />
        <Kpi l="File Review 6 Blocks" v={num(k.evo6_blocks)} onClick={() => go('evo6')} /><Kpi l="Family contacts" v={num(k.family_contacts)} />
        <Kpi l="At-Need 6 Blocks open" v={num(k.add_leads)} onClick={() => go('add-leads')} /><Kpi l="Priority cases" v={num(k.priority_cases)} />
        <Kpi l="Unassigned work" v={num(k.unassigned_work)} tone={k.unassigned_work ? '#B94B4B' : null} />
      </div>
      {k.missing_sale_amounts > 0 && <div className="bk-chip warn"><b>{k.missing_sale_amounts}</b> sales marked “Sold” without amounts — kept out of the totals above until amounts are recorded.</div>}
      <div className="bk-grid g2">
        <div className="bk-card"><h2>Production by source</h2><Bars data={d.production_by_source_cents} /></div>
        <div className="bk-card"><h2>Family network funnel</h2><div className="bk-funnel">{[['Contacts collected', 'collected'], ['Eligible', 'eligible'], ['Campaigns enrolled', 'enrolled'], ['Reached', 'reached'], ['Appointments booked', 'booked'], ['Closed sales', 'closed_sales']].map(([l, kk]) => {
          const max = d.network_funnel.collected || 1
          return <div className="st" key={kk}><span>{l}</span><div className="fill" style={{ width: Math.max(2, (d.network_funnel[kk] / max) * 100) + '%' }} /><b>{d.network_funnel[kk]}</b></div>
        })}</div></div>
      </div>
      <div className="bk-card"><h2>Team performance<small>select an advisor to drill in</small></h2><div className="bk-scroll"><table className="bk-table">
        <thead><tr><th>Advisor</th><th>Upcoming</th><th>Today</th><th>Showed</th><th>Show rate</th><th>Open FU</th><th>Due now</th><th>Worked 7d</th><th>File Review 6</th><th>At-Need 6</th><th>Contracts</th><th>Closed value</th><th>Last update</th></tr></thead>
        <tbody>{d.team.map(p => <tr key={p.name} className="click" onClick={() => p.name !== 'Not assigned' && setDrill(p.name)}><td><b>{p.name}</b></td><td>{p.appointments_upcoming}</td><td>{p.appointments_today}</td>
          <td>{p.showed}</td><td>{pct(p.show_rate)}</td><td>{p.follow_ups_open}</td><td>{p.follow_ups_due_now}</td><td>{p.followed_up_7d}</td><td>{p.evo6_blocks}</td><td>{p.add_leads}</td>
          <td>{p.closed_contracts}</td><td className="money">{usd(p.closed_value_cents)}</td><td className="faint">{fmtStamp(p.last_update)}</td></tr>)}</tbody></table></div></div>
      <div className="bk-card"><h2>Recent closed sales</h2>{d.recent.length ? d.recent.map(r => <div key={r.id} className="bk-row" onClick={() => go('sales?open=' + r.id)}><div className="ic"><Icon name="dollar" /></div><div><div className="t money">{usd(r.net_cents)}</div><div className="d">{r.advisor}</div></div><div className="r faint">{fmtDate(r.date)}</div></div>) : <Empty>No closed sales in this range yet.</Empty>}</div>
    </div>
  )
}

// ── management pages ────────────────────────────────────────────────────────

// Team (owner's preview): a card per advisor with their booked appointments and
// open follow-ups; "View advisor" shows that person's work. Who is in the
// rotation still comes from the sheet's Team tab; access is paused here.
export function Advisors() {
  const { say, refresh } = useBk()
  const [d] = useData(R + '/team')
  const [w] = useData(R + '/work')
  const [pick, setPick] = useState(null)
  const [panel, setPanel] = useState(null)
  if (!d || !w) return <div className="bk-card"><Skeleton rows={5} /></div>
  const t = today()
  const work = name => ({
    appts: w.appointments.filter(a => a.assigned === name && a.date >= t && /^(Booked|Confirmed)/.test(a.status)),
    fus: w.follow_ups.filter(f => f.assigned === name && f.open),
  })
  const inRot = m => !m.held && /^In the rotation/i.test(m.status || '')
  const rotate = async (m) => {
    const out = inRot(m)
    if (out && !window.confirm('Take ' + m.name + ' out of the rotation? They keep everything already assigned; new families go to the others.')) return
    if (await send(() => api.patch(R + '/team/' + m.id + '/rotation', { data: { in_rotation: !out } }), say,
      out ? m.name + ' is out of the rotation.' : m.name + ' is back in the rotation.')) refresh()
  }
  const toggle = async (m) => { if (await send(() => api.patch(R + '/team/' + m.id, { data: { access_suspended: !m.access_suspended } }), say, 'Updated.')) refresh() }
  const panels = <>{panel?.type === 'appt' && <AppointmentPanel id={panel.id} startMode={panel.mode} onClose={() => setPanel(null)} />}
    {panel?.type === 'fu' && <FollowUpPanel id={panel.id} onClose={() => setPanel(null)} />}</>
  if (pick) {
    const m = d.team.find(x => x.id === pick), x = work(m.name)
    return (
      <div>
        <div className="bk-hero"><div className="bk-line flat"><div><h2>{m.name}</h2><p><Pill>{inRot(m) ? 'In rotation' : 'Not in rotation'}</Pill></p></div>
          <button className="bk-btn" onClick={() => setPick(null)}>Back to team</button></div></div>
        <div className="bk-metrics"><Kpi l="Booked" v={num(x.appts.length)} s="Upcoming" /><Kpi l="Open follow-ups" v={num(x.fus.length)} /></div>
        <div className="bk-split">
          <section className="bk-card"><h2>Assigned appointments</h2>{x.appts.length === 0 ? <p className="bk-line muted">None upcoming.</p>
            : <><ApptTable rows={x.appts.slice(0, 10)} onOpen={(id, mode) => setPanel({ type: 'appt', id, mode })} />
              {x.appts.length > 10 && <p className="faint">Showing the next 10 of {num(x.appts.length)}.</p>}</>}</section>
          <section className="bk-card"><h2>Assigned follow-ups</h2>{x.fus.length === 0 ? <p className="bk-line muted">No open follow-ups.</p>
            : <><FuTable rows={byUrgency(x.fus, t).slice(0, 10)} t={t} onOpen={id => setPanel({ type: 'fu', id })} />
              {x.fus.length > 10 && <p className="faint">Showing the 10 most urgent of {num(x.fus.length)}.</p>}</>}</section>
        </div>
        {panels}
      </div>
    )
  }
  const active = d.team.filter(inRot).length
  return (
    <div>
      <div className="bk-metrics"><Kpi l="Advisors" v={num(d.team.length)} /><Kpi l="In rotation" v={num(active)} /><Kpi l="Not in rotation" v={num(d.team.length - active)} /></div>
      <div className="bk-team">{d.team.map(m => {
        const x = work(m.name)
        return (
          <section key={m.id} className="bk-card">
            <div className="bk-line flat"><h2 style={{ margin: 0 }}>{m.name}</h2><Pill>{inRot(m) ? 'In rotation' : 'Not in rotation'}</Pill></div>
            <p className="bk-line"><span><strong>{num(x.appts.length)}</strong> booked · <strong>{num(x.fus.length)}</strong> open follow-ups</span></p>
            <div className="bk-actions"><button className="bk-btn pri" onClick={() => setPick(m.id)}>View advisor</button>
              <button className="bk-btn" onClick={() => rotate(m)}>{inRot(m) ? 'Take out of rotation' : 'Put in rotation'}</button>
              <button className="bk-btn" onClick={() => toggle(m)}>{m.access_suspended ? 'Restore access' : 'Pause access'}</button></div>
          </section>)
      })}</div>
      <p className="faint" style={{ marginTop: 12 }}>The rotation follows the sheet's Team tab (Active?) and these buttons; changes apply on the board's next pass, within about two minutes. Pausing access keeps all of a person's history.</p>
    </div>
  )
}

export function Team() {
  const { say, refresh } = useBk()
  const [d, , setD] = useData(R + '/team')
  const [link, setLink] = useState(null)
  if (!d) return <div className="bk-card"><Skeleton rows={5} /></div>
  const reload = () => api.get(R + '/team').then(setD)
  const toggle = async (m) => { if (await send(() => api.patch(R + '/team/' + m.id, { data: { access_suspended: !m.access_suspended } }), say, 'Updated.')) refresh() }
  const make = async (m) => {
    if (!m.email) { say('Add an email for ' + m.name + ' on the Team tab first.', true); return }
    if (!window.confirm('Create a BookIT login for ' + m.name + ' (' + m.email + ')? Nothing is emailed yet.')) return
    if (await send(() => api.post(R + '/team/' + m.id + '/login', { data: {} }), say, 'Login created for ' + m.name + '.')) reload()
  }
  const setup = async (m, email) => {
    if (email && !window.confirm('Email ' + m.name + ' a link to set their password (' + (m.login_email || m.email) + ')?')) return
    const r = await send(() => api.post(R + '/team/' + m.id + '/setup-link', { data: { email } }), say)
    if (r) { if (r.emailed) say('Emailed ' + r.to + '.'); else setLink({ name: m.name, url: r.setup_url, failed: email }) }
  }
  return (
    <div className="bk-grid">
      <div className="bk-card"><h2>Advisors<small>{d.live_for_team ? 'BookIT is open to the team' : 'Logins can be made now - nobody gets in until you open BookIT to the team'}</small></h2>
        <div className="bk-scroll"><table className="bk-table"><thead><tr><th>Name</th><th>Email</th><th>Rotation</th><th>Login</th><th>BookIT access</th><th /></tr></thead>
          <tbody>{d.team.map(m => <tr key={m.id}><td><b>{m.name}</b></td><td>{m.login_email || m.email || '—'}</td><td>{m.status}</td>
            <td>{!m.has_login ? <button className="bk-btn pri" onClick={() => make(m)}>Create login</button>
              : <div className="bk-actions"><span>{m.signed_in ? 'Signed in' : 'Made - not signed in yet'}</span>
                  {!m.signed_in && <><button className="bk-btn" onClick={() => setup(m, true)}>Email setup link</button>
                    <button className="bk-btn ghost" onClick={() => setup(m, false)}>Get link</button></>}</div>}</td>
            <td>{m.access_suspended ? <span className="badge err">Paused</span> : <span className="badge ok">Allowed</span>}</td>
            <td><button className="bk-btn" onClick={() => toggle(m)}>{m.access_suspended ? 'Restore access' : 'Pause access'}</button></td></tr>)}</tbody></table></div>
        {link && <div className="bk-alert" style={{ marginTop: 12 }}>{link.failed ? 'The email didn\u2019t go out. ' : ''}One-time link for <b>{link.name}</b> (works once, 3 days) - send it to them yourself:
          <div style={{ wordBreak: 'break-all', marginTop: 6 }}><code>{link.url}</code></div>
          <button className="bk-btn" style={{ marginTop: 8 }} onClick={() => { navigator.clipboard?.writeText(link.url); say('Copied.') }}>Copy link</button></div>}
        <p className="faint">Names, emails and who's in the rotation come from the sheet's Team tab. "Create login" uses the email shown; it sends nothing. "Email setup link" sends them a one-time link to set their own password. Pausing access keeps all of a person's history.</p></div>
    </div>
  )
}

export function Audit() {
  const [d] = useData(R + '/audit')
  if (!d) return <div className="bk-card"><Skeleton rows={6} /></div>
  return (<div className="bk-card"><h2>Activity audit<small>latest {d.audit.length}</small></h2>{d.audit.length === 0 ? <Empty>Nothing yet.</Empty> :
    <div className="bk-scroll"><table className="bk-table"><thead><tr><th>When</th><th>Who</th><th>What</th><th>Field</th><th>Before</th><th>After</th><th>Reason</th></tr></thead>
      <tbody>{d.audit.map((a, i) => <tr key={i}><td>{fmtStamp(a.at)}</td><td>{a.by}</td><td>{label(a.entity)} · {a.action}</td><td>{a.field}</td><td>{a.old}</td><td>{a.new}</td><td>{a.reason}</td></tr>)}</tbody></table></div>}</div>)
}

export function Settings() {
  const [d] = useData(R + '/settings')
  if (!d) return <div className="bk-card"><Skeleton /></div>
  return (<div className="bk-grid"><div className="bk-card"><h2>BookIT status</h2>
    <p>{d.live_for_team ? 'Open to the team — each advisor sees only their own work.' : 'Owner preview — only your logins can open BookIT. Opening it to the team is your call.'}</p>
    <OpenToTeam live={d.live_for_team} /></div>
    <div className="bk-card"><h2>Campaign templates</h2>
      <p>File Review 6 Block: <b>{d.campaigns?.evo6?.approved ? 'approved' : 'not approved yet'}</b> · At-Need 6 Block: <b>{d.campaigns?.add?.approved ? 'approved' : 'not approved yet'}</b></p>
      <p className="faint">No contact is enrolled until the matching template is approved. Nothing has been sent from BookIT.</p></div>
    <PreviewAccess /><TestLogin /><CoachSettings /></div>)
}

function OpenToTeam({ live }) {
  const { say, refresh } = useBk()
  const [pa] = useData(R + '/preview-access')
  if (!pa) return null                                    // owner logins only
  const flip = async () => {
    if (!window.confirm(live ? 'Close BookIT to the team? Only your logins will get in.' : 'Open BookIT to the team? Each advisor with a login can sign in to their own side.')) return
    if (await send(() => api.put(R + '/open-to-team', { data: { live: !live } }), say, live ? 'Closed to the team.' : 'Open to the team.')) refresh()
  }
  return <div className="bk-actions"><button className={'bk-btn' + (live ? '' : ' pri')} onClick={flip}>{live ? 'Close to the team' : 'Open BookIT to the team'}</button></div>
}

// Who may open BookIT before it is opened to the team (owner, Nov 27: his
// co-CEO gets the owner/manager side). Only the owner's logins see this.
function PreviewAccess() {
  const { say } = useBk()
  const [d, , setD] = useData(R + '/preview-access')
  const [add, setAdd] = useState('')
  if (!d) return null
  const extra = d.emails.filter(e => !d.fixed.map(x => x.toLowerCase()).includes(e))
  const save = async list => { const r = await send(() => api.put(R + '/preview-access', { data: { emails: list } }), say, 'Saved.'); if (r) { setD({ ...d, emails: r.emails }); setAdd('') } }
  return (<div className="bk-card"><h2>Who can open BookIT now</h2>
    <p className="faint">{d.live ? 'BookIT is open to the team.' : 'Before BookIT is opened to the team, only these logins get in (with the management side, if they manage Restland).'}</p>
    <ul>{d.fixed.map(e => <li key={e}>{e} <span className="faint">· owner</span></li>)}
      {extra.map(e => <li key={e}>{e} <button className="bk-btn sm" onClick={() => save(extra.filter(x => x !== e))}>Remove</button></li>)}</ul>
    <div className="bk-actions"><input placeholder="Add a login email" value={add} onChange={e => setAdd(e.target.value)} />
      <button className="bk-btn" disabled={!add.trim()} onClick={() => save([...extra, add.trim()])}>Add</button></div></div>)
}

function TestLogin() {
  const { me, say } = useBk()
  const [d, , setD] = useData(R + '/test-login')
  const [email, setEmail] = useState('')
  const [who, setWho] = useState('')
  useEffect(() => { if (d) { setEmail(d.email || 'simmonsmj242+bookittest@gmail.com'); setWho(d.mirrors || (me.team || [])[0] || '') } }, [d])
  if (!d) return <div className="bk-card"><Skeleton rows={3} /></div>
  const save = async (sendLink) => {
    const r = await send(() => api.put(R + '/test-login', { data: { email, mirrors: who, send_link: sendLink } }), say,
      sendLink ? 'Saved. Check ' + email + ' for the set-password link.' : 'Saved.')
    if (r) { setD(r); if (r.link && !r.link.emailed) say('The email didn\u2019t go out - use this link: ' + r.link.setup_url, true) }
  }
  return (
    <div className="bk-card"><h2>Test login<small>{d.email ? (d.has_signed_in ? 'Signed in before' : 'Not signed in yet') : 'Not set up'}</small></h2>
      <p>A separate login that shows BookIT exactly as an advisor sees it, view only. It works now, before BookIT is opened to the team, and it's never in the rotation.</p>
      <div className="bk-line"><span>Login email</span><input value={email} onChange={e => setEmail(e.target.value)} style={{ width: 290 }} aria-label="Test login email" /></div>
      <div className="bk-line"><span>Show me the side of</span><select value={who} onChange={e => setWho(e.target.value)} aria-label="Advisor to show">
        {(me.team || []).map(n => <option key={n}>{n}</option>)}</select></div>
      <div className="bk-line"><span className="muted">Switch the advisor any time; the login stays the same.</span>
        <div className="bk-actions"><button className="bk-btn" onClick={() => save(false)}>Save</button>
          {!d.has_signed_in && <button className="bk-btn pri" onClick={() => save(true)}>Save and email me the link</button>}</div></div>
    </div>
  )
}

function CoachSettings() {
  const { say } = useBk()
  const [d, , setD] = useData(R + '/coach/usage')
  const [v, setV] = useState(null)
  useEffect(() => { if (d && !v) setV(d.settings) }, [d, v])
  if (!d || !v) return <div className="bk-card"><Skeleton rows={3} /></div>
  const save = async () => {
    if (await send(() => api.patch(R + '/coach/settings', { data: v }), say, 'FSA Coach limits saved.')) api.get(R + '/coach/usage').then(setD)
  }
  const money = n => '$' + Number(n || 0).toFixed(n < 1 ? 3 : 2)
  return (
    <div className="bk-card"><h2>FSA Coach<small>{v.enabled ? 'On' : 'Off'}</small></h2>
      <div className="bk-line"><label><input type="checkbox" checked={!!v.enabled} onChange={e => setV({ ...v, enabled: e.target.checked })} /> FSA Coach is on</label></div>
      <div className="bk-line"><span>Questions per person per day</span><input type="number" min="0" max="500" value={v.per_person_day} onChange={e => setV({ ...v, per_person_day: e.target.value })} style={{ width: 90 }} /></div>
      <div className="bk-line"><span>Questions for the whole team per day</span><input type="number" min="0" max="5000" value={v.team_day} onChange={e => setV({ ...v, team_day: e.target.value })} style={{ width: 90 }} /></div>
      <div className="bk-line"><span className="muted">Resets at midnight Central.</span><button className="bk-btn pri" onClick={save}>Save limits</button></div>
      <h2 style={{ marginTop: 14 }}>Usage this month<small>{d.total.today} today · {d.total.month} this month · about {money(d.total.cost_month)}</small></h2>
      {d.people.length === 0 ? <p className="bk-line muted">Nobody has asked FSA Coach anything yet.</p> : d.people.map(p => (
        <div key={p.name} className="bk-line"><strong>{p.name}</strong><span className="muted">{p.today} today · {p.month} this month · about {money(p.cost_month)}</span></div>))}
      <p className="faint" style={{ marginTop: 10 }}>Cost is an estimate from {d.price.model} list prices (${d.price.in_per_million} per million tokens read, ${d.price.out_per_million} per million tokens written; a token is about three-quarters of a word). The question and answer text are not stored — only the count.</p>
    </div>
  )
}

export function Notifications({ onRead }) {
  const { say, refresh, go } = useBk()
  const [d] = useData(R + '/notifications')
  if (!d) return <div className="bk-card"><Skeleton rows={5} /></div>
  const unread = d.notifications.filter(n => !n.read).map(n => n.id)
  const read = async () => { if (await send(() => api.post(R + '/notifications/read', { data: { ids: unread } }), say)) { onRead(); refresh() } }
  const where = { follow_up: 'follow-ups', appointment: 'appointments', add_case: 'add-leads', referral_contact: 'evo6', service_request: 'service', sale: 'sales' }
  return (<div className="bk-card"><h2>Notifications<small>{unread.length > 0 && <button className="bk-btn ghost" onClick={read}>Mark all read</button>}</small></h2>
    {d.notifications.length === 0 ? <Empty>No notifications.</Empty> : <div className="bk-list">{d.notifications.map(n => (
      <div key={n.id} className="bk-row" onClick={() => where[n.ref_type] && go(where[n.ref_type] + '?open=' + n.ref_id)}><div className={'ic' + (n.read ? '' : ' warn')}><Icon name="bell" /></div>
        <div><div className="t">{n.title}</div><div className="d">{n.body}</div></div><div className="r"><span className="faint">{fmtStamp(n.at)}</span>{!n.read && <span className="badge gold">New</span>}{n.for === 'management' && <span className="badge">Management</span>}</div></div>))}</div>}</div>)
}

export function Profile() {
  const { me } = useBk()
  return (<div className="bk-card" style={{ maxWidth: 560 }}><h2>My profile</h2><dl className="bk-kv"><dt>Name</dt><dd>{me.me.name}</dd><dt>Access</dt><dd>{me.me.manager ? 'Management' : 'Advisor — your own families only'}</dd></dl>
    <p className="faint">Your name, phone and email come from the Restland team list. Ask your manager to change them.</p></div>)
}

// ── FSA Coach — AI answers from this login's own BookIT records ──────────────
// The server decides what it may read (only this person's families) and how
// many questions are left today; it drafts, it never sends or books.

const QUESTIONS = ['Who should I call first today?', 'Which appointments still need an outcome?',
  'Who said yes and then went quiet?', 'Plan my day', 'Help me answer a family who said "my kids handle that"']

export function Coach({ onClose }) {
  const [log, setLog] = useState([{ who: 'bot', text: 'I\u2019m your FSA Coach. I read only your own BookIT families and help you plan, prep and find the words. I never send, book or change anything \u2014 you do. Ask about a family by name.' }])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const [left, setLeft] = useState(null)
  useEffect(() => { api.get(R + '/coach').then(r => setLeft(r.left_today)).catch(() => {}) }, [])
  const ask = async (text) => {
    if (!text.trim() || busy) return
    const history = log.slice(1).map(m => ({ role: m.who === 'me' ? 'user' : 'assistant', content: m.text }))
    setLog(l => [...l, { who: 'me', text }]); setQ(''); setBusy(true)
    try {
      const r = await api.post(R + '/coach', { data: { question: text, history } })
      setLog(l => [...l, { who: 'bot', text: r.answer }]); setLeft(r.left_today)
    } catch (e) {
      setLog(l => [...l, { who: 'bot', text: e.message || 'FSA Coach couldn\u2019t answer just now. Please try again.' }])
    } finally { setBusy(false) }
  }
  return (
    <div className="bk-copilot" role="dialog" aria-label="FSA Coach">
      <div className="ch"><Icon name="spark" width="18" /><b>FSA Coach</b>
        {left !== null && <span className="faint" style={{ fontSize: 12 }}>{left} question{left === 1 ? '' : 's'} left today</span>}
        <button className="bk-iconbtn" style={{ marginLeft: 'auto' }} onClick={onClose} aria-label="Close"><Icon name="x" /></button></div>
      <div className="cb">{log.map((m, i) => <div key={i} className={'bk-msg ' + (m.who === 'me' ? 'out' : 'in')}>{m.text}</div>)}
        {busy && <div className="bk-msg in faint">Thinking…</div>}
        {log.length === 1 && <div className="bk-actions">{QUESTIONS.map(x => <button key={x} className="bk-chip btn" onClick={() => ask(x)}>{x}</button>)}</div>}</div>
      <div className="cf"><input value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && ask(q)} placeholder="Ask about your families or your day" aria-label="Ask FSA Coach" disabled={busy} />
        <button className="bk-btn pri" onClick={() => ask(q)} disabled={busy || !q.trim()}>Ask</button></div>
    </div>
  )
}
