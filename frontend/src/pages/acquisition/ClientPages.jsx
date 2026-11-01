/**
 * The client's pages (Issue #452). Plain words, big targets, one job per
 * screen. Every number comes from the server; an empty list says so.
 */
import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import { BASE, useAcq } from './AcquisitionShell'
import { DateChip, Icon, Kpi, Loading, Pill, apptPill, clock, errText, money, useLoad, useToast, when } from './ui'

function ProspectRow({ x }) {
  return (
    <div className="aq-row">
      <div className="aq-row__main">
        <b>{x.business_name}</b>
        <span>{[x.segment_label, x.city, x.contact_name].filter(Boolean).join(' · ') || '—'}</span>
      </div>
      {x.monthly_value != null && <span className="aq-time">{money(x.monthly_value, '/mo')}</span>}
      <Pill stage={x.stage} label={x.stage_label} />
      <Link className="aq-btn aq-btn--sm" to={`${BASE}/prospects/${x.id}`}>Open</Link>
    </div>
  )
}

function ApptRow({ x }) {
  const a = x.appointment || {}
  return (
    <div className="aq-row">
      <DateChip iso={a.time} />
      <div className="aq-row__main">
        <b><Link to={`${BASE}/prospects/${x.id}`} style={{ color: 'inherit', textDecoration: 'none' }}>{x.business_name}</Link></b>
        <span>{[x.segment_label, x.city].filter(Boolean).join(' • ') || (a.label || 'Walkthrough')}</span>
      </div>
      <span className="aq-time">🕒 {clock(a.time)}</span>
      {apptPill(a)}
    </div>
  )
}

// ── Dashboard ───────────────────────────────────────────────────────────
export function Home() {
  const { status } = useAcq()
  const [d, err] = useLoad('/acquisition/dashboard')
  if (err) return <div className="aq-page"><div className="aq-err">{errText(err)}</div></div>
  if (!d) return <div className="aq-page"><Loading /></div>
  const c = d.counts
  const first = (status.me?.name || '').split(' ')[0]
  const steps = [
    { done: d.upcoming.length === 0, text: d.upcoming.length ? `Attend your ${d.upcoming.length} scheduled appointment${d.upcoming.length > 1 ? 's' : ''}` : 'Attend scheduled appointments' },
    { done: d.needs_outcome.length === 0, text: d.needs_outcome.length ? `Add details after ${d.needs_outcome.length} meeting${d.needs_outcome.length > 1 ? 's' : ''}` : 'Add estimate details after each meeting' },
    { done: false, text: 'Mark opportunities as Won or Lost' },
    { done: false, text: 'Share feedback on lead quality', link: `${BASE}/feedback` },
  ]
  return (
    <div className="aq-page">
      <h1 className="aq-h1">Welcome{first ? `, ${first}` : ''}!</h1>
      <p className="aq-lede">Here are your results, upcoming appointments, and next steps.</p>
      {!status.running && (
        <div className="aq-note">Your outreach hasn’t started yet. Your 30-day free pilot begins the day it goes live — interested businesses will appear here as soon as they reply.</div>
      )}
      <div className="aq-grid aq-kpis aq-kpis--dark">
        <Kpi icon="building" value={c.in_research} label="New Opportunities" sub="(In Research)" />
        <Kpi icon="chat" value={c.interested} label="Interested Leads" sub="(In Conversation)" />
        <Kpi icon="calendar" value={c.booked} label="Appointments" sub="(Booked)" />
        <Kpi icon="handshake" value={c.won} label="Contracts Won" sub="(This Pilot)" />
      </div>
      <div className="aq-grid aq-split">
        <section className="aq-card">
          <div className="aq-card__head"><h2>Upcoming Appointments</h2><Link to={`${BASE}/appointments`}>View All →</Link></div>
          {d.upcoming.length ? d.upcoming.map(x => <ApptRow key={x.id} x={x} />)
            : <div className="aq-empty">No appointments booked yet.</div>}
        </section>
        <section className="aq-card">
          <h2>Your Next Steps</h2>
          <ul className="aq-checklist">
            {steps.map(st => (
              <li key={st.text}><span className={`aq-box`} style={st.done ? { background: '#15924a', borderColor: '#15924a' } : undefined} />
                {st.link ? <Link to={st.link} style={{ color: 'inherit' }}>{st.text}</Link> : st.text}</li>
            ))}
          </ul>
        </section>
      </div>
      {(d.needs_outcome.length > 0 || d.new_interest.length > 0) && (
        <div className="aq-grid aq-two" style={{ marginTop: 14 }}>
          <section className="aq-card"><h2>Tell us how it went</h2>
            {d.needs_outcome.length ? d.needs_outcome.map(x => <ProspectRow key={x.id} x={x} />) : <div className="aq-empty">Nothing waiting on you.</div>}
          </section>
          <section className="aq-card"><h2>New interest</h2>
            {d.new_interest.length ? d.new_interest.map(x => <ProspectRow key={x.id} x={x} />) : <div className="aq-empty">No new interest right now.</div>}
          </section>
        </div>
      )}
    </div>
  )
}

