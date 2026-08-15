import { useEffect, useState } from 'react'
import { api, fetchAndStoreBranding, getCurrentUser } from '../api/client'
import '../styles/shared.css'
import './OrgSettings.css'

const INDUSTRIES = [
  // Field Sales / D2D
  { value: 'fiber', label: '⚡ Fiber Internet', group: 'Field Sales / D2D' },
  { value: 'door_to_door', label: '🚪 Door-to-Door', group: 'Field Sales / D2D' },
  { value: 'direct_sales', label: '💼 Direct Sales', group: 'Field Sales / D2D' },
  { value: 'solar', label: '☀️ Solar', group: 'Field Sales / D2D' },
  { value: 'telecom', label: '📡 Telecom', group: 'Field Sales / D2D' },
  { value: 'security', label: '🔒 Security Systems', group: 'Field Sales / D2D' },
  // Insurance
  { value: 'insurance', label: '🛡 Life Insurance', group: 'Insurance' },
  { value: 'health_insurance', label: '🏥 Health Insurance', group: 'Insurance' },
  { value: 'medicare', label: '💊 Medicare', group: 'Insurance' },
  { value: 'annuities', label: '📈 Annuities', group: 'Insurance' },
  // Home Services
  { value: 'roofing', label: '🏠 Roofing', group: 'Home Services' },
  { value: 'hvac', label: '❄️ HVAC', group: 'Home Services' },
  { value: 'plumbing', label: '🔧 Plumbing', group: 'Home Services' },
  { value: 'electrical', label: '⚡ Electrical', group: 'Home Services' },
  { value: 'pest_control', label: '🐛 Pest Control', group: 'Home Services' },
  { value: 'landscaping', label: '🌿 Landscaping', group: 'Home Services' },
  { value: 'windows_doors', label: '🪟 Windows & Doors', group: 'Home Services' },
  { value: 'painting', label: '🎨 Painting', group: 'Home Services' },
  { value: 'flooring', label: '🏡 Flooring', group: 'Home Services' },
  { value: 'cleaning', label: '🧹 Cleaning', group: 'Home Services' },
  { value: 'pool_spa', label: '🏊 Pool & Spa', group: 'Home Services' },
  { value: 'tree_service', label: '🌲 Tree Service', group: 'Home Services' },
  { value: 'water_treatment', label: '💧 Water Treatment', group: 'Home Services' },
  // Healthcare
  { value: 'dental', label: '🦷 Dental', group: 'Healthcare' },
  { value: 'medical', label: '🏥 Medical', group: 'Healthcare' },
  { value: 'chiropractic', label: '🦴 Chiropractic', group: 'Healthcare' },
  { value: 'physical_therapy', label: '🏋️ Physical Therapy', group: 'Healthcare' },
  { value: 'veterinary', label: '🐾 Veterinary', group: 'Healthcare' },
  // Real Estate & Finance
  { value: 'real_estate', label: '🏡 Real Estate', group: 'Real Estate & Finance' },
  { value: 'mortgage', label: '🏦 Mortgage', group: 'Real Estate & Finance' },
  { value: 'financial_services', label: '💰 Financial Services', group: 'Real Estate & Finance' },
  // Funeral & Cemetery
  { value: 'funeral', label: '⚰️ Funeral & Cemetery', group: 'Funeral & Cemetery' },
  // Other
  { value: 'legal', label: '⚖️ Legal', group: 'Other' },
  { value: 'fitness', label: '💪 Fitness', group: 'Other' },
  { value: 'education', label: '📚 Education', group: 'Other' },
  { value: 'auto_repair', label: '🚗 Auto Repair', group: 'Other' },
  { value: 'custom', label: '⚙️ Custom / Other', group: 'Other' },
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
  const [memberLabel, setMemberLabel] = useState('')
  const [membersLabel, setMembersLabel] = useState('')

  // Industry
  const [industry, setIndustry] = useState('funeral')
  const [changingIndustry, setChangingIndustry] = useState(false)

  // Tiers
  const [tiers, setTiers] = useState([])
  const [savingTiers, setSavingTiers] = useState(false)

  // Social links
  const [facebookUrl, setFacebookUrl] = useState('')
  const [googleReviewUrl, setGoogleReviewUrl] = useState('')
  const [instagramUrl, setInstagramUrl] = useState('')
  const [linkedinUrl, setLinkedinUrl] = useState('')
  const [savingSocial, setSavingSocial] = useState(false)
  const [socialSaved, setSocialSaved] = useState(false)

  // Org-level email sender
  const [fromEmail, setFromEmail] = useState('')
  const [resendApiKey, setResendApiKey] = useState('')
  const [resendApiKeySet, setResendApiKeySet] = useState(false)
  const [savingEmailSender, setSavingEmailSender] = useState(false)
  const [emailSenderSaved, setEmailSenderSaved] = useState(false)
  const [emailSenderError, setEmailSenderError] = useState('')

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
        setMemberLabel(data.member_label || '')
        setMembersLabel(data.members_label || '')
        setIndustry(data.industry || 'funeral')
        setTiers(data.tier_config || [])
        setFacebookUrl(data.facebook_url || '')
        setGoogleReviewUrl(data.google_review_url || '')
        setInstagramUrl(data.instagram_url || '')
        setLinkedinUrl(data.linkedin_url || '')
        setFromEmail(data.from_email || '')
        setResendApiKeySet(!!data.resend_api_key_set)
        setResendApiKey('')  // Never pre-populate — only write when user explicitly enters it
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
        member_label: memberLabel || null,
        members_label: membersLabel || null,
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
    const isReset = newIndustry === industry
    const msg = isReset
      ? `Reset tier configuration to ${newIndustry} defaults? Current tiers will be replaced.`
      : `Switching to ${newIndustry} will reset tier labels to defaults. Continue?`
    if (!window.confirm(msg)) return
    setChangingIndustry(true)
    try {
      const result = await api.patch(`/org-settings/industry${orgQuery}`, { industry: newIndustry })
      setIndustry(newIndustry)
      setTiers(result.tiers || [])
      setSuccess(isReset ? 'Tiers reset to industry defaults.' : 'Industry updated and tiers reset to defaults.')
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

  async function saveSocialLinks() {
    setSavingSocial(true)
    setSocialSaved(false)
    setError('')
    try {
      await api.patch(`/org-settings/social-links${orgQuery}`, {
        facebook_url: facebookUrl || null,
        google_review_url: googleReviewUrl || null,
        instagram_url: instagramUrl || null,
        linkedin_url: linkedinUrl || null,
      })
      setSocialSaved(true)
      setTimeout(() => setSocialSaved(false), 3000)
    } catch (err) {
      setError(err.message)
    } finally {
      setSavingSocial(false)
    }
  }

  async function saveEmailSender() {
    setSavingEmailSender(true)
    setEmailSenderSaved(false)
    setEmailSenderError('')
    try {
      const payload = { from_email: fromEmail || null }
      // Only send the API key if the user actually typed something — empty field means "leave unchanged"
      if (resendApiKey.trim()) payload.resend_api_key = resendApiKey.trim()
      await api.patch(`/org-settings/email-sender${orgQuery}`, payload)
      setEmailSenderSaved(true)
      if (resendApiKey.trim()) {
        setResendApiKeySet(true)
        setResendApiKey('')  // Clear the field after saving so it doesn't linger
      }
      setTimeout(() => setEmailSenderSaved(false), 3000)
    } catch (err) {
      setEmailSenderError(err.message)
    } finally {
      setSavingEmailSender(false)
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

              <label className="os-label" style={{ marginTop: 8 }}>
                Member role label (singular)
                <input
                  className="os-input"
                  value={memberLabel}
                  onChange={(e) => setMemberLabel(e.target.value)}
                  placeholder={`e.g. Agent, Rep, Tech, Advisor (leave blank for industry default)`}
                />
                <span className="os-hint">How this org refers to a single non-admin user. Leave blank to use the industry default.</span>
              </label>

              <label className="os-label">
                Member role label (plural)
                <input
                  className="os-input"
                  value={membersLabel}
                  onChange={(e) => setMembersLabel(e.target.value)}
                  placeholder={`e.g. Agents, Reps, Techs, Advisors`}
                />
                <span className="os-hint">Plural form shown in headings like "Advisor Twilio Numbers".</span>
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

              {isSuperAdmin ? (
                // Super admin only — org admins cannot change their own industry
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
              ) : (
                // Org admin: read-only display
                <div style={{ display: 'flex', alignItems: 'center', gap: 14, marginTop: 10 }}>
                  <span style={{
                    padding: '10px 22px',
                    borderRadius: 10,
                    background: 'rgba(47,182,255,0.1)',
                    border: '1px solid rgba(47,182,255,0.3)',
                    fontSize: 16,
                    fontWeight: 700,
                    color: 'var(--accent)',
                    letterSpacing: '0.01em',
                  }}>
                    {INDUSTRIES.find(i => i.value === industry)?.label || industry}
                  </span>
                  <span style={{ fontSize: 13, opacity: 0.5 }}>Set by your platform administrator</span>
                </div>
              )}
            </section>
          </div>

          <section className="panel os-section" style={{ marginTop: 16 }}>
            <div className="panel-header">
              <h2 className="panel-title">Tier configuration</h2>
              <div style={{ display: 'flex', gap: 8 }}>
                {isSuperAdmin && (
                  <button className="btn btn--secondary" onClick={() => changeIndustry(industry)}
                    disabled={changingIndustry} style={{ fontSize: 12, padding: '4px 12px', color: '#f0c040', borderColor: 'rgba(240,192,64,0.4)' }}
                    title="Reset tiers to industry defaults">
                    {changingIndustry ? 'Resetting…' : '↺ Reset to industry defaults'}
                  </button>
                )}
                <button className="btn btn--secondary" onClick={addTier} style={{ fontSize: 12, padding: '4px 12px' }}>+ Add tier</button>
              </div>
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
          {/* ── Social & Review Links ── */}
          <section className="panel os-section" style={{ marginTop: 16 }}>
            <div className="panel-header"><h2 className="panel-title">📣 Social &amp; Review Links</h2></div>
            <p className="os-hint">These links appear on the post-appointment survey page sent to leads.</p>

            <label className="os-label">
              Facebook
              <input className="os-input" value={facebookUrl} onChange={(e) => setFacebookUrl(e.target.value)} placeholder="https://facebook.com/yourpage" />
            </label>
            <label className="os-label">
              Google Review
              <input className="os-input" value={googleReviewUrl} onChange={(e) => setGoogleReviewUrl(e.target.value)} placeholder="https://g.page/r/..." />
            </label>
            <label className="os-label">
              Instagram
              <input className="os-input" value={instagramUrl} onChange={(e) => setInstagramUrl(e.target.value)} placeholder="https://instagram.com/yourhandle" />
            </label>
            <label className="os-label">
              LinkedIn
              <input className="os-input" value={linkedinUrl} onChange={(e) => setLinkedinUrl(e.target.value)} placeholder="https://linkedin.com/company/..." />
            </label>

            <button className="btn btn--primary" onClick={saveSocialLinks} disabled={savingSocial} style={{ marginTop: 8 }}>
              {savingSocial ? 'Saving…' : socialSaved ? '✓ Saved' : 'Save social links'}
            </button>
          </section>

          {/* ── Email Sender ── */}
          <section className="panel os-section" style={{ marginTop: 16 }}>
            <div className="panel-header"><h2 className="panel-title">📧 Email Sender</h2></div>
            <p className="os-hint">
              Set the address outbound emails are sent <em>from</em>. Use your org's own verified domain
              (e.g. <code>support@bookaboost.live</code>) so replies land in your real inbox and emails
              don't land in spam. Requires the domain to be verified in Resend first.
            </p>

            <label className="os-label">
              From address
              <input
                className="os-input"
                value={fromEmail}
                onChange={(e) => setFromEmail(e.target.value)}
                placeholder="support@yourdomain.live"
                type="email"
              />
            </label>

            <label className="os-label" style={{ marginTop: 10 }}>
              Resend API key
              <input
                className="os-input"
                value={resendApiKey}
                onChange={(e) => setResendApiKey(e.target.value)}
                placeholder={resendApiKeySet ? '●●●●●●●● (key on file — leave blank to keep)' : 're_xxxxxxxxxxxxxxxxxxxx'}
                type="password"
                autoComplete="new-password"
              />
              {resendApiKeySet && !resendApiKey && (
                <span style={{ fontSize: 11, color: 'var(--signal-green, #1ef0a8)', marginTop: 3 }}>
                  ✓ API key is configured
                </span>
              )}
            </label>

            {emailSenderError && (
              <p style={{ color: 'var(--signal-red, #ff4d4f)', fontSize: 12, marginTop: 6 }}>{emailSenderError}</p>
            )}

            <button
              className="btn btn--primary"
              onClick={saveEmailSender}
              disabled={savingEmailSender}
              style={{ marginTop: 10 }}
            >
              {savingEmailSender ? 'Saving…' : emailSenderSaved ? '✓ Saved' : 'Save email sender'}
            </button>
          </section>

        {/* ── Demo Data Seed (super admin only) ── */}
        {isSuperAdmin && selectedOrgId && (
          <section className="panel os-section" style={{ marginTop: 16, borderColor: 'rgba(217,119,6,0.3)' }}>
            <div className="panel-header">
              <h2 className="panel-title" style={{ color: '#f59e0b' }}>🧪 Demo data</h2>
            </div>
            <p className="os-hint">
              Seed this organization with 120 leads, messages, replies, and booked outcomes so charts and reports show real-looking data.
              <strong style={{ color: '#f59e0b' }}> This adds data — it does not clear existing records first.</strong>
            </p>
            <SeedDemoButton orgId={selectedOrgId} />
          </section>
        )}
      </>
      )}
    </div>
  )
}

