/**
 * EVOSYS SALES (/program/evosys-sales/*) - owner request 2026-11-16.
 *
 * EvoSys's own sales program: the businesses EvoSys is signing up (cleaning
 * companies first). Today, Leads, Conversations, Outreach, Appointments,
 * Results. Layout follows the approved "Simplified Sales Workflow - My Day"
 * (compact navy sidebar, light workspace, big action cards); colours are the
 * EvoSys Pro navy / blue / gold with the official EP emblem.
 *
 * The server is the authority (/sales/evosys/*): only the EvoSys Pro sales
 * team gets in, reps see their own leads, and only a manager can change,
 * approve or switch on the automatic follow-up.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, NavLink, Outlet, useLocation, useParams } from 'react-router-dom'
import { api, getCurrentUser } from '../../api/client'
import './evosysSales.css'

export const BASE = '/program/evosys-sales'
const API = '/sales/evosys'
const EMBLEM = '/brands/evosyspro-emblem-96.png'

// ── icons (inline, no new dependency) ────────────────────────────────────────
const P = {
  today: 'M3 10.5 12 3l9 7.5V20a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z',
  leads: 'M16 11a4 4 0 1 0-8 0M4 21a8 8 0 0 1 16 0M12 7a3 3 0 1 0 0-.01',
  chat: 'M4 5h16v11H8l-4 4z',
  send: 'M3 11 21 3l-8 18-2-8z',
  cal: 'M4 6h16v15H4zM4 10h16M8 3v4M16 3v4',
  chart: 'M4 20V10M10 20V4M16 20v-7M22 20H2',
  phone: 'M5 3h4l2 5-3 2a11 11 0 0 0 6 6l2-3 5 2v4a2 2 0 0 1-2 2A17 17 0 0 1 3 5a2 2 0 0 1 2-2z',
  back: 'M15 18l-6-6 6-6',
  arrow: 'M9 6l6 6-6 6',
  reply: 'M10 9V5l-7 7 7 7v-4c5 0 8 1.5 11 5-1-6-4-10-11-11z',
}
function Icon({ name, size = 20 }) {
  return (
    <svg className="evs-ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor"
         strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={P[name]} />
    </svg>
  )
}

const NAV = [
  { to: BASE, label: 'Today', icon: 'today', end: true },
  { to: `${BASE}/leads`, label: 'Leads', icon: 'leads' },
  { to: `${BASE}/conversations`, label: 'Conversations', short: 'Replies', icon: 'chat' },
  { to: `${BASE}/outreach`, label: 'Outreach', icon: 'send' },
  { to: `${BASE}/appointments`, label: 'Appointments', short: 'Calls', icon: 'cal' },
  { to: `${BASE}/results`, label: 'Results', icon: 'chart' },
]

export function errText(e) {
  if (typeof e?.detail === 'string') return e.detail
  return e?.message || 'Something went wrong. Please try again.'
}

function useLoad(path) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const load = useCallback(() => {
    setError(null)
    return api.get(path).then(setData).catch(e => setError(errText(e)))
  }, [path])
  useEffect(() => { load() }, [load])
  return { data, error, reload: load }
}

export function when(iso, opts) {
  if (!iso) return ''
  return new Date(iso).toLocaleString(undefined, opts || { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}
const LONG = { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }

function Status({ o }) {
  if (!o) return null
  const tone = { active: 'blue', replied: 'gold', booked: 'good', finished: 'muted', no_email: 'warn',
                 waiting: 'muted', opted_out: 'bad', email_failed: 'bad' }[o.key] || 'muted'
  return <span className={'evs-pill evs-' + tone}>{o.label}</span>
}

function Box({ error, data, children }) {
  if (error) return <div className="evs-error" role="alert">{error}</div>
  if (!data) return <div className="evs-loading">Loading…</div>
  return children
}

function CallBtn({ phone, label = 'Call' }) {
  if (!phone) return null
  return <a className="evs-act evs-act-call" href={'tel:' + phone} onClick={e => e.stopPropagation()}><Icon name="phone" size={16} />{label}</a>
}

function PageHead({ title, sub, children }) {
  return (
    <div className="evs-head">
      <div><h1>{title}</h1>{sub && <p>{sub}</p>}</div>
      {children}
    </div>
  )
}

// ── shell ────────────────────────────────────────────────────────────────────

export default function EvoSysSalesShell() {
  const user = getCurrentUser() || {}
  const name = user.full_name || user.name || ''
  const initials = name.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0]).join('').toUpperCase() || 'ES'
  const { pathname } = useLocation()
  useEffect(() => { window.scrollTo?.(0, 0) }, [pathname])
  return (
    <div className="evs">
      <aside className="evs-side" aria-label="EvoSys Sales">
        <div className="evs-logo">
          <img src={EMBLEM} alt="EvoSys Pro" width="40" height="40" />
          <div><b>EvoSys Sales</b><small>Signing up new businesses</small></div>
        </div>
        <nav className="evs-nav">
          {NAV.map(n => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => 'evs-navlink' + (isActive ? ' on' : '')}>
              <Icon name={n.icon} />{n.label}
            </NavLink>
          ))}
        </nav>
        <div className="evs-side-foot">
          <Link to="/workspaces" className="evs-back"><Icon name="back" size={16} />Program Access</Link>
          <div className="evs-me"><span className="evs-avatar">{initials}</span><span>{name || 'Signed in'}</span></div>
        </div>
      </aside>

      <header className="evs-mtop">
        <img src={EMBLEM} alt="EvoSys Pro" width="32" height="32" />
        <b>EvoSys Sales</b>
        <Link to="/workspaces" className="evs-back evs-back-m"><Icon name="back" size={16} />Programs</Link>
      </header>

      <main className="evs-main"><Outlet /></main>

      <nav className="evs-tabbar" aria-label="EvoSys Sales sections">
        {NAV.map(n => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => 'evs-tab' + (isActive ? ' on' : '')}>
            <Icon name={n.icon} size={20} /><span>{n.short || n.label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  )
}

// ── Today ────────────────────────────────────────────────────────────────────

function greeting() {
  const h = new Date().getHours()
  return h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening'
}

export function Today() {
  const { data, error } = useLoad(`${API}/overview`)
  const c = data?.counts || {}
  const o = data?.outreach || {}
  const first = ((getCurrentUser() || {}).full_name || '').split(' ')[0]
  const replies = data?.replies || []
  return (
    <Box error={error} data={data}>
      <PageHead title="Today" sub="Who to contact, who replied, and what's booked." />

      <section className="evs-hello">
        <div>
          <h2>{greeting()}{first ? `, ${first}` : ''}!</h2>
          <p>{replies.length} {replies.length === 1 ? 'reply' : 'replies'} to answer · {c.to_call || 0} to call · {(data?.appointments || []).length} booked</p>
        </div>
        <Link to={`${BASE}/outreach`} className={'evs-flag ' + (o.sending_on ? 'on' : 'off')}>
          <i />{o.sending_on ? 'Follow-up emails ON' : 'Follow-up emails OFF'}
        </Link>
      </section>

      <div className="evs-stats">
        <Stat n={replies.length} label="Replies to answer" to={`${BASE}/conversations`} tone="gold" />
        <Stat n={c.to_call} label="To call (no email)" to={`${BASE}/leads?view=no_email`} />
        <Stat n={c.in_followup} label="In follow-up" to={`${BASE}/leads?view=followup`} />
        <Stat n={c.booked} label="Booked calls" to={`${BASE}/appointments`} />
      </div>

      <div className="evs-bigacts">
        <Link to={`${BASE}/conversations`} className="evs-big evs-big-blue"><Icon name="reply" size={26} /><div><b>Answer replies</b><small>{replies.length} waiting</small></div></Link>
        <a href="#evs-call" className="evs-big evs-big-navy"><Icon name="phone" size={26} /><div><b>Start calling</b><small>{c.to_call || 0} leads with no email</small></div></a>
        <Link to={`${BASE}/appointments`} className="evs-big evs-big-gold"><Icon name="cal" size={26} /><div><b>Booked calls</b><small>{c.booked || 0} on the calendar</small></div></Link>
      </div>

      {!o.sending_on && (
        <div className="evs-banner">
          <b>Automatic follow-up is off.</b>{' '}
          {o.approved ? 'Your messages are approved — turn it on in Outreach.' : 'Approve your messages in Outreach, then turn it on.'}
          {' '}<Link to={`${BASE}/outreach`}>Go to Outreach</Link>
        </div>
      )}

      <section className="evs-card">
        <div className="evs-card-h"><h3>Replied — answer these first</h3><Link to={`${BASE}/conversations`}>All conversations <Icon name="arrow" size={14} /></Link></div>
        {replies.length ? replies.map(m => (
          <div key={m.id} className="evs-row">
            <Link to={`${BASE}/leads/${m.lead.id}`} className="evs-row-main">
              <b>{m.lead.company}</b>
              <small>{m.lead.contact} · replied {when(m.at)}</small>
              <span className="evs-snip">“{m.body}”</span>
            </Link>
            <div className="evs-row-acts">
              <CallBtn phone={m.lead.phone} />
              <Link className="evs-act" to={`${BASE}/leads/${m.lead.id}`}>Open</Link>
            </div>
          </div>
        )) : <p className="evs-empty">No replies yet. A reply to a follow-up email shows up here by itself and stops that lead's emails.</p>}
      </section>

      <section className="evs-card" id="evs-call">
        <div className="evs-card-h"><h3>Call these (no email address)</h3><Link to={`${BASE}/leads?view=no_email`}>See all {c.to_call || 0} <Icon name="arrow" size={14} /></Link></div>
        {data?.to_call?.length ? data.to_call.map(l => (
          <div key={l.id} className="evs-row">
            <Link to={`${BASE}/leads/${l.id}`} className="evs-row-main"><b>{l.company}</b><small>{l.contact || 'No name'} · {l.phone}</small></Link>
            <div className="evs-row-acts">
              <CallBtn phone={l.phone} />
              <Link className="evs-act" to={`${BASE}/leads/${l.id}`}>Log call</Link>
            </div>
          </div>
        )) : <p className="evs-empty">Nobody to call right now.</p>}
      </section>

      <section className="evs-card">
        <div className="evs-card-h"><h3>Booked calls</h3><Link to={`${BASE}/appointments`}>Appointments <Icon name="arrow" size={14} /></Link></div>
        {data?.appointments?.length ? data.appointments.map(a => (
          <div key={a.id} className="evs-row">
            <div className="evs-row-main"><b>{a.company || a.title}</b><small>{a.contact ? a.contact + ' · ' : ''}{when(a.starts_at, LONG)}</small></div>
            <div className="evs-row-acts">
              {a.lead_id && <Link className="evs-act" to={`${BASE}/leads/${a.lead_id}`}>Open lead</Link>}
            </div>
          </div>
        )) : <p className="evs-empty">No calls booked yet. Leads book from the link in your emails.</p>}
      </section>
    </Box>
  )
}

function Stat({ n, label, to, tone }) {
  return <Link to={to} className={'evs-stat' + (tone ? ' evs-stat-' + tone : '')}><b>{n ?? '–'}</b><span>{label}</span></Link>
}

// ── Leads ────────────────────────────────────────────────────────────────────

const VIEWS = [['all', 'All'], ['followup', 'In follow-up'], ['no_email', 'No email'], ['replied', 'Replied'],
               ['booked', 'Booked'], ['stopped', 'Stopped']]

export function Leads() {
  const params = new URLSearchParams(useLocation().search)
  const [view, setView] = useState(params.get('view') || 'all')
  const [q, setQ] = useState('')
  const { data, error } = useLoad(`${API}/leads?view=${view}`)
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase()
    return (data?.leads || []).filter(l => !n || [l.company, l.contact, l.phone, l.email].join(' ').toLowerCase().includes(n))
  }, [data, q])
  return (
    <>
      <PageHead title="Leads" sub={data ? `${rows.length} of ${data.total} businesses` : 'Businesses EvoSys is signing up'} />
      <div className="evs-filters">
        {VIEWS.map(([k, l]) => <button key={k} className={'evs-chip' + (view === k ? ' on' : '')} onClick={() => setView(k)}>{l}</button>)}
        <input className="evs-input evs-search" placeholder="Search company, name, phone…" value={q} onChange={e => setQ(e.target.value)} />
      </div>
      <Box error={error} data={data}>
        <div className="evs-card evs-list">
          {rows.length ? rows.map(l => (
            <div key={l.id} className="evs-row">
              <Link to={`${BASE}/leads/${l.id}`} className="evs-row-main">
                <b>{l.company}</b><small>{[l.contact, l.phone, l.email].filter(Boolean).join(' · ')}</small>
              </Link>
              <div className="evs-row-acts">
                <Status o={l.outreach} />
                <CallBtn phone={l.phone} />
                <Link className="evs-act" to={`${BASE}/leads/${l.id}`}>Open</Link>
              </div>
            </div>
          )) : <p className="evs-empty">No leads here.</p>}
        </div>
      </Box>
    </>
  )
}

// ── one lead ─────────────────────────────────────────────────────────────────

export function Lead() {
  const { id } = useParams()
  const { data, error, reload } = useLoad(`${API}/leads/${id}`)
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const act = async (path, body) => {
    setBusy(true); setMsg(null)
    try { await api.post(`${API}/leads/${id}/${path}`, body || {}); setNote(''); await reload() }
    catch (e) { setMsg(errText(e)) } finally { setBusy(false) }
  }
  const l = data?.lead
  return (
    <Box error={error} data={data}>
      <Link className="evs-crumb" to={`${BASE}/leads`}><Icon name="back" size={14} />Leads</Link>
      <PageHead title={l?.company} sub={l?.contact}>
        <div className="evs-row-acts"><Status o={l?.outreach} /><CallBtn phone={l?.phone} label={l?.phone} /></div>
      </PageHead>
      <div className="evs-card evs-facts">
        <div><span>Email</span><b>{l?.email || '—'}</b></div>
        <div><span>Website</span><b>{l?.website ? <a href={l.website.startsWith('http') ? l.website : 'https://' + l.website} target="_blank" rel="noreferrer">{l.website}</a> : '—'}</b></div>
        <div><span>Pipeline</span><b>{l?.stage_label}</b></div>
        <div><span>Booked</span><b>{l?.booked ? 'Yes' : 'Not yet'}</b></div>
      </div>

      <section className="evs-card">
        <h3>What happened</h3>
        <textarea className="evs-input" rows={3} placeholder="What they said, or how the call went…" value={note} onChange={e => setNote(e.target.value)} />
        <div className="evs-actions">
          <button className="evs-btn" disabled={busy} onClick={() => act('call', { note })}>I called them</button>
          <button className="evs-btn evs-ghost" disabled={busy} onClick={() => act('reply', { note, channel: 'email' })}>They replied (by phone or another inbox)</button>
          {l?.outreach?.key === 'active'
            ? <button className="evs-btn evs-ghost" disabled={busy} onClick={() => act('stop')}>Stop follow-up</button>
            : ['manual', 'replied', 'finished'].includes(l?.outreach?.key) &&
              <button className="evs-btn evs-ghost" disabled={busy} onClick={() => act('restart')}>Restart follow-up</button>}
        </div>
        {msg && <div className="evs-error" role="alert">{msg}</div>}
        <p className="evs-hint">Email replies are picked up by themselves and stop the follow-up. Use “They replied” only when they answered some other way.</p>
      </section>

      {data?.next && (
        <section className="evs-card">
          <h3>Next email · step {data.next.step} · {when(data.next.due_at)}</h3>
          <p className="evs-subject">{data.next.subject}</p>
          <pre className="evs-body">{data.next.body}</pre>
        </section>
      )}

      <section className="evs-card">
        <h3>Conversation</h3>
        {data?.messages?.length ? data.messages.map(m => <Message key={m.id} m={m} />)
          : <p className="evs-empty">Nothing sent yet.</p>}
      </section>

      <section className="evs-card">
        <h3>History</h3>
        {(data?.timeline || []).map((e, i) => (
          <div key={i} className="evs-hist"><small>{when(e.at)}</small> {e.summary}</div>
        ))}
      </section>
    </Box>
  )
}

const HOW = { reply_address: 'matched by their reply address', sender: 'matched by their email address', manual: 'logged by hand' }

function Message({ m }) {
  const inbound = m.direction === 'in'
  const who = inbound ? 'They replied' : m.channel === 'call' ? 'Call' : m.channel === 'text' ? 'Text' : 'Email ' + (m.step || '')
  const problem = m.status === 'held' ? 'Not sent - no texting number yet' : m.status === 'failed' ? 'Did not send' : null
  return (
    <div className={'evs-msg ' + (inbound ? 'in' : 'out')}>
      <div className="evs-msg-head"><b>{who}</b><small>{when(m.at)}{inbound && HOW[m.how] ? ' · ' + HOW[m.how] : ''}</small>
        {problem && <span className="evs-pill evs-bad">{problem}</span>}
      </div>
      {m.subject && <div className="evs-subject">{m.subject}</div>}
      <pre className="evs-body">{m.body}</pre>
      {m.error && m.status === 'failed' && <small className="evs-err-small">{m.error}</small>}
    </div>
  )
}

// ── Conversations ────────────────────────────────────────────────────────────

export function Conversations() {
  const { data, error } = useLoad(`${API}/conversations`)
  return (
    <>
      <PageHead title="Conversations" sub="Everyone you've emailed, newest first. Replies come in by themselves." />
      <Box error={error} data={data}>
        <div className="evs-card evs-list">
          {data?.conversations?.length ? data.conversations.map(c => {
            const lm = c.last_message || {}
            return (
              <div key={c.id} className={'evs-row' + (lm.direction === 'in' ? ' evs-row-new' : '')}>
                <Link to={`${BASE}/leads/${c.id}`} className="evs-row-main">
                  <b>{c.company}</b>
                  <small>{lm.direction === 'in' ? 'They replied' : (lm.subject || lm.channel)} · {when(lm.at)} · {c.message_count} message{c.message_count === 1 ? '' : 's'}</small>
                  {lm.direction === 'in' && lm.body && <span className="evs-snip">“{lm.body}”</span>}
                </Link>
                <div className="evs-row-acts">
                  <Status o={c.outreach} />
                  <CallBtn phone={c.phone} />
                  <Link className="evs-act" to={`${BASE}/leads/${c.id}`}>Open</Link>
                </div>
              </div>
            )
          }) : <p className="evs-empty">No messages yet. They appear here once outreach is on.</p>}
        </div>
      </Box>
    </>
  )
}

// ── Outreach ─────────────────────────────────────────────────────────────────

export function Outreach() {
  const { data, error, reload } = useLoad(`${API}/outreach`)
  const [form, setForm] = useState(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [preview, setPreview] = useState(null)
  const [testTo, setTestTo] = useState('')
  useEffect(() => { if (data) setForm(JSON.parse(JSON.stringify(data))) }, [data])
  const dirty = form && data && JSON.stringify(pick(form)) !== JSON.stringify(pick(data))
  const run = async (fn, ok) => {
    setBusy(true); setMsg(null)
    try { await fn(); if (ok) setMsg({ ok: true, text: ok }); await reload() }
    catch (e) { setMsg({ ok: false, text: errText(e) }) } finally { setBusy(false) }
  }
  const setStep = (i, k, v) => setForm(f => ({ ...f, steps: f.steps.map((s, j) => j === i ? { ...s, [k]: k === 'day' ? Number(v) : v } : s) }))
  const loadPreview = () => api.get(`${API}/outreach/preview`).then(setPreview).catch(e => setMsg({ ok: false, text: errText(e) }))
  const can = form?.can_manage
  const rt = form?.reply_tracking
  return (
    <Box error={error} data={form}>
      <PageHead title="Outreach" sub="The automatic follow-up emails. Nothing sends until you approve them and turn it on." />
      <div className={'evs-card evs-switchcard ' + (form?.sending_on ? 'on' : '')}>
        <div>
          <b>Automatic follow-up is {form?.sending_on ? 'ON' : 'OFF'}</b>
          <small>{form?.approved ? 'Messages approved ' + when(form.approved_at) : 'Messages not approved yet'} · {form?.sending_hours}</small>
        </div>
        {can && (form?.sending_on
          ? <button className="evs-btn evs-danger" disabled={busy} onClick={() => run(() => api.post(`${API}/outreach/switch`, { on: false }), 'Outreach is off.')}>Turn off</button>
          : <button className="evs-btn" disabled={busy || dirty || !form?.approved}
                    onClick={() => run(() => api.post(`${API}/outreach/switch`, { on: true }), 'Outreach is on.')}>Turn on</button>)}
      </div>
      {rt && (
        <div className={'evs-banner ' + (rt.automatic ? 'evs-banner-ok' : '')}>
          <b>{rt.automatic ? 'Replies are read automatically.' : 'Replies are not read automatically.'}</b> {rt.text}
          {rt.last_auto_reply_at && <> Last reply picked up {when(rt.last_auto_reply_at)}.</>}
        </div>
      )}
      {form?.problems?.length > 0 && (
        <div className="evs-banner"><b>Before you can approve:</b>
          <ul>{form.problems.map(p => <li key={p}>{p}</li>)}</ul></div>
      )}
      {msg && <div className={msg.ok ? 'evs-okmsg' : 'evs-error'} role="status">{msg.text}</div>}

      <section className="evs-card">
        <h3>Your details</h3>
        <div className="evs-grid">
          <Field label="Offer (goes where {offer} is)" wide>
            <textarea className="evs-input" rows={2} disabled={!can} value={form?.offer || ''} onChange={e => setForm({ ...form, offer: e.target.value })} />
          </Field>
          <Field label="Booking link ({booking_link})" wide>
            <input className="evs-input" disabled={!can} value={form?.booking_url || ''} onChange={e => setForm({ ...form, booking_url: e.target.value })} />
          </Field>
          <Field label="Your name ({sender_name})">
            <input className="evs-input" disabled={!can} value={form?.sender_name || ''} onChange={e => setForm({ ...form, sender_name: e.target.value })} />
          </Field>
          <Field label="Sends from">
            <input className="evs-input" disabled={!can} value={form?.from_email || ''} onChange={e => setForm({ ...form, from_email: e.target.value })} />
          </Field>
          <Field label="Replies go to">
            <input className="evs-input" disabled={!can} value={form?.reply_to_email || ''} onChange={e => setForm({ ...form, reply_to_email: e.target.value })} />
          </Field>
          <Field label="Most emails a day">
            <input className="evs-input" type="number" min="1" max="300" disabled={!can} value={form?.daily_limit || 60} onChange={e => setForm({ ...form, daily_limit: Number(e.target.value) })} />
          </Field>
          <Field label="Mailing address (required at the bottom of every sales email)" wide>
            <input className="evs-input" disabled={!can} placeholder="Street, City, State ZIP" value={form?.mailing_address || ''} onChange={e => setForm({ ...form, mailing_address: e.target.value })} />
          </Field>
        </div>
      </section>

      <section className="evs-card">
        <h3>The emails</h3>
        <p className="evs-hint">These fill in by themselves: {'{first_name}'}, {'{company}'}, {'{offer}'}, {'{booking_link}'}, {'{sender_name}'}. Each lead gets them in order and stops when they reply, book, or opt out. An opt-out link is added to every email.</p>
        {(form?.steps || []).map((s, i) => (
          <div key={i} className="evs-step">
            <div className="evs-step-head">
              <b>Step {i + 1}</b>
              <label>Day <input className="evs-input evs-day" type="number" min="0" max="90" disabled={!can} value={s.day} onChange={e => setStep(i, 'day', e.target.value)} /></label>
              <select className="evs-input evs-chan" disabled={!can} value={s.channel} onChange={e => setStep(i, 'channel', e.target.value)}>
                <option value="email">Email</option>
                <option value="text">Text (waits for a texting number)</option>
              </select>
              {can && form.steps.length > 1 && <button className="evs-link" onClick={() => setForm({ ...form, steps: form.steps.filter((_, j) => j !== i) })}>Remove</button>}
            </div>
            {s.channel === 'email' && <input className="evs-input" disabled={!can} placeholder="Subject" value={s.subject} onChange={e => setStep(i, 'subject', e.target.value)} />}
            <textarea className="evs-input" rows={7} disabled={!can} value={s.body} onChange={e => setStep(i, 'body', e.target.value)} />
          </div>
        ))}
        {can && (form?.steps?.length || 0) < 10 && (
          <button className="evs-btn evs-ghost" onClick={() => setForm({ ...form, steps: [...form.steps, { day: (form.steps.at(-1)?.day || 0) + 7, channel: 'email', subject: '', body: '' }] })}>+ Add a step</button>
        )}
      </section>

      {can && (
        <div className="evs-card evs-actions">
          <button className="evs-btn" disabled={busy || !dirty} onClick={() => run(() => api.put(`${API}/outreach`, pick(form)), 'Saved. Approve the messages again to send them.')}>Save changes</button>
          <button className="evs-btn evs-btn-gold" disabled={busy || dirty || form?.approved || form?.problems?.length > 0}
                  onClick={() => run(() => api.post(`${API}/outreach/approve`, {}), 'Approved. Now you can turn outreach on.')}>Approve messages</button>
          <button className="evs-btn evs-ghost" disabled={busy} onClick={loadPreview}>Preview with a real lead</button>
        </div>
      )}

      {can && (
        <section className="evs-card">
          <h3>Send yourself a test</h3>
          <div className="evs-actions">
            <input className="evs-input" placeholder="Your email" value={testTo} onChange={e => setTestTo(e.target.value)} />
            <button className="evs-btn evs-ghost" disabled={busy || dirty || !testTo}
                    onClick={() => run(() => api.post(`${API}/outreach/test`, { to: testTo, step: 1 }), 'Test sent to ' + testTo + '.')}>Send step 1 to me</button>
          </div>
          <p className="evs-hint">Goes only to the address you type, never to a lead.</p>
        </section>
      )}

      {preview && (
        <section className="evs-card">
          <h3>Preview for {preview.lead?.company || '—'}</h3>
          {(preview.steps || []).map((s, i) => (
            <div key={i} className="evs-msg out">
              <div className="evs-msg-head"><b>Step {i + 1} · day {s.day} · {s.channel}</b></div>
              <div className="evs-subject">{s.subject}</div>
              <div className="evs-html" dangerouslySetInnerHTML={{ __html: s.html }} />
            </div>
          ))}
        </section>
      )}
    </Box>
  )
}

function pick(f) {
  const { steps, offer, booking_url, sender_name, from_email, reply_to_email, mailing_address, daily_limit } = f || {}
  return { steps, offer, booking_url, sender_name, from_email, reply_to_email, mailing_address, daily_limit }
}

function Field({ label, wide, children }) {
  return <label className={'evs-field' + (wide ? ' wide' : '')}><span>{label}</span>{children}</label>
}

// ── Appointments ─────────────────────────────────────────────────────────────

export function Appointments() {
  const { data, error } = useLoad(`${API}/appointments`)
  const row = a => (
    <div key={a.id} className="evs-row">
      <div className="evs-row-main"><b>{a.company || a.title}</b><small>{a.contact ? a.contact + ' · ' : ''}{when(a.starts_at, LONG)}</small></div>
      <div className="evs-row-acts">
        <span className="evs-pill evs-muted">{a.status}</span>
        {a.lead_id && <Link className="evs-act" to={`${BASE}/leads/${a.lead_id}`}>Open lead</Link>}
      </div>
    </div>
  )
  return (
    <>
      <PageHead title="Appointments" sub="Calls booked from the link in your emails." />
      <Box error={error} data={data}>
        <section className="evs-card"><h3>Coming up</h3>
          {data?.upcoming?.length ? data.upcoming.map(row) : <p className="evs-empty">No calls booked yet.</p>}
        </section>
        <section className="evs-card"><h3>Last 30 days</h3>
          {data?.recent?.length ? data.recent.map(row) : <p className="evs-empty">None.</p>}
        </section>
      </Box>
    </>
  )
}

// ── Results ──────────────────────────────────────────────────────────────────

export function Results() {
  const [days, setDays] = useState(14)
  const { data, error } = useLoad(`${API}/results?days=${days}`)
  const t = data?.totals || {}
  const max = Math.max(1, ...(data?.by_day || []).map(d => d.sent))
  return (
    <>
      <PageHead title="Results" sub="What the follow-up is doing." />
      <div className="evs-filters">
        {[7, 14, 30, 90].map(d => <button key={d} className={'evs-chip' + (days === d ? ' on' : '')} onClick={() => setDays(d)}>Last {d} days</button>)}
      </div>
      <Box error={error} data={data}>
        <div className="evs-stats evs-stats-6">
          <div className="evs-stat"><b>{t.emails_sent}</b><span>Emails sent</span></div>
          <div className="evs-stat evs-stat-gold"><b>{t.replied}</b><span>Replied ({t.reply_rate}%)</span></div>
          <div className="evs-stat"><b>{t.booked}</b><span>Booked calls</span></div>
          <div className="evs-stat"><b>{t.calls_logged}</b><span>Calls logged</span></div>
          <div className="evs-stat"><b>{t.opted_out}</b><span>Opted out</span></div>
          <div className="evs-stat"><b>{t.bounced}</b><span>Bad emails</span></div>
        </div>
        <section className="evs-card">
          <h3>Emails sent by day</h3>
          {data?.by_day?.length ? (
            <div className="evs-bars">
              {data.by_day.map(d => (
                <div key={d.date} className="evs-bar" title={`${d.date}: ${d.sent} sent, ${d.replies} replies`}>
                  <i style={{ height: `${Math.round(100 * d.sent / max)}%` }} />
                  <small>{d.date.slice(5)}</small>
                </div>
              ))}
            </div>
          ) : <p className="evs-empty">Nothing sent in this period.</p>}
        </section>
      </Box>
    </>
  )
}
