/**
 * AI WORKFORCE COMMAND — the management surface.
 *
 * THE FIRST THING ON THE PAGE IS WHAT NEEDS A PERSON. Not a chart, not a
 * score, not a row of KPI tiles. A manager opening this screen has one
 * question — what needs me — and everything else on the page is there to
 * answer the follow-up. Section 21 says do not bury Needs Attention and do
 * not build a wall of charts; the layout below is where that is decided.
 *
 * EVERY NUMBER SAYS WHERE IT CAME FROM. The server labels each value as a
 * fact, a computed metric, an interpretation or unknown, and this screen
 * renders the label rather than flattening everything into a figure. An
 * unknown is drawn as "—" with its reason on hover, never as a zero, because
 * a zero on a management screen reads as failure and gets acted on.
 *
 * EVERY ACTION IS A DELEGATION. Pausing an employee, taking a conversation
 * over, accepting a handoff and escalating to support all POST to routes that
 * hand the work to the system that owns it, and the receipt names that system.
 * Nothing on this page changes authority, entitlement, channels, voice or
 * live sending, because there is no endpoint that would.
 *
 * Data:
 *   GET  /ai-workforce-intelligence/overview
 *   GET  /ai-workforce-intelligence/attention
 *   GET  /ai-workforce-intelligence/scorecards
 *   GET  /ai-workforce-intelligence/quality
 *   GET  /ai-workforce-intelligence/costs
 *   GET  /ai-workforce-intelligence/exceptions
 *   GET  /ai-workforce-intelligence/handoffs
 *   POST /ai-workforce-intelligence/refresh
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import PageShell from '../components/PageShell'
import '../styles/shared.css'

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'attention', label: 'Needs attention' },
  { key: 'employees', label: 'Employees' },
  { key: 'performance', label: 'Performance' },
  { key: 'quality', label: 'Quality' },
  { key: 'handoffs', label: 'Handoffs' },
  { key: 'exceptions', label: 'Exceptions' },
  { key: 'costs', label: 'Costs & usage' },
]

const SEVERITY_TONE = {
  critical: 'red',
  high: 'amber',
  normal: 'blue',
  low: 'neutral',
  info: 'neutral',
}

const FRESHNESS_TONE = {
  live: 'green',
  recent: 'blue',
  stale: 'amber',
  never_computed: 'neutral',
}

/** A value the server marked unknown is a dash with a reason — never a zero. */
function Value ({ entry, suffix = '' }) {
  if (!entry || entry.value === null || entry.value === undefined) {
    return (
      <span title={entry?.note || 'Not known from authoritative records.'}
            style={{ color: 'var(--text-secondary)' }}>—</span>
    )
  }
  const shown = entry.unit === 'rate'
    ? `${Math.round(entry.value * 1000) / 10}%`
    : entry.value
  return <span title={entry.calculation || ''}>{shown}{suffix}</span>
}

function Stat ({ label, entry, hint }) {
  return (
    <div className="panel" style={{ padding: 14, minWidth: 150 }}>
      <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 600, lineHeight: 1.2 }}>
        <Value entry={entry} />
      </div>
      {hint ? (
        <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{hint}</div>
      ) : null}
    </div>
  )
}

function Severity ({ level, label }) {
  return (
    <span className={`badge badge--${SEVERITY_TONE[level] || 'neutral'}`}>
      {label || level}
    </span>
  )
}

function Freshness ({ state }) {
  if (!state) return null
  return (
    <span className={`badge badge--${FRESHNESS_TONE[state.state] || 'neutral'}`}
          title={state.statement || ''}>
      {state.label}
    </span>
  )
}

function age (seconds) {
  if (!seconds && seconds !== 0) return ''
  const m = Math.floor(seconds / 60)
  if (m < 90) return `${m}m`
  const h = Math.floor(m / 60)
  if (h < 48) return `${h}h`
  return `${Math.floor(h / 24)}d`
}


/** ONE ITEM ON THE LIST. What, why, how old, how bad, what to do, and a way
 *  through to the record that proves it. All six, on every row. */
