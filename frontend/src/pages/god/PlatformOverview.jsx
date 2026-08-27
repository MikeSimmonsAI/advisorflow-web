/**
 * PLATFORM OVERVIEW — where the owner lands, with nobody selected.
 *
 * The default state is deliberately empty of customer data. Before this screen
 * existed the owner arrived somewhere already inside a tenant, which is how you
 * end up editing the wrong company's records without noticing.
 *
 * Everything shown here is counted by the SERVER, including the exclusion of
 * the platform's own placeholder organization. Counting customers in the
 * browser is how "how many customers do we have" ends up different on two
 * screens.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../../api/client'
import { errText } from './GodOpsShared'
import './GodOps.css'

export default function PlatformOverview() {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')
  const [openBrand, setOpenBrand] = useState(null)
  const [customers, setCustomers] = useState({})

  useEffect(() => {
    api.get('/god/platform/overview').then(setData).catch(e => setErr(errText(e)))
  }, [])

  async function toggleBrand(p) {
    if (openBrand === p.id) { setOpenBrand(null); return }
    setOpenBrand(p.id)
    if (!customers[p.id]) {
      try {
        const r = await api.get('/god/platform/brands/' + p.id + '/customers')
        setCustomers(c => ({ ...c, [p.id]: r.customers }))
      } catch (e) { setErr(errText(e)) }
    }
  }

  if (err) return <div className="go-wrap"><div className="go-err">{err}</div></div>
  if (!data) return <div className="go-wrap"><div className="go-muted">Loading…</div></div>

  const t = data.totals

  return (
    <div className="go-wrap">
      <div className="go-head">
        <div>
          <h1 className="go-h1">Platform</h1>
          <p className="go-sub">
            No customer is selected. Choose a brand, then a customer, to operate
            inside one.
          </p>
        </div>
        <button className="go-btn go-btn-primary" onClick={() => nav('/god/customers/new')}>
          + Create customer
        </button>
      </div>

      <div className="go-stats">
        <Stat label="Brands" value={t.platforms} />
        <Stat label="Customers" value={t.customers} />
        <Stat label="Active" value={t.active_customers} />
        <Stat label="Sales orgs" value={t.brand_sales_orgs} />
      </div>

      {data.unassigned_customers.length > 0 && (
        <div className="go-warn">
          <strong>{data.unassigned_customers.length} customer(s) belong to no brand.</strong>{' '}
          An organization with no platform sits outside every scoping decision in
          the system — including the customer list of whoever is meant to own it.
          <ul className="go-plain-list">
            {data.unassigned_customers.map(o => (
              <li key={o.id}>
                {o.name} <span className="go-muted">({o.slug})</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="go-card-list">
        {data.platforms.map(p => (
          <div key={p.id} className="go-card">
            <button className="go-card-head" onClick={() => toggleBrand(p)}>
              <div>
                <div className="go-card-title">{p.name}</div>
                <div className="go-muted">
                  {p.customer_count} customer{p.customer_count === 1 ? '' : 's'}
                  {p.active_customer_count !== p.customer_count &&
                    <> · {p.active_customer_count} active</>}
                  {p.brand_sales_orgs.length > 0 &&
                    <> · {p.brand_sales_orgs.map(b => b.name).join(', ')}</>}
                </div>
              </div>
              <span className="go-chev">{openBrand === p.id ? '▾' : '▸'}</span>
            </button>

            {openBrand === p.id && (
              <div className="go-card-body">
                {!customers[p.id] && <div className="go-muted">Loading…</div>}
                {customers[p.id] && customers[p.id].length === 0 && (
                  <div className="go-muted">
                    No customers on this brand yet.
                  </div>
                )}
                {customers[p.id] && customers[p.id].map(o => (
                  <button key={o.id} className="go-row-btn"
                          onClick={() => nav('/god/customers/' + o.id)}>
                    <span className="go-row-name">{o.name}</span>
                    <span className="go-muted">{o.industry || '—'}</span>
                    <span className={'go-pill ' + (o.is_active ? 'live' : 'blocked')}>
                      {o.is_active ? 'Active' : 'Suspended'}
                    </span>
                  </button>
                ))}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function Stat({ label, value }) {
  return (
    <div className="go-stat">
      <div className="go-stat-v">{value}</div>
      <div className="go-stat-l">{label}</div>
    </div>
  )
}
