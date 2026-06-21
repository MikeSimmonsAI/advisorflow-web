import { useEffect, useState, useRef, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
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

export default function Leads() {
  const navigate = useNavigate()
  const [leads, setLeads] = useState([])
  const [needsReview, setNeedsReview] = useState([])
  const [loading, setLoading] = useState(true)
  const [preview, setPreview] = useState(null)
  const [previewing, setPreviewing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const [sourceYear, setSourceYear] = useState('')
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
      const result = await api.upload('/leads/upload/confirm', formData)
      setPreview(null)
      pendingFile.current = null
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

  // Only leads with a phone, not DNC, not duplicate are eligible for bulk SMS send.
  const sendableLeads = filteredLeads.filter((l) => l.phone && l.status !== 'dnc' && !l.is_duplicate)

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
      <header className="page-header">
        <div>
          <h1 className="page-title">Leads</h1>
          <p className="page-subtitle">Import, dedupe, and route every lead to the right track.</p>
        </div>
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
          <input
            ref={fileInputRef}
            type="file"
            accept=".xlsx"
            onChange={handleFileChange}
            className="file-input"
          />
        </div>

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
            Use <code>{'{first_name}'}</code>, <code>{'{advisor_name}'}</code>, and <code>{'{booking_link}'}</code> as placeholders — they'll be filled in per lead.
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
                          <input type="checkbox" checked={selected.has(lead.id)} onChange={() => toggleSelect(lead.id)} />
                        )}
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
