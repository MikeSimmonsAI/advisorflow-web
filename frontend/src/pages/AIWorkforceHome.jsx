/**
 * AI WORKFORCE — the one front door. SET UP | WORKING | NEEDS YOU | RESULTS.
 *
 * WHO THIS IS FOR. A business owner who has never set up software like this.
 * Every word on this screen is written for an eighth-grade reader: what an AI
 * worker does, what it needs, what happened, why, and what to do next. No
 * internal names, model names, settings jargon or codes are shown.
 *
 * WHAT IS TRUE ON THIS SCREEN, AND WHERE IT COMES FROM. Everything is read
 * from GET /ai-workforce/home, which counts real rows on the server. Nothing
 * here invents a number, a percentage or activity. An empty list says "nothing
 * yet"; a rate that would mislead says N/A and why.
 *
 * SAFE BY DESIGN. Test mode is the only mode a customer can switch on here. In
 * test mode the AI worker practices on pretend leads and never texts, emails
 * or calls a real person — the server refuses anything else. Live messages are
 * a separate, later step taken with the platform.
 *
 * Data:
 *   GET    /ai-workforce/home
 *   GET    /ai-workforce/catalog/:key/questions
 *   POST   /ai-workforce/deployments
 *   GET    /ai-workforce/deployments/:id
 *   PATCH  /ai-workforce/deployments/:id/configuration
 *   GET    /ai-workforce/deployments/:id/test-mode
 *   POST   /ai-workforce/deployments/:id/test-mode/run|start|pause|resume|stop
 *   POST   /ai-workforce/deployments/:id/retire
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import PageShell from '../components/PageShell'
import '../styles/shared.css'
import '../styles/aiWorkforce.css'

const TABS = [
  { key: 'setup', label: 'Set Up' },
  { key: 'working', label: 'Working' },
  { key: 'needs', label: 'Needs You' },
  { key: 'results', label: 'Results' },
]

const STATUS_TONE = {
  not_set_up: 'amber',
  ready_to_test: 'blue',
  test_mode_on: 'green',
  paused: 'amber',
  stopped: 'neutral',
  live: 'green',
  retired: 'neutral',
}

const AVAIL_TONE = {
  available: 'green',
  set_up: 'blue',
  coming_soon: 'neutral',
  not_on_plan: 'amber',
  not_offered: 'neutral',
}

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

const HANDOFF_REASONS = [
  'The person asks to talk to a real person',
  'The person is upset or angry',
  'The person asks about money or billing',
  'The person wants to cancel',
]

const WIZARD_STEPS = [
  { key: 'business', label: 'Your business' },
  { key: 'hours', label: 'When and how' },
  { key: 'handoff', label: 'Your team' },
  { key: 'test', label: 'Run test' },
  { key: 'start', label: 'Start working' },
]

function errorText (err) {
  const d = err?.data?.detail ?? err?.detail
  if (typeof d === 'string') return d
  if (d && typeof d.message === 'string') return d.message
  return 'Something went wrong. Please try again.'
}

function Badge ({ tone, children }) {
  return <span className={`badge badge--${tone || 'neutral'}`}>{children}</span>
}

function Problem ({ p, onFix }) {
  return (
    <li className="wf-problem">
      <div className="wf-problem__what">{p.what}</div>
      {p.why ? <div className="wf-problem__why"><strong>Why:</strong> {p.why}</div> : null}
      <div className="wf-problem__next">
        <strong>What to do:</strong> {p.next}
        {p.who === 'admin' ? <span className="wf-problem__who"> (only an administrator can fix this)</span> : null}
      </div>
      {p.fix && onFix ? (
        <button type="button" className="btn btn--secondary btn--sm" onClick={() => onFix(p)}>
          Fix this
        </button>
      ) : null}
    </li>
  )
}

function ProblemList ({ problems, onFix }) {
  if (!problems?.length) return null
  return <ul className="wf-problems">{problems.map((p, i) => <Problem key={p.code + i} p={p} onFix={onFix} />)}</ul>
}

function LiveNote ({ note }) {
  return (
    <div className="wf-livenote" role="note">
      <strong>Safe test mode.</strong> {note}
    </div>
  )
}

// ── SET UP: the job cards ────────────────────────────────────────────────────

function JobCard ({ card, canManage, busy, onSetUp, onOpen }) {
  return (
    <article className="wf-job panel" aria-labelledby={`job-${card.key}`}>
      <div className="wf-job__head">
        <h3 id={`job-${card.key}`}>{card.title}</h3>
        <Badge tone={AVAIL_TONE[card.availability]}>{card.availability_label}</Badge>
      </div>
      <p className="wf-job__does">{card.does}</p>
      <p className="wf-job__needs"><strong>It needs:</strong> {card.needs}</p>
      {card.availability === 'set_up' ? (
        <button type="button" className="btn btn--secondary" onClick={() => onOpen(card.deployment_ids[0])}>
          Open
        </button>
      ) : card.can_set_up ? (
        canManage ? (
          <button type="button" className="btn btn--primary btn--lg" disabled={busy}
            onClick={() => onSetUp(card)}>
            {busy ? 'Setting up…' : 'Set this up'}
          </button>
        ) : (
          <p className="wf-muted">Ask your administrator to set this up.</p>
        )
      ) : (
        <p className="wf-muted">{card.why}</p>
      )}
    </article>
  )
}

// ── SET UP: the wizard ───────────────────────────────────────────────────────

function Progress ({ step }) {
  const n = WIZARD_STEPS.findIndex(s => s.key === step)
  return (
    <div className="wf-progress" aria-label={`Step ${n + 1} of ${WIZARD_STEPS.length}`}>
      <div className="wf-progress__text">Step {n + 1} of {WIZARD_STEPS.length}: {WIZARD_STEPS[n]?.label}</div>
      <div className="wf-progress__bar" role="progressbar" aria-valuemin={1}
        aria-valuemax={WIZARD_STEPS.length} aria-valuenow={n + 1}>
        <span style={{ width: `${((n + 1) / WIZARD_STEPS.length) * 100}%` }} />
      </div>
    </div>
  )
}

function TextQ ({ id, label, help, value, onChange, placeholder, multiline }) {
  return (
    <div className="wf-q">
      <label htmlFor={id} className="wf-q__label">{label}</label>
      {help ? <div className="wf-q__help" id={`${id}-help`}>{help}</div> : null}
      {multiline ? (
        <textarea id={id} className="input" rows={3} value={value || ''} placeholder={placeholder}
          aria-describedby={help ? `${id}-help` : undefined}
          onChange={e => onChange(e.target.value)} />
      ) : (
        <input id={id} className="input" value={value || ''} placeholder={placeholder}
          aria-describedby={help ? `${id}-help` : undefined}
          onChange={e => onChange(e.target.value)} />
      )}
    </div>
  )
}

function lines (text) {
  return (text || '').split(/\n|;/).map(s => s.trim()).filter(Boolean)
}

/** The ordinary answers -> the configuration the server already understands. */
function toConfiguration (a, templateKey) {
  const hours = { days: a.days, start: a.start, end: a.end }
  const sells = (a.sells || '').trim()
  const cfg = {
    goal: sells
      ? `Find out which people interested in ${sells} are ready to talk.`
      : 'Find out which leads are interested and ready.',
    goals: sells ? [`Ready leads for ${sells}`] : ['Ready leads'],
    business_unit: (a.business || '').trim() || undefined,
    service_area: (a.customers || '').trim() || undefined,
    good_lead: lines(a.goodLead).length ? lines(a.goodLead) : ['Wants to buy soon'],
    audience: { statuses: ['new'] },
    hours,
    working_hours: hours,
    timezone: a.timezone || 'America/Chicago',
    channels: a.channels,
    escalation_conditions: a.escalate,
  }
  if (a.handoffUser) {
    cfg.handoff_to = a.handoffUser
    cfg.handoff_user_id = a.handoffUser
    cfg.booking_owner = a.handoffUser
  }
  if (templateKey === 'appointment_setter' && a.appointmentType) {
    cfg.appointment_type = a.appointmentType
  }
  Object.keys(cfg).forEach(k => cfg[k] === undefined && delete cfg[k])
  return cfg
}

