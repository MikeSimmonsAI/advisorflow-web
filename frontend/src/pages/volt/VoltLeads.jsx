// Consumer Leads - VOLT Marketing's subsidiary business (separate from
// recruiting and from HIP).
//   CoverageFunnel      public, one-question-at-a-time eligibility page (/volt/check-coverage);
//                       also rendered signed-in by the back office's Preview (test data only)
//   ConsumerLeadsCard   card on the VOLT Operations overview (counts only)
// The private back office is ConsumerLeadsWorkspace.jsx (/consumer-leads/:orgId).
// Visual/interaction references: docs/volt/design/volt_leads_v3.html and
// volt_leads_backend_demo.html. All access rules are enforced by the server
// (app/services/volt/leads.py); this page only reflects them.
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
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

export function CoverageFunnel({ preview = null } = {}) {
  // preview = { orgId }: signed-in preview from the back office. The whole site
  // works and a submission is saved as TEST data through an authenticated
  // endpoint - never the public one, never the real inbox.
  useVoltTitle(preview ? null : undefined)
  const [cfg, setCfg] = useState(null)
  const [started, setStarted] = useState(false)
  const [step, setStep] = useState(0)
  const [a, setA] = useState({ consent: false })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const head = useRef(null)
  const load = () => { setCfg(null); volt.leadConfig().then(setCfg).catch(() => setCfg({ enabled: false, failed: true })) }
  useEffect(load, [])
  useEffect(() => { head.current?.focus() }, [step, done, started])
  const set = (k, v) => { setA((x) => ({ ...x, [k]: v })); setErr('') }
  const params = new URLSearchParams(window.location.search)
  const q = QUESTIONS[step]
  const page = cfg?.page
  // The website is always shown. Whether a request can be SENT is a separate
  // switch (cfg.enabled), and the server refuses submissions while it is off.
  const open = !!preview || !!cfg?.enabled

  const valid = () => {
    if (q.type === 'zip') return /^\d{5}$/.test((a.zip || '').trim()) || 'Please enter a 5-digit ZIP code.'
    if (q.type === 'state') return !!a.state || 'Please choose your state.'
    if (q.type === 'pref') return (!!a.contact_preference && !!a.best_time) || 'Please choose how and when to be contacted.'
    if (q.type === 'contact') {
      if (!open) return 'Online requests are not open yet, so this form cannot be sent.'
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
    if (!open) return
    setBusy(true)
    let referrer = ''
    try { referrer = document.referrer ? new URL(document.referrer).hostname : '' } catch { referrer = '' }
    try {
      const send = preview ? (b) => api.post(`/volt/ops/${preview.orgId}/leads/preview`, b) : volt.submitLead
      await send({
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
        {cfg?.failed && <div className="v-card" role="alert"><h1 tabIndex={-1} ref={head}>We couldn’t load this page</h1>
          <p>Please check your connection and try again.</p>
          <button type="button" className="v-btn" style={{ background: NAVY, color: '#fff' }} onClick={load}>Try again</button></div>}
        {page && !open && <p role="note" data-intake-closed style={{ background: '#FFF4D6', border: '2px solid #E0B44C', borderRadius: 12,
          padding: '12px 16px', fontSize: 18, margin: '0 0 18px' }}><b>Online requests are not open yet.</b> You can read about the
          coverage and look through the questions, but we can’t take your information on this page right now. Please check back soon.</p>}
        {open && done && !preview && <div className="v-card" role="status" style={{ borderColor: GREEN }}><h1 tabIndex={-1} ref={head}>Thank you.</h1>
          <p>Your request was received. A licensed agent may contact you using the details you gave.</p></div>}
        {done && preview && <div className="v-card" role="status" style={{ borderColor: GREEN }} data-preview-done><h1 tabIndex={-1} ref={head}>Test request saved.</h1>
          <p>This is what a visitor would see after sending. Your answers were saved as <b>test data</b> only. No one was contacted.</p>
          <p><Link to={`/consumer-leads/${preview.orgId}/leads?data=test`}>Open it in the test leads</Link> · <button type="button" className="v-btn v-btn--link"
            onClick={() => { setDone(false); setStarted(false); setStep(0); setA({ consent: false }) }}>Start again</button></p></div>}
        {page && !done && !started && (
          <section aria-labelledby="lead-h" data-funnel-intro>
            <p className="v-eyebrow" style={{ color: GREEN }}>Free to check · No obligation · No account needed</p>
            <h1 id="lead-h" tabIndex={-1} ref={head} style={{ fontSize: 36, lineHeight: 1.15, color: NAVY }}>{page.headline}</h1>
            <p style={{ fontSize: 20 }}>{page.subhead}</p>
            <button type="button" className="v-btn" style={{ background: NAVY, color: '#fff', fontSize: 20, padding: '16px 28px', borderRadius: 12 }}
              onClick={() => setStarted(true)} data-funnel-start>Check if I qualify →</button>
            <ul style={{ listStyle: 'none', padding: 0, margin: '24px 0', display: 'grid', gap: 12 }}>
              {page.benefits.map(([t, d]) => <li key={t} style={{ background: BLUE, borderRadius: 12, padding: '14px 16px' }}><b>{t}.</b> {d}</li>)}
            </ul>
            {page.steps && <section aria-labelledby="lead-steps" style={{ margin: '28px 0' }} data-funnel-steps>
              <h2 id="lead-steps" style={{ fontSize: 26, color: NAVY }}>Three simple steps</h2>
              <ol style={{ listStyle: 'none', padding: 0, display: 'grid', gap: 12 }}>
                {page.steps.map(([t, d], i) => <li key={t} style={{ display: 'flex', gap: 14, alignItems: 'flex-start' }}>
                  <span aria-hidden="true" style={{ flex: 'none', width: 40, height: 40, borderRadius: 20, background: NAVY, color: '#fff', display: 'grid', placeItems: 'center', fontWeight: 700 }}>{i + 1}</span>
                  <span><b>{t}</b><br />{d}</span></li>)}
              </ol>
              <button type="button" className="v-btn" style={{ background: NAVY, color: '#fff', fontSize: 20, padding: '14px 24px', borderRadius: 12 }}
                onClick={() => setStarted(true)}>Check my options →</button>
            </section>}
            {page.faq && <section aria-labelledby="lead-faq" style={{ margin: '28px 0' }} data-funnel-faq>
              <h2 id="lead-faq" style={{ fontSize: 26, color: NAVY }}>Common questions</h2>
              {page.faq.map(([qq, aa]) => <details key={qq} style={{ borderTop: '1px solid #c9d6e2', padding: '12px 0' }}>
                <summary style={{ fontSize: 19, fontWeight: 600, cursor: 'pointer', minHeight: 32 }}>{qq}</summary>
                <p style={{ margin: '8px 0 0' }}>{aa}</p></details>)}
            </section>}
            <div className="v-small" style={{ fontSize: 16 }} data-funnel-disclaimers><b>Please know:</b>
              <ul>{page.disclaimers.map((d) => <li key={d}>{d}</li>)}</ul></div>
          </section>)}
        {page && !done && started && (
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
              {!open && <p role="note" data-closed-final style={{ background: '#FFF4D6', borderRadius: 10, padding: 12 }}>
                This is the last step. Requests can’t be sent yet, so these boxes are turned off and nothing you see here is saved.</p>}
              <label>First name<input style={field} autoComplete="given-name" value={a.first_name || ''} disabled={!open} onChange={(e) => set('first_name', e.target.value)} /></label>
              <label>Last name<input style={field} autoComplete="family-name" value={a.last_name || ''} disabled={!open} onChange={(e) => set('last_name', e.target.value)} /></label>
              <label>Phone{a.contact_preference === 'email' ? ' (optional)' : ''}<input style={field} type="tel" autoComplete="tel" value={a.phone || ''} disabled={!open} onChange={(e) => set('phone', e.target.value)} /></label>
              <label>Email{a.contact_preference === 'email' ? '' : ' (optional)'}<input style={field} type="email" autoComplete="email" value={a.email || ''} disabled={!open} onChange={(e) => set('email', e.target.value)} /></label>
              <label style={{ display: 'flex', gap: 10, alignItems: 'flex-start', margin: '14px 0', fontSize: 17 }}>
                <input type="checkbox" style={{ width: 24, height: 24, flex: 'none' }} disabled={!open} checked={a.consent} onChange={(e) => set('consent', e.target.checked)} />
                <span>{cfg.consent_text}</span></label>
            </>}
            <div id="lead-err" role="alert" className={err ? 'v-alert v-alert--err' : ''}>{err}</div>
            <div style={{ display: 'flex', gap: 10, marginTop: 16 }}>
              <button type="button" className="v-btn v-btn--line" style={{ fontSize: 18, minHeight: 52 }}
                onClick={() => { setErr(''); if (step === 0) setStarted(false); else setStep(step - 1) }}>← Back</button>
              <button type="submit" className="v-btn" style={{ background: NAVY, color: '#fff', fontSize: 18, minHeight: 52, flex: 1 }} disabled={busy || (!open && step === QUESTIONS.length - 1)} data-funnel-next>
                {step === QUESTIONS.length - 1 ? (!open ? 'Requests open soon' : busy ? 'Sending…' : 'Send my request') : 'Continue →'}</button>
            </div>
          </form>)}
        {page && started && !done && <p style={{ fontSize: 15, marginTop: 18 }}>{page.disclaimers.join(' ')}</p>}
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
        <p className="v-small v-muted" style={{ margin: 0 }}>Health Insurance Lead Acquisition &amp; Enrollment · a VOLT Marketing business</p></div>
        {s && <span className="v-tag">{s.intake_enabled ? 'Public intake ON' : 'Public intake OFF'}</span>}</div>
      {!s ? <p className="v-mono">Loading…</p> : <>
        <div className="v-kpis" style={{ marginTop: 10 }}>
          {nums.map(([k, l]) => <div key={k} className="v-kpi" data-card-kpi={k}><b>{s[k]}</b><span>{l}</span></div>)}
        </div>
        <p className="v-small v-muted">Last activity: {fmtCT(s.last_activity_at)}{s.access === 'assigned' ? ' · counts include only leads assigned to you' : ''}</p>
        <div className="v-row" style={{ justifyContent: 'flex-start', gap: 8, flexWrap: 'wrap' }}>
          <Link className="v-btn v-btn--red v-btn--sm" to={`/consumer-leads/${orgId}`} data-card-open>Open Consumer Leads</Link>
          {s.can_preview && <Link className="v-btn v-btn--line v-btn--sm" to={`/consumer-leads/${orgId}/preview`} data-card-preview>Preview Customer Website</Link>}
          <a className="v-btn v-btn--line v-btn--sm" href={publicSiteUrl()} target="_blank" rel="noreferrer" data-card-site>View Public Website ↗</a>
          {s.can_develop && <Link className="v-btn v-btn--line v-btn--sm" to={`/consumer-leads/${orgId}/dev`} data-card-dev>Development</Link>}
          {s.can_settings && <Link className="v-btn v-btn--line v-btn--sm" to={`/consumer-leads/${orgId}/settings`} data-card-settings>Settings</Link>}
        </div></>}
    </section>
  )
}
