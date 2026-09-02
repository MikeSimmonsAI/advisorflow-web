import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import '../styles/shared.css'
import './ImportBatchReview.css'

const ACTION_TO_STATUS = {
  accept_as_new: 'accepted',
  merge_with_existing: 'merged',
  reject: 'rejected',
}

const DUP_LABELS = {
  new: 'New', matched_existing: 'Matched', possible_duplicate: 'Possible Dup',
  within_batch_duplicate: 'Batch Dup', dnc_blocked: 'DNC — Blocked',
}
const DUP_CLASS = {
  new: 'dup-new', matched_existing: 'dup-matched', possible_duplicate: 'dup-possible',
  within_batch_duplicate: 'dup-batch', dnc_blocked: 'dup-dnc',
}
const REV_LABELS = { pending: 'Pending', accepted: 'Accepted', merged: 'Merged', rejected: 'Rejected', committed: 'Committed' }
const REV_CLASS  = { pending: 'rev-pending', accepted: 'rev-accepted', merged: 'rev-merged', rejected: 'rev-rejected', committed: 'rev-committed' }
const CONF_LABELS = { high: 'High — phone+name', medium: 'Medium — phone', low: 'Low — email+name', none: '' }

function DupPill({ status }) {
  return <span className={`dup-pill ${DUP_CLASS[status] || ''}`}>{DUP_LABELS[status] || status}</span>
}
function RevPill({ status }) {
  return <span className={`rev-pill ${REV_CLASS[status] || ''}`}>{REV_LABELS[status] || status}</span>
}

function RowCard({ row, batchStatus, onDecision }) {
  const [loading, setLoading] = useState(false)
  const [err, setErr] = useState(null)
  const [note, setNote] = useState('')
  const [showNote, setShowNote] = useState(false)
  const isDnc = row.duplicate_status === 'dnc_blocked'
  const isCommitted = row.review_status === 'committed'
  const canAct = !isDnc && !isCommitted && !['committed', 'archived'].includes(batchStatus)
  const errors = Array.isArray(row.validation_errors) ? row.validation_errors : []

  async function act(action) {
    setLoading(true); setErr(null)
    try { await onDecision(row.id, action, note || undefined) }
    catch (e) { setErr(e.response?.data?.detail || 'Action failed') }
    finally { setLoading(false) }
  }

  return (
    <div className={`row-card ${isDnc ? 'row-card-dnc' : ''} ${isCommitted ? 'row-card-committed' : ''}`}>
      <div className="row-card-header">
        <div className="row-card-identity">
          <span className="row-name">{[row.first_name, row.last_name].filter(Boolean).join(' ') || <em>No name</em>}</span>
          <span className="row-num">#{row.row_number}</span>
        </div>
        <div className="row-card-pills">
          <DupPill status={row.duplicate_status} />
          <RevPill status={row.review_status} />
          {row.validation_status === 'invalid' && <span className="val-pill val-invalid">Invalid</span>}
          {row.validation_status === 'warning' && <span className="val-pill val-warning">Warning</span>}
        </div>
      </div>
      <div className="row-card-body">
        <div className="row-field">
          <span className="field-label">Phone</span>
          <span className="field-value">{row.phone_raw || '—'}</span>
          {row.phone_normalized && row.phone_normalized !== row.phone_raw &&
            <span className="field-norm">→ {row.phone_normalized}</span>}
        </div>
        <div className="row-field">
          <span className="field-label">Email</span>
          <span className="field-value">{row.email_normalized || '—'}</span>
        </div>
        {row.tier && <div className="row-field"><span className="field-label">Tier</span><span className="field-value">{row.tier}</span></div>}
        {row.city && (
          <div className="row-field">
            <span className="field-label">Location</span>
            <span className="field-value">{[row.city, row.state, row.zip_code].filter(Boolean).join(', ')}</span>
          </div>
        )}
        {row.matched_lead_id && (
          <div className="row-field row-field-match">
            <span className="field-label">Match</span>
            <span className="field-value">
              Lead …{row.matched_lead_id.slice(-8)}
              {row.match_confidence && ` — ${CONF_LABELS[row.match_confidence] || row.match_confidence}`}
            </span>
          </div>
        )}
        {errors.length > 0 && (
          <div className="row-field row-field-errors">
            <span className="field-label">Issues</span>
            <span className="field-value field-errors">{errors.join(' · ')}</span>
          </div>
        )}
        {isDnc && <div className="row-dnc-notice">⛔ DNC — matched a Do Not Contact lead. Cannot be accepted or merged.</div>}
        {row.review_note && (
          <div className="row-field"><span className="field-label">Note</span><span className="field-value">{row.review_note}</span></div>
        )}
      </div>
      {canAct && (
        <div className="row-card-actions">
          {showNote ? (
            <div className="note-row">
              <input className="note-input" type="text" placeholder="Optional note…" value={note}
                onChange={e => setNote(e.target.value)} autoFocus />
              <button className="btn btn-sm btn-outline" onClick={() => setShowNote(false)}>✕</button>
            </div>
          ) : (
            <button className="btn btn-sm btn-outline note-btn" onClick={() => setShowNote(true)}>+ Note</button>
          )}
          {row.review_status !== 'accepted' &&
            <button className="btn btn-sm btn-accept" disabled={loading} onClick={() => act('accept_as_new')}>Accept</button>}
          {row.matched_lead_id && row.review_status !== 'merged' &&
            <button className="btn btn-sm btn-merge" disabled={loading} onClick={() => act('merge_with_existing')}>Merge</button>}
          {row.review_status !== 'rejected' &&
            <button className="btn btn-sm btn-reject" disabled={loading} onClick={() => act('reject')}>Reject</button>}
        </div>
      )}
      {err && <div className="row-card-error">{err}</div>}
    </div>
  )
}

