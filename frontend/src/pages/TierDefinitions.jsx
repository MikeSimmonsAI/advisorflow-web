import { useEffect, useState, useCallback } from 'react'
import { api, getCurrentUser } from '../api/client'
import '../styles/shared.css'
import './TierDefinitions.css'

const EMPTY_FORM = {
  tier_key: '',
  tier_label: '',
  track_key: '',
  track_label: '',
  ai_tone_context: '',
  is_manual_selectable: true,
  sort_order: 0,
}

export default function TierDefinitions() {
  const user = getCurrentUser()
  const isSuperAdmin = user?.role === 'super_admin'

  const [tiers, setTiers] = useState([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [seeding, setSeeding] = useState(false)
  const [error, setError] = useState(null)
  const [success, setSuccess] = useState(null)

  const [showModal, setShowModal] = useState(false)
  const [editingTier, setEditingTier] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)

  const [orgId, setOrgId] = useState('')
  const [orgIdInput, setOrgIdInput] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const params = isSuperAdmin && orgId ? `?org_id=${encodeURIComponent(orgId)}` : ''
      const data = await api.get(`/tier-definitions${params}`)
      setTiers(data || [])
    } catch (e) {
      setError(e.message || 'Failed to load tiers')
    } finally {
      setLoading(false)
    }
  }, [isSuperAdmin, orgId])

  useEffect(() => { load() }, [load])

  function flash(msg, isError = false) {
    if (isError) { setError(msg); setSuccess(null) }
    else { setSuccess(msg); setError(null) }
    setTimeout(() => { setError(null); setSuccess(null) }, 4000)
  }

  function openCreate() {
    setEditingTier(null)
    setForm({ ...EMPTY_FORM, sort_order: tiers.filter(t => t.is_active).length })
    setShowModal(true)
  }

  function openEdit(tier) {
    setEditingTier(tier)
    setForm({
      tier_key: tier.tier_key,
      tier_label: tier.tier_label,
      track_key: tier.track_key,
      track_label: tier.track_label,
      ai_tone_context: tier.ai_tone_context || '',
      is_manual_selectable: tier.is_manual_selectable,
      sort_order: tier.sort_order,
    })
    setShowModal(true)
  }

  function closeModal() {
    setShowModal(false)
    setEditingTier(null)
    setForm(EMPTY_FORM)
  }

  async function handleSubmit() {
    if (!form.tier_key || !form.tier_label || !form.track_key || !form.track_label) {
      flash('tier_key, tier_label, track_key, and track_label are all required.', true)
      return
    }
    setSaving(true)
    try {
      if (editingTier) {
        await api.put(`/tier-definitions/${editingTier.id}`, {
          tier_label: form.tier_label,
          track_key: form.track_key,
          track_label: form.track_label,
          ai_tone_context: form.ai_tone_context || null,
          is_manual_selectable: form.is_manual_selectable,
          sort_order: Number(form.sort_order),
        })
        flash('Tier updated.')
      } else {
        const payload = {
          tier_key: form.tier_key,
          tier_label: form.tier_label,
          track_key: form.track_key,
          track_label: form.track_label,
          ai_tone_context: form.ai_tone_context || null,
          is_manual_selectable: form.is_manual_selectable,
          sort_order: Number(form.sort_order),
        }
        if (isSuperAdmin && orgId) payload.org_id = orgId
        await api.post('/tier-definitions', payload)
        flash('Tier created.')
      }
      closeModal()
      load()
    } catch (e) {
      flash(e.message || 'Save failed', true)
    } finally {
      setSaving(false)
    }
  }

  async function toggleActive(tier) {
    try {
      await api.put(`/tier-definitions/${tier.id}`, { is_active: !tier.is_active })
      flash(tier.is_active ? 'Tier deactivated.' : 'Tier reactivated.')
      load()
    } catch (e) {
      flash(e.message || 'Update failed', true)
    }
  }

  async function deleteTier(tier) {
    if (!window.confirm(`Permanently delete tier "${tier.tier_label}"? This cannot be undone. Existing leads with this tier key are unaffected.`)) return
    try {
      await api.delete(`/tier-definitions/${tier.id}`)
      flash('Tier deleted.')
      load()
    } catch (e) {
      flash(e.message || 'Delete failed', true)
    }
  }

  async function seedDefaults() {
    setSeeding(true)
    try {
      const params = isSuperAdmin && orgId ? `?org_id=${encodeURIComponent(orgId)}` : ''
      const result = await api.post(`/tier-definitions/seed-defaults${params}`, {})
      flash(result.message || 'Defaults seeded.')
      load()
    } catch (e) {
      flash(e.message || 'Seed failed', true)
    } finally {
      setSeeding(false)
    }
  }

  const active = tiers.filter(t => t.is_active)
  const inactive = tiers.filter(t => !t.is_active)

  return (
    <div className="td-page">
      <div className="td-header">
        <div>
          <h1 className="td-title">Tier Configuration</h1>
          <p className="td-subtitle">
            Define how leads are categorized and which AI message track each tier uses.
            Changes take effect immediately — no redeploy needed.
          </p>
        </div>
        <div className="td-header-actions">
          <div className="td-btn td-btn--primary" onClick={openCreate}>+ New Tier</div>
          <div
            className={`td-btn td-btn--outline ${seeding ? 'td-btn--disabled' : ''}`}
            onClick={seedDefaults}
          >
            {seeding ? 'Seeding…' : '⟳ Seed Defaults'}
          </div>
        </div>
      </div>

      {isSuperAdmin && (
        <div className="td-org-filter panel">
          <label className="td-label">Manage a different org (super admin)</label>
          <div className="td-org-row">
            <input
              className="td-input"
              placeholder="Paste org UUID to manage another org's tiers"
              value={orgIdInput}
              onChange={e => setOrgIdInput(e.target.value)}
            />
            <div className="td-btn td-btn--outline" onClick={() => setOrgId(orgIdInput)}>Load</div>
            {orgId && <div className="td-btn td-btn--outline" onClick={() => { setOrgId(''); setOrgIdInput('') }}>Clear</div>}
          </div>
          {orgId && <p className="td-hint" style={{ marginTop: 6 }}>Viewing org: <code className="td-code">{orgId}</code></p>}
        </div>
      )}

      {error && <div className="td-alert td-alert--error">{error}</div>}
      {success && <div className="td-alert td-alert--success">{success}</div>}

      {loading ? (
        <div className="td-loading">Loading tiers…</div>
      ) : tiers.length === 0 ? (
        <div className="td-empty panel">
          <p style={{ fontSize: '2rem', marginBottom: 8 }}>🏗️</p>
          <p><strong>No tiers configured yet.</strong></p>
          <p>Click <strong>Seed Defaults</strong> to load Restland's standard 8-tier set, or create your own tiers from scratch.</p>
          <div className="td-btn td-btn--primary" style={{ marginTop: 16, display: 'inline-flex' }} onClick={seedDefaults}>
            ⟳ Seed Default Tiers
          </div>
        </div>
      ) : (
        <>
          <div className="td-section-label">Active Tiers ({active.length})</div>
          <div className="td-table-wrap panel">
            <table className="td-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Tier Key</th>
                  <th>Label</th>
                  <th>Track Key</th>
                  <th>Track Label</th>
                  <th>Manual?</th>
                  <th>AI Context</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {active.map(t => (
                  <tr key={t.id}>
                    <td className="td-sort">{t.sort_order}</td>
                    <td><code className="td-code">{t.tier_key}</code></td>
                    <td className="td-bold">{t.tier_label}</td>
                    <td><code className="td-code">{t.track_key}</code></td>
                    <td>{t.track_label}</td>
                    <td>
                      <span className={`td-badge ${t.is_manual_selectable ? 'td-badge--yes' : 'td-badge--no'}`}>
                        {t.is_manual_selectable ? 'Yes' : 'Auto'}
                      </span>
                    </td>
                    <td className="td-context" title={t.ai_tone_context || ''}>
                      {t.ai_tone_context
                        ? t.ai_tone_context.substring(0, 60) + (t.ai_tone_context.length > 60 ? '…' : '')
                        : <em style={{ color: 'var(--text-tertiary, var(--text-secondary))' }}>—</em>}
                    </td>
                    <td>
                      <div className="td-actions">
                        <span className="td-action-btn" onClick={() => openEdit(t)}>Edit</span>
                        <span className="td-action-btn td-action-btn--warn" onClick={() => toggleActive(t)}>Deactivate</span>
                        <span className="td-action-btn td-action-btn--danger" onClick={() => deleteTier(t)}>Delete</span>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {inactive.length > 0 && (
            <>
              <div className="td-section-label td-section-label--muted">Inactive Tiers ({inactive.length})</div>
              <div className="td-table-wrap panel td-table-wrap--muted">
                <table className="td-table td-table--muted">
                  <thead>
                    <tr>
                      <th>Tier Key</th>
                      <th>Label</th>
                      <th>Track</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {inactive.map(t => (
                      <tr key={t.id}>
                        <td><code className="td-code">{t.tier_key}</code></td>
                        <td>{t.tier_label}</td>
                        <td>{t.track_label}</td>
                        <td>
                          <div className="td-actions">
                            <span className="td-action-btn" onClick={() => toggleActive(t)}>Reactivate</span>
                            <span className="td-action-btn td-action-btn--danger" onClick={() => deleteTier(t)}>Delete</span>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}

      {showModal && (
        <div className="td-modal-overlay" onClick={closeModal}>
          <div className="td-modal" onClick={e => e.stopPropagation()}>
            <div className="td-modal-header">
              <h2>{editingTier ? `Edit: ${editingTier.tier_label}` : 'New Tier Definition'}</h2>
              <span className="td-modal-close" onClick={closeModal}>✕</span>
            </div>

            <div className="td-modal-body">
              <div className="td-field-row">
                <div className="td-field">
                  <label className="td-label">Tier Key *</label>
                  <input
                    className="td-input"
                    placeholder="e.g. pre_need"
                    value={form.tier_key}
                    onChange={e => setForm(f => ({ ...f, tier_key: e.target.value }))}
                    disabled={!!editingTier}
                  />
                  <span className="td-hint">Stored in Lead.tier — lowercase, underscores only. Cannot be changed after creation.</span>
                </div>
                <div className="td-field">
                  <label className="td-label">Tier Label *</label>
                  <input
                    className="td-input"
                    placeholder="e.g. Pre-Need"
                    value={form.tier_label}
                    onChange={e => setForm(f => ({ ...f, tier_label: e.target.value }))}
                  />
                  <span className="td-hint">Shown in the UI (tier selector, lead cards, etc.)</span>
                </div>
              </div>

              <div className="td-field-row">
                <div className="td-field">
                  <label className="td-label">Track Key *</label>
                  <input
                    className="td-input"
                    placeholder="e.g. pre_need_lock_price"
                    value={form.track_key}
                    onChange={e => setForm(f => ({ ...f, track_key: e.target.value }))}
                  />
                  <span className="td-hint">Stored in Lead.message_track — drives cadence and email templates.</span>
                </div>
                <div className="td-field">
                  <label className="td-label">Track Label *</label>
                  <input
                    className="td-input"
                    placeholder="e.g. Pre-Need (Lock Price)"
                    value={form.track_label}
                    onChange={e => setForm(f => ({ ...f, track_label: e.target.value }))}
                  />
                  <span className="td-hint">Human-readable name for the message track.</span>
                </div>
              </div>

              <div className="td-field">
                <label className="td-label">AI Tone Context</label>
                <textarea
                  className="td-textarea"
                  rows={4}
                  placeholder="Describe the tone and context the AI should use when drafting messages for this tier. Be specific — e.g. 'At-Need: the lead's family is currently arranging services for a recent loss. Tone should be warm, supportive, and unhurried — never salesy.'"
                  value={form.ai_tone_context}
                  onChange={e => setForm(f => ({ ...f, ai_tone_context: e.target.value }))}
                />
                <span className="td-hint">Used by the AI to calibrate message tone. This replaces the old hardcoded TRACK_CONTEXT dict.</span>
              </div>

              <div className="td-field-row td-field-row--sm">
                <div className="td-field">
                  <label className="td-label">Sort Order</label>
                  <input
                    className="td-input"
                    type="number"
                    min="0"
                    value={form.sort_order}
                    onChange={e => setForm(f => ({ ...f, sort_order: e.target.value }))}
                  />
                </div>
                <div className="td-field">
                  <label className="td-label">Manual Selectable</label>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 4 }}>
                    <div
                      className={`td-toggle ${form.is_manual_selectable ? 'td-toggle--on' : ''}`}
                      onClick={() => setForm(f => ({ ...f, is_manual_selectable: !f.is_manual_selectable }))}
                    >
                      <div className="td-toggle-knob" />
                    </div>
                    <span className="td-hint">
                      {form.is_manual_selectable
                        ? 'Advisors can assign this tier manually.'
                        : 'System-assigned only — advisors cannot pick this tier from the tier selector.'}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            <div className="td-modal-footer">
              <div className="td-btn td-btn--outline" onClick={closeModal}>Cancel</div>
              <div
                className={`td-btn td-btn--primary ${saving ? 'td-btn--disabled' : ''}`}
                onClick={handleSubmit}
              >
                {saving ? 'Saving…' : editingTier ? 'Save Changes' : 'Create Tier'}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