// ── Interested businesses ───────────────────────────────────────────────
export function Prospects() {
  const [stage, setStage] = useState('')
  const [d, err] = useLoad('/acquisition/prospects' + (stage ? `?stage=${stage}` : ''))
  return (
    <div className="aq-page">
      <h1 className="aq-h1">Opportunities</h1>
      <p className="aq-lede">Every business here told us they want to hear about cleaning. Open one to see who they are and record what happened.</p>
      <div className="aq-tabs">
        <button className={!stage ? 'on' : ''} onClick={() => setStage('')}>All</button>
        {(d?.stages || []).map(s => (
          <button key={s.key} className={stage === s.key ? 'on' : ''} onClick={() => setStage(s.key)}>{s.label}</button>
        ))}
      </div>
      {err && <div className="aq-err">{errText(err)}</div>}
      <section className="aq-card">
        {!d ? <Loading /> : d.items.length ? d.items.map(x => <ProspectRow key={x.id} x={x} />)
          : <div className="aq-empty">No businesses here yet.</div>}
      </section>
    </div>
  )
}

const OUTCOMES = {
  appointment_booked: [['appointment_held', 'We met'], ['no_show', 'They didn’t show'], ['quote_issued', 'I sent a quote'], ['lost', 'Not moving forward']],
  no_show: [['appointment_held', 'We met after all'], ['lost', 'Not moving forward']],
  appointment_held: [['quote_issued', 'I sent a quote'], ['won', 'Contract won'], ['lost', 'Not moving forward']],
  quote_issued: [['won', 'Contract won'], ['lost', 'Not moving forward']],
  interested: [['lost', 'Not a fit for me']],
}

