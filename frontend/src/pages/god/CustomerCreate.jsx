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

const INDUSTRIES = [
  { value: 'funeral', label: 'Funeral / cemetery' },
  { value: 'fiber', label: 'Fiber / telecom' },
  { value: 'general', label: 'General' },
]

export default function CustomerCreate() {
  const nav = useNavigate()
  const [brands, setBrands] = useState([])
  const [f, setF] = useState({
    name: '', platform_id: '', industry: 'funeral', plan: 'trial',
    timezone: 'America/Chicago', phone: '',
    loc_name: '', loc_city: '', loc_state: '', loc_phone: '',
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get('/god/platform/overview')
      .then(d => setBrands(d.platforms))
      .catch(e => setErr(errText(e)))
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
        <button className="go-btn" onClick={() => nav('/god/platform')}>Cancel</button>
      </div>

      {err && <div className="go-err">{err}</div>}

      <section className="go-card go-pad">
        <h2 className="go-h2">Company</h2>

        <label className="go-label">Company name</label>
        <input className="go-input" value={f.name} onChange={set('name')}
               placeholder="e.g. Riverside Memorial" />

        <label className="go-label">Brand</label>
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
            <label className="go-label">Industry</label>
            <select className="go-input" value={f.industry} onChange={set('industry')}>
              {INDUSTRIES.map(i => <option key={i.value} value={i.value}>{i.label}</option>)}
            </select>
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

      <div className="go-actions">
        <button className="go-btn go-btn-primary" disabled={!ready || busy}
                onClick={submit}>
          {busy ? 'Creating…' : 'Create customer'}
        </button>
      </div>
    </div>
  )
}
