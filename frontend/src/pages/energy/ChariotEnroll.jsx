/**
 * ENROLL — the two ways a home customer gets enrolled, side by side:
 *   1. Advisor enrolls manually   (existing: hand-off on Suppliers + Enroll on Rate Requests)
 *   2. Enroll online with Chariot (plans -> meter -> start -> customer -> submit)
 *
 *   GET  /energy-ops/leads/{id}/chariot                 eligibility, switch, earlier submissions
 *   GET  /energy-ops/leads/{id}/chariot/plans?zip=
 *   GET  /energy-ops/leads/{id}/chariot/addresses?zip=&address=
 *   POST /energy-ops/leads/{id}/chariot/enroll
 *
 * Date of birth / SSN / licence live only in this form's memory and the one
 * request; they are cleared after sending and never stored by the server.
 * While online enrollment is switched off the form can be filled but not sent.
 */
import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../../api/client'

const EMPTY_ID = { dob: '', ssn: '', drivers_licence: '', drivers_licence_state: 'TX' }

export default function ChariotEnroll({ leadId }) {
  const [info, setInfo] = useState(null)
  const [err, setErr] = useState('')
  const [sent, setSent] = useState(null)       // kept here: the form is replaced once a submission exists
  const load = useCallback(() => {
    api.get(`/energy-ops/leads/${leadId}/chariot`).then(d => { setErr(''); setInfo(d) })
      .catch(e => setErr(e.message || 'Could not load enrollment options'))
  }, [leadId])
  useEffect(() => { load() }, [load])
  if (err) return <div className="eo-banner eo-banner--error">{err}</div>
  if (!info) return null
  const open = info.submissions.find(s => !s.resolved_at)
  return (
    <section className="eo-panel" data-testid="enroll-options">
      <h3 className="eo-sec" style={{ marginTop: 0 }}>Enroll this customer</h3>
      {sent && <div className="eo-note" role="status" data-testid="online-result"><strong>Sent to Chariot.</strong> {sent.meaning}</div>}
      <div className="eo-enroll-grid">
        <div className="eo-enroll-card" data-option="manual">
          <h4>Advisor enrolls manually</h4>
          <p className="eo-sub">Hand the customer to a supplier, enroll them on the supplier's side, then mark them enrolled once the supplier confirms.</p>
          <span className="eo-inline">
            <Link className="eo-btn" to="/energy/suppliers">Record a hand-off</Link>
            <Link className="eo-btn" to={`/rate-requests?open=${leadId}`}>Enroll on Rate Requests</Link>
          </span>
        </div>
        <div className="eo-enroll-card" data-option="online">
          <h4>Enroll online with Chariot</h4>
          {!info.eligible ? <p className="eo-note" data-testid="online-ineligible">{info.reason}</p>
            : open ? <OpenSubmission s={open} />
              : <OnlineForm leadId={leadId} info={info} onDone={r => { setSent(r); load() }} />}
        </div>
      </div>
    </section>
  )
}

function OpenSubmission({ s }) {
  return (
    <div data-testid="open-submission">
      <p className="eo-note"><strong>Already submitted</strong> {(s.submitted_at || '').slice(0, 10)}{s.plan_title ? ` · ${s.plan_title}` : ''}{s.provider_reference ? ` · Ref ${s.provider_reference}` : ''}</p>
      <p className="eo-note">{s.meaning}</p>
      <Link className="eo-btn" to="/energy/suppliers#chariot">Confirm the outcome</Link>
    </div>
  )
}

