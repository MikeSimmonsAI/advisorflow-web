/**
 * NEW REQUESTS — the ONE shared inbox for every website request and field
 * referral. A teammate who can take requests claims one (it becomes theirs and
 * so does its first-contact task); a manager can assign or reassign.
 *
 *   GET  /energy-ops/new-requests?state=unclaimed|mine|all
 *   POST /energy-ops/new-requests/{id}/claim
 *   POST /energy-ops/new-requests/{id}/assign   { user_id }   (managers)
 *
 * Contact details show only to the request's owner (or a manager). Nothing
 * here contacts the customer.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../../api/client'

const KIND = { rate_review: 'Rate review', bill_review: 'Bill review', move: 'Moving', commercial: 'Business', partner: 'Partner' }
const STATES = [['unclaimed', 'Unclaimed'], ['mine', 'Mine'], ['all', 'All new']]

function age(h) {
  if (h == null) return '—'
  if (h < 1) return 'just now'
  return h < 48 ? `${Math.round(h)}h` : `${Math.round(h / 24)}d`
}

export default function NewRequests() {
  const [params] = useSearchParams()
  const focus = params.get('open')
  // a link to one request opens on All, so an already-assigned one is found too
  const [state, setState] = useState(focus ? 'all' : 'unclaimed')
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [msg, setMsg] = useState('')
  const load = useCallback(() => {
    api.get(`/energy-ops/new-requests?state=${state}`).then(d => { setErr(''); setData(d) })
      .catch(e => setErr(e.message || 'Could not load new requests'))
  }, [state])
  useEffect(() => { load() }, [load])

  const claim = row => {
    setMsg('')
    api.post(`/energy-ops/new-requests/${row.lead_id}/claim`, {})
      .then(() => { setMsg(`${row.name} is yours. Their first-contact task is now on your list.`); load() })
      .catch(e => { setMsg(e.message || 'Could not claim'); load() })
  }
  const assign = (row, userId) => {
    if (!userId) return
    setMsg('')
    api.post(`/energy-ops/new-requests/${row.lead_id}/assign`, { user_id: userId })
      .then(() => { setMsg(`${row.name} assigned.`); load() })
      .catch(e => setMsg(e.message || 'Could not assign'))
  }

  if (err) return <div className="eo-banner eo-banner--error" data-testid="new-requests-error">{err}</div>
  if (!data) return <div className="eo-empty">Loading…</div>
  return (
    <div data-testid="new-requests">
      <div className="eo-tabs" role="group" aria-label="Which requests">
        {STATES.map(([k, l]) => (
          <button key={k} type="button" aria-pressed={state === k} className={`eo-btn${state === k ? ' eo-btn--primary' : ''}`}
            onClick={() => setState(k)} data-state={k}>{l} <span className="eo-pill">{data.counts[k]}</span></button>
        ))}
      </div>
      {msg && <div className="eo-note" role="status" data-testid="new-requests-msg">{msg}</div>}
      {data.truncated && <div className="eo-note">Showing the oldest 200. Claim or assign some to see the rest.</div>}
      <div className="eo-panel">
        {data.items.length === 0 ? <div className="eo-empty">{state === 'unclaimed' ? 'Nothing waiting to be claimed.' : 'Nothing here.'}</div> : (
          <table className="eo-table">
            <thead><tr><th>Request</th><th className="eo-hide-sm">From</th><th>Waiting</th><th>Owner</th></tr></thead>
            <tbody>
              {data.items.map(r => (
                <tr key={r.lead_id} data-request={r.lead_id} className={focus === r.lead_id ? 'eo-focus' : ''}>
                  <td>
                    {r.can_open ? <Link to={r.link}>{r.name}</Link> : <strong>{r.name}</strong>}
                    {r.is_test && <> <span className="eo-pill">Test</span></>}
                    <div className="eo-sub">{KIND[r.request_kind] || 'Request'}{r.company ? ` · ${r.company}` : ''}{r.area ? ` · ${r.area}` : ''}</div>
                    {(r.phone || r.email) && <div className="eo-sub">{[r.phone, r.email].filter(Boolean).join(' · ')}</div>}
                  </td>
                  <td className="eo-hide-sm">{r.origin === 'field' ? `Field: ${r.field_rep || 'rep'}` : 'Website'}</td>
                  <td>{age(r.age_hours)}{r.first_contact_overdue && <div><span className="eo-pill eo-pill--red">Overdue</span></div>}</td>
                  <td>
                    {r.assigned_to ? <span>{r.assigned_to}</span> : <span className="eo-sub">Unclaimed</span>}
                    <div className="eo-inline" style={{ marginTop: 4 }}>
                      {!r.assigned_to_id && data.can_claim && (
                        <button type="button" className="eo-btn eo-btn--primary" onClick={() => claim(r)}>Claim</button>
                      )}
                      {data.is_manager && (
                        <select aria-label={`Assign ${r.name}`} value="" onChange={e => assign(r, e.target.value)}>
                          <option value="">{r.assigned_to_id ? 'Reassign…' : 'Assign…'}</option>
                          {(data.assignable || []).filter(a => a.user_id !== r.assigned_to_id).map(a => (
                            <option key={a.user_id} value={a.user_id}>{a.name}</option>
                          ))}
                        </select>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <p className="eo-note">One owner per customer. Claiming also gives you the first-contact task. Contact details show to the owner and managers.</p>
    </div>
  )
}
