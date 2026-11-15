// CONSUMER LEADS - public customer website, V4 design.
//   Public:  https://voltmarketing.live/volt/check-coverage (its own route, no VOLT recruiting header)
//   Preview: /consumer-leads/:orgId/preview (signed in; submissions are TEST data only)
// Visual / interaction reference: docs/volt/design/consumer_leads_v4_design_demo.html.
// All copy comes from the server (app/services/volt/leads.py PAGE) so compliance reviews one place.
// The server decides everything that matters: intake on/off (public POST answers 503 while off),
// validation, consent version, and who may preview.
import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import { volt, useVoltTitle } from './voltApi'
import './consumerSite.css'

const msg = (e) => e?.message || 'Something went wrong. Please try again.'

const INCOME = [['under_20k', 'Under $20,000'], ['20k_35k', '$20,000 – $35,000'], ['35k_50k', '$35,000 – $50,000'],
  ['50k_75k', '$50,000 – $75,000'], ['75k_100k', '$75,000 – $100,000'], ['over_100k', 'Over $100,000'], ['prefer_not', 'I’d rather not say']]

export const QUESTIONS = [
  { key: 'zip', q: 'What is your ZIP code?', hint: 'It helps us look at plans available where you live.', type: 'zip' },
  { key: 'state', q: 'Which state do you live in?', hint: 'Plans and help are different in each state.', type: 'state' },
  { key: 'age_range', q: 'Which age range describes you?', hint: 'Just choose the closest option.',
    options: [['18-25', '18 to 25'], ['26-44', '26 to 44'], ['45-64', '45 to 64'], ['65+', '65 or older']] },
  { key: 'coverage_status', q: 'Do you have health insurance now?', hint: 'There is no wrong answer.',
    options: [['none', 'No, I need coverage'], ['have_compare', 'Yes, but I want to compare'], ['not_sure', 'I’m not sure']] },
  { key: 'household_size', q: 'How many people are in your household?', hint: 'Include yourself. Help with costs depends on household size.',
    options: [['1', '1'], ['2', '2'], ['3', '3'], ['4', '4'], ['5', '5 or more']] },
  { key: 'income_range', q: 'About how much does your household make in a year?', hint: 'An estimate is fine. Help with costs depends on income.', options: INCOME },
  { key: 'contact_preference', q: 'How would you like a licensed agent to reach you?', hint: 'Choose one, and a time of day that works.', type: 'pref' },
  { key: 'contact', q: 'Where can the agent reach you?', hint: 'We use this only to follow up on your request.', type: 'contact' },
]
const PREFS = [['call', 'Phone call'], ['text', 'Text message'], ['email', 'Email']]
const TIMES = [['morning', 'Morning'], ['afternoon', 'Afternoon'], ['evening', 'Evening'], ['any', 'Any time']]

function Headline({ text, accent }) {
  const i = accent ? text.indexOf(accent) : -1
  if (i < 0) return text
  return <>{text.slice(0, i)}<em>{accent}</em>{text.slice(i + accent.length)}</>
}

function Choices({ name, legend, options, value, onPick, hideLegend = true }) {
  return (
    <fieldset className="cp-answers">
      <legend className={hideLegend ? 'cp-sr' : undefined}>{legend}</legend>
      {options.map(([v, label]) => (
        <label key={v} className={`cp-choice${value === v ? ' is-on' : ''}`} data-choice={label}>
          <input type="radio" name={name} value={v} checked={value === v} onChange={() => onPick(v)} /> {label}
        </label>))}
    </fieldset>
  )
}

