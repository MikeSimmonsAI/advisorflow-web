/**
 * God Mode — every customer organisation (Checkpoint 6 §20).
 *
 * Across every platform, including the ones that never came through a sale.
 * Those are LABELLED rather than hidden: an owner counting customers needs to
 * know which of them the pipeline actually produced.
 *
 * Entering a customer's tenant uses the EXISTING elevated-context mechanism in
 * god_router — this screen links to it rather than reimplementing impersonation,
 * because a second path into a tenant is a second path to audit and secure.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { enterCustomer } from './god/enterCustomer'
import { Panel, Empty, StatusBadge, when, errText } from './god/GodOpsShared'
import './god/GodOps.css'

export default function GodCustomers() {
  const nav = useNavigate()
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState('')
  const [q, setQ] = useState('')
  const [entering, setEntering] = useState(null) // orgId currently being entered

  useEffect(() => {
    api.get('/god/ops/customer-organizations')
      .then(r => setRows(r.organizations || []))
      .catch(e => setErr(errText(e)))
  }, [])

  const filtered = (rows || []).filter(o =>
    !q.trim() || (o.name || '').toLowerCase().includes(q.trim().toLowerCase()))

  async function handleEnter(e, orgId, orgName) {
    e.stopPropagation() // don't trigger the row's implementation navigation
    setErr('')
    setEntering(orgId)
    try {
      await enterCustomer(orgId, orgName)
      nav('/god/customer-app')
    } catch (ex) {
      setErr(errText(ex))
      setEntering(null)
    }
  }

  return (
    <div className="go-scope">
      <div className="go-head">
        <div>
          <button className="go-back" onClick={() => nav('/god/sales-operations')}>← Sales Operations</button>
          <h1 style={{ marginTop: 8 }}>Customer organisations</h1>
          <p>Every tenant on every platform. Each one is isolated from the others
             and from the brand-sales tree.</p>
        </div>
      </div>

      {err ? <div className="go-note err">{err}</div> : null}

      <div className="go-filters">
        <input value={q} onChange={e => setQ(e.target.value)} placeholder="Search by name…" />
      </div>

      <Panel title="Customers" count={rows ? filtered.length : null}>
        {rows === null ? <Empty>Loading…</Empty>
          : !filtered.length ? <Empty>No customer organisations match.</Empty> : (
          <table className="go-table">
            <thead>
              <tr><th>Customer</th><th>Platform</th><th>Package</th><th>Users</th>
                  <th>Leads</th><th>Implementation</th><th>Source</th><th></th></tr>
            </thead>
            <tbody>
              {filtered.map(o => (
                <tr key={o.organization_id}
                    className={o.implementation ? 'clickable' : ''}
                    onClick={() => o.implementation
                      && nav('/god/implementations/' + o.implementation.id)}>
                  <td data-label="Customer">
                    {o.name}
                    {!o.is_active ? <span className="go-badge blocked" style={{ marginLeft: 8 }}>suspended</span> : null}
                  </td>
                  <td data-label="Platform">{o.platform ? o.platform.name : <span className="go-badge warn">none</span>}</td>
                  <td data-label="Package">{o.package ? o.package.name : '—'}</td>
                  <td data-label="Users">{o.user_count}</td>
                  <td data-label="Leads">{o.lead_count}</td>
                  <td data-label="Implementation">
                    {o.implementation
                      ? <StatusBadge status={o.implementation.status} />
                      : '—'}
                  </td>
                  <td data-label="Source">
                    {o.provisioned_from_sale
                      ? <span className="go-badge new">from a Won deal</span>
                      : <span className="go-badge">created outside the pipeline</span>}
                  </td>
                  <td data-label="Enter" onClick={e => e.stopPropagation()}>
                    <button
                      className="go-btn"
                      style={{ fontSize: 12, padding: '4px 10px', whiteSpace: 'nowrap' }}
                      disabled={entering === o.organization_id}
                      onClick={e => handleEnter(e, o.organization_id, o.name)}
                    >
                      {entering === o.organization_id ? 'Entering…' : 'Enter'}
                    </button>
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
