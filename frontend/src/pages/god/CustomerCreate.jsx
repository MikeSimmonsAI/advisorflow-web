/**
 * + CREATE CUSTOMER — the supported way to stand up a customer.
 *
 * Company and first location in one step, because a customer with no location
 * has nowhere to route a booking and we would only be sending the operator
 * straight to a second form. Everything after this — people, features,
 * communications — happens on the customer's own page, where it can be revisited.
 *
 * THE BRAND IS REQUIRED AND THE FORM SAYS WHY. A customer with no platform is
 * invisible to every scoped query in the system, so this is not a field to
 * leave blank and fix later.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { errText } from './GodOpsShared'
import './GodOps.css'

// THE INDUSTRY LIST IS NOT DECLARED HERE ANY MORE.
//
// It was three options long and defaulted to the first one, so an operator
// creating a customer in any other business either scrolled past a list that
// did not contain it or accepted a default that was wrong — and the new
// organization then inherited that vertical's lead tiers, appointment types
// and AI vocabulary on the day it was created.
//
// The list now comes from /org-settings/industries, which is the same registry
// the settings page and the backend provisioning path read, and the field
// starts EMPTY so the question has to be answered rather than defaulted.
const INDUSTRY_FALLBACK = [{ value: 'generic', label: 'General service business' }]

export default function CustomerCreate() {
  const nav = useNavigate()
  const [brands, setBrands] = useState([])
  const [industries, setIndustries] = useState(INDUSTRY_FALLBACK)
  const [f, setF] = useState({
    name: '', platform_id: '', industry: '', plan: 'trial',
    timezone: 'America/Chicago', phone: '',
    loc_name: '', loc_city: '', loc_state: '', loc_phone: '',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get('/god/platform/overview')
      .then(d => setBrands(d.platforms))
      .catch(e => setErr(errText(e)))
    // One registry, read by everything. If it cannot be reached the form still
    // works with the neutral option, which is the safe thing to create.
    api.get('/org-settings/industries')
      .then(d => setIndustries(d.industries || INDUSTRY_FALLBACK))
      .catch(() => setIndustries(INDUSTRY_FALLBACK))
  }, [])

  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })

  async function submit() {
    setBusy(true); setErr('')
    try {
      const body = {
        name: f.name.trim(),
        platform_id: f.platform_id,
        industry: f.industry,
        plan: f.plan,
        timezone: f.timezone,
        phone: f.phone.trim() || null,
      }
      if (f.loc_name.trim()) {
        body.primary_location = {
          name: f.loc_name.trim(),
          city: f.loc_city.trim() || null,
          state: f.loc_state.trim() || null,
          phone: f.loc_phone.trim() || null,
          timezone: f.timezone,
        }
      }
      const r = await api.post('/god/customers', body)
      nav('/god/customers/' + r.customer.id)
    } catch (e) { setErr(errText(e)) } finally { setBusy(false) }
  }

  const ready = f.name.trim() && f.platform_id

  return (
    <div className="go-wrap go-narrow">
      <div className="go-head">
        <div>
          <h1 className="go-h1">Create customer</h1>
          <p className="go-sub">
            This is the supported way to stand up a customer. No shell, no seed
            script.
          </p>
        </div>
        <button className="go-btn ghost" onClick={() => nav('/god/platform')}>Cancel</button>
      </div>

      {err && <div className="go-err">{err}</div>}

      <section className="go-card go-pad">
        <h2 className="go-h2">Company</h2>

        <label className="go-label">Company name <Req /></label>
        <input className="go-input" value={f.name} onChange={set('name')}
               placeholder="e.g. Riverside Memorial" />

        <label className="go-label">Brand <Req /></label>
        <select className="go-input" value={f.platform_id} onChange={set('platform_id')}>
          <option value="">Select a brand…</option>
          {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
        </select>
        <p className="go-hint">
          Required. A customer with no brand is excluded from every scoped list
          in the system, including yours.
        </p>

        <div className="go-two">
          <div>
            <label className="go-label">Business type</label>
            <select className="go-input" value={f.industry} onChange={set('industry')}>
              <option value="">Select a business type…</option>
              {industries.map(i => (
                <option key={i.value} value={i.value}>{i.label}</option>
              ))}
            </select>
            <p className="go-hint">
              Sets this customer's starting lead tiers, appointment types and AI
              vocabulary. Leave it unselected and they start neutral — never
              another industry's defaults.
              {f.industry && (industries.find(i => i.value === f.industry)?.segments || []).length > 0 && (
                <> Lines of business can be recorded during onboarding.</>
              )}
            </p>
          </div>
          <div>
            <label className="go-label">Timezone</label>
            <input className="go-input" value={f.timezone} onChange={set('timezone')} />
          </div>
        </div>

        <label className="go-label">Main phone</label>
        <input className="go-input" value={f.phone} onChange={set('phone')}
               placeholder="optional" />
      </section>

      <section className="go-card go-pad">
        <h2 className="go-h2">Primary location</h2>
        <p className="go-hint">
          Optional here, but a customer needs at least one location before it can
          be activated — bookings have to route somewhere.
        </p>

        <label className="go-label">Location name</label>
        <input className="go-input" value={f.loc_name} onChange={set('loc_name')}
               placeholder="e.g. Riverside Chapel" />

        <div className="go-two">
          <div>
            <label className="go-label">City</label>
            <input className="go-input" value={f.loc_city} onChange={set('loc_city')} />
          </div>
          <div>
            <label className="go-label">State</label>
            <input className="go-input go-input-sm" value={f.loc_state}
                   onChange={set('loc_state')} />
          </div>
        </div>

        <label className="go-label">Location phone</label>
        <input className="go-input" value={f.loc_phone} onChange={set('loc_phone')} />
      </section>

      <div className="go-actions" style={{ alignItems: 'center' }}>
        {!ready && !busy && (
          <span className="go-hint" style={{ margin: 0 }}>
            {!f.name.trim() && !f.platform_id
              ? 'Add a company name and choose a brand to continue.'
              : !f.name.trim() ? 'Add a company name to continue.'
              : 'Choose a brand to continue.'}
          </span>
        )}
        <button className="go-btn go-btn-primary" disabled={!ready || busy}
                onClick={submit}>
          {busy ? 'Creating…' : 'Create customer'}
        </button>
      </div>
    </div>
  )
}

/** The required marker. Colour is reinforcement — the asterisk and the
 *  accessible word are what carry it, so "required" survives for anyone who
 *  cannot separate the red from the grey. */
function Req() {
  return (
    <span style={{ color: 'var(--gm-red)', fontWeight: 700 }}>
      *<span className="gm-sr"> (required)</span>
    </span>
  )
}
