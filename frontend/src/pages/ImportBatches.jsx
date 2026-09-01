import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import '../styles/shared.css'
import './ImportBatches.css'

const STATUS_LABELS = {
  uploading: 'Uploading', processing: 'Processing',
  ready_for_review: 'Ready for Review', reviewing: 'Reviewing',
  ready_to_commit: 'Ready to Commit', committing: 'Committing…',
  committed: 'Committed', partially_committed: 'Partially Committed',
  failed: 'Failed', archived: 'Archived',
}
const STATUS_CLASS = {
  uploading: 'status-processing', processing: 'status-processing',
  ready_for_review: 'status-ready', reviewing: 'status-reviewing',
  ready_to_commit: 'status-ready-commit', committing: 'status-processing',
  committed: 'status-committed', partially_committed: 'status-committed',
  failed: 'status-failed', archived: 'status-archived',
}

function StatusPill({ status }) {
  return (
    <span className={`import-status-pill ${STATUS_CLASS[status] || ''}`}>
      {STATUS_LABELS[status] || status}
    </span>
  )
}

function BatchRow({ batch, onRefresh }) {
  const navigate = useNavigate()
  const [archiving, setArchiving] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [error, setError] = useState(null)
  const canReview = ['ready_for_review', 'reviewing', 'ready_to_commit'].includes(batch.status)
  const canArchive = ['committed', 'partially_committed', 'failed'].includes(batch.status)
  const canDelete = ['failed', 'archived'].includes(batch.status)

  async function handleArchive() {
    if (!window.confirm(`Archive "${batch.display_name}"? This cannot be undone.`)) return
    setArchiving(true); setError(null)
    try { await api.post(`/import-batches/${batch.id}/archive`); onRefresh() }
    catch (e) { setError(e.response?.data?.detail || 'Archive failed') }
    finally { setArchiving(false) }
  }

  async function handleDelete() {
    if (!window.confirm(`Delete "${batch.display_name}" and all staged rows? Cannot be undone.`)) return
    setDeleting(true); setError(null)
    try { await api.delete(`/import-batches/${batch.id}`); onRefresh() }
    catch (e) { setError(e.response?.data?.detail || 'Delete failed') }
    finally { setDeleting(false) }
  }

  return (
    <div className="import-batch-row">
      <div className="batch-row-main">
        <div className="batch-row-name">
          <span className="batch-display-name">{batch.display_name || batch.source_filename || 'Untitled'}</span>
          <StatusPill status={batch.status} />
        </div>
        <div className="batch-row-meta">
          {batch.source_type && <span>{batch.source_type.toUpperCase()}</span>}
          {batch.created_at && <span>· {new Date(batch.created_at).toLocaleDateString()}</span>}
        </div>
        {batch.error_message && <div className="batch-error-msg">{batch.error_message}</div>}
      </div>
      <div className="batch-row-counts">
        {[
          ['Total', batch.total_rows ?? 0, ''],
          ['New', batch.new_rows ?? 0, 'count-new'],
          ['Matched', batch.matched_rows ?? 0, 'count-matched'],
          ['Pending', batch.pending_rows ?? 0, 'count-pending'],
          ['Rejected', batch.rejected_rows ?? 0, 'count-rejected'],
          ...(['committed', 'partially_committed'].includes(batch.status) ? [
            ['Created', batch.committed_rows ?? 0, 'count-committed'],
            ['Merged', batch.merged_rows ?? 0, 'count-merged'],
          ] : []),
        ].map(([label, n, cls]) => (
          <div key={label} className="count-cell">
            <span className={`count-num ${cls}`}>{n}</span>
            <span className="count-label">{label}</span>
          </div>
        ))}
      </div>
      <div className="batch-row-actions">
        {canReview && (
          <button className="btn btn-primary btn-sm" onClick={() => navigate(`/import-batches/${batch.id}`)}>
            Review
          </button>
        )}
        {['committed', 'partially_committed'].includes(batch.status) && (
          <button className="btn btn-outline btn-sm" onClick={() => navigate(`/import-batches/${batch.id}`)}>
            View
          </button>
        )}
        {canArchive && (
          <button className="btn btn-outline btn-sm" onClick={handleArchive} disabled={archiving}>
            {archiving ? 'Archiving…' : 'Archive'}
          </button>
        )}
        {canDelete && (
          <button className="btn btn-danger btn-sm" onClick={handleDelete} disabled={deleting}>
            {deleting ? 'Deleting…' : 'Delete'}
          </button>
        )}
      </div>
      {error && <div className="batch-inline-error">{error}</div>}
    </div>
  )
}

