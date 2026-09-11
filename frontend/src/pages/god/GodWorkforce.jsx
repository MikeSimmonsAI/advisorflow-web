/**
 * GOD MODE — AI WORKFORCE.
 *
 * THE FIRST THING ON THE SCREEN IS THE DARK-LAUNCH STATE, in one line, because
 * for the whole of this launch that is the only number anybody should have to
 * check: how many AI employees on this platform could actually reach a person
 * right now. The answer is meant to be zero, and a screen that made you
 * calculate it from four other numbers would be a screen nobody checked.
 *
 * WHAT THIS ADMINISTERS IS PLATFORM CAPABILITY — the job library, the tool
 * registry, activation staging, the kill switch, brand offerings, evaluation
 * and simulation. It deliberately does NOT operate each customer's business
 * configuration; that lives on the customer's own screen, and a second place
 * to edit the same setting is a second place for it to be wrong.
 *
 * Data:
 *   GET  /god/workforce/overview | /tools | /templates | /providers | /health
 *   GET  /god/workforce/customers | /evaluation/suites | /evaluation/history
 *   PUT  /god/workforce/activation
 *   POST /god/workforce/evaluation/run
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import GodStyles from './GodStyles'
import { T } from './godTheme'

const TABS = ['Overview', 'Activation', 'Employees', 'Tools', 'Evaluation',
  'Customers', 'Health']

const STAGES = ['off', 'simulation', 'shadow', 'controlled', 'active']

function Pill ({ tone, children }) {
  const colour = { ok: T.green, warn: T.amber, bad: T.red, off: 'var(--gm-blue)' }[tone] || 'var(--gm-blue)'
  return (
    <span style={{
      display: 'inline-block', padding: '2px 9px', borderRadius: 999,
      fontSize: 11, letterSpacing: '.04em', textTransform: 'uppercase',
      color: colour, border: `1px solid ${colour}44`, background: `${colour}14`,
    }}>{children}</span>
  )
}

// `gm-sec` is not a class GodStyles defines, so the section label is styled
// here rather than referencing a rule that does not exist — a class name that
// resolves to nothing renders as unstyled text and looks like a broken page.
function Sec ({ children, top = 0 }) {
  return (
    <div style={{
      color: 'var(--gm-blue)', fontSize: 10, letterSpacing: '.14em',
      textTransform: 'uppercase', margin: `${top}px 0 8px`,
    }}>{children}</div>
  )
}

function Stat ({ k, v, s, tone }) {
  return (
    <div className="gm-stat">
      <div className="gm-k">{k}</div>
      <div className="gm-v" style={tone ? { color: tone } : undefined}>{v}</div>
      {s && <div className="gm-s">{s}</div>}
    </div>
  )
}

export default function GodWorkforce () {
  const navigate = useNavigate()
  const [tab, setTab] = useState('Overview')
  const [overview, setOverview] = useState(null)
  const [tools, setTools] = useState(null)
  const [templates, setTemplates] = useState(null)
  const [providers, setProviders] = useState(null)
  const [health, setHealth] = useState(null)
  const [customers, setCustomers] = useState([])
  const [suites, setSuites] = useState([])
  const [history, setHistory] = useState([])
  const [evalResult, setEvalResult] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const [a, b, c, d, e, f, g] = await Promise.all([
        api.get('/god/workforce/overview'),
        api.get('/god/workforce/tools'),
        api.get('/god/workforce/templates'),
        api.get('/god/workforce/providers'),
        api.get('/god/workforce/health'),
        api.get('/god/workforce/customers'),
        api.get('/god/workforce/evaluation/suites'),
      ])
      setOverview(a); setTools(b); setTemplates(c); setProviders(d)
      setHealth(e); setCustomers(f.customers || []); setSuites(g.suites || [])
      const h = await api.get('/god/workforce/evaluation/history')
      setHistory(h.runs || [])
    } catch (ex) {
      setErr(ex?.message || 'Could not load the AI workforce console.')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => { load() }, [load])

  async function setStage (scopeType, scopeId, state) {
    setBusy(true); setErr('')
    try {
      await api.put('/god/workforce/activation', {
        scope_type: scopeType, scope_id: scopeId || '', state,
        reason: 'set from the God AI Workforce console',
      })
      await load()
    } catch (ex) {
      setErr(ex?.message || 'That activation change was refused.')
    } finally { setBusy(false) }
  }

  async function setKill (scopeType, scopeId, engaged) {
    setBusy(true); setErr('')
    try {
      await api.put('/god/workforce/activation', {
        scope_type: scopeType, scope_id: scopeId || '', kill_switch: engaged,
        reason: engaged ? 'kill switch engaged from God Mode'
          : 'kill switch released from God Mode',
      })
      await load()
    } catch (ex) {
      setErr(ex?.message || 'That change was refused.')
    } finally { setBusy(false) }
  }

  async function runSuite (key) {
    setBusy(true); setErr(''); setEvalResult(null)
    try {
      const out = await api.post('/god/workforce/evaluation/run', { suite: key })
      setEvalResult(out)
      const h = await api.get('/god/workforce/evaluation/history')
      setHistory(h.runs || [])
    } catch (ex) {
      setErr(ex?.message || 'The evaluation could not be run.')
    } finally { setBusy(false) }
  }

  const dark = overview?.dark_launch || {}
  const couldExecute = dark.employees_that_could_execute ?? 0

  return (
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div className="gm-grid-overlay" />
      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1560, margin: '0 auto', padding: '24px 26px 60px' }}>

        <div style={{ padding: '8px 2px 18px' }}>
          <button className="gm-btn" style={{ marginBottom: 12 }} onClick={() => navigate('/god')}>
            ← COMMAND CENTER
          </button>
          <h1 style={{ margin: 0, color: 'var(--gm-head)', fontSize: 27, letterSpacing: '-.04em', lineHeight: 1 }}>
            AI Workforce
          </h1>
          <p style={{ margin: '9px 0 0', color: 'var(--gm-blue)', fontSize: 12, maxWidth: 860 }}>
            One engine, every AI employee. This console administers platform
            capability — the job library, the registered tools, how far anything
            may go, and the switch that stops it. A customer's own objective,
            audience and hours live on their screen, not here.
          </p>
        </div>

        {err && (
          <div className="gm-card" style={{ borderColor: 'var(--gm-pill-red-bd)', marginBottom: 16 }}>
            <div style={{ color: T.red, fontSize: 12 }}>{err}</div>
          </div>
        )}

        {/* ── THE ONE LINE THAT MATTERS DURING A DARK LAUNCH ─────────── */}
        <div className="gm-card" style={{
          marginBottom: 18,
          borderColor: couldExecute ? 'var(--gm-pill-amber-bd)' : 'var(--gm-pill-teal-bd)',
        }}>
          <div style={{ display: 'flex', gap: 14, alignItems: 'center', flexWrap: 'wrap' }}>
            <Pill tone={couldExecute ? 'warn' : 'ok'}>
              {couldExecute ? `${couldExecute} could reach people` : 'Dark — nothing can reach anybody'}
            </Pill>
            <span style={{ color: 'var(--gm-blue)', fontSize: 12 }}>
              platform stage <strong style={{ color: 'var(--gm-head)' }}>{dark.platform_stage || '—'}</strong>
              {dark.kill_switch ? ' · KILL SWITCH ENGAGED' : ''}
              {dark.environment_kill ? ' · ENVIRONMENT KILL SET' : ''}
              {' · live voice '}<strong style={{ color: dark.live_voice_enabled ? T.amber : T.green }}>
                {dark.live_voice_enabled ? 'ENABLED' : 'disabled'}
              </strong>
            </span>
          </div>
        </div>

        <div className="gm-stats" style={{ marginBottom: 18 }}>
          <Stat k="EMPLOYEES" v={dark.employees_total ?? '—'} s="across every customer" />
          <Stat k="CUSTOMERS" v={dark.customers_with_employees ?? '—'} s="with an AI team" />
          <Stat k="COULD EXECUTE" v={couldExecute}
                s="employees whose stage reaches people"
                tone={couldExecute ? T.amber : T.green} />
          <Stat k="JOBS" v={overview?.templates ?? '—'} s="in the library" />
          <Stat k="TOOLS" v={overview?.tools ?? '—'} s="registered" />
          <Stat k="OUTWARD TOOLS" v={(overview?.executing_tools || []).length}
                s="can reach a person" />
        </div>

        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 16 }}>
          {TABS.map(t => (
            <button key={t}
                    className="gm-btn"
                    style={tab === t ? { borderColor: T.blue, color: 'var(--gm-head)' } : undefined}
                    onClick={() => setTab(t)}>
              {t.toUpperCase()}
            </button>
          ))}
        </div>

        {loading && <div className="gm-card"><div style={{ color: 'var(--gm-blue)' }}>Loading…</div></div>}

        {/* ── OVERVIEW ───────────────────────────────────────────────── */}
        {!loading && tab === 'Overview' && (
          <div className="gm-card">
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(260px,1fr))', gap: 18 }}>
              <div>
                <Sec>WORK</Sec>
                {(overview?.supervisor?.work?.groups || []).map(g => (
                  <div key={g.key} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
                    <span style={{ color: 'var(--gm-blue)', fontSize: 12 }}>{g.label}</span>
                    <span style={{ color: 'var(--gm-head)', fontSize: 12 }}>{g.count}</span>
                  </div>
                ))}
              </div>
              <div>
                <Sec>TOOL CALLS (7d)</Sec>
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
                  <span style={{ color: 'var(--gm-blue)', fontSize: 12 }}>Allowed</span>
                  <span style={{ color: T.green, fontSize: 12 }}>{overview?.supervisor?.tools?.allowed ?? 0}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
                  <span style={{ color: 'var(--gm-blue)', fontSize: 12 }}>Refused</span>
                  <span style={{ color: T.amber, fontSize: 12 }}>{overview?.supervisor?.tools?.denied ?? 0}</span>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
                  <span style={{ color: 'var(--gm-blue)', fontSize: 12 }}>Errors</span>
                  <span style={{ color: T.red, fontSize: 12 }}>{overview?.supervisor?.tools?.errors ?? 0}</span>
                </div>
              </div>
              <div>
                <Sec>OUTBOUND ADAPTERS</Sec>
                {Object.entries(dark.outbound_adapters || {}).map(([ch, kind]) => (
                  <div key={ch} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
                    <span style={{ color: 'var(--gm-blue)', fontSize: 12 }}>{ch}</span>
                    <Pill tone={kind === 'live' ? 'off' : 'ok'}>{kind}</Pill>
                  </div>
                ))}
                <p style={{ color: 'var(--gm-blue)', fontSize: 11, margin: '8px 0 0' }}>
                  Live adapters are the production configuration. The gateway —
                  not the adapter — is what refuses an outward tool below the
                  controlled stage.
                </p>
              </div>
              <div>
                <Sec>MODEL PROVIDERS</Sec>
                {(providers?.providers || []).map(p => (
                  <div key={p.key} style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
                    <span style={{ color: 'var(--gm-blue)', fontSize: 12 }}>{p.label}</span>
                    <Pill tone={p.available ? 'ok' : 'off'}>
                      {p.available ? 'available' : 'not enabled'}
                    </Pill>
                  </div>
                ))}
                <p style={{ color: 'var(--gm-blue)', fontSize: 11, margin: '8px 0 0' }}>
                  {providers?.note}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* ── ACTIVATION ─────────────────────────────────────────────── */}
        {!loading && tab === 'Activation' && (
          <div className="gm-card">
            <Sec>PLATFORM</Sec>
            <p style={{ color: 'var(--gm-blue)', fontSize: 12, margin: '0 0 12px', maxWidth: 820 }}>
              Resolution takes the LOWEST stage across platform → brand →
              customer → employee, and an unconfigured scope is off rather than
              inherited. Raising the platform stage cannot switch any customer
              on by itself.
            </p>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 12 }}>
              {STAGES.map(s => (
                <button key={s} className="gm-btn" disabled={busy}
                        style={dark.platform_stage === s
                          ? { borderColor: T.blue, color: 'var(--gm-head)' } : undefined}
                        onClick={() => setStage('platform', '', s)}>
                  {s.toUpperCase()}
                </button>
              ))}
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <button className="gm-btn" disabled={busy}
                      style={{ borderColor: dark.kill_switch ? T.green : T.red,
                               color: dark.kill_switch ? T.green : T.red }}
                      onClick={() => setKill('platform', '', !dark.kill_switch)}>
                {dark.kill_switch ? 'RELEASE KILL SWITCH' : 'ENGAGE KILL SWITCH'}
              </button>
              <span style={{ color: 'var(--gm-blue)', fontSize: 11 }}>
                Engaging it also pauses every queued work item in scope.
              </span>
            </div>
          </div>
        )}

        {/* ── EMPLOYEES (the library) ────────────────────────────────── */}
        {!loading && tab === 'Employees' && (
          <div className="gm-card">
            <Sec>THE JOB LIBRARY</Sec>
            <table className="gm-table">
              <thead>
                <tr><th>Job</th><th>Role</th><th>Channels</th><th>Tools</th>
                  <th>Depth</th><th>In use</th></tr>
              </thead>
              <tbody>
                {(templates?.templates || []).map(t => (
                  <tr key={t.key}>
                    <td style={{ color: 'var(--gm-head)' }}>{t.name}</td>
                    <td>{t.job_role}</td>
                    <td>{(t.channels || []).join(', ') || '—'}</td>
                    <td>{(t.tool_keys || []).length}</td>
                    <td><Pill tone={t.depth === 'implemented' ? 'ok' : 'off'}>{t.depth}</Pill></td>
                    <td>{t.in_use}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p style={{ color: 'var(--gm-blue)', fontSize: 11, margin: '10px 0 0' }}>
              “Implemented” means the job has been driven end to end through the
              simulator and the evaluation harness. “Architected” means the
              engine supports it and it has not been proven to that standard yet.
            </p>
            <button className="gm-btn" style={{ marginTop: 12 }} disabled={busy}
                    onClick={async () => {
                      setBusy(true)
                      try { await api.post('/god/workforce/templates/sync', {}); await load() }
                      catch (ex) { setErr(ex?.message || 'Sync failed.') }
                      finally { setBusy(false) }
                    }}>
              SYNC LIBRARY
            </button>
          </div>
        )}

        {/* ── TOOLS ──────────────────────────────────────────────────── */}
        {!loading && tab === 'Tools' && (
          <div className="gm-card">
            <Sec>THE AGENT TOOL GATEWAY</Sec>
            <p style={{ color: 'var(--gm-blue)', fontSize: 12, margin: '0 0 12px', maxWidth: 860 }}>
              Every action available to any AI employee. An action that is not
              here cannot be taken — the model has no database, no shell, no
              HTTP and no SQL. Each call passes all thirteen gates below.
            </p>
            <ol style={{ color: 'var(--gm-blue)', fontSize: 11, margin: '0 0 16px', paddingLeft: 18 }}>
              {(tools?.gates || []).map(g => <li key={g}>{g.replace(/^\d+\.\s*/, '')}</li>)}
            </ol>
            <table className="gm-table">
              <thead>
                <tr><th>Tool</th><th>What it does</th><th>Channel</th>
                  <th>Reaches people</th><th>Needs eligibility</th></tr>
              </thead>
              <tbody>
                {(tools?.tools || []).map(t => (
                  <tr key={t.key}>
                    <td style={{ color: 'var(--gm-head)', fontFamily: 'ui-monospace, monospace', fontSize: 11 }}>{t.key}</td>
                    <td>{t.label}</td>
                    <td>{t.channel || '—'}</td>
                    <td>{t.reaches_outside ? <Pill tone="warn">yes</Pill> : <Pill tone="off">no</Pill>}</td>
                    <td>{t.requires_eligibility || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* ── EVALUATION ─────────────────────────────────────────────── */}
        {!loading && tab === 'Evaluation' && (
          <div className="gm-card">
            <Sec>EVALUATION</Sec>
            <p style={{ color: 'var(--gm-blue)', fontSize: 12, margin: '0 0 12px', maxWidth: 860 }}>
              Each suite runs against the real engine. Every scenario builds its
              own synthetic organization inside a savepoint and rolls back, so
              nothing belonging to a real customer is read or written.
            </p>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 14 }}>
              {suites.map(s => (
                <button key={s.key} className="gm-btn" disabled={busy}
                        title={s.why} onClick={() => runSuite(s.key)}>
                  RUN {s.label.toUpperCase()} ({s.case_count})
                </button>
              ))}
            </div>

            {evalResult && (
              <div className="gm-card" style={{ marginBottom: 14, background: 'var(--gm-row-hover-flat)' }}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                  <Pill tone={evalResult.verdict === 'PASSED' ? 'ok'
                    : evalResult.verdict?.startsWith('BLOCKED') ? 'bad' : 'warn'}>
                    {evalResult.verdict}
                  </Pill>
                  <span style={{ color: 'var(--gm-head)', fontSize: 12 }}>
                    {evalResult.passed}/{evalResult.total} passed
                  </span>
                </div>
                {(evalResult.failures || []).map(f => (
                  <div key={f.key} style={{ marginTop: 8, fontSize: 11 }}>
                    <div style={{ color: T.red }}>{f.key} ({f.dimension})</div>
                    <div style={{ color: 'var(--gm-blue)' }}>expected: {f.expected}</div>
                    <div style={{ color: 'var(--gm-blue)' }}>actual: {f.actual}</div>
                  </div>
                ))}
              </div>
            )}

            <Sec>HISTORY</Sec>
            <table className="gm-table">
              <thead><tr><th>When</th><th>Suite</th><th>Result</th><th>Verdict</th></tr></thead>
              <tbody>
                {history.map(h => (
                  <tr key={h.id}>
                    <td>{h.started_at ? new Date(h.started_at).toLocaleString() : '—'}</td>
                    <td>{h.suite}</td>
                    <td>{h.passed}/{h.total}</td>
                    <td>{h.verdict || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* ── CUSTOMERS ──────────────────────────────────────────────── */}
        {!loading && tab === 'Customers' && (
          <div className="gm-card">
            <Sec>CUSTOMERS WITH AN AI TEAM</Sec>
            {!customers.length && (
              <div style={{ color: 'var(--gm-blue)', fontSize: 12 }}>
                No customer has an AI employee yet.
              </div>
            )}
            <table className="gm-table">
              <thead>
                <tr><th>Customer</th><th>Employees</th><th>Active</th>
                  <th>Paused</th><th>Stage</th><th>Can reach people</th></tr>
              </thead>
              <tbody>
                {customers.map(c => (
                  <tr key={c.organization_id}>
                    <td style={{ color: 'var(--gm-head)' }}>
                      {c.name}{c.is_demo ? ' (demo)' : ''}
                    </td>
                    <td>{c.employees}</td>
                    <td>{c.active}</td>
                    <td>{c.paused}</td>
                    <td>{c.effective_stage}</td>
                    <td>{c.may_execute
                      ? <Pill tone="warn">yes</Pill>
                      : <Pill tone="ok">no</Pill>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* ── HEALTH ─────────────────────────────────────────────────── */}
        {!loading && tab === 'Health' && (
          <div className="gm-card">
            <Sec>HEALTH</Sec>
            <div className="gm-stats" style={{ marginBottom: 14 }}>
              <Stat k="STALLED" v={health?.stalled_work_items ?? 0}
                    s="due and not moving" />
              <Stat k="TOOL ERRORS" v={health?.tool_errors ?? 0} s="last 7 days" />
              <Stat k="UNACKNOWLEDGED" v={(health?.unacknowledged_events || []).length}
                    s="supervisor events" />
            </div>
            <Sec>REFUSALS</Sec>
            <table className="gm-table">
              <thead><tr><th>Reason</th><th>Count</th></tr></thead>
              <tbody>
                {(health?.denials || []).map(d => (
                  <tr key={d.code}><td>{d.code}</td><td>{d.count}</td></tr>
                ))}
              </tbody>
            </table>
            <Sec top={16}>NEEDS SOMEBODY</Sec>
            {!(health?.unacknowledged_events || []).length && (
              <div style={{ color: 'var(--gm-blue)', fontSize: 12 }}>Nothing outstanding.</div>
            )}
            {(health?.unacknowledged_events || []).map(e => (
              <div key={e.id} style={{ padding: '8px 0', borderBottom: '1px solid var(--gm-card-line)' }}>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  <Pill tone={e.severity === 'critical' ? 'bad'
                    : e.severity === 'warning' ? 'warn' : 'off'}>{e.severity}</Pill>
                  <span style={{ color: 'var(--gm-head)', fontSize: 12 }}>{e.message}</span>
                </div>
                {e.recommended_action && (
                  <div style={{ color: 'var(--gm-blue)', fontSize: 11, marginTop: 4 }}>
                    {e.recommended_action}
                  </div>
                )}
              </div>
            ))}
            <button className="gm-btn" style={{ marginTop: 12 }} disabled={busy}
                    onClick={async () => {
                      setBusy(true)
                      try { await api.post('/god/workforce/health/supervise', {}); await load() }
                      catch (ex) { setErr(ex?.message || 'The sweep failed.') }
                      finally { setBusy(false) }
                    }}>
              RUN SUPERVISION SWEEP
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
