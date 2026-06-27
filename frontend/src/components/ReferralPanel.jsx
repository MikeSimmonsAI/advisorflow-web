import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import './ReferralPanel.css'

const RELATIONSHIP_OPTIONS = [
  { value: 'spouse', label: 'Spouse' },
  { value: 'child', label: 'Child' },
  { value: 'parent', label: 'Parent' },
  { value: 'sibling', label: 'Sibling' },
  { value: 'decision_maker', label: 'Decision maker' },
  { value: 'power_of_attorney', label: 'Power of attorney' },
  { value: 'other_family', label: 'Other family' },
]

const RELATIONSHIP_LABELS = Object.fromEntries(RELATIONSHIP_OPTIONS.map((o) => [o.value, o.label]))

const TIER_OPTIONS = [
  { value: 'pre_need', label: 'Pre-Need' },
  { value: 'at_need', label: 'At-Need' },
  { value: 'imminent', label: 'Imminent' },
  { value: 'contract_sold', label: 'Contract Sold' },
  { value: 'new_inquiry', label: 'New Inquiry' },
]

/**
 * Real referral lead generation, per Mike's explicit, concrete
 * scenario: a permission-to-access form gives him a plus-one (e.g.
 * Deborah's daughter Lisa) he can message directly and work toward
 * their own pre-need conversation - not a notes field, a REAL separate
 * Lead record. This panel both adds new referrals and shows the two
 * directions: who this lead referred, and who referred this lead (if
 * anyone), since both matter when picking up a lead's full context.
 */
export default function ReferralPanel({ leadId }) {
  const navigate = useNavigate()
  const [referrals, setReferrals] = useState(null)
  const [showForm, setShowForm] = useState(false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [form, setForm] = useState({
    first_name: '', last_name: '', phone: '', email: '',
    relationship_type: 'child', tier: 'pre_need', notes: '',
  })

  function load() {
    api.get(`/leads/${leadId}/referrals`).then(setReferrals).catch(() => setReferrals({ referred: [], referred_by: null }))
  }

  useEffect(() => { load() }, [leadId])

  async function handleAdd(e) {
    e.preventDefault()
    setError('')

    if (!form.first_name.trim() || !form.last_name.trim()) {
      setError('First and last name are required.')
      return
    }
    if (!form.phone.trim() && !form.email.trim()) {
      setError('A phone number or email address is required.')
      return
    }

    setSaving(true)
    try {
      await api.post(`/leads/${leadId}/referrals`, {
        first_name: form.first_name.trim(),
        last_name: form.last_name.trim(),
        phone: form.phone.trim() || null,
        email: form.email.trim() || null,
        relationship_type: form.relationship_type,
        tier: form.tier,
        notes: form.notes.trim() || null,
      })
      setShowForm(false)
      setForm({ first_name: '', last_name: '', phone: '', email: '', relationship_type: 'child', tier: 'pre_need', notes: '' })
      load()
    } catch (err) {
      setError(err.message || 'Could not add this referral.')
    } finally {
      setSaving(false)
    }
  }

  if (!referrals) {
    return (
      <section className="panel">
        <div className="panel-header"><h2 className="panel-title">Referrals</h2></div>
        <div className="empty-state">Loading…</div>
      </section>
    )
  }

  return (
    <section className="panel">
      <div className="panel-header">
        <h2 className="panel-title">Referrals</h2>
        {!showForm && (
          <button className="btn btn--secondary" onClick={() => setShowForm(true)}>
            + Add referral
          </button>
        )}
      </div>

      {referrals.referred_by && (
        <div className="referral-referred-by">
          Referred by{' '}
          <button className="referral-link" onClick={() => navigate(`/leads/${referrals.referred_by.lead_id}`)}>
            {referrals.referred_by.first_name} {referrals.referred_by.last_name}
          </button>{' '}
          ({RELATIONSHIP_LABELS[referrals.referred_by.relationship_type] || referrals.referred_by.relationship_type})
        </div>
      )}

      {showForm ? (
        <form onSubmit={handleAdd} className="referral-form">
          <p className="referral-form-hint">
            This creates a real, separate lead — they'll get their own outreach and show up in your pipeline like anyone else.
          </p>
          <div className="referral-form-row">
            <label className="settings-label">
              First name
              <input className="settings-input" value={form.first_name} onChange={(e) => setForm({ ...form, first_name: e.target.value })} autoFocus />
            </label>
            <label className="settings-label">
              Last name
              <input className="settings-input" value={form.last_name} onChange={(e) => setForm({ ...form, last_name: e.target.value })} />
            </label>
          </div>
          <div className="referral-form-row">
            <label className="settings-label">
              Phone
              <input className="settings-input" type="tel" placeholder="(214) 555-0100" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
            </label>
            <label className="settings-label">
              Email
              <input className="settings-input" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </label>
          </div>
          <p className="referral-form-hint">At least one of phone or email is required.</p>
          <div className="referral-form-row">
            <label className="settings-label">
              Relationship
              <select className="settings-input" value={form.relationship_type} onChange={(e) => setForm({ ...form, relationship_type: e.target.value })}>
                {RELATIONSHIP_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </label>
            <label className="settings-label">
              Starting tier
              <select className="settings-input" value={form.tier} onChange={(e) => setForm({ ...form, tier: e.target.value })}>
                {TIER_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>{opt.label}</option>
                ))}
              </select>
            </label>
          </div>
          <label className="settings-label">
            Notes (optional)
            <textarea className="compose-textarea" rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
          </label>

          {error && <div className="compose-error">{error}</div>}

          <div className="settings-actions">
            <button type="button" className="btn btn--secondary" onClick={() => setShowForm(false)} disabled={saving}>Cancel</button>
            <button type="submit" className="btn btn--primary" disabled={saving}>
              {saving ? 'Adding…' : 'Add referral'}
            </button>
          </div>
        </form>
      ) : referrals.referred.length === 0 ? (
        <div className="empty-state">No referrals yet. Use "+ Add referral" for a permission-to-access plus-one.</div>
      ) : (
        <ul className="referral-list">
          {referrals.referred.map((r) => (
            <li key={r.referral_id} className="referral-list-item" onClick={() => navigate(`/leads/${r.lead_id}`)}>
              <div>
                <strong>{r.first_name} {r.last_name}</strong>
                <span className="referral-relationship-tag">{RELATIONSHIP_LABELS[r.relationship_type] || r.relationship_type}</span>
              </div>
              <span className="referral-list-contact mono">{r.phone || r.email || '—'}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
