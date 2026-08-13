import { useEffect, useState, useRef, useMemo, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import MessageReview from '../components/MessageReview'
import '../styles/shared.css'
import './Leads.css'
import VoiceCampaign from '../components/VoiceCampaign'

const TIER_OPTIONS = [
  { value: 'pre_need', label: 'Pre-Need' },
  { value: 'at_need', label: 'At-Need' },
  { value: 'imminent', label: 'Imminent' },
  { value: 'contract_sold', label: 'Contract Sold' },
  { value: 'new_inquiry', label: 'New Inquiry' },
]

const TIER_FILTER_OPTIONS = [
  { value: '', label: 'All tiers' },
  { value: 'pre_need', label: 'Pre-Need' },
  { value: 'at_need', label: 'At-Need' },
  { value: 'imminent', label: 'Imminent' },
  { value: 'contract_sold', label: 'Contract Sold' },
  { value: 'new_inquiry', label: 'New Inquiry' },
  { value: 'email_only', label: 'Email Only' },
  { value: 'partial', label: 'Needs Review' },
]

const STATUS_FILTER_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'new', label: 'New' },
  { value: 'sent', label: 'Sent' },
  { value: 'replied', label: 'Replied' },
  { value: 'hot', label: 'Hot' },
  { value: 'booked', label: 'Booked' },
  { value: 'dnc', label: 'DNC' },
]

