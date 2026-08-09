import { useState, useEffect } from 'react'
import { api } from '../api/client'
import './CaseFile.css'

// ── Constants ─────────────────────────────────────────────────────────────────

const OUTCOME_LABELS = {
  sold: '✅ Sold',
  partial_sale: '🟡 Partial Sale',
  no_sale: '❌ No Sale',
  needs_followup: '🔄 Needs Follow-Up',
  rescheduled: '📅 Rescheduled',
  no_show: '👻 No Show',
  lost_to_competitor: '🏁 Lost to Competitor',
  referred_out: '↗️ Referred Out',
}

const CASE_STATUS_LABELS = {
  open: '🟢 Open',
  pending_application: '📋 Pending Application',
  pending_issue: '⏳ Pending Issue',
  in_force: '✅ In Force',
  closed_won: '🏆 Closed — Won',
  closed_lost: '❌ Closed — Lost',
  follow_up_needed: '🔄 Follow-Up Needed',
  annual_review_due: '📆 Annual Review Due',
  rescheduled: '📅 Rescheduled',
}

const NEXT_ACTION_LABELS = {
  schedule_appointment: '📅 Schedule Appointment',
  restart_ai_conversation: '🤖 Restart AI Conversation',
  add_to_campaign: '📣 Add to Campaign',
  set_reminder: '⏰ Set Reminder',
  refer_to_specialist: '↗️ Refer to Specialist',
  close_case: '🔒 Close Case',
  none: '— None',
}

const APPT_TYPE_LABELS = {
  in_person: '🤝 In Person',
  phone: '📞 Phone',
  video: '💻 Video',
  other: '📌 Other',
}

const PRODUCTS = [
  { key: 'final_expense',        label: 'Final Expense',            icon: '🪦' },
  { key: 'term_life_10yr',       label: 'Term Life — 10yr',         icon: '📄' },
  { key: 'term_life_20yr',       label: 'Term Life — 20yr',         icon: '📄' },
  { key: 'term_life_30yr',       label: 'Term Life — 30yr',         icon: '📄' },
  { key: 'whole_life',           label: 'Whole Life',               icon: '🛡️' },
  { key: 'universal_life_iul',   label: 'IUL (Indexed UL)',         icon: '📈' },
  { key: 'universal_life_vul',   label: 'VUL (Variable UL)',        icon: '📊' },
  { key: 'universal_life_gul',   label: 'GUL (Guaranteed UL)',      icon: '🔒' },
  { key: 'annuity_fixed',        label: 'Fixed Annuity',            icon: '💰' },
  { key: 'annuity_fixed_indexed',label: 'Fixed Indexed Annuity',    icon: '💹' },
  { key: 'annuity_variable',     label: 'Variable Annuity',         icon: '📉' },
  { key: 'medicare_supplement',  label: 'Medicare Supplement',      icon: '🏥' },
  { key: 'medicare_advantage',   label: 'Medicare Advantage',       icon: '🏨' },
  { key: 'long_term_care',       label: 'Long-Term Care',           icon: '🏠' },
  { key: 'disability_income',    label: 'Disability Income',        icon: '🦽' },
  { key: 'dental_vision_hearing',label: 'Dental / Vision / Hearing',icon: '👁️' },
  { key: 'burial_preneed',       label: 'Burial / Pre-Need',        icon: '⚱️' },
  { key: 'cemetery_property',    label: 'Cemetery Property',        icon: '🌿' },
  { key: 'marker_monument',      label: 'Marker / Monument',        icon: '🪨' },
  { key: 'memorial',             label: 'Memorial',                 icon: '🕊️' },
  { key: 'funeral_arrangement',  label: 'Funeral Arrangement',      icon: '🌹' },
  { key: 'veterans_benefits',    label: 'Veterans Benefits',        icon: '🎖️' },
  { key: 'other',                label: 'Other',                    icon: '📌' },
]

const CHECKLIST_ITEMS = [
  { key: 'chk_id_verified',            label: 'ID / Driver\'s License Verified' },
  { key: 'chk_beneficiary_named',      label: 'Beneficiary Designated' },
  { key: 'chk_beneficiary_reviewed',   label: 'Existing Beneficiary Reviewed' },
  { key: 'chk_app_signed',             label: 'Application Signed' },
  { key: 'chk_payment_collected',      label: 'Payment Method Collected' },
  { key: 'chk_illustrations_reviewed', label: 'Policy Illustrations Reviewed' },
  { key: 'chk_riders_explained',       label: 'Riders / Add-Ons Explained' },
  { key: 'chk_medical_history',        label: 'Medical History Form Completed' },
  { key: 'chk_hipaa_signed',           label: 'HIPAA Authorization Signed' },
  { key: 'chk_replacement_form',       label: 'Replacement Form (if applicable)' },
]

