/**
 * MY AI WORKFORCE — hiring, setting up and running AI employees.
 *
 * THE DIFFERENCE BETWEEN THIS SCREEN AND "YOUR AI TEAM". That one is the
 * OPERATIONAL view: who is working, what is in the queue, what needs a person.
 * This one is the DEPLOYMENT view: what this business could hire, what it has
 * hired, what each one still needs before it can start, and who is allowed to
 * start it. They are different questions asked by the same person on different
 * days, and merging them would make both worse.
 *
 * WHAT A CUSTOMER READS HERE: a job, what it does, whether their account has
 * it, a short interview about their business, a list of what is still missing,
 * and a button that asks for it to be switched on. What they never read is a
 * system prompt, a model name, a temperature or a tool key — the server does
 * not send any of those, and this screen could not render them if it wanted to.
 *
 * HIRING IS DELIBERATELY SEVERAL STEPS. An AI employee is created switched off,
 * setting it up is its own screen, readiness is shown before anybody can ask
 * for it to start, and starting it is a separate request that the platform
 * completes with the customer. A one-click "add employee" that immediately
 * began messaging families would be the wrong shape however carefully it was
 * worded.
 *
 * Data:
 *   GET    /ai-workforce/overview
 *   GET    /ai-workforce/catalog/:templateKey/questions
 *   POST   /ai-workforce/deployments
 *   GET    /ai-workforce/deployments/:id
 *   PATCH  /ai-workforce/deployments/:id/configuration
 *   POST   /ai-workforce/deployments/:id/activation | pause | resume | retire
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import PageShell from '../components/PageShell'
import '../styles/shared.css'

const STATE_TONE = {
  available: 'neutral',
  selected: 'blue',
  configuring: 'blue',
  validation_required: 'amber',
  ready: 'purple',
  controlled: 'amber',
  active: 'green',
  paused: 'amber',
  suspended: 'red',
  retired: 'neutral',
}

const VERDICT_TONE = {
  ready: 'green',
  not_ready: 'red',
  review_required: 'amber',
}

function StateBadge ({ state, label }) {
  const tone = STATE_TONE[state] || 'neutral'
  return <span className={`badge badge--${tone}`}>{label || state}</span>
}

function Money ({ commerce }) {
  if (!commerce) return null
  const tone = commerce.is_live ? 'green'
    : (commerce.commercial_state === 'pending' ? 'amber' : 'neutral')
  return <span className={`badge badge--${tone}`}>{commerce.commercial_label}</span>
}

function ReadinessPanel ({ readiness }) {
  if (!readiness) return null
  const tone = VERDICT_TONE[readiness.verdict] || 'neutral'
  const blocking = readiness.blocking || []
  const review = readiness.review || []
  return (
    <div style={{ marginTop: 12 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span className={`badge badge--${tone}`}>{readiness.verdict_label}</span>
        <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
          {readiness.passed_count} of {readiness.total} checks passed
        </span>
      </div>
      {!!blocking.length && (
        <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 13 }}>
          {blocking.map(c => (
            <li key={c.key} style={{ marginBottom: 4 }}>
              <strong>{c.label}.</strong>{' '}
              <span style={{ color: 'var(--text-secondary)' }}>{c.detail} {c.fix}</span>
            </li>
          ))}
        </ul>
      )}
      {!!review.length && (
        <ul style={{ margin: '8px 0 0', paddingLeft: 18, fontSize: 13 }}>
          {review.map(c => (
            <li key={c.key} style={{ marginBottom: 4, color: 'var(--text-secondary)' }}>
              {c.detail}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Field ({ field, value, onChange }) {
  const id = `f-${field.key}`
  const common = { id, className: 'input', style: { width: '100%' } }

  if (field.type === 'boolean') {
    return (
      <label htmlFor={id} style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
        <input id={id} type="checkbox" checked={!!value}
          onChange={e => onChange(e.target.checked)} />
        {field.label}
      </label>
    )
  }

  if (field.type === 'user' || field.type === 'appointment_type' ||
      field.type === 'ai_employee' || field.type === 'followup') {
    return (
      <select {...common} value={value || ''} onChange={e => onChange(e.target.value)}>
        <option value="">Not chosen</option>
        {(field.options || []).map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    )
  }

  if (field.type === 'channels' || field.type === 'knowledge') {
    const picked = Array.isArray(value) ? value : []
    return (
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        {(field.options || []).map(o => (
          <label key={o.value} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}>
            <input
              type="checkbox"
              checked={picked.includes(o.value)}
              onChange={e => onChange(e.target.checked
                ? [...picked, o.value]
                : picked.filter(v => v !== o.value))}
            />
            {o.label}
          </label>
        ))}
      </div>
    )
  }

  if (field.type === 'hours') {
    const hours = value && typeof value === 'object' ? value : {}
    const days = Array.isArray(hours.days) ? hours.days : []
    const names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    return (
      <div>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
          {names.map((n, i) => (
            <label key={n} style={{ display: 'flex', gap: 4, alignItems: 'center', fontSize: 12 }}>
              <input
                type="checkbox"
                checked={days.includes(i)}
                onChange={e => onChange({
                  ...hours,
                  days: e.target.checked
                    ? [...days, i].sort((a, b) => a - b)
                    : days.filter(d => d !== i),
                })}
              />
              {n}
            </label>
          ))}
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          <input className="input" type="time" value={hours.start || '09:00'}
            onChange={e => onChange({ ...hours, start: e.target.value })} />
          <input className="input" type="time" value={hours.end || '17:00'}
            onChange={e => onChange({ ...hours, end: e.target.value })} />
        </div>
      </div>
    )
  }

  if (field.type === 'text_list') {
    const items = Array.isArray(value) ? value : []
    return (
      <textarea
        {...common} rows={3} value={items.join('\n')}
        placeholder="One per line"
        onChange={e => onChange(e.target.value.split('\n').map(s => s.trim()).filter(Boolean))}
      />
    )
  }

  if (field.type === 'audience') {
    const aud = value && typeof value === 'object' ? value : {}
    const statuses = Array.isArray(aud.statuses) ? aud.statuses : []
    const choices = ['new', 'contacted', 'working', 'nurture', 'closed']
    return (
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        {choices.map(s => (
          <label key={s} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 13 }}>
            <input
              type="checkbox"
              checked={statuses.includes(s)}
              onChange={e => onChange({
                ...aud,
                statuses: e.target.checked
                  ? [...statuses, s]
                  : statuses.filter(v => v !== s),
              })}
            />
            {s}
          </label>
        ))}
      </div>
    )
  }

  return (
    <input {...common} type="text" value={value || ''}
      onChange={e => onChange(e.target.value)} />
  )
}

export default function AIWorkforce () {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [openId, setOpenId] = useState(null)
  const [detail, setDetail] = useState(null)
  const [answers, setAnswers] = useState({})
  const [problems, setProblems] = useState([])

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      setData(await api.get('/ai-workforce/overview'))
    } catch (e) {
      setErr(e?.message || 'Could not load your AI workforce.')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])

  const openDeployment = useCallback(async (id) => {
    setOpenId(id); setDetail(null); setProblems([]); setErr('')
    try {
      const d = await api.get(`/ai-workforce/deployments/${id}`)
      setDetail(d)
      setAnswers(d.configuration || {})
    } catch (e) {
      setErr(e?.message || 'Could not open that AI employee.')
    }
  }, [])

  const workforce = data?.workforce || []
  const available = useMemo(
    () => (data?.available || []).filter(c => c.can_hire), [data])
  const notAvailable = useMemo(
    () => (data?.available || []).filter(c => !c.can_hire && !c.held), [data])

  async function hire (templateKey, name) {
    setBusy(true); setErr('')
    try {
      // THE IDEMPOTENCY KEY IS THE BROWSER'S. A retry after a dropped
      // response sends the same one and gets the same employee back rather
      // than a second one on the same entitlement.
      const key = `hire-${templateKey}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      const created = await api.post('/ai-workforce/deployments', {
        template_key: templateKey, name, provisioning_key: key,
      })
      await load()
      if (created?.id) await openDeployment(created.id)
    } catch (e) {
      setErr(readError(e, 'That AI employee could not be added.'))
    } finally {
      setBusy(false)
    }
  }

  async function saveConfiguration () {
    if (!openId) return
    setBusy(true); setErr(''); setProblems([])
    try {
      const out = await api.patch(
        `/ai-workforce/deployments/${openId}/configuration`,
        { configuration: answers })
      setDetail(out)
      setProblems(out?.result?.problems || [])
      await load()
      await openDeployment(openId)
    } catch (e) {
      // `api/client.js` puts the server's structured detail on `err.detail`,
      // so a refusal that names the fields it refused can be shown against
      // those fields rather than collapsed into one sentence at the top.
      if (e?.detail?.problems) setProblems(e.detail.problems)
      setErr(readError(e, 'That setup could not be saved.'))
    } finally {
      setBusy(false)
    }
  }

  async function act (path, body) {
    if (!openId) return
    setBusy(true); setErr('')
    try {
      await api.post(`/ai-workforce/deployments/${openId}/${path}`, body || {})
      await load()
      await openDeployment(openId)
    } catch (e) {
      setErr(readError(e, 'That could not be done.'))
    } finally {
      setBusy(false)
    }
  }

  function readError (e, fallback) {
    const d = e?.detail
    if (typeof d === 'string') return d
    if (d?.message) return d.message
    if (Array.isArray(d?.problems) && d.problems.length) return d.problems[0].message
    return e?.message || fallback
  }

  return (
    <PageShell
      eyebrow="AI Workforce"
      title="My AI Workforce"
      subtitle="Hire an AI employee, tell it how your business works, and see exactly what it still needs before it can start."
      action={<button className="btn btn--secondary btn--sm" onClick={load} disabled={loading}>Refresh</button>}
    >
      {err && (
        <div className="panel" style={{ borderColor: 'rgba(255,77,126,.35)' }}>
          <p style={{ margin: 0, color: 'var(--signal-red)', fontSize: 13 }}>{err}</p>
        </div>
      )}

      <div className="panel" style={{ marginBottom: 18 }}>
        <p style={{ margin: 0, fontSize: 14 }}>
          {data?.status_line || (loading ? 'Loading…' : '')}
        </p>
        <p style={{ margin: '8px 0 0', fontSize: 12, color: 'var(--text-secondary)' }}>
          {data?.capacity?.note}
        </p>
      </div>

      <div className="panel">
        <div className="panel-header">
          <h2 className="panel-title">My AI employees</h2>
          <span className="panel-count">{workforce.length}</span>
        </div>

        {!loading && !workforce.length && (
          <div className="empty-state">
            <p>You have not hired any AI employees yet.</p>
            <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
              Every one starts switched off and does nothing until you and the
              platform agree to start it.
            </p>
          </div>
        )}

        <div className="stagger-children">
          {workforce.map(item => (
            <div key={item.id} className="glass-card" style={{ marginBottom: 10 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 15 }}>{item.name}</div>
                  <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 2 }}>
                    {item.job} · {(item.channels || []).join(' · ') || 'No channels chosen'}
                  </div>
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                  <Money commerce={item.commerce} />
                  <StateBadge state={item.state} label={item.state_label} />
                  <button
                    className="btn btn--secondary btn--sm"
                    onClick={() => (openId === item.id ? setOpenId(null) : openDeployment(item.id))}
                  >
                    {openId === item.id ? 'Close' : 'Open'}
                  </button>
                </div>
              </div>
              {item.why && (
                <p style={{ margin: '8px 0 0', fontSize: 12, color: 'var(--text-secondary)' }}>{item.why}</p>
              )}

              {openId === item.id && (
                <div style={{ marginTop: 14, borderTop: '1px solid var(--border-subtle)', paddingTop: 14 }}>
                  {!detail && <p style={{ fontSize: 13 }}>Loading…</p>}
                  {detail && (
                    <>
                      <ReadinessPanel readiness={detail.readiness} />

                      <h3 style={{ fontSize: 13, margin: '16px 0 8px', textTransform: 'uppercase', letterSpacing: '.06em', color: 'var(--text-secondary)' }}>
                        About your business
                      </h3>
                      <p style={{ margin: '0 0 12px', fontSize: 12, color: 'var(--text-secondary)' }}>
                        {detail.questions?.note}
                      </p>

                      {(detail.questions?.fields || []).map(f => (
                        <div key={f.key} style={{ marginBottom: 14 }}>
                          {f.type !== 'boolean' && (
                            <label htmlFor={`f-${f.key}`} style={{ display: 'block', fontSize: 13, marginBottom: 5 }}>
                              {f.label}{f.required ? ' *' : ''}
                            </label>
                          )}
                          <Field
                            field={f}
                            value={answers[f.key]}
                            onChange={v => setAnswers(a => ({ ...a, [f.key]: v }))}
                          />
                        </div>
                      ))}

                      {!!problems.length && (
                        <ul style={{ margin: '0 0 12px', paddingLeft: 18, fontSize: 13, color: 'var(--signal-red)' }}>
                          {problems.map((p, i) => <li key={i}>{p.message}</li>)}
                        </ul>
                      )}

                      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                        <button className="btn btn--primary btn--sm" disabled={busy}
                          onClick={saveConfiguration}>Save setup</button>
                        <button className="btn btn--secondary btn--sm" disabled={busy}
                          onClick={() => act('activation', { stage: 'controlled', expected_state: detail.state })}>
                          Ask to start working
                        </button>
                        {item.state === 'paused'
                          ? <button className="btn btn--secondary btn--sm" disabled={busy}
                            onClick={() => act('resume')}>Resume</button>
                          : <button className="btn btn--secondary btn--sm" disabled={busy}
                            onClick={() => act('pause', { reason: 'Paused from My AI Workforce', expected_state: detail.state })}>
                            Pause
                          </button>}
                        <button className="btn btn--danger btn--sm" disabled={busy}
                          onClick={() => act('retire', { reason: 'Retired from My AI Workforce', expected_state: detail.state })}>
                          Retire
                        </button>
                      </div>

                      <h3 style={{ fontSize: 13, margin: '18px 0 8px', textTransform: 'uppercase', letterSpacing: '.06em', color: 'var(--text-secondary)' }}>
                        Recent activity
                      </h3>
                      {!(detail.activity || []).length && (
                        <p style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Nothing yet.</p>
                      )}
                      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
                        {(detail.activity || []).slice(0, 10).map((a, i) => (
                          <li key={i} style={{ marginBottom: 4 }}>
                            <span>{a.what}</span>{' '}
                            <span style={{ color: 'var(--text-secondary)' }}>
                              {a.why || a.by}
                            </span>
                          </li>
                        ))}
                      </ul>
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      <div className="panel" style={{ marginTop: 18 }}>
        <div className="panel-header">
          <h2 className="panel-title">Available AI employees</h2>
          <span className="panel-count">{available.length}</span>
        </div>

        {!available.length && !notAvailable.length && !loading && (
          <div className="empty-state"><p>No AI employees are offered here yet.</p></div>
        )}

        {available.map(item => (
          <div className="glass-card" key={item.template_key} style={{ marginBottom: 10 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
              <div style={{ maxWidth: 620 }}>
                <div style={{ fontWeight: 600 }}>{item.name}</div>
                <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-secondary)' }}>
                  {item.description}
                </p>
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <Money commerce={item.commerce} />
                <button className="btn btn--primary btn--sm" disabled={busy}
                  onClick={() => hire(item.template_key, item.name)}>Hire</button>
              </div>
            </div>
          </div>
        ))}

        {!!notAvailable.length && (
          <>
            <h3 style={{ fontSize: 13, margin: '18px 0 8px', textTransform: 'uppercase', letterSpacing: '.06em', color: 'var(--text-secondary)' }}>
              Not available on this account
            </h3>
            {notAvailable.map(item => (
              <div className="glass-card" key={item.template_key} style={{ marginBottom: 10, opacity: .75 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                  <div style={{ maxWidth: 620 }}>
                    <div style={{ fontWeight: 600 }}>{item.name}</div>
                    <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-secondary)' }}>
                      {item.description}
                    </p>
                    {!!(item.blockers || []).length && (
                      <p style={{ margin: '6px 0 0', fontSize: 12, color: 'var(--text-secondary)' }}>
                        {item.blockers[0]}
                      </p>
                    )}
                  </div>
                  <Money commerce={item.commerce} />
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </PageShell>
  )
}