function fromConfiguration (cfg, businessName) {
  const h = cfg?.working_hours || cfg?.hours || {}
  return {
    business: cfg?.business_unit || businessName || '',
    sells: '',
    customers: cfg?.service_area || '',
    goodLead: (cfg?.good_lead || []).join('\n'),
    days: h.days || [0, 1, 2, 3, 4],
    start: h.start || '09:00',
    end: h.end || '17:00',
    timezone: cfg?.timezone || 'America/Chicago',
    channels: cfg?.channels?.length ? cfg.channels : ['sms', 'email'],
    handoffUser: cfg?.handoff_user_id || cfg?.handoff_to || '',
    escalate: cfg?.escalation_conditions?.length ? cfg.escalation_conditions : HANDOFF_REASONS.slice(0, 2),
    appointmentType: cfg?.appointment_type || '',
  }
}

function TestReport ({ report }) {
  if (!report) return null
  const pass = report.verdict === 'PASS'
  return (
    <section className={`wf-report ${pass ? 'wf-report--pass' : 'wf-report--attn'}`} aria-live="polite">
      <h3>{report.headline}</h3>
      {report.ran ? (
        <>
          <p>
            We made a pretend lead named <strong>{report.practice_lead?.name}</strong>. Nobody real was contacted.
            {report.tested_outside_hours ? ' It is outside your working hours right now, so we tested it as if it were during your hours.' : ''}
          </p>
          <p><strong>Result:</strong> {report.ended_as}</p>
          <h4>What your AI worker did</h4>
          <ol className="wf-steps">
            {(report.steps || []).map((s, i) => <li key={i}>{s.step}</li>)}
          </ol>
          {!!report.what_it_tried?.length && (
            <>
              <h4>Actions it took</h4>
              <ul className="wf-steps">
                {report.what_it_tried.map((t, i) => (
                  <li key={i}>
                    {t.action}{t.allowed ? ((t.tool || '').startsWith('conversation.send') ? ' (practice only — nothing was sent)' : '') : ` — stopped: ${t.refused_because}`}
                  </li>
                ))}
              </ul>
            </>
          )}
          {!!report.handoffs?.length && (
            <p>It passed the lead to a person on your team, just like it will with real leads. You can see it under <strong>Needs You</strong>.</p>
          )}
          {report.pretend_reply_sent ? (
            <p className="wf-muted">To finish the test, the pretend lead wrote back: “yes, I’m interested.”</p>
          ) : null}
        </>
      ) : null}
      <ProblemList problems={report.problems} />
    </section>
  )
}

