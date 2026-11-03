/**
 * CLEANING CLIENT PORTAL pages (#452). Booked appointments delivered by
 * EvoSys, what happened at each one, and requests back to EvoSys.
 * Every number comes from the server; an empty list says so.
 */
import { useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import { BASE, useAcq } from './AcquisitionShell'
import { BusinessForm, Readiness } from './BusinessForm'
import { Dot, Empty, Feed, Icon, Loading, PageHeader, Stat, Toolbar, clock, errText, money, useLoad, useToast, when } from './ui'

const STATUS_TONE = { appointment_booked: 'blue', appointment_held: 'green', no_show: 'amber', quote_issued: 'violet',
                      won: 'green', lost: 'gray' }
const STATUS_LABEL = { appointment_booked: 'Upcoming', appointment_held: 'Completed', no_show: 'No-show',
                       quote_issued: 'Estimate sent', won: 'Contract won', lost: 'Not moving forward' }
const PERIODS = [['7d', 'Last 7 days'], ['month', 'This month'], ['last_month', 'Last month'], ['90d', 'Last 90 days'], ['all', 'All time']]

function Status({ x }) {
  return <Dot tone={STATUS_TONE[x.stage] || 'gray'}>{STATUS_LABEL[x.stage] || x.stage_label}</Dot>
}

function ApptTable({ items, empty }) {
  const nav = useNavigate()
  if (!items.length) return <Empty icon="calendar" title={empty} text="EvoSys adds an appointment here the moment one is booked for you." />
  return (<>
    <div className="hb-mcards">{items.map(x => (
      <article className="hb-mcard" key={x.id}>
        <div className="hb-mcard__top"><b>{x.business_name}</b><Status x={x} /></div>
        <div className="hb-muted">{x.segment_label || 'Commercial cleaning'} · {x.appointment?.time ? `${when(x.appointment.time)}` : 'Time to be confirmed'}</div>
        <div className="hb-muted">{[x.contact_name, x.phone].filter(Boolean).join(' · ') || 'Contact shared at booking'}</div>
        <div className="hb-mcard__foot"><span className="hb-muted">{x.city || ''}</span>
          <Link className="aq-btn aq-btn--sm aq-btn--primary" to={`${BASE}/appointments/${x.id}`}>Open</Link></div>
      </article>))}</div>
    <div className="hb-table-wrap hb-desk"><table className="aq-table"><thead><tr>
      <th>Business</th><th>Type</th><th>Date &amp; time</th><th>Contact</th><th>Status</th></tr></thead>
      <tbody>{items.map(x => (
        <tr key={x.id} className="hb-click" onClick={() => nav(`${BASE}/appointments/${x.id}`)}>
          <td><b>{x.business_name}</b>{x.city && <div className="hb-muted">{x.city}</div>}</td>
          <td>{x.segment_label || '—'}</td>
          <td>{x.appointment?.time ? <>{new Date(x.appointment.time).toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' })}<div className="hb-muted">{clock(x.appointment.time)}</div></> : '—'}</td>
          <td>{x.contact_name || '—'}{x.phone && <div className="hb-muted">{x.phone}</div>}</td>
          <td><Status x={x} /></td></tr>))}</tbody></table></div>
  </>)
}

// ── Dashboard ───────────────────────────────────────────────────────────
export function Home() {
  const { name, status } = useAcq()
  const [d, err] = useLoad('/acquisition/dashboard')
  const [m] = useLoad('/acquisition/report?period=month')
  if (err) return <div className="aq-page"><div className="aq-err">{errText(err)}</div></div>
  if (!d) return <div className="aq-page"><Loading /></div>
  const c = d.counts
  const total = (d.by_segment || []).reduce((a, s) => a + s.count, 0)
  const next = d.needs_outcome[0] || d.upcoming[0]
  return (
    <div className="aq-page">
      <PageHeader eyebrow="Client Portal" title={`${name} — Booked Appointments`}
        sub="Appointments delivered by EvoSys. You handle the visit and update the result." />
      {!status.running && c.delivered === 0 && <div className="aq-note">EvoSys hasn’t booked an appointment for you yet. Each one appears here as soon as it’s on your calendar.</div>}
      <div className="hb-stats">
        <Stat icon="calendar" label="Upcoming appointments" value={c.upcoming} sub="Booked and scheduled" to={`${BASE}/appointments`} />
        <Stat icon="check" tone="green" label="Completed visits" value={c.held} sub={`of ${c.booked} delivered`} to={`${BASE}/appointments`} />
        <Stat icon="doc" tone="blue" label="Estimates sent" value={c.quoted} sub="Quotes you provided" to={`${BASE}/estimates`} />
        <Stat icon="handshake" tone="green" label="Contracts won" value={c.won} sub={`${c.no_show} no-shows`} to={`${BASE}/results`} />
        <Stat icon="dollar" tone="rose" label="Monthly recurring value" value={money(d.won_monthly_value)} sub="As you reported it" to={`${BASE}/results`} />
      </div>
      <section className="aq-card" style={{ marginBottom: 14 }}>
        <div className="aq-card__head"><h2>Appointments by type{m ? ` · delivered this month: ${m.appointments_booked}` : ''}</h2><Link to={`${BASE}/results`}>View results →</Link></div>
        {total ? <div className="hb-funnel">{d.by_segment.map(s => (
          <div className="hb-funnel__step" key={s.segment}><b>{s.count}</b><span>{s.label} ({Math.round((s.count / total) * 100)}%)</span></div>))}</div>
          : <p className="hb-muted" style={{ margin: 0 }}>No appointments delivered yet.</p>}
      </section>
      <div className="aq-grid aq-split">
        <section className="aq-card">
          <div className="aq-card__head"><h2>Next appointments</h2><Link to={`${BASE}/appointments`}>View all →</Link></div>
          <ApptTable items={d.upcoming} empty="No upcoming appointments" />
        </section>
        <section className="aq-card">
          <h2>Appointment actions</h2>
          <p className="hb-muted">Keep each appointment up to date so EvoSys can measure what’s working.</p>
          {d.needs_outcome.length > 0 && <div className="aq-note aq-note--warn">{d.needs_outcome.length} appointment{d.needs_outcome.length > 1 ? 's' : ''} need{d.needs_outcome.length > 1 ? '' : 's'} a result from you.</div>}
          <div className="aq-actions" style={{ flexDirection: 'column', alignItems: 'stretch' }}>
            {next ? <>
              <Link className="aq-btn aq-btn--primary" to={`${BASE}/appointments/${next.id}#outcome`}><Icon name="check" size={16} />Report outcome — {next.business_name}</Link>
              <Link className="aq-btn" to={`${BASE}/appointments/${next.id}#followup`}><Icon name="clock" size={16} />Request reschedule or follow-up</Link></>
              : <span className="hb-muted">Actions appear here once an appointment is booked.</span>}
            <Link className="aq-btn" to={`${BASE}/feedback`}><Icon name="chat" size={16} />Feedback &amp; support</Link>
          </div>
        </section>
      </div>
      <div className="aq-note" style={{ marginTop: 14 }}><b>Note:</b> prospecting, outreach and responses are handled by EvoSys. You only see appointments booked for you.</div>
    </div>
  )
}

// ── Appointments ────────────────────────────────────────────────────────
export function Appointments() {
  const [d, err] = useLoad('/acquisition/appointments')
  const [tab, setTab] = useState('upcoming')
  const all = d?.all || []
  const lists = {
    upcoming: d?.upcoming || [],
    completed: all.filter(x => ['appointment_held', 'quote_issued', 'won', 'lost'].includes(x.stage)),
    no_show: all.filter(x => x.stage === 'no_show'),
    all,
  }
  return (
    <div className="aq-page">
      <PageHeader eyebrow="Client Portal" title="Appointments" sub="Every appointment EvoSys booked for you, and where each one stands. Each is also on your connected calendar." />
      {err && <div className="aq-err">{errText(err)}</div>}
      <div className="aq-tabs">
        {[['upcoming', `Upcoming (${lists.upcoming.length})`], ['completed', `Completed (${lists.completed.length})`], ['no_show', `No-shows (${lists.no_show.length})`], ['all', `All (${all.length})`]].map(([k, l]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{l}</button>))}
      </div>
      <section className="aq-card">{!d ? <Loading /> : <ApptTable items={lists[tab]} empty="Nothing here yet" />}</section>
    </div>
  )
}
export const Prospects = Appointments

// ── Appointment detail: outcome + follow-up ─────────────────────────────
const NEXT = {
  appointment_booked: [['appointment_held', 'Completed — met the decision-maker'], ['no_show', 'They didn’t show'], ['lost', 'Not a fit / not moving forward']],
  no_show: [['appointment_held', 'We met after all'], ['lost', 'Not moving forward']],
  appointment_held: [['quote_issued', 'Estimate submitted'], ['won', 'Contract won'], ['lost', 'Contract lost / not a fit']],
  quote_issued: [['won', 'Contract won'], ['lost', 'Contract lost']],
}

export function Prospect() {
  const { id } = useParams()
  const { status, reloadCounts } = useAcq()
  const [x, err, reload] = useLoad(`/acquisition/prospects/${id}`)
  const [fu] = useLoad('/acquisition/followups')
  const [pick, setPick] = useState(null)
  const [form, setForm] = useState({})
  const [req, setReq] = useState({ reason: '', note: '' })
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [toast, say] = useToast()
  if (err) return <div className="aq-page"><div className="aq-err">{errText(err)}</div><Link to={`${BASE}/appointments`}>Back to appointments</Link></div>
  if (!x) return <div className="aq-page"><Loading /></div>
  const set = k => e => setForm({ ...form, [k]: e.target.value })
  const run = async (fn, ok) => { setBusy(true); setFail(null); try { await fn(); say(ok); await reload(); reloadCounts() } catch (e) { setFail(errText(e)) } finally { setBusy(false) } }
  const saveOutcome = () => run(async () => { await api.post(`/acquisition/prospects/${id}/outcome`, { outcome: pick, ...form }); setPick(null); setForm({}) }, 'Result saved — EvoSys can see it')
  const sendReq = () => run(async () => { const r = await api.post('/acquisition/followups', { opportunity_id: id, ...req }); setReq({ reason: '', note: '' }); say(r.message) }, null)
  const choices = NEXT[x.stage] || []
  const a = x.appointment || {}
  return (
    <div className="aq-page">{toast}
      <Link to={`${BASE}/appointments`} className="aq-btn aq-btn--sm"><Icon name="back" size={14} />All appointments</Link>
      <PageHeader title={x.business_name} sub={[x.segment_label, x.city].filter(Boolean).join(' · ')} actions={<Status x={x} />} />
      <div className="aq-grid aq-two">
        <section className="aq-card">
          <h2>Appointment</h2>
          <dl className="aq-dl">
            <dt>When</dt><dd>{a.time ? `${when(a.time)}${a.minutes ? ` · ${a.minutes} min` : ''}` : 'Time to be confirmed'}</dd>
            <dt>Type</dt><dd>{a.label || 'Walkthrough / consultation'}</dd>
            <dt>Where</dt><dd>{x.address || 'Shared by EvoSys at booking'}</dd>
            <dt>Service</dt><dd>{x.segment_label || 'Commercial cleaning'}</dd>
            <dt>Contact</dt><dd>{x.contact_name || '—'}{x.contact_title ? `, ${x.contact_title}` : ''}{x.phone && <div><a href={`tel:${x.phone}`}>{x.phone}</a></div>}</dd>
            <dt>Booking</dt><dd><Dot tone={a.status === 'booked' || a.status === 'confirmed' ? 'green' : 'amber'}>{a.status ? (a.status === 'booked' ? 'Confirmed' : a.status) : 'Recorded by EvoSys'}</Dot></dd>
            <dt>Booked</dt><dd>{when(x.booked_at)}</dd>
          </dl>
          <div className="hb-section-title">History</div>
          <Feed items={(x.history || []).map(h => ({ icon: h.kind === 'stage' ? 'check' : 'clock', tone: h.kind === 'stage' ? 'green' : 'blue', title: h.label, sub: h.note || '', at: h.at }))} empty="No history yet." />
        </section>
        <div>
          <section className="aq-card" id="outcome" style={{ marginBottom: 14 }}>
            <h2>Report the outcome</h2>
            <dl className="aq-dl">
              <dt>Estimate</dt><dd>{money(x.quote_amount)}</dd>
              <dt>Monthly value</dt><dd>{money(x.monthly_value)}{x.contract?.confirmed ? ' · confirmed by EvoSys' : x.stage === 'won' ? ' · reported, awaiting EvoSys confirmation' : ''}</dd>
              {x.contract?.contract_type && <><dt>Contract</dt><dd>{x.contract.contract_type.replace('_', ' ')}{x.contract.service_frequency ? ` · ${x.contract.service_frequency}` : ''}{x.contract.contract_start ? ` · starts ${new Date(x.contract.contract_start).toLocaleDateString()}` : ''}</dd></>}
              {x.lost_reason && <><dt>Why not</dt><dd>{x.lost_reason}</dd></>}
              {x.outcome_note && <><dt>Your notes</dt><dd>{x.outcome_note}</dd></>}
            </dl>
            {!status.can_manage ? <p className="hb-muted">Your company administrator records results.</p>
              : choices.length ? <>
                <div className="aq-actions">{choices.map(([k, l]) => <button key={k} className={`aq-btn ${pick === k ? 'aq-btn--primary' : ''}`} onClick={() => { setPick(k); setFail(null) }}>{l}</button>)}</div>
                {pick && <div style={{ marginTop: 12 }}>
                  {pick === 'quote_issued' && <div className="aq-grid aq-two"><div className="aq-field"><label htmlFor="o-q">Estimate amount ($)</label><input id="o-q" inputMode="decimal" value={form.quote_amount || ''} onChange={set('quote_amount')} /></div>
                    <div className="aq-field"><label htmlFor="o-m">Monthly equivalent ($)</label><input id="o-m" inputMode="decimal" value={form.monthly_value || ''} onChange={set('monthly_value')} /></div></div>}
                  {pick === 'won' && <div className="aq-grid aq-two">
                    <div className="aq-field"><label htmlFor="o-ct">Contract type</label><select id="o-ct" value={form.contract_type || ''} onChange={set('contract_type')}><option value="">Choose…</option><option value="recurring">Recurring</option><option value="one_time">One-time</option><option value="trial">Trial</option></select></div>
                    <div className="aq-field"><label htmlFor="o-f">Service frequency</label><input id="o-f" placeholder="e.g. 3x weekly" value={form.service_frequency || ''} onChange={set('service_frequency')} /></div>
                    <div className="aq-field"><label htmlFor="o-v">Total contract value ($)</label><input id="o-v" inputMode="decimal" value={form.contract_value || ''} onChange={set('contract_value')} /></div>
                    <div className="aq-field"><label htmlFor="o-mv">Monthly recurring equivalent ($)</label><input id="o-mv" inputMode="decimal" value={form.monthly_value || ''} onChange={set('monthly_value')} /></div>
                    <div className="aq-field"><label htmlFor="o-s">Start date</label><input id="o-s" type="date" value={form.contract_start || ''} onChange={set('contract_start')} /></div></div>}
                  {pick === 'lost' && <div className="aq-field"><label htmlFor="o-r">Why not? (optional)</label><input id="o-r" value={form.reason || ''} onChange={set('reason')} /></div>}
                  <div className="aq-field"><label htmlFor="o-n">Private notes (optional)</label><textarea id="o-n" value={form.note || ''} onChange={set('note')} /></div>
                  <button className="aq-btn aq-btn--primary" disabled={busy} onClick={saveOutcome}>{busy ? 'Saving…' : 'Save result'}</button>
                </div>}</>
                : <p className="hb-muted">This appointment is closed. Need something? Ask EvoSys below.</p>}
          </section>
          <section className="aq-card" id="followup">
            <h2>Request EvoSys follow-up</h2>
            <p className="hb-muted">Can’t make it, need the business to answer, or want a second visit? EvoSys handles the conversation — nothing is sent automatically.</p>
            {(x.open_followups || []).map(f => <div className="aq-note" key={f.id}><b>{f.reason_label}</b> — {f.status === 'open' ? 'waiting for EvoSys' : 'EvoSys is on it'}</div>)}
            <div className="aq-field"><label htmlFor="f-r">What do you need?</label><select id="f-r" value={req.reason} onChange={e => setReq({ ...req, reason: e.target.value })}>
              <option value="">Choose…</option>{(fu?.reasons || []).map(r => <option key={r.key} value={r.key}>{r.label}</option>)}</select></div>
            <div className="aq-field"><label htmlFor="f-n">Details</label><textarea id="f-n" value={req.note} onChange={e => setReq({ ...req, note: e.target.value })} /></div>
            <button className="aq-btn aq-btn--primary" disabled={busy || !req.reason} onClick={sendReq}>Send to EvoSys</button>
          </section>
          {fail && <div className="aq-err" style={{ marginTop: 10 }}>{fail}</div>}
        </div>
      </div>
    </div>
  )
}

// ── Results (period reporting) ──────────────────────────────────────────
export function Results() {
  const [period, setPeriod] = useState('month')
  const [r, err] = useLoad(`/acquisition/report?period=${period}`)
  const [list] = useLoad('/acquisition/appointments')
  const pct = v => (v == null ? '—' : Math.round(v * 100) + '%')
  const won = (list?.all || []).filter(x => x.stage === 'won')
  return (
    <div className="aq-page">
      <PageHeader eyebrow="Client Portal" title="Results" sub="What EvoSys delivered and what came of it. Each number is a different step, counted from your own records." />
      <Toolbar><div className="aq-tabs" style={{ marginBottom: 0 }}>{PERIODS.map(([k, l]) => <button key={k} className={period === k ? 'on' : ''} onClick={() => setPeriod(k)}>{l}</button>)}</div></Toolbar>
      {err && <div className="aq-err">{errText(err)}</div>}
      {!r ? <Loading /> : <>
        <div className="hb-stats">
          <Stat icon="calendar" label="Appointments delivered" value={r.appointments_booked} sub={`${r.upcoming_now} upcoming now`} />
          <Stat icon="check" tone="green" label="Visits held" value={r.visits_held} sub={`${r.no_shows} no-shows`} />
          <Stat icon="doc" tone="blue" label="Estimates sent" value={r.estimates_sent} />
          <Stat icon="handshake" tone="green" label="Contracts won" value={r.contracts_won} sub={`Close rate ${pct(r.close_rate)} of held visits`} />
          <Stat icon="dollar" tone="violet" label="Reported monthly value" value={money(r.reported_monthly_value)} sub={`${money(r.confirmed_monthly_value)} confirmed by EvoSys`} />
          <Stat icon="clock" tone="amber" label="Days to appointment" value={r.avg_days_to_appointment ?? '—'} sub="Average, from EvoSys start" />
        </div>
        <section className="aq-card" style={{ marginBottom: 14 }}><h2>Delivered by type</h2>
          {r.by_segment.length ? r.by_segment.map(s => <div className="aq-row" key={s.segment}><div className="aq-row__main"><b>{s.label}</b></div><b>{s.count}</b></div>)
            : <p className="hb-muted">No appointments in this period.</p>}
          <p className="hb-muted" style={{ marginBottom: 0 }}>{r.notes}</p></section>
        <section className="aq-card"><h2>Contracts won</h2>
          {won.length ? won.map(x => <div className="aq-row" key={x.id}><div className="aq-row__main"><b>{x.business_name}</b><span>{x.segment_label || ''}</span></div>
            <span className="aq-time">{money(x.monthly_value, '/mo')}</span><Link className="aq-btn aq-btn--sm" to={`${BASE}/appointments/${x.id}`}>Open</Link></div>)
            : <Empty icon="handshake" title="No contracts won yet" />}</section>
      </>}
    </div>
  )
}

// ── Estimates & contracts ───────────────────────────────────────────────
export function Estimates() {
  const [d, err] = useLoad('/acquisition/appointments')
  const all = d?.all || []
  const open = all.filter(x => x.stage === 'quote_issued')
  const won = all.filter(x => x.stage === 'won')
  const total = open.reduce((a, x) => a + (x.monthly_value || 0), 0)
  const row = x => <div className="aq-row" key={x.id}><div className="aq-row__main"><b>{x.business_name}</b><span>{[x.segment_label, x.quote_amount != null ? `Estimate ${money(x.quote_amount)}` : null].filter(Boolean).join(' · ')}</span></div>
    {x.monthly_value != null && <span className="aq-time">{money(x.monthly_value, '/mo')}</span>}<Link className="aq-btn aq-btn--sm" to={`${BASE}/appointments/${x.id}#outcome`}>Update</Link></div>
  return (
    <div className="aq-page">
      <PageHeader eyebrow="Client Portal" title="Estimates & Contracts" sub="Quotes waiting on an answer, and contracts you’ve won. Mark each estimate won or lost when you hear back." />
      {err && <div className="aq-err">{errText(err)}</div>}
      <div className="hb-stats"><Stat icon="doc" label="Open estimates" value={d ? open.length : '—'} /><Stat icon="chart" tone="blue" label="Monthly value quoted (forecast)" value={d ? money(total) : '—'} />
        <Stat icon="handshake" tone="green" label="Contracts won" value={d ? won.length : '—'} /></div>
      <div className="aq-grid aq-two">
        <section className="aq-card"><h2>Open estimates</h2>{!d ? <Loading /> : open.length ? open.map(row) : <Empty icon="doc" title="No open estimates" />}</section>
        <section className="aq-card"><h2>Contracts won</h2>{!d ? <Loading /> : won.length ? won.map(row) : <Empty icon="handshake" title="No contracts won yet" />}</section>
      </div>
    </div>
  )
}

// ── My business ─────────────────────────────────────────────────────────
export function Settings() {
  const { status, reloadStatus } = useAcq()
  const [p, err, reload] = useLoad('/acquisition/profile')
  const [toast, say] = useToast()
  if (err) return <div className="aq-page"><div className="aq-err">{errText(err)}</div></div>
  if (!p) return <div className="aq-page"><Loading /></div>
  return (
    <div className="aq-page">{toast}
      <PageHeader eyebrow="Client Portal" title="My Business" sub="How EvoSys describes you and which businesses we book for you. Only what’s true today — we never claim anything you haven’t confirmed." />
      <Readiness r={p.readiness} />
      <div style={{ marginTop: 14 }}>
        <BusinessForm profile={p} segments={status.segments} canEdit={status.can_manage}
          onSave={async body => { await api.put('/acquisition/profile', body); say('Saved'); await reload(); reloadStatus() }} />
      </div>
    </div>
  )
}

// ── Feedback & support ──────────────────────────────────────────────────
export function Feedback() {
  const [d, err, reload] = useLoad('/acquisition/feedback')
  const [fu, , reloadFu] = useLoad('/acquisition/followups')
  const [body, setBody] = useState('')
  const [rating, setRating] = useState('general')
  const [req, setReq] = useState({ reason: '', note: '' })
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  const [toast, say] = useToast()
  const run = async (fn) => { setBusy(true); setFail(null); try { await fn() } catch (e) { setFail(errText(e)) } finally { setBusy(false) } }
  const send = () => run(async () => { await api.post('/acquisition/feedback', { body, rating }); setBody(''); say('Thanks — we read every note'); reload() })
  const ask = () => run(async () => { const r = await api.post('/acquisition/followups', req); setReq({ reason: '', note: '' }); say(r.message); reloadFu() })
  return (
    <div className="aq-page">{toast}
      <PageHeader eyebrow="Client Portal" title="Feedback & Support" sub="Tell EvoSys what’s working, or ask for help. Requests become a task for the EvoSys team — nothing is sent to a business automatically." />
      {fail && <div className="aq-err">{fail}</div>}
      <div className="aq-grid aq-two">
        <section className="aq-card"><h2>Ask EvoSys</h2>
          <div className="aq-field"><label htmlFor="s-r">What do you need?</label><select id="s-r" value={req.reason} onChange={e => setReq({ ...req, reason: e.target.value })}>
            <option value="">Choose…</option>{(fu?.reasons || []).map(r => <option key={r.key} value={r.key}>{r.label}</option>)}</select></div>
          <div className="aq-field"><label htmlFor="s-n">Details</label><textarea id="s-n" value={req.note} onChange={e => setReq({ ...req, note: e.target.value })} /></div>
          <button className="aq-btn aq-btn--primary" disabled={busy || !req.reason} onClick={ask}>Send request</button>
          <div className="hb-section-title">Your requests</div>
          {!fu ? <Loading /> : fu.items.length ? fu.items.map(f => <div className="aq-row" key={f.id}><div className="aq-row__main"><b>{f.reason_label}{f.business_name ? ` · ${f.business_name}` : ''}</b>
            <span>{when(f.created_at)}{f.resolution_note ? ` · ${f.resolution_note}` : ''}</span></div><Dot tone={f.status === 'done' ? 'green' : f.status === 'closed' ? 'gray' : 'blue'}>{f.status.replace('_', ' ')}</Dot></div>)
            : <p className="hb-muted">No requests yet.</p>}
        </section>
        <section className="aq-card"><h2>Feedback on appointments</h2>
          <div className="aq-tabs">{[['general', 'General'], ['good_fit', 'Good fit'], ['poor_fit', 'Not a good fit']].map(([k, l]) => (
            <button key={k} className={rating === k ? 'on' : ''} onClick={() => setRating(k)}>{l}</button>))}</div>
          <div className="aq-field"><label htmlFor="aq-fb">Your note</label><textarea id="aq-fb" value={body} onChange={e => setBody(e.target.value)} /></div>
          <button className="aq-btn aq-btn--primary" disabled={busy || !body.trim()} onClick={send}>Send</button>
          <div className="hb-section-title">What you’ve told us</div>
          {err && <div className="aq-err">{errText(err)}</div>}
          {!d ? <Loading /> : d.items.length ? d.items.map(f => (
            <div className="aq-row" key={f.id}><div className="aq-row__main"><b>{f.body}</b><span>{when(f.at)} · {f.rating.replace('_', ' ')}</span></div></div>
          )) : <p className="hb-muted">No notes yet.</p>}
        </section>
      </div>
    </div>
  )
}
