/**
 * AI WORKFORCE (God Mode) — the one page an owner needs.
 *
 * Plain words only. Top to bottom: what is happening right now, anything
 * that needs you, the emergency stop, recent AI activity across every
 * account, and the on/off switch for every brand and account. The technical
 * consoles stay reachable at the bottom as "Advanced".
 *
 * Data:
 *   GET  /god/ai-workforce/control
 *   POST /god/ai-workforce/control/emergency-stop   {on}
 *   (switches: see GodAISwitchboard)
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'
import GodStyles from './GodStyles'
import GodAISwitchboard from './GodAISwitchboard'
import { T } from './godTheme'
import { Pill, Page, Strip, Card, ErrorState, Loading, Section } from './ui/GodKit'

function message (e, fallback) {
  const d = e?.data?.detail ?? e?.detail
  if (typeof d === 'string') return d
  if (d && typeof d.message === 'string') return d.message
  return fallback
}

function when (iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export default function GodAIControl () {
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [refreshKey, setRefreshKey] = useState(0)

  const load = useCallback(async () => {
    setErr('')
    try { setData(await api.get('/god/ai-workforce/control')) } catch (e) {
      setErr(message(e, 'Could not load the AI Workforce page.'))
    }
  }, [])
  useEffect(() => { load() }, [load])
  useEffect(() => {
    const t = setInterval(load, 30000)   // keep "right now" honest
    return () => clearInterval(t)
  }, [load])

  async function stop (on) {
    const text = on
      ? 'EMERGENCY STOP: every AI worker on every account stops now and its waiting work is parked. Nothing is deleted. Continue?'
      : 'Release the emergency stop? AI workers on accounts that are turned on will start working again.'
    if (!window.confirm(text)) return
    setBusy(true); setErr('')
    try {
      const r = await api.post('/god/ai-workforce/control/emergency-stop', { on })
      setData(r.control); setRefreshKey(k => k + 1)
    } catch (e) { setErr(message(e, 'That did not work.')) } finally { setBusy(false) }
  }

  const w = data?.worker || {}

  return (
    <>
      <GodStyles />
      {/* Owner Desk design language (#411): shared header with the emergency
          stop beside the title, one status card, two plain rows of numbers.
          The stop/release flow, its confirmation and every number are unchanged. */}
      <Page eyebrow="AI Workforce" title="AI Workforce"
        sub="Turn AI workers on or off for any account, see what they are doing, and stop everything with one button."
        actions={data ? (data.stopped ? (
          data.stopped_by_environment ? null : (
            <button type="button" className="gk-btn gk-btn--primary" disabled={busy} onClick={() => stop(false)}>
              {busy ? '…' : 'Release emergency stop'}
            </button>)
        ) : (
          <button type="button" className="gk-btn gk-btn--danger" disabled={busy} onClick={() => stop(true)}
            style={{ fontWeight: 800 }}>
            {busy ? '…' : 'Emergency stop — all AI'}
          </button>
        )) : null}>

        {err ? <ErrorState onRetry={load}>{err}</ErrorState> : null}
        {!data && !err ? <Card><Loading /></Card> : null}

        {data && (
          <>
            <Card style={data.stopped ? { borderColor: 'var(--gm-pill-red-bd)' } : undefined}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, flexWrap: 'wrap', alignItems: 'center' }}>
                <div style={{ fontSize: 17, fontWeight: 750, color: data.stopped ? 'var(--gm-red)' : 'var(--gm-head)', letterSpacing: '-.01em' }}>
                  {data.headline}
                </div>
                <Pill tone={data.stopped ? 'bad' : !w.running || data.problems.length ? 'warn' : data.accounts_on ? 'good' : 'off'}>
                  {data.stopped ? 'Stopped' : !w.running || data.problems.length ? 'Needs attention' : data.accounts_on ? 'Working' : 'Off'}
                </Pill>
              </div>
              {data.stopped && data.stopped_by_environment ? (
                <p className="gk-row-sub" style={{ color: 'var(--gm-red)', marginTop: 8 }}>
                  Stopped by a server setting (AI_WORKFORCE_KILL or AI_OPERATIONS_KILL). Remove it in Render to release.
                </p>
              ) : null}
              {!!data.problems.length && (
                <ul style={{ margin: '12px 0 0', paddingLeft: 18, fontSize: 13, color: 'var(--gm-amber)', lineHeight: 1.6 }}>
                  {data.problems.map((p, i) => <li key={i}>{p}</li>)}
                </ul>
              )}
            </Card>

            <Section title="Status">
              <Strip items={[
                { label: 'Accounts on', value: `${data.accounts_on} / ${data.accounts_total}`, sub: 'with AI workers turned on' },
                { label: 'Worker', value: w.running ? 'Running' : 'Not running', tone: w.running ? 'teal' : 'red',
                  sub: w.last_success_at ? `last run ${when(w.last_success_at)}` : 'no run recorded yet' },
                { label: 'Real messages', value: data.real_messages ? 'On' : 'Off', tone: data.real_messages ? 'amber' : 'teal',
                  sub: data.real_messages ? 'AI can contact real people' : 'practice only — nobody is contacted' },
                { label: 'AI workers', value: data.ai_workers, sub: 'set up across accounts' },
              ]} />
            </Section>

            <Section title="Work">
              <Strip items={[
                { label: 'Working now', value: data.work.working_now, sub: 'leads being worked' },
                { label: 'Waiting', value: data.work.waiting, sub: 'for the lead to reply' },
                { label: 'Need a person', value: data.work.needs_a_person + data.open_handoffs,
                  tone: (data.work.needs_a_person + data.open_handoffs) ? 'amber' : undefined, sub: 'passed to a person or stuck' },
                { label: 'Finished', value: data.work.finished, sub: 'all-time' },
              ]} />
            </Section>

            <div className="gk-section" />
            {Array.isArray(data.jobs) && (
              <div className="gm-card" style={{ padding: '16px 18px', marginBottom: 14 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap', marginBottom: 8 }}>
                  <div style={{ fontWeight: 700 }}>
                    The {data.jobs.length} AI jobs
                  </div>
                  <div style={{ fontSize: 12, color: T.dim }}>
                    {data.jobs.filter(j => j.proven).length} proven end to end in test mode ·{' '}
                    {data.jobs.filter(j => !j.proven).length} not proven yet · {data.jobs.filter(j => j.deployed).length} in use
                  </div>
                </div>
                <div className="gm-tablewrap" tabIndex={0} role="region" aria-label="AI jobs">
                  <table className="gm-table">
                    <thead className="gm-thead"><tr><th>Job</th><th>What it does</th><th>Status</th><th>AI workers</th><th>Runs, 7 days</th></tr></thead>
                    <tbody>
                      {data.jobs.map(j => (
                        <tr key={j.key}>
                          <td style={{ fontWeight: 650, whiteSpace: 'nowrap' }}>{j.name}</td>
                          <td>{j.what}{j.prerequisite ? <div style={{ color: T.dim, fontSize: 11.5, marginTop: 2 }}>{j.prerequisite}</div> : null}</td>
                          <td style={{ whiteSpace: 'nowrap' }}>
                            <Pill tone={j.proven ? 'good' : 'off'}>
                              {j.proven ? 'Proven in test mode' : 'Not proven yet'}
                            </Pill>
                          </td>
                          <td>{j.workers ? `${j.workers} (${j.deployed} deployed)` : 'None'}</td>
                          <td style={{ whiteSpace: 'nowrap' }}>
                            {j.runs_7d.completed || j.runs_7d.failed
                              ? `${j.runs_7d.completed} finished · ${j.runs_7d.failed} failed` : '—'}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div style={{ fontSize: 11.5, color: T.dim, marginTop: 8 }}>
                  "Proven" means an automatic test runs the whole job on practice leads with nothing sent. It does not mean
                  real messages are on — that is the Real messages setting above.
                </div>
              </div>
            )}

            <div className="gm-card" style={{ padding: '16px 18px', marginBottom: 14 }}>
              <div style={{ fontWeight: 700, marginBottom: 8 }}>What the AI did recently (last 7 days)</div>
              {data.recent.length ? (
                <div className="gm-tablewrap" tabIndex={0} role="region" aria-label="Recent AI activity">
                  <table className="gm-table">
                    <thead className="gm-thead"><tr><th>When</th><th>Account</th><th>AI worker</th><th>What it did</th><th>Result</th></tr></thead>
                    <tbody>
                      {data.recent.map((r, i) => (
                        <tr key={i}>
                          <td style={{ whiteSpace: 'nowrap' }}>{when(r.at)}</td>
                          <td>{r.account}</td>
                          <td>{r.worker}</td>
                          <td>{r.what}{r.practice ? ' (pretend lead)' : ''}</td>
                          <td>{r.result}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div className="gm-empty">Nothing yet. Activity shows here as soon as any AI worker does something.</div>
              )}
            </div>
          </>
        )}

        <Section title="Turn AI workers on or off" sub="Per account. Changes take effect on the next worker run.">
          <GodAISwitchboard key={refreshKey} embedded onChange={load} />
        </Section>

        <Section title="Advanced (technical)">
          <Card>
            <div className="gk-row-sub" style={{ marginTop: 0 }}>
              <Link className="gk-link" to="/god/workforce">Engine &amp; job library</Link>{' · '}
              <Link className="gk-link" to="/god/ai-operations">Messaging switches</Link>{' · '}
              <Link className="gk-link" to="/god/ai-deployment">Brand terms &amp; deployments</Link>
            </div>
          </Card>
        </Section>
      </Page>
    </>
  )
}