function SetupWizard ({ deploymentId, businessName, canManage, onClose, onChanged }) {
  const [params, setParams] = useSearchParams()
  const step = params.get('step') || 'business'
  const [dep, setDep] = useState(null)
  const [questions, setQuestions] = useState(null)
  const [answers, setAnswers] = useState(null)
  const [summary, setSummary] = useState(null)
  const [report, setReport] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const goStep = useCallback((key) => {
    const next = new URLSearchParams(params)
    next.set('step', key)
    setParams(next, { replace: false })
    setError('')
  }, [params, setParams])

  const load = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([
        api.get(`/ai-workforce/deployments/${deploymentId}`),
        api.get(`/ai-workforce/deployments/${deploymentId}/test-mode`),
      ])
      setDep(d)
      setSummary(s)
      const q = await api.get(`/ai-workforce/catalog/${s.job}/questions`).catch(() => null)
      setQuestions(q)
      setAnswers(prev => prev || fromConfiguration(d.configuration || {}, businessName))
    } catch (err) {
      setError(errorText(err))
    }
  }, [deploymentId, businessName])

  useEffect(() => { load() }, [deploymentId]) // eslint-disable-line react-hooks/exhaustive-deps

  const people = useMemo(() => {
    const f = (questions?.fields || []).find(x => x.key === 'handoff_user_id' || x.key === 'handoff_to')
    return f?.options || []
  }, [questions])
  const templateKey = summary?.job

  const set = (k, v) => setAnswers(a => ({ ...a, [k]: v }))

  async function save (nextStep) {
    if (!canManage) { goStep(nextStep); return }
    setBusy(true); setError('')
    try {
      await api.patch(`/ai-workforce/deployments/${deploymentId}/configuration`,
        { configuration: toConfiguration(answers, templateKey) })
      const s = await api.get(`/ai-workforce/deployments/${deploymentId}/test-mode`)
      setSummary(s)
      onChanged && onChanged()
      goStep(nextStep)
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  async function runTest () {
    setBusy(true); setError(''); setReport(null)
    try {
      const r = await api.post(`/ai-workforce/deployments/${deploymentId}/test-mode/run`, {})
      setReport(r)
      setSummary(await api.get(`/ai-workforce/deployments/${deploymentId}/test-mode`))
      onChanged && onChanged()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  async function act (what) {
    setBusy(true); setError('')
    try {
      const r = await api.post(`/ai-workforce/deployments/${deploymentId}/test-mode/${what}`, {})
      setSummary(r.summary)
      onChanged && onChanged()
      if (what === 'start' && r.result?.started === false) {
        setError('Your AI worker could not start yet. See what to fix below.')
      }
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  if (!answers || !summary) {
    return <div className="panel wf-empty">{error || 'Loading your AI worker…'}</div>
  }

  const fixStep = (p) => goStep(p.fix === 'handoff' ? 'handoff' : 'business')

  return (
    <section className="panel wf-wizard" aria-label="Set up your AI worker">
      <div className="wf-wizard__top">
        <div>
          <h2 className="wf-wizard__title">{summary.name || 'Your AI worker'}</h2>
          <Badge tone={STATUS_TONE[summary.status]}>{summary.status_label}</Badge>
        </div>
        <button type="button" className="btn btn--ghost btn--sm" onClick={onClose}>Close</button>
      </div>
      <Progress step={step} />
      {!canManage ? <p className="wf-muted">Only an administrator can change these answers.</p> : null}
      {error ? <div className="wf-error" role="alert">{error}</div> : null}

      {step === 'business' && (
        <div className="wf-qs">
          <p className="wf-intro">Tell us about your business. Short answers are fine. You can change them later.</p>
          <TextQ id="q-business" label="What is your business called?" value={answers.business} onChange={v => set('business', v)} />
          <TextQ id="q-sells" label="What do you sell or provide?" placeholder="Example: home insurance" value={answers.sells} onChange={v => set('sells', v)} />
          <TextQ id="q-customers" label="Who are your customers?" placeholder="Example: families in Dallas" value={answers.customers} onChange={v => set('customers', v)} />
          <TextQ id="q-good" label="What makes someone a good lead?" help="One idea per line." multiline
            placeholder={'Wants a quote this month\nIs the one who decides'} value={answers.goodLead} onChange={v => set('goodLead', v)} />
          <div className="wf-nav">
            <span />
            <button type="button" className="btn btn--primary btn--lg" disabled={busy} onClick={() => save('hours')}>
              {busy ? 'Saving…' : 'Save and continue'}
            </button>
          </div>
        </div>
      )}

      {step === 'hours' && (
        <div className="wf-qs">
          <p className="wf-intro">When may your AI worker reach people, and how?</p>
          <fieldset className="wf-q">
            <legend className="wf-q__label">Which days?</legend>
            <div className="wf-chips">
              {WEEKDAYS.map((d, i) => (
                <label key={d} className="wf-chip">
                  <input type="checkbox" checked={answers.days.includes(i)}
                    onChange={e => set('days', e.target.checked ? [...answers.days, i].sort() : answers.days.filter(x => x !== i))} />
                  {d}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="wf-q wf-q--row">
            <label className="wf-q__label" htmlFor="q-start">From</label>
            <input id="q-start" type="time" className="input" value={answers.start} onChange={e => set('start', e.target.value)} />
            <label className="wf-q__label" htmlFor="q-end">to</label>
            <input id="q-end" type="time" className="input" value={answers.end} onChange={e => set('end', e.target.value)} />
          </div>
          <div className="wf-q">
            <label className="wf-q__label" htmlFor="q-tz">Your time zone</label>
            <select id="q-tz" className="input" value={answers.timezone} onChange={e => set('timezone', e.target.value)}>
              <option value="America/New_York">Eastern</option>
              <option value="America/Chicago">Central</option>
              <option value="America/Denver">Mountain</option>
              <option value="America/Phoenix">Arizona</option>
              <option value="America/Los_Angeles">Pacific</option>
            </select>
          </div>
          <fieldset className="wf-q">
            <legend className="wf-q__label">How may it reach people?</legend>
            <div className="wf-chips">
              {[['sms', 'Text message'], ['email', 'Email']].map(([v, l]) => (
                <label key={v} className="wf-chip">
                  <input type="checkbox" checked={answers.channels.includes(v)}
                    onChange={e => set('channels', e.target.checked ? [...answers.channels, v] : answers.channels.filter(x => x !== v))} />
                  {l}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="wf-nav">
            <button type="button" className="btn btn--secondary" onClick={() => goStep('business')}>Back</button>
            <button type="button" className="btn btn--primary btn--lg" disabled={busy || !answers.days.length || !answers.channels.length}
              onClick={() => save('handoff')}>
              {busy ? 'Saving…' : 'Save and continue'}
            </button>
          </div>
        </div>
      )}

      {step === 'handoff' && (
        <div className="wf-qs">
          <p className="wf-intro">Your AI worker always passes people to a real person when it should.</p>
          <div className="wf-q">
            <label className="wf-q__label" htmlFor="q-person">Who should get leads that are ready?</label>
            <select id="q-person" className="input" value={answers.handoffUser} onChange={e => set('handoffUser', e.target.value)}>
              <option value="">Pick a person…</option>
              {people.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
          <fieldset className="wf-q">
            <legend className="wf-q__label">When should your AI worker ask a person for help?</legend>
            <div className="wf-checklist">
              {HANDOFF_REASONS.map(r => (
                <label key={r} className="wf-check">
                  <input type="checkbox" checked={answers.escalate.includes(r)}
                    onChange={e => set('escalate', e.target.checked ? [...answers.escalate, r] : answers.escalate.filter(x => x !== r))} />
                  {r}
                </label>
              ))}
            </div>
          </fieldset>
          {templateKey === 'appointment_setter' && (
            <TextQ id="q-appt" label="What kind of appointment should it book?" placeholder="Example: Free consultation"
              value={answers.appointmentType} onChange={v => set('appointmentType', v)} />
          )}
          <div className="wf-nav">
            <button type="button" className="btn btn--secondary" onClick={() => goStep('hours')}>Back</button>
            <button type="button" className="btn btn--primary btn--lg" disabled={busy || !answers.handoffUser}
              onClick={() => save('test')}>
              {busy ? 'Saving…' : 'Save and continue'}
            </button>
          </div>
        </div>
      )}

      {step === 'test' && (
        <div className="wf-qs">
          <p className="wf-intro">
            Let’s make sure it works. We will make one pretend lead and let your AI worker handle it from start to finish.
            Nobody real is contacted.
          </p>
          {!summary.can_run_test ? (
            <>
              <p><strong>Before you can test, fix these:</strong></p>
              <ProblemList problems={summary.test_blockers} onFix={fixStep} />
            </>
          ) : null}
          <button type="button" className="btn btn--primary btn--lg wf-bigbutton"
            disabled={busy || !summary.can_run_test || !canManage} onClick={runTest}>
            {busy ? 'Testing… this takes a few seconds' : 'Run test'}
          </button>
          <TestReport report={report} />
          {!report && summary.last_test ? (
            <p className="wf-muted">Last test: {summary.last_test.verdict === 'PASS' ? 'PASS' : 'NEEDS ATTENTION'}.</p>
          ) : null}
          <div className="wf-nav">
            <button type="button" className="btn btn--secondary" onClick={() => goStep('handoff')}>Back</button>
            <button type="button" className="btn btn--primary"
              disabled={!(report?.verdict === 'PASS' || summary.last_test?.verdict === 'PASS')}
              onClick={() => goStep('start')}>
              Continue
            </button>
          </div>
        </div>
      )}

      {step === 'start' && (
        <div className="wf-qs">
          <LiveNote note={summary.live_messages_note} />
          {summary.status === 'test_mode_on' ? (
            <p className="wf-ok"><strong>Your AI worker is working in test mode.</strong> Watch it on the Working tab.</p>
          ) : null}
          {!summary.can_start && summary.status !== 'test_mode_on' ? (
            <>
              <p><strong>It can’t start yet:</strong></p>
              <ProblemList problems={summary.start_blockers} onFix={fixStep} />
            </>
          ) : null}
          <button type="button" className="btn btn--primary btn--lg wf-bigbutton"
            disabled={busy || !summary.can_start || !canManage} onClick={() => act('start')}>
            {busy ? 'Starting…' : 'Start working'}
          </button>
          {!!summary.needed_later_for_live?.length && (
            <details className="wf-later">
              <summary>Needed later, for real messages</summary>
              <ul>{summary.needed_later_for_live.map(c => <li key={c.code}>{c.what}</li>)}</ul>
            </details>
          )}
          <div className="wf-nav">
            <button type="button" className="btn btn--secondary" onClick={() => goStep('test')}>Back</button>
            <button type="button" className="btn btn--secondary" onClick={onClose}>Done</button>
          </div>
        </div>
      )}
    </section>
  )
}

// ── WORKING ──────────────────────────────────────────────────────────────────

function WorkerCard ({ w, canManage, onAct, onOpen, busy }) {
  const b = w.board || {}
  return (
    <article className="panel wf-worker" aria-labelledby={`w-${w.deployment_id}`}>
      <div className="wf-worker__head">
        <div>
          <h3 id={`w-${w.deployment_id}`}>{w.name}</h3>
          <Badge tone={STATUS_TONE[w.status]}>{w.status_label}</Badge>
          {b.last_run_at ? <span className="wf-muted"> · Last worked {new Date(b.last_run_at).toLocaleString()}</span> : null}
        </div>
        {canManage ? (
          <div className="wf-worker__actions">
            {w.can_pause ? <button type="button" className="btn btn--secondary btn--sm" disabled={busy} onClick={() => onAct(w, 'pause')}>Pause</button> : null}
            {w.can_resume ? <button type="button" className="btn btn--primary btn--sm" disabled={busy} onClick={() => onAct(w, 'resume')}>Resume</button> : null}
            {w.can_stop ? <button type="button" className="btn btn--danger btn--sm" disabled={busy} onClick={() => onAct(w, 'stop')}>Stop</button> : null}
            {!w.can_stop && !w.can_resume ? <button type="button" className="btn btn--secondary btn--sm" onClick={() => onOpen(w.deployment_id)}>Set up / Start</button> : null}
          </div>
        ) : null}
      </div>
      <div className="wf-stat-grid">
        {(b.groups || []).map(g => (
          <div key={g.key} className="wf-stat">
            <span className="wf-stat__label">{g.label}</span>
            <span className="wf-stat__value">{g.count}</span>
          </div>
        ))}
      </div>
      {b.has_any_work ? (
        <table className="wf-table">
          <caption className="wf-muted">Most recent work</caption>
          <thead><tr><th scope="col">Lead</th><th scope="col">What is happening</th><th scope="col">Updated</th></tr></thead>
          <tbody>
            {(b.items || []).slice(0, 10).map(it => (
              <tr key={it.id}>
                <td>{it.who}{it.practice ? <Badge tone="purple">Practice</Badge> : null}</td>
                <td>{it.status}</td>
                <td>{it.updated_at ? new Date(it.updated_at).toLocaleString() : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="wf-muted">Nothing yet. Work shows up here as soon as your AI worker does it.</p>
      )}
    </article>
  )
}

// ── RESULTS ──────────────────────────────────────────────────────────────────

function ResultsPanel ({ title, r, note }) {
  const rows = [
    ['Finished', r.finished],
    ['Ready for a person', r.qualified],
    ['Appointments', r.appointments],
    ['Passed to a person (other reasons)', r.handed_to_person],
    ['Said no', r.said_no],
    ['Could not reach', r.could_not_reach],
    ['Needs a person', r.needs_a_person],
    ['Still in progress', r.in_progress],
  ]
  return (
    <section className="panel wf-results">
      <h3>{title}</h3>
      {note ? <p className="wf-muted">{note}</p> : null}
      <div className="wf-stat-grid">
        {rows.map(([l, v]) => (
          <div key={l} className="wf-stat"><span className="wf-stat__label">{l}</span><span className="wf-stat__value">{v}</span></div>
        ))}
        <div className="wf-stat">
          <span className="wf-stat__label">Ready rate</span>
          <span className="wf-stat__value">{r.ready_rate?.shown ? `${r.ready_rate.value}%` : 'N/A'}</span>
          <span className="wf-stat__hint">{r.ready_rate?.how}</span>
        </div>
      </div>
    </section>
  )
}

// ── THE PAGE ─────────────────────────────────────────────────────────────────

export default function AIWorkforceHome () {
  const [params, setParams] = useSearchParams()
  const tab = TABS.some(t => t.key === params.get('tab')) ? params.get('tab') : 'setup'
  const openId = params.get('worker')
  const [home, setHome] = useState(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    try {
      setHome(await api.get('/ai-workforce/home'))
      setError('')
    } catch (err) {
      setError(errorText(err))
    }
  }, [])

  useEffect(() => { load() }, [load])

  const setTab = (key) => {
    const next = new URLSearchParams(params)
    next.set('tab', key)
    next.delete('worker'); next.delete('step')
    setParams(next)
  }
  const openWorker = (id, step) => {
    const next = new URLSearchParams(params)
    next.set('tab', 'setup'); next.set('worker', id)
    if (step) next.set('step', step); else next.delete('step')
    setParams(next)
  }
  const closeWorker = () => {
    const next = new URLSearchParams(params)
    next.delete('worker'); next.delete('step')
    setParams(next)
    load()
  }

  async function setUp (card) {
    setBusy(true); setError('')
    try {
      const created = await api.post('/ai-workforce/deployments', {
        template_key: card.key,
        name: `AI ${card.title}`,
        provisioning_key: `ui-${card.key}`,
      })
      await load()
      openWorker(created.id, 'business')
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  async function act (w, what) {
    if (what === 'stop' && !window.confirm(`Stop ${w.name}? It will stop working until you press Start working again. Nothing is deleted.`)) return
    setBusy(true); setError('')
    try {
      await api.post(`/ai-workforce/deployments/${w.deployment_id}/test-mode/${what}`, {})
      await load()
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  const needsCount = home?.needs_you?.length || 0
  const canManage = !!home?.can_manage

  return (
    <PageShell
      eyebrow="AI Workforce"
      title="Your AI workers"
      subtitle="Pick a job, answer a few questions, test it, and start it. It practices safely before it ever talks to a real customer."
    >
      {home ? <LiveNote note={home.live_messages_note} /> : null}
      <nav className="wf-tabs" role="tablist" aria-label="AI Workforce sections">
        {TABS.map(t => (
          <button key={t.key} type="button" role="tab" aria-selected={tab === t.key}
            className={`btn btn--sm ${tab === t.key ? 'btn--primary' : 'btn--ghost'}`}
            onClick={() => setTab(t.key)}>
            {t.label}{t.key === 'needs' && needsCount ? ` (${needsCount})` : ''}
          </button>
        ))}
      </nav>
      {error ? <div className="wf-error" role="alert">{error}</div> : null}
      {!home && !error ? <div className="panel wf-empty">Loading…</div> : null}

      {home && tab === 'setup' && (
        openId ? (
          <SetupWizard key={openId} deploymentId={openId} businessName={home.business_name}
            canManage={canManage} onClose={closeWorker} onChanged={load} />
        ) : (
          <>
            <h2 className="wf-h2">Step 1 — Pick your AI worker</h2>
            <div className="wf-jobs">
              {home.jobs.map(c => (
                <JobCard key={c.key} card={c} canManage={canManage} busy={busy}
                  onSetUp={setUp} onOpen={(id) => openWorker(id)} />
              ))}
            </div>
            {!!home.workers.length && (
              <>
                <h2 className="wf-h2">Your AI workers</h2>
                <ul className="wf-list">
                  {home.workers.map(w => (
                    <li key={w.deployment_id} className="panel wf-list__row">
                      <span><strong>{w.name}</strong> <Badge tone={STATUS_TONE[w.status]}>{w.status_label}</Badge></span>
                      <button type="button" className="btn btn--secondary btn--sm" onClick={() => openWorker(w.deployment_id)}>
                        {w.status === 'not_set_up' ? 'Continue setup' : 'Open'}
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )
      )}

      {home && tab === 'working' && (
        home.workers.length ? home.workers.map(w => (
          <WorkerCard key={w.deployment_id} w={w} canManage={canManage} busy={busy}
            onAct={act} onOpen={(id) => openWorker(id, 'start')} />
        )) : (
          <div className="panel wf-empty">
            <strong>You don’t have an AI worker yet.</strong>
            <span>Go to Set Up to pick one.</span>
            <button type="button" className="btn btn--primary" onClick={() => setTab('setup')}>Go to Set Up</button>
          </div>
        )
      )}

      {home && tab === 'needs' && (
        needsCount ? (
          <ul className="wf-needs">
            {home.needs_you.map((n, i) => (
              <li key={i} className="panel wf-need">
                <div className="wf-need__head">
                  <strong>{n.worker}</strong>
                  {n.practice ? <Badge tone="purple">Practice</Badge> : null}
                  {n.kind === 'handoff' ? <Badge tone="green">Ready for you</Badge> : null}
                </div>
                <ProblemList problems={[n]} onFix={n.deployment_id ? (p) => openWorker(n.deployment_id, p.fix === 'handoff' ? 'handoff' : 'business') : null} />
              </li>
            ))}
          </ul>
        ) : (
          <div className="panel wf-empty"><strong>Nothing needs you right now.</strong><span>When your AI worker needs a person, it shows up here.</span></div>
        )
      )}

      {home && tab === 'results' && (
        <>
          <ResultsPanel title="Real leads" r={home.results.real}
            note={home.results.real.finished ? null : 'No real leads have been worked yet. Real results start after live messages are turned on.'} />
          <ResultsPanel title="Practice (test mode)" r={home.results.practice} note={home.results.note} />
        </>
      )}

      {home && canManage ? (
        <p className="wf-muted wf-advanced">
          More detail: <Link to="/ai-workforce/team">team activity</Link>
          {' · '}<Link to="/ai-workforce/advanced">plan and setup details</Link>
          {' · '}<Link to="/ai-workforce/command">full report</Link>
        </p>
      ) : null}
    </PageShell>
  )
}
