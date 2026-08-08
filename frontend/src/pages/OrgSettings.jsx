import { useEffect, useState } from 'react'
import { api, fetchAndStoreBranding, getCurrentUser } from '../api/client'
import '../styles/shared.css'
import './OrgSettings.css'

const INDUSTRIES = [
  { value: 'funeral', label: '⚰️ Funeral & Cemetery' },
  { value: 'roofing', label: '🏠 Roofing' },
  { value: 'insurance', label: '🛡 Insurance' },
  { value: 'real_estate', label: '🏡 Real Estate' },
  { value: 'dental', label: '🦷 Dental' },
  { value: 'custom', label: '⚙️ Custom' },
]

const COLOR_OPTIONS = [
  { value: '#2fb6ff', label: 'Blue' },
  { value: '#1ef0a8', label: 'Teal' },
  { value: '#f59e0b', label: 'Amber' },
  { value: '#ef4444', label: 'Red' },
  { value: '#8b5cf6', label: 'Purple' },
  { value: '#10b981', label: 'Green' },
  { value: '#f97316', label: 'Orange' },
]

const TIER_COLORS = ['blue', 'green', 'amber', 'red', 'purple', 'neutral']

export default function OrgSettings() {
  const user = getCurrentUser()
  const isSuperAdmin = user?.role === 'super_admin'

  // Super admin org selector
  const [allOrgs, setAllOrgs] = useState([])
  const [selectedOrgId, setSelectedOrgId] = useState(null)

  const [settings, setSettings] = useState(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  // Branding
  const [brandName, setBrandName] = useState('')
  const [brandLogoUrl, setBrandLogoUrl] = useState('')
  const [brandColorPrimary, setBrandColorPrimary] = useState('#2fb6ff')
  const [brandColorAccent, setBrandColorAccent] = useState('#1ef0a8')

  // Industry
  const [industry, setIndustry] = useState('funeral')
  const [changingIndustry, setChangingIndustry] = useState(false)

  // Tiers
  const [tiers, setTiers] = useState([])
  const [savingTiers, setSavingTiers] = useState(false)

  // Load all orgs for super admin selector
  useEffect(() => {
    if (!isSuperAdmin) return
    api.get('/admin/organizations')
      .then(orgs => {
        setAllOrgs(orgs)
        if (orgs.length > 0) setSelectedOrgId(orgs[0].id)
      })
      .catch(() => {})
  }, [isSuperAdmin])

  // Build the query string for org-scoped calls
  const orgQuery = isSuperAdmin && selectedOrgId ? `?org_id=${selectedOrgId}` : ''

  // Load settings whenever the selected org changes (or on mount for non-super-admin)
  useEffect(() => {
    if (isSuperAdmin && !selectedOrgId) return
    setLoading(true)
    setError('')
    setSuccess('')
    api.get(`/org-settings/${orgQuery}`)
      .then((data) => {
        setSettings(data)
        setBrandName(data.brand_name || '')
        setBrandLogoUrl(data.brand_logo_url || '')
        setBrandColorPrimary(data.brand_color_primary || '#2fb6ff')
        setBrandColorAccent(data.brand_color_accent || '#1ef0a8')
        setIndustry(data.industry || 'funeral')
        setTiers(data.tier_config || [])
        setLoading(false)
      })
      .catch(() => setLoading(false))
  }, [selectedOrgId, isSuperAdmin])

  async function saveBranding() {
    setSaving(true)
    setError('')
    setSuccess('')
    try {
      await api.patch(`/org-settings/branding${orgQuery}`, {
        brand_name: brandName || null,
        brand_logo_url: brandLogoUrl || null,
        brand_color_primary: brandColorPrimary,
        brand_color_accent: brandColorAccent,
      })
      setSuccess('Branding saved.')
      // Only update localStorage/CSS for the logged-in user's own org
      if (!isSuperAdmin) await fetchAndStoreBranding()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function changeIndustry(newIndustry) {
    if (!confirm(`Switching to ${newIndustry} will reset tier labels to defaults. Continue?`)) return
    setChangingIndustry(true)
    try {
      const result = await api.patch(`/org-settings/industry${orgQuery}`, { industry: newIndustry })
      setIndustry(newIndustry)
      setTiers(result.tiers || [])
      setSuccess('Industry updated and tiers reset to defaults.')
    } catch (err) {
      setError(err.message)
    } finally {
      setChangingIndustry(false)
    }
  }

  async function saveTiers() {
    setSavingTiers(true)
    setError('')
    try {
      await api.patch(`/org-settings/tiers${orgQuery}`, { tiers })
      setSuccess('Tier configuration saved.')
    } catch (err) {
      setError(err.message)
    } finally {
      setSavingTiers(false)
    }
  }

  function updateTier(index, field, value) {
    setTiers((prev) => prev.map((t, i) => i === index ? { ...t, [field]: value } : t))
  }

  function addTier() {
    setTiers((prev) => [...prev, { value: `tier_${prev.length + 1}`, label: 'New Tier', color: 'blue', description: '' }])
  }

  function removeTier(index) {
    setTiers((prev) => prev.filter((_, i) => i !== index))
  }

  // Super admin with no orgs loaded yet
  if (isSuperAdmin && allOrgs.length === 0 && loading) {
    return <div className="empty-state">Loading organizations…</div>
  }

  // Non-super-admin loading
  if (!isSuperAdmin && loading) {
    return <div className="empty-state">Loading org settings…</div>
  }

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Org Settings</h1>
          <p className="page-subtitle">White labeling, industry configuration, and tier management.</p>
        </div>
      </header>

      {/* Super admin org selector */}
      {isSuperAdmin && (
        <div className="panel" style={{ marginBottom: 16, display: 'flex', alignItems: 'center', gap: 12 }}>
          <label style={{ fontWeight: 600, fontSize: 14, whiteSpace: 'nowrap' }}>Managing org:</label>
          <select
            className="os-input"
            style={{ maxWidth: 320 }}
            value={selectedOrgId || ''}
            onChange={(e) => {
              setSelectedOrgId(e.target.value)
              setSuccess('')
              setError('')
            }}
          >
            {allOrgs.map(org => (
              <option key={org.id} value={org.id}>
                {org.name} ({org.industry} · {org.plan})
              </option>
            ))}
          </select>
          {loading && <span style={{ fontSize: 13, opacity: 0.6 }}>Loading…</span>}
        </div>
      )}

      {error && <div className="os-error">{error}</div>}
      {success && <div className="os-success">{success}</div>}

      {(!isSuperAdmin || (isSuperAdmin && selectedOrgId && !loading)) && (
        <>
          <div className="os-grid">
            <section className="panel os-section">
              <div className="panel-header"><h2 className="panel-title">Branding</h2></div>
              <p className="os-hint">Customize how this organization appears in the platform.</p>

              <label className="os-label">
                Brand name
                <input className="os-input" value={brandName} onChange={(e) => setBrandName(e.target.value)} placeholder="e.g. Acme Roofing Co." />
                <span className="os-hint">Replaces "BookaBoost" in the sidebar and emails</span>
              </label>

              <label className="os-label">
                Logo
                <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 6 }}>
                  {brandLogoUrl && (
                    <img src={brandLogoUrl} alt="Logo preview"
                      style={{ height: 40, maxWidth: 120, objectFit: 'contain', borderRadius: 4, background: 'rgba(255,255,255,0.08)', padding: 4 }}
                      onError={(e) => e.target.style.display='none'} />
                  )}
                  <label style={{ cursor: 'pointer' }}>
                    <span className="btn btn--secondary" style={{ fontSize: 13, padding: '6px 14px', pointerEvents: 'none' }}>
                      {brandLogoUrl ? '🔄 Replace logo' : '📁 Upload logo'}
                    </span>
                    <input type="file" accept="image/*" style={{ display: 'none' }}
                      onChange={(e) => {
                        const file = e.target.files[0]
                        if (!file) return
                        const reader = new FileReader()
                        reader.onload = (ev) => setBrandLogoUrl(ev.target.result)
                        reader.readAsDataURL(file)
                      }} />
                  </label>
                  {brandLogoUrl && (
                    <button className="btn btn--secondary" style={{ fontSize: 12, padding: '4px 10px', color: 'var(--error, #ef4444)' }}
                      onClick={() => setBrandLogoUrl('')}>Remove</button>
                  )}
                </div>
                <span className="os-hint">PNG, JPG, or SVG — stored directly, no URL needed</span>
              </label>

              <label className="os-label">
                Primary color
                <div className="os-color-row">
                  {COLOR_OPTIONS.map((c) => (
                    <button key={c.value}
                      className={`os-color-swatch ${brandColorPrimary === c.value ? 'os-color-swatch--active' : ''}`}
                      style={{ background: c.value }} onClick={() => setBrandColorPrimary(c.value)} title={c.label} />
                  ))}
                  <input type="color" value={brandColorPrimary} onChange={(e) => setBrandColorPrimary(e.target.value)} className="os-color-input" />
                </div>
              </label>

              <label className="os-label">
                Accent color
                <div className="os-color-row">
                  {COLOR_OPTIONS.map((c) => (
                    <button key={c.value}
                      className={`os-color-swatch ${brandColorAccent === c.value ? 'os-color-swatch--active' : ''}`}
                      style={{ background: c.value }} onClick={() => setBrandColorAccent(c.value)} title={c.label} />
                  ))}
                  <input type="color" value={brandColorAccent} onChange={(e) => setBrandColorAccent(e.target.value)} className="os-color-input" />
                </div>
              </label>

              <div className="os-preview-bar" style={{ background: brandColorPrimary }}>
                <span style={{ color: '#fff', fontWeight: 700 }}>{brandName || 'BookaBoost'}</span>
                <span style={{ color: brandColorAccent, fontWeight: 600, fontSize: 13 }}>● Live</span>
              </div>

              <button className="btn btn--primary" onClick={saveBranding} disabled={saving}>
                {saving ? 'Saving…' : 'Save branding'}
              </button>
            </section>

            <section className="panel os-section">
              <div className="panel-header"><h2 className="panel-title">Industry</h2></div>
              <p className="os-hint">Determines default tier labels and cadence templates.</p>
              <div className="os-industry-grid">
                {INDUSTRIES.map((ind) => (
                  <button key={ind.value}
                    className={`os-industry-btn ${industry === ind.value ? 'os-industry-btn--active' : ''}`}
                    onClick={() => industry !== ind.value && changeIndustry(ind.value)}
                    disabled={changingIndustry}
                  >
                    {ind.label}
                    {industry === ind.value && <span className="os-industry-current">Current</span>}
                  </button>
                ))}
              </div>
            </section>
          </div>

          <section className="panel os-section" style={{ marginTop: 16 }}>
            <div className="panel-header">
              <h2 className="panel-title">Tier configuration</h2>
              <button className="btn btn--secondary" onClick={addTier} style={{ fontSize: 12, padding: '4px 12px' }}>+ Add tier</button>
            </div>
            <p className="os-hint">Define lead tiers for this organization.</p>
            <div className="os-tier-list">
              {tiers.map((tier, i) => (
                <div key={i} className="os-tier-row">
                  <div className="os-tier-fields">
                    <label className="os-tier-label">
                      Value (internal)
                      <input className="os-input os-input--sm" value={tier.value} onChange={(e) => updateTier(i, 'value', e.target.value)} placeholder="pre_need" />
                    </label>
                    <label className="os-tier-label">
                      Display label
                      <input className="os-input os-input--sm" value={tier.label} onChange={(e) => updateTier(i, 'label', e.target.value)} placeholder="Pre-Need" />
                    </label>
                    <label className="os-tier-label">
                      Description
                      <input className="os-input os-input--sm" value={tier.description || ''} onChange={(e) => updateTier(i, 'description', e.target.value)} placeholder="Optional" />
                    </label>
                    <label className="os-tier-label">
                      Color
                      <select className="os-input os-input--sm" value={tier.color} onChange={(e) => updateTier(i, 'color', e.target.value)}>
                        {TIER_COLORS.map((c) => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </label>
                  </div>
                  <div className="os-tier-preview">
                    <span className={`badge badge--${tier.color}`}>{tier.label}</span>
                  </div>
                  <button className="os-tier-remove" onClick={() => removeTier(i)}>✕</button>
                </div>
              ))}
            </div>
            <button className="btn btn--primary" onClick={saveTiers} disabled={savingTiers} style={{ marginTop: 14 }}>
              {savingTiers ? 'Saving…' : 'Save tier configuration'}
            </button>
          </section>
        </>
      )}
    </div>
  )
}
