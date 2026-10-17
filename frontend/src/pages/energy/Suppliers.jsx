/**
 * SUPPLIERS & ENROLLMENTS — what each supplier can really do, the advisor's
 * hand-off, and the queue of enrollments a person still has to confirm.
 *
 *   GET  /energy-ops/suppliers                      what is true per supplier
 *   POST /energy-ops/leads/{id}/handoff             record a hand-off + follow-up task
 *   GET  /energy-ops/enrollments                    submissions waiting for a person
 *   POST /energy-ops/enrollments/{id}/reconcile     record what was verified
 *
 * Nothing on this page sends anything to a customer or a supplier.
 */
import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import { LeadPicker } from './MoveConcierge'
import './energy.css'

const OUTCOMES = [
  ['enrolled', 'Enrolled — confirmed'],
  ['not_enrolled', 'Not enrolled'],
  ['not_received', 'Chariot never received it (allows a retry)'],
]
const STATUS = {
  provider_confirmed: 'Chariot said completed', pending_provider: 'Pending — no answer yet',
  action_required: 'Customer must act', provider_failed: 'Declined by Chariot',
  failed_validation: 'Our request was incomplete', integration_error: 'Connection problem',
}

export default function Suppliers() {
  const [sups, setSups] = useState(null)
  const [queue, setQueue] = useState(null)
  const [err, setErr] = useState('')
  const load = useCallback(() => {
    api.get('/energy-ops/suppliers').then(d => setSups(d.suppliers)).catch(e => setErr(e.message || 'Could not load suppliers'))
    api.get('/energy-ops/enrollments').then(setQueue).catch(() => setQueue({ items: [] }))
  }, [])
  useEffect(() => { load() }, [load])

  return (
    <div className="eo" data-testid="suppliers">
      <div className="eo-head">
        <div>
          <h1>Suppliers &amp; Enrollments</h1>
          <p className="eo-sub">What each supplier can do today, the customers you handed off, and the enrollments still waiting for someone to confirm.</p>
        </div>
      </div>
      {err && <div className="eo-banner eo-banner--error">{err}</div>}

      <Handoff suppliers={sups || []} onDone={load} />

      <h2 className="eo-sec">Enrollments to confirm</h2>
      <div className="eo-banner">
        Chariot has no status lookup: every enrollment stays here until someone confirms it with the customer or Chariot's weekly report.
        {queue && <> Online enrollment is <strong>{queue.submissions_enabled ? 'on' : 'off'}</strong> on this server.</>}
      </div>
      <div className="eo-panel">
        {!queue ? <div className="eo-empty">Loading…</div> : queue.items.length === 0 ? (
          <div className="eo-empty">Nothing waiting.</div>
        ) : (
          <table className="eo-table">
            <thead><tr><th>Customer</th><th>Submitted</th><th>Chariot said</th><th>Reference</th><th>Confirm</th></tr></thead>
            <tbody>
              {queue.items.map(r => <QueueRow key={r.id} r={r} onDone={load} />)}
            </tbody>
          </table>
        )}
      </div>

      <h2 className="eo-sec">What each supplier can do</h2>
      <div className="eo-panel">
        <table className="eo-table">
          <thead><tr><th>Supplier</th><th>Homes</th><th>Businesses</th><th>Online enrollment</th><th>How to hand off</th></tr></thead>
          <tbody>
            {(sups || []).map(s => (
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
      .then(() => { setMsg('Recorded. A follow-up task was added to confirm the outcome.'); setNote(''); onDone() })
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

function QueueRow({ r, onDone }) {
  const [outcome, setOutcome] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const save = () => {
    if (!outcome || !note.trim()) { setErr('Pick an outcome and say how you checked.'); return }
    setBusy(true); setErr('')
    api.post(`/energy-ops/enrollments/${r.id}/reconcile`, { outcome, note })
      .then(onDone).catch(e => setErr(e.message || 'Could not save')).finally(() => setBusy(false))
  }
  return (
    <tr>
      <td><strong>{r.name || 'Customer'}</strong><div className="eo-sub">{r.operation === 'submit_renewal' ? 'Renewal' : 'Enrollment'}{r.esiid ? ` · ESIID ${r.esiid}` : ''}</div></td>
      <td>{r.submitted_at ? new Date(r.submitted_at).toLocaleString() : '—'}<div className="eo-sub">{r.waiting_hours != null ? `${r.waiting_hours} h waiting` : ''}</div></td>
      <td>{STATUS[r.status] || r.status}<div className="eo-sub">{r.provider_said || ''}</div></td>
      <td>{r.provider_reference || '—'}</td>
      <td>
        <select value={outcome} onChange={e => setOutcome(e.target.value)} aria-label="Outcome">
          <option value="">Outcome…</option>
          {OUTCOMES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <input value={note} onChange={e => setNote(e.target.value)} placeholder="How you checked" aria-label="How you checked" />
        <button type="button" className="eo-btn eo-btn--sm" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
        {err && <div className="eo-sub" style={{ color: 'var(--signal-red, #c33)' }}>{err}</div>}
      </td>
    </tr>
  )
}
