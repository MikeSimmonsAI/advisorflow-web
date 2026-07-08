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
]

const TIER_FILTER_OPTIONS = [
  { value: '', label: 'All tiers' },
  { value: 'pre_need', label: 'Pre-Need' },
  { value: 'at_need', label: 'At-Need' },
  { value: 'imminent', label: 'Imminent' },
  { value: 'contract_sold', label: 'Contract Sold' },
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

const SORT_OPTIONS = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'name_az', label: 'Name A–Z' },
  { value: 'name_za', label: 'Name Z–A' },
]

const EMPTY_LEAD_FORM = {
  first_name: '',
  last_name: '',
  phone: '',
  email: '',
  tier: '',
  notes: '',
}

export default function Leads() {
  const navigate = useNavigate()
  const currentUser = getCurrentUser()
  const currentUser = getCurrentUser()
  const [leads, setLeads] = useState([])
  const [needsReview, setNeedsReview] = useState([])
  const [loading, setLoading] = useState(true)
  const [preview, setPreview] = useState(null)
  const [previewing, setPreviewing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [sourceYear, setSourceYear] = useState('')
  const [googleImporting, setGoogleImporting] = useState(false)
  const [googleImportResult, setGoogleImportResult] = useState(null)
  const [view, setView] = useState('all') // 'all' | 'review'
  const [reviewLeadIds, setReviewLeadIds] = useState(null) // set right after a successful import
  const fileInputRef = useRef(null)
  const pendingFile = useRef(null)

  // Filtering & search
  const [searchQuery, setSearchQuery] = useState('')
  const [tierFilter, setTierFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')
  const [sortBy, setSortBy] = useState('newest')

  // Bulk select & send
  const [selected, setSelected] = useState(new Set())
  const [bulkMessage, setBulkMessage] = useState('')
  const [bulkIncludeBooking, setBulkIncludeBooking] = useState(true)
  const [bulkSending, setBulkSending] = useState(false)
  const [bulkResult, setBulkResult] = useState(null)
  const [showBulkCompose, setShowBulkCompose] = useState(false)

  // Add Lead modal
  const [showAddLead, setShowAddLead] = useState(false)
  const [addLeadForm, setAddLeadForm] = useState(EMPTY_LEAD_FORM)
  const [addLeadSaving, setAddLeadSaving] = useState(false)
  const [addLeadError, setAddLeadError] = useState('')

  function loadLeads() {
    setLoading(true)
    Promise.all([
      api.get(currentUser?.role === 'org_admin' || currentUser?.role === 'super_admin' ? '/admin/leads' : '/leads/'),
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
      const result = await api.post('/google_contacts/import', {})
      setGoogleImportResult(result)
      // Refresh leads list
      const leadsData = await api.get(currentUser?.role === 'org_admin' || currentUser?.role === 'super_admin' ? '/admin/leads' : '/leads/')
      setLeads(leadsData)
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
      await api.upload('/leads/upload/confirm', formData)
      setPreview(null)
      pendingFile.current = null
      if (fileInputRef.current) fileInputRef.current.value = ''
      loadLeads()
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

  async function handleAddLead(e) {
    e.preventDefault()
    if (!addLeadForm.first_name.trim() && !addLeadForm.last_name.trim()) {
      setAddLeadError('First or last name is required.')
      return
    }
    setAddLeadSaving(true)
    setAddLeadError('')
    try {
      await api.post('/leads/', {
        first_name: addLeadForm.first_name.trim() || null,
        last_name: addLeadForm.last_name.trim() || null,
        phone: addLeadForm.phone.trim() || null,
        email: addLeadForm.email.trim() || null,
        tier: addLeadForm.tier || null,
        notes: addLeadForm.notes.trim() || null,
      })
      setShowAddLead(false)
      setAddLeadForm(EMPTY_LEAD_FORM)
      loadLeads()
    } catch (err) {
      setAddLeadError(err.message || 'Could not add lead.')
    } finally {
      setAddLeadSaving(false)
    }
  }

  const baseLeads = view === 'review' ? needsReview : leads

  // Real (non-duplicate) lead count for the tab badge
  const realLeadCount = useMemo(() => leads.filter(l => !l.is_duplicate).length, [leads])

  // Filtering, search, and sort applied client-side
  const filteredLeads = useMemo(() => {
    let result = baseLeads

    if (tierFilter) result = result.filter(l => l.tier === tierFilter)
    if (statusFilter) result = result.filter(l => l.status === statusFilter)

    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase()
      const qDigits = q.replace(/\D/g, '')
      result = result.filter(l => {
        const name = `${l.first_name || ''} ${l.last_name || ''}`.toLowerCase()
        const email = (l.email || '').toLowerCase()
        const phoneDigits = (l.phone || '').replace(/\D/g, '')
        return (
          name.includes(q) ||
          email.includes(q) ||
          (qDigits.length > 0 && phoneDigits.includes(qDigits))
        )
      })
    }

    // Sort
    result = [...result]
    if (sortBy === 'newest') {
      result.sort((a, b) => new Date(b.created_at || 0) - new Date(a.created_at || 0))
    } else if (sortBy === 'oldest') {
      result.sort((a, b) => new Date(a.created_at || 0) - new Date(b.created_at || 0))
    } else if (sortBy === 'name_az') {
      result.sort((a, b) => `${a.last_name || ''}${a.first_name || ''}`.localeCompare(`${b.last_name || ''}${b.first_name || ''}`))
    } else if (sortBy === 'name_za') {
      result.sort((a, b) => `${b.last_name || ''}${b.first_name || ''}`.localeCompare(`${a.last_name || ''}${a.first_name || ''}`))
    }

    return result
  }, [baseLeads, tierFilter, statusFilter, searchQuery, sortBy])

  // Only leads with a phone, not DNC, not duplicate are eligible for bulk SMS send.
  const sendableLeads = filteredLeads.filter(l => l.phone && l.status !== 'dnc' && !l.is_duplicate)

  function toggleSelect(id) {
    const next = new Set(selected)
    next.has(id) ? next.delete(id) : next.add(id)
    setSelected(next)
  }

  function toggleSelectAll() {
    const sendableIds = sendableLeads.map(l => l.id)
    const allSelected = sendableIds.length > 0 && sendableIds.every(id => selected.has(id))
    if (allSelected) {
      const next = new Set(selected)
      sendableIds.forEach(id => next.delete(id))
      setSelected(next)
    } else {
      setSelected(new Set([...selected, ...sendableIds]))
    }
  }

  async function handleBulkSend() {
    if (!bulkMessage.trim() || selected.size === 0) return
    setBulkSending(true)
    setBulkResult(null)
    try {
      const result = await api.post('/sms/send-batch', {
        lead_ids: Array.from(selected),
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

  const selectedCount = selected.size

  return (
    <div>
      {/* Add Lead Modal */}
      {showAddLead && (
        <div className="modal-overlay" onClick={() => setShowAddLead(false)}>
          <div className="modal-panel" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h2 className="panel-title">Add Lead</h2>
              <button className="back-link" onClick={() => { setShowAddLead(false); setAddLeadForm(EMPTY_LEAD_FORM); setAddLeadError('') }}>✕</button>
            </div>
            <form className="add-lead-form" onSubmit={handleAddLead}>
              <div className="add-lead-row">
                <label>
                  First name
                  <input
                    value={addLeadForm.first_name}
                    onChange={e => setAddLeadForm(f => ({ ...f, first_name: e.target.value }))}
                    placeholder="First name"
                  />
                </label>
                <label>
                  Last name
                  <input
                    value={addLeadForm.last_name}
                    onChange={e => setAddLeadForm(f => ({ ...f, last_name: e.target.value }))}
                    placeholder="Last name"
                  />
                </label>
              </div>
              <label>
                Phone
                <input
                  value={addLeadForm.phone}
                  onChange={e => setAddLeadForm(f => ({ ...f, phone: e.target.value }))}
                  placeholder="214-555-0199"
                  type="tel"
                />
              </label>
              <label>
                Email
                <input
                  value={addLeadForm.email}
                  onChange={e => setAddLeadForm(f => ({ ...f, email: e.target.value }))}
                  placeholder="family@example.com"
                  type="email"
                />
              </label>
              <label>
                Tier
                <select
                  value={addLeadForm.tier}
                  onChange={e => setAddLeadForm(f => ({ ...f, tier: e.target.value }))}
                >
                  <option value="">Select tier…</option>
                  {TIER_OPTIONS.map(opt => (
                    <option key={opt.value} value={opt.value}>{opt.label}</option>
                  ))}
                </select>
              </label>
              <label>
                Notes
                <textarea
                  value={addLeadForm.notes}
                  onChange={e => setAddLeadForm(f => ({ ...f, notes: e.target.value }))}
                  placeholder="Optional notes…"
                  rows={3}
                />
              </label>
              {addLeadError && <div className="upload-error">{addLeadError}</div>}
              <div className="add-lead-actions">
                <button type="button" className="btn btn--secondary" onClick={() => { setShowAddLead(false); setAddLeadForm(EMPTY_LEAD_FORM); setAddLeadError('') }}>
                  Cancel
                </button>
                <button type="submit" className="btn btn--primary" disabled={addLeadSaving}>
                  {addLeadSaving ? 'Saving…' : 'Add Lead'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      <header className="page-header">
        <div>
          <h1 className="page-title">Leads</h1>
          <p className="page-subtitle">Import, dedupe, and route every lead to the right track.</p>
        </div>
        <button className="btn btn--primary" onClick={() => setShowAddLead(true)}>
          + Add Lead
        </button>
      </header>

      <section className="panel upload-panel">
        <div className="panel-header">
          <h2 className="panel-title">Import leads</h2>
        </div>
        <div className="upload-row">
          <input
            type="number"
            placeholder="Source year (optional, e.g. 2019)"
            value={sourceYear}
            onChange={(e) => setSourceYear(e.target.value)}
            className="year-input"
          />
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
            title="Import contacts directly from your Google Contacts"
          >
            {googleImporting ? 'Importing…' : '📋 Import from Google Contacts'}
          </button>
        </div>

        {googleImportResult && (
          <div className={googleImportResult.error ? 'upload-error' : 'upload-success'}>
            {googleImportResult.error
              ? googleImportResult.error
              : `Imported ${googleImportResult.new_active_sms_leads || 0} new leads from Google Contacts. ${googleImportResult.duplicates_flagged || 0} duplicates skipped.`}
          </div>
        )}

        {previewing && <div className="empty-state">Checking for duplicates and routing tiers…</div>}

        {preview && (
          <div className="preview-box">
            <div className="preview-grid">
              <PreviewStat label="Total rows" value={preview.total_rows} />
              <PreviewStat label="Active SMS leads" value={preview.new_active_sms_leads} accent="green" />
              <PreviewStat label="Email-only queued" value={preview.email_only_leads_queued} accent="blue" />
              <PreviewStat label="Duplicates flagged" value={preview.duplicates_flagged} accent="amber" />
              <PreviewStat label="Call-restricted (DNC)" value={preview.flagged_call_restricted} accent="red" />
              <PreviewStat label="Needs tier review" value={preview.flagged_needs_tier_review} accent="amber" />
            </div>
            <div className="tier-breakdown">
              {Object.entries(preview.tier_breakdown || {}).map(([tier, count]) => (
                <span key={tier} className="tier-chip">
                  <TierBadge tier={tier} /> <span className="mono">{count}</span>
                </span>
              ))}
            </div>
            <div className="preview-actions">
              <button className="btn btn--secondary" onClick={cancelPreview}>Cancel</button>
              <button className="btn btn--primary" onClick={handleConfirmUpload} disabled={confirming}>
                {confirming ? 'Importing…' : `Confirm import of ${preview.imported} leads`}
              </button>
            </div>
          </div>
        )}
      </section>

      <div className="leads-tabs">
        <button className={`tab ${view === 'all' ? 'tab--active' : ''}`} onClick={() => setView('all')}>
          All leads <span className="mono">{realLeadCount}</span>
        </button>
        <button className={`tab ${view === 'review' ? 'tab--active' : ''}`} onClick={() => setView('review')}>
          Needs tier review <span className="mono">{needsReview.length}</span>
        </button>
      </div>

      <div className="filter-bar">
        <input
          type="text"
          placeholder="Search by name, phone, or email…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="search-input"
        />
        <select className="filter-select" value={tierFilter} onChange={(e) => setTierFilter(e.target.value)}>
          {TIER_FILTER_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
        </select>
        <select className="filter-select" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          {STATUS_FILTER_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
        </select>
        <select className="filter-select" value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
          {SORT_OPTIONS.map(opt => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
        </select>
        <span className="filter-count mono">{filteredLeads.length} shown</span>
      </div>

      {selectedCount > 0 && (
        <div className="bulk-bar">
          <span className="bulk-bar-count">{selectedCount} selected</span>
          <button className="btn btn--secondary" onClick={() => setSelected(new Set())}>Clear</button>
          <button className="btn btn--primary" onClick={() => setShowBulkCompose(true)}>Send to selected</button>
        </div>
      )}

      {showBulkCompose && (
        <section className="panel bulk-compose-panel">
          <div className="panel-header">
            <h2 className="panel-title">Send to {selectedCount} leads</h2>
            <button className="back-link" onClick={() => setShowBulkCompose(false)}>Cancel</button>
          </div>
          <textarea
            className="compose-textarea"
            placeholder="Hi {first_name}, this is..."
            value={bulkMessage}
            onChange={(e) => setBulkMessage(e.target.value)}
            rows={4}
          />
          <p className="settings-help">
            Use <code>&#123;'first_name'&#125;</code>, <code>&#123;'advisor_name'&#125;</code>, and <code>&#123;'booking_link'&#125;</code> as placeholders — they'll be filled in per lead.
          </p>
          <div className="compose-footer">
            <label className="compose-checkbox">
              <input type="checkbox" checked={bulkIncludeBooking} onChange={(e) => setBulkIncludeBooking(e.target.checked)} />
              Include booking link
            </label>
            <button className="btn btn--primary" onClick={handleBulkSend} disabled={bulkSending || !bulkMessage.trim()}>
              {bulkSending ? 'Sending…' : `Send to ${selectedCount} leads`}
            </button>
          </div>
          {bulkResult && (
            <div className="bulk-result mono">
              Sent: {bulkResult.sent_count} · Skipped: {bulkResult.skipped_count}
            </div>
          )}
        </section>
      )}

      <section className="panel">
        {loading ? (
          <div className="empty-state">Loading leads…</div>
        ) : filteredLeads.length === 0 ? (
          <div className="empty-state">
            {view === 'review' ? 'Nothing needs review right now.' : 'No leads match your filters.'}
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                {view !== 'review' && (
                  <th style={{ width: 30 }}>
                    <input
                      type="checkbox"
                      checked={sendableLeads.length > 0 && sendableLeads.every(l => selected.has(l.id))}
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
                const sendable = lead.phone && lead.status !== 'dnc' && !lead.is_duplicate
                return (
                  <tr
                    key={lead.id}
                    onClick={() => view !== 'review' && navigate(`/leads/${lead.id}`)}
                    style={{ cursor: view !== 'review' ? 'pointer' : 'default' }}
                  >
                    {view !== 'review' && (
                      <td onClick={(e) => e.stopPropagation()}>
                        {sendable && (
                          <input
                            type="checkbox"
                            checked={selected.has(lead.id)}
                            onChange={() => toggleSelect(lead.id)}
                          />
                        )}
                      </td>
                    )}
                    <td>{lead.first_name} {lead.last_name}</td>
                    <td className="mono">{lead.phone || '–'}</td>
                    <td className="mono">{lead.email || '–'}</td>
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
                          {TIER_OPTIONS.map(opt => (
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
