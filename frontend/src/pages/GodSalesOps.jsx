/**
 * God Mode — Sales Operations (Checkpoint 6 §2, §3, §37).
 *
 * The owner's first screen: which brands are selling, what the pipeline is
 * worth, what is Won and not yet a customer, and which implementations need
 * somebody today.
 *
 * EVERY NUMBER COMES FROM ONE SERVER PAYLOAD. Nothing is computed here, and
 * nothing is padded when a figure is missing - a brand with no deals shows zero,
 * because a control plane that rounds up is a control plane its owner stops
 * believing.
 */
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { Kpi, Panel, Empty, money, when, StatusBadge, errText } from './god/GodOpsShared'
import './god/GodOps.css'

const QUEUES = [
  { key: 'won_awaiting_provisioning', label: 'Won — awaiting provisioning',
    action: 'Provision', hot: true },
  { key: 'ready_for_launch',          label: 'Ready for launch', action: 'Open' },
  { key: 'blocked_implementations',   label: 'Blocked implementations', action: 'Open', hot: true },
  { key: 'implementation_has_no_owner', label: 'Implementation has no owner', action: 'Assign', hot: true },
  { key: 'customer_admin_not_invited', label: 'Customer admin not invited', action: 'Invite', hot: true },
  { key: 'launch_date_overdue',       label: 'Launch date overdue', action: 'Open', hot: true },
  { key: 'billing_review_needed',     label: 'Billing review needed', action: 'Open' },
]

export default function GodSalesOps() {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get('/god/ops/sales-operations')
      .then(setData)
      .catch(e => setErr(errText(e)))
  }, [])

  if (err) return <div className="go-scope"><div className="go-note err">{err}</div></div>
  if (!data) return <div className="go-scope"><div className="go-empty">Loading…</div></div>

  const t = data.totals || {}
  const q = data.queues || {}

  return (
    <div className="go-scope">
      <div className="go-head">
        <div>
          <h1>Sales Operations</h1>
          <p>Every figure below is a live query. Brands, packages and customers
             are data — nothing on this screen is specific to any one brand.</p>
        </div>
        <button className="go-btn ghost" onClick={() => nav('/god/implementations')}>
          Implementations →
        </button>
      </div>

      <div className="go-kpis">
        <Kpi label="Brands selling" value={data.brands_selling} />
        <Kpi label="Open opportunities" value={t.open_opportunities} />
        <Kpi label="Pipeline value" value={money(t.pipeline_value)} />
        <Kpi label="Closing" value={t.closing_opportunities} />
        <Kpi label="Meetings scheduled" value={t.meetings_scheduled} />
        <Kpi label="Proposals outstanding" value={t.proposals_outstanding}
             sub={(t.proposals_with_buyer_activity || 0) + ' with buyer activity'} />
        <Kpi label="Won deals" value={t.won_deals} sub={money(t.won_value) + ' won'} />
        <Kpi label="Awaiting provisioning" value={t.won_awaiting_provisioning}
             tone={t.won_awaiting_provisioning > 0 ? 'alert' : undefined} />
        <Kpi label="Customers onboarding" value={t.customers_onboarding} />
        <Kpi label="Customers live" value={t.customers_live} tone="good" />
        <Kpi label="Blocked" value={t.implementations_blocked}
             tone={t.implementations_blocked > 0 ? 'alert' : undefined} />
        <Kpi label="Stalled / overdue" value={(t.stalled_opportunities || 0) + ' / ' + (t.overdue_next_actions || 0)}
             sub="opportunities / next actions"
             tone={(t.stalled_opportunities || t.overdue_next_actions) ? 'alert' : undefined} />
      </div>

      {t.customer_organizations_without_implementation > 0 ? (
        <div className="go-note warn">
          {t.customer_organizations_without_implementation} customer organisation
          {t.customer_organizations_without_implementation === 1 ? '' : 's'} exist
          outside this pipeline — created before Checkpoint 6, or by hand. They are
          counted separately rather than folded into the sales figures.
        </div>
      ) : null}

      <Panel title="Brands">
        <div className="go-body">
          <div className="go-brands">
            {(data.brands || []).map(b => (
              <div key={b.brand_sales_org_id} className="go-brand"
                   onClick={() => nav('/god/brands/' + b.brand_sales_org_id)}>
                <h3>{b.brand_sales_org_name}</h3>
                <div className="plat">
                  {b.platform ? b.platform.name : 'no platform'}
                  {' · '}
                  {b.managers.length
                    ? b.managers.map(m => m.name).join(', ')
                    : 'no manager'}
                  {' · '}{b.active_rep_count} active rep{b.active_rep_count === 1 ? '' : 's'}
                </div>
                <div className="stats">
                  <div><b>{b.open_opportunities}</b><span>Open</span></div>
                  <div><b>{money(b.pipeline_value)}</b><span>Pipeline</span></div>
                  <div><b>{b.closing_opportunities}</b><span>Closing</span></div>
                  <div><b>{b.won_deals}</b><span>Won</span></div>
                  <div><b>{b.customers_onboarding}</b><span>Onboarding</span></div>
                  <div><b>{b.customers_live}</b><span>Live</span></div>
                </div>
                {b.attention.length ? (
                  <ul className="go-attn">
                    {b.attention.map((a, i) => <li key={i}>{a}</li>)}
                  </ul>
                ) : null}
              </div>
            ))}
          </div>
          {!(data.brands || []).length ? <Empty>No brand sales organisations exist yet.</Empty> : null}
        </div>
      </Panel>

      {QUEUES.map(def => {
        const rows = q[def.key] || []
        return (
          <Panel key={def.key} title={def.label} count={rows.length} hot={def.hot}>
            {!rows.length ? <Empty>Nothing here. This queue is genuinely clear.</Empty> : (
              <table className="go-table">
                <thead>
                  <tr>
                    <th>Customer</th><th>Brand</th><th>Status</th>
                    <th className="num">Detail</th><th></th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map(r => {
                    const isOpp = def.key === 'won_awaiting_provisioning'
                    return (
                      <tr key={isOpp ? r.opportunity_id : r.implementation_id}>
                        <td data-label="Customer">
                          {isOpp ? r.company_name : (r.organization_name || r.opportunity_company)}
                        </td>
                        <td data-label="Brand">
                          {isOpp ? '' : (r.brand_sales_org ? r.brand_sales_org.name : '—')}
                        </td>
                        <td data-label="Status">
                          {isOpp ? <span className="go-badge warn">Won {when(r.won_at)}</span>
                                 : <StatusBadge status={r.status} />}
                        </td>
                        <td data-label="Detail" className="num">
                          {isOpp ? money(r.deal_value)
                                 : (r.target_launch_date
                                    ? 'launch ' + when(r.target_launch_date)
                                    : (r.blocker_note || r.percent_complete + '%'))}
                        </td>
                        <td data-label="">
                          <button className="go-btn sm"
                                  onClick={() => nav(isOpp
                                    ? '/god/provision/' + r.opportunity_id
                                    : '/god/implementations/' + r.implementation_id)}>
                            {def.action}
                          </button>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </Panel>
        )
      })}
    </div>
  )
}
