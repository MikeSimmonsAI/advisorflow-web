import { useEffect, useState, useRef, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { api, getCurrentUser } from '../api/client'
import { TierBadge, StatusBadge } from '../components/StatusBadge'
import MessageReview from '../components/MessageReview'
import '../styles/shared.css'
import './Leads.css'

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
  const [needsReview, setNeedsReview] = useState([])
  const [loading, setLoading] = useState(true)
  const [preview, setPreview] = useState(null)
  const [previewing, setPreviewing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [sourceYear, setSourceYear] = useState('')
  const [forceNewInquiry, setForceNewInquiry] = useState(false)
  const [view, setView] = useState('all')
  const [reviewLeadIds, setReviewLeadIds] = useState(null)
  const [showImport, setShowImport] = useState(false)
  const fileInputRef = useRef(null)
  const pendingFile = useRef(null)
  const [googleImporting, setGoogleImporting] = useState(false)
  const [googleImportResult, setGoogleImportResult] = useState(null)

  const [searchQuery, setSearchQuery] = useState('')
  const [tierFilter, setTierFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  const [selected, setSelected] = useState(new Set())
  const [bulkMessage, setBulkMessage] = useState('')
  const [bulkIncludeBooking, setBulkIncludeBooking] = useState(true)
  const [bulkSending, setBulkSending] = useState(false)
  const [bulkResult, setBulkResult] = useState(null)
  const [showBulkCompose, setShowBulkCompose] = useState(false)

  const currentUser = getCurrentUser()
  const canBulkAssign = currentUser?.role === 'org_admin' || currentUser?.role === 'super_admin'
  const [assignableUsers, setAssignableUsers] = useState([])
  const [showBulkAssign, setShowBulkAssign] = useState(false)
  const [bulkAssignTarget, setBulkAssignTarget] = useState('')
  const [bulkAssigning, setBulkAssigning] = useState(false)
  const [bulkAssignResult, setBulkAssignResult] = useState(null)
  const [bulkAssignError, setBulkAssignError] = useState('')

  function loadLeads() {
    setLoading(true)
    Promise.all([
      api.get('/leads/'),
      api.get('/leads/needs-review'),
    ]).then(([leadsData, reviewData]) => {
      setLeads(leadsData)
      setNeedsReview(reviewData)
      setLoading(false)
    })
  }

  useEffect(() => { loadLeads() }, [])

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
    try {
      const formData = new FormData()
      formData.append('file', file)
      if (sourceYear) formData.append('source_year', sourceYear)
      if (forceNewInquiry) formData.append('force_new_inquiry', 'true')
      const result = await api.upload('/leads/upload/preview', formData)
      setPreview(result)
    } catch (err) {
      alert(`Preview failed: ${err.message}`)
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
      alert(`Import failed: ${err.message}`)
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

  const baseLeads = view === 'review' ? needsReview : leads

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

  async function handleBulkSend() {
    if (!bulkMessage.trim() || sendableSelectedIds.length === 0) return
    setBulkSending(true)
    setBulkResult(null)
    try {
      const result = await api.post('/sms/send-batch', {
        lead_ids: sendableSelectedIds,
        template: bulkMessage,
        include_booking_link: bulkIncludeBooking,
      })
      setBulkResult(result)
      setSelected(new Set())
      setBulkMessage('')
      loadLeads()
    } catch (err) {
      alert(`Bulk send failed: ${err.message}`)
    } finally {
      setBulkSending(false)
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
    total: leads.length,
    shown: filteredLeads.length,
    sendable: sendableLeads.length,
    selected: selectedCount,
    needsReview: needsReview.length,
    dnc: leads.filter((l) => l.status === 'dnc').length,
    missingPhone: leads.filter((l) => !l.phone).length,
    duplicates: leads.filter((l) => l.is_duplicate).length,
  }), [leads, filteredLeads.length, sendableLeads.length, selectedCount, needsReview.length])

  useEffect(() => {
    if (!canBulkAssign) return
    api.get('/admin/users')
      .then((users) => setAssignableUsers(users.filter((u) => u.is_active && (u.role === 'advisor' || u.role === 'org_admin'))))
      .catch(() => {})
  }, [canBulkAssign])

  return (
    <div className="leads-page">

      {/* ── Header ── */}
      <header className="leads-header">
        <div>
          <p className="leads-eyebrow">Lead operations</p>
          <h1 className="page-title">Leads</h1>
          <p className="page-subtitle">Import, dedupe, search, assign, and send from one control surface.</p>
        </div>
        <button className="btn btn--primary leads-import-btn" onClick={() => setShowImport(!showImport)}>
          {showImport ? '✕ Close import' : '+ Import leads'}
        </button>
      </header>

      {/* ── KPI Cards ── */}
      <div className="leads-kpi-grid">
        {[
          { label: 'TOTAL LEADS', value: stats.total, accent: 'blue', icon: '👥' },
          { label: 'SMS READY', value: stats.sendable, accent: 'green', icon: '📱', sub: 'Phone, not DNC, not duplicate' },
          { label: 'NEEDS REVIEW', value: stats.needsReview, accent: 'amber', icon: '⚠️', sub: 'Assign tier before outreach', action: () => setView('review') },
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
          <div className="leads-import-row">
            <input
              type="number"
              placeholder="Source year (optional)"
              value={sourceYear}
              onChange={(e) => setSourceYear(e.target.value)}
              className="settings-input leads-year-input"
            />
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
              className="file-input"
            />
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
        </section>
      )}

      {/* ── Tabs + Filter Bar ── */}
      <div className="leads-controls">
        <div className="leads-tabs">
          <button className={`tab ${view === 'all' ? 'tab--active' : ''}`} onClick={() => setView('all')}>
            All leads <span className="mono">{leads.length}</span>
          </button>
          <button className={`tab ${view === 'review' ? 'tab--active' : ''}`} onClick={() => setView('review')}>
            Needs tier review <span className="mono">{needsReview.length}</span>
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

      {/* ── Bulk Compose Panel ── */}
      {showBulkCompose && (
        <section className="panel">
          <div className="panel-header">
            <h2 className="panel-title">Send to {sendableSelectedIds.length} lead{sendableSelectedIds.length === 1 ? '' : 's'}</h2>
            <button className="back-link" onClick={() => setShowBulkCompose(false)}>Cancel</button>
          </div>
          <textarea
            className="compose-textarea"
            placeholder="Hi {first_name}, this is..."
            value={bulkMessage}
            onChange={(e) => setBulkMessage(e.target.value)}
            rows={4}
          />
          <div className="compose-footer">
            <label className="compose-checkbox">
              <input type="checkbox" checked={bulkIncludeBooking} onChange={(e) => setBulkIncludeBooking(e.target.checked)} />
              Include booking link
            </label>
            <button
              className="btn btn--primary"
              onClick={handleBulkSend}
              disabled={bulkSending || !bulkMessage.trim() || sendableSelectedIds.length === 0}
            >
              {bulkSending ? 'Sending…' : `Send to ${sendableSelectedIds.length} lead${sendableSelectedIds.length === 1 ? '' : 's'}`}
            </button>
          </div>
          {bulkResult && (
            <div className="leads-bulk-result">Sent: {bulkResult.sent_count} · Skipped: {bulkResult.skipped_count}</div>
          )}
        </section>
      )}

      {/* ── Leads Table ── */}
      <section className="panel leads-table-panel">
        {loading ? (
          <div className="empty-state">Loading leads…</div>
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

      {/* ── Floating Bulk Action Bar ── */}
      {selectedCount > 0 && (
        <div className="leads-bulk-bar">
          <span className="leads-bulk-count">{selectedCount} selected</span>
          <button className="btn btn--secondary leads-bulk-clear" onClick={() => setSelected(new Set())}>Clear</button>
          {canBulkAssign && (
            <button className="btn btn--secondary" onClick={() => setShowBulkAssign(true)}>Assign to…</button>
          )}
          <button
            className="btn btn--primary"
            onClick={() => setShowBulkCompose(true)}
            disabled={sendableSelectedIds.length === 0}
          >
            Send to {sendableSelectedIds.length}
          </button>
        </div>
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
