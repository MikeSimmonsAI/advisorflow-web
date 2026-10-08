/**
 * AI Workforce operator ledger — queue, active work, review-needed, recent
 * completion/failure, evidence and filters, from GET /workforce/ledger only.
 *
 * Read-only except one decision: a review (POST /workforce/queue/:id/review)
 * that carries the row's `expected_version`. Nothing here messages anyone,
 * calls a provider or deploys. A failed refresh keeps the last-good rows and
 * says so; a response for a superseded request is discarded.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api/client'
import { applyRefresh, createActionGuard, createSequencer, redact } from '../utils/workforceTruth'
import {
  LEDGER_STATES, canDecide, factText, filterItems, phaseRows, reviewOutcome,
  reviewPayload, sections, stateLabel,
} from '../utils/workforceLedger'

const DECISIONS = [
  ['return_to_queue', 'Return to queue'],
  ['close_not_interested', 'Close: not interested'],
  ['close_do_not_contact', 'Close: do not contact'],
  ['close_bad_contact', 'Close: bad contact'],
]

function Entry ({ e, openId, setOpenId, onDecide, busyKey }) {
  const open = openId === e.id
  const onToggle = () => setOpenId(open ? null : e.id)
  const ph = phaseRows(e)
  return (
    <li className="panel" style={{ padding: 12, marginBottom: 8, listStyle: 'none' }}>
      <button type="button" className="btn btn--secondary btn--sm" onClick={onToggle}
              aria-expanded={open} style={{ width: '100%', textAlign: 'left' }}>
        <strong>{stateLabel(e.state)}</strong>
        {' · '}{e.kind === 'run' ? 'Run' : 'Work'} {e.id}
        {e.worker && e.worker.name ? ` · ${redact(e.worker.name)}` : ''}
      </button>
      {e.blocker ? <div style={{ fontSize: 13, marginTop: 6 }}>Reason: {redact(e.blocker.reason)}</div> : null}
      {open && (
        <div style={{ fontSize: 13, marginTop: 8, display: 'grid', gap: 4 }}>
          {e.goal ? <div>Goal: {redact(e.goal)}</div> : null}
          <div>Capability: {redact((e.worker && e.worker.capability) || '') || 'Unavailable'}</div>
          <div>Source: {e.source_ref && e.source_ref.id ? `${e.source_ref.type} ${e.source_ref.id}` : 'Unavailable'}</div>
          <div>Attempts: {typeof e.attempts === 'number' ? e.attempts : 'Unavailable'}</div>
          <div>Checkpoint: {e.last_checkpoint && e.last_checkpoint.status === 'available'
            ? redact(e.last_checkpoint.summary) : 'Unavailable'}</div>
          <div>Commit: {factText(e.commit)} · Tests: {factText(e.tests)} · Deploy: {factText(e.deploy)}</div>
          <div>{ph.rows.map(r => `${r.label}: ${r.text}`).join(' · ')}</div>
          <div>{ph.done ? 'All delivery phases evidenced' : 'Not done: delivery phases are not all evidenced'}</div>
          {canDecide(e) ? (
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 4 }}>
              {DECISIONS.map(([key, label]) => (
                <button key={key} type="button" className="btn btn--primary btn--sm"
                        disabled={busyKey === e.id} onClick={() => onDecide(e, key)}>
                  {label}
                </button>
              ))}
            </div>
          ) : (e.decisions && e.decisions.review && e.decisions.review.blocker
            ? <div>Decision unavailable: {e.decisions.review.blocker}</div> : null)}
        </div>
      )}
    </li>
  )
}

function Group ({ title, items, ...rest }) {
  return (
    <section style={{ marginTop: 12 }}>
      <h3 style={{ margin: '0 0 6px' }}>{title} ({items.length})</h3>
      {items.length === 0 ? <p style={{ fontSize: 13 }}>Nothing here.</p>
        : <ul style={{ padding: 0, margin: 0 }}>
          {items.map(e => <Entry key={`${e.kind}:${e.id}`} e={e} {...rest} />)}
        </ul>}
    </section>
  )
}

export default function WorkforceLedger () {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [err, setErr] = useState('')
  const [stale, setStale] = useState(false)
  const [filters, setFilters] = useState({ state: '', kind: '' })
  const [openId, setOpenId] = useState(null)
  const [busyKey, setBusyKey] = useState(null)
  const [notice, setNotice] = useState('')
  const seq = useRef(createSequencer()).current
  const guard = useRef(createActionGuard()).current
  const lastGood = useRef(null)
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])

  const load = useCallback(async () => {
    const token = seq.begin()
    setLoading(true)
    let outcome
    try {
      outcome = { ok: true, data: await api.get('/workforce/ledger', { params: { limit: 200 } }), at: Date.now() }
    } catch (e) {
      outcome = { ok: false, message: e?.message || 'Could not load the work ledger.' }
    }
    if (!mounted.current || !seq.isCurrent(token)) return
    const next = applyRefresh(lastGood.current, outcome)
    lastGood.current = next
    setData(next.data); setStale(next.stale); setErr(next.error); setLoading(false)
  }, [seq])
  useEffect(() => { load() }, [load])

  async function decide (entry, decision) {
    const body = reviewPayload(entry, decision)
    if (!body || !guard.tryAcquire(entry.id)) return
    setBusyKey(entry.id); setNotice('')
    try {
      const out = reviewOutcome(await api.post(`/workforce/queue/${entry.id}/review`, body))
      if (!out.ok) setNotice('The server did not confirm that decision.')
      else setNotice(out.replayed ? 'Already decided; nothing changed.' : 'Decision recorded.')
    } catch (e) {
      setNotice(e?.message || 'The decision was not recorded.')
    } finally {
      guard.release(entry.id)
      if (mounted.current) { setBusyKey(null); load() }
    }
  }

  const items = useMemo(() => (data && data.items) || [], [data])
  const shown = useMemo(() => filterItems(items, filters), [items, filters])
  const sec = useMemo(() => sections(shown), [shown])
  const common = { busyKey, onDecide: decide, openId, setOpenId }

  return (
    <div className="panel" style={{ padding: 16, marginTop: 18 }}>
      <h2 className="panel-title">Work ledger</h2>
      {loading && !data ? <p>Loading work ledger…</p> : null}
      {err ? <p role="alert">{err}{stale ? ' Showing the last information loaded; it may be out of date.' : ''}</p> : null}
      {data && data.partial ? <p role="status">Partial: could not read {data.source_errors.join(', ')}.</p> : null}
      {data && data.truncated ? <p role="status">Showing the newest entries only.</p> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {data ? (
        <>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <select aria-label="State" value={filters.state}
                    onChange={ev => setFilters(f => ({ ...f, state: ev.target.value }))}>
              <option value="">All states</option>
              {LEDGER_STATES.map(s => <option key={s} value={s}>{stateLabel(s)} ({data.counts[s] || 0})</option>)}
            </select>
            <select aria-label="Kind" value={filters.kind}
                    onChange={ev => setFilters(f => ({ ...f, kind: ev.target.value }))}>
              <option value="">Work and runs</option>
              <option value="work_item">Work items</option>
              <option value="run">Runs</option>
            </select>
            <button type="button" className="btn btn--secondary btn--sm" onClick={load} disabled={loading}>Refresh</button>
          </div>
          {items.length === 0 && !data.partial ? <p>No work has been assigned yet.</p> : null}
          {['active:Active work', 'review:Review needed', 'recent:Recently finished'].map(def => {
            const [k, title] = def.split(':')
            return (
              <Group key={k} title={title} items={sec[k]} {...common} />
            )
          })}
        </>
      ) : null}
    </div>
  )
}
