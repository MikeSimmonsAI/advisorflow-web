import { useState, useEffect } from 'react'
import { api } from '../api/client'
import PageShell from '../components/PageShell'
import './ProvisionClient.css'

function slugify(str) {
  return str.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

const COLOR_OPTIONS = [
  { value: '#2fb6ff', label: 'Blue' },
  { value: '#1ef0a8', label: 'Teal' },
  { value: '#f59e0b', label: 'Amber' },
  { value: '#ef4444', label: 'Red' },
  { value: '#8b5cf6', label: 'Purple' },
  { value: '#10b981', label: 'Green' },
  { value: '#f97316', label: 'Orange' },
]

function ColorPicker({ field, value, onChange }) {
  return (
    <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
      {COLOR_OPTIONS.map(c => (
        <button key={c.value} title={c.label}
          onClick={() => onChange(field, c.value)}
          style={{
            width: 28, height: 28, borderRadius: '50%', background: c.value, border: 'none',
            cursor: 'pointer',
            outline: value === c.value ? '2px solid white' : 'none',
            boxShadow: value === c.value ? `0 0 0 3px ${c.value}` : 'none',
          }} />
      ))}
      <input type="color" value={value || '#2fb6ff'}
        onChange={e => onChange(field, e.target.value)}
        style={{ width: 32, height: 32, border: 'none', background: 'none', cursor: 'pointer' }} />
    </div>
  )
}

// ── Edit Org Modal ─────────────────────────────────────────────────────────
function EditOrgModal({ org, onClose, onSaved }) {
  const [form, setForm] = useState({
    name: org.name || '',
    industry: org.industry || 'funeral',
    plan: org.plan || 'trial',
    is_active: org.is_active !== false,
    brand_name: org.brand_name || '',
    brand_color_primary: org.brand_color_primary || '#2fb6ff',
    brand_color_accent: org.brand_color_accent || '#1ef0a8',
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  function setColor(field, value) {
    setForm(f => ({ ...f, [field]: value }))
  }

  async function handleSave() {
    setError('')
    if (!form.name.trim()) { setError('Name is required'); return }
    setSaving(true)
    try {
      const updated = await api.put(`/admin/organizations/${org.id}`, {
        name: form.name.trim(),
        industry: form.industry,
        plan: form.plan,
        is_active: form.is_active,
        brand_name: form.brand_name.trim() || null,
        brand_color_primary: form.brand_color_primary || null,
        brand_color_accent: form.brand_color_accent || null,
      })
      onSaved(updated)
    } catch (err) {
      setError(err.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={e => e.target === e.currentTarget && onClose()}>
      <div className="modal-box" style={{ maxWidth: 520 }}>
        <div className="modal-header">
          <h3>Edit — {org.name}</h3>
          <button className="modal-close" onClick={onClose}>✕</button>
        </div>
        <div className="modal-body">
          <div className="modal-form">

            <label>Organization Name
              <input value={form.name}
                onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                placeholder="Acme Roofing Co." />
            </label>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <label>Industry
                <select value={form.industry}
                  onChange={e => setForm(f => ({ ...f, industry: e.target.value }))}>
                  <option value="funeral">Funeral</option>
                  <option value="roofing">Roofing</option>
                  <option value="insurance">Insurance</option>
                  <option value="real_estate">Real Estate</option>
                  <option value="dental">Dental</option>
                  <option value="legal">Legal</option>
                  <option value="home_services">Home Services</option>
                </select>
              </label>
              <label>Plan
                <select value={form.plan}
                  onChange={e => setForm(f => ({ ...f, plan: e.target.value }))}>
                  <option value="trial">Trial</option>
                  <option value="standard">Standard ($299/mo)</option>
                  <option value="enterprise">Enterprise</option>
                </select>
              </label>
            </div>

            <label>Brand Name <span style={{ color: 'var(--text-tertiary)', fontWeight: 400, fontSize: 12 }}>(shows in sidebar)</span>
              <input value={form.brand_name}
                onChange={e => setForm(f => ({ ...f, brand_name: e.target.value }))}
                placeholder={form.name} />
            </label>

            <label>Primary color
              <div style={{ marginTop: 6 }}>
                <ColorPicker field="brand_color_primary" value={form.brand_color_primary} onChange={setColor} />
              </div>
            </label>

            <label>Accent color
              <div style={{ marginTop: 6 }}>
                <ColorPicker field="brand_color_accent" value={form.brand_color_accent} onChange={setColor} />
              </div>
            </label>

            {/* Live preview */}
            <div style={{
              background: form.brand_color_primary, borderRadius: 8, padding: '10px 16px',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            }}>
              <span style={{ color: '#fff', fontWeight: 700, fontSize: 14 }}>
                {form.brand_name || form.name || 'Brand Preview'}
              </span>
              <span style={{ color: form.brand_color_accent, fontWeight: 600, fontSize: 13 }}>● Live</span>
            </div>

            <label style={{ display: 'flex', flexDirection: 'row', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
              <input type="checkbox" checked={form.is_active}
                onChange={e => setForm(f => ({ ...f, is_active: e.target.checked }))} />
              <span>Active (uncheck to deactivate)</span>
            </label>

            {error && <div className="form-error">{error}</div>}

            <div className="modal-actions">
              <button className="btn btn--secondary" onClick={onClose}>Cancel</button>
              <button className="btn btn--primary" onClick={handleSave} disabled={saving}>
                {saving ? 'Saving…' : 'Save changes'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Main Page ──────────────────────────────────────────────────────────────
export default function ProvisionClient() {
  const [form, setForm] = useState({
    org_name: '',
    org_slug: '',
    industry: 'funeral',
    plan: 'trial',
    supervisor_full_name: '',
    supervisor_email: '',
    supervisor_password: '',
    brand_name: '',
    brand_logo_url: '',
    brand_color_primary: '#2fb6ff',
    brand_color_accent: '#1ef0a8',
  })

  const [autoSlug, setAutoSlug] = useState(true)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [orgs, setOrgs] = useState([])
  const [orgsLoading, setOrgsLoading] = useState(true)
  const [editOrg, setEditOrg] = useState(null)

  function loadOrgs() {
    api.get('/admin/organizations')
      .then(setOrgs)
      .catch(() => {})
      .finally(() => setOrgsLoading(false))
  }

  useEffect(() => { loadOrgs() }, [])

  function handleOrgName(e) {
    const val = e.target.value
    setForm(f => ({
      ...f,
      org_name: val,
      org_slug: autoSlug ? slugify(val) : f.org_slug,
      brand_name: f.brand_name === '' || f.brand_name === f.org_name ? val : f.brand_name,
    }))
  }

  function handleSlug(e) {
    setAutoSlug(false)
    setForm(f => ({ ...f, org_slug: e.target.value }))
  }

  function handleChange(e) {
    const { name, value } = e.target
    setForm(f => ({ ...f, [name]: value }))
  }

  function setColor(field, value) {
    setForm(f => ({ ...f, [field]: value }))
  }

  async function handleSubmit() {
    setError(null)
    setResult(null)
    if (!form.org_name.trim() || !form.org_slug.trim()) {
      setError('Organization name and slug are required.')
      return
    }
    if (!form.supervisor_full_name.trim() || !form.supervisor_email.trim()) {
      setError('Supervisor name and email are required.')
      return
    }
    setLoading(true)
    try {
      const payload = {
        org_name: form.org_name.trim(),
        org_slug: form.org_slug.trim(),
        industry: form.industry,
        plan: form.plan,
        supervisor_full_name: form.supervisor_full_name.trim(),
        supervisor_email: form.supervisor_email.trim(),
        brand_name: form.brand_name.trim() || null,
        brand_logo_url: form.brand_logo_url.trim() || null,
        brand_color_primary: form.brand_color_primary || null,
        brand_color_accent: form.brand_color_accent || null,
      }
      if (form.supervisor_password.trim()) {
        payload.supervisor_password = form.supervisor_password.trim()
      }
      const res = await api.post('/admin/provision-client', payload)
      setResult(res)
      loadOrgs()
      setForm({
        org_name: '', org_slug: '', industry: 'funeral', plan: 'trial',
        supervisor_full_name: '', supervisor_email: '', supervisor_password: '',
        brand_name: '', brand_logo_url: '',
        brand_color_primary: '#2fb6ff', brand_color_accent: '#1ef0a8',
      })
      setAutoSlug(true)
    } catch (err) {
      setError(err.message || 'Provisioning failed.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <PageShell title="Provision New Client">
      <div className="provision-layout">

        {/* LEFT — Form */}
        <div className="provision-card">
          <div className="provision-section-header">
            <span className="provision-section-icon">🏢</span>
            <span>Organization</span>
          </div>

          <div className="provision-field">
            <label>Organization Name</label>
            <input type="text" placeholder="e.g. Acme Roofing Co."
              value={form.org_name} onChange={handleOrgName} />
          </div>

          <div className="provision-field">
            <label>Slug <span className="provision-hint">(URL-safe identifier)</span></label>
            <input type="text" placeholder="e.g. acme-roofing"
              value={form.org_slug} onChange={handleSlug} />
          </div>

          <div className="provision-row">
            <div className="provision-field">
              <label>Industry</label>
              <select name="industry" value={form.industry} onChange={handleChange}>
                <option value="funeral">Funeral</option>
                <option value="roofing">Roofing</option>
                <option value="insurance">Insurance</option>
                <option value="real_estate">Real Estate</option>
                <option value="dental">Dental</option>
                <option value="legal">Legal</option>
                <option value="home_services">Home Services</option>
              </select>
            </div>
            <div className="provision-field">
              <label>Plan</label>
              <select name="plan" value={form.plan} onChange={handleChange}>
                <option value="trial">Trial</option>
                <option value="standard">Standard ($299/mo)</option>
                <option value="enterprise">Enterprise</option>
              </select>
            </div>
          </div>

          {/* Branding */}
          <div className="provision-section-header" style={{ marginTop: '1.5rem' }}>
            <span className="provision-section-icon">🎨</span>
            <span>White Label Branding</span>
          </div>

          <div className="provision-field">
            <label>Brand Name <span className="provision-hint">(shows in sidebar — auto-filled from org name)</span></label>
            <input type="text" name="brand_name" placeholder="e.g. Acme Roofing Co."
              value={form.brand_name} onChange={handleChange} />
          </div>

          <div className="provision-field">
            <label>Logo <span className="provision-hint">(optional)</span></label>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 4 }}>
              {form.brand_logo_url && (
                <img src={form.brand_logo_url} alt="Logo preview"
                  style={{ height: 36, maxWidth: 100, objectFit: 'contain', borderRadius: 4, background: 'rgba(255,255,255,0.08)', padding: 3 }}
                  onError={(e) => e.target.style.display='none'} />
              )}
              <label style={{ cursor: 'pointer' }}>
                <span className="provision-btn" style={{ fontSize: 13, padding: '6px 14px', display: 'inline-block', pointerEvents: 'none' }}>
                  {form.brand_logo_url ? '🔄 Replace' : '📁 Upload logo'}
                </span>
                <input type="file" accept="image/*" style={{ display: 'none' }}
                  onChange={(e) => {
                    const file = e.target.files[0]
                    if (!file) return
                    const reader = new FileReader()
                    reader.onload = (ev) => setForm(f => ({ ...f, brand_logo_url: ev.target.result }))
                    reader.readAsDataURL(file)
                  }} />
              </label>
              {form.brand_logo_url && (
                <button style={{ background: 'none', border: 'none', color: '#ef4444', cursor: 'pointer', fontSize: 12 }}
                  onClick={() => setForm(f => ({ ...f, brand_logo_url: '' }))}>Remove</button>
              )}
            </div>
          </div>

          <div className="provision-field">
            <label>Primary color</label>
            <ColorPicker field="brand_color_primary" value={form.brand_color_primary} onChange={setColor} />
          </div>

          <div className="provision-field">
            <label>Accent color</label>
            <ColorPicker field="brand_color_accent" value={form.brand_color_accent} onChange={setColor} />
          </div>

          {/* Live preview bar */}
          <div style={{ background: form.brand_color_primary, borderRadius: 8, padding: '10px 16px',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <span style={{ color: '#fff', fontWeight: 700, fontSize: 14 }}>
              {form.brand_name || form.org_name || 'Brand Preview'}
            </span>
            <span style={{ color: form.brand_color_accent, fontWeight: 600, fontSize: 13 }}>● Live</span>
          </div>

          {/* Supervisor */}
          <div className="provision-section-header" style={{ marginTop: '1.5rem' }}>
            <span className="provision-section-icon">👤</span>
            <span>Supervisor Account</span>
          </div>

          <div className="provision-field">
            <label>Full Name</label>
            <input type="text" name="supervisor_full_name" placeholder="e.g. Jane Smith"
              value={form.supervisor_full_name} onChange={handleChange} />
          </div>

          <div className="provision-field">
            <label>Email</label>
            <input type="email" name="supervisor_email" placeholder="jane@acmeroofing.com"
              value={form.supervisor_email} onChange={handleChange} />
          </div>

          <div className="provision-field">
            <label>Password <span className="provision-hint">(leave blank to auto-generate)</span></label>
            <input type="text" name="supervisor_password" placeholder="Auto-generate if empty"
              value={form.supervisor_password} onChange={handleChange} />
          </div>

          {error && <div className="provision-error">{error}</div>}

          <button className="provision-btn" onClick={handleSubmit} disabled={loading}>
            {loading ? 'Provisioning…' : '🚀 Provision Client'}
          </button>
        </div>

        {/* RIGHT — Result + Org List */}
        <div className="provision-right">
          {result && (
            <div className="provision-result">
              <div className="provision-result-title">✅ Client Provisioned</div>
              <div className="provision-result-row"><span>Org</span><strong>{result.org_name}</strong></div>
              <div className="provision-result-row"><span>Org ID</span><code>{result.org_id}</code></div>
              <div className="provision-result-row"><span>Supervisor Email</span><strong>{result.supervisor_email}</strong></div>
              {result.temp_password && (
                <div className="provision-result-row provision-result-password">
                  <span>Temp Password</span><strong>{result.temp_password}</strong>
                </div>
              )}
              <div className="provision-result-note">
                Share the email and password with the supervisor. They'll be prompted to change their password on first login.
              </div>
            </div>
          )}

          <div className="provision-orgs-panel">
            <div className="provision-orgs-title">
              Active Organizations
              <span style={{ fontSize: 12, color: 'var(--text-tertiary)', fontWeight: 400, marginLeft: 8 }}>
                Click a row to edit
              </span>
            </div>
            {orgsLoading ? (
              <div className="provision-orgs-loading">Loading…</div>
            ) : orgs.length === 0 ? (
              <div className="provision-orgs-empty">No organizations yet.</div>
            ) : (
              <table className="provision-orgs-table">
                <thead>
                  <tr><th>Name</th><th>Industry</th><th>Plan</th><th>Status</th><th>Colors</th></tr>
                </thead>
                <tbody>
                  {orgs.map(org => (
                    <tr key={org.id} onClick={() => setEditOrg(org)} style={{ cursor: 'pointer' }}
                      className="provision-orgs-row">
                      <td>
                        <div className="org-name">{org.name}</div>
                        <div className="org-slug">{org.slug}</div>
                      </td>
                      <td className="org-industry">{org.industry}</td>
                      <td className="org-plan">{org.plan}</td>
                      <td>
                        <span className={`org-status ${org.is_active ? 'active' : 'inactive'}`}>
                          {org.is_active ? 'Active' : 'Inactive'}
                        </span>
                      </td>
                      <td>
                        <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
                          {org.brand_color_primary && (
                            <span style={{ width: 14, height: 14, borderRadius: '50%', background: org.brand_color_primary, display: 'inline-block', border: '1px solid rgba(255,255,255,0.2)' }} title={`Primary: ${org.brand_color_primary}`} />
                          )}
                          {org.brand_color_accent && (
                            <span style={{ width: 14, height: 14, borderRadius: '50%', background: org.brand_color_accent, display: 'inline-block', border: '1px solid rgba(255,255,255,0.2)' }} title={`Accent: ${org.brand_color_accent}`} />
                          )}
                          {!org.brand_color_primary && !org.brand_color_accent && (
                            <span style={{ fontSize: 11, color: 'var(--text-tertiary)' }}>—</span>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {/* Edit org modal */}
      {editOrg && (
        <EditOrgModal
          org={editOrg}
          onClose={() => setEditOrg(null)}
          onSaved={(updated) => {
            setOrgs(prev => prev.map(o => o.id === updated.id ? { ...o, ...updated } : o))
            setEditOrg(null)
          }}
        />
      )}
    </PageShell>
  )
}