function AttentionRow ({ item, onAct, busy, readOnly }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="panel" style={{ padding: 14, marginBottom: 10 }}>
      <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start',
                    flexWrap: 'wrap' }}>
        <Severity level={item.severity} label={item.severity_label} />
        <div style={{ flex: 1, minWidth: 240 }}>
          <div style={{ fontWeight: 600 }}>{item.what}</div>
          <div style={{ fontSize: 13, color: 'var(--text-secondary)',
                        marginTop: 2 }}>{item.why}</div>
          <div style={{ fontSize: 12, marginTop: 6,
                        color: 'var(--text-secondary)' }}>
            {item.employee_name ? <>AI employee: {item.employee_name} · </> : null}
            Waiting {age(item.age_seconds)}
            {item.affects > 1 ? <> · affects {item.affects} records</> : null}
            {item.state === 'acknowledged' ? <> · acknowledged</> : null}
          </div>
          {item.recommended_action ? (
            <div style={{ fontSize: 13, marginTop: 8 }}>
              <strong>What to do:</strong> {item.recommended_action}
            </div>
          ) : null}
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <button className="btn btn--ghost" onClick={() => setOpen(v => !v)}>
            {open ? 'Hide evidence' : 'Evidence'}
          </button>
          {!readOnly ? (
            <>
              <button className="btn btn--ghost" disabled={busy}
                      onClick={() => onAct(item, 'acknowledge')}>
                Acknowledge
              </button>
              <button className="btn btn--ghost" disabled={busy}
                      onClick={() => onAct(item, 'resolve')}>
                Done
              </button>
            </>
          ) : null}
        </div>
      </div>
      {open ? (
        <div style={{ marginTop: 10, fontSize: 12,
                      color: 'var(--text-secondary)' }}>
          <div>
            Source: {item.source?.table || 'unknown'}
            {item.source?.id ? ` · ${item.source.id}` : ''}
          </div>
          <pre style={{ whiteSpace: 'pre-wrap', marginTop: 6 }}>
            {JSON.stringify(item.evidence || {}, null, 2)}
          </pre>
        </div>
      ) : null}
    </div>
  )
}