export default function ConsumerSite({ preview = null } = {}) {
  // preview = { orgId }: the signed-in back-office preview. The whole site works
  // and a request is saved as TEST data through an authenticated endpoint -
  // never the public one, never the real inbox, nothing sent to anyone.
  const [cfg, setCfg] = useState(null)
  const [wiz, setWiz] = useState(false)
  const [step, setStep] = useState(0)
  const [a, setA] = useState({ consent: false })
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)
  const head = useRef(null)
  const dialog = useRef(null)
  const opener = useRef(null)
  const params = useRef(new URLSearchParams(typeof window !== 'undefined' ? window.location.search : ''))
  useVoltTitle(preview ? null : `${cfg?.page?.brand || 'CoveragePath'} · Health coverage options`)

  const load = () => { setCfg(null); volt.leadConfig().then(setCfg).catch(() => setCfg({ enabled: false, failed: true })) }
  useEffect(load, [])
  const page = cfg?.page
  const open = !!preview || !!cfg?.enabled   // may a request be sent?
  const q = QUESTIONS[step]
  const set = (k, v) => { setA((x) => ({ ...x, [k]: v })); setErr('') }

  useEffect(() => { if (wiz) head.current?.focus() }, [wiz, step, done])
  useEffect(() => {
    if (!wiz || preview) return undefined
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = prev }
  }, [wiz, preview])

  const start = (e) => { opener.current = e?.currentTarget || null; setWiz(true); setStep(0); setErr(''); setDone(false) }
  const close = () => { setWiz(false); setErr(''); setTimeout(() => opener.current?.focus?.(), 0) }
  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); close(); return }
    if (e.key !== 'Tab' || !dialog.current) return
    const f = [...dialog.current.querySelectorAll('button:not([disabled]),input:not([disabled]),select:not([disabled]),a[href],[tabindex="0"]')]
    if (!f.length) return
    const first = f[0], last = f[f.length - 1]
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus() }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus() }
  }

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
    if (step < QUESTIONS.length - 1) { setStep(step + 1); setErr(''); return }
    if (!open) return
    setBusy(true)
    let referrer = ''
    try { referrer = document.referrer ? new URL(document.referrer).hostname : '' } catch { referrer = '' }
    const p = params.current
    try {
      const send = preview ? (b) => api.post(`/volt/ops/${preview.orgId}/leads/preview`, b) : volt.submitLead
      await send({
        first_name: a.first_name, last_name: a.last_name, zip: a.zip, state: a.state, phone: a.phone, email: a.email,
        age_range: a.age_range, coverage_status: a.coverage_status, household_size: a.household_size,
        income_range: a.income_range, contact_preference: a.contact_preference, best_time: a.best_time,
        consent: true, consent_version: cfg.consent_version,
        utm_source: p.get('utm_source') || '', utm_medium: p.get('utm_medium') || '',
        utm_campaign: p.get('utm_campaign') || '', utm_content: p.get('utm_content') || '',
        utm_term: p.get('utm_term') || '', referrer_host: referrer, landing_path: window.location.pathname,
      })
      setDone(true)
    } catch (x) { setErr(msg(x)) } finally { setBusy(false) }
  }
  const back = () => { setErr(''); if (step === 0) close(); else setStep(step - 1) }
  const restart = () => { setDone(false); setStep(0); setA({ consent: false }); setErr('') }

  if (!cfg) return <div className="cp" role="status" style={{ padding: 40 }}>Loading…</div>
  if (cfg.failed || !page) {
    return (
      <div className="cp"><main className="cp-max" style={{ padding: '60px 24px' }} role="alert">
        <h1 style={{ fontSize: 32 }}>We couldn’t load this page</h1><p>Please check your connection and try again.</p>
        <button type="button" className="cp-pill" onClick={load}>Try again</button></main></div>
    )
  }
  const brand = page.brand || 'CoveragePath'
  const cta = page.cta || 'Check My Options'
  const last = step === QUESTIONS.length - 1

  const wizard = wiz && (
    <div className={`cp-overlay${preview ? ' cp-overlay--inline' : ''}`} onKeyDown={onKey}
      onMouseDown={(e) => { if (!preview && e.target === e.currentTarget) close() }}>
      <div className="cp-wiz" ref={dialog} role="dialog" aria-modal={preview ? undefined : 'true'} aria-labelledby="cp-q" data-wizard>
        <div className="cp-wiz-top"><b>{brand} · Options check</b>
          <button type="button" className="cp-close" onClick={close} aria-label="Close the questions">✕ Close</button></div>
        {done ? (
          preview ? (
            <div className="cp-done" role="status" data-preview-done>
              <div className="cp-big" aria-hidden="true">✓</div>
              <h2 id="cp-q" tabIndex={-1} ref={head}>Test request saved.</h2>
              <p>This is what a visitor would see after sending. Your answers were saved as <b>test data</b> only. No one was contacted.</p>
              <p><Link to={`/consumer-leads/${preview.orgId}/leads?data=test`}>Open it in the test leads</Link></p>
              <button type="button" className="cp-pill cp-pill--line" onClick={restart}>Start again</button>
            </div>
          ) : (
            <div className="cp-done" role="status" data-done>
              <div className="cp-big" aria-hidden="true">✓</div>
              <h2 id="cp-q" tabIndex={-1} ref={head}>Thank you.</h2>
              <p>Your request was received. A licensed agent may contact you using the details you gave.</p>
              <button type="button" className="cp-pill cp-pill--line" onClick={close}>Back to the website</button>
            </div>)
        ) : (
          <form onSubmit={next} noValidate data-funnel>
            <p className="cp-eyebrow" aria-live="polite" style={{ margin: 0 }} data-step>Question {step + 1} of {QUESTIONS.length}</p>
            <div className="cp-progress" aria-hidden="true"><div className="cp-bar" style={{ width: `${((step + 1) / QUESTIONS.length) * 100}%` }} /></div>
            <h2 id="cp-q" tabIndex={-1} ref={head}>{q.q}</h2>
            <p className="cp-hint">{q.hint}</p>
            {q.type === 'zip' && <><label className="cp-label" htmlFor="cp-zip">ZIP code</label>
              <input id="cp-zip" className="cp-field" inputMode="numeric" autoComplete="postal-code" maxLength={5} value={a.zip || ''}
                aria-invalid={!!err} aria-describedby="cp-err" placeholder="e.g. 75201" onChange={(e) => set('zip', e.target.value.replace(/\D/g, ''))} /></>}
            {q.type === 'state' && <><label className="cp-label" htmlFor="cp-state">State</label>
              <select id="cp-state" className="cp-field" value={a.state || ''} aria-invalid={!!err} aria-describedby="cp-err" onChange={(e) => set('state', e.target.value)}>
                <option value="">Choose your state</option>{cfg.states.map((s) => <option key={s} value={s}>{s}</option>)}</select></>}
            {q.options && <Choices name={q.key} legend={q.q} options={q.options} value={a[q.key]} onPick={(v) => set(q.key, v)} />}
            {q.key === 'age_range' && a.age_range === '65+' && <p className="cp-note" role="note" data-medicare-note>{page.medicare_note}</p>}
            {q.type === 'pref' && <>
              <Choices name="contact_preference" legend="Contact me by" options={PREFS} value={a.contact_preference} onPick={(v) => set('contact_preference', v)} hideLegend={false} />
              <Choices name="best_time" legend="Best time of day" options={TIMES} value={a.best_time} onPick={(v) => set('best_time', v)} hideLegend={false} />
            </>}
            {q.type === 'contact' && <>
              {!open && <p className="cp-note cp-note--warn" role="note" data-closed-final>This is the last step. Requests can’t be sent yet, so these boxes are turned off and nothing you see here is saved.</p>}
              <label className="cp-label" htmlFor="cp-fn">First name</label>
              <input id="cp-fn" className="cp-field" autoComplete="given-name" value={a.first_name || ''} disabled={!open} onChange={(e) => set('first_name', e.target.value)} />
              <label className="cp-label" htmlFor="cp-ln">Last name</label>
              <input id="cp-ln" className="cp-field" autoComplete="family-name" value={a.last_name || ''} disabled={!open} onChange={(e) => set('last_name', e.target.value)} />
              <label className="cp-label" htmlFor="cp-ph">Phone{a.contact_preference === 'email' ? ' (optional)' : ''}</label>
              <input id="cp-ph" className="cp-field" type="tel" autoComplete="tel" value={a.phone || ''} disabled={!open} onChange={(e) => set('phone', e.target.value)} />
              <label className="cp-label" htmlFor="cp-em">Email{a.contact_preference === 'email' ? '' : ' (optional)'}</label>
              <input id="cp-em" className="cp-field" type="email" autoComplete="email" value={a.email || ''} disabled={!open} onChange={(e) => set('email', e.target.value)} />
              <label className="cp-consent"><input type="checkbox" checked={a.consent} disabled={!open} onChange={(e) => set('consent', e.target.checked)} data-consent />
                <span>{cfg.consent_text}</span></label>
            </>}
            <div id="cp-err" className="cp-error" role="alert">{err}</div>
            <div className="cp-wiz-buttons">
              <button type="button" className="cp-pill cp-pill--line" onClick={back} data-back>← Back</button>
              <button type="submit" className="cp-pill" disabled={busy || (!open && last)} data-funnel-next>
                {last ? (!open ? 'Requests open soon' : busy ? 'Sending…' : 'Send my request') : 'Continue →'}</button>
            </div>
            <p className="cp-note" style={{ fontSize: 13 }}>{page.disclaimers.join(' ')}</p>
          </form>)}
      </div>
    </div>
  )

  return (
    <div className="cp" data-consumer-site>
      {!open && <p className="cp-closed" role="note" data-intake-closed><b>Online requests are not open yet.</b> You can read about the coverage
        and look through the questions, but we can’t take your information on this page right now. Please check back soon.</p>}
      {preview && wiz ? wizard : <>
        <div className="cp-mini">{(page.trust || []).map((t, i) => <span key={t}>{i ? <span className="cp-sep" aria-hidden="true">•</span> : null}✓ {t}</span>)}</div>
        <header className="cp-max cp-nav">
          <a className="cp-logo" href="#cp-top"><span className="cp-mark" aria-hidden="true">✚</span><span>{brand}</span></a>
          <nav className="cp-navr" aria-label="On this page">
            <a href="#cp-benefits">Benefits</a><a href="#cp-how">How it works</a><a href="#cp-faq">Questions</a>
            <button type="button" className="cp-pill" onClick={start} data-cta="nav">{cta}</button>
          </nav>
        </header>
        <main id="cp-top">
          <section className="cp-hero" aria-labelledby="cp-h1" data-funnel-intro>
            <div className="cp-max cp-hero-grid">
              <div>
                <p className="cp-eyebrow" style={{ margin: 0 }}><span className="cp-dot" aria-hidden="true" />{page.eyebrow}</p>
                <h1 id="cp-h1"><Headline text={page.headline} accent={page.headline_accent} /></h1>
                <p className="cp-lead">{page.subhead}</p>
                <div className="cp-ctas">
                  <button type="button" className="cp-pill" onClick={start} data-funnel-start>{cta} <span aria-hidden="true">→</span></button>
                  <a className="cp-pill cp-pill--line" href="#cp-how">See how it works</a>
                </div>
                <ul className="cp-trust" data-trust>{(page.trust || []).map((t) => <li key={t}>{t}</li>)}</ul>
              </div>
              <div className="cp-visual" aria-hidden="true">
                <div className="cp-orb" />
                <div className="cp-ill"><i className="cp-sun" /><i className="cp-plant" /><i className="cp-body" /><i className="cp-neck" />
                  <i className="cp-face" /><i className="cp-hair" /><i className="cp-smile" /></div>
                <div className="cp-float cp-float--top"><span>♡</span><strong>Real human help</strong><small>A licensed agent explains your options</small></div>
                <div className="cp-float cp-float--bottom"><span>✓</span><strong>Simple steps</strong><small>A few easy questions</small></div>
              </div>
            </div>
          </section>
          <div className="cp-max cp-strip" data-highlights>
            {(page.highlights || []).map(([t, d], i) => <div key={t}><span className="cp-icon" aria-hidden="true">{['✚', '$', '☏'][i] || '✓'}</span><span><b>{t}</b><p>{d}</p></span></div>)}
          </div>
          <section className="cp-section cp-max" id="cp-benefits" aria-labelledby="cp-ben-h">
            <div className="cp-head"><p className="cp-eyebrow" style={{ margin: 0 }}>What matters most</p>
              <h2 id="cp-ben-h">{page.benefits_title}</h2><p>{page.benefits_intro}</p></div>
            <ul className="cp-benefits" data-benefits>{page.benefits.map(([t, d], i) => (
              <li key={t}><span className="cp-icon" aria-hidden="true">{['$', '♡', '✚', '✓', '☂', '☏'][i] || '✓'}</span><h3>{t}</h3><p>{d}</p></li>))}</ul>
          </section>
          <section className="cp-section cp-how" id="cp-how" aria-labelledby="cp-how-h">
            <div className="cp-max">
              <div className="cp-head"><p className="cp-eyebrow" style={{ margin: 0 }}>No complicated process</p>
                <h2 id="cp-how-h">{page.steps_title}</h2><p>{page.steps_intro}</p></div>
              <ol className="cp-steps" data-funnel-steps>{page.steps.map(([t, d], i) => (
                <li key={t}><span className="cp-num" aria-hidden="true">{i + 1}</span><h3>{t}</h3><p>{d}</p></li>))}</ol>
              <div className="cp-center"><button type="button" className="cp-pill" onClick={start}>{cta} <span aria-hidden="true">→</span></button></div>
            </div>
          </section>
          <section className="cp-section cp-max" id="cp-faq" aria-labelledby="cp-faq-h">
            <div className="cp-head"><p className="cp-eyebrow" style={{ margin: 0 }}>Good questions</p><h2 id="cp-faq-h">{page.faq_title}</h2></div>
            <div className="cp-faq" data-funnel-faq>{page.faq.map(([qq, aa]) => <details key={qq}><summary>{qq}</summary><p>{aa}</p></details>)}</div>
          </section>
          <section className="cp-final" aria-labelledby="cp-final-h">
            <div className="cp-max"><p className="cp-eyebrow" style={{ margin: 0 }}>Your next step can be simple</p>
              <h2 id="cp-final-h">{page.final_title}</h2><p>{page.final_text}</p>
              <button type="button" className="cp-pill" onClick={start}>{cta} <span aria-hidden="true">→</span></button></div>
          </section>
        </main>
        <footer className="cp-foot" data-funnel-disclaimers>
          <p><b>{brand}</b>. {page.brand_note}</p>
          <ul>{page.disclaimers.map((d) => <li key={d}>{d}</li>)}</ul>
        </footer>
        {wizard}
      </>}
    </div>
  )
}
