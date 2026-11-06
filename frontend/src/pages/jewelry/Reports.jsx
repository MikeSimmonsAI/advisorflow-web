import { useState } from 'react'
import { Empty, ErrorNote, Info, Kpi, Loading, Pill, money, n, useApi } from './jwui'

const PERIODS = [['7d', '7 days'], ['30d', '30 days'], ['90d', '90 days'], ['12m', '12 months']]

export default function Reports() {
  const [period, setPeriod] = useState('30d')
  const { data: d, error, loading, reload } = useApi(`/jewelry-ops/reports?period=${period}`, [period])
  const df = d?.definitions || {}
  return (
    <div>
      <div className="jw-head">
        <div><div className="jw-eyebrow">Reporting & Revenue</div><h1>Reports</h1><p>Counted from records saved in this workspace. Nothing here is estimated; where a connection doesn’t exist yet it says so.</p></div>
        <div className="jw-seg">{PERIODS.map(([k, l]) => <button key={k} className={period === k ? 'on' : ''} onClick={() => setPeriod(k)}>{l}</button>)}</div>
      </div>
      <ErrorNote onRetry={reload}>{error}</ErrorNote>
      {loading && !d && <Loading />}
      {d && (
        <>
          <div className="jw-kpis">
            <Kpi label="New customers" value={n(d.customers.new)} def={df.new_customers} />
            <Kpi label="Returning customers" value={n(d.customers.returning)} def={df.returning_customers} />
            <Kpi label="Reactivated" value={n(d.customers.reactivated)} def={df.reactivated} />
            <Kpi label="Appointments" value={n(d.appointments.in_period)} foot={`${n(d.appointments.completed)} completed visits`} def={df.appointments} />
            <Kpi label="Visit rate" value={d.appointments.conversion === null ? '—' : d.appointments.conversion + '%'} def={df.conversion} />
            <Kpi label="Transactions recorded" value={n(d.transactions.count)} def={df.transactions} />
            <Kpi label="Recorded revenue" value={money(d.transactions.revenue)} foot={`Bought from customers: ${money(d.transactions.purchases)}`} def={df.revenue} tone="gold" />
            <Kpi label="Marketing cost" value={money(d.campaigns.marketing_cost)} def={df.marketing_cost} />
          </div>
          <div className="jw-grid jw-grid--2">
            <div className="jw-card">
              <div className="jw-card__head"><h3>Appointments by type</h3></div>
              {Object.keys(d.appointments.by_type).length === 0 ? <Empty title="No appointments in this period" /> : (
                <ul className="jw-list">{Object.entries(d.appointments.by_type).map(([k, v]) => <li key={k}><span>{k}</span><b>{n(v)}</b></li>)}</ul>
              )}
            </div>
            <div className="jw-card">
              <div className="jw-card__head"><h3>Appointments by status</h3></div>
              <ul className="jw-list">{Object.entries(d.appointments.by_status).map(([k, v]) => <li key={k}><span className="jw-cap">{k.replace('_', ' ')}</span><b>{n(v)}</b></li>)}</ul>
            </div>
          </div>
          <div className="jw-card">
            <div className="jw-card__head"><div><h3>Campaign performance</h3><small>Delivery and replies: not connected (nothing has been sent) <Info text={df.campaigns} /></small></div></div>
            {d.campaign_rows.length === 0 ? <Empty title="No campaigns yet" /> : (
              <div className="jw-table-wrap"><table className="jw-table">
                <thead><tr><th>Campaign</th><th>Status</th><th className="num">Delivered</th><th className="num">Replies</th><th className="num">Appointments</th><th className="num">Visits</th><th className="num">Transactions</th><th className="num">Revenue</th><th className="num">Cost</th></tr></thead>
                <tbody>{d.campaign_rows.map(c => (
                  <tr key={c.id}><td><b>{c.name}</b></td><td><Pill>{c.status}</Pill></td><td className="num">Not connected</td><td className="num">Not connected</td>
                    <td className="num">{n(c.results.appointments)}</td><td className="num">{n(c.results.completed_visits)}</td><td className="num">{n(c.results.transactions)}</td>
                    <td className="num">{money(c.results.recorded_revenue)}</td><td className="num">{c.results.marketing_cost === null ? '—' : money(c.results.marketing_cost)}</td></tr>
                ))}</tbody>
              </table></div>
            )}
          </div>
          <div className="jw-card">
            <div className="jw-card__head"><h3>How each number is defined</h3></div>
            <dl className="jw-defs">{Object.entries(df).map(([k, v]) => <div key={k}><dt>{k.replace(/_/g, ' ')}</dt><dd>{v}</dd></div>)}</dl>
          </div>
        </>
      )}
    </div>
  )
}
