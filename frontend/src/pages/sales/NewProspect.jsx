/**
 * New Prospect — creates a real opportunity via POST /sales/opportunities.
 *
 * This is the ONLY way a prospect enters the production pipeline. No seeding,
 * no fixtures, no demo rows: production stays empty until a salesperson types
 * a real company in here.
 */
import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { ErrorBar } from './parts'

export default function NewProspect({ onClose, onCreated }) {
  const [packages, setPackages] = useState([])
  const [form, setForm] = useState({
    company_name: '', contact_name: '', phone: '', email: '',
    website: '', industry: '', source: '', package_interest_id: '',
    next_action: 'First contact', next_action_due_at: '',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    api.get('/sales/packages').then(setPackages).catch(() => setPackages([]))
  }, [])

  function set(k, v) { setForm(f => ({ ...f, [k]: v })) }

  async function submit(e) {
    e.preventDefault()
    if (!form.company_name.trim()) { setError('Company name is required.'); return }
    setSaving(true); setError(null)
    try {
      const body = { ...form }
      Object.keys(body).forEach(k => { if (body[k] === '') delete body[k] })
      if (body.next_action_due_at) {
        body.next_action_due_at = new Date(body.next_action_due_at).toISOString()
      }
      const opp = await api.post('/sales/opportunities', body)
      onCreated(opp)
    } catch (err) {
      setError(err.message || 'Could not create the prospect.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="sw-modal-back" onMouseDown={e => { if (e.target === e.currentTarget) onClose() }}>
      <form className="sw-modal" onSubmit={submit}>
        <div className="sw-card-h">
          <div><h3>NEW PROSPECT</h3><small>Starts at the Prospect stage</small></div>
        </div>
        <div className="sw-card-b">
          <ErrorBar error={error} />

          <div className="sw-field">
            <label>COMPANY *</label>
            <input className="sw-input" autoFocus value={form.company_name}
                   onChange={e => set('company_name', e.target.value)} />
          </div>

          <div className="sw-grid-even">
            <div className="sw-field">
              <label>CONTACT NAME</label>
              <input className="sw-input" value={form.contact_name}
                     onChange={e => set('contact_name', e.target.value)} />
            </div>
            <div className="sw-field">
              <label>INDUSTRY</label>
              <input className="sw-input" value={form.industry}
                     onChange={e => set('industry', e.target.value)} />
            </div>
            <div className="sw-field">
              <label>PHONE</label>
              <input className="sw-input" value={form.phone}
                     onChange={e => set('phone', e.target.value)} />
            </div>
            <div className="sw-field">
              <label>EMAIL</label>
              <input className="sw-input" type="email" value={form.email}
                     onChange={e => set('email', e.target.value)} />
            </div>
            <div className="sw-field">
              <label>WEBSITE</label>
              <input className="sw-input" value={form.website}
                     onChange={e => set('website', e.target.value)} />
            </div>
            <div className="sw-field">
              <label>SOURCE</label>
              <input className="sw-input" value={form.source}
                     placeholder="referral, inbound, scraper…"
                     onChange={e => set('source', e.target.value)} />
            </div>
          </div>

          <div className="sw-field">
            <label>PACKAGE INTEREST</label>
            <select className="sw-select" value={form.package_interest_id}
                    onChange={e => set('package_interest_id', e.target.value)}>
              <option value="">Not yet known</option>
              {packages.map(p => (
                <option key={p.id} value={p.id}>
                  {p.name}{p.price != null ? ' · $' + Number(p.price).toLocaleString() : ' · custom'}
                </option>
              ))}
            </select>
          </div>

          <div className="sw-grid-even">
            <div className="sw-field">
              <label>NEXT ACTION</label>
              <input className="sw-input" value={form.next_action}
                     onChange={e => set('next_action', e.target.value)} />
            </div>
            <div className="sw-field">
              <label>DUE</label>
              <input className="sw-input" type="date" value={form.next_action_due_at}
                     onChange={e => set('next_action_due_at', e.target.value)} />
            </div>
          </div>

          <div className="sw-flex sw-mt" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="sw-btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="sw-btn sw-primary" disabled={saving}>
              {saving ? 'Creating…' : 'Create Prospect'}
            </button>
          </div>
        </div>
      </form>
    </div>
  )
}
