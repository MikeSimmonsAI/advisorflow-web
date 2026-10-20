/**
 * OPEN SUPPLIER HAND-OFFS — every customer handed to a supplier that still has
 * no final outcome, oldest first, so a referral cannot quietly drop away.
 *
 *   GET  /energy-ops/supplier-handoffs?state=open|all
 *   POST /energy-ops/leads/{id}/handoffs/{hid}/outcome   { outcome, source, note }
 *
 * Recording an outcome NEVER enrolls anyone: enrollment stays the Enroll action
 * on Rate Requests, and only after the supplier itself confirmed it.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'

export const OUTCOMES = [
  ['awaiting_provider', 'Waiting on supplier'],
  ['customer_action_pending', 'Customer still has to act'],
  ['confirmed', 'Confirmed'],
  ['not_completed', 'Did not go ahead'],
  ['cancelled', 'Cancelled'],
  ['unknown', 'Unknown'],
]
const LABEL = Object.fromEntries([['referred', 'Handed off'], ...OUTCOMES])

function ago(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  const days = Math.floor((Date.now() - d.getTime()) / 86400000)
  return days <= 0 ? 'today' : `${days}d ago`
}

export default function HandoffOutcomes({ suppliers = [], refreshKey = 0 }) {
  const [state, setState] = useState('open')
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [notice, setNotice] = useState('')
  const names = Object.fromEntries(suppliers.map(s => [s.key, s.name]))
  const load = useCallback(() => {
    // keep the current rows on screen while refreshing (no flash, no lost focus)
    api.get(`/energy-ops/supplier-handoffs?state=${state}`).then(setData)
      .catch(e => setErr(e.message || 'Could not load hand-offs'))
  }, [state])
  useEffect(() => { load() }, [load, refreshKey])

  return (
    <div data-testid="handoff-outcomes">
      <h2 className="eo-sec">
        {state === 'open' ? 'Hand-offs waiting for an outcome' : 'All hand-offs'}
        {data && <> <span className="eo-pill">{data.total}</span></>}
      </h2>
      <div className="eo-tabs" role="tablist">
        {['open', 'all'].map(s => (
          <button key={s} type="button" role="tab" aria-selected={state === s}
            className={`eo-btn${state === s ? ' eo-btn--primary' : ''}`} onClick={() => setState(s)}>
            {s === 'open' ? 'Waiting' : 'All'}
          </button>
        ))}
      </div>
      {err && <div className="eo-banner eo-banner--error">{err}</div>}
      {notice && <div className="eo-note" role="status" data-testid="outcome-notice">{notice}</div>}
      <div className="eo-panel">
        {!data ? <div className="eo-empty">Loading…</div>
          : data.items.length === 0 ? <div className="eo-empty">{state === 'open' ? 'Every hand-off has an outcome.' : 'No hand-offs recorded yet.'}</div>
            : (
              <table className="eo-table">
                <thead><tr><th>Customer</th><th>Supplier</th><th>Status</th><th className="eo-hide-sm">Handed off</th><th>Record outcome</th></tr></thead>
                <tbody>
                  {data.items.map(r => (
                    <tr key={`${r.lead_id}-${r.handoff.id}`} data-handoff={r.handoff.id}>
                      <td><Link to={r.lead_link}>{r.name || 'Unnamed'}</Link>{r.owner && <div className="eo-sub">{r.owner}</div>}</td>
                      <td>{names[r.handoff.supplier] || r.handoff.supplier}</td>
                      <td>{LABEL[r.handoff.status] || r.handoff.status}
                        {r.handoff.status_source === 'supplier_confirmation' && <div className="eo-sub">Supplier confirmed</div>}
                        {r.handoff.status_source === 'staff_report' && <div className="eo-sub">Reported by staff</div>}</td>
                      <td className="eo-hide-sm">{ago(r.handoff.at)}</td>
                      <td><OutcomeForm row={r} onDone={m => { setNotice(m); load() }} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
      </div>
    </div>
  )
}

function OutcomeForm({ row, onDone }) {
  const [outcome, setOutcome] = useState('')
  const [source, setSource] = useState('staff_report')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const terminal = ['confirmed', 'not_completed', 'cancelled'].includes(row.handoff.status)
  if (terminal) return <span className="eo-sub">Final</span>
  const save = e => {
    e.preventDefault()
    if (!outcome) { setMsg('Choose an outcome.'); return }
    if (!note.trim()) { setMsg('Say how you know (who told you, and how).'); return }
    setBusy(true); setMsg('')
    api.post(`/energy-ops/leads/${row.lead_id}/handoffs/${row.handoff.id}/outcome`, { outcome, source, note })
      .then(r => {
        const who = row.name || 'this customer'
        onDone(r.next_step ? `Saved for ${who}. ${r.next_step}`
          : `Saved for ${who}. Not enrolled - enrollment is the Enroll action only.`)
      })
      .catch(e2 => setMsg(e2.message || 'Could not save'))
      .finally(() => setBusy(false))
  }
  return (
    <form className="eo-inline" onSubmit={save}>
      <select aria-label="Outcome" value={outcome} onChange={e => setOutcome(e.target.value)}>
        <option value="">Outcome…</option>
        {OUTCOMES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
      <select aria-label="How do you know" value={source} onChange={e => setSource(e.target.value)}>
        <option value="staff_report">Customer / staff told me</option>
        <option value="supplier_confirmation">Supplier confirmed it</option>
      </select>
      <input aria-label="Note" value={note} onChange={e => setNote(e.target.value)} placeholder="How you know" />
      <button type="submit" className="eo-btn" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
      {msg && <div className="eo-note">{msg}</div>}
    </form>
  )
}
