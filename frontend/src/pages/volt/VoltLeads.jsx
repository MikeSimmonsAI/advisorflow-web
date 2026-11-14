// VOLT Consumer Leads - a campaign inside VOLT Marketing (separate from
// recruiting and from HIP).
//   CoverageFunnel      public, one-question-at-a-time eligibility page (/volt/check-coverage)
//   ConsumerLeadsCard   campaign card on the VOLT Operations overview (counts only)
//   LeadsDashboard      the internal dashboard (Consumer leads tab)
// Visual/interaction references: docs/volt/design/volt_leads_v3.html and
// volt_leads_backend_demo.html. All access rules are enforced by the server
// (app/services/volt/leads.py); this page only reflects them.
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { volt, useVoltTitle } from './voltApi'
import './volt.css'

const msg = (e) => e?.message || 'Something went wrong.'
// Stored in UTC; shown in Central time with CST/CDT.
export const fmtCT = (iso) => (iso ? new Date(iso).toLocaleString('en-US', {
  timeZone: 'America/Chicago', month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit',
  timeZoneName: 'short',
}) : '—')
const NAVY = '#163B60'
const BLUE = '#DCEEF8'
const GREEN = '#237A61'
const big = { fontSize: 18, lineHeight: 1.55 }
const choice = (on) => ({ display: 'block', margin: '10px 0', padding: '14px 16px', border: `2px solid ${on ? NAVY : '#c9d6e2'}`,
  borderRadius: 12, background: on ? BLUE : '#fff', fontSize: 20, cursor: 'pointer', minHeight: 52 })
const field = { fontSize: 20, minHeight: 52, padding: '10px 14px', width: '100%', borderRadius: 10, border: '2px solid #9fb3c8' }

export const publicSiteUrl = () => {
  const h = window.location.hostname
  return h.endsWith('voltmarketing.live') || h === 'localhost' || h === '127.0.0.1'
    ? '/volt/check-coverage' : 'https://voltmarketing.live/volt/check-coverage'
}

const INCOME = [['under_20k', 'Under $20,000'], ['20k_35k', '$20,000 – $35,000'], ['35k_50k', '$35,000 – $50,000'],
  ['50k_75k', '$50,000 – $75,000'], ['75k_100k', '$75,000 – $100,000'], ['over_100k', 'Over $100,000'], ['prefer_not', 'I’d rather not say']]

const QUESTIONS = [
  { key: 'zip', q: 'What is your ZIP code?', hint: 'It helps us look at plans available where you live.', type: 'zip' },
  { key: 'state', q: 'Which state do you live in?', hint: 'Plans and help are different in each state.', type: 'state' },
  { key: 'age_range', q: 'What is your age range?', hint: 'Choose one answer.',
    options: [['18-25', '18 to 25'], ['26-44', '26 to 44'], ['45-64', '45 to 64'], ['65+', '65 or older']] },
  { key: 'coverage_status', q: 'Do you have health insurance right now?', hint: 'Choose the answer that fits best.',
    options: [['none', 'No, I need coverage'], ['have_compare', 'Yes, but I want to compare'], ['not_sure', 'I’m not sure']] },
  { key: 'household_size', q: 'How many people live in your household?', hint: 'Count yourself. Help with costs depends on household size.',
    options: [['1', '1'], ['2', '2'], ['3', '3'], ['4', '4'], ['5', '5 or more']] },
  { key: 'income_range', q: 'About how much does your household earn in a year?', hint: 'An estimate is fine. Help with costs depends on income.', options: INCOME },
  { key: 'contact_preference', q: 'How would you like a licensed agent to reach you?', hint: 'Choose one, and a time of day that works.', type: 'pref' },
  { key: 'contact', q: 'Where can the agent reach you?', hint: 'We use this only to follow up on your request.', type: 'contact' },
]