/** A finding: fact, metric, interpretation and unknown, kept visibly apart. */
function FindingCard ({ finding }) {
  return (
    <div className="panel" style={{ padding: 14, marginBottom: 10 }}>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
        <Severity level={finding.severity} label={finding.severity_label} />
        <strong>{finding.headline}</strong>
        {finding.confidence ? (
          <span className="badge badge--neutral">
            confidence: {finding.confidence}
          </span>
        ) : null}
      </div>
      {finding.fact?.items?.length ? (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11, textTransform: 'uppercase',
                        color: 'var(--text-secondary)' }}>What is true</div>
          <ul style={{ margin: '4px 0 0 18px', fontSize: 13 }}>
            {finding.fact.items.map((f, i) => (
              <li key={i}>
                {f.statement}
                <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>
                  {' '}({f.source_table})
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {finding.metric?.items?.length ? (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11, textTransform: 'uppercase',
                        color: 'var(--text-secondary)' }}>Worked out from it</div>
          <ul style={{ margin: '4px 0 0 18px', fontSize: 13 }}>
            {finding.metric.items.map((m, i) => (
              <li key={i} title={m.calculation}>
                {m.key}: {String(m.value)}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {finding.interpretation?.text ? (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11, textTransform: 'uppercase',
                        color: 'var(--text-secondary)' }}>
            Reading of the numbers
          </div>
          <div style={{ fontSize: 13 }}>{finding.interpretation.text}</div>
          <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
            {finding.interpretation.note}
          </div>
        </div>
      ) : null}
      {finding.unknown?.items?.length ? (
        <div style={{ marginTop: 8 }}>
          <div style={{ fontSize: 11, textTransform: 'uppercase',
                        color: 'var(--text-secondary)' }}>Not known</div>
          <ul style={{ margin: '4px 0 0 18px', fontSize: 13,
                       color: 'var(--text-secondary)' }}>
            {finding.unknown.items.map((u, i) => <li key={i}>{u}</li>)}
          </ul>
        </div>
      ) : null}
      {finding.recommendation?.text ? (
        <div style={{ marginTop: 8, fontSize: 13 }}>
          <strong>Suggestion:</strong> {finding.recommendation.text}
        </div>
      ) : null}
    </div>
  )
}


export default function AIWorkforceCommand () {
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') || 'overview'
  const [overview, setOverview] = useState(null)
  const [freshness, setFreshness] = useState(null)
  const [extra, setExtra] = useState({})
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const res = await api.get('/ai-workforce-intelligence/overview')
      setOverview(res.data || res)
      setFreshness(res.freshness || null)
    } catch (e) {
      setErr(e?.message || 'Could not load AI Workforce Command.')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])

  // EACH TAB FETCHES ITS OWN DETAIL. The overview carries the headline and
  // the queue because those are what the page opens on; scorecards, quality
  // and costs are the expensive parts and are not paid for until asked for.
  useEffect(() => {
    let cancelled = false
    async function fetchTab () {
      const routes = {
        employees: '/ai-workforce-intelligence/scorecards',
        performance: '/ai-workforce-intelligence/performance',
        quality: '/ai-workforce-intelligence/quality',
        handoffs: '/ai-workforce-intelligence/handoffs',
        exceptions: '/ai-workforce-intelligence/exceptions',
        costs: '/ai-workforce-intelligence/costs',
      }
      const url = routes[tab]
      if (!url || extra[tab]) return
      try {
        const res = await api.get(url)
        if (!cancelled) setExtra(prev => ({ ...prev, [tab]: res }))
      } catch (e) {
        if (!cancelled) setErr(e?.message || 'Could not load that view.')
      }
    }
    fetchTab()
    return () => { cancelled = true }
  }, [tab, extra])

  const readOnly = !!overview?.read_only

  async function refresh () {
    setBusy(true); setErr('')
    try {
      await api.post('/ai-workforce-intelligence/refresh', {})
      setExtra({})
      await load()
    } catch (e) {
      setErr(e?.message || 'Could not recompute.')
    } finally {
      setBusy(false)
    }
  }

  async function act (item, what) {
    setBusy(true); setErr('')
    try {
      await api.post(
        `/ai-workforce-intelligence/attention/${item.id}/${what}`, {})
      await load()
    } catch (e) {
      setErr(e?.detail?.message || e?.message || 'That did not go through.')
    } finally {
      setBusy(false)
    }
  }

  async function escalate (item) {
    setBusy(true); setErr('')
    try {
      const res = await api.post(
        `/ai-workforce-intelligence/exceptions/${item.id}/escalate`, {})
      setErr('')
      setExtra({})
      await load()
      if (res?.ticket_number) {
        window.alert(`Support ticket ${res.ticket_number} opened.`)
      }
    } catch (e) {
      setErr(e?.detail?.message || e?.message || 'Could not escalate.')
    } finally {
      setBusy(false)
    }
  }

  const attention = overview?.attention
  const critical = attention?.by_severity?.critical || 0

  return (
    <PageShell
      eyebrow="AI Workforce"
      title="Workforce Command"
      subtitle={overview?.workforce?.statement || 'Loading your AI workforce…'}
      action={
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <Freshness state={freshness} />
          <button className="btn btn--ghost" onClick={refresh} disabled={busy}>
            Recompute
          </button>
        </div>
      }
    >
      {err ? <div className="panel panel--error">{err}</div> : null}
      {loading ? <div className="panel">Loading…</div> : null}

      <nav className="panel" style={{ display: 'flex', gap: 6, flexWrap: 'wrap',
                                      padding: 8, marginBottom: 12 }}>
        {TABS.map(t => (
          <button key={t.key}
                  className={`btn ${tab === t.key ? '' : 'btn--ghost'}`}
                  onClick={() => setParams({ tab: t.key })}>
            {t.label}
            {t.key === 'attention' && attention?.total
              ? ` (${attention.total})`
              : null}
          </button>
        ))}
      </nav>

      {overview ? (
        <>
          {/* THE HEADLINE, ON EVERY TAB. "What needs me" is not something a
              manager should have to click back to. */}
          <div className="panel"
               style={{ padding: 14, marginBottom: 12,
                        borderLeft: `4px solid var(--${critical ? 'danger' : 'border'}, #ccc)` }}>
            <strong>{overview.headline}</strong>
            {readOnly ? (
              <span className="badge badge--neutral" style={{ marginLeft: 8 }}>
                Read only
              </span>
            ) : null}
          </div>

          {tab === 'overview' ? <Overview data={overview} onAct={act}
                                          busy={busy} readOnly={readOnly} /> : null}
          {tab === 'attention' ? <Attention data={overview} onAct={act}
                                            busy={busy} readOnly={readOnly} /> : null}
          {tab === 'employees' ? <Employees data={extra.employees}
                                            onOpen={id => navigate(`/ai-workforce-command/${id}`)} /> : null}
          {tab === 'performance' ? <Performance data={extra.performance} /> : null}
          {tab === 'quality' ? <Quality data={extra.quality} /> : null}
          {tab === 'handoffs' ? <Handoffs data={extra.handoffs} /> : null}
          {tab === 'exceptions' ? <Exceptions data={extra.exceptions}
                                              onEscalate={escalate}
                                              busy={busy}
                                              readOnly={readOnly} /> : null}
          {tab === 'costs' ? <Costs data={extra.costs} /> : null}
        </>
      ) : null}
    </PageShell>
  )
}


/* ── THE TABS ──────────────────────────────────────────────────────────── */

function Overview ({ data, onAct, busy, readOnly }) {
  const values = data.outcomes?.values || {}
  const waiting = data.waiting || {}
  const items = (data.attention?.items || []).slice(0, 5)
  return (
    <>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap',
                    marginBottom: 12 }}>
        <Stat label="Working" entry={{ value: data.workforce?.working }} />
        <Stat label="Needs a person"
              entry={{ value: waiting.needs_a_person }} />
        <Stat label="Work queued" entry={{ value: waiting.work_queued }} />
        <Stat label="Appointments booked"
              entry={values.appointments_booked} />
        <Stat label="Response rate" entry={values.response_rate}
              hint={values.response_rate?.note || ''} />
        <Stat label="Revenue" entry={values.revenue}
              hint="Reported by the systems that own it" />
      </div>

      <h3>What needs you first</h3>
      {items.length
        ? items.map(item => (
            <AttentionRow key={item.id} item={item} onAct={onAct} busy={busy}
                          readOnly={readOnly} />
          ))
        : <div className="panel" style={{ padding: 14 }}>
            Nothing needs you right now.
          </div>}

      {data.findings?.findings?.length ? (
        <>
          <h3 style={{ marginTop: 16 }}>What changed, and why it matters</h3>
          {data.findings.findings.slice(0, 4).map(f => (
            <FindingCard key={f.id} finding={f} />
          ))}
        </>
      ) : null}

      {data.reconciliation?.contradictions?.length ? (
        <>
          <h3 style={{ marginTop: 16 }}>Systems that disagree</h3>
          <div className="panel" style={{ padding: 14 }}>
            <p style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
              {data.reconciliation.note}
            </p>
            <ul style={{ margin: '6px 0 0 18px', fontSize: 13 }}>
              {data.reconciliation.contradictions.slice(0, 8).map(c => (
                <li key={c.id}>
                  {c.statement}{' '}
                  <span style={{ color: 'var(--text-secondary)', fontSize: 11 }}>
                    (fixed through {c.remediation_owner})
                  </span>
                </li>
              ))}
            </ul>
          </div>
        </>
      ) : null}

      <PlatformState state={data.platform_state} />
      <SelfCheck check={data.self_check} />
    </>
  )
}

