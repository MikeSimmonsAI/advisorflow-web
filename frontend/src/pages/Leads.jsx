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
  const [view, setView] = useState('all') // 'all' | 'review'
  const [reviewLeadIds, setReviewLeadIds] = useState(null) // set right after a successful import
  const fileInputRef = useRef(null)
  const pendingFile = useRef(null)

  // Filtering & search
  const [searchQuery, setSearchQuery] = useState('')
  const [tierFilter, setTierFilter] = useState('')
  const [statusFilter, setStatusFilter] = useState('')

  // Bulk select & send
  const [selected, setSelected] = useState(new Set())
  const [bulkMessage, setBulkMessage] = useState('')
  const [bulkIncludeBooking, setBulkIncludeBooking] = useState(true)
  const [bulkSending, setBulkSending] = useState(false)
  const [bulkResult, setBulkResult] = useState(null)
  const [showBulkCompose, setShowBulkCompose] = useState(false)

  // Bulk assign (admin-only — checked via canBulkAssign below)
  const currentUser = getCurrentUser()
  const canBulkAssign = currentUser?.role === 'org_admin' || currentUser?.role === 'super_admin'
  const [assignableUsers, setAssignableUsers] = useState([])
  const [showBulkAssign, setShowBulkAssign] = useState(false)
  const [bulkAssignTarget, setBulkAssignTarget] = useState('')
  const [bulkAssigning, setBulkAssigning] = useState(false)
  const [bulkAssignResult, setBulkAssignResult] = useState(null)
  const [bulkAssignError, setBulkAssignError] = useState('')

  // Manual single-lead entry - "if I get one person's information, I
  // need to be able to enter that person directly into the system
  // without uploading a spreadsheet"
  const [showAddLead, setShowAddLead] = useState(false)
  const [addLeadForm, setAddLeadForm] = useState({ first_name: '', last_name: '', phone: '', email: '', tier: 'pre_need', notes: '' })
  const [addLeadSaving, setAddLeadSaving] = useState(false)
  const [addLeadError, setAddLeadError] = useState('')
  const [addLeadResult, setAddLeadResult] = useState(null)

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
      if (fileInputRef.current) fileInputRef.current.value = ''
      loadLeads()

      // The real fix: leads used to sit silently at status=NEW after
      // import, with nothing telling the advisor a message was about to
      // go out automatically. Now the import hands off straight into the
      // review screen - drafted messages shown per lead, nothing sends
      // until explicitly confirmed there.
      if (result.created_lead_ids && result.created_lead_ids.length > 0) {
        setReviewLeadIds(result.created_lead_ids)
      }
    } catch (err) {
      alert(`Import failed: ${err.message}`)
    } finally {
      setConfirming(false)
    }
  }

  async function handleAddLead(e) {
    e.preventDefault()
    setAddLeadError('')

    const { first_name, last_name, phone, email } = addLeadForm
    if (!first_name.trim() || !last_name.trim()) {
      setAddLeadError('First and last name are required.')
      return
    }
    if (!phone.trim() && !email.trim()) {
      setAddLeadError('A phone number or email address is required.')
      return
    }

    setAddLeadSaving(true)
    try {
      const newLead = await api.post('/leads/manual', {
        first_name: first_name.trim(),
        last_name: last_name.trim(),
        phone: phone.trim() || null,
        email: email.trim() || null,
        tier: addLeadForm.tier,
        notes: addLeadForm.notes.trim() || null,
      })
      setAddLeadResult(newLead)
      loadLeads()
    } catch (err) {
      setAddLeadError(err.message || 'Could not add this lead.')
    } finally {
      setAddLeadSaving(false)
    }
  }

  function closeAddLeadModal() {
    setShowAddLead(false)
    setAddLeadForm({ first_name: '', last_name: '', phone: '', email: '', tier: 'pre_need', notes: '' })
    setAddLeadError('')
    setAddLeadResult(null)
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

  // Filtering & search applied client-side over whichever list is active.
  // Search matches name or phone (digits-only comparison so formatting doesn't matter).
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
        return (
          name.includes(q) ||
          email.includes(q) ||
          (qDigits.length > 0 && phoneDigits.includes(qDigits))
        )
      })
    }

    return result
  }, [baseLeads, tierFilter, statusFilter, searchQuery])

  // Leads eligible for bulk SMS send specifically (phone, not DNC, not duplicate).
  // Bulk *assign* has no such restriction — any lead can be reassigned.
  const sendableLeads = filteredLeads.filter((l) => l.phone && l.status !== 'dnc' && !l.is_duplicate)
  const sendableSelectedIds = Array.from(selected).filter((id) => sendableLeads.some((l) => l.id === id))

  function toggleSelect(id) {
    const next = new Set(selected)
    next.has(id) ? next.delete(id) : next.add(id)
    setSelected(next)
  }

  function toggleSelectAll() {
    // "Select all" intentionally only grabs sendable leads, since the most
    // common bulk action is still SMS send. Leads excluded here (DNC, no
    // phone, duplicates) can still be checked individually for bulk-assign.
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

  useEffect(() => {
    if (!canBulkAssign) return
    api.get('/admin/users')
      .then((users) => setAssignableUsers(users.filter((u) => u.is_active && (u.role === 'advisor' || u.role === 'org_admin'))))
      .catch(() => {})
  }, [canBulkAssign])

  return (
    <div>
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
          <h2 className="panel-title">Import leads from Excel</h2>
        </div>
        <div className="upload-row">
          <input
            type="number"
            placeholder="Source year (optional, e.g. 2012)"
            value={sourceYear}
            onChange={(e) => setSourceYear(e.target.value)}
            className="year-input"
          />
          <label className="compose-checkbox upload-new-inquiry-toggle">
            <input
              type="checkbox"
              checked={forceNewInquiry}
              onChange={(e) => setForceNewInquiry(e.target.checked)}
            />
            This whole file is New Inquiry leads (web/cold, no prior relationship)
          </label>
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx"
            onChange={handleFileChange}
            className="file-input"
          />
        </div>
        <p className="settings-help">
          Leads are also auto-tagged as New Inquiry if the file has a "Source" column with a value like Web, Online, or Lead Gen — check the box above to tag the whole file regardless, e.g. for an export that has no source column at all.
        </p>

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
          All leads <span className="mono">{leads.length}</span>
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
          {TIER_FILTER_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
        </select>
        <select className="filter-select" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          {STATUS_FILTER_OPTIONS.map((opt) => <option key={opt.value} value={opt.value}>{opt.label}</option>)}
        </select>
        <span className="filter-count mono">{filteredLeads.length} shown</span>
      </div>

      {selectedCount > 0 && (
        <div className="bulk-bar">
          <span className="bulk-bar-count">{selectedCount} selected</span>
          <button className="btn btn--secondary" onClick={() => setSelected(new Set())}>Clear</button>
          {canBulkAssign && (
            <button className="btn btn--secondary" onClick={() => setShowBulkAssign(true)}>Assign to…</button>
          )}
          <button
            className="btn btn--primary"
            onClick={() => setShowBulkCompose(true)}
            disabled={sendableSelectedIds.length === 0}
            title={sendableSelectedIds.length === 0 ? 'None of the selected leads can receive SMS (no phone, DNC, or duplicate)' : undefined}
          >
            Send to selected
          </button>
        </div>
      )}

      {showBulkAssign && (
        <section className="panel bulk-assign-panel">
          <div className="panel-header">
            <h2 className="panel-title">Assign {selectedCount} lead{selectedCount === 1 ? '' : 's'} to…</h2>
            <button className="back-link" onClick={() => { setShowBulkAssign(false); setBulkAssignError('') }}>Cancel</button>
          </div>
          <div className="bulk-assign-row">
            <select
              className="filter-select"
              value={bulkAssignTarget}
              onChange={(e) => setBulkAssignTarget(e.target.value)}
            >
              <option value="">Unassigned (back to pool)</option>
              {assignableUsers.map((user) => (
                <option key={user.id} value={user.id}>
                  {user.full_name} {user.role !== 'advisor' ? `(${user.role.replace('_', ' ')})` : ''}
                </option>
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
        <div className="bulk-result mono" style={{ marginBottom: 12 }}>
          Reassigned: {bulkAssignResult.reassigned_count}
          {bulkAssignResult.skipped_count > 0 && ` · Skipped: ${bulkAssignResult.skipped_count}`}
        </div>
      )}

      {showBulkCompose && (
        <section className="panel bulk-compose-panel">
          <div className="panel-header">
            <h2 className="panel-title">Send to {sendableSelectedIds.length} lead{sendableSelectedIds.length === 1 ? '' : 's'}</h2>
            <button className="back-link" onClick={() => setShowBulkCompose(false)}>Cancel</button>
          </div>
          {sendableSelectedIds.length < selectedCount && (
            <p className="settings-help">
              {selectedCount - sendableSelectedIds.length} of your {selectedCount} selected lead{selectedCount === 1 ? '' : 's'} can't receive SMS (no phone, DNC, or duplicate) and will be skipped.
            </p>
          )}
          <textarea
            className="compose-textarea"
            placeholder="Hi {first_name}, this is..."
            value={bulkMessage}
            onChange={(e) => setBulkMessage(e.target.value)}
            rows={4}
          />
          <p className="settings-help">
            Use <code>{'{first_name}'}</code>, <code>{'{advisor_name}'}</code>, and <code>{'{booking_link}'}</code> as placeholders — they'll be filled in per lead.
          </p>
          <div className="compose-footer">
            <label className="compose-checkbox">
              <input type="checkbox" checked={bulkIncludeBooking} onChange={(e) => setBulkIncludeBooking(e.target.checked)} />
              Include booking link
            </label>
            <button className="btn btn--primary" onClick={handleBulkSend} disabled={bulkSending || !bulkMessage.trim() || sendableSelectedIds.length === 0}>
              {bulkSending ? 'Sending…' : `Send to ${sendableSelectedIds.length} lead${sendableSelectedIds.length === 1 ? '' : 's'}`}
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
                return (
                  <tr
                    key={lead.id}
                    onClick={() => navigate(`/leads/${lead.id}`)}
                    style={{ cursor: 'pointer' }}
                  >
                    {view !== 'review' && (
                      <td onClick={(e) => e.stopPropagation()}>
                        <input type="checkbox" checked={selected.has(lead.id)} onChange={() => toggleSelect(lead.id)} />
                      </td>
                    )}
                    <td>{lead.first_name} {lead.last_name}</td>
                    <td className="mono">{lead.phone || '—'}</td>
                    <td className="mono">{lead.email || '—'}</td>
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

      {reviewLeadIds && (
        <MessageReview
          leadIds={reviewLeadIds}
          onClose={() => { setReviewLeadIds(null); loadLeads() }}
          onSent={() => loadLeads()}
        />
      )}

      {showAddLead && (
        <div className="message-review-overlay">
          <div className="message-review-modal add-lead-modal">
            <div className="message-review-header">
              <div>
                <h2 className="panel-title">Add a lead</h2>
                <p className="message-review-subtitle">
                  Enter someone directly — a walk-in, a phone call, anyone who didn't come in through a spreadsheet.
                </p>
              </div>
              <button className="back-link" onClick={closeAddLeadModal}>Close</button>
            </div>

            {addLeadResult ? (
              <div className="message-review-result">
                <h3 style={{ color: 'var(--signal-green)' }}>
                  {addLeadForm.first_name || addLeadResult.first_name} added
                </h3>
                {addLeadResult.is_duplicate && (
                  <p style={{ color: 'var(--signal-amber)', fontSize: 13 }}>
                    Heads up — this phone number matches an existing lead. Check Lead Cleanup if this might be a duplicate.
                  </p>
                )}
                <div style={{ display: 'flex', gap: 8 }}>
                  <button className="btn btn--secondary" onClick={closeAddLeadModal}>Add another</button>
                  <button className="btn btn--primary" onClick={() => { closeAddLeadModal(); navigate(`/leads/${addLeadResult.id}`) }}>
                    Open this lead
                  </button>
                </div>
              </div>
            ) : (
              <form onSubmit={handleAddLead} className="add-lead-form">
                <div className="add-lead-form-row">
                  <label className="settings-label">
                    First name
                    <input
                      className="settings-input"
                      value={addLeadForm.first_name}
                      onChange={(e) => setAddLeadForm({ ...addLeadForm, first_name: e.target.value })}
                      autoFocus
                    />
                  </label>
                  <label className="settings-label">
                    Last name
                    <input
                      className="settings-input"
                      value={addLeadForm.last_name}
                      onChange={(e) => setAddLeadForm({ ...addLeadForm, last_name: e.target.value })}
                    />
                  </label>
                </div>
                <div className="add-lead-form-row">
                  <label className="settings-label">
                    Phone
                    <input
                      className="settings-input"
                      type="tel"
                      placeholder="(214) 555-0100"
                      value={addLeadForm.phone}
                      onChange={(e) => setAddLeadForm({ ...addLeadForm, phone: e.target.value })}
                    />
                  </label>
                  <label className="settings-label">
                    Email
                    <input
                      className="settings-input"
                      type="email"
                      value={addLeadForm.email}
                      onChange={(e) => setAddLeadForm({ ...addLeadForm, email: e.target.value })}
                    />
                  </label>
                </div>
                <p className="add-lead-form-hint">At least one of phone or email is required.</p>
                <label className="settings-label">
                  Tier
                  <select
                    className="settings-input"
                    value={addLeadForm.tier}
                    onChange={(e) => setAddLeadForm({ ...addLeadForm, tier: e.target.value })}
                  >
                    {TIER_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>{opt.label}</option>
                    ))}
                  </select>
                </label>
                <label className="settings-label">
                  Notes (optional)
                  <textarea
                    className="compose-textarea"
                    rows={3}
                    value={addLeadForm.notes}
                    onChange={(e) => setAddLeadForm({ ...addLeadForm, notes: e.target.value })}
                  />
                </label>

                {addLeadError && <div className="compose-error">{addLeadError}</div>}

                <div className="settings-actions">
                  <button type="button" className="btn btn--secondary" onClick={closeAddLeadModal}>Cancel</button>
                  <button type="submit" className="btn btn--primary" disabled={addLeadSaving}>
                    {addLeadSaving ? 'Adding…' : 'Add lead'}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
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
