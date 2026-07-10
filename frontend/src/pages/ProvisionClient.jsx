import { useState, useEffect } from 'react'
import { api } from '../api/client'
import PageShell from '../components/PageShell'
import './ProvisionClient.css'

function slugify(str) {
  return str.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '')
}

export default function ProvisionClient() {
  const [form, setForm] = useState({
    org_name: '',
    org_slug: '',
    industry: 'funeral',
    plan: 'trial',
    supervisor_full_name: '',
    supervisor_email: '',
    supervisor_password: '',
  })
  const [autoSlug, setAutoSlug] = useState(true)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [orgs, setOrgs] = useState([])
  const [orgsLoading, setOrgsLoading] = useState(true)

  useEffect(() => {
    api.get('/admin/organizations')
      .then(setOrgs)
      .catch(() => {})
      .finally(() => setOrgsLoading(false))
  }, [])

  function handleOrgName(e) {
    const val = e.target.value
    setForm(f => ({
      ...f,
      org_name: val,
      org_slug: autoSlug ? slugify(val) : f.org_slug,
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
      }
      if (form.supervisor_password.trim()) {
        payload.supervisor_password = form.supervisor_password.trim()
      }

      const res = await api.get('/admin/provision-client', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      setResult(res)
      // Refresh org list
      api.get('/admin/organizations').then(setOrgs).catch(() => {})
      // Reset form
      setForm({
        org_name: '',
        org_slug: '',
        industry: 'funeral',
        plan: 'trial',
        supervisor_full_name: '',
        supervisor_email: '',
        supervisor_password: '',
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
            <input
              type="text"
              placeholder="e.g. Acme Funeral Home"
              value={form.org_name}
              onChange={handleOrgName}
            />
          </div>

          <div className="provision-field">
            <label>Slug <span className="provision-hint">(URL-safe identifier)</span></label>
            <input
              type="text"
              placeholder="e.g. acme-funeral"
              value={form.org_slug}
              onChange={handleSlug}
            />
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

          <div className="provision-section-header" style={{ marginTop: '1.5rem' }}>
            <span className="provision-section-icon">👤</span>
            <span>Supervisor Account</span>
          </div>

          <div className="provision-field">
            <label>Full Name</label>
            <input
              type="text"
              name="supervisor_full_name"
              placeholder="e.g. Jane Smith"
              value={form.supervisor_full_name}
              onChange={handleChange}
            />
          </div>

          <div className="provision-field">
            <label>Email</label>
            <input
              type="email"
              name="supervisor_email"
              placeholder="jane@acmefuneral.com"
              value={form.supervisor_email}
              onChange={handleChange}
            />
          </div>

          <div className="provision-field">
            <label>Password <span className="provision-hint">(leave blank to auto-generate)</span></label>
            <input
              type="text"
              name="supervisor_password"
              placeholder="Auto-generate if empty"
              value={form.supervisor_password}
              onChange={handleChange}
            />
          </div>

          {error && <div className="provision-error">{error}</div>}

          <button
            className="provision-btn"
            onClick={handleSubmit}
            disabled={loading}
          >
            {loading ? 'Provisioning…' : '🚀 Provision Client'}
          </button>
        </div>

        {/* RIGHT — Result + Org List */}
        <div className="provision-right">

          {result && (
            <div className="provision-result">
              <div className="provision-result-title">✅ Client Provisioned</div>
              <div className="provision-result-row">
                <span>Org</span>
                <strong>{result.org_name}</strong>
              </div>
              <div className="provision-result-row">
                <span>Org ID</span>
                <code>{result.org_id}</code>
              </div>
              <div className="provision-result-row">
                <span>Supervisor Email</span>
                <strong>{result.supervisor_email}</strong>
              </div>
              {result.temp_password && (
                <div className="provision-result-row provision-result-password">
                  <span>Temp Password</span>
                  <strong>{result.temp_password}</strong>
                </div>
              )}
              <div className="provision-result-note">
                Share the email and password with the supervisor. They will be prompted to change their password on first login.
              </div>
            </div>
          )}

          <div className="provision-orgs-panel">
            <div className="provision-orgs-title">Active Organizations</div>
            {orgsLoading ? (
              <div className="provision-orgs-loading">Loading…</div>
            ) : orgs.length === 0 ? (
              <div className="provision-orgs-empty">No organizations yet.</div>
            ) : (
              <table className="provision-orgs-table">
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Industry</th>
                    <th>Plan</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {orgs.map(org => (
                    <tr key={org.id}>
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
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </PageShell>
  )
}
