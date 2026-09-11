/**
 * ONE AI EMPLOYEE, AS A MANAGER READS IT.
 *
 * THE SAME NUMBERS THE TEAM PAGE SHOWS. This screen reads
 * `/scorecards/{id}`, which the server builds from the same function the team
 * list uses — a detail page with its own query is a detail page that
 * eventually disagrees with the list it was opened from, and "the team page
 * says 14 and this one says 12" is a support call nobody can settle without
 * reading both queries.
 *
 * PAUSE AND RESUME ARE DELEGATIONS. They POST to routes that call T8's
 * deployment lifecycle, which runs its own checks — a resume re-verifies
 * entitlement and readiness and may come back "ready, and somebody still has
 * to start it". That answer is shown rather than smoothed over.
 *
 * Data:
 *   GET  /ai-workforce-intelligence/scorecards/{employeeId}
 *   GET  /ai-workforce-intelligence/attention?employee_id=…
 *   GET  /ai-workforce-intelligence/quality
 *   POST /ai-workforce-intelligence/employees/{deploymentId}/pause|resume
 */
import { useCallback, useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import PageShell from '../components/PageShell'
import '../styles/shared.css'

function Unknown ({ note }) {
  return <span style={{ color: 'var(--text-secondary)' }} title={note}>—</span>
}

function Num ({ entry }) {
  if (!entry || entry.value === null || entry.value === undefined) {
    return <Unknown note={entry?.note} />
  }
  if (entry.unit === 'rate') {
    return <span title={entry.calculation}>
      {Math.round(entry.value * 1000) / 10}%
    </span>
  }
  return <span title={entry.calculation}>{entry.value}</span>
}

export default function AIWorkforceEmployee () {
  const { employeeId } = useParams()
  const navigate = useNavigate()
  const [card, setCard] = useState(null)
  const [attention, setAttention] = useState([])
  const [quality, setQuality] = useState(null)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    setLoading(true); setErr('')
    try {
      const [a, b, c] = await Promise.all([
        api.get(`/ai-workforce-intelligence/scorecards/${employeeId}`),
        api.get(`/ai-workforce-intelligence/attention?employee_id=${employeeId}`),
        api.get('/ai-workforce-intelligence/quality'),
      ])
      setCard(a)
      setAttention(b.items || [])
      setQuality((c.employees || {})[employeeId] || null)
    } catch (e) {
      setErr(e?.message || 'Could not load this AI employee.')
    } finally {
      setLoading(false)
    }
  }, [employeeId])
  useEffect(() => { load() }, [load])

  async function control (what) {
    const deploymentId = card?.card?.deployment?.id
    if (!deploymentId) {
      setErr('This employee has no deployment record, so it cannot be '
             + 'paused or resumed from here.')
      return
    }
    setBusy(true); setErr('')
    try {
      const res = await api.post(
        `/ai-workforce-intelligence/employees/${deploymentId}/${what}`, {})
      await load()
      if (res?.result?.state && what === 'resume') {
        window.alert(
          'Resumed. Its entitlement and readiness were re-checked; it is now '
          + `'${res.state}'.`)
      }
    } catch (e) {
      setErr(e?.detail?.message || e?.message || 'That did not go through.')
    } finally {
      setBusy(false)
    }
  }

  const c = card?.card
  const outcomes = c?.outcomes || {}
  const trends = c?.trends || {}

  return (
    <PageShell
      eyebrow="AI Workforce"
      title={c?.name || 'AI employee'}
      subtitle={c ? `${c.job_role} · ${c.deployment?.state || 'no deployment'}`
        : 'Loading…'}
      action={
        <div style={{ display: 'flex', gap: 8 }}>
          <button className="btn btn--ghost"
                  onClick={() => navigate('/ai-workforce-command?tab=employees')}>
            Back
          </button>
          {c?.deployment?.live ? (
            <button className="btn btn--ghost" disabled={busy}
                    onClick={() => control('pause')}>Pause</button>
          ) : (
            <button className="btn btn--ghost" disabled={busy}
                    onClick={() => control('resume')}>Resume</button>
          )}
        </div>
      }
    >
      {err ? <div className="panel panel--error">{err}</div> : null}
      {loading ? <div className="panel">Loading…</div> : null}

      {attention.length ? (
        <>
          <h3>What needs you about this employee</h3>
          {attention.map(item => (
            <div key={item.id} className="panel"
                 style={{ padding: 14, marginBottom: 10 }}>
              <strong>{item.what}</strong>
              <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
                {item.why}
              </div>
              {item.recommended_action ? (
                <div style={{ fontSize: 13, marginTop: 6 }}>
                  <strong>What to do:</strong> {item.recommended_action}
                </div>
              ) : null}
            </div>
          ))}
        </>
      ) : null}

      {c ? (
        <>
          <h3>Right now</h3>
          <div className="panel" style={{ padding: 14, marginBottom: 12 }}>
            <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap',
                          fontSize: 13 }}>
              <span>Open work: {c.activity?.open_work_items ?? 0}</span>
              <span>Deployment: {c.deployment?.state || '—'}</span>
              <span>Entitlement: {c.deployment?.commercial_state || '—'}</span>
              <span>Readiness: {c.deployment?.readiness_state || '—'}</span>
              <span>Activation stage: {c.activation_state}</span>
            </div>
            {c.deployment?.note ? (
              <div style={{ fontSize: 12, color: 'var(--text-secondary)',
                            marginTop: 6 }}>{c.deployment.note}</div>
            ) : null}
          </div>

          <h3>Outcomes</h3>
          <div className="panel" style={{ padding: 0, overflowX: 'auto',
                                          marginBottom: 12 }}>
            <table className="table">
              <thead>
                <tr><th>Measure</th><th>This period</th><th>Previous</th>
                  <th>Against its own baseline</th></tr>
              </thead>
              <tbody>
                {['appointments', 'qualified', 'handoffs', 'responses',
                  'messages_sent', 'opt_outs'].map(key => {
                  const t = trends[key]
                  if (!t) return null
                  return (
                    <tr key={key}>
                      <td>{t.label}</td>
                      <td>{t.current}</td>
                      <td>{t.previous}</td>
                      <td style={{ fontSize: 12,
                                   color: 'var(--text-secondary)' }}>
                        {t.baseline_note
                          || (t.baseline_change_fraction === null
                            ? '—'
                            : `${Math.round(t.baseline_change_fraction * 100)}%`
                              + (t.off_baseline ? ' — worth a look' : ''))}
                      </td>
                    </tr>
                  )
                })}
                <tr>
                  <td>Revenue attributed</td>
                  <td colSpan={3} style={{ color: 'var(--text-secondary)',
                                           fontSize: 12 }}>
                    {outcomes.revenue?.note}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <h3>Effort</h3>
          <div className="panel" style={{ padding: 14, marginBottom: 12,
                                          fontSize: 13 }}>
            <div>Messages per outcome:{' '}
              {c.efficiency?.messages_per_outcome
                ?? <Unknown note={c.efficiency?.messages_per_outcome_note} />}
            </div>
            <div style={{ color: 'var(--text-secondary)', marginTop: 4 }}>
              {c.efficiency?.cost_per_outcome_note}
            </div>
          </div>

          <h3>Exceptions</h3>
          <div className="panel" style={{ padding: 14, marginBottom: 12,
                                          fontSize: 13 }}>
            <div>Refused by policy: {c.exceptions?.policy_denials ?? 0}</div>
            <div>Tool failures: {c.exceptions?.tool_failures ?? 0}</div>
            <div>Provider failures: {c.exceptions?.provider_failures ?? 0}</div>
            <div>Runs stopped by a limit: {c.exceptions?.runs_aborted ?? 0}</div>
          </div>
        </>
      ) : null}

      {quality ? (
        <>
          <h3>Quality</h3>
          <div className="panel" style={{ padding: 0, overflowX: 'auto' }}>
            <table className="table">
              <thead>
                <tr><th>What is graded</th><th>Result</th><th>How</th></tr>
              </thead>
              <tbody>
                {Object.values(quality.dimensions || {}).map(dim => (
                  <tr key={dim.key}>
                    <td>{dim.label}</td>
                    <td>
                      {dim.measured
                        ? `${dim.passed} of ${dim.total}`
                        : <Unknown note={dim.note} />}
                    </td>
                    <td style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                      {dim.calculation || dim.note}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="panel" style={{ padding: 14, marginTop: 10,
                                          fontSize: 12,
                                          color: 'var(--text-secondary)' }}>
            {quality.score?.explanation}
          </div>
        </>
      ) : null}

      {card?.benchmark?.published ? (
        <>
          <h3>Compared with the same job here</h3>
          <div className="panel" style={{ padding: 14, fontSize: 13 }}>
            {card.benchmark.scope_note}
          </div>
        </>
      ) : card?.benchmark ? (
        <div className="panel" style={{ padding: 14, marginTop: 12,
                                        fontSize: 12,
                                        color: 'var(--text-secondary)' }}>
          {card.benchmark.why}
        </div>
      ) : null}
    </PageShell>
  )
}
