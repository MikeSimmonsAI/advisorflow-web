/**
 * CHARIOT ONLINE ENROLLMENTS — waiting for a person to confirm what happened.
 * Chariot has no status lookup, so every submission lands here and only a
 * person closes it. "Enrolled" runs the same Enroll action as a manual
 * enrollment; "Chariot never received it" allows one fresh submission.
 *
 *   GET  /energy-ops/enrollments?state=open|all
 *   POST /energy-ops/enrollments/{id}/reconcile   { outcome, note }
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'

const OUTCOMES = [['enrolled', 'Enrolled (confirmed)'], ['not_enrolled', 'Not enrolled'], ['not_received', 'Chariot never received it']]

export default function ChariotQueue() {
  const [data, setData] = useState(null)
  const [state, setState] = useState('open')
  const [notice, setNotice] = useState('')
  const load = useCallback(() => {
    api.get(`/energy-ops/enrollments?state=${state}`).then(setData).catch(() => setData(null))
  }, [state])
  useEffect(() => { load() }, [load])
  if (!data) return null
  return (
    <div id="chariot" data-testid="chariot-queue">
      <h2 className="eo-sec">
        Online enrollments with Chariot <span className="eo-pill">{data.total}</span>
        {' '}<span className={`eo-pill${data.status.submissions_enabled ? ' eo-pill--green' : ''}`}>{data.status.submissions_enabled ? 'Online enrollment on' : 'Online enrollment off'}</span>
      </h2>
      <div className="eo-tabs" role="group" aria-label="Which enrollments">
        {['open', 'all'].map(s => (
          <button key={s} type="button" aria-pressed={state === s} className={`eo-btn${state === s ? ' eo-btn--primary' : ''}`}
            onClick={() => setState(s)}>{s === 'open' ? 'Waiting for confirmation' : 'All'}</button>
        ))}
      </div>
      {notice && <div className="eo-note" role="status" data-testid="chariot-notice">{notice}</div>}
      <div className="eo-panel">
        {data.items.length === 0 ? <div className="eo-empty">{state === 'open' ? 'Nothing waiting for confirmation.' : 'No online enrollments yet.'}</div> : (
          <table className="eo-table">
            <thead><tr><th>Customer</th><th>Chariot said</th><th>Confirm</th></tr></thead>
            <tbody>
              {data.items.map(r => (
                <tr key={r.id} data-submission={r.id}>
                  <td><Link to={`/leads/${r.lead_id}?tab=overview`}>{r.name || 'Customer'}</Link>
                    <div className="eo-sub">{r.plan_title || r.product_id}{r.provider_reference ? ` · Ref ${r.provider_reference}` : ''} · {(r.submitted_at || '').slice(0, 10)}</div></td>
                  <td>{r.provider_said || '—'}<div className="eo-sub">{r.meaning}</div>{r.amount_quoted && <div className="eo-sub">Deposit quoted: {r.amount_quoted}</div>}</td>
                  <td>{r.resolved_at ? <span className="eo-sub">Closed: {r.verified_outcome}</span>
                    : <Confirm row={r} onDone={m => { setNotice(m); load() }} />}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function Confirm({ row, onDone }) {
  const [outcome, setOutcome] = useState('')
  const [note, setNote] = useState('')
  const [msg, setMsg] = useState('')
  const save = e => {
    e.preventDefault()
    if (!outcome || !note.trim()) { setMsg('Choose what happened and say how you verified it.'); return }
    api.post(`/energy-ops/enrollments/${row.id}/reconcile`, { outcome, note })
      .then(r => onDone(r.enrolled ? `${row.name || 'Customer'} is enrolled.` : 'Saved.'))
      .catch(e2 => setMsg(e2.message || 'Could not save'))
  }
  return (
    <form className="eo-inline" onSubmit={save}>
      <select aria-label="What happened" value={outcome} onChange={e => setOutcome(e.target.value)}>
        <option value="">What happened…</option>
        {OUTCOMES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
      <input aria-label="How you verified it" placeholder="How you verified it" value={note} onChange={e => setNote(e.target.value)} />
      <button type="submit" className="eo-btn">Save</button>
      {msg && <div className="eo-note">{msg}</div>}
    </form>
  )
}
