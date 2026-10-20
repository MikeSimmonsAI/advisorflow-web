/**
 * FIELD VISITS — a door-to-door rep logs what happened at each door, and sees
 * their own numbers; a manager sees every rep.
 *
 *   POST /energy-ops/field/visits
 *   GET  /energy-ops/field/summary?days=
 *
 * Only a resident who gave a phone/email AND said yes to being contacted
 * becomes a referral. Location is what the rep typed - it is not verified.
 * Nothing here texts, emails or calls anyone.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'

const OUTCOMES = [
  ['no_answer', 'No answer'],
  ['door_hanger_left', 'Left a door hanger'],
  ['declined', 'Not interested'],
  ['interested', 'Interested - wants a call'],
  ['appointment_requested', 'Wants an appointment'],
  ['business_lead', 'Business owner interested'],
  ['not_eligible', 'Not eligible'],
  ['do_not_contact', 'Asked not to be contacted'],
]
const NEEDS_CONTACT = ['interested', 'appointment_requested', 'business_lead']
const RESULT = {
  new_referral: 'Referral created and sent to the advisor queue.',
  existing_customer: 'Already a customer record - your visit was linked to it.',
  forwarded_to_owner: 'This person is already being handled by someone else. Their owner has been told.',
  marked_dnc: 'Marked do-not-contact.',
  recorded: 'Visit recorded.',
}
const BLANK = { outcome: 'no_answer', address: '', zip_code: '', first_name: '', last_name: '', phone: '', email: '', company: '', notes: '', permission_to_contact: false }

export default function FieldDesk() {
  const [f, setF] = useState(BLANK)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState(null)
  const [sum, setSum] = useState(null)
  const [days, setDays] = useState(7)
  const loadSum = useCallback(() => {
    api.get(`/energy-ops/field/summary?days=${days}`).then(setSum).catch(() => setSum(null))
  }, [days])
  useEffect(() => { loadSum() }, [loadSum])
  const set = k => e => setF(p => ({ ...p, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))
  const needs = NEEDS_CONTACT.includes(f.outcome)

  const submit = e => {
    e.preventDefault()
    if (!f.address.trim()) { setMsg({ err: true, text: 'Enter the address you visited.' }); return }
    if (needs && !(f.phone.trim() || f.email.trim())) { setMsg({ err: true, text: 'Add a phone or email so an advisor can reach them.' }); return }
    if (needs && !f.permission_to_contact) { setMsg({ err: true, text: 'Only send a referral if they said yes to being contacted.' }); return }
    if (f.outcome === 'business_lead' && !f.company.trim()) { setMsg({ err: true, text: 'Add the business name.' }); return }
    setBusy(true); setMsg(null)
    api.post('/energy-ops/field/visits', f)
      .then(r => {
        setMsg({ text: r.duplicate ? 'Already logged this door recently - not counted twice.' : (RESULT[r.result] || 'Saved.'), lead: r.lead_id })
        setF(p => ({ ...BLANK, outcome: p.outcome, zip_code: p.zip_code }))
        loadSum()
      })
      .catch(err => setMsg({ err: true, text: err.message || 'Could not save the visit' }))
      .finally(() => setBusy(false))
  }

  return (
    <div data-testid="field-desk">
      <form className="eo-form" onSubmit={submit} data-testid="field-visit-form">
        <h2 className="eo-sec" style={{ marginTop: 0 }}>Log a door</h2>
        <label>What happened
          <select value={f.outcome} onChange={set('outcome')}>
            {OUTCOMES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </label>
        <label>Address<input value={f.address} onChange={set('address')} placeholder="Street and number" autoComplete="off" /></label>
        <label>ZIP<input value={f.zip_code} onChange={set('zip_code')} inputMode="numeric" maxLength={10} /></label>
        {(needs || f.outcome === 'do_not_contact') && (<>
          {f.outcome === 'business_lead' && <label>Business name<input value={f.company} onChange={set('company')} /></label>}
          {needs && <label>First name<input value={f.first_name} onChange={set('first_name')} /></label>}
          {needs && <label>Last name<input value={f.last_name} onChange={set('last_name')} /></label>}
          <label>Phone<input value={f.phone} onChange={set('phone')} inputMode="tel" /></label>
          <label>Email<input value={f.email} onChange={set('email')} inputMode="email" /></label>
          {needs && (
            <label className="eo-check">
              <input type="checkbox" checked={f.permission_to_contact} onChange={set('permission_to_contact')} />
              They said yes to an Atlantis advisor contacting them
            </label>
          )}
        </>)}
        <label>Note<input value={f.notes} onChange={set('notes')} placeholder="Optional" /></label>
        <button type="submit" className="eo-btn eo-btn--primary" disabled={busy}>{busy ? 'Saving…' : 'Save visit'}</button>
        {msg && <div className={msg.err ? 'eo-banner eo-banner--error' : 'eo-note'}>{msg.text}{msg.lead && <> <Link to={`/leads/${msg.lead}`}>Open</Link></>}</div>}
      </form>

      <h2 className="eo-sec">
        {sum?.scope === 'all_reps' ? 'Every rep' : 'My doors'}&nbsp;
        <select value={days} onChange={e => setDays(Number(e.target.value))} aria-label="Period">
          {[1, 7, 30].map(d => <option key={d} value={d}>{d === 1 ? 'today' : `last ${d} days`}</option>)}
        </select>
      </h2>
      <div className="eo-panel">
        {!sum ? <div className="eo-empty">Loading…</div> : sum.reps.length === 0 ? <div className="eo-empty">No visits logged yet.</div> : (
          <table className="eo-table">
            <thead><tr><th>Rep</th><th>Doors</th><th>Referrals</th><th className="eo-hide-sm">Door hangers</th><th className="eo-hide-sm">Enrolled</th></tr></thead>
            <tbody>
              {sum.reps.map(r => (
                <tr key={r.rep_id}><td>{r.name || 'Rep'}</td><td>{r.visits}</td>
                  <td>{r.new_referrals}{r.visits ? <span className="eo-sub"> ({Math.round((r.referral_rate || 0) * 100)}%)</span> : null}</td>
                  <td className="eo-hide-sm">{r.door_hangers}</td><td className="eo-hide-sm">{r.enrolled}</td></tr>
              ))}
            </tbody>
          </table>
        )}
        {sum?.basis && <p className="eo-note">{sum.basis}</p>}
      </div>
    </div>
  )
}