export function CoverageFunnel() {
  useVoltTitle()
  const [cfg, setCfg] = useState(null)
  const [started, setStarted] = useState(false)
  const [step, setStep] = useState(0)
  const [a, setA] = useState({ consent: false })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const head = useRef(null)
  useEffect(() => { volt.leadConfig().then(setCfg).catch(() => setCfg({ enabled: false })) }, [])
  useEffect(() => { head.current?.focus() }, [step, done, started])
  const set = (k, v) => { setA((x) => ({ ...x, [k]: v })); setErr('') }
  const params = new URLSearchParams(window.location.search)
  const q = QUESTIONS[step]
  const page = cfg?.page

  const valid = () => {
    if (q.type === 'zip') return /^\d{5}$/.test((a.zip || '').trim()) || 'Please enter a 5-digit ZIP code.'
    if (q.type === 'state') return !!a.state || 'Please choose your state.'
    if (q.type === 'pref') return (!!a.contact_preference && !!a.best_time) || 'Please choose how and when to be contacted.'
    if (q.type === 'contact') {
      if (!(a.first_name || '').trim()) return 'Please enter your first name.'
      if (a.contact_preference !== 'email' && !(a.phone || '').trim()) return 'Please enter your phone number.'
      if (a.contact_preference === 'email' && !(a.email || '').trim()) return 'Please enter your email address.'
      if (!a.consent) return 'Please agree to be contacted so an agent can help you.'
      return true
    }
    return a[q.key] !== undefined || 'Please choose an answer.'
  }
  const next = async (e) => {
    e?.preventDefault()
    const v = valid()
    if (v !== true) { setErr(v); return }
    if (step < QUESTIONS.length - 1) { setStep(step + 1); return }
    setBusy(true)
    let referrer = ''
    try { referrer = document.referrer ? new URL(document.referrer).hostname : '' } catch { referrer = '' }
    try {
      await volt.submitLead({
        first_name: a.first_name, last_name: a.last_name, zip: a.zip, state: a.state, phone: a.phone, email: a.email,
        age_range: a.age_range, coverage_status: a.coverage_status, household_size: a.household_size,
        income_range: a.income_range, contact_preference: a.contact_preference, best_time: a.best_time,
        consent: true, consent_version: cfg.consent_version,
        utm_source: params.get('utm_source') || '', utm_medium: params.get('utm_medium') || '',
        utm_campaign: params.get('utm_campaign') || '', utm_content: params.get('utm_content') || '',
        utm_term: params.get('utm_term') || '', referrer_host: referrer, landing_path: window.location.pathname,
      })
      setDone(true)
    } catch (x) { setErr(msg(x)) } finally { setBusy(false) }
  }

  return (
    <div className="volt" style={{ background: '#fff', minHeight: '100vh' }}>
      <main className="v-wrap" style={{ maxWidth: 720, padding: '24px 18px 48px', color: NAVY, ...big }} data-funnel>
        {!cfg && <p role="status">Loading…</p>}
        {cfg && !cfg.enabled && <div className="v-card"><h1 tabIndex={-1} ref={head}>This form is not open yet</h1>
          <p>We are not taking requests online right now. Please check back soon.</p><Link to="/volt">Back to VOLT Marketing</Link></div>}
        {cfg?.enabled && done && <div className="v-card" role="status" style={{ borderColor: GREEN }}><h1 tabIndex={-1} ref={head}>Thank you.</h1>
          <p>Your request was received. A licensed agent may contact you using the details you gave.</p></div>}
        {cfg?.enabled && !done && !started && page && (
          <section aria-labelledby="lead-h" data-funnel-intro>
            <p className="v-eyebrow" style={{ color: GREEN }}>Free to check · No obligation · No account needed</p>
            <h1 id="lead-h" tabIndex={-1} ref={head} style={{ fontSize: 36, lineHeight: 1.15, color: NAVY }}>{page.headline}</h1>
            <p style={{ fontSize: 20 }}>{page.subhead}</p>
            <button type="button" className="v-btn" style={{ background: NAVY, color: '#fff', fontSize: 20, padding: '16px 28px', borderRadius: 12 }}
              onClick={() => setStarted(true)} data-funnel-start>Check if I qualify →</button>
            <ul style={{ listStyle: 'none', padding: 0, margin: '24px 0', display: 'grid', gap: 12 }}>
              {page.benefits.map(([t, d]) => <li key={t} style={{ background: BLUE, borderRadius: 12, padding: '14px 16px' }}><b>{t}.</b> {d}</li>)}
            </ul>
            <div className="v-small" style={{ fontSize: 16 }} data-funnel-disclaimers><b>Please know:</b>
              <ul>{page.disclaimers.map((d) => <li key={d}>{d}</li>)}</ul></div>
          </section>)}
        {cfg?.enabled && !done && started && (
          <form className="v-card" onSubmit={next} noValidate aria-labelledby="lead-q" style={{ borderRadius: 16 }}>
            <p aria-live="polite" style={{ margin: 0 }}>Question {step + 1} of {QUESTIONS.length}</p>
            <progress max={QUESTIONS.length} value={step + 1} aria-label="Progress" style={{ width: '100%', height: 12, accentColor: NAVY }} />
            <h1 id="lead-q" tabIndex={-1} ref={head} style={{ fontSize: 28, color: NAVY }}>{q.q}</h1>
            <p style={{ fontSize: 18 }}>{q.hint}</p>
            {q.type === 'zip' && <label>ZIP code
              <input style={field} inputMode="numeric" autoComplete="postal-code" maxLength={5} value={a.zip || ''} aria-invalid={!!err}
                aria-describedby="lead-err" onChange={(e) => set('zip', e.target.value.replace(/\D/g, ''))} /></label>}
            {q.type === 'state' && <label>State
              <select style={field} value={a.state || ''} onChange={(e) => set('state', e.target.value)} aria-describedby="lead-err">
                <option value="">Choose your state</option>{cfg.states.map((s) => <option key={s} value={s}>{s}</option>)}</select></label>}
            {q.options && <fieldset style={{ border: 0, padding: 0 }}><legend className="v-sr">{q.q}</legend>
              {q.options.map(([val, label]) => (
                <label key={val} style={choice(a[q.key] === val)}>
                  <input type="radio" name={q.key} checked={a[q.key] === val} onChange={() => set(q.key, val)} /> {label}</label>))}
            </fieldset>}
            {q.key === 'age_range' && a.age_range === '65+' && <p role="note" style={{ background: BLUE, padding: 12, borderRadius: 10 }} data-medicare-note>{page.medicare_note}</p>}
            {q.type === 'pref' && <>
              <fieldset style={{ border: 0, padding: 0 }}><legend>Contact me by</legend>
                {[['call', 'Phone call'], ['text', 'Text message'], ['email', 'Email']].map(([v, l]) => (
                  <label key={v} style={choice(a.contact_preference === v)}><input type="radio" name="contact_preference"
                    checked={a.contact_preference === v} onChange={() => set('contact_preference', v)} /> {l}</label>))}</fieldset>
              <fieldset style={{ border: 0, padding: 0 }}><legend>Best time</legend>
                {[['morning', 'Morning'], ['afternoon', 'Afternoon'], ['evening', 'Evening'], ['any', 'Any time']].map(([v, l]) => (
                  <label key={v} style={choice(a.best_time === v)}><input type="radio" name="best_time"
                    checked={a.best_time === v} onChange={() => set('best_time', v)} /> {l}</label>))}</fieldset>
            </>}
            {q.type === 'contact' && <>
              <label>First name<input style={field} autoComplete="given-name" value={a.first_name || ''} onChange={(e) => set('first_name', e.target.value)} /></label>
              <label>Last name<input style={field} autoComplete="family-name" value={a.last_name || ''} onChange={(e) => set('last_name', e.target.value)} /></label>
              <label>Phone{a.contact_preference === 'email' ? ' (optional)' : ''}<input style={field} type="tel" autoComplete="tel" value={a.phone || ''} onChange={(e) => set('phone', e.target.value)} /></label>
              <label>Email{a.contact_preference === 'email' ? '' : ' (optional)'}<input style={field} type="email" autoComplete="email" value={a.email || ''} onChange={(e) => set('email', e.target.value)} /></label>
              <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', margin: '14px 0', fontSize: 17 }}>
                <input type="checkbox" style={{ width: 24, height: 24, flex: 'none' }} checked={a.consent} onChange={(e) => set('consent', e.target.checked)} />
                <span>{cfg.consent_text}</span></label>
            </>}
            <div id="lead-err" role="alert" className={err ? 'v-alert v-alert--err' : ''}>{err}</div>
            <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
              <button type="button" className="v-btn v-btn--line" style={{ fontSize: 18, minHeight: 52 }}
                onClick={() => { setErr(''); if (step === 0) setStarted(false); else setStep(step - 1) }}>← Back</button>
              <button type="submit" className="v-btn" style={{ background: NAVY, color: '#fff', fontSize: 18, minHeight: 52, flex: 1 }} disabled={busy}>
                {step === QUESTIONS.length - 1 ? (busy ? 'Sending…' : 'Send my request') : 'Continue →'}</button>
            </div>
          </form>)}
        {cfg?.enabled && page && started && !done && <p style={{ fontSize: 15, marginTop: 18 }}>{page.disclaimers.join(' ')}</p>}
      </main>
    </div>
  )
}

