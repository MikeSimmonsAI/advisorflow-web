/**
 * ADD ONE HOMEOWNER - the by-hand way in, no spreadsheet.
 *
 * Saves ONE real homeowner (a Lead) in this roofing company through
 * POST /roofing-ops/leads: address on the record, roofing answers in the same
 * fields the importer fills. Saving sends nothing, starts no follow-up and
 * books nothing. A possible duplicate is shown, never silently merged.
 */
import { useEffect, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { BASE, useRoofing } from './RoofingShell'
import { ErrorNote, Icon, errText } from './ui'
import './roofing-ops.css'

export const SOURCES = ['Phone call', 'Website', 'Referral', 'Door visit', 'Past estimate', 'Other']
const BLANK = { first_name: '', last_name: '', phone: '', email: '', street_address: '', city: '', state: 'TX', zip_code: '',
  roof_issue: '', service_type: '', urgency: '', source: 'Phone call', owner_id: '', notes: '' }

export default function AddHomeowner({ onClose, onSaved, say }) {
  const { status } = useRoofing()
  const nav = useNavigate()
  const [f, setF] = useState(BLANK)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [dups, setDups] = useState(null)
  const [saved, setSaved] = useState(null)
  const [starting, setStarting] = useState(false)
  const box = useRef(null)
  // Keep the form / the result in view (on a phone the page is long).
  useEffect(() => { box.current?.scrollIntoView?.({ block: 'start', behavior: 'smooth' }) }, [saved])
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  const team = status?.team || []
  const canSave = f.first_name.trim() && (f.phone.trim() || f.email.trim() || f.street_address.trim())

  const save = async (allowDuplicate = false) => {
    setBusy(true); setErr('')
    try {
      const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, typeof v === 'string' ? (v.trim() || null) : v]))
      const r = await api.post('/roofing-ops/leads', { ...body, allow_duplicate: allowDuplicate })
      setSaved(r); setDups(null); onSaved && onSaved(r)
    } catch (e) {
      if (e.status === 409 && e.detail?.duplicates) setDups(e.detail.duplicates)
      else setErr(errText(e))
    } finally { setBusy(false) }
  }
  const startNow = async () => {
    setStarting(true)
    try {
      const ins = await api.post('/roofing-ops/inspections', { lead_id: saved.lead_id })
      nav(`${BASE}/inspections/${ins.id}`)
    } catch (e) { setErr(errText(e)); setStarting(false) }
  }
  const again = () => { setF({ ...BLANK, source: f.source, owner_id: f.owner_id }); setSaved(null); setErr(''); setDups(null) }

  if (saved) {
    return (
      <section ref={box} className="rf-panel rf-add rf-add--done" aria-label="Homeowner saved">
        <div className="rf-add__ok"><span className="rf-book__ok"><Icon name="check" size={26} /></span>
          <div><h2 className="rf-panel__title">{saved.name} is saved</h2>
            <p className="rf-muted">Added to this company’s homeowners. Nothing was sent and no follow-up was started.</p></div>
        </div>
        <div className="rf-add__actions">
          <Link className="rf-btn rf-btn--ghost rf-add__big" to={`${BASE}/homeowners/${saved.lead_id}`}><Icon name="users" size={18} />Open homeowner</Link>
          <Link className="rf-btn rf-btn--ghost rf-add__big" to={`${BASE}/homeowners/${saved.lead_id}?book=1`}><Icon name="calendar" size={18} />Book inspection</Link>
          <button className="rf-btn rf-btn--primary rf-add__big" disabled={starting} onClick={startNow}><Icon name="clipboard" size={18} />{starting ? 'Opening…' : 'Start inspection now'}</button>
          <button className="rf-btn rf-btn--ghost rf-add__big" onClick={again}><Icon name="plus" size={18} />Add another homeowner</button>
        </div>
        {err && <ErrorNote>{err}</ErrorNote>}
      </section>
    )
  }

  return (
    <section ref={box} className="rf-panel rf-add" aria-label="Add a homeowner">
      <header className="rf-add__head">
        <div><h2 className="rf-panel__title">Add a homeowner</h2>
          <p className="rf-panel__sub">Name plus one way to reach them or the property address. Everything else is optional.</p></div>
        {onClose && <button className="rf-btn rf-btn--ghost rf-btn--sm" onClick={onClose} aria-label="Close"><Icon name="close" size={16} /></button>}
      </header>
      <form className="rf-add__form" onSubmit={(e) => { e.preventDefault(); if (canSave && !busy) save(false) }}>
        <fieldset><legend>Homeowner</legend>
          <div className="rf-add__row">
            <label className="rf-label">First name *<input className="rf-input" autoFocus value={f.first_name} onChange={set('first_name')} autoComplete="off" /></label>
            <label className="rf-label">Last name<input className="rf-input" value={f.last_name} onChange={set('last_name')} autoComplete="off" /></label>
          </div>
          <div className="rf-add__row">
            <label className="rf-label">Phone<input className="rf-input" type="tel" inputMode="tel" value={f.phone} onChange={set('phone')} autoComplete="off" /></label>
            <label className="rf-label">Email<input className="rf-input" type="email" value={f.email} onChange={set('email')} autoComplete="off" /></label>
          </div>
        </fieldset>
        <fieldset><legend>Property</legend>
          <label className="rf-label">Street address<input className="rf-input" value={f.street_address} onChange={set('street_address')} autoComplete="off" /></label>
          <div className="rf-add__row rf-add__row--3">
            <label className="rf-label">City<input className="rf-input" value={f.city} onChange={set('city')} autoComplete="off" /></label>
            <label className="rf-label">State<input className="rf-input" value={f.state} maxLength={2} onChange={set('state')} autoComplete="off" /></label>
            <label className="rf-label">ZIP<input className="rf-input" inputMode="numeric" value={f.zip_code} maxLength={10} onChange={set('zip_code')} autoComplete="off" /></label>
          </div>
        </fieldset>
        <fieldset><legend>Roof</legend>
          <label className="rf-label">Roofing problem / reason for calling<input className="rf-input" value={f.roof_issue} onChange={set('roof_issue')} placeholder="e.g. Leak over the kitchen after the storm" /></label>
          <div className="rf-add__row">
            <label className="rf-label">Repair or replacement
              <select className="rf-select" value={f.service_type} onChange={set('service_type')}>
                <option value="">Not sure yet</option><option value="repair">Repair</option><option value="replacement">Replacement</option><option value="not_sure">Homeowner isn’t sure</option>
              </select></label>
            <label className="rf-label">Urgency
              <select className="rf-select" value={f.urgency} onChange={set('urgency')}>
                <option value="">—</option><option value="emergency">Emergency (active leak)</option><option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option>
              </select></label>
          </div>
        </fieldset>
        <fieldset><legend>Sales</legend>
          <div className="rf-add__row">
            <label className="rf-label">Lead source
              <select className="rf-select" value={f.source} onChange={set('source')}>{SOURCES.map((s) => <option key={s}>{s}</option>)}</select></label>
            {team.length > 0 ? (
              <label className="rf-label">Salesperson
                <select className="rf-select" value={f.owner_id} onChange={set('owner_id')}>
                  <option value="">Unassigned</option>{team.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
                </select></label>
            ) : <div />}
          </div>
          <label className="rf-label">Notes<textarea className="rf-textarea" rows={2} value={f.notes} onChange={set('notes')} placeholder="Gate code, best time to call, who referred them…" /></label>
        </fieldset>
        {dups && (
          <div className="rf-note rf-note--warn rf-add__dups" role="alert">
            <Icon name="alert" size={18} />
            <div><b>This looks like a homeowner you already have.</b>
              <ul>{dups.map((d) => (
                <li key={d.lead_id}><span><b>{d.name}</b> - {d.address || d.phone || ''} <span className="rf-muted">({d.reason})</span></span>
                  <Link className="rf-btn rf-btn--ghost rf-btn--sm" to={`${BASE}/homeowners/${d.lead_id}`}>Open existing</Link></li>
              ))}</ul>
              <button type="button" className="rf-btn rf-btn--ghost rf-btn--sm" disabled={busy} onClick={() => save(true)}>No - create a separate homeowner</button>
            </div>
          </div>
        )}
        {err && <ErrorNote>{err}</ErrorNote>}
        <p className="rf-muted rf-small">Saving doesn’t text, email or book anyone. Follow-up only starts if you start it, and only for homeowners who pass the contact checks.</p>
        <div className="rf-add__foot">
          {onClose && <button type="button" className="rf-btn rf-btn--ghost" onClick={onClose}>Cancel</button>}
          <button type="submit" className="rf-btn rf-btn--primary" disabled={!canSave || busy}><Icon name="check" size={18} />{busy ? 'Saving…' : 'Save homeowner'}</button>
        </div>
      </form>
    </section>
  )
}
