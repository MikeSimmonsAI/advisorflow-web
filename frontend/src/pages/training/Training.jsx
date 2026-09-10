/**
 * TRAINING — what somebody has been asked to learn, and where they are in it.
 *
 * Two views in one file because they are one thing: the list of paths assigned
 * to the caller, and one path opened. There is no user id anywhere in this
 * screen or in the endpoints behind it — the subject is always the person
 * signed in, so there is no id to get wrong.
 *
 * ── A PRACTICE STEP CANNOT BE TICKED ──────────────────────────────────────
 * Steps that say "practise this" are completed by actually running that
 * scenario in the Demo Suite. The button says so and links there, and the
 * server refuses the shortcut regardless of what this screen renders. Reading
 * about how to run a demo produces somebody who has read about running a demo.
 *
 * Shares the Demo Suite's visual language on purpose: it is the same people,
 * preparing for the same meeting.
 *
 * Data:
 *   GET  /training/me
 *   GET  /training/paths/{key}
 *   POST /training/paths/{key}/complete   · /uncomplete
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../api/client'
import DemoStyles from '../demo/DemoStyles'

const TONE = { complete: 'ok', in_progress: 'warm', not_started: '' }

function day (iso) {
  if (!iso) return null
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? null : d.toLocaleDateString()
}

/**
 * The smallest markdown that the training bodies actually use: paragraphs,
 * `*` bullets and `**bold**`. Deliberately not a markdown library — one more
 * dependency in a bundle with three of them, to render four constructs, is a
 * bad trade, and an unparsed asterisk is a cosmetic bug rather than a broken
 * screen.
 */
function Body ({ text }) {
  const blocks = useMemo(() => String(text || '').split('\n\n'), [text])
  const bold = s => s.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**')
      ? <strong key={i} style={{ color: '#fff' }}>{part.slice(2, -2)}</strong>
      : <span key={i}>{part}</span>)
  return (
    <>
      {blocks.map((b, i) => {
        const lines = b.split('\n')
        if (lines.every(l => l.trim().startsWith('*'))) {
          return (
            <ul key={i} style={{ margin: '0 0 14px', paddingLeft: 20, lineHeight: 1.75 }}>
              {lines.map((l, j) => <li key={j}>{bold(l.replace(/^\s*\*\s?/, ''))}</li>)}
            </ul>
          )
        }
        return (
          <p key={i} style={{ margin: '0 0 14px', lineHeight: 1.75 }}>{bold(b)}</p>
        )
      })}
    </>
  )
}

