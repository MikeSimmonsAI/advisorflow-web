import { useState, useEffect, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { Panel, Empty, when, whenExact, errText } from './GodOpsShared'
import './GodOps.css'

export default function GodMeetings() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const brandId = params.get('brand') || ''

  const [rows, setRows] = useState(null)
  const [brands, setBrands] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    const qs = new URLSearchParams()
    if (brandId) qs.set('brand_id', brandId)
    fetch(`/god/ops/appointments?${qs}`, { credentials: 'include' })
      .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e)))
      .then(data => { setRows(data); setError(null) })
      .catch(e => setError(errText(e)))
      .finally(() => setLoading(false))
  }, [brandId])

  useEffect(() => {
    fetch('/god/ops/brands', { credentials: 'include' })
      .then(r => r.ok ? r.json() : [])
      .then(setBrands)
      .catch(() => {})
  }, [])

  useEffect(() => { load() }, [load])

  function set(key, val) {
    const next = new URLSearchParams(params)
    if (val) next.set(key, val); else next.delete(key)
    setParams(next)
  }

  return (
    <div className="god-page">
      <div className="god-page-header">
        <h1>Scheduled Meetings</h1>
        <div className="god-filter-bar">
          <select value={brandId} onChange={e => set('brand', e.target.value)}>
            <option value="">All brands</option>
            {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
        </div>
      </div>

      {error && <div className="god-error">{error}</div>}
      {loading && !rows && <div className="god-loading">Loading…</div>}

      {rows !== null && (
        <Panel title="Upcoming Meetings" count={rows.length}>
          {rows.length === 0
            ? <Empty>No upcoming scheduled meetings.</Empty>
            : (
              <div className="god-table-wrap">
                <table className="god-table">
                  <thead>
                    <tr>
                      <th>Title</th>
                      <th>Prospect</th>
                      <th>Company</th>
                      <th>Brand</th>
                      <th>Starts</th>
                      <th>Ends</th>
                      <th>Join</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(a => (
                      <tr
                        key={a.id}
                        className={a.opportunity_id ? 'god-tr-link' : ''}
                        onClick={() => a.opportunity_id && navigate(`/sales/opportunities/${a.opportunity_id}`)}
                        title={a.opportunity_id ? 'View opportunity' : undefined}
                      >
                        <td className="god-td-primary" data-label="Title">{a.title || '—'}</td>
                        <td data-label="Prospect">{a.prospect_name || '—'}</td>
                        <td data-label="Company">{a.prospect_company || '—'}</td>
                        <td data-label="Brand">{a.brand_name}</td>
                        <td data-label="Starts">{whenExact(a.starts_at)}</td>
                        <td data-label="Ends">{whenExact(a.ends_at)}</td>
                        <td data-label="Join">
                          {a.meeting_url
                            ? <a href={a.meeting_url} target="_blank" rel="noreferrer"
                                onClick={e => e.stopPropagation()}
                                className="god-link">Join</a>
                            : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          }
        </Panel>
      )}
    </div>
  )
}
