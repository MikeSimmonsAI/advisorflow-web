/* CLIENT GROWTH - the prospect's private questionnaire (/growth-plan/:token).
 *
 * No login, no navigation, no Program Access, no God Mode: the token names one
 * invitation and the server answers only for it. Everything on the page -
 * wording, questions, "Prepared for" and the pre-filled contact - comes from
 * /client-growth-public/invitations/:token; nothing about any prospect is
 * written into this file. Answers live only in this page's memory until Submit
 * (nothing is saved to the browser), and a submitted link never shows them again.
 *
 * Desktop: the whole form in the white card beside the navy panel.
 * Phone (< 900px): three steps with Back / Continue and visible progress.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { API_BASE, api } from '../../api/client'
import '@fontsource/playfair-display/600.css'
import '@fontsource/playfair-display/700.css'
import './growthQuestionnaire.css'

const PANEL_ICONS = [
  // people, calendar, chart - simple line icons, drawn here (no external assets)
  <svg key="p" viewBox="0 0 24 24" aria-hidden="true"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" /></svg>,
  <svg key="c" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 5h18v16H3zM16 3v4M8 3v4M3 10h18M8 14h.01M12 14h.01M16 14h.01M8 18h.01M12 18h.01" /></svg>,
  <svg key="t" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 20v-4M9 20v-8M14 20V9M19 20V4" /></svg>,
]

function useIsPhone () {
  const q = '(max-width: 899px)'
  const [phone, setPhone] = useState(() => typeof window !== 'undefined' && window.matchMedia(q).matches)
  useEffect(() => {
    const m = window.matchMedia(q)
    const on = () => setPhone(m.matches)
    m.addEventListener ? m.addEventListener('change', on) : m.addListener(on)
    return () => (m.removeEventListener ? m.removeEventListener('change', on) : m.removeListener(on))
  }, [])
  return phone
}

function usePrivatePage (title) {
  useEffect(() => {
    const prev = document.title
    document.title = title || 'EvoSys Pro'
    const add = (name, content) => {
      const m = document.createElement('meta')
      m.name = name; m.content = content; m.setAttribute('data-growth', '1')
      document.head.appendChild(m)
      return m
    }
    const metas = [add('robots', 'noindex, nofollow'), add('referrer', 'no-referrer')]
    return () => { document.title = prev; metas.forEach(m => m.remove()) }
  }, [title])
}

function visible (f, a) {
  const c = f.show_if
  if (!c) return true
  const v = a[c.field]
  if ('equals' in c) return v === c.equals
  if ('includes' in c) return Array.isArray(v) && v.includes(c.includes)
  return true
}

function isEmpty (v) {
  return v === undefined || v === null || (typeof v === 'string' && !v.trim()) || (Array.isArray(v) && !v.length)
}

// The same rules the server applies, so a phone user hears about a missed
// question on the step it belongs to. The server still decides.
function checkField (f, v) {
  if (isEmpty(v)) return f.required ? (f.type === 'multi' ? 'Please choose at least one.' : 'This is required.') : null
  if (f.type === 'email' && !/^[^@\s]+@[^@\s]+\.[^@\s]{2,}$/.test(String(v).trim())) return 'Please enter a valid email address.'
  if (f.type === 'tel' && !/^[0-9+().\-\s]{7,25}$/.test(String(v).trim())) return 'Please enter a valid phone number.'
  if (['count_or_help', 'amount_or_help', 'int'].includes(f.type) && v !== f.help_value) {
    const n = Number(v)
    const lo = f.min ?? 0
    if (!/^\d+$/.test(String(v).trim()) || n < lo || n > (f.maxValue ?? 1e9)) return `Please enter a whole number of at least ${lo}.`
  }
  if (f.max && typeof v === 'string' && v.trim().length > f.max) return `Please keep this under ${f.max} characters.`
  return null
}

function payload (steps, a) {
  const out = {}
  for (const s of steps) for (const f of s.fields) {
    if (!visible(f, a) || isEmpty(a[f.key])) continue
    out[f.key] = a[f.key]
  }
  return out
}

/* ── fields ──────────────────────────────────────────────────────────────── */

function Label ({ f, id }) {
  return (
    <label className="gq-label" htmlFor={id}>
      {f.label}{f.optional_label ? <span className="gq-opt"> (Optional)</span> : null}
    </label>
  )
}

