/* SCI Command - the phone command center, built to Mike's two-phone mockup
   (Oct 14). Families needing attention first, statistics second, every
   control working:
     Home: location filter, search, four tiles, compact owner alert (owner
           only), Priority inbox with per-family actions, today's actions.
     Family: Call / Reply / Schedule Visit / Add Note, the conversation,
           family context with Assign, suggested next step, quick schedule
           with two REAL calendar times, Note / Text / Email composer.
     Tabs: Home, Inbox, Search, Appointments.
   Reads /program/command (+ /family/{id}, /slots) and writes through the
   existing endpoints: /sms/send, /email/send/{id}, /work/leads/{id}/notes,
   /program/responses/{id}/mark, /program/command/schedule, /assign.
   Route: /program/command */
import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../../../api/client'
import './Command.css'

const REFRESH_MS = 45000
const time = iso => (iso ? new Date(iso).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) : '')
const when = iso => {
  if (!iso) return ''
  const d = new Date(iso)
  return new Date().toDateString() === d.toDateString() ? time(iso)
    : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + time(iso)
}
const ago = iso => {
  if (!iso) return ''
  const m = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  return m < 2 ? 'just now' : m < 60 ? `${m} min ago` : `at ${time(iso)}`
}
const dayLabel = iso => {
  const d = new Date(iso), t = new Date()
  const tm = new Date(t.getFullYear(), t.getMonth(), t.getDate() + 1)
  if (d.toDateString() === t.toDateString()) return 'Today'
  if (d.toDateString() === tm.toDateString()) return 'Tomorrow'
  return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })
}
const fmtPhone = p => {
  const d = (p || '').replace(/\D/g, '').replace(/^1(?=\d{10}$)/, '')
  return d.length === 10 ? `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}` : (p || '')
}
const BADGE = { New: 'new', Hot: 'new', Review: 'review', Active: 'active', Low: 'low', Booked: 'active' }

const I = {
  reply: <path d="M4 5h16v11H8l-4 4z" />,
  call: <path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2z" />,
  cal: <><rect x="4" y="5" width="16" height="15" rx="2" /><path d="M4 10h16M9 3v4M15 3v4" /></>,
  note: <><path d="M6 3h9l4 4v14H6z" /><path d="M9 12h7M9 16h5" /></>,
  check: <path d="M5 12l4 4 10-10" />,
  clock: <><circle cx="12" cy="12" r="8" /><path d="M12 8v5l3 2" /></>,
  x: <><circle cx="12" cy="12" r="8" /><path d="M9 9l6 6M15 9l-6 6" /></>,
  home: <path d="M4 11l8-7 8 7v9h-5v-6H9v6H4z" />,
  inbox: <path d="M4 5h16v11H8l-4 4z" />,
  search: <><circle cx="11" cy="11" r="6" /><path d="M20 20l-4.5-4.5" /></>,
  back: <path d="M15 5l-7 7 7 7" />,
  warn: <><path d="M12 3l10 18H2z" /><path d="M12 10v5M12 18v.5" /></>,
  mail: <><rect x="3" y="5" width="18" height="14" rx="2" /><path d="M3 7l9 6 9-6" /></>,
  person: <><circle cx="12" cy="8" r="4" /><path d="M4 21a8 8 0 0 1 16 0" /></>,
  pin: <><path d="M12 21s7-6 7-12a7 7 0 0 0-14 0c0 6 7 12 7 12z" /><circle cx="12" cy="9" r="2.5" /></>,
  flag: <path d="M5 21V4h11l-2 4 2 4H5" />,
  down: <path d="M6 9l6 6 6-6" />,
  more: <><circle cx="5" cy="12" r="1.3" fill="currentColor" /><circle cx="12" cy="12" r="1.3" fill="currentColor" /><circle cx="19" cy="12" r="1.3" fill="currentColor" /></>,
  bulb: <><path d="M9 18h6M10 21h4" /><path d="M12 3a6 6 0 0 0-3 11v2h6v-2a6 6 0 0 0-3-11z" /></>,
}
// Solid icons for the four tiles and the active tab, as in the mockup.
const F = {
  alert: <><circle cx="12" cy="12" r="10" /><path d="M12 7v6M12 16.5v.5" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" /></>,
  chat: <><path d="M4 4h16a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1H9l-5 4V5a1 1 0 0 1 1-1z" /><path d="M8 10.5h.5M12 10.5h.5M16 10.5h.5" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" /></>,
  cal: <><rect x="3" y="5" width="18" height="16" rx="2.5" /><path d="M8 3v4M16 3v4" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /><rect x="5.5" y="10" width="13" height="8.5" rx="1" fill="#fff" /><path d="M8 13h2M11 13h2M14 13h2M8 16h2M11 16h2" stroke="currentColor" strokeWidth="1.6" /></>,
  people: <><circle cx="9" cy="8" r="3.6" /><path d="M2 20c0-4 3.2-6.5 7-6.5s7 2.5 7 6.5z" /><circle cx="17" cy="9" r="2.8" /><path d="M14.5 14.2c.8-.4 1.7-.6 2.5-.6 3 0 5 2 5 5.4h-5" /></>,
  home: <path d="M3 11.2 12 3.5l9 7.7V21h-6.5v-6.5h-5V21H3z" />,
}
const Solid = ({ n, s = 22 }) => <svg className="ti" viewBox="0 0 24 24" width={s} height={s} fill="currentColor" aria-hidden="true">{F[n]}</svg>
const Icon = ({ n, s = 18 }) => (
  <svg viewBox="0 0 24 24" width={s} height={s} fill="none" stroke="currentColor" strokeWidth="1.8"
       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{I[n]}</svg>
)