const TABS = ['Appointment', 'Products', 'Policy Details', 'File Review', 'Notes & Next Steps']

const EMPTY_FORM = {
  appointment_date: '', appointment_type: '', outcome_type: '',
  products_discussed: [], products_sold: [],
  policy_carrier: '', policy_number: '', coverage_amount: '',
  premium_monthly: '', premium_annual: '', application_date: '', issue_date: '',
  chk_id_verified: false, chk_beneficiary_named: false, chk_app_signed: false,
  chk_payment_collected: false, chk_illustrations_reviewed: false,
  chk_medical_history: false, chk_hipaa_signed: false,
  chk_replacement_form: false, chk_beneficiary_reviewed: false, chk_riders_explained: false,
  advisor_notes: '', objections_raised: '', client_concerns: '',
  referral_potential: false, referral_notes: '',
  case_status: 'open', next_action: '', next_action_date: '', next_action_notes: '',
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function CaseFile({ lead, onClose, onSaved }) {
  const [activeTab, setActiveTab] = useState(0)
  const [form, setForm] = useState(EMPTY_FORM)
  const [existingFiles, setExistingFiles] = useState([])
  const [selectedFileId, setSelectedFileId] = useState(null)  // null = new
  const [saving, setSaving] = useState(false)
  const [crmPushing, setCrmPushing] = useState(false)
  const [closing, setClosing] = useState(false)
  const [closeOutcome, setCloseOutcome] = useState('closed_won')
  const [closeNotes, setCloseNotes] = useState('')
  const [showCloseModal, setShowCloseModal] = useState(false)
  const [toast, setToast] = useState(null)

  useEffect(() => {
    if (!lead?.id) return
    api.get(`/case-file/lead/${lead.id}`)
      .then(r => {
        setExistingFiles(r.data)
        if (r.data.length > 0) {
          loadFile(r.data[0])
        }
      })
      .catch(() => {})
  }, [lead?.id])

  function loadFile(cf) {
    setSelectedFileId(cf.id)
    setForm({
      appointment_date: cf.appointment_date ? cf.appointment_date.slice(0, 16) : '',
      appointment_type: cf.appointment_type || '',
      outcome_type: cf.outcome_type || '',
      products_discussed: cf.products_discussed || [],
      products_sold: cf.products_sold || [],
      policy_carrier: cf.policy_carrier || '',
      policy_number: cf.policy_number || '',
      coverage_amount: cf.coverage_amount || '',
      premium_monthly: cf.premium_monthly || '',
      premium_annual: cf.premium_annual || '',
      application_date: cf.application_date ? cf.application_date.slice(0, 10) : '',
      issue_date: cf.issue_date ? cf.issue_date.slice(0, 10) : '',
      chk_id_verified: cf.chk_id_verified || false,
      chk_beneficiary_named: cf.chk_beneficiary_named || false,
      chk_app_signed: cf.chk_app_signed || false,
      chk_payment_collected: cf.chk_payment_collected || false,
      chk_illustrations_reviewed: cf.chk_illustrations_reviewed || false,
      chk_medical_history: cf.chk_medical_history || false,
      chk_hipaa_signed: cf.chk_hipaa_signed || false,
      chk_replacement_form: cf.chk_replacement_form || false,
      chk_beneficiary_reviewed: cf.chk_beneficiary_reviewed || false,
      chk_riders_explained: cf.chk_riders_explained || false,
      advisor_notes: cf.advisor_notes || '',
      objections_raised: cf.objections_raised || '',
      client_concerns: cf.client_concerns || '',
      referral_potential: cf.referral_potential || false,
      referral_notes: cf.referral_notes || '',
      case_status: cf.case_status || 'open',
      next_action: cf.next_action || '',
      next_action_date: cf.next_action_date ? cf.next_action_date.slice(0, 10) : '',
      next_action_notes: cf.next_action_notes || '',
    })
  }

  function set(field, value) {
    setForm(f => ({ ...f, [field]: value }))
  }

  function toggleProduct(key, list) {
    const current = form[list] || []
    set(list, current.includes(key) ? current.filter(k => k !== key) : [...current, key])
  }

  function showToast(msg, type = 'success') {
    setToast({ msg, type })
    setTimeout(() => setToast(null), 3000)
  }

  async function handleSave() {
    setSaving(true)
    try {
      const payload = {
        ...form,
        appointment_date: form.appointment_date || null,
        application_date: form.application_date || null,
        issue_date: form.issue_date || null,
        next_action_date: form.next_action_date || null,
      }
      let saved
      if (selectedFileId) {
        saved = await api.patch(`/case-file/${selectedFileId}`, payload)
      } else {
        saved = await api.post(`/case-file/lead/${lead.id}`, payload)
        setSelectedFileId(saved.data.id)
        setExistingFiles(prev => [saved.data, ...prev])
      }
      showToast('Case file saved ✓')
      if (onSaved) onSaved(saved.data)
    } catch (e) {
      showToast(e?.response?.data?.detail || 'Save failed', 'error')
    } finally {
      setSaving(false)
    }
  }

  async function handleCrmPush() {
    if (!selectedFileId) { showToast('Save the case file first', 'error'); return }
    setCrmPushing(true)
    try {
      const r = await api.post(`/case-file/${selectedFileId}/crm-push`)
      const ok = r.data.results?.filter(x => x.ok).length
      showToast(ok ? `Pushed to ${ok} CRM connection(s) ✓` : 'No active CRM connections found')
    } catch (e) {
      showToast(e?.response?.data?.detail || 'CRM push failed', 'error')
    } finally {
      setCrmPushing(false)
    }
  }

  async function handleClose() {
    if (!selectedFileId) { showToast('Save the case file first', 'error'); return }
    setClosing(true)
    try {
      await api.post(`/case-file/${selectedFileId}/close`, { outcome: closeOutcome, close_notes: closeNotes })
      setForm(f => ({ ...f, case_status: closeOutcome }))
      setShowCloseModal(false)
      showToast(`Case closed as ${CASE_STATUS_LABELS[closeOutcome]} ✓`)
      if (onSaved) onSaved({ case_status: closeOutcome })
    } catch (e) {
      showToast(e?.response?.data?.detail || 'Close failed', 'error')
    } finally {
      setClosing(false)
    }
  }

  function startNew() {
    setSelectedFileId(null)
    setForm(EMPTY_FORM)
  }

  const checkedCount = CHECKLIST_ITEMS.filter(i => form[i.key]).length

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div className="cf-overlay" onClick={e => e.target === e.currentTarget && onClose?.()}>
      <div className="cf-panel">

        {/* Header */}
        <div className="cf-header">
          <div className="cf-header-left">
            <div className="cf-header-title">📁 Case File</div>
            <div className="cf-header-sub">
              {lead?.first_name} {lead?.last_name}
              {lead?.phone && <span className="cf-header-phone"> · {lead.phone}</span>}
            </div>
          </div>
          <div className="cf-header-right">
            <span className={`cf-status-chip cf-status--${form.case_status}`}>
              {CASE_STATUS_LABELS[form.case_status] || form.case_status}
            </span>
            <div className="cf-header-actions">
              {existingFiles.length > 0 && (
                <select className="cf-file-picker" onChange={e => {
                  if (e.target.value === '__new__') { startNew() }
                  else { const f = existingFiles.find(x => x.id === e.target.value); if (f) loadFile(f) }
                }} value={selectedFileId || '__new__'}>
                  <option value="__new__">+ New Entry</option>
                  {existingFiles.map((f, i) => (
                    <option key={f.id} value={f.id}>
                      {f.appointment_date ? new Date(f.appointment_date).toLocaleDateString() : `Entry ${existingFiles.length - i}`}
                      {f.outcome_type ? ` — ${OUTCOME_LABELS[f.outcome_type] || f.outcome_type}` : ''}
                    </option>
                  ))}
                </select>
              )}
              {!selectedFileId && <span className="cf-new-badge">New Entry</span>}
            </div>
            <div className="cf-close-btn" onClick={onClose}>✕</div>
          </div>
        </div>

        {/* Tabs */}
        <div className="cf-tabs">
          {TABS.map((t, i) => (
            <div key={t} className={`cf-tab ${activeTab === i ? 'cf-tab--active' : ''}`}
              onClick={() => setActiveTab(i)}>
              {t}
              {i === 3 && checkedCount > 0 && (
                <span className="cf-tab-badge">{checkedCount}/{CHECKLIST_ITEMS.length}</span>
              )}
              {i === 1 && form.products_sold.length > 0 && (
                <span className="cf-tab-badge sold">{form.products_sold.length} sold</span>
              )}
            </div>
          ))}
        </div>

        {/* Tab Body */}
        <div className="cf-body">

          {/* ── Tab 0: Appointment ─────────────────────────────────────────── */}
          {activeTab === 0 && (
            <div className="cf-section-grid">
              <div className="cf-field-group">
                <label className="cf-label">Appointment Date & Time</label>
                <input className="cf-input" type="datetime-local"
                  value={form.appointment_date}
                  onChange={e => set('appointment_date', e.target.value)} />
              </div>
              <div className="cf-field-group">
                <label className="cf-label">Meeting Type</label>
                <div className="cf-pill-row">
                  {Object.entries(APPT_TYPE_LABELS).map(([k, v]) => (
                    <div key={k} className={`cf-pill ${form.appointment_type === k ? 'cf-pill--active' : ''}`}
                      onClick={() => set('appointment_type', form.appointment_type === k ? '' : k)}>
                      {v}
                    </div>
                  ))}
                </div>
              </div>
              <div className="cf-field-group cf-field-group--full">
                <label className="cf-label">Appointment Outcome</label>
                <div className="cf-outcome-grid">
                  {Object.entries(OUTCOME_LABELS).map(([k, v]) => (
                    <div key={k}
                      className={`cf-outcome-card ${form.outcome_type === k ? 'cf-outcome-card--active' : ''}`}
                      onClick={() => set('outcome_type', form.outcome_type === k ? '' : k)}>
                      {v}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* ── Tab 1: Products ────────────────────────────────────────────── */}
          {activeTab === 1 && (
            <div className="cf-products-layout">
              <div className="cf-products-col">
                <div className="cf-products-header">
                  <span className="cf-products-title">📋 Products Discussed</span>
                  <span className="cf-products-count">{form.products_discussed.length} selected</span>
                </div>
                <div className="cf-products-grid">
                  {PRODUCTS.map(p => (
                    <div key={p.key}
                      className={`cf-product-chip ${form.products_discussed.includes(p.key) ? 'cf-product-chip--discussed' : ''}`}
                      onClick={() => toggleProduct(p.key, 'products_discussed')}>
                      <span className="cf-product-icon">{p.icon}</span>
                      <span className="cf-product-label">{p.label}</span>
                      {form.products_discussed.includes(p.key) && <span className="cf-product-check">✓</span>}
                    </div>
                  ))}
                </div>
              </div>
              <div className="cf-products-col">
                <div className="cf-products-header">
                  <span className="cf-products-title">💰 Products Sold</span>
                  <span className="cf-products-count sold">{form.products_sold.length} sold</span>
                </div>
                <div className="cf-products-grid">
                  {PRODUCTS.map(p => (
                    <div key={p.key}
                      className={`cf-product-chip ${form.products_sold.includes(p.key) ? 'cf-product-chip--sold' : ''}`}
                      onClick={() => toggleProduct(p.key, 'products_sold')}>
                      <span className="cf-product-icon">{p.icon}</span>
                      <span className="cf-product-label">{p.label}</span>
                      {form.products_sold.includes(p.key) && <span className="cf-product-check">✓</span>}
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* ── Tab 2: Policy Details ──────────────────────────────────────── */}
          {activeTab === 2 && (
            <div className="cf-section-grid">
              <div className="cf-field-group">
                <label className="cf-label">Carrier / Insurance Company</label>
                <input className="cf-input" placeholder="e.g. Mutual of Omaha, Lincoln Benefit..."
                  value={form.policy_carrier} onChange={e => set('policy_carrier', e.target.value)} />
              </div>
              <div className="cf-field-group">
                <label className="cf-label">Policy / Application Number</label>
                <input className="cf-input" placeholder="e.g. A-123456789"
                  value={form.policy_number} onChange={e => set('policy_number', e.target.value)} />
              </div>
              <div className="cf-field-group">
                <label className="cf-label">Coverage / Face Amount</label>
                <input className="cf-input" placeholder="e.g. $25,000"
                  value={form.coverage_amount} onChange={e => set('coverage_amount', e.target.value)} />
              </div>
              <div className="cf-field-group">
                <label className="cf-label">Monthly Premium</label>
                <input className="cf-input" placeholder="e.g. $87.50/mo"
                  value={form.premium_monthly} onChange={e => set('premium_monthly', e.target.value)} />
              </div>
              <div className="cf-field-group">
                <label className="cf-label">Annual Premium</label>
                <input className="cf-input" placeholder="e.g. $1,050/yr"
                  value={form.premium_annual} onChange={e => set('premium_annual', e.target.value)} />
              </div>
              <div className="cf-field-group">
                <label className="cf-label">Application Date</label>
                <input className="cf-input" type="date"
                  value={form.application_date} onChange={e => set('application_date', e.target.value)} />
              </div>
              <div className="cf-field-group">
                <label className="cf-label">Issue / In-Force Date</label>
                <input className="cf-input" type="date"
                  value={form.issue_date} onChange={e => set('issue_date', e.target.value)} />
              </div>
            </div>
          )}

          {/* ── Tab 3: File Review Checklist ───────────────────────────────── */}
          {activeTab === 3 && (
            <div className="cf-checklist-wrap">
              <div className="cf-checklist-progress">
                <div className="cf-progress-bar">
                  <div className="cf-progress-fill"
                    style={{ width: `${(checkedCount / CHECKLIST_ITEMS.length) * 100}%` }} />
                </div>
                <span className="cf-progress-label">{checkedCount} of {CHECKLIST_ITEMS.length} items completed</span>
              </div>
              <div className="cf-checklist-grid">
                {CHECKLIST_ITEMS.map(item => (
                  <div key={item.key}
                    className={`cf-check-card ${form[item.key] ? 'cf-check-card--done' : ''}`}
                    onClick={() => set(item.key, !form[item.key])}>
                    <div className="cf-check-box">{form[item.key] ? '✅' : '⬜'}</div>
                    <div className="cf-check-label">{item.label}</div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* ── Tab 4: Notes & Next Steps ──────────────────────────────────── */}
          {activeTab === 4 && (
            <div className="cf-section-grid">
              <div className="cf-field-group cf-field-group--full">
                <label className="cf-label">Advisor Notes (what was covered, key talking points, overall impression)</label>
                <textarea className="cf-textarea cf-textarea--lg" rows={4}
                  placeholder="Add your notes from the appointment here..."
                  value={form.advisor_notes} onChange={e => set('advisor_notes', e.target.value)} />
              </div>
              <div className="cf-field-group">
                <label className="cf-label">Objections Raised</label>
                <textarea className="cf-textarea" rows={3}
                  placeholder="What objections did the client raise?"
                  value={form.objections_raised} onChange={e => set('objections_raised', e.target.value)} />
              </div>
              <div className="cf-field-group">
                <label className="cf-label">Client Concerns</label>
                <textarea className="cf-textarea" rows={3}
                  placeholder="What concerns did the client express?"
                  value={form.client_concerns} onChange={e => set('client_concerns', e.target.value)} />
              </div>
              <div className="cf-field-group">
                <label className="cf-label">Referral Potential</label>
                <div className="cf-pill-row">
                  <div className={`cf-pill ${form.referral_potential ? 'cf-pill--active' : ''}`}
                    onClick={() => set('referral_potential', !form.referral_potential)}>
                    {form.referral_potential ? '👥 Yes — Referral Noted' : '👥 Mark as Referral Potential'}
                  </div>
                </div>
                {form.referral_potential && (
                  <input className="cf-input" style={{ marginTop: 8 }}
                    placeholder="Who might they refer? Any names mentioned?"
                    value={form.referral_notes} onChange={e => set('referral_notes', e.target.value)} />
                )}
              </div>
              <div className="cf-field-group cf-field-group--full">
                <div className="cf-divider">Next Steps</div>
              </div>
              <div className="cf-field-group cf-field-group--full">
                <label className="cf-label">Case Status</label>
                <div className="cf-status-grid">
                  {Object.entries(CASE_STATUS_LABELS).map(([k, v]) => (
                    <div key={k}
                      className={`cf-status-card ${form.case_status === k ? 'cf-status-card--active' : ''}`}
                      onClick={() => set('case_status', k)}>
                      {v}
                    </div>
                  ))}
                </div>
              </div>
              <div className="cf-field-group cf-field-group--full">
                <label className="cf-label">Next Action</label>
                <div className="cf-pill-row cf-pill-row--wrap">
                  {Object.entries(NEXT_ACTION_LABELS).map(([k, v]) => (
                    <div key={k} className={`cf-pill ${form.next_action === k ? 'cf-pill--active' : ''}`}
                      onClick={() => set('next_action', form.next_action === k ? '' : k)}>
                      {v}
                    </div>
                  ))}
                </div>
              </div>
              {form.next_action && form.next_action !== 'none' && (
                <>
                  <div className="cf-field-group">
                    <label className="cf-label">Follow-Up Date</label>
                    <input className="cf-input" type="date"
                      value={form.next_action_date} onChange={e => set('next_action_date', e.target.value)} />
                  </div>
                  <div className="cf-field-group">
                    <label className="cf-label">Follow-Up Notes</label>
                    <input className="cf-input" placeholder="What specifically needs to happen?"
                      value={form.next_action_notes} onChange={e => set('next_action_notes', e.target.value)} />
                  </div>
                </>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="cf-footer">
          <div className="cf-footer-left">
            <div className="cf-btn cf-btn--ghost" onClick={startNew}>+ New Entry</div>
            {selectedFileId && (
              <>
                <div className={`cf-btn cf-btn--crm ${crmPushing ? 'cf-btn--loading' : ''}`}
                  onClick={crmPushing ? null : handleCrmPush}>
                  {crmPushing ? '⏳ Pushing...' : '🔗 Push to CRM'}
                </div>
                <div className="cf-btn cf-btn--danger" onClick={() => setShowCloseModal(true)}>
                  🔒 Close Case
                </div>
              </>
            )}
          </div>
          <div className="cf-footer-right">
            <div className="cf-btn cf-btn--ghost" onClick={onClose}>Cancel</div>
            <div className={`cf-btn cf-btn--primary ${saving ? 'cf-btn--loading' : ''}`}
              onClick={saving ? null : handleSave}>
              {saving ? '⏳ Saving...' : selectedFileId ? '💾 Save Changes' : '💾 Create Case File'}
            </div>
          </div>
        </div>

        {/* Toast */}
        {toast && (
          <div className={`cf-toast cf-toast--${toast.type}`}>{toast.msg}</div>
        )}

        {/* Close Case Modal */}
        {showCloseModal && (
          <div className="cf-modal-overlay" onClick={() => setShowCloseModal(false)}>
            <div className="cf-modal" onClick={e => e.stopPropagation()}>
              <div className="cf-modal-title">🔒 Close This Case</div>
              <div className="cf-modal-body">
                <label className="cf-label">Outcome</label>
                <div className="cf-pill-row">
                  <div className={`cf-pill ${closeOutcome === 'closed_won' ? 'cf-pill--won' : ''}`}
                    onClick={() => setCloseOutcome('closed_won')}>🏆 Closed — Won</div>
                  <div className={`cf-pill ${closeOutcome === 'closed_lost' ? 'cf-pill--lost' : ''}`}
                    onClick={() => setCloseOutcome('closed_lost')}>❌ Closed — Lost</div>
                </div>
                <label className="cf-label" style={{ marginTop: 12 }}>Close Notes (optional)</label>
                <textarea className="cf-textarea" rows={3}
                  placeholder="Any final notes about why the case closed..."
                  value={closeNotes} onChange={e => setCloseNotes(e.target.value)} />
                <div className="cf-modal-warning">
                  This will mark the lead as {closeOutcome === 'closed_won' ? 'won' : 'lost'} and
                  stop all future AI outreach for this contact.
                </div>
              </div>
              <div className="cf-modal-footer">
                <div className="cf-btn cf-btn--ghost" onClick={() => setShowCloseModal(false)}>Cancel</div>
                <div className={`cf-btn ${closeOutcome === 'closed_won' ? 'cf-btn--primary' : 'cf-btn--danger'} ${closing ? 'cf-btn--loading' : ''}`}
                  onClick={closing ? null : handleClose}>
                  {closing ? '⏳ Closing...' : `Confirm — ${closeOutcome === 'closed_won' ? 'Won ✓' : 'Lost ✗'}`}
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