function Attention ({ data, onAct, busy, readOnly }) {
  const items = data.attention?.items || []
  const counts = data.attention?.by_severity || {}
  return (
    <>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap',
                    marginBottom: 12 }}>
        {['critical', 'high', 'normal', 'low'].map(level => (
          <Stat key={level} label={level} entry={{ value: counts[level] || 0 }} />
        ))}
      </div>
      {items.length
        ? items.map(item => (
            <AttentionRow key={item.id} item={item} onAct={onAct} busy={busy}
                          readOnly={readOnly} />
          ))
        : <div className="panel" style={{ padding: 14 }}>
            Nothing needs you right now.
          </div>}
    </>
  )
}

function Employees ({ data, onOpen }) {
  if (!data) return <div className="panel">Loading employees…</div>
  const cards = data.cards || []
  return (
    <>
      <p style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
        {data.comparison_policy}
      </p>
      {cards.map(card => (
        <div key={card.employee_id} className="panel"
             style={{ padding: 14, marginBottom: 10 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center',
                        flexWrap: 'wrap' }}>
            <strong>{card.name}</strong>
            <span className="badge badge--neutral">{card.job_role}</span>
            <span className={`badge badge--${card.deployment?.live ? 'green' : 'neutral'}`}>
              {card.deployment?.state || 'no deployment'}
            </span>
            <button className="btn btn--ghost"
                    style={{ marginLeft: 'auto' }}
                    onClick={() => onOpen(card.employee_id)}>
              Open
            </button>
          </div>
          <div style={{ display: 'flex', gap: 16, marginTop: 8, flexWrap: 'wrap',
                        fontSize: 13 }}>
            <span>Open work: {card.activity?.open_work_items ?? 0}</span>
            <span>Appointments:{' '}
              <Value entry={card.outcomes?.appointments_booked} />
            </span>
            <span>Qualified: <Value entry={card.outcomes?.qualified} /></span>
            <span>Handoffs: <Value entry={card.outcomes?.handoffs} /></span>
            <span>Messages per outcome:{' '}
              {card.efficiency?.messages_per_outcome ?? (
                <span title={card.efficiency?.messages_per_outcome_note}
                      style={{ color: 'var(--text-secondary)' }}>—</span>
              )}
            </span>
          </div>
          <TrendRow trends={card.trends} />
        </div>
      ))}
    </>
  )
}

