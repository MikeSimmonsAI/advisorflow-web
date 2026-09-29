/* Callback Center — Due Now / Upcoming / Overdue / Completed.
 *
 * Every count and row comes from GET /wholesale/ops/callbacks. Missed callbacks
 * stay in Overdue until a person completes or cancels them. Seller requests the
 * system detected (a reply asking to be called) appear here too, marked
 * "Seller asked", until someone works them. Nothing on this page places a call.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../../api/client'
import '../../../styles/shared.css'
import '../wholesale.css'
import { Alert, EvoApp, Hero, Panel, Tag } from '../ds/ds'
import '../ds/evo-pages.css'
import { errText, fmtWhen } from '../wsShared'
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
  async function finish(kind) {
    setBusy(true); setErr('')
    try { await api.post(`/wholesale/ops/callbacks/${encodeURIComponent(it.id)}/${kind}`, { note }); onDone() }
    catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }
  const open = it.status === 'due'
  return (
    <li className="wso-item">
      <div className="wso-row">
        <span className="wso-item__title">{it.seller_name || 'Seller'}</span>
        {it.kind === 'requested' ? <Tag kind="info">Seller asked</Tag> : null}
        {it.dnc ? <Tag kind="danger">Do Not Contact</Tag> : null}
        {it.is_test ? <Tag kind="sandbox">Test</Tag> : null}
        {it.deal_id ? <Link className="wso-small" to={`/wholesale/deals/${it.deal_id}`}>Open deal</Link> : null}
      </div>
      <div className="wso-muted">
        {it.status === 'completed' ? `completed ${fmtWhen(it.completed_at)}` : `due ${fmtWhen(it.due_at)}`}
        {it.phone ? ` · ${it.phone}` : ''}
        {it.assigned_to_name ? ` · ${it.assigned_to_name}` : ' · unassigned'}
      </div>
      {it.notes ? <p className="wso-item__body">{it.notes}</p> : null}
      {it.outcome_note ? <p className="wso-item__body"><strong>Outcome:</strong> {it.outcome_note}</p> : null}
      {open ? (
        <>
          <input className="wso-input" style={{ marginTop: 8 }} placeholder="What happened on the call? (optional)"
                 value={note} onChange={e => setNote(e.target.value)} aria-label="Outcome note" />
          <div className="wso-row" style={{ marginTop: 8 }}>
            <button className="btn btn--primary" disabled={busy} onClick={() => finish('complete')}>Mark completed</button>
            <button className="btn btn--secondary" disabled={busy} onClick={() => finish('cancel')}>Cancel callback</button>
          </div>
        </>
      ) : null}
      {err ? <Alert kind="warn">{err}</Alert> : null}
    </li>
  )
}

export default function CallbackCenter() {
  const [bucket, setBucket] = useState('due_now')
  const [mine, setMine] = useState(false)
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')

  const load = useCallback(async () => {
    setErr('')
    try { setData(await api.get(`/wholesale/ops/callbacks?mine=${mine ? 'true' : 'false'}`)) }
    catch (e) { setErr(errText(e)) }
  }, [mine])
  useEffect(() => { load() }, [load])

  const items = data?.buckets?.[bucket] || []
  return (
    <EvoApp world="operations">
      <Hero scene="capital" eyebrow="Wholesale" title="Callback Center"
            sub="Every promised call back to a seller. A missed callback stays overdue until someone completes or cancels it." />
      {err ? <Alert kind="warn">{err}</Alert> : null}
      <div className="wso-buckets" role="group" aria-label="Callback buckets">
        {BUCKETS.map(([k, label]) => (
          <button key={k} type="button" className={`wso-bucket wso-bucket--${k}`} aria-pressed={bucket === k}
                  onClick={() => setBucket(k)}>
            <span className="wso-bucket__n">{data ? data.counts?.[k] ?? 0 : '—'}</span>
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
        {!data ? <p className="wso-muted">Loading…</p> : !items.length ? (
          <p className="wso-muted">Nothing here.</p>
        ) : (
          <ul className="wso-list">
            {items.map(it => <CallbackItem key={it.id} it={it} onDone={load} />)}
          </ul>
        )}
      </Panel>
    </EvoApp>
  )
}
