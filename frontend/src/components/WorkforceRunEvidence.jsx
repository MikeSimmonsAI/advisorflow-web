/**
 * Evidenced AI Workforce runs — current activity and history.
 *
 * Source: GET /workforce/runs (persisted `ai_employee_runs`, org-scoped
 * server-side). Nothing is derived from switched_on / assignment / eligibility.
 * Fields the server did not send are omitted, not filled in. Percent is shown
 * only when the server supplies `progress_percent` (it does not today).
 *
 * Refresh failure keeps the last-good rows and marks them possibly stale.
 * A response for a previous employee/scope is discarded.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import {
  applyRefresh, createSequencer, evidenceLabel, evidencedPercent,
  lifecycleLabel, redact, selectActiveRun, supportCode, workerDisplayState,
} from '../utils/workforceTruth'
import { formatCT, formatMinutes } from '../utils/relayControlRoom'

/** Defensive client-side selection: server `stale`/`superseded_by` win. */
export function pickRuns (payload) {
  const all = [payload?.current, ...(payload?.history || [])].filter(Boolean)
    .map(r => ({ ...r, id: r.run_id, state: r.stale ? 'stale' : r.state,
      updated_at: r.updated_at, started_at: r.started_at }))
  return selectActiveRun(all)
}

export function elapsedMinutes (run, nowMs = Date.now()) {
  const t = Date.parse(run?.started_at || '')
  return Number.isNaN(t) ? null : Math.max(0, Math.floor((nowMs - t) / 60000))
}

function Row ({ run, active }) {
  const pct = evidencedPercent(run.progress_percent)
  const mins = active ? elapsedMinutes(run) : null
  return (
    <li className="panel" style={{ padding: 12, marginBottom: 8, listStyle: 'none' }}>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 13 }}>
        <strong>{lifecycleLabel(run.state)}</strong>
        {run.task_label ? <span>Task: {run.task_label}</span> : null}
        {run.simulated ? <span>Simulation</span> : null}
        {run.superseded_by ? <span>Superseded by {run.superseded_by}</span> : null}
        {run.stale && run.stale_after_seconds
          ? <span>No end recorded after {Math.round(run.stale_after_seconds / 60)} min — may be hung</span> : null}
        {pct != null ? <span>{pct}%</span> : null}
      </div>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 12,
        color: 'var(--text-secondary)', marginTop: 4 }}>
        <span>Run {run.run_id}</span>
        {run.started_at ? <span>Started {formatCT(run.started_at)}</span> : null}
        {mins != null ? <span>Elapsed {formatMinutes(mins)}</span> : null}
        {run.updated_at ? <span>Last update {formatCT(run.updated_at)}</span> : null}
        {run.evidence?.kind ? <span>Evidence: {evidenceLabel(run.evidence.kind)}</span> : null}
        {run.trigger ? <span>Source: {run.trigger}</span> : null}
      </div>
      {run.checkpoint_summary
        ? <div style={{ fontSize: 13, marginTop: 6 }}>Latest checkpoint: {redact(run.checkpoint_summary)}</div> : null}
      {run.blocked_reason
        ? <div style={{ fontSize: 13, marginTop: 6 }}>Blocked: {redact(run.blocked_reason)}</div> : null}
      {run.failure_summary
        ? <div style={{ fontSize: 13, marginTop: 6 }}>Failure: {redact(run.failure_summary)}</div> : null}
    </li>
  )
}

export default function WorkforceRunEvidence ({ employeeId = null, title = 'Run activity' }) {
  const [payload, setPayload] = useState(null)
  const [err, setErr] = useState('')
  const [code, setCode] = useState(null)
  const [stale, setStale] = useState(false)
  const [loading, setLoading] = useState(true)
  const seq = useRef(createSequencer()).current
  const scope = useRef(employeeId)
  const lastGood = useRef(null)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  const load = useCallback(async () => {
    const token = seq.begin()
    const startedFor = employeeId
    setLoading(true)
    let outcome
    try {
      const url = employeeId
        ? `/workforce/employees/${encodeURIComponent(employeeId)}/runs`
        : '/workforce/runs'
      outcome = { ok: true, data: await api.get(url), at: Date.now() }
    } catch (e) {
      outcome = { ok: false, code: supportCode(e), message: e?.message || 'Could not load run activity.' }
    }
    if (!mounted.current || !seq.isCurrent(token) || scope.current !== startedFor) return
    const next = applyRefresh(lastGood.current, outcome)
    lastGood.current = next
    setPayload(next.data); setStale(next.stale); setCode(outcome.code || null)
    setErr(next.error ? next.error
      + (next.stale ? ' Showing the last run activity loaded; it may be out of date.' : '') : '')
    setLoading(false)
  }, [employeeId, seq])

  useEffect(() => {
    scope.current = employeeId
    lastGood.current = null
    setPayload(null); setStale(false); setErr('')
    load()
  }, [employeeId, load])

  const { current, history } = pickRuns(payload)
  const display = workerDisplayState({ state: null }, current)

  return (
    <section aria-label={title}>
      <h3>{title}</h3>
      {err ? (
        <div className="panel panel--error" role="alert">
          {err}{code ? ` (support code ${code})` : ''}{' '}
          <button className="btn btn--ghost" onClick={load} disabled={loading}>Retry</button>
        </div>
      ) : null}
      {loading && !payload ? <div className="panel">Loading…</div> : null}
      {payload ? (
        <>
          <div style={{ fontSize: 13, marginBottom: 8 }} aria-live="polite">
            {current
              ? <>Current run: <strong>{lifecycleLabel(display)}</strong>{stale ? ' (may be out of date)' : ''}</>
              : 'No run is recorded as active.'}
            {payload.as_of ? <span style={{ color: 'var(--text-secondary)' }}> · as of {formatCT(payload.as_of)}</span> : null}
          </div>
          {current ? <ul style={{ padding: 0 }}><Row run={current} active /></ul> : null}
          <h4>History</h4>
          {history.length
            ? <ul style={{ padding: 0 }}>{history.map(r => <Row key={r.run_id} run={r} />)}</ul>
            : <div className="panel" style={{ fontSize: 13 }}>No runs recorded yet.</div>}
          {payload.truncated ? <div style={{ fontSize: 12 }}>Showing the most recent runs only.</div> : null}
        </>
      ) : null}
    </section>
  )
}
