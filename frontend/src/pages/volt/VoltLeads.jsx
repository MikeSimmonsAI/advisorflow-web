// VOLT consumer leads (DEV-4): the public one-question-at-a-time eligibility
// funnel (/volt/check-coverage) and the agent dashboard (Consumer leads tab).
// Visual/interaction references: docs/volt/design/volt_leads_v3.html and
// volt_leads_backend_demo.html (kept out of the app build). Brand stays VOLT.
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { volt, useVoltTitle } from './voltApi'
import './volt.css'

const msg = (e) => e?.message || 'Something went wrong.'

const QUESTIONS = [
  { key: 'zip', q: 'First, where do you live?', hint: 'Your ZIP code helps us look at options in your area.', type: 'zip' },
  { key: 'age_range', q: 'What age range are you in?', hint: 'Choose one answer.',
    options: [['18-25', '18–25'], ['26-44', '26–44'], ['45-64', '45–64'], ['65+', '65 or older']] },
  { key: 'coverage_status', q: 'Do you currently have health insurance?', hint: 'Choose the answer that best describes you.',
    options: [['none', 'No, I need coverage'], ['have_compare', 'Yes, but I want to compare'], ['not_sure', 'I’m not sure']] },
  { key: 'household_size', q: 'How many people are in your household?', hint: 'Include yourself. Savings depend on household size.',
    options: [['1', '1'], ['2', '2'], ['3', '3'], ['4', '4'], ['5', '5 or more']] },
  { key: 'contact', q: 'How can a licensed agent reach you?', hint: 'We use this only to follow up on your request.', type: 'contact' },
]

