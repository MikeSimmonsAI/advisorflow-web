/**
 * ENERGY LEAD PANEL — shown on a lead's Overview only in energy workspaces.
 *
 *   GET  /energy-ops/leads/{id}/qualification            what is known / missing / next step
 *   POST /energy-ops/leads/{id}/qualification            fields + bill lines typed by an advisor
 *   POST /energy-ops/leads/{id}/qualification/request-info   one "Get missing details" task
 *   GET  /energy-ops/leads/{id}/commercial               business opportunity (409 = not a business)
 *   POST /energy-ops/leads/{id}/commercial/stage         stage change (server enforces the rules)
 *   POST|PATCH /energy-ops/leads/{id}/commercial/locations[/{loc}]
 *
 * No price, rate or saving is shown or calculated. Nothing here contacts the
 * customer or a supplier. Enrollment stays the Enroll action on Rate Requests.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import './energy.css'

const STATE = { have: 'Have it', missing: 'Missing', unknown: 'Not known', na: '—' }
const EDIT = [
  ['service_address', 'Service address'], ['current_supplier', 'Current supplier'],
  ['contract_end_date', 'Contract end date (YYYY-MM-DD)'], ['monthly_usage', 'Monthly usage'],
]
const EDIT_BIZ = [['company', 'Business name'], ['decision_maker', 'Who signs for electricity']]
const BILL = [['total_amount', 'Bill total'], ['usage_kwh', 'Usage (kWh)'], ['period_start', 'Period start'],
  ['period_end', 'Period end'], ['energy_charge', 'Energy charge'], ['delivery_charges', 'Delivery charges']]
const nice = s => (s || '').replace(/_/g, ' ').replace(/^./, c => c.toUpperCase())

export default function EnergyLeadPanel({ leadId }) {
  const [q, setQ] = useState(null)
  const [err, setErr] = useState('')
  const load = useCallback(() => {
    api.get(`/energy-ops/leads/${leadId}/qualification`).then(d => { setErr(''); setQ(d) })
      .catch(e => setErr(e.message || 'Could not load the energy details'))
  }, [leadId])
  useEffect(() => { load() }, [load])
  if (err) return <div className="eo-banner eo-banner--error">{err}</div>
  if (!q) return null
  return (
    <div className="eo eo-leadpanel" data-testid="energy-lead-panel">
      <Qualification q={q} leadId={leadId} onSaved={setQ} />
      {q.segment === 'commercial' && <Commercial leadId={leadId} onChange={load} />}
    </div>
  )
}

function Qualification({ q, leadId, onSaved }) {
  const [edit, setEdit] = useState(false)
  const [form, setForm] = useState({})
  const [bill, setBill] = useState({})
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const fields = q.segment === 'commercial' ? [...EDIT_BIZ, ...EDIT] : EDIT
  const open = () => {
    setForm({}); setBill({ values_are: q.bill?.values_are || 'actual', ...(q.bill || {}) }); setMsg(''); setEdit(true)
  }
  const save = e => {
    e.preventDefault()
    const f = Object.fromEntries(Object.entries(form).filter(([, v]) => String(v).trim() !== ''))
    const b = Object.fromEntries(Object.entries(bill).filter(([, v]) => String(v ?? '').trim() !== ''))
    const hasBill = Object.keys(b).some(k => k !== 'values_are')
    setBusy(true); setMsg('')
    api.post(`/energy-ops/leads/${leadId}/qualification`, { fields: f, bill: hasBill ? b : null })
      .then(d => { onSaved(d); setEdit(false) })
      .catch(e2 => setMsg(e2.message || 'Could not save'))
      .finally(() => setBusy(false))
  }
  const ask = () => {
    setBusy(true); setMsg('')
    api.post(`/energy-ops/leads/${leadId}/qualification/request-info`, {})
      .then(() => setMsg('A "Get missing details" task is open for this customer.'))
      .catch(e2 => setMsg(e2.message || 'Could not add the task'))
      .finally(() => setBusy(false))
  }
  return (
    <section className="eo-panel" data-testid="energy-qualification">
      <div className="eo-head" style={{ marginBottom: 6 }}>
        <h3 className="eo-sec" style={{ margin: 0 }}>Energy details · {nice(q.segment)}</h3>
        {q.ready ? <span className="eo-pill eo-pill--green">Ready</span> : <span className="eo-pill">{q.missing.length} missing</span>}
      </div>
      <p className="eo-note" data-testid="next-step"><strong>Next step:</strong> {q.next_step.label}</p>
      <table className="eo-table">
        <tbody>
          {q.fields.filter(f => f.state !== 'na').map(f => (
            <tr key={f.key} data-field={f.key}>
              <td>{f.label}{f.required && <span className="eo-sub"> · needed</span>}</td>
              <td>{f.state === 'have' ? String(f.value) : <span className={f.required ? 'eo-pill eo-pill--red' : 'eo-pill'}>{STATE[f.state] || f.state}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {q.bill && (
        <p className="eo-note">Bill on file ({q.bill.values_are === 'estimated' ? 'estimated' : 'from the bill'}):{' '}
          {BILL.filter(([k]) => q.bill[k]).map(([k, l]) => `${l} ${q.bill[k]}`).join(' · ')}</p>
      )}
      <div className="eo-inline" style={{ margin: '8px 0' }}>
        <button type="button" className="eo-btn" onClick={open} disabled={busy}>Update details</button>
        {q.missing.length > 0 && <button type="button" className="eo-btn" onClick={ask} disabled={busy}>Add "get missing details" task</button>}
      </div>
      {edit && (
        <form className="eo-form" onSubmit={save} data-testid="qualification-form">
          {fields.map(([k, l]) => (
            <label key={k}>{l}<input value={form[k] ?? ''} onChange={e => setForm(p => ({ ...p, [k]: e.target.value }))} /></label>
          ))}
          <fieldset style={{ border: 0, padding: 0, margin: 0 }}>
            <legend className="eo-sub">From the customer's bill (typed in, nothing is calculated)</legend>
            {BILL.map(([k, l]) => (
              <label key={k}>{l}<input value={bill[k] ?? ''} onChange={e => setBill(p => ({ ...p, [k]: e.target.value }))} /></label>
            ))}
            <label>These numbers are
              <select value={bill.values_are || 'actual'} onChange={e => setBill(p => ({ ...p, values_are: e.target.value }))}>
                <option value="actual">From the actual bill</option><option value="estimated">Estimated by the customer</option>
              </select>
            </label>
          </fieldset>
          <span className="eo-inline">
            <button type="submit" className="eo-btn eo-btn--primary" disabled={busy}>{busy ? 'Saving…' : 'Save'}</button>
            <button type="button" className="eo-btn" onClick={() => setEdit(false)}>Cancel</button>
          </span>
        </form>
      )}
      {msg && <div className="eo-note" role="status">{msg}</div>}
      <h4 className="eo-sec">Suppliers for this customer</h4>
      <ul className="eo-list" data-testid="supplier-options">
        {q.suppliers.map(s => (
          <li key={s.key} data-supplier={s.key}>
            <strong>{s.name}</strong> — {s.eligible ? 'Can be handed off' : 'Not for this customer'}
            {s.note && <span className="eo-sub"> · {s.note}</span>}
          </li>
        ))}
      </ul>
      <p className="eo-note">Record a hand-off on the Suppliers page. A customer is enrolled only after the supplier confirms it.</p>
    </section>
  )
}

function Commercial({ leadId, onChange }) {
  const [v, setV] = useState(null)
  const [err, setErr] = useState('')
  const [stage, setStage] = useState('')
  const [extra, setExtra] = useState({ note: '', reason: '', revisit_on: '' })
  const [loc, setLoc] = useState({ address: '', meter_id: '', utility: '', contract_end_date: '' })
  const [msg, setMsg] = useState('')
  const base = `/energy-ops/leads/${leadId}/commercial`
  const load = useCallback(() => {
    api.get(base).then(d => { setErr(''); setV(d) }).catch(e => setErr(e.message || 'Could not load'))
  }, [base])
  useEffect(() => { load() }, [load])
  if (err) return <div className="eo-banner eo-banner--error">{err}</div>
  if (!v) return null
  const closed = ['won', 'lost'].includes(v.stage)
  const move = e => {
    e.preventDefault()
    if (!stage) return
    const body = { stage, note: extra.note || undefined }
    if (stage === 'lost') body.reason = extra.reason
    if (stage === 'deferred') body.revisit_on = extra.revisit_on
    setMsg('')
    api.post(`${base}/stage`, body)
      .then(d => {
        setV(d); setStage(''); setExtra({ note: '', reason: '', revisit_on: '' })
        setMsg(d.stage === 'won' ? 'Marked won. Not enrolled - record the enrollment with Enroll once the supplier has confirmed it.' : `Moved to ${nice(d.stage)}.`)
        onChange()
      })
      .catch(e2 => setMsg(e2.message || 'That stage change was refused'))
  }
  const addLoc = e => {
    e.preventDefault()
    setMsg('')
    api.post(`${base}/locations`, loc)
      .then(d => { setV(d); setLoc({ address: '', meter_id: '', utility: '', contract_end_date: '' }); setMsg(d.duplicate ? 'That site is already on this account - any new details were added to it.' : 'Location added.') })
      .catch(e2 => setMsg(e2.message || 'Could not add the location'))
  }
  const quote = (id, quote_status) => {
    api.patch(`${base}/locations/${id}`, { quote_status }).then(setV).catch(e2 => setMsg(e2.message || 'Could not update'))
  }
  return (
    <section className="eo-panel" data-testid="energy-commercial">
      <div className="eo-head" style={{ marginBottom: 6 }}>
        <h3 className="eo-sec" style={{ margin: 0 }}>Business opportunity{v.company ? ` · ${v.company}` : ''}</h3>
        <span className={`eo-pill${v.stage === 'won' ? ' eo-pill--green' : v.stage === 'lost' ? ' eo-pill--red' : ''}`} data-testid="commercial-stage">{nice(v.stage)}</span>
      </div>
      {v.lost_reason && <p className="eo-note">Lost: {v.lost_reason}</p>}
      {v.revisit_on && v.stage === 'deferred' && <p className="eo-note">Revisit on {v.revisit_on}</p>}
      {!closed && (
        <form className="eo-inline" onSubmit={move} data-testid="stage-form">
          <select aria-label="Move to stage" value={stage} onChange={e => setStage(e.target.value)}>
            <option value="">Move to…</option>
            {v.stages.filter(s => s !== v.stage).map(s => <option key={s} value={s}>{nice(s)}</option>)}
          </select>
          {stage === 'lost' && <input aria-label="Why it was lost" placeholder="Why it was lost" value={extra.reason} onChange={e => setExtra(p => ({ ...p, reason: e.target.value }))} />}
          {stage === 'deferred' && <input aria-label="Revisit on" type="date" value={extra.revisit_on} onChange={e => setExtra(p => ({ ...p, revisit_on: e.target.value }))} />}
          <input aria-label="Stage note" placeholder="Note (optional)" value={extra.note} onChange={e => setExtra(p => ({ ...p, note: e.target.value }))} />
          <button type="submit" className="eo-btn" disabled={!stage}>Save stage</button>
        </form>
      )}
      <p className="eo-sub">Won is accepted only after the supplier has confirmed the contract.</p>
      <h4 className="eo-sec">Service locations ({v.location_count}){v.earliest_contract_end ? ` · first contract ends ${v.earliest_contract_end}` : ''}</h4>
      {v.locations.length > 0 && (
        <table className="eo-table" data-testid="locations">
          <thead><tr><th>Address</th><th className="eo-hide-sm">Meter</th><th className="eo-hide-sm">Contract ends</th><th>Quote</th></tr></thead>
          <tbody>
            {v.locations.map(l => (
              <tr key={l.id}><td>{l.address}{l.utility && <div className="eo-sub">{l.utility}</div>}</td>
                <td className="eo-hide-sm">{l.meter_id || '—'}</td><td className="eo-hide-sm">{l.contract_end_date || '—'}</td>
                <td><select aria-label={`Quote for ${l.address}`} value={l.quote_status || 'not_requested'} onChange={e => quote(l.id, e.target.value)}>
                  {['not_requested', 'requested', 'received', 'declined'].map(s => <option key={s} value={s}>{nice(s)}</option>)}
                </select></td></tr>
            ))}
          </tbody>
        </table>
      )}
      <form className="eo-inline" onSubmit={addLoc} data-testid="location-form" style={{ marginTop: 8 }}>
        <input aria-label="Site address" placeholder="Site address" value={loc.address} onChange={e => setLoc(p => ({ ...p, address: e.target.value }))} />
        <input aria-label="Meter (ESI ID)" placeholder="Meter (ESI ID)" value={loc.meter_id} onChange={e => setLoc(p => ({ ...p, meter_id: e.target.value }))} />
        <input aria-label="Contract end" type="date" value={loc.contract_end_date} onChange={e => setLoc(p => ({ ...p, contract_end_date: e.target.value }))} />
        <button type="submit" className="eo-btn" disabled={!loc.address.trim()}>Add location</button>
      </form>
      {msg && <div className="eo-note" role="status" data-testid="commercial-msg">{msg}</div>}
      {v.history.length > 0 && (
        <details className="eo-note"><summary>History ({v.history.length})</summary>
          <ul className="eo-list">{v.history.map((h, i) => <li key={i}>{(h.at || '').slice(0, 10)} · {nice(h.from)} → {nice(h.to)}{h.note ? ` · ${h.note}` : ''}</li>)}</ul>
        </details>
      )}
    </section>
  )
}
