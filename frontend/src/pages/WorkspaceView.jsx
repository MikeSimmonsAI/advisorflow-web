/**
 * ONE PAGE FOR EVERY CONFIGURED WORKFLOW SCREEN.
 *
 * Two customers arrived wanting screens the product had no name for - "rate
 * requests", "renewals", "prospects", "follow-up", "walkthroughs". None of
 * them is a new kind of record; each is the lead or appointment table asked a
 * different question. A component per screen would have meant six copies of
 * the same table, six chances to get the empty state wrong, and a seventh the
 * first time somebody sold into a third vertical.
 *
 * So the screen is data. The server answers /workspace-views/{key} with the
 * columns, the counters and the rows already scoped, and this file renders
 * whatever comes back. It knows no customer's name and no vertical's
 * vocabulary; both arrive in the payload.
 *
 * IT DOES NOT WRITE. A row opens the record's own page, which is where the
 * permission checks and the audit trail already are. Adding an inline edit
 * here would mean re-deciding both, in a component whose whole point is that
 * it does not decide anything.
 */

import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../api/client'
import '../styles/shared.css'

function formatDate(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toLocaleDateString(undefined, {
    month: 'short', day: 'numeric', year: 'numeric',
  })
}

function formatDateTime(value) {
  if (!value) return '—'
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return String(value)
  return parsed.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  })
}

const DATE_COLUMNS = new Set(['created_at', 'updated_at', 'last_contact_date'])
const DATETIME_COLUMNS = new Set(['booked_time'])

/** A stage or status key, shown as a word rather than as a column value.
 *  `rate_review` printed raw is the defect this exists to avoid. */
function humanise(value) {
  if (value === null || value === undefined || value === '') return null
  return String(value)
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

function Cell({ column, value }) {
  if (value === null || value === undefined || value === '') {
    return <span style={{ color: 'var(--text-secondary)' }}>—</span>
  }
  if (column === 'contact') {
    return (
      <span>
        {value.phone ? <span className="mono">{value.phone}</span> : null}
        {value.phone && value.email ? <br /> : null}
        {value.email
          ? <span style={{ color: 'var(--text-secondary)', fontSize: '11px' }}>{value.email}</span>
          : null}
        {!value.phone && !value.email
          ? <span style={{ color: 'var(--text-secondary)' }}>—</span>
          : null}
      </span>
    )
  }
  if (DATETIME_COLUMNS.has(column)) return <span>{formatDateTime(value)}</span>
  if (DATE_COLUMNS.has(column)) return <span>{formatDate(value)}</span>
  if (column === 'tier' || column === 'status' || column === 'temperature') {
    return <span className="lead-tone-pill">{humanise(value)}</span>
  }
  if (column === 'note') {
    return (
      <span style={{ color: 'var(--text-secondary)', fontSize: '11px' }}>
        {String(value)}
      </span>
    )
  }
  return <span>{String(value)}</span>
}

export default function WorkspaceView() {
  const { viewKey } = useParams()
  const navigate = useNavigate()

  const [payload, setPayload] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')

  function load() {
    setLoading(true)
    setError('')
    api.get(`/workspace-views/${encodeURIComponent(viewKey)}`)
      .then((data) => { setPayload(data); setError('') })
      .catch((err) => {
        setPayload(null)
        setError(err.status === 404
          ? 'This screen is not switched on for your workspace.'
          : (err.message || 'Could not load this screen.'))
      })
      .finally(() => setLoading(false))
  }

  // The key is in the URL, so a rail click is a route change rather than a
  // component swap - reload on it or the second screen shows the first's rows.
  useEffect(() => { load() }, [viewKey])

  const view = payload?.view
  const columns = view?.columns || []
  const items = payload?.items || []

  const needle = search.trim().toLowerCase()
  const filtered = !needle ? items : items.filter((item) => (
    Object.values(item.values || {}).some((value) => {
      if (value === null || value === undefined) return false
      if (typeof value === 'object') {
        return Object.values(value).some(
          (inner) => inner && String(inner).toLowerCase().includes(needle))
      }
      return String(value).toLowerCase().includes(needle)
    })
  ))

  return (
    <div>
      <h1 className="page-title">{view?.title || 'Loading…'}</h1>
      {view?.subtitle
        ? <p className="page-subtitle">{view.subtitle}</p>
        : null}

      {error ? (
        <div
          className="panel"
          style={{
            borderColor: 'var(--border-danger)',
            background: 'var(--signal-red-dim)',
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            gap: '16px', marginBottom: '16px',
          }}
        >
          <span style={{ color: 'var(--signal-red)' }}>{error}</span>
          <button type="button" className="btn btn--secondary" onClick={load}>
            Try again
          </button>
        </div>
      ) : null}

      {payload?.stats?.length ? (
        <div
          className="panel"
          style={{
            display: 'grid',
            gridTemplateColumns: `repeat(${payload.stats.length}, minmax(0, 1fr))`,
            gap: '20px', marginBottom: '16px',
          }}
        >
          {payload.stats.map((stat) => (
            <div key={stat.label}>
              <div style={{ fontSize: '22px', fontWeight: 700, color: 'var(--accent)' }}>
                {loading ? '—' : stat.value}
              </div>
              <div style={{ fontSize: '11px', color: 'var(--text-secondary)' }}>
                {stat.label}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      <div className="panel">
        <div
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            gap: '16px', marginBottom: '12px',
          }}
        >
          <span style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>
            {loading
              ? 'Loading…'
              : `${filtered.length} of ${payload?.total ?? 0}`}
            {!loading && payload && payload.total > payload.returned
              ? ` · showing the ${payload.returned} most recent`
              : ''}
          </span>
          <input
            className="search-input"
            placeholder="Search this screen"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        {loading ? (
          <div className="empty-state">Loading…</div>
        ) : filtered.length === 0 ? (
          <div className="empty-state">
            {needle
              ? 'Nothing on this screen matches that search.'
              : (view?.empty || 'Nothing here yet.')}
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                {columns.map((column) => (
                  <th key={column}>{view.column_labels?.[column] || column}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((item) => (
                <tr
                  key={item.id}
                  style={{ cursor: item.lead_id ? 'pointer' : 'default' }}
                  onClick={() => { if (item.lead_id) navigate(`/leads/${item.lead_id}`) }}
                >
                  {columns.map((column) => (
                    <td key={column}>
                      <Cell column={column} value={item.values?.[column]} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}
