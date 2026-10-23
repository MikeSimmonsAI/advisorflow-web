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
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import GodStyles from './GodStyles'
import GodAISwitchboard from './GodAISwitchboard'
import { T } from './godTheme'

function message (e, fallback) {
  const d = e?.data?.detail ?? e?.detail
  if (typeof d === 'string') return d
  if (d && typeof d.message === 'string') return d.message
  return fallback
}

function Box ({ label, value, sub, tone }) {
  return (
    <div className="gm-stat">
      <div className="gm-k">{label}</div>
      <div className="gm-v" style={tone ? { color: tone } : undefined}>{value}</div>
      {sub ? <div className="gm-s">{sub}</div> : null}
    </div>
  )
}

function when (iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString([], { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

export default function GodAIControl () {
  const navigate = useNavigate()
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
    <div className="gm-scope" style={{ minHeight: '100%' }}>
      <GodStyles />
      <div className="gm-grid-overlay" />
      <div style={{ position: 'relative', zIndex: 1, maxWidth: 1400, margin: '0 auto', padding: '24px 26px 60px' }}>
        <div style={{ marginBottom: 16 }}>
          <button className="gm-btn" style={{ marginBottom: 12 }} onClick={() => navigate('/god')}>
            ← COMMAND CENTER
          </button>
          <h1 style={{ margin: 0, color: 'var(--gm-head)', fontSize: 27, letterSpacing: '-.04em', lineHeight: 1 }}>AI Workforce</h1>
          <div style={{ color: T.dim, fontSize: 13, marginTop: 8 }}>
            Turn AI workers on or off for any account, see what they are doing, and stop everything with one button.
          </div>
        </div>

        {err && <div className="gm-card" style={{ color: T.red, fontSize: 13, marginBottom: 14 }}>{err}</div>}
        {!data && !err && <div className="gm-card"><div className="gm-empty">Loading…</div></div>}

        {data && (
          <>
            <div className="gm-card" style={{ marginBottom: 14, borderColor: data.stopped ? T.red : undefined }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 14, flexWrap: 'wrap', alignItems: 'center' }}>
                <div style={{ fontSize: 17, fontWeight: 700, color: data.stopped ? T.red : T.head }}>
                  {data.headline}
                </div>
                {data.stopped ? (
                  data.stopped_by_environment ? (
                    <div style={{ fontSize: 12, color: T.red, maxWidth: 360 }}>
                      Stopped by a server setting (AI_WORKFORCE_KILL or AI_OPERATIONS_KILL). Remove it in Render to release.
                    </div>
                  ) : (
                    <button className="gm-btn gm-primary" disabled={busy} onClick={() => stop(false)}>
                      {busy ? '…' : 'RELEASE EMERGENCY STOP'}
                    </button>
                  )
                ) : (
                  <button className="gm-btn" disabled={busy} onClick={() => stop(true)}
                    style={{ borderColor: T.red, color: T.red, fontWeight: 800 }}>
                    {busy ? '…' : 'EMERGENCY STOP — ALL AI'}
                  </button>
                )}
              </div>
              {!!data.problems.length && (
                <ul style={{ margin: '12px 0 0', paddingLeft: 18, fontSize: 13, color: T.amber, lineHeight: 1.6 }}>
                  {data.problems.map((p, i) => <li key={i}>{p}</li>)}
                </ul>
              )}
            </div>

            <div className="gm-stats" style={{ marginBottom: 14 }}>
              <Box label="Accounts on" value={`${data.accounts_on} / ${data.accounts_total}`} />
              <Box label="Worker" value={w.running ? 'RUNNING' : 'NOT RUNNING'}
                tone={w.running ? T.teal : T.red}
                sub={w.last_success_at ? `last run ${when(w.last_success_at)}` : 'no run recorded yet'} />
              <Box label="Real messages" value={data.real_messages ? 'ON' : 'OFF'}
                tone={data.real_messages ? T.amber : T.teal}
                sub={data.real_messages ? 'AI can contact real people' : 'practice only — nobody is contacted'} />
              <Box label="AI workers" value={data.ai_workers} />
              <Box label="Working now" value={data.work.working_now} sub="leads being worked" />
              <Box label="Waiting" value={data.work.waiting} sub="for the lead to reply" />
              <Box label="Need a person" value={data.work.needs_a_person + data.open_handoffs}
                tone={(data.work.needs_a_person + data.open_handoffs) ? T.amber : undefined}
                sub="leads passed to a person or stuck" />
              <Box label="Finished" value={data.work.finished} sub="all-time" />
            </div>

            <div className="gm-card" style={{ marginBottom: 14 }}>
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

        <div style={{ fontWeight: 700, margin: '6px 0 8px' }}>Turn AI workers on or off</div>
        <GodAISwitchboard key={refreshKey} embedded onChange={load} />

        <div className="gm-card" style={{ marginTop: 14, fontSize: 12, color: T.dim }}>
          <strong>Advanced (technical):</strong>{' '}
          <Link to="/god/workforce">engine &amp; job library</Link>{' · '}
          <Link to="/god/ai-operations">messaging switches</Link>{' · '}
          <Link to="/god/ai-deployment">brand terms &amp; deployments</Link>
        </div>
      </div>
    </div>
  )
}
