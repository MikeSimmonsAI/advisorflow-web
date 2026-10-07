/**
 * Evidenced AI Workforce runs — current activity and history.
 *
 * Source: GET /workforce/runs (persisted `ai_employee_runs`, org-scoped
 * server-side). Nothing is derived from switched_on / assignment / eligibility.
 * Fields the server did not send are omitted, not filled in. Percent is shown
 * only when the server supplies `progress_percent` (it does not today).
 *
 * Liveness: heartbeat/stage/lease are shown only when the server recorded them.
 * Polls every 20 s while mounted and the tab is visible (never overlapping)
 * plus a manual "Check status now". "Live" is claimed only while the last
 * successful refresh is recent; otherwise the view says updates are stale.
 *
 * Refresh failure keeps the last-good rows and marks them possibly stale.
 * A response for a previous employee/scope is discarded.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import {
  POLL_INTERVAL_MS, applyRefresh, createRefreshGate, createSequencer,
  evidenceLabel, evidencedPercent, heartbeatAgeSeconds, lifecycleLabel,
  pollFreshness, redact, selectActiveRun, supportCode, workerDisplayState,
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
        {run.stale
          ? <span>{run.stale_reason || 'May be hung'}{run.liveness_source === 'started_at_legacy' ? ' — inferred from start time only' : ''}</span> : null}
        {pct != null ? <span>{pct}%</span> : null}
      </div>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', fontSize: 12,
        color: 'var(--text-secondary)', marginTop: 4 }}>
        <span>Run {run.run_id}</span>
        {run.started_at ? <span>Started {formatCT(run.started_at)}</span> : null}
        {mins != null ? <span>Elapsed {formatMinutes(mins)}</span> : null}
        {run.last_heartbeat_at
          ? <span>Last heartbeat {formatCT(run.last_heartbeat_at)} ({heartbeatAgeSeconds(run.last_heartbeat_at)} s ago)</span>
          : (active && !run.terminal ? <span>No heartbeat recorded</span> : null)}
        {run.stage ? <span>Stage: {redact(run.stage)}</span> : null}
        {run.lease_seconds ? <span>Lease {run.lease_seconds} s</span> : null}
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
  const gate = useRef(createRefreshGate()).current
  const [lastOkAt, setLastOkAt] = useState(null)
  const [tick, setTick] = useState(Date.now())
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  const load = useCallback(async () => {
    if (!gate.tryStart()) return          // never overlap refreshes
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
    if (seq.isCurrent(token)) gate.done()   // a superseded load must not free the newer one's gate
    if (!mounted.current || !seq.isCurrent(token) || scope.current !== startedFor) return
    if (outcome.ok) setLastOkAt(outcome.at)
    const next = applyRefresh(lastGood.current, outcome)
    lastGood.current = next
    setPayload(next.data); setStale(next.stale); setCode(outcome.code || null)
    setErr(next.error ? next.error
      + (next.stale ? ' Showing the last run activity loaded; it may be out of date.' : '') : '')
    setLoading(false)
  }, [employeeId, seq, gate])

  useEffect(() => {
    scope.current = employeeId
    lastGood.current = null
    setPayload(null); setStale(false); setErr(''); setLastOkAt(null)
    gate.done()                           // an employee switch discards any in-flight refresh
    load()
  }, [employeeId, load, gate])

  // Safe polling: only while mounted and the tab is visible; cleared on unmount.
  useEffect(() => {
    const id = setInterval(() => {
      setTick(Date.now())
      if (typeof document !== 'undefined' && document.hidden) return
      load()
    }, POLL_INTERVAL_MS)
    return () => clearInterval(id)
  }, [load])

  const { current, history } = pickRuns(payload)
  const fresh = pollFreshness(lastOkAt, tick)
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
            {' '}<span>{fresh.live ? `· live (checked ${fresh.ageSeconds ?? 0} s ago)` : '· updates stale — not confirmed live'}</span>
            {payload.as_of ? <span style={{ color: 'var(--text-secondary)' }}> · as of {formatCT(payload.as_of)}</span> : null}
          </div>
          {current ? <ul style={{ padding: 0 }}><Row run={current} active /></ul> : null}
          <button className="btn btn--ghost" onClick={load} disabled={loading} aria-busy={loading}>
            {loading ? 'Checking…' : 'Check status now'}
          </button>
          <span style={{ fontSize: 12, marginLeft: 8, color: 'var(--text-secondary)' }}>
            {lastOkAt ? `Last successful refresh ${formatCT(new Date(lastOkAt).toISOString())}` : 'No successful refresh yet'}
          </span>
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