export function CoverageFunnel() {
  useVoltTitle()
  const [cfg, setCfg] = useState(null)
  const [step, setStep] = useState(0)
  const [a, setA] = useState({ consent: false })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const head = useRef(null)
  useEffect(() => { volt.leadConfig().then(setCfg).catch(() => setCfg({ enabled: false })) }, [])
  useEffect(() => { head.current?.focus() }, [step, done])
  const set = (k, v) => { setA((x) => ({ ...x, [k]: v })); setErr('') }
  const params = new URLSearchParams(window.location.search)
  const q = QUESTIONS[step]

  const valid = () => {
    if (q.type === 'zip') return /^\d{5}$/.test((a.zip || '').trim()) || 'Please enter a valid 5-digit ZIP code.'
    if (q.type === 'contact') {
      if (!(a.first_name || '').trim()) return 'Please enter your first name.'
      if (!(a.phone || '').trim() && !(a.email || '').trim()) return 'Please enter a phone number or an email address.'
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
    try {
      await volt.submitLead({
        first_name: a.first_name, last_name: a.last_name, zip: a.zip, phone: a.phone, email: a.email,
        age_range: a.age_range, coverage_status: a.coverage_status, household_size: a.household_size,
        consent: true, consent_version: cfg.consent_version, source: params.get('utm_source') || 'direct',
        campaign: params.get('utm_campaign') || '',
      })
      setDone(true)
    } catch (x) { setErr(msg(x)) } finally { setBusy(false) }
  }

  return (
    <div className="volt"><main className="v-wrap" style={{ maxWidth: 680, padding: '28px 18px' }}>
      <p className="v-eyebrow">Free to check · No obligation</p>
      {!cfg && <p role="status">Loading…</p>}
      {cfg && !cfg.enabled && <div className="v-card"><h1 tabIndex={-1} ref={head}>This form is not open yet</h1>
        <p>We are not taking requests online right now. Please check back soon.</p><Link to="/volt">Back to VOLT Marketing</Link></div>}
      {cfg?.enabled && done && <div className="v-card" role="status"><h1 tabIndex={-1} ref={head}>Thank you.</h1>
        <p>Your request was received. A licensed agent may contact you using the details you gave.</p></div>}
      {cfg?.enabled && !done && (
        <form className="v-card" onSubmit={next} noValidate aria-labelledby="lead-q">
          <p aria-live="polite">Question {step + 1} of {QUESTIONS.length}</p>
          <progress max={QUESTIONS.length} value={step + 1} aria-label="Progress" style={{ width: '100%' }} />
          <h1 id="lead-q" tabIndex={-1} ref={head} style={{ fontSize: 28 }}>{q.q}</h1>
          <p className="v-small">{q.hint}</p>
          {q.type === 'zip' && <label>Your ZIP code
            <input inputMode="numeric" autoComplete="postal-code" maxLength={5} value={a.zip || ''} aria-invalid={!!err}
              aria-describedby="lead-err" onChange={(e) => set('zip', e.target.value.replace(/\D/g, ''))} /></label>}
          {q.options && <fieldset style={{ border: 0, padding: 0 }}><legend className="v-small">Choose one answer.</legend>
            {q.options.map(([val, label]) => (
              <label key={val} style={{ display: 'block', margin: '8px 0' }}>
                <input type="radio" name={q.key} checked={a[q.key] === val} onChange={() => set(q.key, val)} /> {label}</label>))}
          </fieldset>}
          {q.type === 'contact' && <>
            <label>First name<input autoComplete="given-name" value={a.first_name || ''} onChange={(e) => set('first_name', e.target.value)} /></label>
            <label>Last name (optional)<input autoComplete="family-name" value={a.last_name || ''} onChange={(e) => set('last_name', e.target.value)} /></label>
            <label>Phone<input type="tel" autoComplete="tel" value={a.phone || ''} onChange={(e) => set('phone', e.target.value)} /></label>
            <label>Email (optional if you gave a phone)<input type="email" autoComplete="email" value={a.email || ''} onChange={(e) => set('email', e.target.value)} /></label>
            <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', margin: '12px 0' }}>
              <input type="checkbox" checked={a.consent} onChange={(e) => set('consent', e.target.checked)} />
              <span>{cfg.consent_text}</span></label>
          </>}
          <div id="lead-err" role="alert" className={err ? 'v-alert v-alert--err' : ''}>{err}</div>
          <div style={{ display: 'flex', gap: 10, marginTop: 14 }}>
            {step > 0 && <button type="button" className="v-btn v-btn--line" onClick={() => { setErr(''); setStep(step - 1) }}>Back</button>}
            <button type="submit" className="v-btn v-btn--red" disabled={busy}>{step === QUESTIONS.length - 1 ? (busy ? 'Sending…' : 'Send my request') : 'Continue →'}</button>
          </div>
        </form>)}
      {cfg?.enabled && <p className="v-small" style={{ marginTop: 16 }}>{cfg.eligibility_caveat}</p>}
    </main></div>
  )
}

const STATUSES = [['new', 'New'], ['contacted', 'Contacted'], ['qualified', 'Qualified'], ['appointment_set', 'Appointment Set'], ['enrolled', 'Enrolled'], ['not_interested', 'Not Interested']]
const SOURCES = [['facebook', 'Facebook Ads'], ['google', 'Google Ads'], ['direct', 'Direct'], ['other', 'Other']]

export function LeadsDashboard({ orgId }) {
  const base = `/volt/ops/${orgId}/leads`
  const [f, setF] = useState({ q: '', status: '', source: '' })
  const [data, setData] = useState(null)
  const [report, setReport] = useState(null)
  const [sel, setSel] = useState(null)
  const [agents, setAgents] = useState([])
  const [settings, setSettings] = useState(null)
  const [note, setNote] = useState('')
  const [err, setErr] = useState('')
  const seq = useRef(0)
  const load = () => {
    const n = ++seq.current
    const params = Object.fromEntries(Object.entries(f).filter(([, v]) => v))
    api.get(base, { params }).then((x) => { if (n === seq.current) setData(x) }).catch((e) => setErr(msg(e)))
    api.get(`${base}/report`).then(setReport).catch(() => {})
  }
  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t) }, [orgId, JSON.stringify(f)]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.get(`${base}/agents`).then((x) => setAgents(x.agents)).catch(() => {}) }, [orgId]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { api.get(`${base}/settings`).then(setSettings).catch(() => setSettings(null)) }, [orgId]) // eslint-disable-line react-hooks/exhaustive-deps
  const open = (id) => api.get(`${base}/${id}`).then(setSel).catch((e) => setErr(msg(e)))
  const act = (p) => p.then((x) => { setSel(x); setNote(''); load() }).catch((e) => setErr(msg(e)))
  const toggleIntake = () => {
    const enable = !settings.intake_enabled
    const review_note = enable ? window.prompt('Who approved the privacy copy, eligibility claims, consent and agent routing? (required)') : ''
    if (enable && !review_note) return
    api.put(`${base}/settings`, { intake_enabled: enable, reviewed: enable, review_note })
      .then((s) => setSettings((x) => ({ ...x, ...s }))).catch((e) => setErr(msg(e)))
  }
  const canAssign = data?.access === 'all'

  return (
    <section aria-labelledby="leads-h">
      <h2 id="leads-h">Consumer leads</h2>
      <p className="v-small">{data?.access === 'all' ? 'You can see all leads.' : 'You see only leads assigned to you.'}</p>
      {err && <div className="v-alert v-alert--err" role="alert">{err}</div>}
      {settings && <div className="v-card" style={{ margin: '12px 0' }}>
        <strong>Public intake: {settings.intake_enabled ? 'ON' : 'OFF'}</strong>
        <ul className="v-small">{settings.checklist.map((c) => <li key={c}>{c}</li>)}</ul>
        <button type="button" className="v-btn v-btn--black v-btn--sm" onClick={toggleIntake}>{settings.intake_enabled ? 'Turn intake off' : 'Turn intake on'}</button>
      </div>}
      {report && <div className="v-grid" aria-label="Lead totals" style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        <div className="v-card"><div className="v-small">Total leads</div><strong>{report.total}</strong></div>
        {STATUSES.map(([k, l]) => <div className="v-card" key={k}><div className="v-small">{l}</div><strong>{report.by_status[k] || 0}</strong></div>)}
      </div>}
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', margin: '14px 0' }}>
        <label>Search<input value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} placeholder="Name, phone or ZIP" /></label>
        <label>Status<select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">All statuses</option>
          {STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        <label>Source<select value={f.source} onChange={(e) => setF({ ...f, source: e.target.value })}><option value="">All sources</option>
          {SOURCES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
      </div>
      <table className="v-table" data-table="leads"><thead><tr><th>Lead</th><th>ZIP</th><th>Source</th><th>Assigned to</th><th>Status</th><th><span className="v-sr">Open</span></th></tr></thead>
        <tbody>{(data?.leads || []).map((l) => (
          <tr key={l.id}><td>{l.name}<br /><span className="v-small">{l.phone}</span></td><td>{l.zip}</td><td>{l.source}</td>
            <td>{l.assigned_to || 'Unassigned'}</td><td>{l.status_label}</td>
            <td><button type="button" className="v-btn v-btn--link" onClick={() => open(l.id)} aria-label={`Open ${l.name}`}>View</button></td></tr>))}
          {data && !data.leads.length && <tr><td colSpan={6}>No matching leads.</td></tr>}</tbody></table>
      {sel && <aside className="v-card" aria-label="Selected lead" style={{ marginTop: 16 }}>
        <h3>{sel.name}</h3>
        <p className="v-small">Phone {sel.phone || '—'} · Email {sel.email || '—'} · ZIP {sel.zip} · Age {sel.age_range} · Household {sel.household_size || '—'}<br />
          Consent {sel.consent_version} · {sel.submissions} submission(s)</p>
        <label>Status<select value={sel.status} onChange={(e) => act(api.patch(`${base}/${sel.id}`, { status: e.target.value }))}>
          {STATUSES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
        {canAssign && <label>Assign to<select value={sel.assigned_user_id || ''} onChange={(e) => act(api.post(`${base}/${sel.id}/assign`, { user_id: e.target.value || null }))}>
          <option value="">Unassigned</option>{agents.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>}
        <label>Add a note<input value={note} maxLength={1000} onChange={(e) => setNote(e.target.value)} /></label>
        <button type="button" className="v-btn v-btn--red v-btn--sm" disabled={!note.trim()} onClick={() => act(api.patch(`${base}/${sel.id}`, { note }))}>Save note</button>
        <h4>History</h4><ul>{sel.history.map((h, i) => <li key={i} className="v-small">{h.action}{h.new ? `: ${h.new}` : ''}{h.note ? ` — ${h.note}` : ''} · {h.by}</li>)}</ul>
        <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setSel(null)}>Close</button>
      </aside>}
    </section>
  )
}