export default function ImportBatchReview() {
  const { batchId } = useParams()
  const navigate = useNavigate()
  const [batch, setBatch] = useState(null)
  const [rows, setRows] = useState([])
  const [totalRows, setTotalRows] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [reviewFilter, setReviewFilter] = useState('')
  const [dupFilter, setDupFilter] = useState('')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 50
  const [committing, setCommitting] = useState(false)
  const [commitError, setCommitError] = useState(null)
  const [commitResult, setCommitResult] = useState(null)

  const fetchBatch = useCallback(async () => {
    try { const r = await api.get(`/import-batches/${batchId}`); setBatch(r.data) }
    catch (e) { setError(e.response?.data?.detail || 'Failed to load batch.') }
  }, [batchId])

  const fetchRows = useCallback(async () => {
    setLoading(true)
    try {
      const p = new URLSearchParams({ page: page + 1, per_page: PAGE_SIZE })
      if (reviewFilter) p.set('review_status', reviewFilter)
      if (dupFilter) p.set('duplicate_status', dupFilter)
      if (search) p.set('search', search)
      const r = await api.get(`/import-batches/${batchId}/rows?${p}`)
      setRows(r.data.rows || []); setTotalRows(r.data.total || 0)
    } catch (e) { setError(e.response?.data?.detail || 'Failed to load rows.') }
    finally { setLoading(false) }
  }, [batchId, page, reviewFilter, dupFilter, search])

  useEffect(() => { fetchBatch() }, [fetchBatch])
  useEffect(() => { fetchRows() }, [fetchRows])

  async function handleDecision(rowId, action, note) {
    await api.patch(`/import-batches/${batchId}/rows/${rowId}`, {
      review_status: ACTION_TO_STATUS[action] || action,
      ...(note ? { review_note: note } : {}),
    })
    await Promise.all([fetchBatch(), fetchRows()])
  }

  async function handleBulkAction(filterFn, status, label) {
    const ids = rows.filter(filterFn).map(r => r.id)
    if (!ids.length) return
    if (!window.confirm(`Set ${ids.length} rows to "${label}"?`)) return
    await api.post(`/import-batches/${batchId}/rows/bulk-review`, { row_ids: ids, review_status: status })
    await Promise.all([fetchBatch(), fetchRows()])
  }

  async function handleCommit() {
    if (!window.confirm(
      'Commit? Accepted rows become live leads. Merged rows enrich existing leads. This cannot be undone.'
    )) return
    setCommitting(true); setCommitError(null); setCommitResult(null)
    try {
      const r = await api.post(`/import-batches/${batchId}/commit`)
      setBatch(r.data)
      setCommitResult({ committed: r.data.committed_rows, merged: r.data.merged_rows })
    } catch (e) { setCommitError(e.response?.data?.detail || 'Commit failed.') }
    finally { setCommitting(false) }
  }

  const batchStatus = batch?.status || ''
  const canAct = ['ready_for_review', 'reviewing'].includes(batchStatus)
  const canCommit = batchStatus === 'ready_to_commit'
  const isCommitted = ['committed', 'partially_committed'].includes(batchStatus)
  const totalPages = Math.ceil(totalRows / PAGE_SIZE)

  return (
    <div className="ibr-page">
      <button className="ibr-back" onClick={() => navigate('/import-batches')}>← All Imports</button>

      {batch && (
        <div className="ibr-batch-header">
          <div className="ibr-batch-title">
            <h1>{batch.display_name || batch.source_filename || 'Import Batch'}</h1>
            <span className={`import-status-pill status-${(batch.status || '').replace(/_/g, '-')}`}>
              {(batch.status || '').replace(/_/g, ' ')}
            </span>
          </div>
          <div className="ibr-batch-meta">
            {batch.source_type && <span>{batch.source_type.toUpperCase()}</span>}
            {batch.created_at && <span>· {new Date(batch.created_at).toLocaleDateString()}</span>}
          </div>
          <div className="ibr-counts">
            {[
              ['Total', batch.total_rows ?? 0, ''],
              ['New', batch.new_rows ?? 0, 'ibc-new'],
              ['Matched', batch.matched_rows ?? 0, 'ibc-matched'],
              ['Pending', batch.pending_rows ?? 0, 'ibc-pending'],
              ['Rejected', batch.rejected_rows ?? 0, 'ibc-reject'],
              ...(isCommitted ? [
                ['Created', batch.committed_rows ?? 0, 'ibc-new'],
                ['Merged', batch.merged_rows ?? 0, 'ibc-matched'],
              ] : []),
            ].map(([label, n, cls]) => (
              <div key={label} className="ibr-count">
                <span className={`ibc-n ${cls}`}>{n}</span>
                <span className="ibc-l">{label}</span>
              </div>
            ))}
          </div>

          {commitResult && (
            <div className="commit-result">
              ✅ Done — {commitResult.committed} leads created, {commitResult.merged} enriched.
            </div>
          )}

          <div className="ibr-actions">
            {canAct && (
              <div className="ibr-action-group">
                <button className="btn btn-sm btn-outline"
                  onClick={() => handleBulkAction(r => r.review_status === 'pending' && r.duplicate_status === 'new' && r.validation_status !== 'invalid', 'accepted', 'Accepted')}>
                  Bulk Accept New Valid
                </button>
                <button className="btn btn-sm btn-outline"
                  onClick={() => handleBulkAction(r => r.review_status === 'pending' && r.validation_status === 'invalid', 'rejected', 'Rejected')}>
                  Bulk Reject Invalid
                </button>
              </div>
            )}
            {canCommit && (
              <div className="ibr-action-group">
                <div className="commit-warning">⚠️ Committing creates live leads and enriches matched contacts. Cannot be undone.</div>
                <button className="btn btn-commit" onClick={handleCommit} disabled={committing}>
                  {committing ? 'Committing…' : '🚀 Commit to Live Leads'}
                </button>
              </div>
            )}
            {commitError && <div className="ibr-error">{commitError}</div>}
          </div>
        </div>
      )}

      {error && <div className="ibr-page-error">{error}</div>}

      <div className="ibr-filters">
        <select value={reviewFilter} onChange={e => { setReviewFilter(e.target.value); setPage(0) }}>
          <option value="">All review statuses</option>
          <option value="pending">Pending</option>
          <option value="accepted">Accepted</option>
          <option value="merged">Merged</option>
          <option value="rejected">Rejected</option>
          <option value="committed">Committed</option>
        </select>
        <select value={dupFilter} onChange={e => { setDupFilter(e.target.value); setPage(0) }}>
          <option value="">All match types</option>
          <option value="new">New</option>
          <option value="matched_existing">Matched Existing</option>
          <option value="possible_duplicate">Possible Duplicate</option>
          <option value="dnc_blocked">DNC Blocked</option>
        </select>
        <input type="text" className="ibr-search" placeholder="Search name, phone, email…"
          value={search} onChange={e => { setSearch(e.target.value); setPage(0) }} />
        <button className="btn btn-sm btn-outline" onClick={fetchRows}>Refresh</button>
      </div>

      {loading ? <div className="ibr-loading">Loading rows…</div>
        : rows.length === 0 ? <div className="ibr-empty">No rows match current filters.</div>
        : <div className="ibr-row-list">
            {rows.map(r => <RowCard key={r.id} row={r} batchStatus={batchStatus} onDecision={handleDecision} />)}
          </div>}

      {totalPages > 1 && (
        <div className="ibr-pagination">
          <button className="btn btn-sm btn-outline" disabled={page === 0} onClick={() => setPage(p => p - 1)}>← Prev</button>
          <span className="ibr-page-label">Page {page + 1} / {totalPages} ({totalRows} rows)</span>
          <button className="btn btn-sm btn-outline" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>Next →</button>
        </div>
      )}
    </div>
  )
}
