/**
 * AI WORKFORCE SWITCHBOARD (God Mode) - turn safe test mode on or off for a
 * brand or for single accounts, picked from lists. No ids to type.
 *
 * "On" means SAFE TEST MODE: customers in that account can set up an AI
 * worker, run a test and start it working on pretend leads. Nothing is ever
 * sent to a real person. Real messages are a separate, later switch.
 *
 * Data:
 *   GET  /god/ai-workforce/switchboard
 *   POST /god/ai-workforce/switchboard/enable   {platform_id, organization_ids?}
 *   POST /god/ai-workforce/switchboard/disable  {platform_id, organization_ids?}
 *   POST /god/ai-workforce/switchboard/run-worker-now
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { T } from './godTheme'

function message (e, fallback) {
  const d = e?.data?.detail ?? e?.detail
  if (typeof d === 'string') return d
  if (d && typeof d.message === 'string') return d.message
  return fallback
}

const JOB_NAMES = {
  lead_qualifier: 'Lead Qualifier',
  appointment_setter: 'Appointment Setter',
}

function Pill ({ ok, children }) {
  return (
    <span style={{
      display: 'inline-block', padding: '2px 8px', borderRadius: 999, fontSize: 11,
      fontWeight: 700, letterSpacing: '.04em',
      color: ok ? T.teal : 'var(--gm-blue)',
      border: `1px solid ${ok ? T.teal : 'var(--gm-border, #2a3550)'}`,
    }}>{children}</span>
  )
}

export default function GodAISwitchboard ({ embedded = false, onChange } = {}) {
  const [data, setData] = useState(null)
  const [busy, setBusy] = useState('')
  const [err, setErr] = useState('')
  const [note, setNote] = useState('')

  const load = useCallback(async () => {
    setErr('')
    try { setData(await api.get('/god/ai-workforce/switchboard')) } catch (e) {
      setErr(message(e, 'Could not load the switchboard.'))
    }
  }, [])
  useEffect(() => { load() }, [load])

  async function act (path, body, key, done) {
    setBusy(key); setErr(''); setNote('')
    try {
      const r = await api.post(path, body)
      setData(r.status)
      setNote(done(r))
      if (onChange) onChange()
    } catch (e) {
      setErr(message(e, 'That did not work.'))
    } finally { setBusy('') }
  }

  const on = (brand, org) => act('/god/ai-workforce/switchboard/enable',
    { platform_id: brand.id, organization_ids: org ? [org.id] : null },
    `on-${org ? org.id : brand.id}`,
    () => `Test mode is ON for ${org ? org.name : `every ${brand.name} account`}.`)
  const off = (brand, org) => {
    if (!window.confirm(`Turn AI test mode OFF for ${org ? org.name : `every ${brand.name} account`}? AI workers there stop. Nothing is deleted.`)) return
    act('/god/ai-workforce/switchboard/disable',
      { platform_id: brand.id, organization_ids: org ? [org.id] : null },
      `off-${org ? org.id : brand.id}`,
      () => `Test mode is OFF for ${org ? org.name : `every ${brand.name} account`}.`)
  }
  const runNow = () => act('/god/ai-workforce/switchboard/run-worker-now', {},
    'run', (r) => r.summary?.skipped
      ? `Worker did not run: ${r.summary.skipped}.`
      : `Worker ran: ${r.summary?.claimed ?? 0} picked up, ${r.summary?.ran ?? 0} worked, ${r.summary?.errors ?? 0} errors.`)

  if (!data) {
    return <div className="gm-card"><div className="gm-empty">{err || 'Loading…'}</div></div>
  }

  const w = data.worker || {}
  const stopped = data.environment_kill || data.platform_killed

  const brandCards = (
    <>
      {(data.brands || []).map(brand => (
        <div key={brand.id} className="gm-card" style={{ marginBottom: 16 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
            <div>
              <div style={{ fontSize: 15, fontWeight: 700 }}>{brand.name}</div>
              <div style={{ color: 'var(--gm-blue)', fontSize: 11 }}>
                {brand.accounts_ready} of {brand.accounts.length} accounts ready
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button className="gm-btn gm-primary" disabled={!!busy || !brand.accounts.length}
                onClick={() => on(brand, null)}>
                {busy === `on-${brand.id}` ? 'TURNING ON…' : 'TURN ON FOR ALL ACCOUNTS'}
              </button>
              <button className="gm-btn" disabled={!!busy || !brand.accounts_ready}
                onClick={() => off(brand, null)}>
                TURN OFF FOR ALL
              </button>
            </div>
          </div>
          <div className="gm-tablewrap" tabIndex={0} role="region" aria-label={`${brand.name} accounts`}>
            <table className="gm-table">
              <thead className="gm-thead">
                <tr><th>Account</th><th>Plan</th><th>AI test mode</th>
                  {(data.jobs_ready || []).map(j => <th key={j}>{JOB_NAMES[j] || j}</th>)}
                  <th>AI workers</th><th /></tr>
              </thead>
              <tbody>
                {brand.accounts.map(a => {
                  const ready = a.summary.startsWith('Ready')
                  return (
                    <tr key={a.id}>
                      <td>{a.name}{a.demo ? ' (demo)' : ''}</td>
                      <td>{a.plan || '—'}</td>
                      <td><Pill ok={ready}>{ready ? 'ON' : (a.killed ? 'STOPPED' : 'OFF')}</Pill></td>
                      {(data.jobs_ready || []).map(j => (
                        <td key={j} style={{ fontSize: 11 }}>{a.jobs[j]?.on_plan ? 'Yes' : 'No'}</td>
                      ))}
                      <td>{a.ai_workers}</td>
                      <td style={{ whiteSpace: 'nowrap' }}>
                        {ready ? (
                          <button className="gm-btn" disabled={!!busy} onClick={() => off(brand, a)}>
                            {busy === `off-${a.id}` ? '…' : 'TURN OFF'}
                          </button>
                        ) : (
                          <button className="gm-btn gm-primary" disabled={!!busy} onClick={() => on(brand, a)}>
                            {busy === `on-${a.id}` ? '…' : 'TURN ON'}
                          </button>
                        )}
                      </td>
                    </tr>
                  )
                })}
                {!brand.accounts.length && (
                  <tr><td colSpan={5} className="gm-empty">No accounts under this brand.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      ))}
    </>
  )

  if (embedded) {
    return (
      <>
        <div className="gm-card" style={{ marginBottom: 16, display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, color: 'var(--gm-blue)', flex: 1, minWidth: 260 }}>
            &quot;On&quot; means safe test mode: customers in that account can set up an AI worker,
            run a test and start it. It practices on pretend leads only.
          </span>
          <button className="gm-btn" disabled={!!busy} onClick={runNow}>
            {busy === 'run' ? 'RUNNING…' : 'RUN WORKER NOW'}
          </button>
          {note && <div style={{ color: T.teal, fontSize: 12, width: '100%' }}>{note}</div>}
          {err && <div style={{ color: T.red, fontSize: 12, width: '100%' }}>{err}</div>}
        </div>
        {brandCards}
      </>
    )
  }

  return (
    <>
      <div className="gm-card" style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap', alignItems: 'center' }}>
          <div>
            <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 6 }}>
              AI Workforce — safe test mode
            </div>
            <div style={{ color: 'var(--gm-blue)', fontSize: 12, maxWidth: 760, lineHeight: 1.5 }}>
              Turn it on for a brand or one account. Customers there can then pick an AI worker,
              answer a few questions, run a test and start it. It practices on pretend leads only —
              <strong> real texts, emails and calls are {data.real_messages ? 'ON' : 'OFF'}</strong>.
              Ready jobs: {(data.jobs_ready || []).map(j => JOB_NAMES[j] || j).join(', ')}.
            </div>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ fontSize: 12, marginBottom: 4 }}>
              Background worker: <Pill ok={w.running}>{w.running ? 'RUNNING' : 'NOT RUNNING YET'}</Pill>
            </div>
            <div style={{ color: 'var(--gm-blue)', fontSize: 11, marginBottom: 6 }}>
              {w.last_success_at ? `Last good pass ${new Date(w.last_success_at).toLocaleString()}` : 'No pass recorded yet (it starts about a minute after each deploy).'}
            </div>
            <button className="gm-btn" disabled={!!busy} onClick={runNow}>
              {busy === 'run' ? 'RUNNING…' : 'RUN WORKER NOW'}
            </button>
          </div>
        </div>
        {stopped && (
          <div style={{ color: T.red, fontSize: 12, marginTop: 10 }}>
            The emergency stop is ON. Nothing will run until it is cleared.
          </div>
        )}
        {note && <div style={{ color: T.teal, fontSize: 12, marginTop: 10 }}>{note}</div>}
        {err && <div style={{ color: T.red, fontSize: 12, marginTop: 10 }}>{err}</div>}
      </div>

      {brandCards}
    </>
  )
}