function SeedDemoButton({ orgId }) {
  const [status, setStatus] = useState(null)   // null | 'loading' | 'done' | 'error'
  const [result, setResult] = useState(null)
  const [err, setErr] = useState('')

  const run = async () => {
    if (!window.confirm('Seed demo data into this org? This will add ~120 leads and related records.')) return
    setStatus('loading'); setErr(''); setResult(null)
    try {
      const r = await api.post(`/admin/demo/seed/${orgId}`)
      setResult(r); setStatus('done')
    } catch (e) {
      setErr(e.message || 'Seed failed'); setStatus('error')
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, marginTop: 8 }}>
      <button className="btn btn--secondary" onClick={run} disabled={status === 'loading'} style={{ alignSelf: 'flex-start', borderColor: 'rgba(217,119,6,0.4)', color: '#f59e0b' }}>
        {status === 'loading' ? 'Seeding…' : '🌱 Seed demo data'}
      </button>
      {status === 'done' && result && (
        <div style={{ background: 'rgba(16,185,129,0.08)', border: '1px solid rgba(16,185,129,0.25)', borderRadius: 8, padding: '10px 14px', fontSize: 13, color: 'var(--signal-green)' }}>
          ✓ Seeded <strong>{result.org}</strong> — {result.leads} leads · {result.messages} messages · {result.replies} replies · {result.outcomes} outcomes
        </div>
      )}
      {status === 'error' && <div style={{ color: '#f87171', fontSize: 13 }}>{err}</div>}
    </div>
  )
}
