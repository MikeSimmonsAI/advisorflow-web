/* VOLT application, step by step (#535).
 *
 * The steps come from the project (the server sends `profile_fields` and
 * `sections`):
 *
 *   ACA (full file)                      Fiber (approved demo; traveling door-to-door only)
 *   1 About you   location, regions,     1 Travel   home base, departure, time away,
 *                 start, availability               license, transportation, smartphone
 *   2 Experience  work history or "No    2 Sales    door-to-door experience or the
 *                 previous work                     beginner question; optional resume
 *                 experience", resume    3 Mindset  motivation, setbacks, coaching,
 *   3 Project questions                             commitment; anything else
 *   4 Review & submit                    4 Review
 *   ("You", the account step, comes before the application in both.)
 *
 * Every step saves to the server (Back/Next, and quietly while typing), so a
 * person can sign out on any step and resume exactly there. What is REQUIRED is
 * decided by the server at submit; this screen shows the server's list.
 * There is no floating Apply/Next overlay here: each step's own buttons are the
 * only actions, at the end of the step.
 */
import { Fragment, useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { fmtDate, uploadKey, volt } from './voltApi'

export const STAGE_LABEL = { draft: 'Draft', submitted: 'Submitted', needs_update: 'Needs update', approved: 'Approved', started: 'Started' }

export function StageSteps({ status }) {
  const order = ['draft', 'submitted', 'approved', 'started']
  const at = status === 'needs_update' ? 1 : order.indexOf(status)
  return (
    <div className="v-steps" aria-label={`Stage: ${STAGE_LABEL[status]}`}>
      {order.map((s, i) => (
        <span key={s} className={`v-step ${i < at ? 'done' : ''} ${i === at ? 'now' : ''}`}>
          {i === at && status === 'needs_update' ? 'Needs update' : STAGE_LABEL[s]}
        </span>
      ))}
    </div>
  )
}

const STATE_LABEL = { requested: 'Requested', under_review: 'Under review', accepted: 'Accepted', replace: 'Replace', not_provided: 'Optional' }
const RESUME_ACCEPT = '.pdf,.doc,.docx,application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document'

export function DocRow({ d, applicationId, editable, onDone }) {
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const key = useRef(uploadKey())
  const isResume = d.key === 'resume'
  async function choose(e) {
    const f = e.target.files && e.target.files[0]
    if (!f) return
    if (f.size > 10 * 1024 * 1024) { setErr('That file is too large. The limit is 10 MB.'); return }
    setBusy(true); setErr('')
    const form = new FormData()
    form.append('file', f)
    form.append('doc_key', d.key)
    form.append('upload_key', key.current)
    if (applicationId) form.append('application_id', applicationId)
    try {
      await volt.upload(form)
      key.current = uploadKey()
      await onDone()
    } catch (x) { setErr(x.message + (x.status === 0 ? ' Your file was not saved; choose it again to retry.' : '')) } finally { setBusy(false); e.target.value = '' }
  }
  async function view() {
    try { const b = await volt.file(d.document.id); window.open(URL.createObjectURL(b), '_blank', 'noopener') } catch (x) { setErr(x.message) }
  }
  const canUpload = editable && ['requested', 'replace', 'under_review', 'not_provided'].includes(d.state)
  return (
    <div className="v-doc" data-doc={d.key}>
      <div style={{ flex: 1, minWidth: 200 }}>
        <b>{d.label}{d.optional ? ' (optional)' : ''}</b>{' '}
        <span className={`v-tag ${d.state === 'accepted' ? 'v-tag--black' : (d.state === 'requested' || d.state === 'not_provided') ? 'v-tag--line' : ''}`}>{STATE_LABEL[d.state] || d.state}</span>
        {d.document && <p className="v-small v-muted">{d.document.filename} · version {d.document.version} · {fmtDate(d.document.uploaded_at)}</p>}
        {isResume && !d.document && <p className="v-small v-muted">PDF or Word (.pdf, .doc, .docx), up to 10 MB. You can submit without one.</p>}
        {d.reason && <p className="v-ferr">Reviewer: {d.reason}</p>}
        {err && <p className="v-ferr" role="alert">{err}</p>}
      </div>
      <div className="v-row">
        {d.document && <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={view}>View</button>}
        {canUpload && (
          <label className="v-btn v-btn--red v-btn--sm" style={{ margin: 0 }}>
            {busy ? 'Uploading…' : d.document ? 'Replace' : 'Upload'}
            <input type="file" accept={isResume ? RESUME_ACCEPT : '.pdf,.doc,.docx,image/*'} onChange={choose} disabled={busy} style={{ display: 'none' }} />
          </label>
        )}
      </div>
    </div>
  )
}

export function buildSteps(a) {
  const full = (a.profile_fields || []).includes('work_history')
  const sections = a.sections && a.sections.length ? a.sections : [{ id: 'project', label: 'Project questions' }]
  // Full file (ACA): About you, Experience, the project's questions, Review.
  // Fiber (approved demo): Travel (with home base), Sales (with the optional
  // resume), Mindset (with anything else), Review.
  const steps = []
  if (full) steps.push({ id: 'about', label: 'About you' }, { id: 'experience', label: 'Experience' })
  sections.forEach((s) => steps.push({ id: `q:${s.id}`, section: s.id, label: s.label, title: s.title, intro: s.intro }))
  steps.push({ id: 'review', label: full ? 'Review & submit' : 'Review' })
  return steps
}

// A missing item's text starts with the step it belongs to.
function stepOf(steps, missing) {
  const at = (id) => Math.max(0, steps.findIndex((s) => s.id === id))
  if (/^Profile:/.test(missing)) return steps.some((s) => s.id === 'about') ? at('about') : steps.findIndex((s) => s.section)
  if (/^Experience:/.test(missing)) return at('experience')
  const sec = steps.find((s) => s.section && missing.startsWith(`${s.label}:`))
  if (sec) return steps.indexOf(sec)
  if (/^(Upload|Replace):/.test(missing)) {
    const sales = steps.findIndex((s) => s.section === 'experience')
    return sales >= 0 && !steps.some((s) => s.id === 'about') ? sales : steps.length - 2
  }
  return steps.findIndex((s) => s.section) // "Answer: ..." -> the first question step
}

// show_if: a follow-up question appears only for the answers that lead to it.
export function shown(q, ans) {
  return !q.show_if || q.show_if[1].includes(ans[q.show_if[0]] || '')
}

const EMPTY_ROLE = { employer: '', title: '', start: '', end: '', current: false, summary: '' }

function Field({ id, label, error, children, hint }) {
  return (
    <label htmlFor={id}>{label}
      {children}
      {hint && <span className="v-small v-muted" style={{ display: 'block', fontWeight: 400 }}>{hint}</span>}
      {error && <span className="v-ferr" id={`${id}-e`}>{error}</span>}
    </label>
  )
}

function fmtDay(d) {
  return d ? new Date(d.slice(0, 10) + 'T12:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : ''
}

function fmtMonth(m) {
  if (!m) return ''
  const [y, mo] = m.split('-')
  return new Date(Number(y), Number(mo) - 1, 1).toLocaleDateString('en-US', { month: 'short', year: 'numeric' })
}

export default function ApplicationSteps({ reload }) {
  const { id } = useParams()
  const [a, setA] = useState(null)
  const [h, setH] = useState(null)
  const [ans, setAns] = useState({})
  const [step, setStep] = useState(0)
  const [errs, setErrs] = useState({})
  const [err, setErr] = useState('')
  const [saved, setSaved] = useState('')
  const [busy, setBusy] = useState(false)
  const dirty = useRef({ hiring: false, answers: false })
  const timer = useRef(null)
  const top = useRef(null)
  const stepKey = `af_volt_step_${id}`

  const load = useCallback(async () => {
    const x = await volt.application(id)
    setA(x); setH(x.hiring); setAns(x.answers || {})
    return x
  }, [id])
  useEffect(() => {
    load().then((x) => {
      const last = buildSteps(x).length - 1
      let s = 0
      try { s = Number(sessionStorage.getItem(stepKey) || 0) } catch { /* private */ }
      if (!x.editable) s = last
      setStep(Math.min(Math.max(s || 0, 0), last))
    }).catch((x) => setErr(x.message))
  }, [load]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { try { sessionStorage.setItem(stepKey, String(step)) } catch { /* private */ } }, [step, stepKey])

  const persist = useCallback(async () => {
    if (!a || !a.editable) return true
    const tasks = []
    if (dirty.current.hiring) tasks.push(volt.saveHiring(h).then(() => { dirty.current.hiring = false }))
    if (dirty.current.answers) tasks.push(volt.saveAnswers(a.id, ans).then(() => { dirty.current.answers = false }))
    if (!tasks.length) return true
    setSaved('Saving…')
    try {
      await Promise.all(tasks)
      setSaved('Draft saved'); setErrs({})
      return true
    } catch (x) {
      setSaved(''); setErrs(x.detail?.fields || {}); setErr(x.message)
      return false
    }
  }, [a, h, ans])

  // After an upload, refresh ONLY the application/documents. The form state the
  // person is typing (hiring file, answers) is saved first and never replaced
  // by a reload - an upload must not discard a job entry typed a second ago.
  const docsChanged = useCallback(async () => {
    await persist()
    const x = await volt.application(id)
    setA(x)
  }, [persist, id])

  // Quiet autosave while typing.
  useEffect(() => {
    if (!a || !a.editable || (!dirty.current.hiring && !dirty.current.answers)) return undefined
    clearTimeout(timer.current)
    timer.current = setTimeout(() => { persist() }, 900)
    return () => clearTimeout(timer.current)
  }, [h, ans]) // eslint-disable-line react-hooks/exhaustive-deps

  if (err && !a) return <div className="v-page"><div className="v-alert v-alert--err" role="alert">{err}</div></div>
  if (!a || !h) return <div className="v-center" role="status"><p className="v-mono">Loading…</p></div>

  const setHiring = (patch) => { dirty.current.hiring = true; setH((x) => ({ ...x, ...patch })) }
  const setAnswer = (k, v) => { dirty.current.answers = true; setAns((x) => ({ ...x, [k]: v })) }
  async function go(to) {
    clearTimeout(timer.current)
    setErr('')
    const ok = await persist()
    if (!ok) return
    if (to === buildSteps(a).length - 1) { await load() }
    setStep(to)
    setTimeout(() => top.current?.focus(), 30)
    window.scrollTo(0, 0)
  }
  async function submit() {
    setBusy(true); setErr('')
    try {
      clearTimeout(timer.current)
      if (!(await persist())) return
      await volt.submit(a.id)
      await load()
      await reload()
      setSaved('')
    } catch (x) {
      setErr(x.message)
      await load()
    } finally { setBusy(false) }
  }

  const avail = h.availability || { types: [], notes: '' }
  const roles = h.work_history || []
  const setRole = (i, patch) => setHiring({ work_history: roles.map((r, j) => (j === i ? { ...r, ...patch } : r)) })
  const fe = (k) => errs[k]
  const opts = a.availability_options || []
  const editable = a.editable
  const missing = a.missing || []
  const resume = (a.profile_documents || []).find((d) => d.key === 'resume')
  const otherProfileDocs = (a.profile_documents || []).filter((d) => d.key !== 'resume')
  const STEPS = buildSteps(a)
  const LAST = STEPS.length - 1
  const cur = STEPS[Math.min(step, LAST)]
  const pf = a.profile_fields || []
  const full = pf.includes('work_history')
  const allQ = a.questions || []
  const q = allQ.filter((qq) => !qq.legacy)
  const earlier = allQ.filter((qq) => qq.legacy && ans[qq.key])
  const sectionOf = (qq) => qq.section || 'project'
  const docStep = full ? STEPS.findIndex((x) => x.section) + (STEPS.filter((x) => x.section).length - 1) : STEPS.findIndex((x) => x.section === 'experience')
  const firstSection = STEPS.find((x) => x.section)?.section
  const lastSection = [...STEPS].reverse().find((x) => x.section)?.section
  const answerLabel = (qq) => {
    const v = ans[qq.key]
    if (!v) return '—'
    if (qq.type === 'confirm') return v === 'yes' ? 'Yes, I understand' : '—'
    if (qq.type === 'choice') return (qq.options.find((o) => o[0] === v) || [v, v])[1]
    if (qq.type === 'date') return fmtDay(v)
    return v
  }

  return (
    <div className="v-page v-app" style={{ maxWidth: 780 }}>
      <Link to="/volt/me" className="v-btn v-btn--line v-btn--sm">← My dashboard</Link>
      <p className="v-mono" style={{ marginTop: 14 }}>{a.project.short_name}</p>
      <h2 style={{ fontSize: 30, letterSpacing: '-.04em' }}>{a.project.name}</h2>
      <StageSteps status={a.status} />
      {a.status === 'needs_update' && a.return_reason && <div className="v-alert v-alert--info"><b>Update requested.</b> {a.return_reason}</div>}

      {editable && (
        <nav className="v-progress" aria-label="Application steps">
          <p className="v-small" ref={top} tabIndex={-1}><b>Step {step + 1} of {STEPS.length}:</b> {cur.label}</p>
          <div className="v-bar" role="progressbar" aria-valuemin={1} aria-valuemax={STEPS.length} aria-valuenow={step + 1}><span style={{ width: `${((step + 1) / STEPS.length) * 100}%` }} /></div>
          <ol className="v-steplist">
            {STEPS.map((s, i) => (
              <li key={s.id}><button type="button" className={`v-steplink ${i === step ? 'now' : ''}`} aria-current={i === step ? 'step' : undefined} onClick={() => go(i)}>{i + 1}. {s.label}</button></li>
            ))}
          </ol>
        </nav>
      )}

      {editable && cur.id === 'about' && (
        <section className="v-panel v-form" data-step="about">
          <h3>About you</h3>
          <p className="v-small v-muted">{a.profile.first_name} {a.profile.last_name} · {a.profile.email} · {a.profile.phone} · <Link to="/volt/me/profile">Edit name or contact details</Link></p>
          <div className="v-split">
            <Field id="v-city" label="Current city" error={fe('city')}><input id="v-city" value={h.city} autoComplete="address-level2" onChange={(e) => setHiring({ city: e.target.value })} /></Field>
            <Field id="v-state" label="State" error={fe('state')}><input id="v-state" value={h.state} autoComplete="address-level1" maxLength={40} onChange={(e) => setHiring({ state: e.target.value })} /></Field>
          </div>
          <div className="v-split">
            <Field id="v-zip" label="ZIP code" error={fe('zip_code')}><input id="v-zip" value={h.zip_code} inputMode="numeric" autoComplete="postal-code" maxLength={10} aria-invalid={fe('zip_code') ? 'true' : undefined} onChange={(e) => setHiring({ zip_code: e.target.value })} /></Field>
            {full && <Field id="v-start" label="Earliest available start date" error={fe('earliest_start')}><input id="v-start" type="date" value={h.earliest_start} aria-invalid={fe('earliest_start') ? 'true' : undefined} onChange={(e) => setHiring({ earliest_start: e.target.value })} /></Field>}
          </div>
          {!full && <p className="v-small v-muted">Where you live now. The next steps ask about travel.</p>}
          {a.referrer && <p className="v-small" data-referral>Referred by <b>{a.referrer.display_name} · {a.referrer.rep_code}</b></p>}
          {full && <Field id="v-regions" label="Preferred work locations or regions" hint="Cities, states or regions you would work in."><textarea id="v-regions" value={h.preferred_regions} maxLength={500} onChange={(e) => setHiring({ preferred_regions: e.target.value })} /></Field>}
          {full && <fieldset className="v-fieldset" data-availability>
            <legend>Availability</legend>
            {opts.map(([k, l]) => (
              <label key={k} className="v-check"><input type="checkbox" checked={avail.types.includes(k)}
                onChange={(e) => setHiring({ availability: { ...avail, types: e.target.checked ? [...avail.types, k] : avail.types.filter((t) => t !== k) } })} /> {l}</label>
            ))}
            <label htmlFor="v-avnotes" style={{ marginTop: 8 }}>Specific days or hours <span className="v-muted">(optional)</span>
              <input id="v-avnotes" value={avail.notes} maxLength={300} onChange={(e) => setHiring({ availability: { ...avail, notes: e.target.value } })} /></label>
          </fieldset>}
        </section>
      )}

      {editable && cur.id === 'experience' && (
        <section className="v-panel v-form" data-step="experience">
          <h3>Experience</h3>
          <label className="v-check v-consent"><input type="checkbox" checked={!!h.no_experience} data-no-experience
            onChange={(e) => setHiring({ no_experience: e.target.checked })} /> No previous work experience</label>
          {!h.no_experience && (
            <>
              {roles.length === 0 && <p className="v-muted">Add your jobs, starting with the most recent.</p>}
              {roles.map((r, i) => (
                <fieldset key={i} className="v-fieldset v-role" data-role={i}>
                  <legend>Job {i + 1}</legend>
                  <div className="v-split">
                    <Field id={`v-emp-${i}`} label="Employer or company"><input id={`v-emp-${i}`} value={r.employer} maxLength={120} onChange={(e) => setRole(i, { employer: e.target.value })} /></Field>
                    <Field id={`v-title-${i}`} label="Job title"><input id={`v-title-${i}`} value={r.title} maxLength={120} onChange={(e) => setRole(i, { title: e.target.value })} /></Field>
                  </div>
                  <div className="v-split">
                    <Field id={`v-rs-${i}`} label="Start month" error={fe(`work_history.${i}.start`)}><input id={`v-rs-${i}`} type="month" placeholder="YYYY-MM" value={r.start} onChange={(e) => setRole(i, { start: e.target.value })} /></Field>
                    <Field id={`v-re-${i}`} label="End month" error={fe(`work_history.${i}.end`)}><input id={`v-re-${i}`} type="month" placeholder="YYYY-MM" value={r.end} disabled={r.current} onChange={(e) => setRole(i, { end: e.target.value })} /></Field>
                  </div>
                  <label className="v-check"><input type="checkbox" checked={!!r.current} onChange={(e) => setRole(i, { current: e.target.checked, end: e.target.checked ? '' : r.end })} /> I currently work here</label>
                  <Field id={`v-sum-${i}`} label="Responsibilities or results"><textarea id={`v-sum-${i}`} value={r.summary} maxLength={1000} onChange={(e) => setRole(i, { summary: e.target.value })} /></Field>
                  <button type="button" className="v-btn v-btn--line v-btn--sm" onClick={() => setHiring({ work_history: roles.filter((_, j) => j !== i) })}>Remove job {i + 1}</button>
                </fieldset>
              ))}
              {roles.length < 10 && <button type="button" className="v-btn v-btn--line" onClick={() => setHiring({ work_history: [...roles, { ...EMPTY_ROLE }] })}>+ Add a job</button>}
            </>
          )}
          <Field id="v-relexp" label="Relevant sales, door-to-door, customer service, recruiting or field experience" hint="Optional."><textarea id="v-relexp" value={h.relevant_experience} maxLength={2000} onChange={(e) => setHiring({ relevant_experience: e.target.value })} /></Field>
          {resume && <DocRow d={resume} applicationId={null} editable onDone={docsChanged} />}
          <Field id="v-extra" label="Anything else you want our team to consider" hint="Optional."><textarea id="v-extra" value={h.additional_info} maxLength={2000} onChange={(e) => setHiring({ additional_info: e.target.value })} /></Field>
        </section>
      )}

      {editable && cur.section && (
        <section className="v-panel v-form" data-step={cur.section === 'project' ? 'project' : cur.section}>
          <h3>{cur.section === 'project' ? a.project.name : (cur.title || cur.label)}</h3>
          {cur.intro && <p className="v-small v-muted">{cur.intro}</p>}
          {!full && cur.section === firstSection && (
            <div data-homebase>
              <div className="v-split">
                <Field id="v-city" label="Current city" error={fe('city')}><input id="v-city" value={h.city} autoComplete="address-level2" onChange={(e) => setHiring({ city: e.target.value })} /></Field>
                <Field id="v-state" label="State" error={fe('state')}><input id="v-state" value={h.state} autoComplete="address-level1" maxLength={40} onChange={(e) => setHiring({ state: e.target.value })} /></Field>
              </div>
              <Field id="v-zip" label="ZIP code" error={fe('zip_code')}><input id="v-zip" value={h.zip_code} inputMode="numeric" autoComplete="postal-code" maxLength={10} aria-invalid={fe('zip_code') ? 'true' : undefined} onChange={(e) => setHiring({ zip_code: e.target.value })} /></Field>
              {a.referrer && <p className="v-small" data-referral>Referred by <b>{a.referrer.display_name} · {a.referrer.rep_code}</b></p>}
            </div>
          )}
          {q.filter((qq) => sectionOf(qq) === cur.section && shown(qq, ans)).map((qq) => (qq.type === 'choice' ? (
            <fieldset key={qq.key} className="v-fieldset" data-q={qq.key}>
              <legend>{qq.label}{qq.required ? '' : ' (optional)'}</legend>
              <div className="v-radios">
                {qq.options.map(([k, l]) => (
                  <label key={k} className="v-radio"><input type="radio" name={`q-${qq.key}`} value={k} checked={ans[qq.key] === k} onChange={() => setAnswer(qq.key, k)} /> {l}</label>
                ))}
              </div>
              {fe(qq.key) && <span className="v-ferr">{fe(qq.key)}</span>}
            </fieldset>
          ) : qq.type === 'date' ? (
            <Field key={qq.key} id={`v-q-${qq.key}`} label={qq.label} hint={qq.hint} error={fe(qq.key)}>
              <input id={`v-q-${qq.key}`} type="date" data-q={qq.key} value={ans[qq.key] || ''} onChange={(e) => setAnswer(qq.key, e.target.value)} />
            </Field>
          ) : qq.type === 'confirm' ? (
            <div key={qq.key} data-q={qq.key}>
              <label className="v-check v-consent"><input type="checkbox" checked={ans[qq.key] === 'yes'} onChange={(e) => setAnswer(qq.key, e.target.checked ? 'yes' : '')} /> {qq.label}</label>
              {fe(qq.key) && <span className="v-ferr">{fe(qq.key)}</span>}
            </div>
          ) : (
            <Field key={qq.key} id={`v-q-${qq.key}`} hint={qq.hint} label={qq.label + ((qq.required || (qq.required_if && ans[qq.required_if[0]] === qq.required_if[1])) ? '' : ' (optional)')}>
              <textarea id={`v-q-${qq.key}`} data-q={qq.key} value={ans[qq.key] || ''} maxLength={4000} onChange={(e) => setAnswer(qq.key, e.target.value)} />
            </Field>
          )))}
          {!full && cur.section === 'experience' && resume && (
            <div data-resume>
              <p className="v-small v-muted" style={{ marginTop: 6 }}>A resume is optional. You can submit without one.</p>
              <DocRow d={resume} applicationId={null} editable onDone={docsChanged} />
            </div>
          )}
          {!full && cur.section === lastSection && (
            <Field id="v-extra" label="Anything else you want our team to consider" hint="Optional."><textarea id="v-extra" value={h.additional_info} maxLength={2000} onChange={(e) => setHiring({ additional_info: e.target.value })} /></Field>
          )}
          {step === docStep && (a.documents.length > 0 || otherProfileDocs.length > 0) && (
            <div>
              <p className="v-label" style={{ marginTop: 6 }}>Requested documents</p>
              {a.documents.map((d) => <DocRow key={'a' + d.key} d={d} applicationId={a.id} editable onDone={docsChanged} />)}
              {otherProfileDocs.map((d) => <DocRow key={'p' + d.key} d={d} applicationId={null} editable onDone={docsChanged} />)}
            </div>
          )}
        </section>
      )}

      {(cur.id === 'review' || !editable) && (
        <section className="v-panel" data-step="review">
          <h3>{editable ? 'Review & submit' : 'Your application'}</h3>
          {a.next_action && <div className={`v-alert ${a.next_action.kind === 'applicant' ? 'v-alert--info' : 'v-alert--ok'}`} style={{ marginBottom: 12 }}><b>{a.next_action.label}.</b> {a.next_action.detail}</div>}
          {editable && missing.length > 0 && (
            <div className="v-alert v-alert--err" role="alert" data-missing>
              <b>Before you submit:</b>
              <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                {missing.map((m) => <li key={m}>{m} <button type="button" className="v-btn v-btn--link" onClick={() => go(stepOf(STEPS, m))}>Fix</button></li>)}
              </ul>
            </div>
          )}
          <dl className="v-review">
            <dt>Location</dt><dd>{[h.city, h.state, h.zip_code].filter(Boolean).join(', ') || '—'}</dd>
            {full && <>
              <dt>Preferred regions</dt><dd>{h.preferred_regions || '—'}</dd>
              <dt>Earliest start</dt><dd>{h.earliest_start ? fmtDay(h.earliest_start) : '—'}</dd>
              <dt>Availability</dt><dd>{avail.types.map((t) => (opts.find((o) => o[0] === t) || [t, t])[1]).join(', ') || '—'}{avail.notes ? ` · ${avail.notes}` : ''}</dd>
              <dt>Experience</dt><dd>{h.no_experience ? 'No previous work experience' : roles.length ? (
                <ul style={{ margin: 0, paddingLeft: 18 }}>{roles.map((r, i) => <li key={i}><b>{r.title || '—'}</b>, {r.employer || '—'} · {fmtMonth(r.start)} – {r.current ? 'Present' : fmtMonth(r.end)}{r.summary ? ` · ${r.summary}` : ''}</li>)}</ul>) : '—'}</dd>
              <dt>Relevant experience</dt><dd>{h.relevant_experience || '—'}</dd>
            </>}
            <dt>Resume</dt><dd>{resume?.document ? resume.document.filename : 'Not provided (optional)'}</dd>
            <dt>Anything else</dt><dd>{h.additional_info || '—'}</dd>
            <dt>Referred by</dt><dd>{a.referrer ? `${a.referrer.display_name} · ${a.referrer.rep_code}` : 'Direct'}</dd>
          </dl>
          {STEPS.filter((x) => x.section).map((x) => (
            <div key={x.id} data-review-section={x.section}>
              {x.section !== 'project' && <p className="v-label" style={{ marginTop: 14 }}>{x.label}</p>}
              <dl className="v-review">
                {q.filter((qq) => sectionOf(qq) === x.section && shown(qq, ans)).map((qq) => <Fragment key={qq.key}><dt>{qq.label}</dt><dd>{answerLabel(qq)}</dd></Fragment>)}
              </dl>
            </div>
          ))}
          {earlier.length > 0 && (
            <div data-review-section="earlier">
              <p className="v-label" style={{ marginTop: 14 }}>Earlier answers</p>
              <dl className="v-review">
                {earlier.map((qq) => <Fragment key={qq.key}><dt>{qq.label}</dt><dd>{answerLabel(qq)}</dd></Fragment>)}
              </dl>
            </div>
          )}
        </section>
      )}

      {err && <div className="v-alert v-alert--err" role="alert" style={{ marginBottom: 10 }}>{err}</div>}
      {editable && (
        <div className="v-stepnav">
          {step > 0 ? <button type="button" className="v-btn v-btn--line" onClick={() => go(step - 1)}>← Back</button> : <span />}
          <span className="v-small v-muted" aria-live="polite">{saved}</span>
          {step < LAST
            ? <button type="button" className="v-btn v-btn--red" onClick={() => go(step + 1)} data-next>Next →</button>
            : <button type="button" className="v-btn v-btn--red" onClick={submit} disabled={busy || missing.length > 0} data-submit>{busy ? 'Submitting…' : a.status === 'needs_update' ? 'Resubmit application →' : 'Submit application →'}</button>}
        </div>
      )}
    </div>
  )
}
