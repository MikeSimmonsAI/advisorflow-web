/**
 * God Mode — Implementation command centre (Checkpoint 6 §17).
 *
 * Every provisioned customer, filterable by the things that actually make an
 * owner pick one: brand, owner, status, blocked, overdue, live.
 *
 * Filters are applied SERVER-SIDE. A client-side filter over a truncated list
 * silently lies about how many there are.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { Panel, Empty, StatusBadge, Bar, when, errText } from './god/GodOpsShared'
import './god/GodOps.css'

export default function GodImplementations() {
  const nav = useNavigate()
  const [rows, setRows] = useState(null)
  const [statuses, setStatuses] = useState([])
  const [brands, setBrands] = useState([])
  const [err, setErr] = useState('')
  const [f, setF] = useState({ status: '', brand_sales_org_id: '', flag: '' })

  useEffect(() => {
    api.get('/god/ops/brands').then(r => setBrands(r.brands || [])).catch(() => {})
  }, [])

  useEffect(() => {
    const p = new URLSearchParams()
    if (f.status) p.set('status', f.status)
    if (f.brand_sales_org_id) p.set('brand_sales_org_id', f.brand_sales_org_id)
    if (f.flag === 'blocked') p.set('blocked', 'true')
    if (f.flag === 'overdue') p.set('overdue', 'true')
    if (f.flag === 'live') p.set('live', 'true')
    if (f.flag === 'not_live') p.set('live', 'false')
    setRows(null)
    api.get('/god/ops/implementations' + (p.toString() ? '?' + p.toString() : ''))
      .then(r => { setRows(r.implementations || []); setStatuses(r.statuses || []) })
      .catch(e => setErr(errText(e)))
  }, [f.status, f.brand_sales_org_id, f.flag])

  return (
    <div className="go-scope">
      <div className="go-head">
        <div>
          <button className="go-back" onClick={() => nav('/god/sales-operations')}>← Sales Operations</button>
          <h1 style={{ marginTop: 8 }}>Implementations</h1>
          <p>Every customer that came from a Won deal, from provisioned to live.</p>
        </div>
      </div>

      {err ? <div className="go-note err">{err}</div> : null}

      <div className="go-filters">
        <select value={f.brand_sales_org_id}
                onChange={e => setF({ ...f, brand_sales_org_id: e.target.value })}>
          <option value="">All brands</option>
          {brands.map(b => (
            <option key={b.brand_sales_org_id} value={b.brand_sales_org_id}>
              {b.brand_sales_org_name}
            </option>
          ))}
        </select>
        <select value={f.status} onChange={e => setF({ ...f, status: e.target.value })}>
          <option value="">Any status</option>
          {statuses.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
        <select value={f.flag} onChange={e => setF({ ...f, flag: e.target.value })}>
          <option value="">No flag filter</option>
          <option value="blocked">Blocked</option>
          <option value="overdue">Launch date overdue</option>
          <option value="not_live">Not live yet</option>
          <option value="live">Live</option>
        </select>
      </div>

      <Panel title="Customers" count={rows ? rows.length : null}>
        {rows === null ? <Empty>Loading…</Empty>
          : !rows.length ? <Empty>Nothing matches these filters.</Empty> : (
          <table className="go-table">
            <thead>
              <tr>
                <th>Customer</th><th>Brand</th><th>Package</th><th>Sold by</th>
                <th>Owner</th><th>Status</th><th>Progress</th><th>Launch</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(i => (
                <tr key={i.implementation_id} className="clickable"
                    onClick={() => nav('/god/implementations/' + i.implementation_id)}>
                  <td data-label="Customer">{i.organization_name}</td>
                  <td data-label="Brand">{i.brand_sales_org ? i.brand_sales_org.name : '—'}</td>
                  <td data-label="Package">{i.package ? i.package.name : '—'}</td>
                  <td data-label="Sold by">{i.sold_by ? i.sold_by.name : '—'}</td>
                  <td data-label="Owner">
                    {i.owner ? i.owner.name : <span className="go-badge warn">unassigned</span>}
                  </td>
                  <td data-label="Status">
                    <StatusBadge status={i.status} />
                    {i.blocker_note ? <div style={{ fontSize: 11, color: 'var(--go-red)', marginTop: 3 }}>
                      {i.blocker_note}</div> : null}
                  </td>
                  <td data-label="Progress">
                    {i.milestones_settled}/{i.milestones_total} · {i.percent_complete}%
                    <Bar percent={i.percent_complete} />
                  </td>
                  <td data-label="Launch">
                    {i.is_live
                      ? <span className="go-badge live">live {when(i.launched_at)}</span>
                      : i.target_launch_date
                        ? <span className={i.is_overdue ? 'go-badge blocked' : ''}>
                            {when(i.target_launch_date)}</span>
                        : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  )
}
