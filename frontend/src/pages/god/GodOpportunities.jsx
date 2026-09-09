import { useState, useEffect, useCallback } from 'react'
import { useSearchParams, useNavigate, Link } from 'react-router-dom'
import { Panel, Empty, money, when, errText } from './GodOpsShared'
import './GodOps.css'

const FILTERS = [
  { value: '', label: 'All' },
  { value: 'open', label: 'Open' },
  { value: 'closing', label: 'Closing' },
  { value: 'stalled_or_overdue', label: 'Stalled / Overdue' },
  { value: 'won', label: 'Won' },
  { value: 'awaiting_provisioning', label: 'Awaiting Provisioning' },
]

export default function GodOpportunities() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const filterBy = params.get('filter') || ''
  const brandId = params.get('brand') || ''

  const [rows, setRows] = useState(null)
  const [brands, setBrands] = useState([])
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    const qs = new URLSearchParams()
    if (filterBy) qs.set('filter_by', filterBy)
    if (brandId) qs.set('brand_id', brandId)
    fetch(`/god/ops/opportunities?${qs}`, { credentials: 'include' })
      .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e)))
      .then(data => { setRows(data); setError(null) })
      .catch(e => setError(errText(e)))
      .finally(() => setLoading(false))
  }, [filterBy, brandId])

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
        <h1>Opportunities</h1>
        <div className="god-filter-bar">
          <select value={brandId} onChange={e => set('brand', e.target.value)}>
            <option value="">All brands</option>
            {brands.map(b => <option key={b.id} value={b.id}>{b.name}</option>)}
          </select>
          <div className="god-filter-tabs">
            {FILTERS.map(f => (
              <button
                key={f.value}
                className={`god-filter-tab${filterBy === f.value ? ' active' : ''}`}
                onClick={() => set('filter', f.value)}
              >{f.label}</button>
            ))}
          </div>
        </div>
      </div>

      {error && <div className="god-error">{error}</div>}
      {loading && !rows && <div className="god-loading">Loading…</div>}

      {rows !== null && (
        <Panel title="Opportunities" count={rows.length}>
          {rows.length === 0
            ? <Empty>No opportunities match this filter.</Empty>
            : (
              <div className="god-table-wrap">
                <table className="god-table">
                  <thead>
                    <tr>
                      <th>Company</th>
                      <th>Contact</th>
                      <th>Brand</th>
                      <th>Stage</th>
                      <th>Owner</th>
                      <th>Value</th>
                      <th>Next action due</th>
                      <th>Flags</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(o => (
                      <tr
                        key={o.id}
                        className="god-tr-link"
                        onClick={() => navigate(`/sales/opportunities/${o.id}`)}
                        title="View opportunity"
                      >
                        <td className="god-td-primary">{o.company_name || '—'}</td>
                        <td>{o.contact_name || '—'}</td>
                        <td>{o.brand_name}</td>
                        <td>{o.stage_label}</td>
                        <td>{o.owner_name}</td>
                        <td className="god-td-num">{money(o.deal_value)}</td>
                        <td>{when(o.next_action_due_at)}</td>
                        <td>
                          {o.is_stalled && <span className="god-flag stalled">Stalled</span>}
                          {o.is_overdue && <span className="god-flag overdue">Overdue</span>}
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