export function Prospect() {
  const { id } = useParams()
  const { status, reloadCounts } = useAcq()
  const [x, err, reload] = useLoad(`/acquisition/prospects/${id}`)
  const [pick, setPick] = useState(null)
  const [form, setForm] = useState({})
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [toast, say] = useToast()
  if (err) return <div className="aq-page"><div className="aq-err">{errText(err)}</div><Link to={`${BASE}/prospects`}>Back</Link></div>
  if (!x) return <div className="aq-page"><Loading /></div>

  const save = async () => {
    setBusy(true); setFail(null)
    try {
      await api.post(`/acquisition/prospects/${id}/outcome`, { outcome: pick, ...form })
      say('Saved'); setPick(null); setForm({}); await reload(); reloadCounts()
    } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  const choices = OUTCOMES[x.stage] || []
  return (
    <div className="aq-page">
      {toast}
      <Link to={`${BASE}/prospects`} className="aq-btn aq-btn--sm">← All opportunities</Link>
      <h1 className="aq-h1" style={{ marginTop: 14 }}>{x.business_name}</h1>
      <p className="aq-lede"><Pill stage={x.stage} label={x.stage_label} /> {x.segment_label ? ` · ${x.segment_label}` : ''}</p>
      <div className="aq-grid aq-two">
        <section className="aq-card">
          <h2>Who they are</h2>
          <dl className="aq-dl">
            <dt>Contact</dt><dd>{x.contact_name || '—'}{x.contact_title ? `, ${x.contact_title}` : ''}</dd>
            <dt>Email</dt><dd>{x.email ? <a href={`mailto:${x.email}`}>{x.email}</a> : '—'}</dd>
            <dt>Phone</dt><dd>{x.phone ? <a href={`tel:${x.phone}`}>{x.phone}</a> : '—'}</dd>
            <dt>Address</dt><dd>{x.address || '—'}</dd>
            <dt>Website</dt><dd>{x.website ? <a href={x.website} target="_blank" rel="noreferrer">{x.website}</a> : '—'}</dd>
            <dt>What they said</dt><dd>{x.interest_note || '—'}</dd>
          </dl>
        </section>
        <section className="aq-card">
          <h2>Appointment & results</h2>
          <dl className="aq-dl">
            <dt>Appointment</dt><dd>{x.appointment ? `${x.appointment.label || 'Walkthrough'} · ${when(x.appointment.time)}` : 'Not booked yet'}</dd>
            <dt>Quote</dt><dd>{money(x.quote_amount)}</dd>
            <dt>Monthly value</dt><dd>{money(x.monthly_value)}</dd>
            {x.lost_reason && <><dt>Why not</dt><dd>{x.lost_reason}</dd></>}
            {x.outcome_note && <><dt>Your notes</dt><dd>{x.outcome_note}</dd></>}
          </dl>
          {status.can_manage && choices.length > 0 && (
            <>
              <div className="aq-actions">
                {choices.map(([k, label]) => (
                  <button key={k} className={`aq-btn ${pick === k ? 'aq-btn--primary' : ''}`} onClick={() => { setPick(k); setFail(null) }}>{label}</button>
                ))}
              </div>
              {pick && (
                <div style={{ marginTop: 14 }}>
                  {pick === 'quote_issued' && (
                    <div className="aq-field"><label>Quote amount ($)</label>
                      <input inputMode="decimal" value={form.quote_amount || ''} onChange={e => setForm({ ...form, quote_amount: e.target.value })} /></div>
                  )}
                  {(pick === 'quote_issued' || pick === 'won') && (
                    <div className="aq-field"><label>Monthly contract value ($)</label>
                      <input inputMode="decimal" value={form.monthly_value || ''} onChange={e => setForm({ ...form, monthly_value: e.target.value })} /></div>
                  )}
                  {pick === 'lost' && (
                    <div className="aq-field"><label>Why not? (optional)</label>
                      <input value={form.reason || ''} onChange={e => setForm({ ...form, reason: e.target.value })} /></div>
                  )}
                  <div className="aq-field"><label>Notes (optional)</label>
                    <textarea value={form.note || ''} onChange={e => setForm({ ...form, note: e.target.value })} /></div>
                  {fail && <div className="aq-err">{fail}</div>}
                  <button className="aq-btn aq-btn--primary" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
                </div>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  )
}

// ── Appointments ────────────────────────────────────────────────────────
export function Appointments() {
  const [d, err] = useLoad('/acquisition/appointments')
  const [tab, setTab] = useState('upcoming')
  const done = (d?.all || []).filter(x => ['appointment_held', 'quote_issued', 'won', 'lost'].includes(x.stage))
  const noShow = (d?.all || []).filter(x => x.stage === 'no_show')
  const lists = { upcoming: d?.upcoming || [], completed: done, no_show: noShow }
  return (
    <div className="aq-page">
      <h1 className="aq-h1">Appointments</h1>
      <p className="aq-lede">Walkthroughs and consultations we booked for you. They are also on your calendar.</p>
      {err && <div className="aq-err">{errText(err)}</div>}
      <div className="aq-tabs">
        {[['upcoming', `Upcoming (${lists.upcoming.length})`], ['completed', 'Completed'], ['no_show', 'No-Shows']].map(([k, l]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>
        ))}
      </div>
      <section className="aq-card">
        {!d ? <Loading /> : lists[tab].length ? lists[tab].map(x => <ApptRow key={x.id} x={x} />)
          : <div className="aq-empty">Nothing here yet.</div>}
      </section>
    </div>
  )
}

// ── Estimates ───────────────────────────────────────────────────────────
export function Estimates() {
  const [d, err] = useLoad('/acquisition/prospects?stage=quote_issued')
  const total = (d?.items || []).reduce((a, x) => a + (x.monthly_value || 0), 0)
  return (
    <div className="aq-page">
      <h1 className="aq-h1">Estimates</h1>
      <p className="aq-lede">Quotes you’ve sent that are waiting on an answer. Mark each one Won or Lost when you hear back.</p>
      {err && <div className="aq-err">{errText(err)}</div>}
      <div className="aq-grid aq-kpis">
        <Kpi icon="doc" value={d ? d.items.length : '—'} label="Open estimates" />
        <Kpi icon="chart" value={d ? money(total, '/mo') : '—'} label="Monthly value quoted" />
      </div>
      <section className="aq-card">
        {!d ? <Loading /> : d.items.length ? d.items.map(x => <ProspectRow key={x.id} x={x} />)
          : <div className="aq-empty">No open estimates.</div>}
      </section>
    </div>
  )
}

// ── Contracts ───────────────────────────────────────────────────────────
export function Results() {
  const [d, err] = useLoad('/acquisition/dashboard')
  const [list] = useLoad('/acquisition/prospects')
  if (err) return <div className="aq-page"><div className="aq-err">{errText(err)}</div></div>
  if (!d) return <div className="aq-page"><Loading /></div>
  const c = d.counts
  const rate = (a, b) => (b ? Math.round((a / b) * 100) + '%' : '—')
  const won = (list?.items || []).filter(x => x.stage === 'won')
  const lost = (list?.items || []).filter(x => x.stage === 'lost')
  return (
    <div className="aq-page">
      <h1 className="aq-h1">Contracts</h1>
      <p className="aq-lede">Straight counts from your own records — nothing estimated.</p>
      <div className="aq-grid aq-kpis">
        <Kpi icon="handshake" value={c.won} label="Contracts won" />
        <Kpi icon="chart" value={money(d.won_monthly_value, '/mo')} label="Recurring value won" />
        <Kpi icon="calendar" value={rate(c.held, c.booked)} label="Booked → held" />
        <Kpi icon="doc" value={rate(c.won, c.quoted)} label="Quoted → won" />
      </div>
      <div className="aq-grid aq-two">
        <section className="aq-card"><h2>Won</h2>
          {won.length ? won.map(x => <ProspectRow key={x.id} x={x} />) : <div className="aq-empty">No contracts won yet.</div>}
        </section>
        <section className="aq-card"><h2>Lost</h2>
          {lost.length ? lost.map(x => <ProspectRow key={x.id} x={x} />) : <div className="aq-empty">None.</div>}
        </section>
      </div>
    </div>
  )
}

// ── My business ─────────────────────────────────────────────────────────
export function Settings() {
  const { status, reloadStatus } = useAcq()
  const [p, err, reload] = useLoad('/acquisition/profile')
  const [f, setF] = useState(null)
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [toast, say] = useToast()
  useEffect(() => {
    if (p) setF({ ...p, service_area: (p.service_area || []).join(', '), services: (p.services || []).join(', ') })
  }, [p])
  if (err) return <div className="aq-page"><div className="aq-err">{errText(err)}</div></div>
  if (!f) return <div className="aq-page"><Loading /></div>
  const can = status.can_manage
  const set = k => e => setF({ ...f, [k]: e.target.value })
  const toggle = k => setF({ ...f, segments: f.segments.includes(k) ? f.segments.filter(s => s !== k) : [...f.segments, k] })
  const save = async () => {
    setBusy(true); setFail(null)
    const split = s => String(s || '').split(',').map(x => x.trim()).filter(Boolean)
    try {
      await api.put('/acquisition/profile', {
        business_name: f.business_name, owner_name: f.owner_name, contact_phone: f.contact_phone,
        contact_email: f.contact_email, service_area: split(f.service_area), services: split(f.services),
        segments: f.segments, min_job_value: f.min_job_value === '' ? null : f.min_job_value,
        insurance_note: f.insurance_note, certifications_note: f.certifications_note,
        availability_note: f.availability_note, appointment_minutes: Number(f.appointment_minutes),
      })
      say('Saved'); await reload(); reloadStatus()
    } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  const field = (k, label, help, multi) => (
    <div className="aq-field"><label htmlFor={`aq-${k}`}>{label}</label>
      {multi ? <textarea id={`aq-${k}`} value={f[k] || ''} onChange={set(k)} disabled={!can} />
        : <input id={`aq-${k}`} value={f[k] ?? ''} onChange={set(k)} disabled={!can} />}
      {help && <small>{help}</small>}</div>
  )
  return (
    <div className="aq-page">
      {toast}
      <h1 className="aq-h1">My business</h1>
      <p className="aq-lede">This is how we describe you and which businesses we look for. Only write what’s true today — we never claim anything you haven’t confirmed.</p>
      <div className="aq-grid aq-two">
        <section className="aq-card">
          <h2>About you</h2>
          {field('business_name', 'Business name')}
          {field('owner_name', 'Your name')}
          {field('contact_phone', 'Best phone for appointments')}
          {field('contact_email', 'Best email for appointment alerts')}
          {field('services', 'Services you offer', 'Separate with commas, e.g. office cleaning, floor care, move-out cleaning')}
          {field('insurance_note', 'Insurance', 'Only what you can show proof of', true)}
          {field('certifications_note', 'Certifications', 'Only what you actually hold', true)}
        </section>
        <section className="aq-card">
          <h2>Who you want</h2>
          <div className="aq-field"><label>Kinds of businesses</label>
            <div className="aq-checks">
              {status.segments.map(s => (
                <label key={s.key}><input type="checkbox" checked={f.segments.includes(s.key)} onChange={() => toggle(s.key)} disabled={!can} />{s.label}</label>
              ))}
            </div></div>
          {field('service_area', 'Cities you cover', 'Separate with commas')}
          {field('min_job_value', 'Smallest monthly contract you’ll take ($)')}
          <h2 style={{ marginTop: 20 }}>When you can meet</h2>
          {field('availability_note', 'Your usual availability', 'For example: Tue & Thu 9–12, Fri afternoons. Your calendar is always checked before a time is offered.', true)}
          <div className="aq-field"><label htmlFor="aq-mins">Appointment length</label>
            <select id="aq-mins" value={f.appointment_minutes} onChange={set('appointment_minutes')} disabled={!can}>
              {[15, 30, 45, 60, 90, 120].map(m => <option key={m} value={m}>{m} minutes</option>)}
            </select></div>
        </section>
      </div>
      {fail && <div className="aq-err">{fail}</div>}
      {can && <div className="aq-actions"><button className="aq-btn aq-btn--primary" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save changes'}</button></div>}
    </div>
  )
}

// ── Feedback ────────────────────────────────────────────────────────────
export function Feedback() {
  const [d, err, reload] = useLoad('/acquisition/feedback')
  const [body, setBody] = useState('')
  const [rating, setRating] = useState('general')
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [toast, say] = useToast()
  const send = async () => {
    setBusy(true); setFail(null)
    try { await api.post('/acquisition/feedback', { body, rating }); setBody(''); say('Thanks — we read every note'); reload() }
    catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  return (
    <div className="aq-page">
      {toast}
      <h1 className="aq-h1">Feedback</h1>
      <p className="aq-lede">Tell us what’s working and what isn’t. Good fit, bad fit, wrong kind of business — it all makes the next appointment better.</p>
      <section className="aq-card">
        <div className="aq-tabs">
          {[['general', 'General'], ['good_fit', 'Good fit'], ['poor_fit', 'Not a good fit']].map(([k, l]) => (
            <button key={k} className={rating === k ? 'on' : ''} onClick={() => setRating(k)}>{l}</button>
          ))}
        </div>
        <div className="aq-field"><label htmlFor="aq-fb">Your note</label>
          <textarea id="aq-fb" value={body} onChange={e => setBody(e.target.value)} /></div>
        {fail && <div className="aq-err">{fail}</div>}
        <button className="aq-btn aq-btn--primary" disabled={busy || !body.trim()} onClick={send}>{busy ? 'Sending…' : 'Send'}</button>
      </section>
      <section className="aq-card" style={{ marginTop: 14 }}>
        <h2>What you’ve told us</h2>
        {err && <div className="aq-err">{errText(err)}</div>}
        {!d ? <Loading /> : d.items.length ? d.items.map(f => (
          <div className="aq-row" key={f.id}><div className="aq-row__main"><b>{f.body}</b><span>{when(f.at)} · {f.rating.replace('_', ' ')}</span></div></div>
        )) : <div className="aq-empty">No notes yet.</div>}
      </section>
    </div>
  )
}