export default function ImportBatches() {
  const navigate = useNavigate()
  const [batches, setBatches] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [page, setPage] = useState(0)
  const PAGE_SIZE = 25
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState(null)
  const [uploadFile, setUploadFile] = useState(null)
  const [displayName, setDisplayName] = useState('')
  const [sourceCategory, setSourceCategory] = useState('')
  const [showUpload, setShowUpload] = useState(false)

  const fetchBatches = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const params = new URLSearchParams({ page: page + 1, per_page: PAGE_SIZE })
      if (statusFilter) params.set('status', statusFilter)
      const res = await api.get(`/import-batches?${params}`)
      setBatches(res.data.batches || []); setTotal(res.data.total || 0)
    } catch (e) { setError(e.response?.data?.detail || 'Failed to load import batches.') }
    finally { setLoading(false) }
  }, [page, statusFilter])

  useEffect(() => { fetchBatches() }, [fetchBatches])

  async function handleUpload(e) {
    e.preventDefault()
    if (!uploadFile) return
    setUploading(true); setUploadError(null)
    try {
      const form = new FormData()
      form.append('file', uploadFile)
      form.append('display_name', displayName || uploadFile.name)
      if (sourceCategory) form.append('source_category', sourceCategory)
      const res = await api.post('/import-batches', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      })
      navigate(`/import-batches/${res.data.id}`)
    } catch (e) {
      setUploadError(e.response?.data?.detail || 'Upload failed.')
      setUploading(false)
    }
  }

  const totalPages = Math.ceil(total / PAGE_SIZE)

  return (
    <div className="import-batches-page">
      <div className="import-batches-header">
        <div>
          <h1 className="page-title">Lead Imports</h1>
          <p className="page-subtitle">
            Stage, review, and commit imported contact lists — no live leads are created until you commit a reviewed batch.
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowUpload(v => !v)}>
          {showUpload ? 'Cancel' : '+ Upload File'}
        </button>
      </div>

      {showUpload && (
        <div className="upload-panel">
          <h2 className="upload-panel-title">Stage a New Import</h2>
          <form className="upload-form" onSubmit={handleUpload}>
            <div className="form-row">
              <label className="form-label">File <span className="required">*</span></label>
              <input type="file" accept=".xlsx,.xls,.csv" required
                onChange={e => setUploadFile(e.target.files[0] || null)} />
              <span className="form-hint">.xlsx, .xls, or .csv — up to 50 MB</span>
            </div>
            <div className="form-row-group">
              <div className="form-row">
                <label className="form-label">List Name</label>
                <input type="text" placeholder="e.g. 2024 Pre-Need Purchased List"
                  value={displayName} onChange={e => setDisplayName(e.target.value)} />
              </div>
              <div className="form-row">
                <label className="form-label">Source Category</label>
                <select value={sourceCategory} onChange={e => setSourceCategory(e.target.value)}>
                  <option value="">— select —</option>
                  <option value="purchased">Purchased</option>
                  <option value="organic">Organic</option>
                  <option value="referral">Referral</option>
                  <option value="database">Database</option>
                </select>
              </div>
            </div>
            {uploadError && <div className="form-error">{uploadError}</div>}
            <div className="form-actions">
              <button type="submit" className="btn btn-primary" disabled={uploading || !uploadFile}>
                {uploading ? 'Staging…' : 'Stage Import'}
              </button>
            </div>
          </form>
        </div>
      )}

      <div className="import-batches-filters">
        <label className="filter-label">Status</label>
        <select value={statusFilter} onChange={e => { setStatusFilter(e.target.value); setPage(0) }}>
          <option value="">All</option>
          <option value="ready_for_review">Ready for Review</option>
          <option value="reviewing">Reviewing</option>
          <option value="ready_to_commit">Ready to Commit</option>
          <option value="committed">Committed</option>
          <option value="failed">Failed</option>
          <option value="archived">Archived</option>
        </select>
        <button className="btn btn-outline btn-sm" onClick={fetchBatches}>Refresh</button>
      </div>

      {error && <div className="page-error">{error}</div>}

      {loading ? (
        <div className="import-batches-loading">Loading…</div>
      ) : batches.length === 0 ? (
        <div className="import-batches-empty">
          No import batches yet.
          {!showUpload && (
            <button className="btn btn-outline btn-sm" style={{ marginLeft: 12 }}
              onClick={() => setShowUpload(true)}>Upload your first file</button>
          )}
        </div>
      ) : (
        <div className="import-batch-list">
          {batches.map(b => <BatchRow key={b.id} batch={b} onRefresh={fetchBatches} />)}
        </div>
      )}

      {totalPages > 1 && (
        <div className="import-pagination">
          <button className="btn btn-outline btn-sm" disabled={page === 0} onClick={() => setPage(p => p - 1)}>← Prev</button>
          <span className="pagination-label">Page {page + 1} of {totalPages} ({total} batches)</span>
          <button className="btn btn-outline btn-sm" disabled={page >= totalPages - 1} onClick={() => setPage(p => p + 1)}>Next →</button>
        </div>
      )}
    </div>
  )
}