export default function Command() {
  const [tab, setTab] = useState('home')
  const [loc, setLoc] = useState('')
  const [q, setQ] = useState('')
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')
  const [family, setFamily] = useState(null)       // lead id
  const [toast, setToast] = useState('')
  const busy = useRef(false)

  const load = useCallback(() => {
    if (busy.current) return
    busy.current = true
    const p = new URLSearchParams()
    if (loc) p.set('location_id', loc)
    if (q.trim()) p.set('q', q.trim())
    api.get('/program/command' + (p.toString() ? `?${p}` : '')).then(r => { setD(r); setErr('') })
      .catch(e => {
        const msg = (e && e.message) || 'Could not load.'
        if (/no workspace|no outreach program|customer workspace route/i.test(msg) && !sessionStorage.getItem('sci_cmd_entry')) {
          try { sessionStorage.setItem('sci_cmd_entry', '1') } catch { /* private mode */ }
          window.location.replace('/sci?to=command')
          return
        }
        setErr(msg)
      })
      .finally(() => { busy.current = false })
  }, [loc, q])

  useEffect(() => {
    load()
    const t = setInterval(() => { if (document.visibilityState === 'visible') load() }, REFRESH_MS)
    return () => clearInterval(t)
  }, [load])
  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(''), 3500); return () => clearTimeout(t) }, [toast])

  const act = async (item, a) => {
    if (a === 'call') { window.location.href = `tel:${item.phone || ''}`; if (!item.phone) setFamily(item.lead_id); return }
    if (['reply', 'schedule', 'review', 'note'].includes(a)) { setFamily(item.lead_id); return }
    const state = a === 'close' || a === 'dismiss' ? 'closed' : 'opened'
    try {
      await api.post(`/program/responses/${item.response_id}/mark`, { state })
      setToast(a === 'snooze' ? 'Snoozed - it stays in the list as handled.' : 'Closed.')
      load()
    } catch (e) { setToast(e.message || 'Could not update.') }
  }

  if (family) {
    return <Family id={family} onBack={() => { setFamily(null); load() }} toast={setToast} toastText={toast} />
  }
  const inbox = d?.inbox || []
  return (
    <div className="cmd">
      {tab === 'home' && <>
        <header className="cmd-top">
          <div>
            <h1>SCI Command</h1>
            <p className="cmd-sub">{d ? `${d.locations.length} locations · updated ${ago(d.updated_at)}` : 'Loading…'}</p>
          </div>
          <span className="cmd-loc-wrap">
            <select className="cmd-loc" value={loc} onChange={e => setLoc(e.target.value)} aria-label="Location">
              <option value="">All locations</option>
              {(d?.locations || []).map(l => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select><Icon n="down" s={16} />
          </span>
        </header>
        <SearchBox q={q} setQ={setQ} />
        {err && <p className="cmd-err">{err}</p>}
        {d && <>
          <div className="cmd-tiles">
            <Tile icon="alert" tone="red" label="Needs attention" v={d.counts.needs_attention} onClick={() => setTab('inbox')} />
            <Tile icon="chat" tone="green" label="Replies today" v={d.counts.replies_today} onClick={() => setTab('inbox')} />
            <Tile icon="cal" tone="blue" label="Visits booked" v={d.counts.visits_booked} onClick={() => setTab('appts')} />
            <Tile icon="people" tone="teal" label="Contacted today" v={d.counts.contacted_today} onClick={() => setTab('inbox')} />
          </div>
          {d.owner_alert && (
            <a className="cmd-owner" href="/program?tab=health">
              <Icon n="warn" />
              <span><b>Owner alert</b><br />{d.owner_alert.text}</span>
              <span className="cmd-link">View ›</span>
            </a>
          )}
          <SectionHead title="Priority inbox" onAll={() => setTab('inbox')} />
          <Inbox items={inbox.slice(0, 5)} act={act} open={setFamily} />
          <SectionHead title="Today's upcoming actions" onAll={() => setTab('inbox')} />
          <div className="cmd-card cmd-actions">
            <button onClick={() => setTab('inbox')}><Icon n="reply" />
              <span><b>Follow up with {d.upcoming.follow_up} families</b><br />
                <small>Includes {d.upcoming.new} new and {d.upcoming.review} needing review
                  {d.upcoming.callbacks ? ` · ${d.upcoming.callbacks} callbacks` : ''}</small></span>›</button>
            <button onClick={() => setTab('appts')}><Icon n="cal" />
              <span><b>{d.upcoming.visits_booked ? `${d.upcoming.visits_booked} appointment${d.upcoming.visits_booked === 1 ? '' : 's'} booked` : 'No appointments scheduled'}</b><br />
                <small>{d.upcoming.visits_booked ? 'See who is coming in' : 'Get in touch to book a visit today'}</small></span>›</button>
          </div>
        </>}
      </>}
      {tab === 'inbox' && <>
        <header className="cmd-top"><h1>Inbox</h1></header>
        <Inbox items={inbox} act={act} open={setFamily} empty="Nobody is waiting. Nice work." />
      </>}
      {tab === 'search' && <>
        <header className="cmd-top"><h1>Search</h1></header>
        <SearchBox q={q} setQ={setQ} autoFocus />
        {q.trim() ? <Inbox items={inbox} act={act} open={setFamily} empty="No family matches." />
          : <p className="cmd-muted">Search by family name, phone, email or location.</p>}
      </>}
      {tab === 'appts' && <Appointments open={setFamily} />}
      {toast && <div className="cmd-toast" role="status">{toast}</div>}
      <nav className="cmd-tabs" aria-label="Sections">
        {[['home', 'home', 'Home'], ['inbox', 'inbox', 'Inbox'], ['search', 'search', 'Search'], ['appts', 'cal', 'Appointments']].map(([k, ic, lbl]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>
            <span className="cmd-tabicon">{k === 'home' && tab === 'home' ? <Solid n="home" s={22} /> : <Icon n={ic} s={22} />}
              {k === 'inbox' && d?.counts.needs_attention ? <i>{d.counts.needs_attention}</i> : null}</span>{lbl}
          </button>))}
      </nav>
    </div>
  )
}

function SearchBox({ q, setQ, autoFocus }) {
  return (
    <label className="cmd-search"><Icon n="search" />
      <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search family, phone, location..." autoFocus={autoFocus} />
    </label>
  )
}

function Tile({ icon, tone, label, v, onClick }) {
  return (
    <button className={`cmd-tile ${tone}`} onClick={onClick} disabled={!onClick}>
      <Solid n={icon} /><span>{label}</span><b>{v}</b>
    </button>
  )
}

function SectionHead({ title, onAll }) {
  return <div className="cmd-sec"><h2>{title}</h2>{onAll && <button onClick={onAll}>View all ›</button>}</div>
}

const ACT = {
  reply: ['reply', 'Reply', true], call: ['call', 'Call'], schedule: ['cal', 'Schedule'], note: ['note', 'Note'],
  close: ['check', 'Close'], snooze: ['clock', 'Snooze'], review: ['note', 'Review', true], dismiss: ['x', 'Dismiss'],
}

function Inbox({ items, act, open, empty }) {
  if (!items.length) return <p className="cmd-card cmd-muted pad">{empty || 'Nobody is waiting right now.'}</p>
  return items.map(i => (
    <article key={i.lead_id} className="cmd-card cmd-item">
      <button className="cmd-item-main" onClick={() => open(i.lead_id)}>
        <span className={`dot ${i.class}`} />
        <span className="cmd-item-text">
          <b>{i.name}</b><small>{i.location || 'Location not set'}</small>
          <span>{i.summary}</span>
        </span>
        <span className="cmd-item-meta"><em className={`badge ${BADGE[i.badge] || 'low'}`}>{i.badge}</em>
          <small>{time(i.received_at)}</small>›</span>
      </button>
      <div className="cmd-btns">
        {i.actions.map(a => {
          const [ic, lbl, primary] = ACT[a]
          return <button key={a} className={primary ? 'primary' : ''} onClick={() => act(i, a)}><Icon n={ic} />{lbl}</button>
        })}
      </div>
    </article>
  ))
}

function Appointments({ open }) {
  const [rows, setRows] = useState(null)
  useEffect(() => { api.get('/program/appointments?when=upcoming').then(r => setRows(r.items || [])).catch(() => setRows([])) }, [])
  return <>
    <header className="cmd-top"><h1>Appointments</h1></header>
    {rows === null ? <p className="cmd-muted">Loading…</p> : !rows.length
      ? <p className="cmd-card cmd-muted pad">No appointments scheduled yet.</p>
      : rows.map(a => (
        <button key={a.id} className="cmd-card cmd-appt" onClick={() => open(a.lead_id)}>
          <Icon n="cal" /><span><b>{a.name}</b><small>{a.location || ''}</small></span>
          <span className="cmd-item-meta"><b>{new Date(a.when.replace('Z', '')).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</b>
            <small>{a.label || 'Visit'}</small></span>
        </button>))}
  </>
}

function Family({ id, onBack, toast, toastText }) {
  const [f, setF] = useState(null)
  const [err, setErr] = useState('')
  const [mode, setMode] = useState('note')
  const [text, setText] = useState('')
  const [subject, setSubject] = useState('')
  const [slots, setSlots] = useState(null)
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState({})
  const [menu, setMenu] = useState(false)
  const [picking, setPicking] = useState(false)
  const composer = useRef(null)
  const schedRef = useRef(null)

  const load = useCallback(() => {
    api.get(`/program/command/family/${id}`).then(r => { setF(r); setErr('') }).catch(e => setErr(e.message || 'Could not load.'))
  }, [id])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    api.get(`/program/command/slots?lead_id=${id}`).then(setSlots).catch(() => setSlots({ slots: [], reason: 'Calendar unavailable' }))
  }, [id])
  useEffect(() => { if (f?.status === 'new' && f.response_id) api.post(`/program/responses/${f.response_id}/viewed`, {}).catch(() => {}) }, [f?.response_id, f?.status])

  const focusComposer = m => { setMode(m); if (m === 'text' && f?.suggested_reply && !text) setText(f.suggested_reply); setTimeout(() => composer.current?.focus(), 50) }
  const send = async () => {
    if (!text.trim()) return
    setBusy(true)
    try {
      if (mode === 'note') {
        await api.post(`/work/leads/${id}/notes`, { body: text.trim() })
        toast('Note saved.')
      } else if (mode === 'text') {
        await api.post('/sms/send', { lead_id: id, template: text.trim(), include_booking_link: false })
        if (f.response_id) await api.post(`/program/responses/${f.response_id}/mark`, { state: 'responded' }).catch(() => {})
        toast('Text sent.')
      } else {
        await api.post(`/email/send/${id}`, { subject: subject.trim() || 'Following up', body: text.trim(), include_booking_link: false })
        if (f.response_id) await api.post(`/program/responses/${f.response_id}/mark`, { state: 'responded' }).catch(() => {})
        toast('Email sent.')
      }
      setText(''); setSubject(''); load()
    } catch (e) { toast(e.message || 'Not sent.') } finally { setBusy(false) }
  }
  const book = async s => {
    if (!window.confirm(`Book ${f.name} for ${s.label}? The family gets a confirmation text.`)) return
    setBusy(true)
    try {
      const r = await api.post('/program/command/schedule', { lead_id: id, slot: s.value, kind: 'visit' })
      toast(r.ok ? (r.simulated ? `Test contact: ${r.label} is free (nothing booked).` : `Booked - ${r.label}. Confirmed on the calendar.`)
        : (r.reason || 'That time is no longer open.'))
      load()
      api.get(`/program/command/slots?lead_id=${id}`).then(setSlots).catch(() => {})
    } catch (e) { toast(e.message || 'Could not book.') } finally { setBusy(false) }
  }
  const markHandled = async () => {
    try { await api.post(`/program/responses/${f.response_id}/mark`, { state: 'closed' }); toast('Marked handled.'); load() }
    catch (e) { toast(e.message || 'Could not update.') }
  }
  const assign = async uid => {
    setPicking(false)
    try { await api.post('/program/command/assign', { lead_id: id, user_id: uid || null }); toast(uid ? 'Assigned.' : 'Unassigned.'); load() }
    catch (e) { toast(e.message || 'Could not assign.') }
  }

  if (!f) return <div className="cmd"><button className="cmd-back" onClick={onBack}><Icon n="back" /> Back</button>{err ? <p className="cmd-err">{err}</p> : <p className="cmd-muted">Loading…</p>}</div>
  return (
    <div className="cmd cmd-family">
      <header className="cmd-fhead">
        <button className="cmd-back" onClick={onBack} aria-label="Back"><Icon n="back" s={24} /></button>
        <div><h1>{f.name}{f.is_test && <em className="badge low"> Test</em>}</h1><p className="cmd-sub">{f.location || 'Location not set'}</p></div>
        {f.badge && <em className={`badge ${BADGE[f.badge] || 'low'}`}>{f.badge}</em>}
        <span className="cmd-more">
          <button onClick={() => setMenu(!menu)} aria-label="More" aria-expanded={menu}><Icon n="more" s={24} /></button>
          {menu && <div className="cmd-menu" onClick={() => setMenu(false)}>
            <a href={`/leads/${id}`}>Open full family record</a>
            {f.response_id && <button onClick={markHandled}>Mark handled</button>}
            {f.phone && <button onClick={() => { navigator.clipboard?.writeText(f.phone).then(() => toast('Phone copied.')).catch(() => {}) }}>Copy phone number</button>}
          </div>}
        </span>
      </header>
      <div className="cmd-quick">
        <a href={f.phone ? `tel:${f.phone}` : undefined} className={f.phone ? '' : 'off'}><Icon n="call" s={22} />Call</a>
        <button className="primary" onClick={() => focusComposer('text')}><Icon n="reply" s={22} />Reply</button>
        <button onClick={() => schedRef.current?.scrollIntoView({ behavior: 'smooth' })}><Icon n="cal" s={22} />Schedule Visit</button>
        <button onClick={() => focusComposer('note')}><Icon n="note" s={22} />Add Note</button>
      </div>

      <section className="cmd-thread" aria-label="Conversation">
        {f.thread.length ? f.thread.map((t, k) => (<Fragment key={k}>
          {(k === 0 || dayLabel(f.thread[k - 1].at || '') !== dayLabel(t.at || '')) && t.at && <div className="cmd-day">{dayLabel(t.at)}</div>}
          <div className={`bubble ${t.who}`}>
            <p>{t.text}</p>
            <small>{time(t.at)}{t.channel === 'email' ? ' · Email' : ''}{t.who === 'ai' ? ` · AI ${t.delivery === 'draft' ? 'draft' : t.delivery}` : ''}</small>
          </div></Fragment>)) : <p className="cmd-muted">No messages yet.</p>}
      </section>

      <section className="cmd-card cmd-ctx">
        <h3><Icon n="person" /> Family context <a className="cmd-edit" href={`/leads/${id}`}>Edit</a></h3>
        <dl>
          <dt><Icon n="call" />Phone</dt><dd>{fmtPhone(f.phone) || '—'}{f.phone && <a href={`tel:${f.phone}`}>Call</a>}</dd>
          {f.email && <><dt><Icon n="mail" />Email</dt><dd>{f.email}</dd></>}
          <dt><Icon n="pin" />Location</dt><dd>{f.location || '—'}</dd>
          <dt><Icon n="flag" />Campaign</dt><dd>{f.campaign || '—'}</dd>
          <dt><Icon n="person" />Assigned to</dt>
          <dd>{f.assigned_to?.name || 'Unassigned'}<button className="link" onClick={() => setPicking(!picking)}>{f.assigned_to ? 'Change' : 'Assign'}</button></dd>
          {picking && <div className="cmd-assign">
            {f.team.map(u => <button key={u.id} className={f.assigned_to?.id === u.id ? 'on' : ''} onClick={() => assign(u.id)}>{u.name}</button>)}
            {f.assigned_to && <button onClick={() => assign('')}>Unassign</button>}
          </div>}
          <dt><Icon n="clock" />Last touch</dt><dd>{f.last_touch ? `${dayLabel(f.last_touch.at)} at ${time(f.last_touch.at)} (${f.last_touch.channel === 'email' ? 'Email' : 'Text'})` : '—'}</dd>
        </dl>
        {f.setter && <p className="cmd-setter">Appointment setter: <b>{f.setter.state.replace('_', ' ')}</b>
          {f.setter.offered.length ? ` · offered ${f.setter.offered.join(' or ')}` : ''}
          {f.setter.callback_window ? ` · callback ${f.setter.callback_window}` : ''}</p>}
        {f.booking && <p className="cmd-booked"><Icon n="check" /> Booked {new Date(f.booking.when).toLocaleString([], { weekday: 'long', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })} · {f.booking.label || 'Visit'}</p>}
      </section>

      <section className="cmd-card cmd-next">
        <h3><Icon n="bulb" /> Suggested next step</h3>
        <p>{f.suggestion.title}.</p>
        {f.suggestion.steps.map(s => (
          <label key={s}><input type="checkbox" checked={!!done[s]} onChange={e => setDone({ ...done, [s]: e.target.checked })} /> {s}</label>))}
      </section>

      <section className="cmd-card cmd-sched" ref={schedRef}>
        <h3><Icon n="cal" /> Quick schedule a visit</h3>
        {slots === null ? <p className="cmd-muted">Checking the calendar…</p>
          : slots.slots.length ? <div className="cmd-slots">{slots.slots.slice(0, 2).map(s => (
            <button key={s.value} disabled={busy} onClick={() => book(s)}><small>{dayLabel(s.value)}</small>{s.time}</button>))}</div>
            : <p className="cmd-muted">{slots.reason || 'No open times right now.'}</p>}
        {slots?.slots?.length > 2 && <details><summary>Choose custom time ›</summary>
          <div className="cmd-slots">{slots.slots.slice(2).map(s => (
            <button key={s.value} disabled={busy} onClick={() => book(s)}><small>{dayLabel(s.value)}</small>{s.time}</button>))}</div></details>}
      </section>

      <section className="cmd-card cmd-compose">
        <h3>Add a note or reply...</h3>
        <div className="cmd-seg">
          {[['note', 'note', 'Note'], ['text', 'reply', 'Text'], ['email', 'mail', 'Email']].map(([k, ic, l]) => (
            <button key={k} className={mode === k ? 'on' : ''} onClick={() => focusComposer(k)}><Icon n={ic} />{l}</button>))}
        </div>
        {mode === 'email' && <input className="cmd-subj" value={subject} onChange={e => setSubject(e.target.value)} placeholder="Subject" />}
        <textarea ref={composer} value={text} onChange={e => setText(e.target.value)} rows={4}
                  placeholder={mode === 'note' ? 'Write a note about this family...' : mode === 'text' ? 'Write a text...' : 'Write an email...'} />
        <div className="cmd-send">
          {mode === 'text' && f.suggested_reply && text !== f.suggested_reply && <button className="link" onClick={() => setText(f.suggested_reply)}>Use suggested reply</button>}
          <button className="primary" disabled={busy || !text.trim() || (mode === 'text' && !f.phone) || (mode === 'email' && !f.email)} onClick={send}>
            {mode === 'note' ? 'Save note' : mode === 'text' ? 'Send text' : 'Send email'}</button>
        </div>
      </section>
      {toastText && <div className="cmd-toast" role="status">{toastText}</div>}
    </div>
  )
}