function TrendRow ({ trends }) {
  const keys = ['appointments', 'qualified', 'responses', 'messages_sent']
  return (
    <div style={{ display: 'flex', gap: 12, marginTop: 8, flexWrap: 'wrap',
                  fontSize: 12, color: 'var(--text-secondary)' }}>
      {keys.map(k => {
        const t = trends?.[k]
        if (!t) return null
        return (
          <span key={k} title={t.note || ''}>
            {t.label}: {t.current} vs {t.previous} last period
            {t.off_baseline ? ' · off its own baseline' : ''}
          </span>
        )
      })}
    </div>
  )
}


function Performance ({ data }) {
  if (!data) return <div className="panel">Loading performance…</div>
  const values = data.totals?.values || {}
  const order = ['records_assigned', 'records_eligible', 'messages_sent',
    'messages_delivered', 'responses', 'qualified', 'appointments_booked',
    'appointments_standing', 'appointments_cancelled', 'appointments_completed',
    'handoffs', 'opt_outs', 'policy_denials', 'provider_failures', 'revenue']
  const rates = ['response_rate', 'delivery_rate', 'appointment_rate',
    'qualification_rate', 'handoff_rate', 'opt_out_rate', 'eligibility_rate',
    'appointment_cancellation_rate']
  return (
    <>
      <h3>Counts</h3>
      <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr><th>Measure</th><th>Value</th><th>Where it comes from</th></tr>
          </thead>
          <tbody>
            {order.map(key => {
              const entry = values[key]
              if (!entry) return null
              return (
                <tr key={key}>
                  <td>{entry.label || key}</td>
                  <td><Value entry={entry} /></td>
                  <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {entry.calculation || entry.note || ''}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      <h3 style={{ marginTop: 16 }}>Rates</h3>
      <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
        <table className="table">
          <thead>
            <tr><th>Rate</th><th>Value</th><th>Out of</th><th>How</th></tr>
          </thead>
          <tbody>
            {rates.map(key => {
              const entry = values[key]
              if (!entry) return null
              return (
                <tr key={key}>
                  <td>{entry.label || key}</td>
                  <td><Value entry={entry} /></td>
                  <td style={{ fontSize: 12 }}>
                    {entry.denominator === null || entry.denominator === undefined
                      ? '—'
                      : `${entry.numerator} of ${entry.denominator}`}
                  </td>
                  <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {entry.note || entry.calculation}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </>
  )
}

function Quality ({ data }) {
  if (!data) return <div className="panel">Loading quality…</div>
  const employees = Object.values(data.employees || {})
  const exceptions = data.exceptions || {}
  return (
    <>
      <div className="panel" style={{ padding: 14, marginBottom: 12 }}>
        <strong>Exceptions</strong>
        <div style={{ fontSize: 13, marginTop: 4 }}>
          {exceptions.total || 0} message{exceptions.total === 1 ? '' : 's'} that
          should not have gone out — {(exceptions.policy || []).length} refused
          by policy, {(exceptions.stop_condition || []).length} after a stop,
          {' '}{(exceptions.opt_out || []).length} after an opt-out.
        </div>
      </div>

      {employees.map(emp => (
        <div key={emp.employee_id} className="panel"
             style={{ padding: 14, marginBottom: 10 }}>
          <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
            <strong>{emp.name}</strong>
            <span className="badge badge--neutral">{emp.job_role}</span>
            <span style={{ marginLeft: 'auto', fontSize: 13 }}
                  title={emp.score?.explanation}>
              {emp.score?.score === null || emp.score?.score === undefined
                ? <span style={{ color: 'var(--text-secondary)' }}
                        title={emp.score?.note}>Not enough measured yet</span>
                : `${Math.round(emp.score.score * 100)}%`}
            </span>
          </div>
          <div className="panel" style={{ padding: 0, marginTop: 8,
                                          overflowX: 'auto' }}>
            <table className="table">
              <thead>
                <tr><th>What is graded</th><th>Result</th><th>How</th></tr>
              </thead>
              <tbody>
                {Object.values(emp.dimensions || {}).map(dim => (
                  <tr key={dim.key}>
                    <td>
                      {dim.label}
                      {dim.critical ? (
                        <span className="badge badge--red"
                              style={{ marginLeft: 6 }}>critical</span>
                      ) : null}
                    </td>
                    <td>
                      {dim.measured
                        ? `${dim.passed} of ${dim.total}`
                        : <span style={{ color: 'var(--text-secondary)' }}
                                title={dim.note}>Not measured</span>}
                      {dim.violations?.length ? (
                        <span className="badge badge--amber"
                              style={{ marginLeft: 6 }}>
                          {dim.violations.length} exception
                          {dim.violations.length === 1 ? '' : 's'}
                        </span>
                      ) : null}
                    </td>
                    <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                      {dim.calculation || dim.note}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ))}

      {data.platform_evaluation ? (
        <div className="panel" style={{ padding: 14 }}>
          <strong>Platform evaluation</strong>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
            {data.platform_evaluation.note}
          </div>
        </div>
      ) : null}
    </>
  )
}

function Handoffs ({ data }) {
  if (!data) return <div className="panel">Loading handoffs…</div>
  const rows = data.handoffs || []
  if (!rows.length) {
    return <div className="panel" style={{ padding: 14 }}>
      Nobody is waiting for a person.
    </div>
  }
  return (
    <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
      <table className="table">
        <thead>
          <tr><th>Why</th><th>Priority</th><th>Status</th><th>Raised</th>
            <th>Assigned to</th></tr>
        </thead>
        <tbody>
          {rows.map(h => (
            <tr key={h.id}>
              <td>{h.reason_label || h.reason_code}</td>
              <td>{h.priority}</td>
              <td>{h.status}</td>
              <td style={{ fontSize: 12 }}>{h.created_at}</td>
              <td style={{ fontSize: 12 }}>
                {h.assigned_to_user_id || h.assigned_queue || (
                  <span style={{ color: 'var(--text-secondary)' }}>
                    nobody
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}


function Exceptions ({ data, onEscalate, busy, readOnly }) {
  if (!data) return <div className="panel">Loading exceptions…</div>
  const items = data.items || []
  return (
    <>
      <p style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
        {data.note}
      </p>
      {items.length
        ? items.map(item => (
            <div key={item.id} className="panel"
                 style={{ padding: 14, marginBottom: 10 }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start',
                            flexWrap: 'wrap' }}>
                <Severity level={item.severity} label={item.severity_label} />
                <div style={{ flex: 1, minWidth: 220 }}>
                  <strong>{item.what}</strong>
                  <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                    {item.why}
                  </div>
                  <div style={{ fontSize: 12, marginTop: 4,
                                color: 'var(--text-secondary)' }}>
                    {item.source?.table} · open {age(item.age_seconds)}
                    {item.remediation?.owned
                      ? ` · ${item.remediation.actions_taken.length} action(s) taken`
                      : ''}
                  </div>
                </div>
                {item.platform_suspected && !readOnly ? (
                  <button className="btn btn--ghost" disabled={busy}
                          onClick={() => onEscalate(item)}>
                    This looks like AdvisorFlow — escalate
                  </button>
                ) : null}
              </div>
            </div>
          ))
        : <div className="panel" style={{ padding: 14 }}>No open exceptions.</div>}
    </>
  )
}

function Costs ({ data }) {
  if (!data) return <div className="panel">Loading usage…</div>
  const usage = data.usage || {}
  const measured = usage.measured || {}
  const estimated = usage.estimated_cost || {}
  const conditions = data.conditions || []
  return (
    <>
      <div className="panel" style={{ padding: 14, marginBottom: 12 }}>
        <strong>These are estimates, not invoices</strong>
        <div style={{ fontSize: 13, color: 'var(--text-secondary)',
                      marginTop: 4 }}>
          {usage.note}
        </div>
      </div>

      <h3>What was used</h3>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap',
                    marginBottom: 12 }}>
        {['actions', 'messages_sms', 'messages_email', 'calls', 'runs',
          'voice_seconds'].map(k => (
            <Stat key={k} label={k.replace(/_/g, ' ')}
                  entry={{ value: measured[k] }} />
          ))}
      </div>

      <h3>What it may have cost</h3>
      <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap',
                    marginBottom: 12 }}>
        <Stat label="Estimated (operations)"
              entry={{ value: estimated.operations_usd }} hint="estimate" />
        <Stat label="Estimated (models)"
              entry={{ value: estimated.model_usd }}
              hint={estimated.coverage_note || 'estimate'} />
        <Stat label="Provider cost"
              entry={usage.provider_cost}
              hint="Unknown — no invoice reaches this platform" />
      </div>

      <h3>Running ahead of a limit</h3>
      {conditions.length
        ? conditions.map((c, i) => (
            <div key={i} className="panel"
                 style={{ padding: 14, marginBottom: 10 }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <Severity level={c.severity} />
                <strong>{c.code.replace(/_/g, ' ')}</strong>
                <span style={{ marginLeft: 'auto', fontSize: 13 }}>
                  {c.current}
                  {c.limit ? ` of ${c.limit}` : ''} {c.unit}
                </span>
              </div>
              <div style={{ fontSize: 13, marginTop: 4 }}>{c.reason}</div>
              <div style={{ fontSize: 13, marginTop: 4 }}>
                <strong>What to do:</strong> {c.recommended_action}
              </div>
            </div>
          ))
        : <div className="panel" style={{ padding: 14 }}>
            Nothing is close to a limit.
          </div>}
    </>
  )
}

/** THE DARK SWITCHES, QUOTED. "Why is nothing sending" has a factual answer
 *  and this is it. Nothing on this page can change any of them. */
function PlatformState ({ state }) {
  if (!state) return null
  const ops = state.operations || {}
  return (
    <div className="panel" style={{ padding: 14, marginTop: 16 }}>
      <strong>Platform state</strong>
      <div style={{ display: 'flex', gap: 8, marginTop: 6, flexWrap: 'wrap' }}>
        <span className={`badge badge--${ops.operations_enabled ? 'green' : 'neutral'}`}>
          AI operations {ops.operations_enabled ? 'on' : 'off'}
        </span>
        <span className={`badge badge--${ops.live_send_enabled ? 'green' : 'neutral'}`}>
          Live sending {ops.live_send_enabled ? 'on' : 'off'}
        </span>
        <span className={`badge badge--${ops.live_voice_enabled ? 'green' : 'neutral'}`}>
          Voice {ops.live_voice_enabled ? 'on' : 'off'}
        </span>
      </div>
      <div style={{ fontSize: 12, color: 'var(--text-secondary)',
                    marginTop: 6 }}>
        {state.note}
      </div>
    </div>
  )
}

/** WHETHER THIS PAGE WAS ACTUALLY COMPUTED. A dashboard of zeros and a quiet
 *  day look identical, so the page says which it is. */
function SelfCheck ({ check }) {
  if (!check || check.healthy) return null
  return (
    <div className="panel panel--warn" style={{ padding: 14, marginTop: 12 }}>
      <strong>About these numbers</strong>
      <div style={{ fontSize: 13, marginTop: 4 }}>{check.statement}</div>
    </div>
  )
}