/* ── the list ────────────────────────────────────────────────────────────── */
function TrainingHome () {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try { setData(await api.get('/training/me')) }
    catch (e) { setErr(e?.message || 'Could not load your training.') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { load() }, [load])

  return (
    <div className="ds-scope">
      <DemoStyles />
      <div style={{ maxWidth: 900, margin: '0 auto', padding: '44px 24px 70px' }}>
        <h1 className="ds-title">Training</h1>
        <p className="ds-sub">
          What you have been asked to be able to do, and how far you have got.
          Everything here is about this platform — no path teaches a feature
          that does not exist.
        </p>

        {err && <div className="ds-error" style={{ marginTop: 20 }}>{err}</div>}
        {loading && <div className="ds-loading">Loading…</div>}

        {data && (
          <>
            <div className="ds-grid stats" style={{ marginTop: 24 }}>
              <div className="ds-stat">
                <div className="k">Assigned to you</div>
                <div className="v">{data.assigned_count}</div>
              </div>
              <div className="ds-stat">
                <div className="k">Complete</div>
                <div className="v">{data.complete_count}</div>
              </div>
            </div>

            <div style={{ marginTop: 24 }}>
              {data.assigned.length === 0 && (
                <div className="ds-empty">
                  Nothing has been assigned to you yet. If you are presenting
                  the product, ask for the Demo Presenter path.
                </div>
              )}
              {data.assigned.map(p => (
                <button key={p.key} className="ds-row"
                        onClick={() => navigate('/training/' + p.key)}>
                  <span className={'ds-pill ' + (TONE[p.assignment.status] || '')}>
                    {p.assignment.status.replace('_', ' ')}
                  </span>
                  <span>
                    <div className="name">{p.name}</div>
                    <div className="meta">
                      {p.audience_label} · {p.assignment.completed_steps}/
                      {p.assignment.total_steps} steps
                      {p.assignment.due_at
                        ? ` · due ${day(p.assignment.due_at)}` : ''}
                      {p.assignment.overdue ? ' · overdue' : ''}
                    </div>
                  </span>
                  <span className="spacer" />
                  {p.requires_demo && <span className="ds-pill">demo suite</span>}
                </button>
              ))}
            </div>

            {data.available.length > 0 && (
              <>
                <h2 style={{ color: '#fff', fontSize: 15, margin: '32px 0 6px',
                             letterSpacing: '-.02em' }}>
                  Also available
                </h2>
                <p style={{ color: 'var(--ds-ghost)', fontSize: 12, margin: '0 0 14px' }}>
                  Not assigned to you. If one of these is your job, ask the
                  platform owner to assign it — it is one click on your Manage
                  Access screen.
                </p>
                {data.available.map(p => (
                  <div key={p.key} className="ds-row" style={{ cursor: 'default', opacity: .72 }}>
                    <span className="ds-pill">not assigned</span>
                    <span>
                      <div className="name">{p.name}</div>
                      <div className="meta">{p.audience_label} · {p.summary}</div>
                    </span>
                  </div>
                ))}
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}

/* ── one path ────────────────────────────────────────────────────────────── */
function TrainingPath ({ pathKey }) {
  const navigate = useNavigate()
  const [data, setData] = useState(null)
  const [open, setOpen] = useState(0)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const d = await api.get('/training/paths/' + pathKey)
      setData(d)
      const next = d.steps.findIndex(s => !s.done)
      setOpen(next === -1 ? d.steps.length - 1 : next)
    } catch (e) {
      setErr(e?.status === 404
        ? 'This training path has not been assigned to you.'
        : (e?.message || 'Could not load this path.'))
      setData(null)
    } finally { setLoading(false) }
  }, [pathKey])
  useEffect(() => { load() }, [load])

  async function mark (step, done) {
    setBusy(true); setErr('')
    try {
      const d = await api.post(
        '/training/paths/' + pathKey + (done ? '/uncomplete' : '/complete'),
        { step: step.key })
      setData(d)
      if (!done) {
        const next = d.steps.findIndex(s => !s.done)
        if (next !== -1) setOpen(next)
      }
    } catch (e) {
      setErr(e?.message || 'Could not record that.')
    } finally { setBusy(false) }
  }

  const step = data?.steps?.[open]
  const done = data?.steps?.filter(s => s.done).length || 0
  const total = data?.steps?.length || 0

  return (
    <div className="ds-scope">
      <DemoStyles />
      <div style={{ maxWidth: 1080, margin: '0 auto', padding: '32px 24px 70px' }}>
        <button className="ds-btn ghost small" onClick={() => navigate('/training')}>
          ← ALL TRAINING
        </button>

        {err && <div className="ds-error" style={{ marginTop: 18 }}>{err}</div>}
        {loading && <div className="ds-loading">Loading…</div>}

        {data && (
          <>
            <h1 className="ds-title" style={{ marginTop: 16 }}>{data.path.name}</h1>
            <p className="ds-sub">{data.path.summary}</p>
            <p style={{ color: 'var(--ds-ghost)', fontSize: 12, maxWidth: 720,
                        marginTop: 8, lineHeight: 1.6 }}>
              {data.path.why}
            </p>

            <div className="ds-progress" style={{ marginTop: 20, maxWidth: 420 }}>
              <i style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
            </div>
            <div style={{ color: 'var(--ds-dim)', fontSize: 12 }}>
              {done} of {total} complete
              {data.assignment.due_at ? ` · due ${day(data.assignment.due_at)}` : ''}
              {data.assignment.overdue ? ' · overdue' : ''}
            </div>
            {data.assignment.note && (
              <div className="ds-ok" style={{ marginTop: 14, maxWidth: 720 }}>
                {data.assignment.note}
              </div>
            )}

            <div style={{ display: 'flex', gap: 20, marginTop: 26, alignItems: 'flex-start',
                          flexWrap: 'wrap' }}>
              <div style={{ flex: '0 0 268px', minWidth: 240 }}>
                {data.steps.map((s, i) => (
                  <button key={s.key}
                          className={'ds-step' + (i === open ? ' on' : '') +
                                     (s.done ? ' done' : '')}
                          onClick={() => setOpen(i)}>
                    <div className="n">
                      {s.done ? '✓ ' : ''}Step {i + 1}
                      {s.requires_practice ? ' · practice' : ''}
                    </div>
                    <div className="t">{s.title}</div>
                  </button>
                ))}
              </div>

              <div style={{ flex: 1, minWidth: 320 }}>
                {step && (
                  <div className="ds-card">
                    <div className="ds-card-head">
                      <h3 className="ds-card-title">{step.title}</h3>
                      <span className="ds-card-note">
                        about {step.minutes} min
                      </span>
                    </div>

                    <div style={{ fontSize: 13.5, color: 'var(--ds-text)' }}>
                      <Body text={step.body} />
                    </div>

                    {step.requires_practice && (
                      <div className={step.practice_available ? 'ds-ok' : 'ds-draft'}
                           style={{ marginTop: 4 }}>
                        {step.practice_available ? (
                          <>You have run this scenario through to the end. This
                            step can be completed.</>
                        ) : (
                          <>
                            <div className="label">Practice required</div>
                            This step is completed by doing it. Run the{' '}
                            <strong>{String(step.practice_scenario).replace(/_/g, ' ')}</strong>{' '}
                            scenario through to the end in the Demo Suite, then
                            come back.
                            <div style={{ marginTop: 12 }}>
                              <button className="ds-btn primary small"
                                      onClick={() => navigate('/demo-suite')}>
                                OPEN THE DEMO SUITE
                              </button>
                            </div>
                          </>
                        )}
                      </div>
                    )}

                    <div style={{ display: 'flex', gap: 8, marginTop: 18, flexWrap: 'wrap' }}>
                      <button className="ds-btn primary" disabled={busy || step.done ||
                                (step.requires_practice && !step.practice_available)}
                              onClick={() => mark(step, false)}>
                        {step.done ? 'COMPLETED' : busy ? 'SAVING…' : 'MARK COMPLETE'}
                      </button>
                      {step.done && (
                        <button className="ds-btn ghost" disabled={busy}
                                onClick={() => mark(step, true)}>
                          UN-TICK
                        </button>
                      )}
                      <button className="ds-btn ghost" disabled={open === 0}
                              onClick={() => setOpen(o => Math.max(0, o - 1))}>← BACK</button>
                      <button className="ds-btn ghost" disabled={open >= total - 1}
                              onClick={() => setOpen(o => Math.min(total - 1, o + 1))}>
                        NEXT →
                      </button>
                    </div>
                  </div>
                )}

                {done === total && total > 0 && (
                  <div className="ds-ok" style={{ marginTop: 14 }}>
                    That is the whole path. Your completion is recorded and
                    visible to the platform owner — nobody has to ask you
                    whether you are ready.
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  )
}

export default function Training () {
  const { pathKey } = useParams()
  return pathKey ? <TrainingPath pathKey={pathKey} /> : <TrainingHome />
}
