/**
 * GodRelayControlRoom — live, evidence-only view of the automatic agent relay.
 *
 * Surfaces GET /god/relay/state (read-only). No percent complete and no ETA:
 * only facts the relay has actually reported. A failed fetch clears the active
 * state; the page never keeps showing a stale "Working".
 */
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api } from '../../api/client'
import {
  POLL_MS, failedState, headline, isWorking, liveElapsedMinutes, createRefresher, isLapsed,
  formatCT, formatMinutes, leaseText, actionsRunText, upNext, blockers, recommendation, isCheckStale,
} from '../../utils/relayControlRoom'

const card = { background: 'var(--gm-card-bg, var(--gm-pill-blue-bg))', border: '1px solid var(--gm-card-line)',
  borderRadius: 10, padding: 16, marginBottom: 16 }
const label = { color: 'var(--gm-dim)', fontSize: 12 }

function Row({ k, v }) {
  return (
    <div style={{ display: 'flex', gap: 12, padding: '3px 0' }}>
      <div style={{ ...label, width: 170 }}>{k}</div>
      <div style={{ wordBreak: 'break-word' }}>{v === '' || v == null ? 'unavailable' : v}</div>
    </div>
  )
}

function Worker({ worker, generatedAt, tick }) {
  const elapsed = liveElapsedMinutes(worker, generatedAt, tick)
  // Between polls the lease can lapse: flag it from the live clock, never keep saying Working.
  const lapsed = isLapsed(worker, tick)
  const bad = lapsed || worker.health === 'STALE/HUNG' || worker.health === 'STALE' || worker.state === 'mismatch'
  return (
    <div style={card} data-testid="relay-worker">
      <div style={{ fontSize: 20, fontWeight: 600, color: bad ? 'var(--gm-red)' : 'var(--gm-teal)' }}>
        {lapsed ? 'STALE/HUNG' : worker.display}
      </div>
      {worker.state === 'idle' || worker.state === 'unavailable' ? null : (
        <div style={{ marginTop: 8 }}>
          <Row k="Project" v={worker.project} />
          <Row k="Actor" v={worker.actor} />
          <Row k="Branch" v={worker.branch} />
          <Row k="Relay run ID" v={worker.relay_run_id} />
          <Row k="Actions run ID" v={actionsRunText(worker)} />
          <Row k="Stage" v={worker.stage} />
          <Row k="Started" v={formatCT(worker.started_at)} />
          <Row k="Elapsed" v={formatMinutes(elapsed)} />
          <Row k="Lease" v={leaseText({ ...worker, elapsed_min: elapsed })} />
          <Row k="Last update" v={formatCT(worker.last_update_at)} />
          <Row k="Checkpoint" v={worker.checkpoint_sha
            ? `${worker.checkpoint_sha}${worker.checkpoint_age_min != null ? ` (${formatMinutes(worker.checkpoint_age_min)} ago)` : ''}`
            : 'none yet'} />
          {worker.health_detail || (worker.health && worker.health !== 'ok')
            ? <Row k="Health" v={worker.health_detail || worker.health} /> : null}
        </div>
      )}
    </div>
  )
}