function OnlineForm({ leadId, info, onDone }) {
  const pre = info.prefill || {}
  const armed = info.status.submissions_enabled
  const [zip, setZip] = useState(pre.zip || '')
  const [plans, setPlans] = useState(null)
  const [plan, setPlan] = useState(null)
  const [street, setStreet] = useState('')
  const [addrs, setAddrs] = useState(null)
  const [meter, setMeter] = useState(null)
  const [svc, setSvc] = useState({ service_type: 'switching', service_start_date: '' })
  const [who, setWho] = useState({ first_name: pre.first_name || '', last_name: pre.last_name || '', email: pre.email || '', phone: pre.phone || '' })
  const [id, setId] = useState(EMPTY_ID)
  const [ok, setOk] = useState(false)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const look = (path, set) => {
    setMsg('')
    api.get(path).then(d => d.ok ? set(d.data) : setMsg(d.error || 'Chariot did not answer'))
      .catch(e => setMsg(e.message || 'Lookup failed'))
  }
  const submit = e => {
    e.preventDefault()
    setBusy(true); setMsg('')
    const body = { product_id: plan?.product_id ? String(plan.product_id) : '', plan, zip, esiid: meter?.esiid || '',
      ...svc, ...who, ...id, customer_authorized: ok }
    api.post(`/energy-ops/leads/${leadId}/chariot/enroll`, body)
      .then(r => { setId(EMPTY_ID); onDone(r) })
      .catch(e2 => setMsg(e2.status ? (e2.message || 'Not sent')
        : 'The connection dropped while sending. The request MAY have reached Chariot - check Suppliers > Online enrollments before trying again.'))
      .finally(() => { setBusy(false); setId(EMPTY_ID) })      // identity values never linger
  }
  return (
    <form className="eo-form" onSubmit={submit} data-testid="online-form" autoComplete="off">
      {!info.status.connected && <div className="eo-banner" data-testid="not-connected">Chariot is not connected on this server yet, so plans and meters cannot be looked up.</div>}
      {!armed && <div className="eo-banner" data-testid="switched-off">Online enrollment is switched off. You can prepare it, but nothing is sent to Chariot.</div>}

      <label>Service ZIP
        <span className="eo-inline"><input value={zip} onChange={e => setZip(e.target.value)} inputMode="numeric" maxLength={5} aria-label="Service ZIP" />
          <button type="button" className="eo-btn" onClick={() => look(`/energy-ops/leads/${leadId}/chariot/plans?zip=${encodeURIComponent(zip)}`, d => setPlans(d.plans))}>Find plans</button></span>
      </label>
      {plans && (plans.length === 0 ? <p className="eo-note">No plans for this ZIP.</p> : (
        <fieldset className="eo-choices" data-testid="plans"><legend className="eo-sub">Plan</legend>
          {plans.map(p => (
            <label key={p.product_id} className="eo-check">
              <input type="radio" name="plan" checked={plan?.product_id === p.product_id} onChange={() => setPlan(p)} />
              <span>{p.title} · {p.term_months} mo{p.price_1000_kwh ? ` · ${p.price_1000_kwh}¢ at 1000 kWh` : ''}
                {p.efl_url && <> · <a href={p.efl_url} target="_blank" rel="noreferrer">EFL</a></>}</span>
            </label>
          ))}
        </fieldset>))}

      <label>Service street address
        <span className="eo-inline"><input value={street} onChange={e => setStreet(e.target.value)} aria-label="Service street address" />
          <button type="button" className="eo-btn" onClick={() => look(`/energy-ops/leads/${leadId}/chariot/addresses?zip=${encodeURIComponent(zip)}&address=${encodeURIComponent(street)}`, d => setAddrs(d.addresses))}>Find meter</button></span>
      </label>
      {addrs && (addrs.length === 0 ? <p className="eo-note">No meter found at that address.</p> : (
        <fieldset className="eo-choices" data-testid="meters"><legend className="eo-sub">Meter (ESI ID)</legend>
          {addrs.map(a => (
            <label key={a.esiid} className="eo-check">
              <input type="radio" name="meter" disabled={a.switch_hold} checked={meter?.esiid === a.esiid} onChange={() => setMeter(a)} />
              <span>{a.full_address} · {a.esiid}{a.switch_hold ? ' · switch hold (cannot enroll)' : ''}</span>
            </label>
          ))}
        </fieldset>))}

      <label>Switching or moving
        <select value={svc.service_type} onChange={e => setSvc(s => ({ ...s, service_type: e.target.value }))}>
          <option value="switching">Switching supplier at this address</option><option value="moving">Moving in</option>
        </select></label>
      <label>Start date (MM/DD/YYYY)<input value={svc.service_start_date} onChange={e => setSvc(s => ({ ...s, service_start_date: e.target.value }))} placeholder="11/20/2026" /></label>
      <label>First name<input value={who.first_name} onChange={e => setWho(s => ({ ...s, first_name: e.target.value }))} /></label>
      <label>Last name<input value={who.last_name} onChange={e => setWho(s => ({ ...s, last_name: e.target.value }))} /></label>
      <label>Email<input value={who.email} onChange={e => setWho(s => ({ ...s, email: e.target.value }))} inputMode="email" /></label>
      <label>Phone<input value={who.phone} onChange={e => setWho(s => ({ ...s, phone: e.target.value }))} inputMode="tel" /></label>
      <label>Date of birth (MM/DD/YYYY)<input value={id.dob} onChange={e => setId(s => ({ ...s, dob: e.target.value }))} autoComplete="off" /></label>
      <label>SSN<input value={id.ssn} onChange={e => setId(s => ({ ...s, ssn: e.target.value }))} inputMode="numeric" autoComplete="off" type="password" /></label>
      <label>or driver licence<input value={id.drivers_licence} onChange={e => setId(s => ({ ...s, drivers_licence: e.target.value }))} autoComplete="off" type="password" /></label>
      <p className="eo-sub">Sent with: bills by email, English, billing address same as service, no autopay, no deposit paid online. Anything else (deposit, waiver, autopay) is handled with Chariot by a person. Identity numbers go to Chariot only and are not saved.</p>
      <label className="eo-check"><input type="checkbox" checked={ok} onChange={e => setOk(e.target.checked)} /> The customer authorized this enrollment and has this plan's EFL, Terms of Service and YRAC</label>
      <button type="submit" className="eo-btn eo-btn--primary" disabled={!armed || busy || !ok || !plan || !meter}>{busy ? 'Sending… (up to 2 minutes)' : 'Submit to Chariot'}</button>
      {msg && <div className="eo-banner eo-banner--error" role="status">{msg}</div>}
    </form>
  )
}
