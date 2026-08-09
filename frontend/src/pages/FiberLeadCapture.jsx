import { useState } from 'react'
import { api } from '../api/client'
import './FiberLeadCapture.css'

const SPEED_OPTIONS = [
  { value: '', label: 'Current speed (optional)' },
  { value: 'under_100', label: 'Under 100 Mbps' },
  { value: '100_300', label: '100–300 Mbps' },
  { value: '300_500', label: '300–500 Mbps' },
  { value: '500_1000', label: '500 Mbps – 1 Gbps' },
  { value: 'over_1gig', label: 'Over 1 Gbps' },
  { value: 'unknown', label: 'Not sure' },
]

const TIER_OPTIONS = [
  { value: '', label: 'Interested plan (optional)' },
  { value: '500mb', label: '500 Mbps Home' },
  { value: '1gb', label: '1 Gbps Home' },
  { value: '2gb', label: '2 Gbps Home' },
  { value: 'business', label: 'Business Plan' },
]

export default function FiberLeadCapture() {
  const [form, setForm] = useState({
    first_name: '', last_name: '', phone: '', email: '',
    service_address: '', current_provider: '', current_speed: '',
    interested_tier: '', notes: '', verbal_sms_consent: false,
  })
  const [submitting, setSubmitting] = useState(false)
  const [result, setResult] = useState(null)   // { status, first_name, last_name, lead_id }
  const [error, setError] = useState('')

  function set(field, val) {
    setForm(f => ({ ...f, [field]: val }))
    if (error) setError('')
  }

  async function handleSubmit() {
    if (!form.first_name.trim()) return setError('First name is required.')
    if (!form.last_name.trim()) return setError('Last name is required.')
    if (!form.phone.trim()) return setError('Phone number is required.')
    if (!form.verbal_sms_consent) return setError('You must confirm verbal SMS consent before saving.')

    setSubmitting(true)
    setError('')
    try {
      const data = await api.post('/fiber-leads', {
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim(),
        phone: form.phone.trim(),
        email: form.email.trim() || null,
        service_address: form.service_address.trim() || null,
        current_provider: form.current_provider.trim() || null,
        current_speed: form.current_speed || null,
        interested_tier: form.interested_tier || null,
        notes: form.notes.trim() || null,
        verbal_sms_consent: true,
      })
      setResult(data)
    } catch (err) {
      setError(err.message || 'Something went wrong. Try again.')
    } finally {
      setSubmitting(false)
    }
  }

  function reset() {
    setForm({
      first_name: '', last_name: '', phone: '', email: '',
      service_address: '', current_provider: '', current_speed: '',
      interested_tier: '', notes: '', verbal_sms_consent: false,
    })
    setResult(null)
    setError('')
  }

  if (result) {
    const isNew = result.status === 'created'
    return (
      <div className="fiber-capture">
        <div className="fiber-success">
          <div className="fiber-success__icon">{isNew ? '✅' : '🔁'}</div>
          <div className="fiber-success__name">
            {result.first_name} {result.last_name}
          </div>
          <p className="fiber-success__sub">
            {isNew
              ? "Lead captured! Added to CRM as a Prospect."
              : "This customer already exists. Their record is up to date."}
          </p>
          <button className="fiber-success__another" onClick={reset}>
            + Capture Another Lead
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="fiber-capture">
      <div className="fiber-capture__header">
        <h1 className="fiber-capture__title">📡 New Fiber Lead</h1>
        <p className="fiber-capture__sub">Fill this out after getting a verbal yes at the door.</p>
      </div>

      <div className="fiber-form">
        <div className="fiber-row">
          <div className="fiber-field">
            <label>First Name *</label>
            <input
              type="text" placeholder="First name"
              value={form.first_name}
              onChange={e => set('first_name', e.target.value)}
              autoComplete="given-name"
            />
          </div>
          <div className="fiber-field">
            <label>Last Name *</label>
            <input
              type="text" placeholder="Last name"
              value={form.last_name}
              onChange={e => set('last_name', e.target.value)}
              autoComplete="family-name"
            />
          </div>
        </div>

        <div className="fiber-row">
          <div className="fiber-field">
            <label>Phone *</label>
            <input
              type="tel" placeholder="(555) 000-0000"
              value={form.phone}
              onChange={e => set('phone', e.target.value)}
              autoComplete="tel"
              inputMode="tel"
            />
          </div>
          <div className="fiber-field">
            <label>Email</label>
            <input
              type="email" placeholder="email@example.com"
              value={form.email}
              onChange={e => set('email', e.target.value)}
              autoComplete="email"
              inputMode="email"
            />
          </div>
        </div>

        <div className="fiber-field">
          <label>Service Address</label>
          <input
            type="text" placeholder="123 Main St, City, State ZIP"
            value={form.service_address}
            onChange={e => set('service_address', e.target.value)}
            autoComplete="street-address"
          />
        </div>

        <div className="fiber-row">
          <div className="fiber-field">
            <label>Current Provider</label>
            <input
              type="text" placeholder="e.g. Comcast, AT&T"
              value={form.current_provider}
              onChange={e => set('current_provider', e.target.value)}
            />
          </div>
          <div className="fiber-field">
            <label>Current Speed</label>
            <select value={form.current_speed} onChange={e => set('current_speed', e.target.value)}>
              {SPEED_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </div>
        </div>

        <div className="fiber-field">
          <label>Interested Plan</label>
          <select value={form.interested_tier} onChange={e => set('interested_tier', e.target.value)}>
            {TIER_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>

        <div className="fiber-field">
          <label>Notes (optional)</label>
          <textarea
            placeholder="Gate code, best time to call, objections, anything useful..."
            value={form.notes}
            onChange={e => set('notes', e.target.value)}
          />
        </div>

        <div
          className="fiber-consent"
          onClick={() => set('verbal_sms_consent', !form.verbal_sms_consent)}
        >
          <input
            type="checkbox"
            checked={form.verbal_sms_consent}
            onChange={e => set('verbal_sms_consent', e.target.checked)}
            onClick={e => e.stopPropagation()}
          />
          <span>
            <strong>I confirm the customer verbally consented</strong> to receive SMS messages
            from our team regarding their service inquiry. They were informed they can reply
            STOP to opt out at any time.
          </span>
        </div>

        {error && <div className="fiber-error">⚠️ {error}</div>}

        <button
          className="fiber-submit"
          onClick={handleSubmit}
          disabled={submitting}
        >
          {submitting ? 'Saving...' : '💾 Save Lead'}
        </button>
      </div>
    </div>
  )
}
