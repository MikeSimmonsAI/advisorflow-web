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
  return action.replaceAll('_', ' ')
}

function shortId(value) {
  if (!value) return '—'
  return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value
}

export default function AuditLog() {
  const [entries, setEntries] = useState([])
  const [total, setTotal] = useState(0)
  const [offset, setOffset] = useState(0)
  const [actionFilter, setActionFilter] = useState('')
  const [pendingAction, setPendingAction] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const pageNumber = useMemo(() => Math.floor(offset / PAGE_SIZE) + 1, [offset])
  const pageCount = useMemo(() => Math.max(1, Math.ceil(total / PAGE_SIZE)), [total])
  const canGoBack = offset > 0
  const canGoNext = offset + PAGE_SIZE < total

  async function loadAuditLog(nextOffset = offset, action = actionFilter) {
    setError('')
    setLoading(true)

    try {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(nextOffset),
      })
      if (action.trim()) params.set('action', action.trim())

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

  useEffect(() => {
    loadAuditLog(0, '')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function applyFilter(event) {
    event.preventDefault()
    const nextAction = pendingAction.trim()
    setActionFilter(nextAction)
    loadAuditLog(0, nextAction)
  }

  function clearFilter() {
    setPendingAction('')
    setActionFilter('')
    loadAuditLog(0, '')
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
          <small>{actionFilter ? `Filtered by ${formatAction(actionFilter)}` : 'Current organization only'}</small>
        </div>
      </div>

      {error ? <div className="audit-log-alert">{error}</div> : null}

      <section className="panel audit-log-controls">
        <form onSubmit={applyFilter} className="audit-log-filter">
          <label>
            Action filter
            <input
              value={pendingAction}
              onChange={(event) => setPendingAction(event.target.value)}
              placeholder="lead_reassigned, password_reset, suppression_entry_deleted"
            />
          </label>
          <button className="btn btn--primary" type="submit" disabled={loading}>Apply</button>
          <button className="btn btn--secondary" type="button" onClick={clearFilter} disabled={loading || (!actionFilter && !pendingAction)}>
            Clear
          </button>
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
                <tr><td colSpan="5" className="audit-log-empty">No audit events found.</td></tr>
              ) : (
                entries.map((entry) => (
                  <tr key={entry.id}>
                    <td className="mono">{formatDate(entry.created_at)}</td>
                    <td>
                      <span className="audit-action-pill">{formatAction(entry.action)}</span>
                    </td>
                    <td>
                      <div className="audit-target">
                        <strong>{entry.target_type}</strong>
                        <span className="mono">{shortId(entry.target_id)}</span>
                      </div>
                    </td>
                    <td className="mono">{shortId(entry.actor_user_id)}</td>
                    <td className="audit-details">{entry.details || '—'}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>

        <div className="audit-log-pagination">
          <button className="btn btn--secondary" onClick={() => loadAuditLog(Math.max(0, offset - PAGE_SIZE), actionFilter)} disabled={loading || !canGoBack}>
            Previous
          </button>
          <span className="mono">{total === 0 ? '0 events' : `${offset + 1}-${Math.min(offset + PAGE_SIZE, total)} of ${total}`}</span>
          <button className="btn btn--secondary" onClick={() => loadAuditLog(offset + PAGE_SIZE, actionFilter)} disabled={loading || !canGoNext}>
            Next
          </button>
        </div>
      </section>
    </div>
  )
}
