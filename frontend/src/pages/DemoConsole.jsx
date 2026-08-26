/**
 * Demo Console — what the operator drives during a presentation.
 *
 * NO BUSINESS LOGIC LIVES HERE. Every button posts to the scenario engine and
 * re-renders whatever comes back. This component does not know what a cadence
 * is, what a proposal version means, or which step follows which — the server
 * owns the running order and returns it. That is deliberate: a demo whose
 * sequence lived in React would drift from the demo the tests exercise, and
 * the tests are the reason anybody can trust this in front of a customer.
 *
 * THE BIGGEST THING ON THE PAGE IS THE NEXT ACTION. An operator mid-sentence
 * needs one answer: what do I show now. The guide strip at the top carries the
 * step, the narration to say, and where they are in the story.
 *
 * IT RENDERS NOTHING OUTSIDE THE DEMO ENVIRONMENT. The route is registered in
 * every build, and the probe is what decides. A production bundle that somehow
 * navigated here shows a plain "not available" panel and issues no control
 * calls — and the server would 404 them anyway.
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  fetchEnvironment, demoState, seedScenario, advanceScenario, resetDemo,
} from '../api/demo'
import { getCurrentUser } from '../api/client'
import './DemoConsole.css'

const STATUS_LABEL = {
  empty: 'NOT LOADED',
  ready: 'READY',
  running: 'IN PROGRESS',
  complete: 'COMPLETE',
}

function timeOf(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
}

export default function DemoConsole() {
  const [envInfo, setEnvInfo] = useState(undefined)   // undefined = probing
  const [state, setState] = useState(null)
  const [busy, setBusy] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const navigate = useNavigate()
  const user = getCurrentUser()

  const load = useCallback(async () => {
    try {
      setState(await demoState())
      setError(null)
    } catch (e) {
      setError(e?.message || 'Could not read demo state.')
    }
  }, [])

  useEffect(() => {
    fetchEnvironment().then((d) => {
      setEnvInfo(d)
      if (d && d.demo_mode) load()
    })
  }, [load])

  async function run(label, fn) {
    setBusy(label)
    setResult(null)
    setError(null)
    try {
      const out = await fn()
      setResult(out?.result || out?.message
        || (typeof out?.total === 'number'
          ? `Removed ${out.total} demo record${out.total === 1 ? '' : 's'}.`
          : 'Done.'))
      await load()
    } catch (e) {
      setError(e?.message || 'That did not work.')
    } finally {
      setBusy(null)
    }
  }

  if (envInfo === undefined) {
    return <div className="dc-scope"><div className="dc-msg">Checking environment…</div></div>
  }

  // Production, or any non-demo build. No controls, no calls, no affordance.
  if (!envInfo.demo_mode) {
    return (
      <div className="dc-scope">
        <div className="dc-msg">
          <h2>Demo Mode is not available here</h2>
          <p>
            This is the <strong>{envInfo.environment}</strong> environment. Demo
            scenarios, seeding and reset exist only in the isolated demo
            environment, which runs against its own database.
          </p>
          <button className="dc-btn ghost" style={{ marginTop: 18, maxWidth: 200 }}
                  onClick={() => navigate('/')}>Back to the app</button>
        </div>
      </div>
    )
  }

  const isOwner = ['god_admin', 'super_admin'].includes((user?.role || '').toLowerCase())
  if (!isOwner) {
    return (
      <div className="dc-scope">
        <div className="dc-msg">
          <h2>Demo controls require a platform owner</h2>
          <p>
            You are signed in as <strong>{user?.full_name || 'a demo user'}</strong>.
            The scenario controls stay owner-only even inside the demo — Demo
            Mode does not relax permissions.
          </p>
        </div>
      </div>
    )
  }

  const scenarios = state?.scenarios || []
  // The story the operator is actually mid-way through: the one that is
  // running, else the one that is loaded, else nothing.
  const active = scenarios.find((s) => s.status === 'running')
    || scenarios.find((s) => s.status === 'ready')
    || scenarios.find((s) => s.status === 'complete')

  return (
    <div className="dc-scope">
      <div className="dc-head">
        <h1 className="dc-title">Demo Console</h1>
        <p className="dc-sub">
          Load a scenario, then advance it one step at a time while you talk.
          Every record is real; every provider is simulated.
        </p>
        <div className="dc-envline">● {envInfo.environment.toUpperCase()} ENVIRONMENT</div>
      </div>

      {/* ── what to show next ── */}
      <div className="dc-guide">
        <div className="dc-guide-kicker">WHAT TO SHOW NEXT</div>
        {active && active.next_step ? (
          <>
            <div className="dc-guide-step">
              Step {active.current_step + 1} of {active.total_steps} —{' '}
              {active.next_step.label}
            </div>
            <div className="dc-guide-narration">{active.next_step.narration}</div>
          </>
        ) : active && active.status === 'complete' ? (
          <>
            <div className="dc-guide-step">
              {active.scenario.name} — complete
            </div>
            <div className="dc-guide-narration">
              Every step has run. Walk the screens, then reset when you are
              ready for the next audience.
            </div>
          </>
        ) : (
          <div className="dc-guide-empty">
            Nothing is loaded yet. Pick a scenario below and press Load.
          </div>
        )}
      </div>

      {result && <div className="dc-result">{result}</div>}
      {error && <div className="dc-result error">{error}</div>}

      {/* ── scenario cards ── */}
      <div className="dc-section-title">SCENARIOS</div>
      <div className="dc-grid">
        {scenarios.map((s) => {
          const key = s.scenario.key
          const pct = s.total_steps
            ? Math.round((s.current_step / s.total_steps) * 100) : 0
          return (
            <div
              key={key}
              className={`dc-card${active && active.scenario.key === key ? ' is-active' : ''}${s.status === 'complete' ? ' is-complete' : ''}`}
            >
              <div className="dc-card-top">
                <div className="dc-card-name">{s.scenario.name}</div>
                <span className={`dc-domain ${s.scenario.domain}`}>
                  {s.scenario.domain === 'customer' ? 'CUSTOMER' : 'BRAND SALES'}
                </span>
              </div>
              <div className="dc-card-summary">{s.scenario.summary}</div>

              <div className="dc-progress-row">
                <span className={`dc-status ${s.status}`}>
                  {STATUS_LABEL[s.status] || s.status.toUpperCase()}
                </span>
                <span>{s.current_step} / {s.total_steps}</span>
              </div>
              <div className="dc-bar">
                <div className="dc-bar-fill" style={{ width: `${pct}%` }} />
              </div>

              <ul className="dc-steps">
                {s.steps.map((st) => {
                  const isNext = !st.done && st.index === s.current_step
                  return (
                    <li
                      key={st.key}
                      className={`dc-step${st.done ? ' done' : ''}${isNext ? ' next' : ''}`}
                    >
                      <span className="dc-step-mark">
                        {st.done ? '✓' : isNext ? '▸' : '○'}
                      </span>
                      <span>{st.label}</span>
                    </li>
                  )
                })}
              </ul>

              <div className="dc-actions">
                <button
                  className="dc-btn"
                  disabled={!!busy}
                  onClick={() => run(`seed-${key}`, () => seedScenario(key))}
                >
                  {busy === `seed-${key}` ? 'Loading…'
                    : s.status === 'empty' ? 'Load scenario' : 'Reload clean'}
                </button>
                <button
                  className="dc-btn primary"
                  disabled={!!busy || s.status === 'empty' || s.status === 'complete'}
                  onClick={() => run(`adv-${key}`, () => advanceScenario(key))}
                >
                  {busy === `adv-${key}` ? 'Running…' : 'Advance step'}
                </button>
              </div>
            </div>
          )
        })}
      </div>

      {/* ── firewall ── */}
      <div className="dc-section-title">SIDE-EFFECT FIREWALL</div>
      <div className="dc-firewall">
        {state?.firewall?.installed
          ? <span className="dc-fw-ok">● Installed — outbound network egress is default-deny.</span>
          : <span className="dc-fw-bad">● NOT INSTALLED — do not present from this environment.</span>}
        <div className="dc-fw-note">
          Twilio, Resend, Google, Microsoft, Zoom, Stripe and Retell are all
          unreachable from this process. Nothing below is an error you caused —
          it is the firewall doing its job.
        </div>
        {state?.firewall?.blocked_attempts?.length > 0 && (
          <>
            <div className="dc-fw-note" style={{ marginTop: 10 }}>
              Blocked during this session — usually a missing simulation:
            </div>
            <ul className="dc-fw-list">
              {state.firewall.blocked_attempts.map((a, i) => <li key={i}>{a}</li>)}
            </ul>
          </>
        )}
      </div>

      {/* ── activity ── */}
      <div className="dc-section-title">RECENT DEMO ACTIVITY</div>
      <div className="dc-log">
        {(state?.recent_events || []).length === 0 && (
          <div className="dc-log-row"><span className="dc-log-detail">Nothing yet.</span></div>
        )}
        {(state?.recent_events || []).map((e, i) => (
          <div key={i} className={`dc-log-row${e.success ? '' : ' failed'}`}>
            <span className="dc-log-time">{timeOf(e.at)}</span>
            <span className="dc-log-action">{e.action.toUpperCase()}</span>
            <span className="dc-log-detail">
              {e.step ? `${e.step} — ` : ''}{e.detail}
            </span>
          </div>
        ))}
      </div>

      {/* ── full wipe ── */}
      <div className="dc-section-title">BETWEEN PRESENTATIONS</div>
      <div className="dc-actions" style={{ maxWidth: 420 }}>
        <button
          className="dc-btn danger"
          disabled={!!busy}
          onClick={() => run('reset', () => resetDemo())}
        >
          {busy === 'reset' ? 'Resetting…' : 'Reset everything'}
        </button>
        <button className="dc-btn ghost" disabled={!!busy} onClick={load}>
          Refresh
        </button>
      </div>
      <p className="dc-sub" style={{ marginTop: 10, maxWidth: 620 }}>
        Reset removes every demo record and returns all three scenarios to
        empty. Loading a single scenario only resets that one, so you can keep
        the customer story and the sales story on screen at the same time.
      </p>
    </div>
  )
}