function Field ({ f, value, onChange, error }) {
  const id = 'gq-' + f.key
  const err = error ? <p className="gq-err" id={id + '-err'} role="alert">{error}</p> : null
  const help = f.helper ? <p className="gq-help" id={id + '-help'}>{f.helper}</p> : null
  const describedBy = [error ? id + '-err' : null, f.helper ? id + '-help' : null].filter(Boolean).join(' ') || undefined
  const common = { id, 'aria-invalid': error ? 'true' : undefined, 'aria-describedby': describedBy }

  if (f.type === 'radio' || f.type === 'multi') {
    const multi = f.type === 'multi'
    const cur = multi ? (Array.isArray(value) ? value : []) : value
    return (
      <fieldset className={'gq-field gq-wide' + (error ? ' is-error' : '')} aria-describedby={describedBy} id={id}>
        <legend className="gq-label">{f.label}{f.optional_label ? <span className="gq-opt"> (Optional)</span> : null}</legend>
        <div className={'gq-choices' + (multi ? ' gq-choices--multi' : '')}>
          {f.options.map(o => {
            const on = multi ? cur.includes(o.value) : cur === o.value
            return (
              <label key={o.value} className={'gq-choice' + (on ? ' is-on' : '')}>
                <input type={multi ? 'checkbox' : 'radio'} name={id} value={o.value} checked={on}
                  onChange={() => onChange(multi ? (on ? cur.filter(x => x !== o.value) : [...cur, o.value]) : o.value)} />
                <span className="gq-mark" aria-hidden="true" />
                <span>{o.label}</span>
              </label>
            )
          })}
        </div>
        {help}{err}
      </fieldset>
    )
  }
  if (f.type === 'count_or_help' || f.type === 'amount_or_help') {
    const helped = value === f.help_value
    return (
      <div className={'gq-field' + (error ? ' is-error' : '')}>
        <Label f={f} id={id} />
        <div className="gq-combo">
          {f.type === 'amount_or_help' ? <span className="gq-prefix" aria-hidden="true">$</span> : null}
          <input {...common} className="gq-input" inputMode="numeric" pattern="[0-9]*" autoComplete="off"
            placeholder={f.placeholder || ''} disabled={helped} value={helped ? '' : (value ?? '')}
            onChange={e => onChange(e.target.value.replace(/[^\d]/g, '').slice(0, 9))} />
        </div>
        <label className={'gq-toggle' + (helped ? ' is-on' : '')}>
          <input type="checkbox" checked={helped} onChange={() => onChange(helped ? '' : f.help_value)} />
          <span className="gq-mark gq-mark--box" aria-hidden="true" />
          <span>{f.help_label}</span>
        </label>
        {help}{err}
      </div>
    )
  }
  if (f.type === 'select') {
    return (
      <div className={'gq-field' + (error ? ' is-error' : '')}>
        <Label f={f} id={id} />
        <select {...common} className="gq-input gq-select" value={value || ''} onChange={e => onChange(e.target.value)}>
          <option value="">{f.placeholder || 'Choose one'}</option>
          {f.options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        {help}{err}
      </div>
    )
  }
  if (f.type === 'textarea') {
    return (
      <div className={'gq-field gq-wide' + (error ? ' is-error' : '')}>
        <Label f={f} id={id} />
        <textarea {...common} className="gq-input gq-textarea" rows={3} maxLength={f.max || 2000}
          placeholder={f.placeholder || ''} value={value || ''} onChange={e => onChange(e.target.value)} />
        {help}{err}
      </div>
    )
  }
  const type = f.type === 'int' ? 'text' : (f.type === 'tel' ? 'tel' : f.type === 'email' ? 'email' : 'text')
  const wide = f.key === 'service_areas' || f.key === 'insurance_products_other'
  return (
    <div className={'gq-field' + (wide ? ' gq-wide' : '') + (error ? ' is-error' : '')}>
      <Label f={f} id={id} />
      <input {...common} className="gq-input" type={type} maxLength={f.max || 200}
        inputMode={f.type === 'int' ? 'numeric' : undefined} autoComplete={f.autocomplete || 'off'}
        placeholder={f.placeholder || ''} value={value ?? ''}
        onChange={e => onChange(f.type === 'int' ? e.target.value.replace(/[^\d]/g, '').slice(0, 6) : e.target.value)} />
      {help}{err}
    </div>
  )
}

function Step ({ step, answers, setAnswer, errors }) {
  return (
    <section className="gq-step" aria-labelledby={'gq-step-' + step.key}>
      <h2 className="gq-step-title" id={'gq-step-' + step.key}>
        <span className="gq-num">{step.number}</span><span>{step.title}</span><span className="gq-rule" aria-hidden="true" />
      </h2>
      <div className="gq-grid">
        {step.fields.filter(f => visible(f, answers)).map(f => (
          <Field key={f.key} f={f} value={answers[f.key]} error={errors[f.key]} onChange={v => setAnswer(f.key, v)} />
        ))}
      </div>
    </section>
  )
}

/* ── chrome ──────────────────────────────────────────────────────────────── */

function Header ({ product }) {
  const [failed, setFailed] = useState(false)
  return (
    <header className="gq-header">
      <div className="gq-brand">
        {failed ? null : <img src="/brands/evosyspro-emblem-96.png" alt="" width="40" height="40" onError={() => setFailed(true)} />}
        <span className="gq-divider" aria-hidden="true" />
        <span className="gq-brand-name">EvoSys Pro</span>
      </div>
      <div className="gq-product">{product}</div>
    </header>
  )
}

function Panel ({ panel }) {
  if (!panel) return null
  return (
    <aside className="gq-panel" aria-label="About your plan">
      <svg className="gq-compass" viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="20" /><path d="M31 17l-4 10-10 4 4-10z" /></svg>
      <h2 className="gq-panel-title">{panel.title}</h2>
      <ul className="gq-points">
        {(panel.points || []).map((p, i) => <li key={p}>{PANEL_ICONS[i % PANEL_ICONS.length]}<span>{p}</span></li>)}
      </ul>
      <hr />
      <h3 className="gq-panel-sub">{panel.next_title}</h3>
      <ol className="gq-next">{(panel.next || []).map((n, i) => <li key={n}><span>{i + 1}</span>{n}</li>)}</ol>
      <hr />
      <p className="gq-team-title">{panel.team_title}</p>
      <p className="gq-team">{(panel.team || []).join(' · ')}</p>
    </aside>
  )
}

function Shell ({ product, children, preview }) {
  return (
    <div className="gq-page">
      {preview ? (
        <div className="gq-preview-bar" role="status">
          <strong>Preview</strong> — this is what your prospect sees. Nothing is saved and no one is notified.
          <Link to="/client-growth?tab=templates">Back to Client Growth</Link>
        </div>
      ) : null}
      <Header product={product || 'Agency Growth Questionnaire'} />
      <main className="gq-main">{children}</main>
    </div>
  )
}

function Notice ({ title, message, action }) {
  return (
    <div className="gq-notice" role="status">
      <h1>{title}</h1>
      <p>{message}</p>
      {action}
    </div>
  )
}

/* ── page ────────────────────────────────────────────────────────────────── */

/* PREVIEW (/client-growth/preview/:templateId): the same page for signed-in
 * Client Growth staff, built from the template itself. No invitation is read,
 * opened or answered, and Submit saves nothing. */
export function GrowthQuestionnairePreview () {
  return <GrowthQuestionnaire preview />
}

export default function GrowthQuestionnaire ({ preview = false }) {
  const { inviteToken, templateId } = useParams()
  const [search] = useSearchParams()
  const phone = useIsPhone()
  const [state, setState] = useState({ phase: 'loading' })
  const [answers, setAnswers] = useState({})
  const [errors, setErrors] = useState({})
  const [stepIx, setStepIx] = useState(0)
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState('')
  const sending = useRef(false)
  const topRef = useRef(null)
  usePrivatePage('Agency Growth Questionnaire · EvoSys Pro')

  const load = useCallback(async () => {
    setState({ phase: 'loading' })
    if (preview) {
      try {
        const r = await api.get('/client-growth/templates/' + encodeURIComponent(templateId || ''))
        const d = r.definition || {}
        const pub = { ...(d.public || {}) }
        const who = search.get('for') || 'Your prospect'
        pub.eyebrow = (pub.eyebrow || '').replace('{prepared_for}', who)
        const prefill = {}
        for (const k of ['contact_name', 'business_name', 'email']) if (search.get(k)) prefill[k] = search.get(k)
        setAnswers(prefill)
        setState({ phase: 'ready', data: { state: 'ready', public: pub, steps: d.steps || [], prefill } })
      } catch (e) {
        setState({ phase: 'error' })
      }
      return
    }
    try {
      const res = await fetch(`${API_BASE}/client-growth-public/invitations/${encodeURIComponent(inviteToken || '')}`,
        { cache: 'no-store', referrerPolicy: 'no-referrer' })
      const data = await res.json().catch(() => ({}))
      if (res.status === 404) return setState({ phase: 'invalid', message: data.detail || 'This questionnaire link isn’t valid.' })
      if (!res.ok) return setState({ phase: 'error' })
      if (data.state !== 'ready') return setState({ phase: data.state, message: data.message })
      setAnswers({ ...(data.prefill || {}) })
      setState({ phase: 'ready', data })
    } catch {
      setState({ phase: 'error' })
    }
  }, [inviteToken, preview, templateId, search])
  useEffect(() => { load() }, [load])

  const steps = state.data?.steps || []
  const pub = state.data?.public || {}
  const setAnswer = useCallback((k, v) => {
    setAnswers(a => ({ ...a, [k]: v }))
    setErrors(e => (e[k] ? { ...e, [k]: undefined } : e))
  }, [])

  const stepErrors = useCallback((s) => {
    const out = {}
    for (const f of s.fields) {
      if (!visible(f, answers)) continue
      const e = checkField(f, answers[f.key])
      if (e) out[f.key] = e
    }
    return out
  }, [answers])

  const focusFirst = (errs) => {
    const k = Object.keys(errs).find(x => errs[x])
    if (!k) return
    setTimeout(() => {
      const el = document.getElementById('gq-' + k)
      if (!el) return
      el.scrollIntoView({ block: 'center', behavior: 'smooth' })
      const target = el.matches('fieldset') ? el.querySelector('input') : el
      target && target.focus({ preventScroll: true })
    }, 30)
  }

  const goStep = (ix) => {
    setStepIx(ix)
    setTimeout(() => topRef.current && topRef.current.scrollIntoView({ block: 'start', behavior: 'smooth' }), 10)
  }

  const next = () => {
    const errs = stepErrors(steps[stepIx])
    if (Object.keys(errs).length) { setErrors(e => ({ ...e, ...errs })); focusFirst(errs); return }
    goStep(Math.min(stepIx + 1, steps.length - 1))
  }

  const submit = async (e) => {
    e && e.preventDefault()
    if (sending.current) return
    setFormError('')
    const all = {}
    steps.forEach(s => Object.assign(all, stepErrors(s)))
    if (Object.keys(all).length) {
      setErrors(all)
      if (phone) {
        const ix = steps.findIndex(s => s.fields.some(f => all[f.key]))
        if (ix >= 0 && ix !== stepIx) setStepIx(ix)
      }
      focusFirst(all)
      return
    }
    if (preview) {
      const first = String(answers.contact_name || '').trim().split(/\s+/)[0] || 'there'
      setState({ phase: 'success', message: (state.data.public.success || 'Thanks, {first_name}.').replace('{first_name}', first), data: state.data })
      window.scrollTo(0, 0)
      return
    }
    sending.current = true
    setBusy(true)
    try {
      const res = await fetch(`${API_BASE}/client-growth-public/invitations/${encodeURIComponent(inviteToken)}/submit`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, cache: 'no-store', referrerPolicy: 'no-referrer',
        body: JSON.stringify({ answers: payload(steps, answers) }),
      })
      const data = await res.json().catch(() => ({}))
      if (res.ok) { setState({ phase: 'success', message: data.message }); window.scrollTo(0, 0); return }
      if (res.status === 422 && data.detail && data.detail.errors) {
        setErrors(data.detail.errors)
        if (phone) {
          const ix = steps.findIndex(s => s.fields.some(f => data.detail.errors[f.key]))
          if (ix >= 0) setStepIx(ix)
        }
        focusFirst(data.detail.errors)
        setFormError(data.detail.message || 'Please check the highlighted answers.')
      } else if (res.status === 410 || res.status === 404) {
        setState({ phase: (data.detail && data.detail.state) || 'invalid', message: (data.detail && data.detail.message) || data.detail })
      } else if (res.status === 429) {
        setFormError('Too many attempts in a short time. Please wait a minute and try again.')
      } else {
        setFormError('We couldn’t send your answers just now. Your answers are still here - please try again.')
      }
    } catch {
      setFormError('We couldn’t reach our server. Your answers are still here - check your connection and try again.')
    } finally {
      sending.current = false
      setBusy(false)
    }
  }

  const intro = useMemo(() => (
    <div className="gq-intro" ref={topRef}>
      <div>
        {pub.eyebrow ? <p className="gq-eyebrow">{pub.eyebrow}</p> : null}
        <h1 className="gq-title">{pub.headline}</h1>
        <p className="gq-desc">{pub.description}</p>
      </div>
      {pub.estimate ? (
        <p className="gq-time"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="13" r="8" /><path d="M12 9v4l2 2M9 2h6" /></svg>{pub.estimate}</p>
      ) : null}
    </div>
  ), [pub.eyebrow, pub.headline, pub.description, pub.estimate])

  if (state.phase === 'loading') {
    return <Shell preview={preview}><div className="gq-loading" role="status" aria-live="polite"><span className="gq-spinner" aria-hidden="true" />Loading your questionnaire…</div></Shell>
  }
  if (state.phase === 'error') {
    return <Shell preview={preview}><Notice title="We couldn't load this page" message="Something went wrong on our side or with the connection. Your link is fine."
      action={<button type="button" className="gq-cta gq-cta--small" onClick={load}>Try again</button>} /></Shell>
  }
  if (state.phase === 'invalid') return <Shell preview={preview}><Notice title="Link not found" message={state.message || 'This questionnaire link isn’t valid. Please check the link you were sent.'} /></Shell>
  if (state.phase === 'revoked') return <Shell preview={preview}><Notice title="This link is no longer active" message={state.message} /></Shell>
  if (state.phase === 'expired') return <Shell preview={preview}><Notice title="This link has expired" message={state.message} /></Shell>
  if (state.phase === 'submitted') return <Shell preview={preview}><Notice title="Answers already received" message={state.message} /></Shell>
  if (state.phase === 'success') {
    return (
      <Shell product={pub.product} preview={preview}>
        <div className="gq-notice gq-success" role="status" aria-live="polite">
          <svg viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="21" /><path d="M14 25l7 7 13-15" /></svg>
          <h1>{(state.message || '').split('. ')[0] + '.'}</h1>
          <p>{(state.message || '').split('. ').slice(1).join('. ')}</p>
        </div>
      </Shell>
    )
  }

  const last = stepIx === steps.length - 1
  return (
    <Shell product={pub.product} preview={preview}>
      {intro}
      {phone ? (
        <ol className="gq-progress" aria-label="Progress">
          {steps.map((s, i) => (
            <li key={s.key} className={(i === stepIx ? 'is-current ' : '') + (i < stepIx ? 'is-done' : '')}
              aria-current={i === stepIx ? 'step' : undefined}>
              <span className="gq-dot">{i + 1}</span><span className="gq-dot-label">{s.title}</span>
            </li>
          ))}
        </ol>
      ) : null}
      <div className="gq-layout">
        <form className="gq-card" onSubmit={submit} noValidate>
          {phone
            ? <Step step={steps[stepIx]} answers={answers} setAnswer={setAnswer} errors={errors} />
            : steps.map(s => <Step key={s.key} step={s} answers={answers} setAnswer={setAnswer} errors={errors} />)}
          {formError ? <p className="gq-form-error" role="alert">{formError}</p> : null}
          {!phone || last ? (
            <div className={'gq-submit' + (phone ? ' gq-submit--phone' : '')}>
              {phone ? <button type="button" className="gq-back" onClick={() => goStep(stepIx - 1)}>Back</button> : null}
              <button type="submit" className="gq-cta" disabled={busy} aria-busy={busy ? 'true' : undefined}>
                {busy ? 'Sending…' : pub.cta} {busy ? null : <span aria-hidden="true">→</span>}
              </button>
              <p className="gq-cta-help">{pub.cta_helper}</p>
            </div>
          ) : (
            <div className="gq-stepnav">
              {stepIx > 0
                ? <button type="button" className="gq-back" onClick={() => goStep(stepIx - 1)}>Back</button>
                : <span className="gq-stepcount">Step {stepIx + 1} of {steps.length}</span>}
              <button type="button" className="gq-cta gq-cta--step" onClick={next}>Continue <span aria-hidden="true">→</span></button>
            </div>
          )}
        </form>
        {phone ? null : <Panel panel={pub.panel} />}
      </div>
    </Shell>
  )
}
