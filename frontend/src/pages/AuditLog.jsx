import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import '../styles/shared.css'
import './AuditLog.css'

const PAGE_SIZE = 50

function formatDate(value) {
  if (!value) return '—'
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(new Date(value))
  } catch {
    return value
  }
}

function formatAction(action) {
  if (!action) return '—'
  return action.replaceAll('.', ' › ').replaceAll('_', ' ')
}

function shortId(value) {
  if (!value) return '—'
  return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value
}

function exportCsv(entries) {
  const header = ['Time', 'Action', 'Target Type', 'Target ID', 'Actor', 'Details']
  const rows = entries.map(e => [
    formatDate(e.created_at),
    e.action,
    e.target_type,
    e.target_id,
    `"${(e.actor_name || e.actor_user_id || '').replace(/"/g, '""')}"`,
    `"${(e.details || '').replace(/"/g, '""')}"`,
  ])
  const csv = [header, ...rows].map(r => r.join(',')).join('\n')
  const blob = new Blob([csv], { type: 'text/csv' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`
  a.click()
  URL.revokeObjectURL(url)
}

const KNOWN_ACTIONS = [
  'compliance.suppress',
  'compliance.unsuppress',
  'compliance.permanent_dnc',
  'compliance.master_suppress',
  'lead.reassign',
  'lead.delete',
  'lead.import',
  'user.reset_password',
  'user.create',
  'user.deactivate',
  'template.create',
  'template.edit',
  'template.delete',
]

export default function AuditLog() {
  const [entries, setEntries] = useState([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)

  // Filter state — pending (typing) vs applied (committed)
  const [pendingAction, setPendingAction] = useState('')
  const [appliedAction, setAppliedAction] = useState('')
  const [pendingActor, setPendingActor] = useState('')
  const [appliedActor, setAppliedActor] = useState('')
  const [pendingDateFrom, setPendingDateFrom] = useState('')
  const [pendingDateTo, setPendingDateTo] = useState('')
  const [appliedDateFrom, setAppliedDateFrom] = useState('')
  const [appliedDateTo, setAppliedDateTo] = useState('')

  const [availableActions, setAvailableActions] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const pageNumber = useMemo(() => Math.floor(offset / PAGE_SIZE) + 1, [offset])
  const pageCount = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total])
  const canGoBack = offset > 0
  const canGoNext = offset + PAGE_SIZE < total

  const hasActiveFilter = !!(appliedAction || appliedActor || appliedDateFrom || appliedDateTo)

  async function loadAuditLog(nextOffset = 0, opts = {}) {
    setError('')
    setLoading(true)

    const action = opts.action !== undefined ? opts.action : appliedAction
    const actor = opts.actor !== undefined ? opts.actor : appliedActor
    const dateFrom = opts.dateFrom !== undefined ? opts.dateFrom : appliedDateFrom
    const dateTo = opts.dateTo !== undefined ? opts.dateTo : appliedDateTo

    try {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(nextOffset),
      })
      if (action.trim()) params.set('action', action.trim())
      if (actor.trim()) params.set('actor', actor.trim())
      if (dateFrom) params.set('date_from', dateFrom)
      if (dateTo) params.set('date_to', dateTo)

      const data = await api.get(`/audit-log?${params.toString()}`)
      setEntries(data.entries || [])
      setTotal(data.total || 0)
      setOffset(data.offset || 0)
    } catch (err) {
      setError(err.message || 'Could not load audit log.')
    } finally {
      setLoading(false)
    }
  }

  async function loadActions() {
    try {
      const data = await api.get('/audit-log/actions')
      const merged = Array.from(new Set([...KNOWN_ACTIONS, ...(data.actions || [])]))
      setAvailableActions(merged.sort())
    } catch {
      setAvailableActions(KNOWN_ACTIONS)
    }
  }

  useEffect(() => {
    loadAuditLog(0)
    loadActions()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function applyFilter(e) {
    e.preventDefault()
    const opts = {
      action: pendingAction,
      actor: pendingActor,
      dateFrom: pendingDateFrom,
      dateTo: pendingDateTo,
    }
    setAppliedAction(pendingAction)
    setAppliedActor(pendingActor)
    setAppliedDateFrom(pendingDateFrom)
    setAppliedDateTo(pendingDateTo)
    loadAuditLog(0, opts)
  }

  function clearFilters() {
    setPendingAction('')
    setPendingActor('')
    setPendingDateFrom('')
    setPendingDateTo('')
    setAppliedAction('')
    setAppliedActor('')
    setAppliedDateFrom('')
    setAppliedDateTo('')
    loadAuditLog(0, { action: '', actor: '', dateFrom: '', dateTo: '' })
  }

  function goPage(newOffset) {
    loadAuditLog(newOffset)
    setOffset(newOffset)
  }

  function filterSummary() {
    const parts = []
    if (appliedAction) parts.push(`action: ${formatAction(appliedAction)}`)
    if (appliedActor) parts.push(`actor: "${appliedActor}"`)
    if (appliedDateFrom) parts.push(`from ${appliedDateFrom}`)
    if (appliedDateTo) parts.push(`to ${appliedDateTo}`)
    return parts.join(' · ')
  }

  return (
    <div className="audit-log-page">
      <div className="page-header audit-log-header">
        <div>
          <p className="audit-log-eyebrow">Security Ledger</p>
          <h1 className="page-title">Audit Log</h1>
          <p className="page-subtitle">
            Admin-only trail of sensitive actions across leads, users, templates, suppression, and system changes.
          </p>
        </div>
        <div className="audit-log-summary panel">
          <span>Total Events</span>
          <strong>{total}</strong>
          <small>{hasActiveFilter ? filterSummary() : 'Current organization only'}</small>
        </div>
      </div>

      {error ? <div className="audit-log-alert">{error}</div> : null}

      <section className="panel audit-log-controls">
        <form onSubmit={applyFilter} className="audit-log-filter">
          <div className="filter-grid">
            <label className="filter-label">
              Action
              <select
                value={pendingAction}
                onChange={e => setPendingAction(e.target.value)}
                className="filter-select"
              >
                <option value="">All actions</option>
                {availableActions.map(a => (
                  <option key={a} value={a}>{formatAction(a)}</option>
                ))}
              </select>
            </label>

            <label className="filter-label">
              Actor (name)
              <input
                className="filter-input"
                value={pendingActor}
                onChange={e => setPendingActor(e.target.value)}
                placeholder="e.g. John Smith"
              />
            </label>

            <label className="filter-label">
              From date
              <input
                className="filter-input"
                type="date"
                value={pendingDateFrom}
                onChange={e => setPendingDateFrom(e.target.value)}
              />
            </label>

            <label className="filter-label">
              To date
              <input
                className="filter-input"
                type="date"
                value={pendingDateTo}
                onChange={e => setPendingDateTo(e.target.value)}
              />
            </label>
          </div>

          <div className="filter-actions">
            <button className="btn btn--primary" type="submit" disabled={loading}>Apply Filters</button>
            <button
              className="btn btn--secondary"
              type="button"
              onClick={clearFilters}
              disabled={loading || !hasActiveFilter}
            >
              Clear
            </button>
            {entries.length > 0 && (
              <button
                className="btn btn--export"
                type="button"
                onClick={() => exportCsv(entries)}
                disabled={loading}
                title="Export this page to CSV"
              >
                ↓ Export CSV
              </button>
            )}
          </div>
        </form>
      </section>

      <section className="panel audit-log-table-panel">
        <div className="panel-header">
          <h2 className="panel-title">Event Stream</h2>
          <span className="panel-count">Page {pageNumber} / {pageCount}</span>
        </div>

        <div className="audit-log-table-wrap">
          <table className="data-table audit-log-table">
            <thead>
              <tr>
                <th>Time</th>
                <th>Action</th>
                <th>Target</th>
                <th>Actor</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan="5" className="audit-log-empty">Loading audit events...</td></tr>
              ) : entries.length === 0 ? (
                <tr><td colSpan="5" className="audit-log-empty">
                  {hasActiveFilter ? 'No events match the current filters.' : 'No audit events found.'}
                </td></tr>
              ) : (
                entries.map((entry) => (
                  <tr key={entry.id}>
                    <td className="mono">{formatDate(entry.created_at)}</td>
                    <td>
                      <span className={`audit-action-pill ${entry.action.split('.')[0]}`}>
                        {formatAction(entry.action)}
                      </span>
                    </td>
                    <td>
                      <div className="audit-target">
                        <strong>{entry.target_type}</strong>
                        <span className="mono">{shortId(entry.target_id)}</span>
                      </div>
                    </td>
                    <td className="audit-actor">{entry.actor_name || shortId(entry.actor_user_id)}</td>
                    <td className="audit-details">{entry.details || '—'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="audit-log-pagination">
          <button
            className="btn btn--secondary"
            onClick={() => goPage(Math.max(0, offset - PAGE_SIZE))}
            disabled={loading || !canGoBack}
          >
            ← Previous
          </button>
          <span className="mono audit-range">
            {total === 0
              ? '0 events'
              : `${offset + 1}–${Math.min(offset + PAGE_SIZE, total)} of ${total}`}
          </span>
          <button
            className="btn btn--secondary"
            onClick={() => goPage(offset + PAGE_SIZE)}
            disabled={loading || !canGoNext}
          >
            Next →
          </button>
        </div>
      </section>
    </div>
  )
}
