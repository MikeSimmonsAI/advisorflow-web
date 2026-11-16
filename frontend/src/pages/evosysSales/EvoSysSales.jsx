/**
 * EVOSYS SALES (/program/evosys-sales/*) - owner request 2026-11-16.
 *
 * EvoSys's own sales program: the businesses EvoSys is signing up (cleaning
 * companies first). Its own screens, not the old Sales Workspace: Today, Leads,
 * Conversations, Outreach, Appointments, Results.
 *
 * The server is the authority (/sales/evosys/*): only the EvoSys Pro sales
 * team gets in, reps see their own leads, and only a manager can change,
 * approve or switch on the automatic follow-up.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, NavLink, Outlet, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import './evosysSales.css'

export const BASE = '/program/evosys-sales'
const API = '/sales/evosys'

const NAV = [
  { to: BASE, label: 'Today', end: true },
  { to: `${BASE}/leads`, label: 'Leads' },
  { to: `${BASE}/conversations`, label: 'Conversations' },
  { to: `${BASE}/outreach`, label: 'Outreach' },
  { to: `${BASE}/appointments`, label: 'Appointments' },
  { to: `${BASE}/results`, label: 'Results' },
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
  const d = new Date(iso)
  return d.toLocaleString(undefined, opts || { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

function Status({ o }) {
  if (!o) return null
  const tone = { active: 'go', replied: 'good', booked: 'good', finished: 'muted', no_email: 'warn',
                 waiting: 'muted', opted_out: 'bad', email_failed: 'bad' }[o.key] || 'muted'
  return <span className={'evs-pill evs-' + tone}>{o.label}</span>
}

function Box({ error, data, children }) {
  if (error) return <div className="evs-error" role="alert">{error}</div>
  if (!data) return <div className="evs-loading">Loading…</div>
  return children
}

// ── shell ────────────────────────────────────────────────────────────────────

export default function EvoSysSalesShell() {
  return (
    <div className="evs">
      <header className="evs-top">
        <div className="evs-brand">
          <span className="evs-mark">➚</span>
          <div><b>EvoSys Sales</b><small>Signing up new businesses</small></div>
        </div>
        <Link to="/workspaces" className="evs-switch">All programs</Link>
      </header>
      <nav className="evs-nav" aria-label="EvoSys Sales">
        {NAV.map(n => <NavLink key={n.to} to={n.to} end={n.end}
                               className={({ isActive }) => 'evs-tab' + (isActive ? ' on' : '')}>{n.label}</NavLink>)}
      </nav>
      <main className="evs-main"><Outlet /></main>
    </div>
  )
}

// ── Today ────────────────────────────────────────────────────────────────────

export function Today() {
  const { data, error } = useLoad(`${API}/overview`)
  const c = data?.counts || {}
  const o = data?.outreach || {}
  return (
    <Box error={error} data={data}>
      <h1 className="evs-h1">Today</h1>
      {!o.sending_on && (
        <div className="evs-banner">
          <b>Automatic follow-up is off.</b>{' '}
          {o.approved ? 'Your messages are approved. Turn it on in Outreach.'
                      : 'Review and approve your messages in Outreach, then turn it on.'}
          {' '}<Link to={`${BASE}/outreach`}>Go to Outreach →</Link>
        </div>
      )}
      {o.sending_on && (
        <div className="evs-banner evs-banner-on"><b>Automatic follow-up is on.</b>{' '}
          Emails go out weekdays 8 AM - 6 PM Central. {o.in_window ? 'Sending now.' : 'Next send opens at 8 AM on the next weekday.'}
        </div>
      )}
      <div className="evs-stats">
        <Stat n={c.leads} label="Leads" to={`${BASE}/leads`} />
        <Stat n={c.in_followup} label="In follow-up" to={`${BASE}/leads?view=followup`} />
        <Stat n={c.replied} label="Replied" to={`${BASE}/leads?view=replied`} />
        <Stat n={c.booked} label="Booked calls" to={`${BASE}/appointments`} />
        <Stat n={c.sent_today} label="Emails sent today" to={`${BASE}/results`} />
        <Stat n={c.to_call} label="To call (no email)" to={`${BASE}/leads?view=no_email`} />
      </div>

      <section className="evs-card">
        <h2>Replies this week</h2>
        {data?.replies?.length ? data.replies.map(m => (
          <Link key={m.id} className="evs-row" to={`${BASE}/leads/${m.lead.id}`}>
            <div><b>{m.lead.company}</b><small>{m.lead.contact} · {when(m.at)}</small></div>
            <p className="evs-snip">{m.body}</p>
          </Link>
        )) : <p className="evs-empty">No replies yet. When someone answers, open their lead and press “They replied” so the follow-up stops.</p>}
      </section>

      <section className="evs-card">
        <h2>Booked calls</h2>
        {data?.appointments?.length ? data.appointments.map(a => (
          <div key={a.id} className="evs-row">
            <div><b>{a.company || a.title}</b><small>{when(a.starts_at, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</small></div>
            <span className="evs-pill evs-good">{a.status}</span>
          </div>
        )) : <p className="evs-empty">No calls booked yet.</p>}
      </section>

      <section className="evs-card">
        <h2>Call these (no email address)</h2>
        {data?.to_call?.length ? data.to_call.map(l => (
          <Link key={l.id} className="evs-row" to={`${BASE}/leads/${l.id}`}>
            <div><b>{l.company}</b><small>{l.contact || 'No name'}</small></div>
            <a className="evs-phone" href={'tel:' + (l.phone || '')} onClick={e => e.stopPropagation()}>{l.phone}</a>
          </Link>
        )) : <p className="evs-empty">Nobody to call right now.</p>}
      </section>
    </Box>
  )
}

function Stat({ n, label, to }) {
  return <Link to={to} className="evs-stat"><b>{n ?? '–'}</b><span>{label}</span></Link>
}

// ── Leads ────────────────────────────────────────────────────────────────────

const VIEWS = [['all', 'All'], ['followup', 'In follow-up'], ['no_email', 'No email'], ['replied', 'Replied'],
               ['booked', 'Booked'], ['stopped', 'Stopped']]

export function Leads() {
  const params = new URLSearchParams(window.location.search)
  const [view, setView] = useState(params.get('view') || 'all')
  const [q, setQ] = useState('')
  const { data, error } = useLoad(`${API}/leads?view=${view}`)
  const rows = useMemo(() => {
    const n = q.trim().toLowerCase()
    return (data?.leads || []).filter(l => !n || [l.company, l.contact, l.phone, l.email].join(' ').toLowerCase().includes(n))
  }, [data, q])
  return (
    <>
      <h1 className="evs-h1">Leads <small>{data ? `${rows.length} of ${data.total}` : ''}</small></h1>
      <div className="evs-filters">
        {VIEWS.map(([k, l]) => <button key={k} className={'evs-chip' + (view === k ? ' on' : '')} onClick={() => setView(k)}>{l}</button>)}
        <input className="evs-search" placeholder="Search company, name, phone…" value={q} onChange={e => setQ(e.target.value)} />
      </div>
      <Box error={error} data={data}>
        <div className="evs-card evs-list">
          {rows.length ? rows.map(l => (
            <Link key={l.id} className="evs-row" to={`${BASE}/leads/${l.id}`}>
              <div><b>{l.company}</b><small>{[l.contact, l.phone, l.email].filter(Boolean).join(' · ')}</small></div>
              <Status o={l.outreach} />
            </Link>
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
      <p><Link to={`${BASE}/leads`}>← Leads</Link></p>
      <h1 className="evs-h1">{l?.company}</h1>
      <div className="evs-card evs-facts">
        <div><span>Contact</span><b>{l?.contact || '—'}</b></div>
        <div><span>Phone</span><b>{l?.phone ? <a href={'tel:' + l.phone}>{l.phone}</a> : '—'}</b></div>
        <div><span>Email</span><b>{l?.email || '—'}</b></div>
        <div><span>Website</span><b>{l?.website ? <a href={l.website.startsWith('http') ? l.website : 'https://' + l.website} target="_blank" rel="noreferrer">{l.website}</a> : '—'}</b></div>
        <div><span>Pipeline</span><b>{l?.stage_label}</b></div>
        <div><span>Follow-up</span><b><Status o={l?.outreach} /></b></div>
      </div>

      <section className="evs-card">
        <h2>What happened</h2>
        <textarea className="evs-input" rows={3} placeholder="What they said, or how the call went…" value={note} onChange={e => setNote(e.target.value)} />
        <div className="evs-actions">
          <button className="evs-btn" disabled={busy} onClick={() => act('reply', { note, channel: 'email' })}>They replied</button>
          <button className="evs-btn evs-ghost" disabled={busy} onClick={() => act('call', { note })}>I called them</button>
          {l?.outreach?.key === 'active'
            ? <button className="evs-btn evs-ghost" disabled={busy} onClick={() => act('stop')}>Stop follow-up</button>
            : ['manual', 'replied', 'finished'].includes(l?.outreach?.key) &&
              <button className="evs-btn evs-ghost" disabled={busy} onClick={() => act('restart')}>Restart follow-up</button>}
        </div>
        {msg && <div className="evs-error" role="alert">{msg}</div>}
        <p className="evs-hint">“They replied” stops the automatic emails for this lead. Replies arrive in your info@evosyspro.live inbox.</p>
      </section>

      {data?.next && (
        <section className="evs-card">
          <h2>Next email · step {data.next.step} · {when(data.next.due_at)}</h2>
          <p className="evs-subject">{data.next.subject}</p>
          <pre className="evs-body">{data.next.body}</pre>
        </section>
      )}

      <section className="evs-card">
        <h2>Conversation</h2>
        {data?.messages?.length ? data.messages.map(m => <Message key={m.id} m={m} />)
          : <p className="evs-empty">Nothing sent yet.</p>}
      </section>

      <section className="evs-card">
        <h2>History</h2>
        {(data?.timeline || []).map((e, i) => (
          <div key={i} className="evs-hist"><small>{when(e.at)}</small> {e.summary}</div>
        ))}
      </section>
    </Box>
  )
}

function Message({ m }) {
  const who = m.direction === 'in' ? 'They replied' : m.channel === 'call' ? 'Call' : m.channel === 'text' ? 'Text' : 'Email ' + (m.step ? m.step : '')
  return (
    <div className={'evs-msg ' + (m.direction === 'in' ? 'in' : 'out')}>
      <div className="evs-msg-head"><b>{who}</b><small>{when(m.at)}</small>
        {m.status !== 'sent' && m.status !== 'logged' && <span className="evs-pill evs-bad">{m.status === 'held' ? 'Not sent - no texting number yet' : 'Did not send'}</span>}
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
      <h1 className="evs-h1">Conversations</h1>
      <Box error={error} data={data}>
        <div className="evs-card evs-list">
          {data?.conversations?.length ? data.conversations.map(c => (
            <Link key={c.id} className="evs-row" to={`${BASE}/leads/${c.id}`}>
              <div>
                <b>{c.company}</b>
                <small>{c.last_message?.direction === 'in' ? 'They replied' : (c.last_message?.subject || c.last_message?.channel)} · {when(c.last_message?.at)} · {c.message_count} message{c.message_count === 1 ? '' : 's'}</small>
              </div>
              <Status o={c.outreach} />
            </Link>
          )) : <p className="evs-empty">No messages yet. They appear here once outreach is on.</p>}
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
  return (
    <Box error={error} data={form}>
      <h1 className="evs-h1">Outreach</h1>
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
      {form?.problems?.length > 0 && (
        <div className="evs-banner"><b>Before you can approve:</b>
          <ul>{form.problems.map(p => <li key={p}>{p}</li>)}</ul></div>
      )}
      {msg && <div className={msg.ok ? 'evs-okmsg' : 'evs-error'} role="status">{msg.text}</div>}

      <section className="evs-card">
        <h2>Your details</h2>
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
        <h2>The emails</h2>
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
          <button className="evs-btn" disabled={busy || dirty || form?.approved || form?.problems?.length > 0}
                  onClick={() => run(() => api.post(`${API}/outreach/approve`, {}), 'Approved. Now you can turn outreach on.')}>Approve messages</button>
          <button className="evs-btn evs-ghost" disabled={busy} onClick={loadPreview}>Preview with a real lead</button>
        </div>
      )}

      {can && (
        <section className="evs-card">
          <h2>Send yourself a test</h2>
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
          <h2>Preview for {preview.lead?.company || '—'}</h2>
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
      <div><b>{a.company || a.title}</b><small>{a.contact ? a.contact + ' · ' : ''}{when(a.starts_at, { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })}</small></div>
      <span className="evs-pill evs-muted">{a.status}</span>
    </div>
  )
  return (
    <>
      <h1 className="evs-h1">Appointments</h1>
      <Box error={error} data={data}>
        <section className="evs-card"><h2>Coming up</h2>
          {data?.upcoming?.length ? data.upcoming.map(row) : <p className="evs-empty">No calls booked yet. Leads book from the link in your emails.</p>}
        </section>
        <section className="evs-card"><h2>Last 30 days</h2>
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
      <h1 className="evs-h1">Results</h1>
      <div className="evs-filters">
        {[7, 14, 30, 90].map(d => <button key={d} className={'evs-chip' + (days === d ? ' on' : '')} onClick={() => setDays(d)}>Last {d} days</button>)}
      </div>
      <Box error={error} data={data}>
        <div className="evs-stats">
          <div className="evs-stat"><b>{t.emails_sent}</b><span>Emails sent</span></div>
          <div className="evs-stat"><b>{t.replied}</b><span>Replied ({t.reply_rate}%)</span></div>
          <div className="evs-stat"><b>{t.booked}</b><span>Booked calls</span></div>
          <div className="evs-stat"><b>{t.calls_logged}</b><span>Calls logged</span></div>
          <div className="evs-stat"><b>{t.opted_out}</b><span>Opted out</span></div>
          <div className="evs-stat"><b>{t.bounced}</b><span>Bad emails</span></div>
        </div>
        <section className="evs-card">
          <h2>Emails sent by day</h2>
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