export default function GodRelayControlRoom() {
  const [state, setState] = useState(null)
  const [loading, setLoading] = useState(false)
  const [tick, setTick] = useState(Date.now())

  const [lastOk, setLastOk] = useState(null)        // last SUCCESSFUL check
  const [lastFailed, setLastFailed] = useState(null)  // last failed attempt
  const refresh = useMemo(() => createRefresher({
    fetchState: (url, opts) => api.get(url, opts),
    onLoading: setLoading,
    onState: (s) => { setState(s); setLastOk(Date.now()); setLastFailed(null); setTick(Date.now()) },
    onError: (s) => { setState(s); setLastFailed(Date.now()); setTick(Date.now()) },  // clears any stale active worker
  }), [])
  const load = useCallback(() => refresh(true), [refresh])

  useEffect(() => {
    refresh(false)
    const poll = setInterval(() => refresh(false), POLL_MS)
    const clock = setInterval(() => setTick(Date.now()), 15000)
    return () => { clearInterval(poll); clearInterval(clock) }
  }, [refresh])

  const s = state || failedState('Loading…')
  return (
    <div style={{ maxWidth: 900 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h2 style={{ margin: 0 }}>Relay Control Room</h2>
        <button type="button" onClick={load} aria-busy={loading}>
          {loading ? 'Checking…' : 'Check status now'}
        </button>
      </div>
      <div style={label}>
        {isWorking(s) ? 'Worker active' : headline(s)} · refreshes every {POLL_MS / 1000}s
        {lastOk ? ` · last successful check ${formatCT(new Date(lastOk).toISOString())}` : ' · no successful check yet'}
        {lastFailed ? ` · last attempt FAILED ${formatCT(new Date(lastFailed).toISOString())}` : ''}
      </div>
      {lastOk && isCheckStale(lastOk, tick) ? (
        <div style={{ ...card, color: 'var(--gm-red)' }} role="alert">
          Status is stale — the last successful check was more than {Math.round(POLL_MS * 3 / 1000)}s ago.
        </div>
      ) : null}

      {!s.available ? (
        <div style={{ ...card, color: 'var(--gm-red)' }} role="alert">
          Relay status unavailable — {s.reason}. No worker state is being shown.
        </div>
      ) : null}

      <Worker worker={s.worker} generatedAt={s.generated_at} tick={tick} />

      {(s.queued_behind || []).length ? (
        <div style={card}>
          <b>Queued behind</b>
          {s.queued_behind.map(q => <Row key={q.relay_run_id} k={q.relay_run_id} v={`${q.display} — ${q.project}`} />)}
        </div>
      ) : null}

      {blockers(s).length ? (
        <div style={card} data-testid="relay-blockers">
          <b>Blockers</b>
          {blockers(s).map(b => <Row key={`${b.relay_run_id}-${b.kind}`} k={`${b.kind} · ${b.relay_run_id || ''}`} v={b.reason} />)}
        </div>
      ) : null}

      <div style={card} data-testid="relay-up-next">
        <b>UP NEXT</b> <span style={label}>(queued relay directives only)</span>
        {upNext(s).length === 0 ? <div style={label}>Nothing queued.</div> : null}
        {upNext(s).map(u => (
          <div key={u.relay_run_id} style={{ padding: '6px 0', borderTop: '1px solid var(--gm-card-line)' }}>
            <div><b>{u.relay_run_id}</b> · {u.project}</div>
            <div style={label}>
              {u.status}{u.stale ? ' (STALE)' : ''} · owner {u.owner} · last update {formatCT(u.last_update_at)} · {u.start}
            </div>
          </div>
        ))}
      </div>

      {recommendation(s) ? (
        <div style={card} data-testid="relay-recommendation">
          <b>Recommendation</b> <span style={label}>(advice from the last run — not queued work)</span>
          <div>{recommendation(s)}</div>
        </div>
      ) : null}

      <div style={card}>
        <b>History</b> <span style={label}>({s.completed_today} completed today)</span>
        {(s.history || []).length === 0 ? <div style={label}>No history.</div> : null}
        {(s.history || []).map(h => (
          <div key={h.relay_run_id} style={{ padding: '6px 0', borderTop: '1px solid var(--gm-card-line)' }}>
            <div><b style={{ color: h.state === 'terminal' && h.result === 'COMPLETED' ? 'var(--gm-teal)'
              : h.state === 'terminal' || h.state === 'mismatch' ? 'var(--gm-red)' : 'var(--gm-dim)' }}>{h.display}</b> · {h.project} · {h.branch} · <span style={label}>{h.relay_run_id}</span></div>
            <div style={label}>
              {formatCT(h.last_update_at)} · took {formatMinutes(h.elapsed_min)}
              {h.result ? ` · result ${h.result}` : ''}
              {h.checkpoint_sha ? ` · ${h.checkpoint_sha}` : ''}
              {h.superseded_by ? ` · superseded by ${h.superseded_by}` : ''}
            </div>
            {h.blocked_reason || h.health ? <div>{h.blocked_reason || h.health}</div> : null}
          </div>
        ))}
      </div>
    </div>
  )
}
