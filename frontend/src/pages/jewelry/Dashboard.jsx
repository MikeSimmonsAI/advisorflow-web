import { useState } from 'react'
import { Link } from 'react-router-dom'
import { BASE, useJewelry } from './JewelryShell'
import { Empty, ErrorNote, Kpi, Loading, Pill, day, money, n, useApi, when } from './jwui'

const PERIODS = [['7d', '7 days'], ['30d', '30 days'], ['90d', '90 days'], ['12m', '12 months']]

function Chart({ series }) {
  const W = 640, H = 210, pl = 50, pb = 24, pt = 10
  const maxRev = Math.max(0, ...series.map(s => s.revenue))
  const maxNew = Math.max(0, ...series.map(s => s.new_customers))
  if (maxRev === 0 && maxNew === 0) {
    return <Empty title="Nothing recorded in this period yet">The chart fills in as transactions are recorded and customers are added.</Empty>
  }
  const step = (W - pl - 6) / series.length, bw = Math.min(30, step * 0.6)
  const yR = v => pt + (H - pt - pb) * (1 - (maxRev ? v / maxRev : 0))
  const yN = v => pt + (H - pt - pb) * (1 - (maxNew ? v / maxNew : 0))
  const pts = series.map((s, i) => `${pl + step * i + step / 2},${yN(s.new_customers)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="jw-chart" role="img" aria-label="Recorded revenue and new customers">
      {[0, 0.5, 1].map(f => <line key={f} x1={pl} x2={W} y1={pt + (H - pt - pb) * f} y2={pt + (H - pt - pb) * f} stroke="#efe9db" />)}
      {maxRev > 0 && <text x={pl - 6} y={pt + 4} textAnchor="end" fontSize="10" fill="#647085">{money(maxRev)}</text>}
      {maxRev > 0 && <text x={pl - 6} y={H - pb} textAnchor="end" fontSize="10" fill="#647085">$0</text>}
      {maxNew > 0 && <text x={W - 2} y={pt + 4} textAnchor="end" fontSize="10" fill="#1b365f">{maxNew} new</text>}
      {series.map((s, i) => (
        <g key={i}>
          <rect x={pl + step * i + (step - bw) / 2} y={yR(s.revenue)} width={bw} height={H - pb - yR(s.revenue)} rx="4" fill="#c9a24a">
            <title>{s.label}: {money(s.revenue)} recorded · {s.new_customers} new customers</title>
          </rect>
          {(series.length <= 14 || i % 2 === 0) && <text x={pl + step * i + step / 2} y={H - 7} textAnchor="middle" fontSize="10" fill="#647085">{s.label}</text>}
        </g>
      ))}
      {maxNew > 0 && <polyline points={pts} fill="none" stroke="#1b365f" strokeWidth="2.2" />}
    </svg>
  )
}

export default function Dashboard() {
  const { status } = useJewelry()
  const [period, setPeriod] = useState('30d')
  const { data: d, error, loading, reload } = useApi(`/jewelry-ops/dashboard?period=${period}`, [period])
  const df = d?.definitions || {}
  const hour = new Date().getHours()
  return (
    <div>
      <section className="jw-hero">
        <div>
          <div className="jw-eyebrow">{new Date().toLocaleDateString(undefined, { weekday: 'long', month: 'long', day: 'numeric' })}</div>
          <h1>{hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening'}, {status.me?.name?.split(' ')[0] || 'there'}.</h1>
          <p>Everything below is counted from records saved in this workspace{d?.scope === 'my customers' ? ' for the customers assigned to you' : ''}.</p>
        </div>
        <div className="jw-seg" role="group" aria-label="Time period">
          {PERIODS.map(([k, l]) => <button key={k} className={period === k ? 'on' : ''} onClick={() => setPeriod(k)}>{l}</button>)}
        </div>
      </section>
      <ErrorNote onRetry={reload}>{error}</ErrorNote>
      {loading && !d && <Loading />}
      {d && (
        <>
          {d.customers.total === 0 && (
            <div className="jw-card jw-start">
              <b>This workspace has no customers yet.</b>
              <span>Add customers one at a time or import a spreadsheet. Nothing is texted or emailed when you do.</span>
              <div className="jw-row"><Link className="jw-btn jw-btn--gold" to={`${BASE}/customers?add=1`}>Add a customer</Link>
                {status.can_manage && <Link className="jw-btn" to={`${BASE}/customers?import=1`}>Import a list</Link>}</div>
            </div>
          )}
          <div className="jw-kpis">
            <Kpi label="Total customers" value={n(d.customers.total)} foot={`${n(d.customers.new)} new in period`} def={df.total_customers} />
            <Kpi label="Active customers" value={n(d.customers.active)} foot={`${n(d.customers.inactive)} inactive`} def={df.active_customers} />
            <Kpi label="Returning customers" value={n(d.customers.returning)} foot={`${n(d.customers.reactivated)} reactivated in period`} def={df.returning_customers} />
            <Kpi label="Upcoming appointments" value={n(d.appointments.upcoming)} foot="Next 7 days" def={df.upcoming_appointments} />
            <Kpi label="Recorded revenue" value={money(d.transactions.revenue)} foot={`${n(d.transactions.count)} transactions · POS not connected`} def={df.revenue} tone="gold" />
            <Kpi label="Gold & items bought" value={money(d.transactions.purchases)} foot="Paid to customers, as recorded" def={df.purchases} />
            <Kpi label="Customer responses" value={d.responses.messaging_connected ? n(d.responses.count) : 'Not connected'} foot="Replies to texts and emails" def={df.responses} />
            <Kpi label="Open follow-ups" value={n(d.follow_ups.open)} foot={`${n(d.follow_ups.overdue)} past due`} def={df.follow_ups} tone={d.follow_ups.overdue ? 'warn' : ''} />
          </div>
          <div className="jw-grid jw-grid--main">
            <div className="jw-card">
              <div className="jw-card__head"><div><h3>Recorded revenue & new customers</h3><small>Bars: recorded sales and trades · Line: new customer records</small></div></div>
              <Chart series={d.series} />
            </div>
            <div className="jw-card">
              <div className="jw-card__head"><h3>Upcoming appointments</h3><Link className="jw-btn jw-btn--sm" to={`${BASE}/appointments`}>Calendar</Link></div>
              {d.appointments.next.length === 0 ? <Empty title="Nothing booked in the next 7 days" /> : (
                <ul className="jw-list">{d.appointments.next.map(a => (
                  <li key={a.id}><div><b>{a.customer_name}</b><small>{a.type} · {when(a.starts_at)}</small></div><Pill>{a.status.replace('_', ' ')}</Pill></li>
                ))}</ul>
              )}
            </div>
          </div>
          <div className="jw-grid jw-grid--3">
            <div className="jw-card">
              <div className="jw-card__head"><h3>Recent transactions</h3></div>
              {d.transactions.recent.length === 0 ? <Empty title="No transactions recorded">Record a sale or purchase from a customer’s page.</Empty> : (
                <ul className="jw-list">{d.transactions.recent.map(t => (
                  <li key={t.id}><div><b>{t.customer_name}</b><small>{t.kind} · {day(t.occurred_on)}</small></div><b>{money(t.amount)}</b></li>
                ))}</ul>
              )}
            </div>
            <div className="jw-card">
              <div className="jw-card__head"><h3>Campaigns</h3>{status.can_manage && <Link className="jw-btn jw-btn--sm" to={`${BASE}/campaigns`}>Open</Link>}</div>
              {d.campaigns.total === 0 ? <Empty title="No campaigns yet" /> : (
                <ul className="jw-list">{Object.entries(d.campaigns.by_status).map(([k, v]) => <li key={k}><span className="jw-cap">{k}</span><b>{v}</b></li>)}</ul>
              )}
              <p className="jw-note">Sending is not connected, so nothing has been delivered. Delivery and replies show here once it is.</p>
            </div>
            <div className="jw-card">
              <div className="jw-card__head"><h3>AI & follow-up activity</h3></div>
              <ul className="jw-list">
                <li><span>AI questions answered (test mode)</span><b>{n(d.ai.questions)}</b></li>
                <li><span>Handed to a person</span><b>{n(d.ai.handed_to_person)}</b></li>
                <li><span>Appointments completed in period</span><b>{n(d.appointments.completed)}</b></li>
                <li><span>Visit rate (completed ÷ booked)</span><b>{d.appointments.conversion === null ? '—' : d.appointments.conversion + '%'}</b></li>
              </ul>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
