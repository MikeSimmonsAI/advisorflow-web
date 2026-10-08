/**
 * GodRelayControlRoom — live, evidence-only view of the automatic agent relay.
 *
 * Used as the first screen of the Control Room (/god/control-room): CURRENTLY
 * WORKING, UP NEXT, NEEDS MIKE / BLOCKERS, then the project launch board.
 *
 * Surfaces GET /god/relay/worker (read-only). No percent complete and no ETA:
 * only facts the relay has actually reported. A failed fetch clears the active
 * state; the page never keeps showing a stale "Working", and a failed check is
 * never reported as a successful one.
 */
import { useState, useEffect, useCallback, useMemo } from 'react'
import { api } from '../../api/client'
import GodLaunchBoard from './GodLaunchBoard'
import {
  POLL_MS, failedState, headline, isWorking, liveElapsedMinutes, createRefresher, isLapsed,
  formatCT, formatMinutes, leaseText, actionsRunText, recommendation, isCheckStale,
  runOutcomesToday, lastFinished, upNextSplit, currentBlockers, olderBlockerCount, workerView,
} from '../../utils/relayControlRoom'

const card = { background: 'var(--gm-card-bg, var(--god-card, #fff))', border: '1px solid var(--gm-card-line, #e5e7eb)',
  borderRadius: 10, padding: 16, marginBottom: 16 }
const label = { color: 'var(--gm-dim, #6b7280)', fontSize: 12 }
const head = { fontSize: 12, letterSpacing: '.06em', textTransform: 'uppercase', fontWeight: 700,
  color: 'var(--gm-dim, #6b7280)', marginBottom: 8 }
const grid = { display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 16 }

function Row({ k, v }) {
  return (
    <div style={{ display: 'flex', gap: 12, padding: '3px 0' }}>
      <div style={{ ...label, width: 120, flex: '0 0 120px' }}>{k}</div>
      <div style={{ wordBreak: 'break-word' }}>{v === '' || v == null ? 'unavailable' : v}</div>
    </div>
  )
}

function Worker({ worker, generatedAt, tick, last }) {
  const elapsed = liveElapsedMinutes(worker, generatedAt, tick)
  // Between polls the lease can lapse: flag it from the live clock, never keep saying Working.
  const lapsed = isLapsed(worker, tick)
  const bad = lapsed || worker.health === 'STALE/HUNG' || worker.health === 'STALE' || worker.state === 'mismatch'
  const idle = worker.state === 'idle' || worker.state === 'unavailable'
  return (
    <div style={{ ...card, marginBottom: 0 }} data-testid="relay-worker">
      <div style={head}>Currently working</div>
      <div style={{ fontSize: 20, fontWeight: 700, color: bad ? 'var(--gm-red, #b91c1c)' : idle ? 'var(--gm-dim, #4b5563)' : 'var(--gm-teal, #047857)' }}>
        {lapsed ? 'STALE/HUNG' : worker.state === 'idle' ? 'IDLE' : worker.display}
      </div>
      {worker.state === 'idle' ? <div style={label}>{worker.display}</div> : null}
      {worker.queuedHead ? (
        <div style={{ ...label, marginTop: 4 }}>
          Next directive waiting to be accepted: {worker.queuedHead.relay_run_id}
          {worker.queuedHead.health && worker.queuedHead.health !== 'ok' ? ` (${worker.queuedHead.health_detail || worker.queuedHead.health})` : ''}
        </div>
      ) : null}
      {idle ? null : (
        <div style={{ marginTop: 8 }}>
          <Row k="Project" v={worker.project} />
          <Row k="Stage" v={worker.stage} />
          <Row k="Branch" v={worker.branch} />
          <Row k="Elapsed" v={formatMinutes(elapsed)} />
          <Row k="Lease" v={leaseText({ ...worker, elapsed_min: elapsed })} />
          <Row k="Last update" v={formatCT(worker.last_update_at)} />
          {worker.health_detail || (worker.health && worker.health !== 'ok')
            ? <Row k="Health" v={worker.health_detail || worker.health} /> : null}
          <details style={{ marginTop: 6 }}>
            <summary style={label}>Technical details</summary>
            <Row k="Actor" v={worker.actor} />
            <Row k="Relay run ID" v={worker.relay_run_id} />
            <Row k="Actions run ID" v={actionsRunText(worker)} />
            <Row k="Started" v={formatCT(worker.started_at)} />
            <Row k="Checkpoint" v={worker.checkpoint_sha
              ? `${worker.checkpoint_sha}${worker.checkpoint_age_min != null ? ` (${formatMinutes(worker.checkpoint_age_min)} ago)` : ''}`
              : 'none yet'} />
          </details>
        </div>
      )}
      <div style={{ ...label, marginTop: 10, borderTop: '1px solid var(--gm-card-line, #e5e7eb)', paddingTop: 8 }}
           data-testid="relay-last-finished">
        Last finished run: {last
          ? `${last.result || last.display} · ${last.project || 'no project'} · ${formatCT(last.last_update_at)}`
          : 'none reported'}
      </div>
    </div>
  )
}

