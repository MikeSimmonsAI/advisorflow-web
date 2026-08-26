/**
 * God Mode — one brand's operations (Checkpoint 6 §3, §4).
 *
 * Two contexts on one page, kept visibly separate: the brand's SALES operation
 * on the left of the mental model, and the CUSTOMERS it produced below it.
 * They are not the same tree and the screen does not pretend otherwise.
 *
 * The configuration section only shows settings backed by real columns. There
 * is no generic settings framework here on purpose — an editor for a field
 * nothing reads is worse than no editor at all.
 */
import { useEffect, useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import { Kpi, Panel, Empty, Fact, money, when, StatusBadge, errText, Bar } from './god/GodOpsShared'
import './god/GodOps.css'

export default function GodBrandDetail() {
  const { brandId } = useParams()
  const nav = useNavigate()
  const [d, setD] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    api.get('/god/ops/brands/' + brandId).then(setD).catch(e => setErr(errText(e)))
  }, [brandId])

  if (err) return <div className="go-scope"><div className="go-note err">{err}</div></div>
  if (!d) return <div className="go-scope"><div className="go-empty">Loading…</div></div>

  const s = d.summary || {}
  const cfg = d.configuration || {}

  return (
    <div className="go-scope">
      <div className="go-head">
        <div>
          <button className="go-back" onClick={() => nav('/god/sales-operations')}>← Sales Operations</button>
          <h1 style={{ marginTop: 8 }}>{s.brand_sales_org_name}</h1>
          <p>{s.platform ? s.platform.name + ' platform' : 'No platform'} ·{' '}
             {s.is_active ? 'active' : 'inactive'}</p>
        </div>
      </div>

      {s.attention && s.attention.length ? (
        <div className="go-note warn">
          <strong>Needs attention</strong>
          <ul>{s.attention.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </div>
      ) : null}

      <div className="go-kpis">
        <Kpi label="Open opportunities" value={s.open_opportunities} />
        <Kpi label="Pipeline value" value={money(s.pipeline_value)} />
        <Kpi label="Closing" value={s.closing_opportunities} />
        <Kpi label="Meetings" value={s.meetings_scheduled} />
        <Kpi label="Proposals out" value={s.proposals_outstanding}
             sub={(s.proposals_with_buyer_activity || 0) + ' viewed by the buyer'} />
        <Kpi label="Won" value={s.won_deals} sub={money(s.won_value)} />
        <Kpi label="Awaiting provisioning" value={s.won_awaiting_provisioning}
             tone={s.won_awaiting_provisioning > 0 ? 'alert' : undefined} />
        <Kpi label="Customers live" value={s.customers_live} tone="good" />
      </div>

      <Panel title="Sales team">
        <div className="go-body">
          <div className="go-facts">
            <Fact k="Sales managers"
                  v={s.managers && s.managers.length
                     ? s.managers.map(m => m.name + (m.is_active ? '' : ' (inactive)')).join(', ')
                     : null} />
            <Fact k="Representatives" v={s.rep_count} />
            <Fact k="Active representatives" v={s.active_rep_count} />
            <Fact k="Stalled opportunities" v={s.stalled_opportunities} />
            <Fact k="Overdue next actions" v={s.overdue_next_actions} />
          </div>
        </div>
      </Panel>

      <Panel title="Package catalogue" count={(cfg.packages || []).length}>
        {!(cfg.packages || []).length ? (
          <Empty>This platform has no packages configured.</Empty>
        ) : (
          <table className="go-table">
            <thead>
              <tr><th>Package</th><th>Key</th><th className="num">Price</th>
                  <th className="num">Setup</th><th>Billing plan</th><th>Status</th></tr>
            </thead>
            <tbody>
              {cfg.packages.map(p => (
                <tr key={p.id}>
                  <td data-label="Package">{p.name}</td>
                  <td data-label="Key"><code>{p.key}</code></td>
                  <td data-label="Price" className="num">
                    {p.is_custom ? 'custom' : money(p.price, p.currency)}
                  </td>
                  <td data-label="Setup" className="num">
                    {p.setup_fee ? money(p.setup_fee, p.currency) : '—'}
                  </td>
                  <td data-label="Billing plan">
                    {p.billing_plan_key
                      ? <span className="go-badge">{p.billing_plan_key}</span>
                      : <span className="go-badge">not linked</span>}
                  </td>
                  <td data-label="Status">
                    <span className={'go-badge' + (p.is_active ? ' live' : '')}>
                      {p.is_active ? 'active' : 'inactive'}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="go-body" style={{ borderTop: '1px solid var(--go-line)' }}>
          <p style={{ margin: 0, fontSize: 12, color: 'var(--go-dim)' }}>
            Sales packages are not the legacy Stripe plans. The billing plan column
            shows the link where one exists; it is deliberately not wired to
            charging, and provisioning records billing <em>intent</em> only.
          </p>
        </div>
      </Panel>

      <Panel title="Customers from this brand" count={(d.implementations || []).length}>
        {!(d.implementations || []).length ? (
          <Empty>No deals from this brand have been provisioned yet.</Empty>
        ) : (
          <table className="go-table">
            <thead>
              <tr><th>Customer</th><th>Package</th><th>Owner</th><th>Status</th>
                  <th>Progress</th><th>Launch</th></tr>
            </thead>
            <tbody>
              {d.implementations.map(i => (
                <tr key={i.implementation_id} className="clickable"
                    onClick={() => nav('/god/implementations/' + i.implementation_id)}>
                  <td data-label="Customer">{i.organization_name}</td>
                  <td data-label="Package">{i.package ? i.package.name : '—'}</td>
                  <td data-label="Owner">{i.owner ? i.owner.name : <span className="go-badge warn">unassigned</span>}</td>
                  <td data-label="Status"><StatusBadge status={i.status} /></td>
                  <td data-label="Progress">
                    {i.percent_complete}%<Bar percent={i.percent_complete} />
                  </td>
                  <td data-label="Launch">
                    {i.is_live ? when(i.launched_at)
                      : (i.target_launch_date
                         ? <span className={i.is_overdue ? 'go-badge blocked' : ''}>
                             {when(i.target_launch_date)}</span>
                         : '—')}
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
