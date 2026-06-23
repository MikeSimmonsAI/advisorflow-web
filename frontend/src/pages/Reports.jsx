import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '../api/client'
import StatCard from '../components/StatCard'
import '../styles/shared.css'
import './Reports.css'

const PRODUCT_MIX_LABELS = {
  funeral_arrangement: 'Funeral arrangement',
  cemetery_property: 'Cemetery property',
  marker: 'Marker',
  memorial: 'Memorial',
}

function todayISO() {
  return new Date().toISOString().slice(0, 10)
}

function daysAgoISO(days) {
  const d = new Date()
  d.setDate(d.getDate() - days)
  return d.toISOString().slice(0, 10)
}

function formatChartDate(value) {
  const date = new Date(`${value}T00:00:00`)
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

const chartTooltipStyle = {
  background: 'rgba(7, 14, 32, 0.96)',
  border: '1px solid rgba(86, 200, 255, 0.42)',
  borderRadius: 12,
  color: '#eef5ff',
  boxShadow: '0 20px 60px rgba(0, 0, 0, 0.45)',
}

const RANGE_PRESETS = [
  { label: 'Last 7 days', days: 7 },
  { label: 'Last 30 days', days: 30 },
  { label: 'Last 90 days', days: 90 },
]

export default function Reports() {
  const navigate = useNavigate()
  const [startDate, setStartDate] = useState(daysAgoISO(30))
  const [endDate, setEndDate] = useState(todayISO())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [trend, setTrend] = useState(null)
  const [engagementVsConversion, setEngagementVsConversion] = useState(null)
  const [revenue, setRevenue] = useState(null)

  function applyPreset(days) {
    setStartDate(daysAgoISO(days))
    setEndDate(todayISO())
  }

  function load() {
    setLoading(true)
    setError('')
    const params = `?start_date=${startDate}&end_date=${endDate}`
    Promise.all([
      api.get(`/reports/conversion-trend${params}`),
      api.get(`/reports/engagement-vs-conversion${params}`),
      api.get(`/reports/revenue-by-period${params}`),
    ])
      .then(([trendData, evcData, revenueData]) => {
        setTrend(trendData)
        setEngagementVsConversion(evcData)
        setRevenue(revenueData)
      })
      .catch((err) => setError(err.message || 'Could not load reports.'))
      .finally(() => setLoading(false))
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function handleApplyRange(event) {
    event.preventDefault()
    load()
  }

  return (
    <div>
      <header className="page-header">
        <div>
          <h1 className="page-title">Reports</h1>
          <p className="page-subtitle">Conversions, engagement, and revenue counts for a date range you pick.</p>
        </div>
      </header>

      <section className="panel reports-filter-panel">
        <form className="reports-date-form" onSubmit={handleApplyRange}>
          <label className="reports-date-field">
            From
            <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} max={endDate} />
          </label>
          <label className="reports-date-field">
            To
            <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} min={startDate} max={todayISO()} />
          </label>
          <button className="btn btn--primary" type="submit" disabled={loading}>
            {loading ? 'Loading…' : 'Apply range'}
          </button>
          <div className="reports-presets">
            {RANGE_PRESETS.map((preset) => (
              <button key={preset.days} type="button" className="btn btn--secondary" onClick={() => { applyPreset(preset.days); setTimeout(load, 0) }}>
                {preset.label}
              </button>
            ))}
          </div>
        </form>
      </section>

      {error && <div className="panel reports-error">{error}</div>}

      {loading ? (
        <section className="panel"><div className="empty-state">Loading reports…</div></section>
      ) : (
        <>
          <div className="stat-grid reports-summary-stats">
            <StatCard label="Replies" value={trend?.totals?.replies ?? 0} accent="blue" />
            <StatCard label="Hot replies" value={trend?.totals?.hot_replies ?? 0} accent="red" />
            <StatCard label="Booked" value={trend?.totals?.booked ?? 0} accent="green" />
            <StatCard label="Sales" value={revenue?.total_sales ?? 0} accent="amber" sublabel="Count, not a dollar total" />
          </div>

          <section className="panel reports-chart-panel">
            <div className="panel-header">
              <h2 className="panel-title">Conversion trend</h2>
              <span className="chart-subtitle-inline">Replies, hot replies, bookings, and sales by day</span>
            </div>
            {(trend?.trend || []).length === 0 ? (
              <div className="empty-state">No activity in this date range yet.</div>
            ) : (
              <ResponsiveContainer width="100%" height={280}>
                <LineChart data={trend.trend} margin={{ top: 16, right: 18, left: -10, bottom: 2 }}>
                  <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="4 8" vertical={false} />
                  <XAxis dataKey="date" tickFormatter={formatChartDate} stroke="var(--text-tertiary)" tickLine={false} axisLine={false} minTickGap={24} />
                  <YAxis allowDecimals={false} stroke="var(--text-tertiary)" tickLine={false} axisLine={false} width={36} />
                  <Tooltip contentStyle={chartTooltipStyle} labelFormatter={formatChartDate} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Line type="monotone" dataKey="replies" name="Replies" stroke="var(--signal-blue)" strokeWidth={2.5} dot={false} />
                  <Line type="monotone" dataKey="hot_replies" name="Hot replies" stroke="var(--signal-red)" strokeWidth={2.5} dot={false} />
                  <Line type="monotone" dataKey="booked" name="Booked" stroke="var(--signal-green)" strokeWidth={2.5} dot={false} />
                  <Line type="monotone" dataKey="sold" name="Sold" stroke="var(--signal-amber)" strokeWidth={2.5} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            )}
          </section>

          <section className="panel reports-table-panel">
            <div className="panel-header">
              <h2 className="panel-title">Engagement vs. conversion, by advisor</h2>
              <span className="chart-subtitle-inline">Are replies turning into bookings, or stalling?</span>
            </div>
            {(engagementVsConversion?.advisors || []).length === 0 ? (
              <div className="empty-state">No advisor activity in this date range.</div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Advisor</th>
                    <th>Leads messaged</th>
                    <th>Replies</th>
                    <th>Hot replies</th>
                    <th>Booked</th>
                    <th>Sold</th>
                    <th>Engagement rate</th>
                    <th>Conversion rate</th>
                  </tr>
                </thead>
                <tbody>
                  {engagementVsConversion.advisors.map((row) => (
                    <tr key={row.advisor_id}>
                      <td>{row.advisor_name}</td>
                      <td className="mono">{row.leads_messaged}</td>
                      <td className="mono">{row.replies}</td>
                      <td className="mono" style={{ color: row.hot_replies > 0 ? 'var(--signal-red)' : undefined }}>{row.hot_replies}</td>
                      <td className="mono">{row.booked}</td>
                      <td className="mono">{row.sold}</td>
                      <td className="mono">{row.engagement_rate}%</td>
                      <td className="mono">{row.conversion_rate}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="panel reports-chart-panel">
            <div className="panel-header">
              <h2 className="panel-title">Sales by advisor</h2>
              <span className="chart-subtitle-inline">Count of sales in this range, not a dollar total</span>
            </div>
            {(revenue?.by_advisor || []).length === 0 ? (
              <div className="empty-state">No sales recorded in this date range.</div>
            ) : (
              <ResponsiveContainer width="100%" height={Math.max(160, revenue.by_advisor.length * 50)}>
                <BarChart data={revenue.by_advisor} layout="vertical" margin={{ top: 8, right: 24, left: 8, bottom: 8 }}>
                  <CartesianGrid stroke="var(--border-subtle)" strokeDasharray="4 8" horizontal={false} />
                  <XAxis type="number" allowDecimals={false} stroke="var(--text-tertiary)" tickLine={false} axisLine={false} />
                  <YAxis type="category" dataKey="advisor_name" stroke="var(--text-tertiary)" tickLine={false} axisLine={false} width={120} />
                  <Tooltip contentStyle={chartTooltipStyle} />
                  <Bar dataKey="sale_count" name="Sales" fill="var(--signal-amber)" radius={[0, 6, 6, 0]} />
                </BarChart>
              </ResponsiveContainer>
            )}
          </section>

          <section className="panel reports-product-mix-panel">
            <div className="panel-header">
              <h2 className="panel-title">What's selling</h2>
            </div>
            <div className="reports-product-mix-grid">
              {Object.entries(revenue?.product_mix || {}).map(([key, count]) => (
                <div className="reports-product-mix-card" key={key}>
                  <span>{PRODUCT_MIX_LABELS[key] || key}</span>
                  <strong>{count}</strong>
                </div>
              ))}
            </div>
          </section>
        </>
      )}
    </div>
  )
}