export default function GodRelayControlRoom({ onManualRefresh }) {
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
  const load = useCallback(() => {
    refresh(true)
    if (onManualRefresh) onManualRefresh()
  }, [refresh, onManualRefresh])

  useEffect(() => {
    refresh(false)
    const poll = setInterval(() => refresh(false), POLL_MS)
    const clock = setInterval(() => setTick(Date.now()), 15000)
    return () => { clearInterval(poll); clearInterval(clock) }
  }, [refresh])

  const s = state || failedState('Loading…')
  const { fresh: next, stale: neverStarted } = upNextSplit(s)  // fresh queued directives only; never-accepted ones listed apart
  const blocks = currentBlockers(s, tick)
  const olderBlocks = olderBlockerCount(s, tick)
  const outcomes = runOutcomesToday(s, tick)
  return (
    <div data-testid="relay-control-room">
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginBottom: 8 }}>
        <button type="button" className="btn status" onClick={load} aria-busy={loading} data-testid="check-status-now">
          {loading ? 'Checking…' : 'Check status now'}
        </button>
        <span style={label} data-testid="relay-check-line">
          {isWorking(s) ? 'Worker active' : headline({ ...s, worker: workerView(s) })} · refreshes every {POLL_MS / 1000}s
          {lastOk ? ` · last successful check ${formatCT(new Date(lastOk).toISOString())}` : ' · no successful check yet'}
          {lastFailed ? ` · last attempt FAILED ${formatCT(new Date(lastFailed).toISOString())}` : ''}
        </span>
      </div>
      {lastOk && isCheckStale(lastOk, tick) ? (
        <div style={{ ...card, color: 'var(--gm-red, #b91c1c)' }} role="alert" data-testid="relay-stale">
          Status is stale — the last successful check was more than {Math.round(POLL_MS * 3 / 1000)}s ago.
        </div>
      ) : null}

      {!s.available ? (
        <div style={{ ...card, color: 'var(--gm-red, #b91c1c)' }} role="alert" data-testid="relay-unavailable">
          Relay status unavailable — {s.reason}. No worker state is being shown.
        </div>
      ) : null}

      <div style={{ ...grid, marginBottom: 16 }}>
        <Worker worker={workerView(s)} generatedAt={s.generated_at} tick={tick} last={lastFinished(s)} />

        <div style={{ ...card, marginBottom: 0 }} data-testid="relay-up-next">
          <div style={head}>UP NEXT <span style={{ ...label, textTransform: 'none', letterSpacing: 0, fontWeight: 400 }}>(approved relay directives only)</span></div>
          {next.length === 0 ? <div style={{ fontWeight: 600 }}>Nothing queued.</div> : null}
          {next.map(u => (
            <div key={u.relay_run_id} style={{ padding: '6px 0', borderTop: '1px solid var(--gm-card-line, #e5e7eb)' }}>
              <div><b>{u.project || 'No project named'}</b></div>
              <div style={label}>
                {u.status}{u.stale ? ' (STALE)' : ''} · owner {u.owner} · last update {formatCT(u.last_update_at)} · {u.start}
              </div>
              <div style={label}>{u.relay_run_id}</div>
            </div>
          ))}
          {neverStarted.length ? (
            <details style={{ marginTop: 8 }} data-testid="relay-never-started">
              <summary style={label}>{neverStarted.length} older directive{neverStarted.length === 1 ? '' : 's'} never accepted (past the lease) — not scheduled to run</summary>
              {neverStarted.map(u => (
                <div key={u.relay_run_id} style={{ ...label, paddingTop: 4 }}>{u.project || 'No project named'} · {u.relay_run_id} · last update {formatCT(u.last_update_at)}</div>
              ))}
            </details>
          ) : null}
          {(s.queued_behind || []).filter(q => q.state !== 'queued').map(q => (
            <div key={q.relay_run_id} style={{ ...label, paddingTop: 6 }}>Also open: {q.display} — {q.project} ({q.relay_run_id})</div>
          ))}
        </div>

        <div style={{ ...card, marginBottom: 0 }} data-testid="relay-blockers">
          <div style={head}>Needs Mike / blockers</div>
          {blocks.length === 0 ? <div style={{ fontWeight: 600 }}>Nothing blocked in the last 24 hours.</div> : null}
          {blocks.map(b => (
            <div key={`${b.relay_run_id}-${b.kind}`} style={{ padding: '6px 0', borderTop: '1px solid var(--gm-card-line, #e5e7eb)' }}>
              <div style={{ fontWeight: 700, color: b.kind === 'APPROVAL_REQUIRED' ? 'var(--gm-red, #b91c1c)' : undefined }}>
                {b.kind === 'APPROVAL_REQUIRED' ? 'NEEDS MIKE' : b.kind}
              </div>
              <div>{b.reason}</div>
              <div style={label}>{b.relay_run_id || ''}</div>
            </div>
          ))}
          {olderBlocks > 0 ? <div style={{ ...label, marginTop: 8 }}>{olderBlocks} older blocked run{olderBlocks === 1 ? '' : 's'} — see Technical log</div> : null}
        </div>
      </div>

      <div style={{ ...card, display: 'flex', gap: 24, flexWrap: 'wrap' }} data-testid="relay-outcomes">
        <div><div style={label}>Runs reported complete today</div><div style={{ fontWeight: 700 }}>{outcomes.completed}</div>
          <div style={label}>source/tests reported — not a release</div></div>
        <div><div style={label}>Blocked runs today</div><div style={{ fontWeight: 700 }}>{outcomes.blocked}</div></div>
        <div><div style={label}>Stopped for approval today</div><div style={{ fontWeight: 700 }}>{outcomes.approval}</div></div>
        <div><div style={label}>Verified releases</div><div style={{ fontWeight: 700 }}>see launch gates</div>
          <div style={label}>only gates with verified evidence count</div></div>
      </div>

      {recommendation(s) ? (
        <div style={card} data-testid="relay-recommendation">
          <b>Recommendation</b> <span style={label}>(advice from the last run — not queued work)</span>
          <div>{recommendation(s)}</div>
        </div>
      ) : null}

      <GodLaunchBoard relayState={s} working={isWorking(s) && !isLapsed(s.worker, tick)} />

      <details style={card} data-testid="relay-technical-log">
        <summary><b>Technical log</b> <span style={label}>({(s.history || []).length} finished or retired runs — expand for detail)</span></summary>
        {(s.history || []).length === 0 ? <div style={label}>No history.</div> : null}
        {(s.history || []).map(h => (
          <div key={h.relay_run_id} style={{ padding: '6px 0', borderTop: '1px solid var(--gm-card-line, #e5e7eb)' }}>
            <div><b style={{ color: h.state === 'terminal' && h.result === 'COMPLETED' ? 'var(--gm-teal, #047857)'
              : h.state === 'terminal' || h.state === 'mismatch' ? 'var(--gm-red, #b91c1c)' : 'var(--gm-dim, #6b7280)' }}>{h.display}</b> · {h.project} · {h.branch} · <span style={label}>{h.relay_run_id}</span></div>
            <div style={label}>
              {formatCT(h.last_update_at)} · took {formatMinutes(h.elapsed_min)}
              {h.result ? ` · result ${h.result}` : ''}
              {h.checkpoint_sha ? ` · ${h.checkpoint_sha}` : ''}
              {h.superseded_by ? ` · superseded by ${h.superseded_by}` : ''}
            </div>
            {h.blocked_reason || h.health ? <div>{h.blocked_reason || h.health}</div> : null}
          </div>
        ))}
      </details>
    </div>
  )
}
