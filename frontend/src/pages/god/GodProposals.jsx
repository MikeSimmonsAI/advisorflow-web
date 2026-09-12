import { useState, useEffect, useCallback } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { Panel, Empty, money, when, errText } from './GodOpsShared'
import './GodOps.css'

export default function GodProposals() {
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
    fetch(`/god/ops/proposals?${qs}`, { credentials: 'include' })
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
        <h1>Outstanding Proposals</h1>
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
        <Panel title="Outstanding Proposals" count={rows.length}>
          {rows.length === 0
            ? <Empty>No outstanding proposals.</Empty>
            : (
              <div className="god-table-wrap">
                <table className="god-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Client</th>
                      <th>Title</th>
                      <th>Brand</th>
                      <th>Status</th>
                      <th>Amount</th>
                      <th>Sent</th>
                      <th>Last viewed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map(p => (
                      <tr
                        key={p.id}
                        className={p.opportunity_id ? 'god-tr-link' : ''}
                        onClick={() => p.opportunity_id && navigate(`/sales/opportunities/${p.opportunity_id}`)}
                        title={p.opportunity_id ? 'View opportunity' : undefined}
                      >
                        <td className="god-td-mono" data-label="#">{p.proposal_number}</td>
                        <td className="god-td-primary" data-label="Client">{p.client_company || '—'}</td>
                        <td data-label="Title">{p.title || '—'}</td>
                        <td data-label="Brand">{p.brand_name}</td>
                        <td data-label="Status"><span className={`god-prop-status ${p.sales_status}`}>{p.sales_status_label}</span></td>
                        <td className="god-td-num" data-label="Amount">{money(p.final_amount)}</td>
                        <td data-label="Sent">{when(p.sent_at)}</td>
                        <td data-label="Last viewed">{when(p.last_viewed_at)}</td>
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
