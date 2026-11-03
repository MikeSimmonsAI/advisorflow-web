// #452 stage 2 - the cleaning client's onboarding, shared by her portal
// ("My business") and the operator's Onboarding page. Draft is fine; the
// readiness list says plainly what's still missing before outreach can start.
import { useEffect, useState } from 'react'
import { errText } from './ui'

const list = v => (Array.isArray(v) ? v.join(', ') : v || '')
const split = s => String(s || '').split(/[,\n]/).map(x => x.trim()).filter(Boolean)

export function Readiness({ r }) {
  if (!r) return null
  return (
    <section className="aq-card">
      <h2>Onboarding {r.launch_ready ? <span className="aq-pill aq-pill--ok">Ready to launch</span> : <span className="aq-pill">Draft</span>}</h2>
      {r.launch_ready ? <p className="aq-lede">Everything outreach needs is filled in.</p>
        : <p className="aq-lede">Still needed before any real business is emailed: <b>{r.missing.join(', ')}</b>.</p>}
      <ul className="aq-checklist">
        {r.required.map(c => <li key={c.key}><span className={`aq-tick ${c.ok ? 'on' : ''}`} />{c.label}</li>)}
      </ul>
      <small style={{ color: 'var(--aq-sub)' }}>Helpful but optional: {r.optional.filter(c => !c.ok).map(c => c.label).join(', ') || 'all filled in'}</small>
    </section>
  )
}

export function BusinessForm({ profile, segments, canEdit, ownerControls, onSave }) {
  const [f, setF] = useState(null)
  const [busy, setBusy] = useState(false)
  const [fail, setFail] = useState(null)
  useEffect(() => {
    if (!profile) return
    setF({ ...profile, service_area: list(profile.service_area), services: list(profile.services),
      service_zips: list(profile.service_zips), service_states: list(profile.service_states),
      qualification_questions: (profile.qualification_questions || []).join('\n') })
  }, [profile])
  if (!f) return null
  const set = k => e => setF({ ...f, [k]: e.target.value })
  const toggle = k => setF({ ...f, segments: f.segments.includes(k) ? f.segments.filter(s => s !== k) : [...f.segments, k] })
  const field = (k, label, help, multi) => (
    <div className="aq-field"><label htmlFor={`aqb-${k}`}>{label}</label>
      {multi ? <textarea id={`aqb-${k}`} value={f[k] || ''} onChange={set(k)} disabled={!canEdit} />
        : <input id={`aqb-${k}`} value={f[k] ?? ''} onChange={set(k)} disabled={!canEdit} />}
      {help && <small>{help}</small>}</div>
  )
  const save = async () => {
    setBusy(true); setFail(null)
    const body = {
      business_name: f.business_name, owner_name: f.owner_name, contact_phone: f.contact_phone,
      contact_email: f.contact_email, website: f.website, services: split(f.services), segments: f.segments,
      service_area: split(f.service_area), service_zips: split(f.service_zips), service_states: split(f.service_states),
      service_radius_miles: f.service_radius_miles === '' ? null : f.service_radius_miles,
      timezone: f.timezone, recurring_pref: f.recurring_pref || null, capacity_note: f.capacity_note,
      min_job_value: f.min_job_value === '' ? null : f.min_job_value, availability_note: f.availability_note,
      appointment_minutes: Number(f.appointment_minutes), appointment_attendee: f.appointment_attendee,
      business_description: f.business_description, approved_offer: f.approved_offer,
      qualification_questions: String(f.qualification_questions || '').split('\n').map(q => q.trim()).filter(Boolean),
      insurance_note: f.insurance_note, certifications_note: f.certifications_note, mailing_address: f.mailing_address,
    }
    if (ownerControls) body.offer_type = f.offer_type
    try { await onSave(body) } catch (e) { setFail(errText(e)) } finally { setBusy(false) }
  }
  return (
    <>
      <div className="aq-grid aq-two">
        <section className="aq-card">
          <h2>The business</h2>
          {field('business_name', 'Business name')}
          {field('owner_name', 'Contact name')}
          {field('contact_phone', 'Phone')}
          {field('contact_email', 'Email')}
          {field('website', 'Website')}
          {field('mailing_address', 'Business mailing address', 'Printed at the bottom of every outreach email (required by law)')}
          {field('services', 'Services offered', 'Separate with commas')}
          {field('business_description', 'Short business description', 'In your own words. Outreach never claims more than this.', true)}
          {field('approved_offer', 'Approved offer', 'Exactly what outreach may offer, e.g. "a free walkthrough and written quote"', true)}
          {field('insurance_note', 'Insurance', 'Only what you can show proof of', true)}
          {field('certifications_note', 'Certifications', 'Only what you actually hold', true)}
        </section>
        <section className="aq-card">
          <h2>Who and where</h2>
          <div className="aq-field"><label>Kinds of customers wanted</label>
            <div className="aq-checks">{segments.map(s => (
              <label key={s.key}><input type="checkbox" checked={f.segments.includes(s.key)} onChange={() => toggle(s.key)} disabled={!canEdit} />{s.label}</label>))}</div></div>
          <div className="aq-field"><label htmlFor="aqb-rec">Recurring or one-time work</label>
            <select id="aqb-rec" value={f.recurring_pref || ''} onChange={set('recurring_pref')} disabled={!canEdit}>
              <option value="">Not set</option><option value="recurring">Recurring contracts</option>
              <option value="one_time">One-time jobs</option><option value="both">Both</option></select></div>
          {field('min_job_value', 'Smallest monthly contract ($)')}
          {field('service_states', 'States served', 'Two-letter codes. Contiguous US only, not California.')}
          {field('service_area', 'Cities served', 'Separate with commas')}
          {field('service_zips', 'ZIP codes served (optional)')}
          {field('service_radius_miles', 'Radius in miles (optional)')}
          {field('timezone', 'Time zone', 'e.g. America/Chicago')}
          <h2 style={{ marginTop: 18 }}>Appointments</h2>
          {field('appointment_attendee', 'Who meets prospects and closes')}
          {field('capacity_note', 'Capacity', 'e.g. room for 3 new recurring accounts this month', true)}
          {field('availability_note', 'Usual availability', 'Your connected calendar is always checked before a time is offered.', true)}
          <div className="aq-field"><label htmlFor="aqb-mins">Walkthrough length</label>
            <select id="aqb-mins" value={f.appointment_minutes} onChange={set('appointment_minutes')} disabled={!canEdit}>
              {[15, 30, 45, 60, 90, 120].map(m => <option key={m} value={m}>{m} minutes</option>)}</select></div>
          {field('qualification_questions', 'Qualification questions', 'One per line, e.g. "How many square feet?"', true)}
          {ownerControls && <div className="aq-field"><label htmlFor="aqb-offer">Offer (owner only)</label>
            <select id="aqb-offer" value={f.offer_type} onChange={set('offer_type')}>
              <option value="free_pilot">Free 30-day pilot</option><option value="growth_pilot_250">$250 / 30-day Growth Pilot</option></select>
            <small>Choose the offer arranged for this client. No billing action happens here.</small></div>}
        </section>
      </div>
      {fail && <div className="aq-err">{fail}</div>}
      {canEdit && <div className="aq-actions"><button className="aq-btn aq-btn--primary" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save'}</button></div>}
    </>
  )
}
