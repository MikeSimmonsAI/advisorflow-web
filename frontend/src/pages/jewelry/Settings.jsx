import { useState } from 'react'
import { api } from '../../api/client'
import { useJewelry } from './JewelryShell'
import { Empty, ErrorNote, Field, Loading, Pill, ago, errText, useApi, useToast } from './jwui'

const DAYS = [['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'], ['fri', 'Friday'], ['sat', 'Saturday'], ['sun', 'Sunday']]

function Profile({ status, reload, toast }) {
  const p = status.profile
  const ro = !status.is_owner
  const [f, setF] = useState(() => ({ business_name: p.business_name_override || '', street_address: p.street_address || '', city_state_zip: p.city_state_zip || '',
    business_phone: p.business_phone || '', business_email: p.business_email || '', business_hours: { ...p.business_hours }, hours_confirmed: p.hours_confirmed,
    services: (p.services || []).join('\n'), appointment_types: (p.appointment_types || []).join('\n'), appointment_minutes: p.appointment_minutes,
    approved_answers: p.approved_answers?.length ? p.approved_answers : [{ q: '', a: '' }] }))
  const [err, setErr] = useState(null)
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const save = async () => {
    setErr(null)
    try {
      await api.put('/jewelry-ops/profile', { ...f, services: f.services.split('\n').map(s => s.trim()).filter(Boolean),
        appointment_types: f.appointment_types.split('\n').map(s => s.trim()).filter(Boolean), appointment_minutes: Number(f.appointment_minutes),
        approved_answers: f.approved_answers.filter(a => a.q.trim() && a.a.trim()) })
      await reload(); toast('Business information saved.')
    } catch (e) { setErr(errText(e)) }
  }
  return (
    <div className="jw-card">
      <div className="jw-card__head"><div><h3>Business information</h3><small>{ro ? 'Only the owner can change this.' : 'Used on screens and by the AI receptionist. Only what’s confirmed here is ever stated to a customer.'}</small></div></div>
      <fieldset disabled={ro} className="jw-fieldset">
        <div className="jw-form2">
          <Field label="Business name"><input className="jw-input" value={f.business_name} onChange={set('business_name')} placeholder={status.organization_name} /></Field>
          <Field label="Phone"><input className="jw-input" value={f.business_phone} onChange={set('business_phone')} /></Field>
          <Field label="Street address"><input className="jw-input" value={f.street_address} onChange={set('street_address')} /></Field>
          <Field label="City, state, ZIP"><input className="jw-input" value={f.city_state_zip} onChange={set('city_state_zip')} /></Field>
          <Field label="Email"><input className="jw-input" value={f.business_email} onChange={set('business_email')} /></Field>
          <Field label="Default appointment length (minutes)"><input className="jw-input" type="number" min="10" max="480" value={f.appointment_minutes} onChange={set('appointment_minutes')} /></Field>
        </div>
        <Field label="Business hours" hint="Leave a day blank if not confirmed. The assistant states hours only when “confirmed by owner” is checked.">
          <div className="jw-hours">{DAYS.map(([k, l]) => (
            <label key={k}><span>{l}</span><input className="jw-input jw-input--sm" value={f.business_hours[k] || ''} placeholder="e.g. 10am–6pm or Closed"
              onChange={e => setF({ ...f, business_hours: { ...f.business_hours, [k]: e.target.value } })} /></label>
          ))}</div>
        </Field>
        <label className="jw-check"><input type="checkbox" checked={!!f.hours_confirmed} onChange={e => setF({ ...f, hours_confirmed: e.target.checked })} /> Hours confirmed by owner</label>
        <div className="jw-form2">
          <Field label="Services offered" hint="One per line"><textarea className="jw-input" rows={5} value={f.services} onChange={set('services')} /></Field>
          <Field label="Appointment types" hint="One per line"><textarea className="jw-input" rows={5} value={f.appointment_types} onChange={set('appointment_types')} /></Field>
        </div>
        <Field label="Approved answers for the AI receptionist" hint="The assistant uses these word for word when a question matches.">
          {f.approved_answers.map((a, i) => (
            <div key={i} className="jw-qa">
              <input className="jw-input" placeholder="Customer asks…" value={a.q} onChange={e => { const x = [...f.approved_answers]; x[i] = { ...a, q: e.target.value }; setF({ ...f, approved_answers: x }) }} />
              <input className="jw-input" placeholder="Approved answer…" value={a.a} onChange={e => { const x = [...f.approved_answers]; x[i] = { ...a, a: e.target.value }; setF({ ...f, approved_answers: x }) }} />
            </div>
          ))}
          {!ro && <button type="button" className="jw-btn jw-btn--sm" onClick={() => setF({ ...f, approved_answers: [...f.approved_answers, { q: '', a: '' }] })}>+ Add answer</button>}
        </Field>
      </fieldset>
      <ErrorNote>{err}</ErrorNote>
      {!ro && <div className="jw-row jw-row--end"><button className="jw-btn jw-btn--gold" onClick={save}>Save business information</button></div>}
    </div>
  )
}

export default function Settings() {
  const { status, reload } = useJewelry()
  const { data: team, error: teamErr } = useApi('/jewelry-ops/team')
  const { data: act } = useApi('/jewelry-ops/activity?limit=60')
  const [toast, toastNode] = useToast()
  return (
    <div>
      <div className="jw-head"><div><div className="jw-eyebrow">Settings & Team</div><h1>Workspace settings</h1></div></div>
      <div className="jw-grid jw-grid--main">
        <Profile status={status} reload={reload} toast={toast} />
        <div>
          <div className="jw-card">
            <div className="jw-card__head"><h3>Connections</h3></div>
            <ul className="jw-checks">
              <li><span>✕</span><div><b>Messaging (texts & email)</b><small>Not connected. Nothing is sent from this workspace.</small></div></li>
              <li className={status.texts_on ? 'ok' : ''}><span>{status.texts_on ? '✓' : '✕'}</span><div><b>Automated texting switch</b><small>{status.texts_on ? 'On' : 'Off'} · changed only by the platform owner</small></div></li>
              <li><span>✕</span><div><b>Point of sale / inventory feed</b><small>Not connected. Transactions and items are entered by staff.</small></div></li>
              <li><span>✕</span><div><b>AI receptionist live channel</b><small>Not connected. Test mode only.</small></div></li>
            </ul>
          </div>
          <div className="jw-card">
            <div className="jw-card__head"><h3>Team</h3></div>
            <ErrorNote>{teamErr}</ErrorNote>
            {!team ? <Loading /> : (
              <>
                {team.members.length === 0 ? <Empty title="No team members yet" /> : (
                  <ul className="jw-list">{team.members.map(m => <li key={m.id}><div><b>{m.name}</b><small>{m.email}</small></div><Pill>{m.role_label}</Pill></li>)}</ul>
                )}
                <dl className="jw-defs">{team.roles.map(r => <div key={r.role}><dt>{r.label}</dt><dd>{r.can}</dd></div>)}</dl>
                <p className="jw-note">{team.invite_detail}</p>
              </>
            )}
          </div>
          <div className="jw-card">
            <div className="jw-card__head"><h3>Activity log</h3></div>
            {!act ? <Loading /> : act.entries.length === 0 ? <Empty title="No activity yet" /> : (
              <ul className="jw-list jw-list--dense">{act.entries.map(e => <li key={e.id}><div><b>{e.action.replace('jewelry.', '').replace(/[._]/g, ' ')}</b><small>{e.actor}</small></div><small>{ago(e.at)}</small></li>)}</ul>
            )}
          </div>
        </div>
      </div>
      {toastNode}
    </div>
  )
}