// ── campaign card (VOLT Operations overview) ─────────────────────────────────
export function ConsumerLeadsCard({ orgId }) {
  const [s, setS] = useState(null)
  const [hidden, setHidden] = useState(false)
  useEffect(() => { api.get(`/volt/ops/${orgId}/leads/summary`).then(setS).catch(() => setHidden(true)) }, [orgId])
  if (hidden) return null
  const nums = s ? [['total', 'Total leads'], ['new', 'New'], ['contacted', 'Contacted'], ['qualified', 'Qualified'],
    ['appointments', 'Appointments'], ['enrollments', 'Enrollments']] : []
  return (
    <section className="v-panel" data-campaign-card="consumer-leads" style={{ borderLeft: `6px solid ${NAVY}` }}>
      <div className="v-row"><div><h3 style={{ margin: 0 }}>Consumer Leads</h3>
        <p className="v-small v-muted" style={{ margin: 0 }}>Health Insurance Lead Acquisition &amp; Enrollment · a VOLT Marketing campaign</p></div>
        {s && <span className="v-tag">{s.intake_enabled ? 'Public intake ON' : 'Public intake OFF'}</span>}</div>
      {!s ? <p className="v-mono">Loading…</p> : <>
        <div className="v-kpis" style={{ marginTop: 10 }}>
          {nums.map(([k, l]) => <div key={k} className="v-kpi" data-card-kpi={k}><b>{s[k]}</b><span>{l}</span></div>)}
        </div>
        <p className="v-small v-muted">Last activity: {fmtCT(s.last_activity_at)}{s.access === 'assigned' ? ' · counts include only leads assigned to you' : ''}</p>
        <div className="v-row" style={{ justifyContent: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
          <Link className="v-btn v-btn--red v-btn--sm" to={`/volt-ops/${orgId}/leads`} data-card-open>Open Dashboard</Link>
          <a className="v-btn v-btn--line v-btn--sm" href={publicSiteUrl()} target="_blank" rel="noreferrer" data-card-site>View Customer Website ↗</a>
          {s.can_develop && <Link className="v-btn v-btn--line v-btn--sm" to={`/volt-ops/${orgId}/dev?campaign=consumer_leads`} data-card-dev>Development</Link>}
          {s.can_settings && <Link className="v-btn v-btn--line v-btn--sm" to={`/volt-ops/${orgId}/leads#settings`} data-card-settings>Campaign Settings</Link>}
        </div></>}
    </section>
  )
}

// ── internal dashboard ───────────────────────────────────────────────────────
const STATUSES = [['new', 'New'], ['contacted', 'Contacted'], ['qualified', 'Qualified'], ['appointment_set', 'Appointment Set'], ['enrolled', 'Enrolled'], ['not_interested', 'Not Interested']]
const CHANNELS = [['meta', 'Meta / Facebook'], ['google', 'Google'], ['organic', 'Organic search'], ['direct', 'Direct'], ['referral', 'Referral'], ['other', 'Other']]

export function LeadsDashboard({ orgId }) {
  const base = `/volt/ops/${orgId}/leads`
  const navigate = useNavigate()
  const [f, setF] = useState({ q: '', status: '', channel: '' })
  const [data, setData] = useState(null)
  const [report, setReport] = useState(null)
  const [sel, setSel] = useState(null)
  const [agents, setAgents] = useState([])
  const [settings, setSettings] = useState(null)
  const [note, setNote] = useState('')
  const [appt, setAppt] = useState('')
  const [err, setErr] = useState('')
  const [summary, setSummary] = useState(null)
  const seq = useRef(0)
  const load = () => {
    const n = ++seq.current
    const params = Object.fromEntries(Object.entries(f).filter(([, v]) => v))
    api.get(base, { params }).then((x) => { if (n === seq.current) setData(x) }).catch((e) => setErr(msg(e)))
    api.get(`${base}/report`).then(setReport).catch(() => {})
    api.get(`${base}/summary`).then(setSummary).catch(() => {})
  }
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t) }, [orgId, JSON.stringify(f)]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.get(`${base}/agents`).then((x) => setAgents(x.agents)).catch(() => {}) }, [orgId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.get(`${base}/settings`).then(setSettings).catch(() => setSettings(null)) }, [orgId]) // eslint-disable-line react-hooks/exhaustive-deps
  const open = (id) => api.get(`${base}/${id}`).then((x) => { setSel(x); setAppt('') }).catch((e) => setErr(msg(e)))
  const act = (p) => p.then((x) => { setSel(x); setNote(''); setErr(''); load() }).catch((e) => setErr(msg(e)))
  const toggleIntake = () => {
    const enable = !settings.intake_enabled
    const review_note = enable ? window.prompt('Who approved the privacy copy, eligibility claims, consent and agent routing? (required)') : ''
    if (enable && !review_note) return
    api.put(`${base}/settings`, { intake_enabled: enable, reviewed: enable, review_note })
      .then((s) => setSettings((x) => ({ ...x, ...s }))).catch((e) => setErr(msg(e)))
  }
  const grant = (user_id, active) => api.post(`${base}/grants`, { user_id, active })
    .then((r) => setSettings((x) => ({ ...x, grants: r.grants }))).catch((e) => setErr(msg(e)))
  const canAssign = data?.access === 'all'
  const granted = new Set((settings?.grants || []).map((g) => g.user_id))

  return (
    <section aria-labelledby="leads-h" data-leads-dashboard>
      <div className="v-row"><div><h2 id="leads-h" style={{ fontSize: 30, letterSpacing: '-.04em', marginBottom: 0 }}>Consumer Leads</h2>
        <p className="v-small v-muted" style={{ marginTop: 0 }}>Health Insurance Lead Acquisition &amp; Enrollment · VOLT Marketing campaign · {data?.access === 'all' ? 'you see every lead' : 'you see only leads assigned to you'}</p></div>
        <div className="v-row" style={{ gap: 8 }}>
          <a className="v-btn v-btn--line v-btn--sm" href={publicSiteUrl()} target="_blank" rel="noreferrer">Customer website ↗</a>
          {summary?.can_develop && <button type="button" className="v-btn v-btn--black v-btn--sm" onClick={() => navigate(`/volt-ops/${orgId}/dev?campaign=consumer_leads`)} data-leads-dev>Development</button>}
        </div></div>
      {err && <div className="v-alert v-alert--err" role="alert">{err}</div>}
      {report && <div className="v-kpis" aria-label="Lead totals">
        <div className="v-kpi"><b>{report.total}</b><span>Total leads</span></div>
        {STATUSES.map(([k, l]) => <button type="button" className="v-kpi" key={k} onClick={() => setF({ ...f, status: f.status === k ? '' : k })}><b>{report.by_status[k] || 0}</b><span>{l}</span></button>)}
      </div>}
      {report && <p className="v-small v-muted">Last activity {fmtCT(report.last_activity_at)} · {report.appointments} appointment(s) · {report.enrollments} enrollment(s)</p>}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', margin: '14px 0' }}>
        <label>Search<input value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} placeholder="Name, phone, email or ZIP" /></label>
        <label>Status<select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">All statuses</option>
          {STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label>Channel<select value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}><option value="">All channels</option>
          {CHANNELS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
      </div>
      <div className="v-scroll"><table className="v-table" data-table="leads"><thead><tr><th>Lead</th><th>ZIP / State</th><th>Channel</th><th>Assigned to</th><th>Status</th><th>Received</th><th><span className="v-sr">Open</span></th></tr></thead>
        <tbody>{(data?.leads || []).map((l) => (
          <tr key={l.id}><td>{l.name}<br /><span className="v-small">{l.phone}</span></td><td>{l.zip}{l.state ? ` · ${l.state}` : ''}</td><td>{l.channel}</td>
            <td>{l.assigned_to || 'Unassigned'}</td><td>{l.status_label}{l.appointment_at ? <><br /><span className="v-small">Appt {fmtCT(l.appointment_at)}</span></> : null}</td>
            <td className="v-small">{fmtCT(l.created_at)}</td>
            <td><button type="button" className="v-btn v-btn--link" onClick={() => open(l.id)} aria-label={`Open ${l.name}`}>View</button></td></tr>))}
          {data && !data.leads.length && <tr><td colSpan={7}>No matching leads.</td></tr>}</tbody></table></div>
      {sel && <aside className="v-card" aria-label="Selected lead" style={{ marginTop: 16 }} data-lead-detail>
        <h3>{sel.name}</h3>
        <p className="v-small">Phone {sel.phone || '—'} · Email {sel.email || '—'} · ZIP {sel.zip}{sel.state ? ` · ${sel.state}` : ''} · Age {sel.age_range} · Household {sel.household_size || '—'} · Income {sel.income_range || '—'}<br />
          Prefers {sel.contact_preference || '—'}{sel.best_time ? ` (${sel.best_time})` : ''} · Coverage now: {sel.coverage_status}<br />
          Channel {sel.channel}{sel.utm?.utm_campaign ? ` · campaign ${sel.utm.utm_campaign}` : ''} · Consent {sel.consent_version} at {fmtCT(sel.consent_at)} · {sel.submissions} submission(s)</p>
        <label>Status<select value={sel.status} onChange={(e) => act(api.patch(`${base}/${sel.id}`, { status: e.target.value }))} data-lead-status>
          {STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        {canAssign && <label>Assign to<select value={sel.assigned_user_id || ''} onChange={(e) => act(api.post(`${base}/${sel.id}/assign`, { user_id: e.target.value || null }))} data-lead-assign>
          <option value="">Unassigned</option>{agents.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>}
        <div className="v-row" style={{ justifyContent: 'flex-start', gap: 8, flexWrap: 'wrap', margin: '10px 0' }}>
          <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => act(api.patch(`${base}/${sel.id}`, { event: 'call_attempted', note }))} data-log="call_attempted">Log call attempt</button>
          <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => act(api.patch(`${base}/${sel.id}`, { event: 'contact_completed', note }))} data-log="contact_completed">Contact made</button>
          <label className="v-small">Appointment<input type="datetime-local" value={appt} onChange={(e) => setAppt(e.target.value)} data-appt-input /></label>
          <button type="button" className="v-btn v-btn--line v-btn--sm" disabled={!appt}
            onClick={() => act(api.patch(`${base}/${sel.id}`, { event: 'appointment_set', appointment_at: new Date(appt).toISOString(), note }))} data-log="appointment_set">{sel.appointment_at ? 'Change appointment' : 'Set appointment'}</button>
          <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => act(api.patch(`${base}/${sel.id}`, { event: 'enrollment_recorded', note }))} data-log="enrollment_recorded">Record enrollment</button>
        </div>
        <label>Note (added to the next action, or saved on its own)<input value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} data-lead-note /></label>
        <button type="button" className="v-btn v-btn--red v-btn--sm" disabled={!note.trim()} onClick={() => act(api.patch(`${base}/${sel.id}`, { note }))}>Save note</button>
        <h4>History (Central time)</h4><ul data-lead-history>{sel.history.map((h) => <li key={h.id} className="v-small">
          <b>{fmtCT(h.at)}</b> · {h.label}{h.old || h.new ? `: ${h.old ? `${h.old} → ` : ''}${h.new || ''}` : ''}{h.note ? ` — ${h.note}` : ''} · {h.by}</li>)}</ul>
        <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setSel(null)}>Close</button>
      </aside>}
      {report && <section className="v-panel" data-leads-report>
        <h3>Campaign reporting</h3>
        <div className="v-scroll"><table className="v-table"><thead><tr><th>Channel</th><th>Leads</th><th>Qualified</th><th>Appointments</th><th>Enrolled</th></tr></thead>
          <tbody>{report.by_channel.length === 0 ? <tr><td colSpan={5} className="v-muted">No leads yet.</td></tr>
            : report.by_channel.map((c) => <tr key={c.channel}><td>{c.label}</td><td>{c.leads}</td><td>{c.qualified}</td><td>{c.appointments}</td><td>{c.enrolled}</td></tr>)}</tbody></table></div>
        <h4>Agents</h4>
        <div className="v-scroll"><table className="v-table"><thead><tr><th>Agent</th><th>Assigned</th><th>Enrolled</th><th>Conversion</th></tr></thead>
          <tbody>{report.agents.length === 0 ? <tr><td colSpan={4} className="v-muted">No assigned leads yet.</td></tr>
            : report.agents.map((x) => <tr key={x.name}><td>{x.name}</td><td>{x.assigned}</td><td>{x.enrolled}</td><td>{x.conversion_pct == null ? '—' : `${x.conversion_pct}%`}</td></tr>)}</tbody></table></div>
        <p className="v-small v-muted">{report.cost_note}</p>
      </section>}
      {settings && <section className="v-panel" id="settings" data-leads-settings>
        <h3>Campaign settings</h3>
        <p><strong>Public intake: {settings.intake_enabled ? 'ON' : 'OFF'}</strong>{settings.reviewed_at ? ` · last approved ${fmtCT(settings.reviewed_at)}` : ''}</p>
        <p className="v-small">Before turning intake on, the owner confirms:</p>
        <ul className="v-small">{settings.checklist.map((c) => <li key={c}>{c}</li>)}</ul>
        <button type="button" className="v-btn v-btn--black v-btn--sm" onClick={toggleIntake}>{settings.intake_enabled ? 'Turn intake off' : 'Turn intake on'}</button>
        <h4>Who can see every lead</h4>
        <p className="v-small v-muted">The owner and VOLT&apos;s two authorized operators always can. Others see only leads assigned to them unless granted here.</p>
        <ul className="v-small">{(settings.assignable || []).map((p) => <li key={p.id}>{p.name} · {granted.has(p.id)
          ? <button type="button" className="v-btn v-btn--link" onClick={() => grant(p.id, false)}>Remove full access</button>
          : <button type="button" className="v-btn v-btn--link" onClick={() => grant(p.id, true)}>Give full access</button>}</li>)}</ul>
      </section>}
    </section>
  )
}
