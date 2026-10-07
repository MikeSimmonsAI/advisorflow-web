/* Callback Center — Due Now / Upcoming / Overdue / Completed.
 *
 * Every count and row comes from GET /wholesale/ops/callbacks. Missed callbacks
 * stay in Overdue until a person completes or cancels them. Seller requests the
 * system detected (a reply asking to be called) appear here too, marked
 * "Seller asked", until someone works them. Nothing on this page places a call.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import '../../../styles/shared.css'
import '../wholesale.css'
import { Alert, EvoApp, Hero, Panel, Tag } from '../ds/ds'
import '../ds/evo-pages.css'
import { errText, fmtWhen } from '../wsShared'
import { describeError } from '../wsActionState'
import {
  assigneeLabel, bucketCount, initialQueue, itemLabel, loadFailed, loadStarted,
  loadSucceeded, queueView, sortQueue, truncationNote,
} from '../wsQueueState'
import './ops.css'

const BUCKETS = [
  ['due_now', 'Due now'],
  ['upcoming', 'Upcoming'],
  ['overdue', 'Overdue'],
  ['completed', 'Completed'],
]

function CallbackItem({ it, onDone }) {
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const inFlight = useRef(false)   // synchronous: two clicks in one tick see it
  const exceptionOnly = String(it.id).startsWith('exc:')
  async function finish(kind) {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true); setErr('')
    try { await api.post(`/wholesale/ops/callbacks/${encodeURIComponent(it.id)}/${kind}`, { note }); onDone() }
    catch (e) { setErr(describeError(errText(e))) }   // note stays so it can be retried
    finally { inFlight.current = false; setBusy(false) }
  }
  async function adopt() {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true); setErr('')
    try { await api.post(`/wholesale/ops/callbacks/from-exception/${encodeURIComponent(it.exception_id)}`, {}); onDone() }
    catch (e) { setErr(describeError(errText(e))) }
    finally { inFlight.current = false; setBusy(false) }
  }
  const open = it.status === 'due' && !exceptionOnly
  return (
    <li className="wso-item">
      <div className="wso-row">
        <span className="wso-item__title">{it.seller_name || 'Seller'}</span>
        {it.kind === 'requested' ? <Tag kind="info">Seller asked</Tag> : null}
        {it.dnc ? <Tag kind="danger">Do Not Contact</Tag> : null}
        {it.is_test ? <Tag kind="sandbox">Test</Tag> : null}
        <Tag kind={it.bucket === 'overdue' ? 'danger' : undefined}>{itemLabel(it)}</Tag>
        {it.deal_id ? <Link className="wso-small" to={`/wholesale/deals/${it.deal_id}`}>Open deal</Link> : null}
      </div>
      <div className="wso-muted">
        {it.status === 'completed' ? `completed ${fmtWhen(it.completed_at) || 'time not recorded'}` : `due ${fmtWhen(it.due_at) || 'time not set'}`}
        {it.phone ? ` · ${it.phone}` : ''}
        {` · ${assigneeLabel(it)}`}
      </div>
      {it.notes ? <p className="wso-item__body">{it.notes}</p> : null}
      {it.outcome_note ? <p className="wso-item__body"><strong>Outcome:</strong> {it.outcome_note}</p> : null}
      {exceptionOnly && it.exception_id ? (
        <div className="wso-row" style={{ marginTop: 8 }}>
          <button type="button" className="btn btn--primary" disabled={busy} aria-busy={busy} onClick={adopt}>
            Start a callback
          </button>
        </div>
      ) : null}
      {open ? (
        <>
          <input className="wso-input" style={{ marginTop: 8 }} placeholder="What happened on the call? (optional)"
                 value={note} onChange={e => setNote(e.target.value)} aria-label="Outcome note" />
          <div className="wso-row" style={{ marginTop: 8 }}>
            <button type="button" className="btn btn--primary" disabled={busy} aria-busy={busy} onClick={() => finish('complete')}>Mark completed</button>
            <button type="button" className="btn btn--secondary" disabled={busy} aria-busy={busy} onClick={() => finish('cancel')}>Cancel callback</button>
          </div>
        </>
      ) : null}
      {err ? <div role="alert"><Alert kind="warn">{err} Your note is kept — try again.</Alert></div> : null}
    </li>
  )
}

export default function CallbackCenter() {
  const [bucket, setBucket] = useState('due_now')
  const [mine, setMine] = useState(false)
  const [q, setQ] = useState(initialQueue)
  const latest = useRef(0)   // mirrors q.latest so a response can be checked synchronously

  const load = useCallback(async () => {
    const gen = latest.current + 1
    latest.current = gen
    setQ(prev => loadStarted({ ...prev, latest: gen - 1 }))
    try {
      const d = await api.get(`/wholesale/ops/callbacks?mine=${mine ? 'true' : 'false'}`)
      if (gen === latest.current) setQ(prev => loadSucceeded(prev, gen, d))
    } catch (e) {
      if (gen === latest.current) setQ(prev => loadFailed(prev, gen, describeError(errText(e))))
    }
  }, [mine])
  useEffect(() => { load() }, [load])

  const data = q.data
  const view = queueView(q, bucket)
  const items = sortQueue(data?.buckets?.[bucket], bucket)
  const note = truncationNote(q, bucket)
  return (
    <EvoApp world="operations">
      <Hero scene="capital" eyebrow="Wholesale" title="Callback Center"
            sub="Every promised call back to a seller. A missed callback stays overdue until someone completes or cancels it." />
      {q.error ? (
        <div role="alert">
          <Alert kind="warn">
            {view === 'stale'
              ? `Could not refresh — showing the last list we loaded, which may be out of date. ${q.error}`
              : `Could not load callbacks. ${q.error}`}
            {' '}<button type="button" className="btn btn--secondary" onClick={load}>Try again</button>
          </Alert>
        </div>
      ) : null}
      <div className="wso-buckets" role="group" aria-label="Callback buckets">
        {BUCKETS.map(([k, label]) => (
          <button key={k} type="button" className={`wso-bucket wso-bucket--${k}`} aria-pressed={bucket === k}
                  onClick={() => setBucket(k)}>
            <span className="wso-bucket__n">{bucketCount(q, k)}</span>
            <span className="wso-muted">{label}</span>
          </button>
        ))}
      </div>
      <div className="wso-row" style={{ marginBottom: 12 }}>
        <label className="wso-row wso-small">
          <input type="checkbox" checked={mine} onChange={e => setMine(e.target.checked)} /> Only mine
        </label>
        <span className="wso-muted">
          {data ? `"Due now" is within ${data.window_minutes} minutes either side of the scheduled time.` : ''}
        </span>
      </div>
      <Panel title={BUCKETS.find(b => b[0] === bucket)[1]} count={items.length}
             hint="Schedule new callbacks from a deal's Callbacks & notes tab.">
        {view === 'loading' ? <p className="wso-muted" role="status">Loading…</p>
          : view === 'error' ? <p className="wso-muted">Callbacks could not be loaded.</p>
          : !items.length ? (
          <p className="wso-muted" role="status">Nothing here.</p>
        ) : (
          <>
          {view === 'refreshing' ? <p className="wso-muted" role="status">Refreshing…</p> : null}
          {note ? <p className="wso-muted">{note}</p> : null}
          <ul className="wso-list">
            {items.map(it => <CallbackItem key={it.id} it={it} onDone={load} />)}
          </ul>
          </>
        )}
      </Panel>
    </EvoApp>
  )
}
