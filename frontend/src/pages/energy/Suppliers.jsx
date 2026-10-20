/**
 * SUPPLIERS — what each supplier can really do, and the advisor's hand-off.
 *
 *   GET  /energy-ops/suppliers               what is true per supplier
 *   POST /energy-ops/leads/{id}/handoff      record a hand-off + follow-up task
 *
 * Nothing on this page sends anything to a customer or a supplier.
 * Ported from PR #116 (dd73a038) without its Chariot enrollment queue, which
 * is Phase 2 and stays switched off.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { LeadPicker } from './MoveConcierge'
import HandoffOutcomes from './HandoffOutcomes'
import './energy.css'

export default function Suppliers() {
  const [sups, setSups] = useState(null)
  const [err, setErr] = useState('')
  const [tick, setTick] = useState(0)
  const load = useCallback(() => {
    setTick(t => t + 1)
    api.get('/energy-ops/suppliers').then(d => setSups(d.suppliers)).catch(e => setErr(e.message || 'Could not load suppliers'))
  }, [])
  useEffect(() => { load() }, [load])

  return (
    <div className="eo" data-testid="suppliers">
      <div className="eo-head">
        <div>
          <h1>Suppliers</h1>
          <p className="eo-sub">What each supplier can do today, and a record of every customer you hand to one. A hand-off adds a follow-up task to confirm the outcome.</p>
        </div>
      </div>
      {err && <div className="eo-banner eo-banner--error">{err}</div>}
      <div className="eo-banner">A quote is not an enrollment. A customer is only enrolled once the supplier confirms it.</div>

      <Handoff suppliers={sups || []} onDone={load} />

      <HandoffOutcomes suppliers={sups || []} refreshKey={tick} />

      <h2 className="eo-sec">What each supplier can do</h2>
      <div className="eo-panel">
        {!sups ? <div className="eo-empty">Loading…</div> : (
          <table className="eo-table">
            <thead><tr><th>Supplier</th><th>Homes</th><th>Businesses</th><th>Online enrollment</th><th>How to hand off</th></tr></thead>
            <tbody>
              {sups.map(s => (
                <tr key={s.key}>
                  <td><strong>{s.name}</strong></td>
                  <td>{s.residential ? 'Yes' : 'No'}</td>
                  <td>{s.commercial ? 'Yes' : 'No'}</td>
                  <td>{s.online_enrollment ? <span className="eo-pill eo-pill--green">On</span> : <span className="eo-pill">Off</span>}</td>
                  <td>{s.how}{s.link && <> <a href={s.link} target="_blank" rel="noreferrer">Atlantis link</a></>}<div className="eo-sub">Confirm with: {s.confirm_with}</div></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function Handoff({ suppliers, onDone }) {
  const [lead, setLead] = useState(null)
  const [supplier, setSupplier] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const submit = e => {
    e.preventDefault()
    if (!lead || !supplier) { setMsg('Pick a customer and a supplier.'); return }
    setBusy(true); setMsg('')
    api.post(`/energy-ops/leads/${lead.id}/handoff`, { supplier, note })
      .then(r => {
        setMsg(r.duplicate
          ? 'This customer already has an open hand-off to that supplier - nothing new was recorded. Record its outcome below.'
          : 'Recorded. A follow-up task was added to confirm the outcome.')
        setNote(''); onDone()
      })
      .catch(e => setMsg(e.message || 'Could not record the hand-off'))
      .finally(() => setBusy(false))
  }
  return (
    <form className="eo-form" onSubmit={submit} data-testid="handoff-form">
      <h2 className="eo-sec" style={{ marginTop: 0 }}>Record a hand-off</h2>
      <LeadPicker value={lead?.id} onPick={setLead} />
      <label>Supplier
        <select value={supplier} onChange={e => setSupplier(e.target.value)}>
          <option value="">Choose…</option>
          {suppliers.map(s => <option key={s.key} value={s.key}>{s.name}</option>)}
        </select>
      </label>
      <label>What you did
        <input value={note} onChange={e => setNote(e.target.value)} placeholder="e.g. Sent the ComparePower link by text, customer choosing tonight" />
      </label>
      <button type="submit" className="eo-btn eo-btn--primary" disabled={busy}>{busy ? 'Saving…' : 'Record hand-off'}</button>
      {msg && <div className="eo-note">{msg}</div>}
    </form>
  )
}
