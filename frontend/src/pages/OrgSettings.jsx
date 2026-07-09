import { useEffect, useState } from 'react'
import { api } from '../api/client'
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

  useEffect(() => {
    api.get('/org-settings/')
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
  }, [])

  async function saveBranding() {
    setSaving(true)
    setError('')
    setSuccess('')
    try {
      await api.patch('/org-settings/branding', {
        brand_name: brandName || null,
        brand_logo_url: brandLogoUrl || null,
        brand_color_primary: brandColorPrimary,
        brand_color_accent: brandColorAccent,
      })
      setSuccess('Branding saved.')
      // Apply colors live
      document.documentElement.style.setProperty('--signal-blue', brandColorPrimary)
      document.documentElement.style.setProperty('--signal-green', brandColorAccent)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  async function changeIndustry(newIndustry) {
    if (!confirm(`Switching to ${newIndustry} will reset your tier labels to defaults. Continue?`)) return
    setChangingIndustry(true)
    try {
      const result = await api.patch('/org-settings/industry', { industry: newIndustry })
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
      await api.patch('/org-settings/tiers', { tiers })
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

  if (loading) return <div className="empty-state">Loading org settings…</div>

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Org Settings</h1>
          <p className="page-subtitle">White labeling, industry configuration, and tier management.</p>
        </div>
      </header>

      {error && <div className="os-error">{error}</div>}
      {success && <div className="os-success">{success}</div>}

      <div className="os-grid">
        <section className="panel os-section">
          <div className="panel-header"><h2 className="panel-title">Branding</h2></div>
          <p className="os-hint">Customize how your organization appears in the platform.</p>

          <label className="os-label">
            Brand name
            <input className="os-input" value={brandName} onChange={(e) => setBrandName(e.target.value)} placeholder="Restland Cemetery & Funeral Home" />
            <span className="os-hint">Replaces "BookaBoost" in the sidebar and emails</span>
          </label>

          <label className="os-label">
            Logo URL
            <input className="os-input" value={brandLogoUrl} onChange={(e) => setBrandLogoUrl(e.target.value)} placeholder="https://yourdomain.com/logo.png" />
          </label>
          {brandLogoUrl && <img src={brandLogoUrl} alt="Logo preview" className="os-logo-preview" onError={(e) => e.target.style.display='none'} />}

          <label className="os-label">
            Primary color
            <div className="os-color-row">
              {COLOR_OPTIONS.map((c) => (
                <button key={c.value} className={`os-color-swatch ${brandColorPrimary === c.value ? 'os-color-swatch--active' : ''}`}
                  style={{ background: c.value }} onClick={() => setBrandColorPrimary(c.value)} title={c.label} />
              ))}
              <input type="color" value={brandColorPrimary} onChange={(e) => setBrandColorPrimary(e.target.value)} className="os-color-input" />
            </div>
          </label>

          <label className="os-label">
            Accent color
            <div className="os-color-row">
              {COLOR_OPTIONS.map((c) => (
                <button key={c.value} className={`os-color-swatch ${brandColorAccent === c.value ? 'os-color-swatch--active' : ''}`}
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
          <p className="os-hint">Your industry determines default tier labels and cadence templates.</p>
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
        <p className="os-hint">Define the lead tiers for your organization. These appear throughout the app when classifying leads.</p>

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
                  <input className="os-input os-input--sm" value={tier.description || ''} onChange={(e) => updateTier(i, 'description', e.target.value)} placeholder="Optional description" />
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
    </div>
  )
}