export default function Leads() {
  const navigate = useNavigate()
  const [leads, setLeads] = useState([])
  const [leadsTotal, setLeadsTotal] = useState(0)
  const [needsReview, setNeedsReview] = useState([])
  const [needsReviewTotal, setNeedsReviewTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [preview, setPreview] = useState(null)
  const [previewing, setPreviewing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [sourceYear, setSourceYear] = useState('')
  const [importRelationshipType, setImportRelationshipType] = useState('cold_lead')
  const [importListName, setImportListName] = useState('')
  const [importCampaignPurpose, setImportCampaignPurpose] = useState('')
  const [importOfferHook, setImportOfferHook] = useState('')
  const [forceNewInquiry, setForceNewInquiry] = useState(false)
  const [batchFilter, setBatchFilter] = useState('')   // filter leads by import_list_name
  const [bulkAiStarting, setBulkAiStarting] = useState(false)
  const [bulkAiStartResult, setBulkAiStartResult] = useState(null)
  const [view, setView] = useState('all')
  const [reviewLeadIds, setReviewLeadIds] = useState(null)
  const [showImport, setShowImport] = useState(false)
  const fileInputRef = useRef(null)
  const pendingFile = useRef(null)
  const [googleImporting, setGoogleImporting] = useState(false)
  const [googleImportResult, setGoogleImportResult] = useState(null)
  const [showAddLead, setShowAddLead] = useState(false)
  const [addLeadForm, setAddLeadForm] = useState({ first_name: '', last_name: '', phone: '', email: '', tier: 'pre_need', source_year: '' })
  const [addLeadSaving, setAddLeadSaving] = useState(false)
  const [importError, setImportError] = useState('')
  const [addLeadResult, setAddLeadResult] = useState(null)

  const [searchQuery, setSearchQuery] = useState('')
  const [tierFilter, setTierFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const [selected, setSelected] = useState(new Set())
  const [bulkMessage, setBulkMessage] = useState('')
  const [bulkIncludeBooking, setBulkIncludeBooking] = useState(true)
  const [bulkSending, setBulkSending] = useState(false)
  const [bulkResult, setBulkResult] = useState(null)
  const [aiTone, setAiTone] = useState('warm')
  const [aiChannel, setAiChannel] = useState('sms') // sms | email | both
  const [aiActioning, setAiActioning] = useState(null) // null | 'queue' | 'send_sms' | 'send_email' | 'send_both'
  const [aiResult, setAiResult] = useState(null)
  const [showBulkCompose, setShowBulkCompose] = useState(false)
  const [bulkAiDirection, setBulkAiDirection] = useState('')
  const [bulkRelationshipType, setBulkRelationshipType] = useState('')
  const [bulkAiGenerating, setBulkAiGenerating] = useState(false)
  const [bulkAiError, setBulkAiError] = useState('')
  // Phase 4: media attach for batch sends
  const [bulkMediaUrl, setBulkMediaUrl] = useState('')
  const [bulkMediaUploading, setBulkMediaUploading] = useState(false)
  const [bulkMediaFileName, setBulkMediaFileName] = useState('')
  const bulkMediaInputRef = useRef(null)

  const currentUser = getCurrentUser()
  const canBulkAssign = currentUser?.role === 'org_admin' || currentUser?.role === 'super_admin'
  const [assignableUsers, setAssignableUsers] = useState([])
  const [showBulkAssign, setShowBulkAssign] = useState(false)
  const [bulkAssignTarget, setBulkAssignTarget] = useState('')
  const [bulkAssigning, setBulkAssigning] = useState(false)
  const [bulkAssignResult, setBulkAssignResult] = useState(null)
  const [bulkAssignError, setBulkAssignError] = useState('')
  const [showVoiceCampaign, setShowVoiceCampaign] = useState(false)

  // Import history
  const [importBatches, setImportBatches] = useState([])
  const [batchesLoading, setBatchesLoading] = useState(false)
  const [deletingBatch, setDeletingBatch] = useState(null)   // source_file being deleted
  const [deleteConfirm, setDeleteConfirm] = useState(null)   // batch object awaiting confirm
  const canManageBatches = currentUser?.role === 'org_admin' || currentUser?.role === 'super_admin'
  const [deletingLeads, setDeletingLeads] = useState(false)
  const [dedupeRunning, setDedupeRunning] = useState(false)
  const [dedupeResult, setDedupeResult] = useState(null)

  function loadImportBatches() {
    // All advisors load batches (needed for batch filter dropdown)
    setBatchesLoading(true)
    api.get('/leads/import-batches').then(setImportBatches).catch(() => {}).finally(() => setBatchesLoading(false))
  }

  async function handleDedupeEmailLeads() {
    if (!window.confirm('Scan all email-only leads and flag duplicates (same name + email address)?\n\nThis is safe — nothing gets deleted, just flagged. You can review and then bulk-delete the flagged ones.')) return
    setDedupeRunning(true)
    setDedupeResult(null)
    try {
      const result = await api.post('/leads/deduplicate-email-leads', {})
      setDedupeResult(result)
      loadLeads()
    } catch (err) {
      setDedupeResult({ error: err.message })
    } finally {
      setDedupeRunning(false)
    }
  }

  async function handleDeleteBatch(batch) {
    setDeletingBatch(batch.source_file)
    try {
      await api.delete(`/leads/import-batches?source_file=${encodeURIComponent(batch.source_file)}`)
      setDeleteConfirm(null)
      loadLeads()
      loadImportBatches()
    } catch (err) {
      alert(`Delete failed: ${err.message}`)
    } finally {
      setDeletingBatch(null)
    }
  }

  function loadLeads(filterBatch) {
    setLoading(true)
    setLoadError(null)
    // filterBatch may be passed directly (e.g. from batch filter change); fall back to state
    const activeBatch = filterBatch !== undefined ? filterBatch : batchFilter
    const batchParam = activeBatch ? `&import_list_name=${encodeURIComponent(activeBatch)}` : ''
    Promise.all([
      api.get(`/leads/?page=1&page_size=500${batchParam}`),
      api.get('/leads/needs-review?page=1&page_size=500'),
    ]).then(([leadsData, reviewData]) => {
      // Both endpoints return a paginated envelope {items, total, page, page_size}.
      // Fall back to raw array for backward-compatibility during rolling deploys.
      const leadsArr = Array.isArray(leadsData) ? leadsData : (leadsData.items ?? [])
      const reviewArr = Array.isArray(reviewData) ? reviewData : (reviewData.items ?? [])
      setLeads(leadsArr)
      setLeadsTotal(Array.isArray(leadsData) ? leadsData.length : (leadsData.total ?? leadsArr.length))
      setNeedsReview(reviewArr)
      setNeedsReviewTotal(Array.isArray(reviewData) ? reviewData.length : (reviewData.total ?? reviewArr.length))
      setLoading(false)
    }).catch((err) => {
      setLeads([])
      setNeedsReview([])
      setLoadError(err?.message || 'Failed to load leads')
      setLoading(false)
    })
  }

  useEffect(() => { loadLeads(); loadImportBatches() }, [])

  async function handleGoogleContactsImport() {
    setGoogleImporting(true)
    setGoogleImportResult(null)
    try {
      const result = await api.post('/google-contacts/import', {})
      setGoogleImportResult(result)
      loadLeads()
    } catch (err) {
      setGoogleImportResult({ error: err.message || 'Import failed. Make sure Google is connected in Settings.' })
    } finally {
      setGoogleImporting(false)
    }
  }

  async function handleFileChange(e) {
    const file = e.target.files[0]
    if (!file) return
    pendingFile.current = file
    setPreviewing(true)
    setPreview(null)
    setImportError('')
    try {
      const formData = new FormData()
      formData.append('file', file)
      if (sourceYear) formData.append('source_year', sourceYear)
      if (importRelationshipType) formData.append('relationship_type', importRelationshipType)
      if (importListName.trim()) formData.append('import_list_name', importListName.trim())
      if (importCampaignPurpose) formData.append('campaign_purpose', importCampaignPurpose)
      if (importOfferHook) formData.append('offer_hook', importOfferHook)
      if (forceNewInquiry) formData.append('force_new_inquiry', 'true')
      const result = await api.upload('/leads/upload/preview', formData)
      setPreview(result)
    } catch (err) {
      setImportError(`Preview failed: ${err.message}`)
    } finally {
      setPreviewing(false)
    }
  }

  async function handleConfirmUpload() {
    if (!pendingFile.current) return
    setConfirming(true)
    try {
      const formData = new FormData()
      formData.append('file', pendingFile.current)
      if (sourceYear) formData.append('source_year', sourceYear)
      if (importRelationshipType) formData.append('relationship_type', importRelationshipType)
      if (importListName.trim()) formData.append('import_list_name', importListName.trim())
      if (importCampaignPurpose) formData.append('campaign_purpose', importCampaignPurpose)
      if (importOfferHook) formData.append('offer_hook', importOfferHook)
      if (forceNewInquiry) formData.append('force_new_inquiry', 'true')
      const result = await api.upload('/leads/upload/confirm', formData)
      setPreview(null)
      pendingFile.current = null
      setForceNewInquiry(false)
      setShowImport(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
      loadLeads()
      if (result.created_lead_ids && result.created_lead_ids.length > 0) {
        setReviewLeadIds(result.created_lead_ids)
      }
    } catch (err) {
      setImportError(`Import failed: ${err.message}`)
    } finally {
      setConfirming(false)
    }
  }

  function cancelPreview() {
    setPreview(null)
    pendingFile.current = null
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  async function assignTier(leadId, tier) {
    try {
      await api.patch(`/leads/${leadId}/tier?new_tier=${tier}`, {})
      loadLeads()
    } catch (err) {
      alert(`Failed to set tier: ${err.message}`)
    }
  }

  const baseLeads = view === 'review' ? needsReview : view === 'duplicates' ? leads.filter((l) => l.is_duplicate) : leads.filter((l) => !l.is_duplicate)

  const filteredLeads = useMemo(() => {
    let result = baseLeads
    if (tierFilter) result = result.filter((l) => l.tier === tierFilter)
    if (statusFilter) result = result.filter((l) => l.status === statusFilter)
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase()
      const qDigits = q.replace(/\D/g, '')
      result = result.filter((l) => {
        const name = `${l.first_name || ''} ${l.last_name || ''}`.toLowerCase()
        const phoneDigits = (l.phone || '').replace(/\D/g, '')
        const email = (l.email || '').toLowerCase()
        return name.includes(q) || email.includes(q) || (qDigits.length > 0 && phoneDigits.includes(qDigits))
      })
    }
    return result
  }, [baseLeads, tierFilter, statusFilter, searchQuery])

  const sendableLeads = filteredLeads.filter((l) => l.phone && l.status !== 'dnc' && !l.is_duplicate)
  const sendableSelectedIds = Array.from(selected).filter((id) => sendableLeads.some((l) => l.id === id))

  function toggleSelect(id) {
    const next = new Set(selected)
    next.has(id) ? next.delete(id) : next.add(id)
    setSelected(next)
  }

  function toggleSelectAll() {
    const sendableIds = sendableLeads.map((l) => l.id)
    const allSelected = sendableIds.length > 0 && sendableIds.every((id) => selected.has(id))
    if (allSelected) {
      const next = new Set(selected)
      sendableIds.forEach((id) => next.delete(id))
      setSelected(next)
    } else {
      setSelected(new Set([...selected, ...sendableIds]))
    }
  }

  async function handleAiAction(mode) {
    // mode: 'queue' | 'send_sms' | 'send_email' | 'send_both'
    const ids = Array.from(selected)
    if (ids.length === 0) return
    setAiActioning(mode)
    setAiResult(null)
    try {
      const autoSend = mode !== 'queue'
      const channel = mode === 'send_email' ? 'email' : mode === 'send_both' ? 'both' : 'sms'

      const result = await api.post('/ai-conversation/generate-batch', {
        lead_ids: ids,
        tone: aiTone,
        auto_send: autoSend,
        channel,
        ai_direction: bulkAiDirection.trim() || null,
        relationship_type: bulkRelationshipType || null,
      })
      setAiResult({ mode, ...result })
      if (!autoSend) {
        // Queued — tell user to go check Auto-Send Queue
      }
    } catch (err) {
      setAiResult({ error: err.message })
    } finally {
      setAiActioning(null)
    }
  }

  // AI generate: preview from first selected lead → fills textarea for review/edit
  async function handleBulkAiGenerate() {
    const firstId = sendableSelectedIds[0]
    if (!firstId) return
    setBulkAiGenerating(true)
    setBulkAiError('')
    try {
      const result = await api.post('/ai-conversation/preview', {
        lead_id: firstId,
        tone: aiTone,
        ai_direction: bulkAiDirection.trim() || null,
      })
      if (result.message) {
        setBulkMessage(result.message)
      } else {
        setBulkAiError('AI returned no message.')
      }
    } catch (err) {
      setBulkAiError(err.message || 'AI generate failed')
    } finally {
      setBulkAiGenerating(false)
    }
  }

  async function handleBulkMediaUpload(e) {
    const file = e.target.files[0]
    if (!file) return
    setBulkMediaUploading(true)
    try {
      const fd = new FormData()
      fd.append('file', file)
      const result = await api.upload('/sms/upload-media', fd)
      setBulkMediaUrl(result.media_url)
      setBulkMediaFileName(file.name)
    } catch (err) {
      alert(`Upload failed: ${err.message}`)
    } finally {
      setBulkMediaUploading(false)
      e.target.value = ''
    }
  }

  async function handleBulkSend() {
    if (!bulkMessage.trim() || sendableSelectedIds.length === 0) return
    setBulkSending(true)
    setBulkResult(null)
    try {
      let result
      if (bulkMediaUrl) {
        // MMS batch: fire individually (send-batch doesn't support MMS yet)
        const sends = await Promise.allSettled(
          sendableSelectedIds.map(id =>
            api.post('/sms/send-mms', {
              lead_id: id,
              template: bulkMessage,
              media_url: bulkMediaUrl,
              include_booking_link: bulkIncludeBooking,
            })
          )
        )
        const sent = sends.filter(r => r.status === 'fulfilled').length
        result = { sent_count: sent, skipped_count: sendableSelectedIds.length - sent }
      } else {
        result = await api.post('/sms/send-batch', {
          lead_ids: sendableSelectedIds,
          template: bulkMessage,
          include_booking_link: bulkIncludeBooking,
        })
      }
      setBulkResult(result)
      setSelected(new Set())
      setBulkMessage('')
      setBulkMediaUrl('')
      setBulkMediaFileName('')
      loadLeads()
    } catch (err) {
      alert(`Bulk send failed: ${err.message}`)
    } finally {
      setBulkSending(false)
    }
  }

  async function handleBulkAiStart() {
    if (selected.size === 0) return
    const count = selected.size
    if (!window.confirm(`Start AI conversations on ${count} lead${count === 1 ? '' : 's'}?\n\nThis will send the first AI outreach message to each lead immediately. Leads that are already active or on DNC will be skipped.`)) return
    setBulkAiStarting(true)
    setBulkAiStartResult(null)
    try {
      const result = await api.post('/ai-conversation/bulk-start', {
        lead_ids: Array.from(selected),
        channel: 'auto',  // routes by each lead's contact_channel
      })
      setBulkAiStartResult(result)
      setSelected(new Set())
      loadLeads()
    } catch (err) {
      setBulkAiStartResult({ error: err.message || 'Bulk AI start failed.' })
    } finally {
      setBulkAiStarting(false)
    }
  }

  async function handleBulkAssign() {
    if (selected.size === 0) return
    setBulkAssigning(true)
    setBulkAssignError('')
    setBulkAssignResult(null)
    try {
      const result = await api.post('/admin/leads/reassign', {
        lead_ids: Array.from(selected),
        new_assigned_to_id: bulkAssignTarget || null,
      })
      setBulkAssignResult(result)
      setSelected(new Set())
      setShowBulkAssign(false)
      setBulkAssignTarget('')
      loadLeads()
    } catch (err) {
      setBulkAssignError(err.message || 'Bulk assign failed.')
    } finally {
      setBulkAssigning(false)
    }
  }

  const selectedCount = selected.size

  const stats = useMemo(() => ({
    total: leadsTotal,          // real total from server, not capped at 500
    shown: filteredLeads.length,
    sendable: sendableLeads.length,
    selected: selectedCount,
    needsReview: needsReviewTotal, // real total from server
    dnc: leads.filter((l) => l.status === 'dnc').length,
    missingPhone: leads.filter((l) => !l.phone).length,
    duplicates: leads.filter((l) => l.is_duplicate).length,
  }), [leadsTotal, needsReviewTotal, leads, filteredLeads.length, sendableLeads.length, selectedCount])

  useEffect(() => {
    if (!canBulkAssign) return
    api.get('/admin/users')
      .then((users) => setAssignableUsers(users.filter((u) => u.is_active && (u.role === 'advisor' || u.role === 'org_admin'))))
      .catch(() => {})
  }, [canBulkAssign])

  async function handleAddLead() {
    if (!addLeadForm.first_name.trim() || !addLeadForm.last_name.trim()) return
    setAddLeadSaving(true)
    setAddLeadResult(null)
    try {
      const result = await api.post('/leads/create', {
        ...addLeadForm,
        source_year: addLeadForm.source_year ? parseInt(addLeadForm.source_year) : null,
      })
      setAddLeadResult(result)
      setAddLeadForm({ first_name: '', last_name: '', phone: '', email: '', tier: 'pre_need', source_year: '' })
      loadLeads()
    } catch (err) {
      setAddLeadResult({ error: err.message || 'Could not create lead.' })
    } finally {
      setAddLeadSaving(false)
    }
  }

  async function handleDeleteLead(e, leadId) {
    e.stopPropagation()
    if (deletingLeads) return
    if (!window.confirm('Permanently delete this lead? This cannot be undone.')) return
    setDeletingLeads(true)
    try {
      await api.delete(`/leads/${leadId}`)
      loadLeads()
    } catch (err) {
      alert(`Delete failed: ${err.message}`)
    } finally {
      setDeletingLeads(false)
    }
  }

  async function handleDeleteSelected() {
    if (deletingLeads) return
    if (!window.confirm(`Permanently delete ${selectedCount} leads? This cannot be undone.`)) return
    setDeletingLeads(true)
    try {
      const results = await Promise.allSettled([...selected].map(id => api.delete(`/leads/${id}`).then(() => id)))
      const deletedIds = results.filter(r => r.status === 'fulfilled').map(r => r.value)
      setSelected(prev => {
        const next = new Set(prev)
        deletedIds.forEach(id => next.delete(id))
        return next
      })
      loadLeads()
    } catch (err) {
      alert(`Delete failed: ${err.message}`)
    } finally {
      setDeletingLeads(false)
    }
  }

  return (
    <div className="leads-page">

      {/* ── Header ── */}
      <header className="leads-header">
        <div>
          <p className="leads-eyebrow">Lead operations</p>
          <h1 className="page-title">Leads</h1>
          <p className="page-subtitle">Import, dedupe, search, assign, and send from one control surface.</p>
        </div>
        <div style={{ display: 'flex', gap: 10 }}>
          <button className="btn btn--secondary" onClick={() => { setShowAddLead(!showAddLead); setShowImport(false) }}>
            {showAddLead ? '✕ Cancel' : '+ Add lead'}
          </button>
          <button className="btn btn--primary leads-import-btn" onClick={() => { setShowImport(!showImport); setShowAddLead(false) }}>
            {showImport ? '✕ Close import' : '⬆ Import leads'}
          </button>
        </div>
      </header>

      {showAddLead && (
        <section className="panel leads-add-panel">
          <div className="panel-header"><h2 className="panel-title">Add a lead manually</h2></div>
          <div className="leads-add-grid">
            <label className="leads-add-label">First name *
              <input className="search-input" value={addLeadForm.first_name} onChange={(e) => setAddLeadForm((p) => ({ ...p, first_name: e.target.value }))} placeholder="First name" />
            </label>
            <label className="leads-add-label">Last name *
              <input className="search-input" value={addLeadForm.last_name} onChange={(e) => setAddLeadForm((p) => ({ ...p, last_name: e.target.value }))} placeholder="Last name" />
            </label>
            <label className="leads-add-label">Phone
              <input className="search-input" value={addLeadForm.phone} onChange={(e) => setAddLeadForm((p) => ({ ...p, phone: e.target.value }))} placeholder="214-555-0199" />
            </label>
            <label className="leads-add-label">Email
              <input className="search-input" value={addLeadForm.email} onChange={(e) => setAddLeadForm((p) => ({ ...p, email: e.target.value }))} placeholder="name@email.com" />
            </label>
            <label className="leads-add-label">Tier
              <select className="filter-select" value={addLeadForm.tier} onChange={(e) => setAddLeadForm((p) => ({ ...p, tier: e.target.value }))}>
                {TIER_OPTIONS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
              </select>
            </label>
            <label className="leads-add-label">Source year
              <input className="search-input" value={addLeadForm.source_year} onChange={(e) => setAddLeadForm((p) => ({ ...p, source_year: e.target.value }))} placeholder="2024" type="number" />
            </label>
          </div>
          <div style={{ display: 'flex', gap: 10, marginTop: 14, alignItems: 'center' }}>
            <button
              className="btn btn--primary"
              onClick={handleAddLead}
              disabled={addLeadSaving || !addLeadForm.first_name.trim() || !addLeadForm.last_name.trim()}
            >
              {addLeadSaving ? 'Saving…' : 'Create lead'}
            </button>
            {addLeadResult && !addLeadResult.error && (
              <span style={{ color: 'var(--signal-green)', fontSize: 13 }}>
                ✓ {addLeadResult.name} created{addLeadResult.is_duplicate ? ' (flagged as potential duplicate)' : ''}
              </span>
            )}
            {addLeadResult?.error && (
              <span style={{ color: 'var(--signal-red)', fontSize: 13 }}>{addLeadResult.error}</span>
            )}
          </div>
        </section>
      )}

      {/* ── KPI Cards ── */}
      <div className="leads-kpi-grid">
        {[
          { label: 'TOTAL LEADS', value: stats.total, accent: 'blue', icon: '👥' },
          { label: 'SMS READY', value: stats.sendable, accent: 'green', icon: '📱', sub: 'Phone, not DNC, not duplicate' },
          { label: 'NEEDS REVIEW', value: stats.needsReview, accent: 'amber', icon: '⚠️', sub: 'Assign tier before outreach', action: () => { setView('review'); setSelected(new Set()); } },
          { label: 'BLOCKED', value: stats.dnc + stats.duplicates + stats.missingPhone, accent: 'red', icon: '🚫', sub: 'DNC, duplicate, or no phone' },
        ].map(({ label, value, accent, icon, sub, action }) => (
          <div key={label} className={`leads-kpi-card leads-kpi-card--${accent}`} onClick={action} style={{ cursor: action ? 'pointer' : 'default' }}>
            <div className="leads-kpi-top">
              <span className="leads-kpi-label">{label}</span>
              <span className="leads-kpi-icon">{icon}</span>
            </div>
            <div className={`leads-kpi-value leads-kpi-value--${accent}`}>{loading ? '—' : value}</div>
            {sub && <div className="leads-kpi-sub">{sub}</div>}
          </div>
        ))}
      </div>

      {/* ── Import Panel ── */}
      {showImport && (
        <section className="panel leads-import-panel">
          <div className="panel-header">
            <h2 className="panel-title">Import leads</h2>
          </div>
          <div className="leads-import-row" style={{ flexWrap: 'wrap', gap: 10 }}>
            <input
              type="number"
              placeholder="Source year (optional)"
              value={sourceYear}
              onChange={(e) => setSourceYear(e.target.value)}
              className="settings-input leads-year-input"
            />
            <input
              type="text"
              placeholder="List name (optional, e.g. 'Restland Q1 2024')"
              value={importListName}
              onChange={(e) => setImportListName(e.target.value)}
              className="settings-input"
              style={{ minWidth: 200 }}
            />
            <select
              value={importRelationshipType}
              onChange={(e) => setImportRelationshipType(e.target.value)}
              className="filter-select"
              title="AI will use this relationship context for all leads in this import"
            >
              <option value="cold_lead">❄️ Cold leads (default)</option>
              <option value="warm_lead">☀️ Warm leads</option>
              <option value="re_engagement">🔄 Re-engagement</option>
              <option value="previous_prospect">📋 Previous prospects</option>
              <option value="past_customer">🤝 Past customers</option>
              <option value="existing_customer">⭐ Existing customers</option>
            </select>
            <select
              value={importCampaignPurpose}
              onChange={(e) => setImportCampaignPurpose(e.target.value)}
              className="filter-select"
              title="Campaign purpose — AI uses this to set the right message goal"
            >
              <option value="">🎯 Campaign purpose (optional)</option>
              <option value="file_review">📁 File review / reconnect</option>
              <option value="markers">🪦 Markers / memorials</option>
              <option value="pre_need">📋 Pre-need planning</option>
              <option value="at_need_followup">💙 At-need follow-up</option>
              <option value="upsell_existing">⭐ Existing client upsell</option>
              <option value="event_invite">🎟 Event invitation</option>
              <option value="re_engagement">🔄 Re-engagement / check-in</option>
            </select>
            <select
              value={importOfferHook}
              onChange={(e) => setImportOfferHook(e.target.value)}
              className="filter-select"
              title="Offer hook — AI will naturally weave this into outreach messages"
            >
              <option value="">🎁 Offer hook (optional)</option>
              <option value="lunch_and_learn">🍽 Lunch & Learn event</option>
              <option value="free_tour">🚪 Free funeral home tour</option>
              <option value="free_space">🌿 Free space consultation</option>
              <option value="family_service_consult">🤝 Free Family Service consult</option>
            </select>
            <label className="compose-checkbox">
              <input
                type="checkbox"
                checked={forceNewInquiry}
                onChange={(e) => setForceNewInquiry(e.target.checked)}
              />
              Tag whole file as New Inquiry
            </label>
            <input
              ref={fileInputRef}
              type="file"
              accept=".xlsx,.csv,.xls"
              onChange={handleFileChange}
              style={{ display: 'none' }}
            />
            <button
              className="btn btn--primary"
              onClick={() => fileInputRef.current?.click()}
              disabled={previewing}
              style={{ minWidth: 140 }}
            >
              {previewing ? '⏳ Checking…' : '📂 Choose file & preview'}
            </button>
            <button
              className="btn btn--secondary"
              onClick={handleGoogleContactsImport}
              disabled={googleImporting}
            >
              {googleImporting ? 'Importing…' : '📇 Google Contacts'}
            </button>
          </div>

          {googleImportResult && (
            <div className={`leads-import-result ${googleImportResult.error ? 'leads-import-result--error' : 'leads-import-result--success'}`}>
              {googleImportResult.error || `✓ Imported ${googleImportResult.new_active_sms_leads || 0} leads from Google Contacts.`}
            </div>
          )}

          {previewing && <div className="empty-state">Checking for duplicates and routing tiers…</div>}
          {importError && (
            <div className="compose-error" style={{ marginTop: 12, padding: '10px 14px', borderRadius: 8, background: 'rgba(255,77,77,0.1)', color: 'var(--signal-red)', fontSize: 13 }}>
              ⚠️ {importError}
            </div>
          )}

          {preview && (
            <div className="leads-preview-box">
              <div className="leads-preview-grid">
                <PreviewStat label="Total rows" value={preview.total_rows} />
                <PreviewStat label="Active SMS leads" value={preview.new_active_sms_leads} accent="green" />
                <PreviewStat label="Email-only queued" value={preview.email_only_leads_queued} accent="blue" />
                <PreviewStat label="Duplicates flagged" value={preview.duplicates_flagged} accent="amber" />
                <PreviewStat label="Call-restricted" value={preview.flagged_call_restricted} accent="red" />
                <PreviewStat label="Needs tier review" value={preview.flagged_needs_tier_review} accent="amber" />
              </div>
              <div className="leads-tier-chips">
                {Object.entries(preview.tier_breakdown || {}).map(([tier, count]) => (
                  <span key={tier} className="tier-chip">
                    <TierBadge tier={tier} /> <span className="mono">{count}</span>
                  </span>
                ))}
              </div>
              <div className="leads-preview-actions">
                <button className="btn btn--secondary" onClick={cancelPreview}>Cancel</button>
                <button className="btn btn--primary" onClick={handleConfirmUpload} disabled={confirming}>
                  {confirming ? 'Importing…' : `Confirm import of ${preview.imported} leads`}
                </button>
              </div>
            </div>
          )}

          {/* ── Import History ── */}
          {canManageBatches && (
            <div style={{ marginTop: 24, borderTop: '1px solid var(--border)', paddingTop: 16 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
                <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-secondary)' }}>📋 Import history</span>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button
                    className="btn btn--secondary"
                    style={{ fontSize: 12, padding: '3px 12px' }}
                    onClick={handleDedupeEmailLeads}
                    disabled={dedupeRunning}
                    title="Finds and flags email-only leads that share the same name + email address"
                  >
                    {dedupeRunning ? '⏳ Scanning…' : '🧹 Clean up email dupes'}
                  </button>
                  <button className="btn btn--ghost" style={{ fontSize: 12, padding: '3px 10px' }} onClick={loadImportBatches}>↻ Refresh</button>
                </div>
              </div>
              {dedupeResult && (
                <div style={{
                  padding: '10px 14px', borderRadius: 8, marginBottom: 12, fontSize: 13,
                  background: dedupeResult.error ? 'var(--signal-red-dim)' : 'var(--signal-green-dim)',
                  color: dedupeResult.error ? 'var(--signal-red)' : 'var(--signal-green)',
                  border: `1px solid ${dedupeResult.error ? 'var(--signal-red)' : 'var(--signal-green)'}`,
                }}>
                  {dedupeResult.error ? `⚠️ ${dedupeResult.error}` : `✓ ${dedupeResult.message}`}
                  {!dedupeResult.error && dedupeResult.newly_flagged > 0 && (
                    <span style={{ marginLeft: 12, fontSize: 12, opacity: 0.85 }}>
                      Now use "Duplicates" tab to bulk-delete them.
                    </span>
                  )}
                </div>
              )}
              {batchesLoading && <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>Loading…</div>}
              {!batchesLoading && importBatches.length === 0 && (
                <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>No import batches found.</div>
              )}
              {!batchesLoading && importBatches.length > 0 && (
                <table style={{ width: '100%', fontSize: 13, borderCollapse: 'collapse' }}>
                  <thead>
                    <tr style={{ color: 'var(--text-secondary)', textAlign: 'left' }}>
                      <th style={{ padding: '4px 8px', fontWeight: 500 }}>File</th>
                      <th style={{ padding: '4px 8px', fontWeight: 500, textAlign: 'right' }}>Leads</th>
                      <th style={{ padding: '4px 8px', fontWeight: 500 }}>Imported</th>
                      <th style={{ padding: '4px 8px' }}></th>
                    </tr>
                  </thead>
                  <tbody>
                    {importBatches.map((batch) => (
                      <tr key={batch.source_file} style={{ borderTop: '1px solid var(--border)' }}>
                        <td style={{ padding: '6px 8px', maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={batch.source_file}>
                          {batch.source_file}
                        </td>
                        <td style={{ padding: '6px 8px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{batch.lead_count.toLocaleString()}</td>
                        <td style={{ padding: '6px 8px', color: 'var(--text-secondary)' }}>
                          {batch.imported_at ? new Date(batch.imported_at).toLocaleDateString() : '—'}
                        </td>
                        <td style={{ padding: '6px 8px', textAlign: 'right' }}>
                          {deleteConfirm?.source_file === batch.source_file ? (
                            <span style={{ display: 'flex', gap: 6, justifyContent: 'flex-end', alignItems: 'center' }}>
                              <span style={{ fontSize: 12, color: 'var(--signal-red)' }}>Delete {batch.lead_count.toLocaleString()} leads?</span>
                              <button
                                className="btn btn--danger"
                                style={{ fontSize: 12, padding: '2px 10px' }}
                                disabled={deletingBatch === batch.source_file}
                                onClick={() => handleDeleteBatch(batch)}
                              >
                                {deletingBatch === batch.source_file ? 'Deleting…' : 'Yes, delete'}
                              </button>
                              <button className="btn btn--ghost" style={{ fontSize: 12, padding: '2px 8px' }} onClick={() => setDeleteConfirm(null)}>Cancel</button>
                            </span>
                          ) : (
                            <button
                              className="btn btn--ghost"
                              style={{ fontSize: 12, padding: '2px 10px', color: 'var(--signal-red)' }}
                              onClick={() => setDeleteConfirm(batch)}
                            >
                              🗑 Delete batch
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </section>
      )}

      {/* ── Tabs + Filter Bar ── */}
      <div className="leads-controls">
        <div className="leads-tabs">
          <button className={`tab ${view === 'all' ? 'tab--active' : ''}`} onClick={() => { setView('all'); setSelected(new Set()); }}>
            All leads <span className="mono">{leads.filter(l => !l.is_duplicate).length}</span>
          </button>
          <button className={`tab ${view === 'review' ? 'tab--active' : ''}`} onClick={() => setView('review')}>
            Needs tier review <span className="mono">{needsReview.length}</span>
          </button>
          <button className={`tab ${view === 'duplicates' ? 'tab--active' : ''}`} onClick={() => setView('duplicates')}
            style={{ color: view === 'duplicates' ? 'var(--signal-amber)' : undefined }}>
            Duplicates <span className="mono">{leads.filter(l => l.is_duplicate).length}</span>
          </button>
        </div>

        <div className="leads-filter-row">
          <div className="leads-search-wrap">
            <span className="leads-search-icon">🔍</span>
            <input
              type="text"
              placeholder="Search by name, phone, or email…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="leads-search-input"
            />
          </div>
          <select className="filter-select" value={tierFilter} onChange={(e) => setTierFilter(e.target.value)}>
            {TIER_FILTER_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
          </select>
          <select className="filter-select" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            {STATUS_FILTER_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
          </select>
          {importBatches.length > 0 && (
            <select
              className="filter-select"
              value={batchFilter}
              onChange={(e) => { setBatchFilter(e.target.value); loadLeads(e.target.value) }}
              title="Filter by import batch / list name"
              style={{ maxWidth: 220 }}
            >
              <option value="">📦 All batches</option>
              {importBatches.map((b) => (
                <option key={b.source_file} value={b.import_list_name || b.source_file}>
                  {b.import_list_name || b.source_file}
                  {b.lead_count ? ` (${b.lead_count})` : ''}
                </option>
              ))}
            </select>
          )}
          <span className="leads-count-pill">{filteredLeads.length} shown</span>
        </div>
      </div>

      {/* ── Bulk Assign Panel ── */}
      {showBulkAssign && (
        <section className="panel">
          <div className="panel-header">
            <h2 className="panel-title">Assign {selectedCount} lead{selectedCount === 1 ? '' : 's'} to…</h2>
            <button className="back-link" onClick={() => { setShowBulkAssign(false); setBulkAssignError('') }}>Cancel</button>
          </div>
          <div className="leads-import-row">
            <select className="filter-select" value={bulkAssignTarget} onChange={(e) => setBulkAssignTarget(e.target.value)}>
              <option value="">Unassigned (back to pool)</option>
              {assignableUsers.map((user) => (
                <option key={user.id} value={user.id}>{user.full_name}</option>
              ))}
            </select>
            <button className="btn btn--primary" onClick={handleBulkAssign} disabled={bulkAssigning}>
              {bulkAssigning ? 'Assigning…' : `Assign ${selectedCount} lead${selectedCount === 1 ? '' : 's'}`}
            </button>
          </div>
          {bulkAssignError && <div className="compose-error">{bulkAssignError}</div>}
        </section>
      )}

      {bulkAssignResult && (
        <div className="leads-bulk-result">
          ✓ Reassigned: {bulkAssignResult.reassigned_count}
          {bulkAssignResult.skipped_count > 0 && ` · Skipped: ${bulkAssignResult.skipped_count}`}
        </div>
      )}

      {bulkAiStartResult && (
        <div className={`leads-bulk-result`} style={{
          background: bulkAiStartResult.error ? 'rgba(255,77,77,0.12)' : 'rgba(124,58,237,0.12)',
          color: bulkAiStartResult.error ? 'var(--signal-red)' : '#7c3aed',
          border: `1px solid ${bulkAiStartResult.error ? 'var(--signal-red)' : '#7c3aed'}`,
          marginBottom: 8,
        }}>
          {bulkAiStartResult.error
            ? `⚠️ ${bulkAiStartResult.error}`
            : `🤖 AI started on ${bulkAiStartResult.started} lead${bulkAiStartResult.started === 1 ? '' : 's'}${bulkAiStartResult.skipped > 0 ? ` · ${bulkAiStartResult.skipped} skipped (already active or DNC)` : ''}${bulkAiStartResult.errors > 0 ? ` · ${bulkAiStartResult.errors} errors` : ''}`
          }
          <button style={{ marginLeft: 12, background: 'none', border: 'none', cursor: 'pointer', opacity: 0.6, fontSize: 12 }} onClick={() => setBulkAiStartResult(null)}>✕</button>
        </div>
      )}

      {/* ── Batch Compose Drawer (Phase 3) — shown as bottom overlay ── */}

      {/* ── Leads Table ── */}
      <section className="panel leads-table-panel">
        {loading ? (
          <div className="empty-state">Loading leads…</div>
        ) : loadError ? (
          <div className="empty-state" style={{ color: 'var(--signal-red)' }}>
            ⚠️ {loadError}
            <br />
            <button className="btn btn--secondary" style={{ marginTop: 12 }} onClick={loadLeads}>Retry</button>
          </div>
        ) : filteredLeads.length === 0 ? (
          <div className="empty-state">
            {view === 'review' ? 'Nothing needs review right now.' : 'No leads match your filters.'}
          </div>
        ) : (
          <table className="leads-table">
            <thead>
              <tr>
                {view !== 'review' && (
                  <th style={{ width: 36 }}>
                    <input
                      type="checkbox"
                      checked={sendableLeads.length > 0 && sendableLeads.every((l) => selected.has(l.id))}
                      onChange={toggleSelectAll}
                    />
                  </th>
                )}
                <th>Name</th>
                <th>Phone</th>
                <th>Email</th>
                <th>Tier</th>
                <th>Status</th>
                <th>Source</th>
                {view === 'review' && <th>Assign tier</th>}
              </tr>
            </thead>
            <tbody>
              {filteredLeads.slice(0, 200).map((lead) => {
                const initials = `${(lead.first_name || '?')[0]}${(lead.last_name || '?')[0]}`.toUpperCase()
                const isSelected = selected.has(lead.id)
                return (
                  <tr
                    key={lead.id}
                    className={`leads-row ${isSelected ? 'leads-row--selected' : ''}`}
                    onClick={() => view !== 'review' && navigate(`/leads/${lead.id}`)}
                    style={{ cursor: view !== 'review' ? 'pointer' : 'default' }}
                  >
                    {view !== 'review' && (
                      <td onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" checked={isSelected} onChange={() => toggleSelect(lead.id)} />
                      </td>
                    )}
                    <td>
                      <div className="leads-name-cell">
                        <div className="leads-avatar">{initials}</div>
                        <span>{lead.first_name} {lead.last_name}</span>
                      </div>
                    </td>
                    <td className="mono leads-secondary">{lead.phone || '—'}</td>
                    <td className="mono leads-secondary">{lead.email || '—'}</td>
                    <td><TierBadge tier={lead.tier} /></td>
                    <td><StatusBadge status={lead.status} /></td>
                    <td className="mono leads-secondary" style={{ fontSize: 11 }}>
                      {lead.source_file ? lead.source_file.replace(/\.[^.]+$/, '').slice(0, 20) : '—'}
                      {lead.source_year ? ` (${lead.source_year})` : ''}
                    </td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <button
                        className="btn btn--ghost"
                        style={{ fontSize: 11, padding: '2px 8px', color: 'var(--signal-red)' }}
                        onClick={(e) => handleDeleteLead(e, lead.id)}
                        title="Delete lead"
                      >🗑</button>
                    </td>
                    {view === 'review' && (
                      <td>
                        <select
                          defaultValue=""
                          className="tier-select"
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) => e.target.value && assignTier(lead.id, e.target.value)}
                        >
                          <option value="" disabled>Assign…</option>
                          {TIER_OPTIONS.map((opt) => (
                            <option key={opt.value} value={opt.value}>{opt.label}</option>
                          ))}
                        </select>
                      </td>
                    )}
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </section>

      {/* ── Bottom Batch Action System (Phase 3 + 4) ── */}
      {/* Hidden file input for bulk media upload */}
      <input
        ref={bulkMediaInputRef}
        type="file"
        accept=".jpg,.jpeg,.png,.gif,.pdf"
        style={{ display: 'none' }}
        onChange={handleBulkMediaUpload}
      />

      {selectedCount > 0 && (
        <div style={{
          position: 'fixed', bottom: 0, left: 240, right: 0, zIndex: 200,
          display: 'flex', flexDirection: 'column',
          boxShadow: '0 -4px 24px rgba(0,0,0,0.35)',
          background: 'linear-gradient(180deg, rgba(10,20,46,0.98) 0%, rgba(5,10,24,0.99) 100%)',
          borderTop: '2px solid var(--signal-blue)',
          backdropFilter: 'blur(20px)',
        }}>
          {/* Expanded compose drawer — unified panel (no tabs, mirrors individual lead compose) */}
          {showBulkCompose && (
            <div style={{ padding: '16px 24px', borderBottom: '1px solid var(--border-subtle)', maxHeight: '65vh', overflowY: 'auto' }}>
              {/* Header row */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
                <span style={{ fontWeight: 700, fontSize: 14, color: 'var(--text-primary)' }}>
                  ✉️ Compose message — {sendableSelectedIds.length} sendable of {selectedCount} selected
                </span>
                <button
                  onClick={() => setShowBulkCompose(false)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', fontSize: 18, color: 'var(--text-tertiary)', lineHeight: 1 }}
                >✕</button>
              </div>

              {/* Tone + Channel pills row */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 10, alignItems: 'center' }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', minWidth: 40 }}>Tone</span>
                {[
                  { value: 'cold', label: '❄️ Cold' },
                  { value: 'warm', label: '☀️ Warm' },
                  { value: 'hot', label: '🔥 Hot' },
                  { value: 'urgent', label: '⚡ Urgent' },
                ].map(t => (
                  <button key={t.value}
                    className={`leads-ai-pill ${aiTone === t.value ? 'leads-ai-pill--active' : ''}`}
                    onClick={() => setAiTone(t.value)}
                    style={{ fontSize: 11 }}
                  >{t.label}</button>
                ))}
                <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', marginLeft: 10, minWidth: 52 }}>Channel</span>
                {[
                  { value: 'sms', label: '💬 SMS' },
                  { value: 'email', label: '✉️ Email' },
                  { value: 'both', label: '📡 Both' },
                ].map(c => (
                  <button key={c.value}
                    className={`leads-ai-pill ${aiChannel === c.value ? 'leads-ai-pill--active' : ''}`}
                    onClick={() => setAiChannel(c.value)}
                    style={{ fontSize: 11 }}
                  >{c.label}</button>
                ))}
              </div>

              {/* Relationship type row */}
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12, alignItems: 'center' }}>
                <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--text-tertiary)', minWidth: 40 }}>Type</span>
                {[
                  { value: '', label: '🔵 Default' },
                  { value: 'cold_lead', label: '❄️ Cold lead' },
                  { value: 'warm_lead', label: '☀️ Warm lead' },
                  { value: 're_engagement', label: '🔄 Re-engage' },
                  { value: 'previous_prospect', label: '📋 Past prospect' },
                  { value: 'past_customer', label: '🤝 Past customer' },
                  { value: 'existing_customer', label: '⭐ Existing' },
                ].map(r => (
                  <button key={r.value}
                    className={`leads-ai-pill ${bulkRelationshipType === r.value ? 'leads-ai-pill--active' : ''}`}
                    onClick={() => setBulkRelationshipType(r.value)}
                    style={{ fontSize: 11 }}
                  >{r.label}</button>
                ))}
              </div>

              {/* AI direction + generate button */}
              <div style={{ display: 'flex', gap: 8, marginBottom: 10 }}>
                <input
                  style={{
                    flex: 1, fontSize: 13, padding: '7px 10px', borderRadius: 6,
                    border: '1px solid var(--border-default)', fontFamily: 'inherit',
                    background: 'var(--surface-base, #161929)', color: 'var(--text-primary)',
                  }}
                  placeholder="AI direction (optional) — e.g. file check, ask if they still need pre-need planning"
                  value={bulkAiDirection}
                  onChange={e => setBulkAiDirection(e.target.value)}
                />
                <button
                  className="btn btn--secondary"
                  onClick={handleBulkAiGenerate}
                  disabled={bulkAiGenerating || sendableSelectedIds.length === 0}
                  style={{ whiteSpace: 'nowrap', flexShrink: 0 }}
                >
                  {bulkAiGenerating ? '⏳ Drafting…' : '✨ AI Draft'}
                </button>
              </div>
              {bulkAiError && <div className="compose-error" style={{ marginBottom: 8 }}>{bulkAiError}</div>}
              <p style={{ fontSize: 11, color: 'var(--text-tertiary)', margin: '0 0 8px' }}>
                "AI Draft" fills the box below using your tone + direction. Review, edit, then send.
              </p>

              {/* Message textarea */}
              <textarea
                className="compose-textarea"
                placeholder="Hi {first_name}, this is… (use {first_name} to personalize)"
                value={bulkMessage}
                onChange={e => setBulkMessage(e.target.value)}
                rows={4}
                style={{ marginBottom: 10 }}
              />

              {/* Media attachment preview */}
              {bulkMediaFileName && (
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8,
                  padding: '6px 10px', background: 'rgba(30,200,168,0.08)', borderRadius: 6,
                  border: '1px solid rgba(30,200,168,0.25)', fontSize: 12,
                }}>
                  <span>📎</span>
                  <span style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{bulkMediaFileName}</span>
                  <span style={{ opacity: 0.6, fontSize: 11 }}>Will send as MMS</span>
                  <button
                    onClick={() => { setBulkMediaUrl(''); setBulkMediaFileName('') }}
                    style={{ marginLeft: 'auto', background: 'none', border: 'none', cursor: 'pointer', color: 'var(--signal-red)', fontSize: 14 }}
                  >✕</button>
                </div>
              )}

              {/* Compose footer: booking link | attach | queue | send */}
              <div className="compose-footer">
                <label className="compose-checkbox">
                  <input type="checkbox" checked={bulkIncludeBooking} onChange={e => setBulkIncludeBooking(e.target.checked)} />
                  Include booking link
                </label>
                <button
                  onClick={() => bulkMediaInputRef.current?.click()}
                  disabled={bulkMediaUploading}
                  style={{ fontSize: 12, padding: '5px 12px', background: 'none', border: '1px solid var(--border-default)', borderRadius: 6, cursor: 'pointer', color: 'var(--text-secondary)' }}
                >
                  {bulkMediaUploading ? '⏳ Uploading…' : '📎 Attach flyer'}
                </button>
                <button
                  className="btn btn--secondary"
                  onClick={() => handleAiAction('queue')}
                  disabled={!!aiActioning || sendableSelectedIds.length === 0}
                >
                  {aiActioning === 'queue' ? '⏳ Queuing…' : '📥 AI Queue for review'}
                </button>
                <button
                  className="btn btn--primary"
                  onClick={handleBulkSend}
                  disabled={bulkSending || !bulkMessage.trim() || sendableSelectedIds.length === 0}
                >
                  {bulkSending ? 'Sending…' : `${bulkMediaUrl ? '📸 Send MMS' : aiChannel === 'email' ? '✉️ Send Email' : '💬 Send SMS'} to ${sendableSelectedIds.length}`}
                </button>
              </div>

              {/* Results */}
              {bulkResult && (
                <div className="leads-bulk-result" style={{ marginTop: 8 }}>✓ Sent: {bulkResult.sent_count} · Skipped: {bulkResult.skipped_count}</div>
              )}
              {aiResult && !aiResult.error && (
                <div className="leads-ai-result" style={{ marginTop: 8 }}>
                  {aiResult.mode === 'queue'
                    ? `✓ ${aiResult.queued} messages queued for review`
                    : `✓ Sent: ${aiResult.sent} · Queued: ${aiResult.queued} · Skipped: ${aiResult.skipped}`}
                </div>
              )}
              {aiResult?.error && <div className="compose-error" style={{ marginTop: 8 }}>{aiResult.error}</div>}
            </div>
          )}

          {/* Bottom action bar (always visible when leads selected) */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '12px 24px', flexWrap: 'wrap',
          }}>
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: 13, fontWeight: 700, color: 'var(--signal-blue)', marginRight: 4 }}>
              {selectedCount} selected
            </span>
            <button className="btn btn--secondary leads-bulk-clear" style={{ fontSize: 12 }} onClick={() => { setSelected(new Set()); setShowBulkCompose(false) }}>Deselect all</button>
            {canBulkAssign && (
              <button className="btn btn--secondary" style={{ fontSize: 12 }} onClick={() => setShowBulkAssign(true)}>👤 Assign…</button>
            )}
            <button
              className="btn btn--danger"
              style={{ background: 'var(--signal-red)', color: '#fff', border: 'none', fontSize: 12 }}
              onClick={handleDeleteSelected}
            >
              🗑 Delete ({selectedCount})
            </button>
            <button
              className="btn btn--primary"
              style={{ background: '#16a34a', borderColor: '#16a34a', fontSize: 12 }}
              onClick={() => setShowVoiceCampaign(true)}
            >
              📞 AI Call Campaign
            </button>
            <button
              className="btn btn--primary"
              style={{ background: '#7c3aed', borderColor: '#7c3aed', fontSize: 12 }}
              onClick={handleBulkAiStart}
              disabled={bulkAiStarting}
              title="Start AI email/SMS conversations on all selected leads at once (up to 500)"
            >
              {bulkAiStarting ? '⏳ Starting AI…' : `🤖 Start AI on ${selectedCount}`}
            </button>
            <button
              className={`btn ${showBulkCompose ? 'btn--secondary' : 'btn--primary'}`}
              style={{ marginLeft: 'auto', fontSize: 14, fontWeight: 700, minWidth: 180 }}
              onClick={() => { setShowBulkCompose(v => !v); setAiResult(null); setBulkResult(null) }}
            >
              ✉️ {showBulkCompose ? '▼ Close compose' : `▲ Compose & Send (${selectedCount})`}
            </button>
          </div>
        </div>
      )}

      {/* ── Voice Campaign Modal ── */}
      {showVoiceCampaign && (
        <VoiceCampaign
          selectedLeads={leads.filter(l => selected.has(l.id))}
          onClose={() => setShowVoiceCampaign(false)}
          onSuccess={() => { setShowVoiceCampaign(false); setSelected(new Set()) }}
        />
      )}

      {reviewLeadIds && (
        <MessageReview
          leadIds={reviewLeadIds}
          onClose={() => { setReviewLeadIds(null); loadLeads() }}
          onSent={() => loadLeads()}
        />
      )}
    </div>
  )
}

function PreviewStat({ label, value, accent = 'neutral' }) {
  return (
    <div className="preview-stat">
      <div className="preview-stat-label">{label}</div>
      <div className={`preview-stat-value preview-stat-value--${accent}`}>{value}</div>
    </div>
  )
}
