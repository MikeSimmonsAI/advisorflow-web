/**
 * Sold / Onboarding — what happened after Won (Checkpoint 6 §15, §16).
 *
 * A rep sees the deals they sold. A manager sees their brand's, with the
 * salesperson named. Both see the same coarse projection: where the customer
 * got to, who owns it, when it is meant to launch, and whether it is stuck.
 *
 * THE MANAGER VIEW NEEDED NO NEW SCOPING. `my_implementations` has filtered on
 * `brand_sales_org_id IN (manager orgs) OR sold_by_user_id == me` since
 * Checkpoint 6 — a manager's rows were already arriving. What was missing was
 * any sign of it: no salesperson column, no team framing, and a subtitle that
 * said "your deals". The endpoint now reports `is_manager` alongside the rows
 * so this screen can tell the two apart without a second call, and
 * `sales_projection` carries the salesperson, the package and the deal value.
 *
 * WHAT IS DELIBERATELY ABSENT. No tenant leads, no customer users, no
 * communications, no milestone detail, no internal notes, and no blocker text —
 * a blocker often names a customer's staffing problem, and the person who sold
 * the deal has no reason to be holding it. The server does not send those
 * fields at all; this screen could not show them if it tried. Widening it to a
 * manager did not widen that: a manager sees more ROWS, not more per row.
 *
 * PROVISIONING IS NOT HERE. Creating the customer organisation is a god action
 * in God Mode, deliberately. This screen reports what provisioning produced.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import SalesShell from './sales/SalesShell'
import {
  Card, Chip, Empty, ErrorBar, Metric, money, shortDate,
} from './sales/parts'

function tone(row) {
  if (row.is_live) return 'green'
  if (row.is_blocked) return 'red'
  return null
}

export default function SalesImplementations() {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(true)
  const [owner, setOwner] = useState('')

  const load = useCallback(async () => {
    setLoading(true); setError(null)
    try {
      const r = await api.get('/sales/implementations')
      // Tolerate the pre-wrap shape: an older backend returned a bare array.
      setData(Array.isArray(r)
        ? { implementations: r, is_manager: false, total: r.length }
        : r)
    } catch (e) {
      setError(e.message || 'Could not load.')
      setData({ implementations: [], is_manager: false, total: 0 })
    } finally { setLoading(false) }
  }, [])

  useEffect(() => { load() }, [load])

  const isManager = !!data?.is_manager
  const all = data?.implementations || []

  const sellers = useMemo(() => {
    const m = new Map()
    all.forEach(r => {
      if (r.sold_by_user_id && !m.has(r.sold_by_user_id)) {
        m.set(r.sold_by_user_id, r.sold_by_name || 'Unnamed')
      }
    })
    return [...m.entries()]
  }, [all])

  const rows = owner ? all.filter(r => r.sold_by_user_id === owner) : all

  const live = all.filter(r => r.is_live).length
  const blocked = all.filter(r => r.is_blocked).length
  const value = all.reduce((n, r) => n + (r.deal_value || 0), 0)

  return (
    <SalesShell
      title="Sold / Onboarding"
      subtitle={isManager
        ? 'Every deal your team has won, and how far the customer has got.'
        : 'Read-only progress for the customers your deals became.'}
      actions={
        <>
          {isManager && sellers.length > 1 && (
            <select className="sw-select" style={{ width: 180 }}
                    value={owner} onChange={e => setOwner(e.target.value)}>
              <option value="">Everyone</option>
              {sellers.map(([id, name]) => (
                <option key={id} value={id}>{name}</option>
              ))}
            </select>
          )}
          <button className="sw-btn" onClick={load} disabled={loading}>Refresh</button>
        </>
      }
    >
      <ErrorBar error={error} onRetry={load} />

      {all.length > 0 && (
        <div className="sw-metrics">
          <Metric label="Customers" value={all.length}
                  sub={isManager ? 'won by the team' : 'won by you'} />
          <Metric label="Live" value={live}
                  sub={live ? 'launched' : 'none launched yet'} />
          <Metric label="Onboarding" value={all.length - live} sub="still in progress" />
          <Metric label="Blocked" value={blocked} attn={blocked > 0}
                  sub={blocked ? 'stuck' : 'nothing stuck'} />
          <Metric label="Sold value" value={money(value) || '—'} sub="at close" />
        </div>
      )}

      {loading && !data ? <div className="sw-subtle">Loading…</div> : (
        <Card title="Customers"
              sub={owner ? 'Filtered to one salesperson' : null}
              right={owner
                ? <button className="sw-tiny" onClick={() => setOwner('')}>Clear</button>
                : null}
              bodyless>
          {!rows.length ? (
            <div className="sw-card-b">
              <Empty title="Nothing here yet">
                {isManager
                  ? 'None of your team’s Won deals have been provisioned into a customer organisation yet. Provisioning is a deliberate step, not something that happens the moment a deal closes.'
                  : 'None of your Won deals have been provisioned into a customer organisation. Provisioning is a deliberate step, not something that happens the moment a deal closes.'}
              </Empty>
            </div>
          ) : (
            <div className="sw-tablewrap">
              <table className="sw-table">
                <thead>
                  <tr>
                    <th>Customer</th>
                    {isManager ? <th>Sold by</th> : null}
                    <th>Package</th>
                    <th>Status</th>
                    <th>Implementation owner</th>
                    <th>Onboarding</th>
                    <th>Launch</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => (
                    <tr key={r.implementation_id}>
                      <td>
                        <strong>{r.customer_organization_name}</strong>
                        {r.company_name && r.company_name !== r.customer_organization_name
                          ? <div className="sw-subtle">sold as {r.company_name}</div>
                          : null}
                      </td>
                      {isManager ? (
                        <td>
                          {r.sold_by_name || <span className="sw-subtle">—</span>}
                          {r.won_at
                            ? <div className="sw-subtle">won {shortDate(r.won_at)}</div>
                            : null}
                        </td>
                      ) : null}
                      <td>
                        {r.package_name || <span className="sw-subtle">—</span>}
                        {r.deal_value != null
                          ? <div className="sw-subtle">{money(r.deal_value)}</div>
                          : null}
                      </td>
                      <td>
                        <Chip tone={tone(r)}>{r.status_label}</Chip>
                        {r.is_blocked && r.blocked_since ? (
                          <div className="sw-subtle">since {shortDate(r.blocked_since)}</div>
                        ) : null}
                      </td>
                      <td>{r.implementation_owner || <span className="sw-subtle">unassigned</span>}</td>
                      <td>{r.percent_complete}%</td>
                      <td>
                        {r.is_live
                          ? <Chip tone="green">live {shortDate(r.launched_at)}</Chip>
                          : (r.target_launch_date
                             ? shortDate(r.target_launch_date)
                             : <span className="sw-subtle">not set</span>)}
                      </td>
                      <td>
                        {r.opportunity_id ? (
                          <button className="sw-btn"
                                  onClick={() => nav('/sales/opportunities/' + r.opportunity_id)}>
                            Open deal
                          </button>
                        ) : null}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}

      <p className="sw-subtle" style={{ marginTop: 14 }}>
        You keep visibility after the sale; you do not get access to the
        customer's own data. Their leads, users and conversations stay inside
        their tenant. Milestone detail and blocker notes stay with the
        implementation owner.
      </p>
    </SalesShell>
  )
}
